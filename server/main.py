"""
GreenOps Server — Application factory and entry point.

Key fixes vs previous version:
  - Offline checker: catches psycopg2.OperationalError and calls
    db.initialize() to reconnect.  Without this, one dropped connection
    permanently disables the offline checker for the lifetime of the
    master process.
  - _ensure_schema_upgrades(): applies migration 003 idempotently on
    every boot so existing deployments get the new columns/tables without
    requiring a manual psql command.
"""

import os
import sys
import signal
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psycopg2
from flask import Flask, jsonify
from flask_cors import CORS

from server.config import config
from server.database import db
from server.middleware import handle_errors
from server.routes.auth import auth_bp
from server.routes.agents import agents_bp
from server.routes.dashboard import dashboard_bp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    log_path = Path(config.LOG_FILE)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            config.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        handlers.insert(0, file_handler)
    except OSError as exc:
        print(f"[greenops] WARNING: cannot create log dir: {exc}", flush=True)

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


# ---------------------------------------------------------------------------
# Schema upgrades (idempotent — safe to run on every boot)
# ---------------------------------------------------------------------------

def _ensure_schema_upgrades() -> None:
    """
    Apply migration 003 idempotently so that existing deployments
    (where postgres volume was already initialised) get the new columns
    and tables without requiring a manual psql command.
    """
    ddl_statements = [
        # must_change_password column
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'must_change_password'
            ) THEN
                ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
                UPDATE users SET must_change_password = TRUE WHERE username = 'admin';
            END IF;
        END $$
        """,
        # uptime_seconds column
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'machines' AND column_name = 'uptime_seconds'
            ) THEN
                ALTER TABLE machines ADD COLUMN uptime_seconds BIGINT NOT NULL DEFAULT 0;
            END IF;
        END $$
        """,
        # machine_commands table
        """
        CREATE TABLE IF NOT EXISTS machine_commands (
            id          SERIAL PRIMARY KEY,
            machine_id  INTEGER     NOT NULL REFERENCES machines (id) ON DELETE CASCADE,
            command     VARCHAR(20) NOT NULL,
            status      VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_by  INTEGER     REFERENCES users (id),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            executed_at TIMESTAMPTZ,
            result_msg  TEXT,
            CONSTRAINT valid_command    CHECK (command IN ('sleep', 'shutdown')),
            CONSTRAINT valid_cmd_status CHECK (status  IN ('pending', 'executed', 'failed', 'expired'))
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_commands_machine_status
            ON machine_commands (machine_id, status)
            WHERE status = 'pending'
        """,
    ]

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                for stmt in ddl_statements:
                    cur.execute(stmt)
        logger.info("Schema upgrades applied (migration 003).")
    except Exception as exc:
        logger.error(f"Schema upgrade failed: {exc}", exc_info=True)
        # Non-fatal for most columns — app can still start.


# ---------------------------------------------------------------------------
# Admin initial password
# ---------------------------------------------------------------------------

def _apply_admin_initial_password() -> None:
    if not config.ADMIN_INITIAL_PASSWORD:
        return
    logger.info("Applying ADMIN_INITIAL_PASSWORD …")
    try:
        from server.auth import AuthService
        new_hash = AuthService.hash_password(config.ADMIN_INITIAL_PASSWORD)
        db.execute_query(
            "UPDATE users SET password_hash = %s WHERE username = 'admin'",
            (new_hash,),
        )
        logger.info("Admin password updated from ADMIN_INITIAL_PASSWORD.")
    except Exception as exc:
        logger.error(f"Failed to apply ADMIN_INITIAL_PASSWORD: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        config.ADMIN_INITIAL_PASSWORD = None


# ---------------------------------------------------------------------------
# Offline checker (background thread — master process only)
# ---------------------------------------------------------------------------

def _run_offline_check(app: Flask, interval: int) -> None:
    """
    Marks machines offline when no heartbeat received within
    HEARTBEAT_TIMEOUT_SECONDS.  Also expires stale commands.

    Root cause fix: the previous version let a stale pool connection
    permanently break the checker.  We now catch psycopg2.OperationalError
    and call db.initialize() to get a fresh pool before the next tick.
    """
    _stop = threading.Event()

    def _loop() -> None:
        while not _stop.wait(timeout=interval):
            try:
                with app.app_context():
                    from server.services.machine import MachineService
                    count = MachineService.update_offline_machines()
                    if count:
                        logger.info(
                            f"Offline checker: marked {count} machine(s) offline."
                        )
                    # Expire commands older than 5 min
                    db.execute_query(
                        """
                        UPDATE machine_commands SET status = 'expired'
                        WHERE status = 'pending'
                          AND created_at < NOW() - INTERVAL '5 minutes'
                        """
                    )
            except psycopg2.OperationalError as exc:
                # Connection was silently dropped (keepalives failed or
                # DB restarted).  Reinitialise the pool so the next tick works.
                logger.error(
                    f"Offline checker: DB connection lost ({exc}). "
                    f"Reinitialising pool …"
                )
                try:
                    db.initialize()
                    logger.info("Offline checker: DB pool reconnected.")
                except Exception as init_exc:
                    logger.error(f"Offline checker: reconnect failed: {init_exc}")
            except Exception as exc:
                logger.error(f"Offline check error: {exc}", exc_info=True)

    t = threading.Thread(target=_loop, daemon=True, name="offline-check")
    t.start()
    logger.info(
        f"Offline checker started (interval={interval}s, pid={os.getpid()})."
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    _configure_logging()

    logger.info(
        f"create_app() starting (pid={os.getpid()}, "
        f"debug={config.DEBUG}, log={config.LOG_FILE})"
    )

    try:
        config.validate()
    except ValueError as exc:
        logger.error(f"Configuration error: {exc}")
        sys.exit(1)

    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    CORS(app, origins=config.CORS_ORIGINS, supports_credentials=True)

    try:
        db.initialize()
    except Exception as exc:
        logger.error(f"Failed to initialise database: {exc}", exc_info=True)
        sys.exit(1)

    _ensure_schema_upgrades()
    handle_errors(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(dashboard_bp)

    @app.route("/")
    def root():
        return jsonify({"service": "GreenOps", "version": "2.0.0", "status": "operational"})

    @app.route("/health")
    def health():
        try:
            db.execute_one("SELECT 1")
            return jsonify({"status": "healthy", "database": "connected"}), 200
        except Exception:
            return jsonify({"status": "unhealthy", "database": "disconnected"}), 503

    _apply_admin_initial_password()
    _run_offline_check(app, config.OFFLINE_CHECK_INTERVAL_SECONDS)

    logger.info("GreenOps server initialised and ready.")
    return app


def graceful_shutdown(signum, frame):
    logger.info(f"Received signal {signum}, shutting down …")
    db.close()
    sys.exit(0)


app = create_app()


def main():
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    logger.info(f"Starting GreenOps on {config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, threaded=True)


if __name__ == "__main__":
    main()
