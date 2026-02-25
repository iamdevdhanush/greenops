"""
GreenOps — Machines Route
==========================
GET    /api/machines           — list all machines with computed status
GET    /api/machines/<id>      — single machine detail
POST   /api/machines/<id>/sleep     — queue sleep command
POST   /api/machines/<id>/shutdown  — queue shutdown command
DELETE /api/machines/<id>      — remove machine from registry

Status is always computed using the shared compute_status() function
with DB-sourced thresholds. Never computed inline.
"""

import logging

from flask import Blueprint, g, jsonify

from server.config import settings
from server.database import db
from server.middleware import require_jwt
from server.utils.status import compute_status

logger = logging.getLogger(__name__)

machines_bp = Blueprint("machines", __name__, url_prefix="/api/machines")


@machines_bp.route("", methods=["GET"])
@require_jwt
def list_machines():
    """GET /api/machines — all machines with recomputed status."""
    try:
        rows = db.execute_query(
            """
            SELECT
                id, mac_address, hostname, os_type,
                last_seen,
                idle_seconds, cpu_usage, memory_usage, uptime_seconds,
                status AS stored_status,
                energy_wasted_kwh,
                pending_command,
                created_at
            FROM machines
            ORDER BY hostname
            """,
            fetch=True,
        )

        # Read thresholds from DB once for this request
        heartbeat_timeout = settings.get_int("heartbeat_timeout_seconds")
        idle_threshold    = settings.get_int("idle_threshold_seconds")

        machines = []
        for row in (rows or []):
            # Always recompute status — never trust the stored value blindly
            current_status = compute_status(
                last_seen=row["last_seen"],
                idle_seconds=row["idle_seconds"],
                heartbeat_timeout_seconds=heartbeat_timeout,
                idle_threshold_seconds=idle_threshold,
            )

            machines.append({
                "id":                 row["id"],
                "mac_address":        row["mac_address"],
                "hostname":           row["hostname"],
                "os_type":            row["os_type"],
                "last_seen":          row["last_seen"].isoformat() if row["last_seen"] else None,
                "idle_seconds":       row["idle_seconds"] or 0,
                "cpu_usage":          row["cpu_usage"] or 0.0,
                "memory_usage":       row["memory_usage"] or 0.0,
                "uptime_seconds":     row["uptime_seconds"] or 0,
                "status":             current_status,
                "energy_wasted_kwh":  row["energy_wasted_kwh"] or 0.0,
                "pending_command":    row["pending_command"],
                "created_at":         row["created_at"].isoformat() if row["created_at"] else None,
            })

        return jsonify({"machines": machines, "count": len(machines)}), 200

    except Exception as exc:
        logger.error(f"list_machines error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@machines_bp.route("/<int:machine_id>", methods=["GET"])
@require_jwt
def get_machine(machine_id: int):
    """GET /api/machines/<id>"""
    try:
        rows = db.execute_query(
            "SELECT * FROM machines WHERE id = %s",
            (machine_id,),
            fetch=True,
        )
        if not rows:
            return jsonify({"error": "Machine not found"}), 404

        row = rows[0]
        heartbeat_timeout = settings.get_int("heartbeat_timeout_seconds")
        idle_threshold    = settings.get_int("idle_threshold_seconds")

        current_status = compute_status(
            last_seen=row["last_seen"],
            idle_seconds=row["idle_seconds"],
            heartbeat_timeout_seconds=heartbeat_timeout,
            idle_threshold_seconds=idle_threshold,
        )

        return jsonify({
            "id":              row["id"],
            "mac_address":     row["mac_address"],
            "hostname":        row["hostname"],
            "os_type":         row["os_type"],
            "last_seen":       row["last_seen"].isoformat() if row["last_seen"] else None,
            "idle_seconds":    row["idle_seconds"] or 0,
            "cpu_usage":       row["cpu_usage"] or 0.0,
            "memory_usage":    row["memory_usage"] or 0.0,
            "uptime_seconds":  row["uptime_seconds"] or 0,
            "status":          current_status,
            "energy_wasted_kwh": row["energy_wasted_kwh"] or 0.0,
            "pending_command": row["pending_command"],
        }), 200

    except Exception as exc:
        logger.error(f"get_machine error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@machines_bp.route("/<int:machine_id>/sleep", methods=["POST"])
@require_jwt
def queue_sleep(machine_id: int):
    """POST /api/machines/<id>/sleep — queue sleep command."""
    return _queue_command(machine_id, "sleep")


@machines_bp.route("/<int:machine_id>/shutdown", methods=["POST"])
@require_jwt
def queue_shutdown(machine_id: int):
    """POST /api/machines/<id>/shutdown — queue shutdown command."""
    return _queue_command(machine_id, "shutdown")


@machines_bp.route("/<int:machine_id>", methods=["DELETE"])
@require_jwt
def delete_machine(machine_id: int):
    """DELETE /api/machines/<id> — remove machine from registry."""
    try:
        rows = db.execute_query(
            "SELECT id, hostname FROM machines WHERE id = %s",
            (machine_id,),
            fetch=True,
        )
        if not rows:
            return jsonify({"error": "Machine not found"}), 404

        hostname = rows[0]["hostname"]
        db.execute_query("DELETE FROM machines WHERE id = %s", (machine_id,))
        db.commit()
        logger.info(f"Machine {machine_id} ({hostname}) deleted by user {g.user_id}")
        return jsonify({"message": f"Machine {machine_id} removed"}), 200

    except Exception as exc:
        logger.error(f"delete_machine error: {exc}", exc_info=True)
        db.rollback()
        return jsonify({"error": "Internal server error"}), 500


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _queue_command(machine_id: int, command: str):
    try:
        rows = db.execute_query(
            "SELECT id, status FROM machines WHERE id = %s",
            (machine_id,),
            fetch=True,
        )
        if not rows:
            return jsonify({"error": "Machine not found"}), 404

        db.execute_query(
            "UPDATE machines SET pending_command = %s WHERE id = %s",
            (command, machine_id),
        )
        db.commit()
        logger.info(f"Command '{command}' queued for machine {machine_id} by user {g.user_id}")
        return jsonify({
            "message": f"Command '{command}' queued for machine {machine_id}",
            "command": command,
        }), 200

    except Exception as exc:
        logger.error(f"queue_command error: {exc}", exc_info=True)
        db.rollback()
        return jsonify({"error": "Internal server error"}), 500
