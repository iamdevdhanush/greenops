"""
GreenOps — Heartbeat Route
===========================
POST /api/heartbeat

Receives agent metrics, updates machine record, computes status deterministically.

Key fixes vs original:
  - last_seen stored as TIMESTAMPTZ (timezone-aware UTC)
  - idle_threshold and heartbeat_timeout read from DB via SettingsManager
  - Status computed via shared compute_status() — not inline logic
  - Explicit db commit after every write
  - Handles both new machine registration and heartbeat update
  - Returns pending commands to agent
"""

import logging
from datetime import timezone

from flask import Blueprint, jsonify, request

from server.config import settings
from server.database import db
from server.utils.status import compute_status, utcnow

logger = logging.getLogger(__name__)

heartbeat_bp = Blueprint("heartbeat", __name__, url_prefix="/api")


@heartbeat_bp.route("/heartbeat", methods=["POST"])
def heartbeat():
    """
    POST /api/heartbeat

    Expected payload:
    {
        "machine_id":     "aa:bb:cc:dd:ee:ff",   // MAC address used as stable ID
        "hostname":       "my-workstation",
        "os_type":        "Linux",
        "idle_seconds":   342,
        "cpu_usage":      12.4,
        "memory_usage":   67.1,
        "uptime_seconds": 86432
    }

    Returns:
    {
        "status": "ok",
        "machine_status": "idle",
        "command": null | "sleep" | "shutdown"
    }
    """
    raw = request.get_json(silent=True)
    if not raw:
        return jsonify({"error": "JSON body required"}), 400

    # ── Validate required fields ───────────────────────────────────────────────
    machine_id = raw.get("machine_id", "").strip()
    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 422

    hostname      = str(raw.get("hostname", "unknown"))[:255]
    os_type       = str(raw.get("os_type", "unknown"))[:50]
    idle_seconds  = _safe_int(raw.get("idle_seconds"), 0)
    cpu_usage     = _safe_float(raw.get("cpu_usage"), 0.0)
    memory_usage  = _safe_float(raw.get("memory_usage"), 0.0)
    uptime_seconds = _safe_int(raw.get("uptime_seconds"), 0)

    now = utcnow()

    # ── Read thresholds from DB (never from ENV) ───────────────────────────────
    heartbeat_timeout = settings.get_int("heartbeat_timeout_seconds")
    idle_threshold    = settings.get_int("idle_threshold_seconds")
    idle_power_watts  = settings.get_int("idle_power_watts")

    # ── Compute status ─────────────────────────────────────────────────────────
    # Machine just sent a heartbeat — so last_seen = now
    machine_status = compute_status(
        last_seen=now,
        idle_seconds=idle_seconds,
        heartbeat_timeout_seconds=heartbeat_timeout,
        idle_threshold_seconds=idle_threshold,
    )

    # ── Energy calculation ─────────────────────────────────────────────────────
    idle_hours = idle_seconds / 3600.0
    energy_wasted_kwh = idle_hours * (idle_power_watts / 1000.0)

    try:
        # ── Upsert machine record ──────────────────────────────────────────────
        db.execute_query(
            """
            INSERT INTO machines (
                mac_address, hostname, os_type,
                last_seen,
                idle_seconds, cpu_usage, memory_usage, uptime_seconds,
                status, energy_wasted_kwh
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (mac_address) DO UPDATE SET
                hostname        = EXCLUDED.hostname,
                os_type         = EXCLUDED.os_type,
                last_seen       = EXCLUDED.last_seen,
                idle_seconds    = EXCLUDED.idle_seconds,
                cpu_usage       = EXCLUDED.cpu_usage,
                memory_usage    = EXCLUDED.memory_usage,
                uptime_seconds  = EXCLUDED.uptime_seconds,
                status          = EXCLUDED.status,
                energy_wasted_kwh = EXCLUDED.energy_wasted_kwh
            """,
            (
                machine_id, hostname, os_type,
                now,
                idle_seconds, cpu_usage, memory_usage, uptime_seconds,
                machine_status, energy_wasted_kwh,
            ),
        )
        # Explicit commit — don't rely on implicit commit
        db.commit()

        # ── Fetch pending command ──────────────────────────────────────────────
        machine_row = db.execute_query(
            "SELECT id, pending_command FROM machines WHERE mac_address = %s",
            (machine_id,),
            fetch=True,
        )

        command = None
        machine_db_id = None
        if machine_row:
            machine_db_id = machine_row[0]["id"]
            command = machine_row[0].get("pending_command")

            # Clear command after delivery
            if command:
                db.execute_query(
                    "UPDATE machines SET pending_command = NULL WHERE id = %s",
                    (machine_db_id,),
                )
                db.commit()
                logger.info(
                    f"Command '{command}' delivered to machine {machine_id} (id={machine_db_id})"
                )

    except Exception as exc:
        logger.error(f"Heartbeat DB error for {machine_id}: {exc}", exc_info=True)
        db.rollback()
        return jsonify({"error": "Internal server error"}), 500

    logger.debug(
        f"Heartbeat: {machine_id} hostname={hostname} status={machine_status} "
        f"idle={idle_seconds}s cpu={cpu_usage:.1f}% mem={memory_usage:.1f}%"
    )

    return jsonify({
        "status": "ok",
        "machine_status": machine_status,
        "command": command,
    }), 200


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_int(val, default: int) -> int:
    try:
        return int(float(val)) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_float(val, default: float) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
