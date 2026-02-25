"""
GreenOps — Settings Manager
"""

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ─── Hardcoded defaults ───────────────────────────────────────────────────────
_DEFAULTS: Dict[str, str] = {
    "electricity_cost_per_kwh":   "0.12",
    "idle_power_watts":            "65",
    "currency":                    "USD",
    "idle_threshold_seconds":      "300",
    "heartbeat_timeout_seconds":   "180",
    "agent_heartbeat_interval":    "60",
    "organization_name":           "GreenOps",
    "log_level":                   "INFO",
}

# ─── Infrastructure ENV ───────────────────────────────────────────────────────
DATABASE_URL:      str  = os.environ.get("DATABASE_URL", "")
JWT_SECRET_KEY:    str  = os.environ.get("JWT_SECRET_KEY", "")
FLASK_SECRET_KEY:  str  = os.environ.get("FLASK_SECRET_KEY", JWT_SECRET_KEY)
FLASK_ENV:         str  = os.environ.get("FLASK_ENV", "production")
DEBUG:             bool = FLASK_ENV == "development"


class SettingsManager:
    TTL_SECONDS = 30

    def __init__(self) -> None:
        self._cache: Dict[str, str] = {}
        self._cache_ts: float = 0.0
        self._db = None

    def init_app(self, db) -> None:
        self._db = db
        self._refresh()

    def get(self, key: str) -> str:
        self._maybe_refresh()
        if key in self._cache:
            return self._cache[key]
        if key in _DEFAULTS:
            logger.warning(f"Setting '{key}' not found in DB; using default.")
            return _DEFAULTS[key]
        raise KeyError(f"Unknown setting: {key!r}")

    def get_int(self, key: str) -> int:
        val = self.get(key)
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return int(float(_DEFAULTS.get(key, "0")))

    def get_float(self, key: str) -> float:
        val = self.get(key)
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(_DEFAULTS.get(key, "0"))

    def get_all(self) -> Dict[str, str]:
        self._maybe_refresh()
        return dict(self._cache)

    def invalidate(self) -> None:
        self._cache_ts = 0.0

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._cache_ts > self.TTL_SECONDS:
            self._refresh()

    def _refresh(self) -> None:
        if self._db is None:
            self._cache = dict(_DEFAULTS)
            self._cache_ts = time.monotonic()
            return
        try:
            rows = self._db.execute_query(
                "SELECT key, value FROM app_settings",
                fetch=True,
            )
            if rows:
                merged = dict(_DEFAULTS)
                merged.update({r["key"]: r["value"] for r in rows})
                self._cache = merged
            else:
                self._cache = dict(_DEFAULTS)
            self._cache_ts = time.monotonic()
        except Exception as exc:
            logger.error(f"Failed to load settings from DB: {exc}")
            if not self._cache:
                self._cache = dict(_DEFAULTS)
            self._cache_ts = time.monotonic() - (self.TTL_SECONDS / 2)


settings = SettingsManager()


class _ConfigCompat:
    """Complete compatibility shim — provides every attribute main.py needs."""

    # ── Infrastructure (from ENV) ─────────────────────────────────────────
    DATABASE_URL     = DATABASE_URL
    JWT_SECRET_KEY   = JWT_SECRET_KEY
    FLASK_SECRET_KEY = FLASK_SECRET_KEY
    SECRET_KEY       = FLASK_SECRET_KEY
    FLASK_ENV        = FLASK_ENV
    DEBUG            = DEBUG
    PORT             = int(os.environ.get("PORT", "5000"))
    HOST             = os.environ.get("HOST", "0.0.0.0")

    # ── Security / rate limiting ──────────────────────────────────────────
    JWT_ALGORITHM          = "HS256"
    JWT_EXPIRATION_HOURS   = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))
    LOGIN_RATE_LIMIT       = int(os.environ.get("LOGIN_RATE_LIMIT", "5"))
    LOGIN_RATE_WINDOW      = int(os.environ.get("LOGIN_RATE_WINDOW", "900"))

    # ── CORS ──────────────────────────────────────────────────────────────
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

    # ── Logging ───────────────────────────────────────────────────────────
    @property
    def LOG_LEVEL(self) -> str:
        try:
            return settings.get("log_level").upper()
        except Exception:
            return os.environ.get("LOG_LEVEL", "INFO").upper()

    LOG_FILE = os.environ.get(
        "LOG_FILE",
        os.path.join(os.getcwd(), "logs", "greenops.log"),
    )

    # ── DB pool ───────────────────────────────────────────────────────────
    DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))

    # ── Runtime thresholds (from DB via settings) ─────────────────────────
    @property
    def IDLE_THRESHOLD_SECONDS(self) -> int:
        return settings.get_int("idle_threshold_seconds")

    @property
    def HEARTBEAT_TIMEOUT_SECONDS(self) -> int:
        return settings.get_int("heartbeat_timeout_seconds")

    @property
    def IDLE_POWER_WATTS(self) -> int:
        return settings.get_int("idle_power_watts")

    @property
    def ELECTRICITY_COST_PER_KWH(self) -> float:
        return settings.get_float("electricity_cost_per_kwh")

    @property
    def OFFLINE_CHECK_INTERVAL_SECONDS(self) -> int:
        return int(os.environ.get("OFFLINE_CHECK_INTERVAL_SECONDS", "60"))

    # ── Bootstrap ─────────────────────────────────────────────────────────
    ADMIN_INITIAL_PASSWORD = os.environ.get("ADMIN_INITIAL_PASSWORD")

    def validate(self) -> None:
        """Raise ValueError listing all configuration problems."""
        errors = []
        if not self.DATABASE_URL:
            errors.append("DATABASE_URL is not set.")
        if not self.JWT_SECRET_KEY:
            errors.append("JWT_SECRET_KEY is not set.")
        if not self.DEBUG and len(self.JWT_SECRET_KEY) < 32:
            errors.append(
                f"JWT_SECRET_KEY is too short ({len(self.JWT_SECRET_KEY)} chars); "
                "minimum 32 required in production."
            )
        if errors:
            raise ValueError("\n".join(errors))


config = _ConfigCompat()
