"""
GreenOps — Settings Routes
===========================
GET  /api/settings        — return all settings as key-value dict
PUT  /api/settings        — bulk update settings
PUT  /api/settings/<key>  — update a single setting

Settings are always read from and written to the database.
After any write, the SettingsManager cache is invalidated so the
next request picks up the fresh values immediately.
"""

import logging

from flask import Blueprint, g, jsonify, request

from server.config import settings
from server.database import db
from server.middleware import require_jwt

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")

# Whitelist: only these keys can be modified via the API.
# Prevents modification of system-internal or security-sensitive values.
ALLOWED_KEYS = frozenset({
    "electricity_cost_per_kwh",
    "idle_power_watts",
    "currency",
    "idle_threshold_seconds",
    "heartbeat_timeout_seconds",
    "agent_heartbeat_interval",
    "organization_name",
    "log_level",
})

# Validation rules: key → (min, max) for numeric fields
NUMERIC_RANGES = {
    "idle_threshold_seconds":    (10,   86400),
    "heartbeat_timeout_seconds": (10,   86400),
    "agent_heartbeat_interval":  (5,    3600),
    "idle_power_watts":          (1,    2000),
    "electricity_cost_per_kwh":  (0.0,  10.0),
}


@settings_bp.route("", methods=["GET"])
@require_jwt
def get_settings():
    """GET /api/settings — returns all runtime settings as a flat dict."""
    try:
        rows = db.execute_query(
            "SELECT key, value, description, updated_at FROM app_settings ORDER BY key",
            fetch=True,
        )
        result = {}
        for r in (rows or []):
            result[r["key"]] = {
                "value": r["value"],
                "description": r.get("description", ""),
                "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
            }
        return jsonify(result), 200
    except Exception as exc:
        logger.error(f"get_settings error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@settings_bp.route("", methods=["PUT"])
@require_jwt
def update_settings():
    """PUT /api/settings — bulk update {key: value, ...}"""
    try:
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "JSON object required"}), 400

        updates = {}
        errors = {}
        for k, v in data.items():
            if k not in ALLOWED_KEYS:
                errors[k] = "unknown or read-only key"
                continue
            validated, err = _validate(k, v)
            if err:
                errors[k] = err
            else:
                updates[k] = validated

        if errors:
            return jsonify({"error": "Validation failed", "fields": errors}), 422

        if not updates:
            return jsonify({"error": "No valid settings keys provided"}), 422

        for key, value in updates.items():
            db.execute_query(
                """
                INSERT INTO app_settings (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = NOW()
                """,
                (key, value),
            )
        db.commit()

        # ── Invalidate in-memory cache so next request picks up fresh values ──
        settings.invalidate()

        logger.info(f"Settings updated by user {g.user_id}: {list(updates.keys())}")
        return jsonify({
            "message": "Settings updated",
            "updated": list(updates.keys()),
        }), 200

    except Exception as exc:
        logger.error(f"update_settings error: {exc}", exc_info=True)
        db.rollback()
        return jsonify({"error": "Internal server error"}), 500


@settings_bp.route("/<key>", methods=["PUT"])
@require_jwt
def update_setting(key: str):
    """PUT /api/settings/<key> — update a single setting"""
    try:
        if key not in ALLOWED_KEYS:
            return jsonify({"error": f"Unknown or read-only setting: {key!r}"}), 422

        data = request.get_json(silent=True)
        if data is None or "value" not in data:
            return jsonify({"error": "JSON body with 'value' field required"}), 400

        validated, err = _validate(key, data["value"])
        if err:
            return jsonify({"error": f"Validation error for {key!r}: {err}"}), 422

        db.execute_query(
            """
            INSERT INTO app_settings (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = NOW()
            """,
            (key, validated),
        )
        db.commit()

        # ── Invalidate cache ────────────────────────────────────────────────────
        settings.invalidate()

        logger.info(f"Setting {key}={validated!r} updated by user {g.user_id}")
        return jsonify({
            "message": "Setting updated",
            "key": key,
            "value": validated,
        }), 200

    except Exception as exc:
        logger.error(f"update_setting error: {exc}", exc_info=True)
        db.rollback()
        return jsonify({"error": "Internal server error"}), 500


# ─── Validation ───────────────────────────────────────────────────────────────

def _validate(key: str, value) -> tuple:
    """
    Returns (validated_str_value, error_message).
    error_message is None if valid.
    """
    value_str = str(value).strip()

    if not value_str:
        return None, "value cannot be empty"

    if key in NUMERIC_RANGES:
        lo, hi = NUMERIC_RANGES[key]
        try:
            num = float(value_str)
        except ValueError:
            return None, f"must be a number"
        if not (lo <= num <= hi):
            return None, f"must be between {lo} and {hi}"
        # Store integers as integers (no decimal)
        if isinstance(lo, int) and isinstance(hi, int):
            return str(int(num)), None
        return str(num), None

    if key == "currency":
        allowed = {"USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD"}
        if value_str not in allowed:
            return None, f"must be one of: {', '.join(sorted(allowed))}"

    if key == "log_level":
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if value_str.upper() not in allowed:
            return None, f"must be one of: {', '.join(sorted(allowed))}"
        return value_str.upper(), None

    if key == "organization_name":
        if len(value_str) > 100:
            return None, "max 100 characters"

    return value_str, None
