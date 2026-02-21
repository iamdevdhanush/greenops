"""
GreenOps Database Layer
=======================
Root cause fix included here:
  The offline-check thread in the gunicorn master process uses a pool
  connection that sits idle for OFFLINE_CHECK_INTERVAL_SECONDS (60s).
  PostgreSQL (or a Docker network NAT layer) silently drops idle TCP
  connections, causing "server closed the connection unexpectedly" on
  the next use.

  Fixes applied:
  1. TCP keepalives on every connection: OS sends keepalive probes every
     30s so NAT tables and firewalls never expire the socket.
  2. Pool reconnect on OperationalError in the offline checker
     (handled in server/main.py).
"""

import logging
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool, extras

from server.config import config

logger = logging.getLogger(__name__)

_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 5,
    "keepalives_count": 3,
}


class Database:
    """Thread-safe database connection pool manager."""

    def __init__(self):
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._lock = threading.Lock()

    @property
    def pool(self) -> Optional[pool.ThreadedConnectionPool]:
        return self._pool

    def initialize(self) -> None:
        with self._lock:
            if self._pool is not None:
                try:
                    self._pool.closeall()
                    logger.debug("Existing DB pool closed before reinitialisation.")
                except Exception as exc:
                    logger.warning(f"Error closing existing pool: {exc}")
                self._pool = None

            try:
                self._pool = pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=config.DB_POOL_SIZE,
                    dsn=config.DATABASE_URL,
                    **_KEEPALIVE_KWARGS,
                )
                logger.info(
                    f"Database pool initialised "
                    f"(minconn=1, maxconn={config.DB_POOL_SIZE}, keepalives=on)."
                )
            except Exception as exc:
                logger.error(f"Failed to create DB pool: {exc}")
                raise

            try:
                with self.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                logger.info("Database connectivity verified.")
            except Exception as exc:
                logger.error(f"DB smoke-test failed: {exc}")
                raise

    @contextmanager
    def get_connection(self):
        if self._pool is None:
            raise RuntimeError("Database pool is not initialised.")

        conn = None
        try:
            conn = self._pool.getconn()
            yield conn
            conn.commit()
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Database error: {exc}")
            raise
        finally:
            if conn is not None:
                self._pool.putconn(conn)

    def execute_query(self, query: str, params: tuple = None, fetch: bool = False):
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, params)
                if fetch:
                    return cur.fetchall()
                return cur.rowcount

    def execute_one(self, query: str, params: tuple = None):
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return cur.fetchone()

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                try:
                    self._pool.closeall()
                    logger.info("Database pool closed.")
                except Exception as exc:
                    logger.warning(f"Error during pool close: {exc}")
                self._pool = None


db = Database()
