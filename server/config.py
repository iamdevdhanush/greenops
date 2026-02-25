"""
GreenOps — Settings Manager
============================
Single source of truth for all runtime configuration.

Hierarchy (strict):
  1. Database (app_settings table)  ← AUTHORITATIVE
  2. Hardcoded defaults             ← fallback ONLY if DB row missing

ENV vars are NOT used for runtime settings.
Only infrastructure ENV remains: DATABASE_URL, JWT_SECRET_KEY, FLASK_SECRET_KEY.

Usage:
    from server.config import settings
    threshold = settings.get_int('idle_threshold_seconds')
"""

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ─── Hardcoded defaults (last resort only) ───────────────────────────────────
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

# ─── Infrastructure ENV (never in DB) ────────────────────────────────────────
DATABASE_URL:      str = os.environ["DATABASE_URL"]           # required
JWT_SECRET_KEY:    str = os.environ["JWT_SECRET_KEY"]         # required
FLASK_SECRET_KEY:  str = os.environ.get("FLASK_SECRET_KEY", JWT_SECRET_KEY)
FLASK_ENV:         str = os.environ.get("FLASK_ENV", "production")
DEBUG:             bool = FLASK_ENV == "development"


class SettingsManager:
    """
    Reads runtime settings from the database with a short TTL cache.
    Never reads runtime config from ENV.

    Thread-safe for read-heavy workloads (cache refresh is idempotent).
    TTL default: 30 seconds — balances freshness vs DB load.
    """

    TTL_SECONDS = 30

    def __init__(self) -> None:
        self._cache: Dict[str, str] = {}
        self._cache_ts: float = 0.0
        self._db = None  # injected after app init

    def init_app(self, db) -> None:
        """Call once after database pool is ready."""
        self._db = db
        self._refresh()

    # ─── Public API ──────────────────────────────────────────────────────────

    def get(self, key: str) -> str:
        """Return setting value as string. Refreshes cache if stale."""
        self._maybe_refresh()
        if key in self._cache:
            return self._cache[key]
        if key in _DEFAULTS:
            logger.warning(
                f"Setting '{key}' not found in DB; using hardcoded default: {_DEFAULTS[key]!r}"
            )
            return _DEFAULTS[key]
        raise KeyError(f"Unknown setting: {key!r}")

    def get_int(self, key: str) -> int:
        """Return setting as integer. Raises ValueError on bad data."""
        val = self.get(key)
        try:
            return int(float(val))  # handles "300", "300.0", "300.5" → 300
        except (ValueError, TypeError) as exc:
            logger.error(f"Setting '{key}' has non-integer value {val!r}: {exc}")
            # Fall back to default
            return int(float(_DEFAULTS.get(key, "0")))

    def get_float(self, key: str) -> float:
        """Return setting as float."""
        val = self.get(key)
        try:
            return float(val)
        except (ValueError, TypeError) as exc:
            logger.error(f"Setting '{key}' has non-float value {val!r}: {exc}")
            return float(_DEFAULTS.get(key, "0"))

    def get_all(self) -> Dict[str, str]:
        """Return all settings as dict (refreshes if stale)."""
        self._maybe_refresh()
        return dict(self._cache)

    def invalidate(self) -> None:
        """Force next read to hit the database."""
        self._cache_ts = 0.0

    # ─── Internal ────────────────────────────────────────────────────────────

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._cache_ts > self.TTL_SECONDS:
            self._refresh()

    def _refresh(self) -> None:
        if self._db is None:
            logger.debug("SettingsManager: DB not yet initialized; using defaults.")
            self._cache = dict(_DEFAULTS)
            self._cache_ts = time.monotonic()
            return

        try:
            rows = self._db.execute_query(
                "SELECT key, value FROM app_settings",
                fetch=True,
            )
            if rows:
                fresh = {r["key"]: r["value"] for r in rows}
                # Merge: DB values override defaults
                merged = dict(_DEFAULTS)
                merged.update(fresh)
                self._cache = merged
            else:
                logger.warning("app_settings table is empty; using defaults.")
                self._cache = dict(_DEFAULTS)
            self._cache_ts = time.monotonic()
        except Exception as exc:
            logger.error(f"Failed to load settings from DB: {exc}", exc_info=True)
            # Don't crash — keep existing cache or use defaults
            if not self._cache:
                self._cache = dict(_DEFAULTS)
            self._cache_ts = time.monotonic() - (self.TTL_SECONDS / 2)  # retry sooner


# ─── Module-level singleton ──────────────────────────────────────────────────
settings = SettingsManager()

# ─── Backwards-compatibility alias ───────────────────────────────────────────
# main.py does: from server.config import config
# This provides a drop-in so it doesn't crash, while runtime values stay live.
class _ConfigCompat:
    """Compatibility shim for old `config` import in main.py."""
    DATABASE_URL     = DATABASE_URL
    JWT_SECRET_KEY   = JWT_SECRET_KEY
    FLASK_SECRET_KEY = FLASK_SECRET_KEY
    SECRET_KEY       = FLASK_SECRET_KEY  # Flask uses SECRET_KEY
    FLASK_ENV        = FLASK_ENV
    DEBUG            = DEBUG
    PORT             = int(os.environ.get("PORT", "5000"))

    @property
    def IDLE_THRESHOLD_SECONDS(self):
        return settings.get_int("idle_threshold_seconds")

    @property
    def HEARTBEAT_TIMEOUT_SECONDS(self):
        return settings.get_int("heartbeat_timeout_seconds")

    @property
    def IDLE_POWER_WATTS(self):
        return settings.get_int("idle_power_watts")

    @property
    def ELECTRICITY_COST_PER_KWH(self):
        return settings.get_float("electricity_cost_per_kwh")

config = _ConfigCompat()
