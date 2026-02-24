"""
GreenOps Database Layer — Production Hardened
=============================================

KEY FIX: minconn=0 (lazy allocation)
  The previous minconn=1 kept one connection permanently idle between
  60-second offline-checker runs. Docker's NAT table timed out that
  idle TCP connection at exactly the 60s mark on every cycle, producing
  the repeated "server closed the connection unexpectedly" errors visible
  in the logs. With minconn=0, the pool only holds connections while
  they are actively in use; nothing sits idle to be killed by NAT.
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool, extras

from server.config import config

logger = logging.getLogger(__name__)

_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 5,
}

_INIT_RETRIES = 3
_INIT_RETRY_DELAY = 2.0


class Database:
    """Thread-safe PostgreSQL connection pool manager."""

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
                except Exception as exc:
                    logger.warning(f"Error closing existing pool: {exc}")
                self._pool = None

            try:
                self._pool = pool.ThreadedConnectionPool(
                    minconn=0,                        # FIXED: was 1
                    maxconn=config.DB_POOL_SIZE,
                    dsn=config.DATABASE_URL,
                    **_KEEPALIVE_KWARGS,
                )
                logger.info(
                    f"Database pool created "
                    f"(minconn=0, maxconn={config.DB_POOL_SIZE}, keepalives=on)."
                )
            except psycopg2.OperationalError as exc:
                logger.error(f"Failed to create DB pool: {exc}")
                raise
            except Exception as exc:
                logger.error(f"Unexpected error creating DB pool: {exc}")
                raise

        last_exc = None
        for attempt in range(1, _INIT_RETRIES + 1):
            try:
                with self.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                logger.info("Database connectivity verified.")
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"DB smoke-test attempt {attempt}/{_INIT_RETRIES} failed: {exc}"
                )
                if attempt < _INIT_RETRIES:
                    time.sleep(_INIT_RETRY_DELAY)

        logger.error(f"DB smoke-test failed after {_INIT_RETRIES} attempts: {last_exc}")
        raise last_exc

    @contextmanager
    def get_connection(self):
        if self._pool is None:
            raise RuntimeError(
                "Database pool is not initialised. "
                "Call db.initialize() before making requests."
            )

        conn = None
        try:
            conn = self._pool.getconn()
            if conn is None:
                raise RuntimeError("Pool returned None — pool exhausted or closed.")
            yield conn
            conn.commit()
        except psycopg2.pool.PoolError as exc:
            raise RuntimeError(
                f"DB connection pool exhausted (maxconn={config.DB_POOL_SIZE}). "
                f"Consider increasing DB_POOL_SIZE. Original: {exc}"
            ) from exc
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Database error: {exc}")
            raise
        finally:
            if conn is not None and self._pool is not None:
                try:
                    self._pool.putconn(conn)
                except Exception:
                    pass

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
