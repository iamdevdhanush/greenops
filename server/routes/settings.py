"""
GreenOps Settings Routes

GET  /api/settings      — get all settings as key-value dict
PUT  /api/settings      — bulk update settings
PUT  /api/settings/{key} — update a single setting
"""
import logging
from flask import Blueprint, g, jsonify, request
from server.database import db
from server.middleware import require_jwt

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")

# Settings that are allowed to be changed via the API
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


@settings_bp.route("", methods=["GET"])
@require_jwt
def get_settings():
    """GET /api/settings — returns all settings as a flat dict"""
    try:
        rows = db.execute_query(
            "SELECT key, value FROM app_settings ORDER BY key",
            fetch=True,
        )
        result = {r["key"]: r["value"] for r in (rows or [])}
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

        # Filter to only allowed keys
        updates = {k: str(v) for k, v in data.items() if k in ALLOWED_KEYS}
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

        logger.info(f"Settings updated by user {g.user_id}: {list(updates.keys())}")
        return jsonify({"message": "Settings updated", "updated": list(updates.keys())}), 200

    except Exception as exc:
        logger.error(f"update_settings error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@settings_bp.route("/<key>", methods=["PUT"])
@require_jwt
def update_setting(key: str):
    """PUT /api/settings/{key} — update a single setting"""
    try:
        if key not in ALLOWED_KEYS:
            return jsonify({"error": f"Unknown or read-only setting: {key}"}), 422

        data = request.get_json(silent=True)
        if data is None or "value" not in data:
            return jsonify({"error": "JSON body with 'value' field required"}), 400

        value = str(data["value"])
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
        logger.info(f"Setting {key}={value!r} updated by user {g.user_id}")
        return jsonify({"message": "Setting updated", "key": key, "value": value}), 200

    except Exception as exc:
        logger.error(f"update_setting error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
