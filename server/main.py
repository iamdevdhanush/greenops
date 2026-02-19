"""
GreenOps Server
Main application entry point and application factory.
"""
import sys
import logging
import signal
import threading
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify
from flask_cors import CORS

from server.config import config
from server.database import db
from server.middleware import handle_errors
from server.routes.auth import auth_bp
from server.routes.agents import agents_bp
from server.routes.dashboard import dashboard_bp

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def _run_offline_check(app: Flask, interval: int) -> None:
    """Background daemon thread: marks machines offline if heartbeat has timed out."""
    def _loop():
        while True:
            try:
                with app.app_context():
                    from server.services.machine import MachineService
                    count = MachineService.update_offline_machines()
                    if count:
                        logger.info(f"Background check: marked {count} machine(s) offline")
            except Exception as exc:
                logger.error(f"Background offline check failed: {exc}", exc_info=True)
            threading.Event().wait(interval)

    t = threading.Thread(target=_loop, daemon=True, name="offline-check")
    t.start()


def _apply_admin_initial_password(app: Flask) -> None:
    """
    If ADMIN_INITIAL_PASSWORD is set, update the 'admin' account on startup.
    This allows operators to inject a secure password via environment variable
    instead of relying on the default hash committed in the migration.
    """
    if not config.ADMIN_INITIAL_PASSWORD:
        return

    try:
        with app.app_context():
            from server.auth import AuthService
            new_hash = AuthService.hash_password(config.ADMIN_INITIAL_PASSWORD)
            db.execute_query(
                "UPDATE users SET password_hash = %s WHERE username = 'admin'",
                (new_hash,),
            )
            logger.info("Admin password updated from ADMIN_INITIAL_PASSWORD environment variable.")
    except Exception as exc:
        logger.error(f"Failed to apply ADMIN_INITIAL_PASSWORD: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        config.ADMIN_INITIAL_PASSWORD = None


def create_app() -> Flask:
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
        logger.error(f"Failed to initialize database: {exc}", exc_info=True)
        sys.exit(1)

    handle_errors(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(dashboard_bp)

    @app.route("/")
    def root():
        return jsonify({"service": "GreenOps", "version": "1.0.0", "status": "operational"})

    @app.route("/health")
    def health():
        try:
            db.execute_one("SELECT 1")
            return jsonify({"status": "healthy", "database": "connected"}), 200
        except Exception:
            return jsonify({"status": "unhealthy", "database": "disconnected"}), 503

    _apply_admin_initial_password(app)
    _run_offline_check(app, config.OFFLINE_CHECK_INTERVAL_SECONDS)

    logger.info("GreenOps server initialised")
    return app


def graceful_shutdown(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    db.close()
    sys.exit(0)


app = create_app()


def main():
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    logger.info(f"Starting GreenOps server on {config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, threaded=True)


if __name__ == "__main__":
    main()
