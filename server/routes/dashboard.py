"""
GreenOps Dashboard Routes
Adds:
  - POST /api/machines/{id}/sleep
  - POST /api/machines/{id}/shutdown
"""
import logging

from flask import Blueprint, g, jsonify, request

from server.database import db
from server.middleware import require_jwt, validate_status_param
from server.services.machine import MachineService

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api")


@dashboard_bp.route("/machines", methods=["GET"])
@require_jwt
def list_machines():
    try:
        status = request.args.get("status", "").strip() or None
        if status and not validate_status_param(status):
            return jsonify({"error": "Invalid status. Must be: online, idle, offline"}), 422

        try:
            limit = min(int(request.args.get("limit", 100)), 1000)
            offset = max(int(request.args.get("offset", 0)), 0)
        except (TypeError, ValueError):
            return jsonify({"error": "limit and offset must be integers"}), 422

        machines = MachineService.list_machines(status_filter=status, limit=limit, offset=offset)

        if status:
            total_result = db.execute_one(
                "SELECT COUNT(*) AS total FROM machines WHERE status = %s", (status,)
            )
        else:
            total_result = db.execute_one("SELECT COUNT(*) AS total FROM machines")

        total = total_result["total"] if total_result else 0

        formatted = []
        for m in machines:
            uptime_seconds = m.get("uptime_seconds") or 0
            uptime_hours = round(uptime_seconds / 3600.0, 1)

            last_seen = None
            if m.get("last_seen"):
                last_seen = m["last_seen"].strftime("%Y-%m-%dT%H:%M:%SZ")

            formatted.append({
                "id": m["id"],
                "mac_address": m["mac_address"],
                "hostname": m["hostname"],
                "os_type": m["os_type"],
                "status": m["status"],
                "last_seen": last_seen,
                "energy_wasted_kwh": float(m["energy_wasted_kwh"] or 0),
                "uptime_hours": uptime_hours,
                "uptime_seconds": uptime_seconds,
                "total_idle_seconds": m.get("total_idle_seconds") or 0,
            })

        return jsonify({"machines": formatted, "total": total}), 200

    except Exception as exc:
        logger.error(f"List machines error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@dashboard_bp.route("/machines/<int:machine_id>", methods=["GET"])
@require_jwt
def get_machine(machine_id: int):
    try:
        machine = MachineService.get_machine(machine_id)
        if not machine:
            return jsonify({"error": "Machine not found"}), 404

        for field in ["first_seen", "last_seen", "created_at", "updated_at"]:
            if machine.get(field):
                machine[field] = machine[field].strftime("%Y-%m-%dT%H:%M:%SZ")

        if machine.get("energy_wasted_kwh") is not None:
            machine["energy_wasted_kwh"] = float(machine["energy_wasted_kwh"])

        return jsonify(machine), 200

    except Exception as exc:
        logger.error(f"Get machine error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@dashboard_bp.route("/machines/<int:machine_id>/heartbeats", methods=["GET"])
@require_jwt
def get_machine_heartbeats(machine_id: int):
    try:
        limit = min(int(request.args.get("limit", 100)), 1000)
        machine = MachineService.get_machine(machine_id)
        if not machine:
            return jsonify({"error": "Machine not found"}), 404

        heartbeats = db.execute_query(
            """
            SELECT id, timestamp, idle_seconds, cpu_usage, memory_usage, is_idle
            FROM heartbeats
            WHERE machine_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (machine_id, limit),
            fetch=True,
        )

        formatted = [{
            "id": hb["id"],
            "timestamp": hb["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "idle_seconds": hb["idle_seconds"],
            "cpu_usage": float(hb["cpu_usage"]) if hb["cpu_usage"] is not None else None,
            "memory_usage": float(hb["memory_usage"]) if hb["memory_usage"] is not None else None,
            "is_idle": hb["is_idle"],
        } for hb in heartbeats]

        return jsonify({"heartbeats": formatted, "machine_id": machine_id}), 200

    except Exception as exc:
        logger.error(f"Get heartbeats error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@dashboard_bp.route("/dashboard/stats", methods=["GET"])
@require_jwt
def get_dashboard_stats():
    try:
        stats = MachineService.get_dashboard_stats()
        return jsonify(stats), 200
    except Exception as exc:
        logger.error(f"Dashboard stats error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@dashboard_bp.route("/machines/<int:machine_id>", methods=["DELETE"])
@require_jwt
def delete_machine(machine_id: int):
    try:
        machine = MachineService.get_machine(machine_id)
        if not machine:
            return jsonify({"error": "Machine not found"}), 404

        db.execute_query("DELETE FROM machines WHERE id = %s", (machine_id,))
        logger.info(f"Machine deleted: id={machine_id} hostname={machine['hostname']}")
        return jsonify({"message": "Machine deleted successfully"}), 200

    except Exception as exc:
        logger.error(f"Delete machine error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@dashboard_bp.route("/machines/<int:machine_id>/sleep", methods=["POST"])
@require_jwt
def sleep_machine(machine_id: int):
    """
    POST /api/machines/{id}/sleep
    Queues a 'sleep' command for the agent to pick up on its next poll.
    Only valid if machine is online or idle.
    """
    return _queue_command(machine_id, "sleep")


@dashboard_bp.route("/machines/<int:machine_id>/shutdown", methods=["POST"])
@require_jwt
def shutdown_machine(machine_id: int):
    """
    POST /api/machines/{id}/shutdown
    Queues a 'shutdown' command for the agent to pick up on its next poll.
    Only valid if machine is online or idle.
    """
    return _queue_command(machine_id, "shutdown")


def _queue_command(machine_id: int, command: str):
    """Create a pending command record. Agent polls and executes it."""
    try:
        machine = MachineService.get_machine(machine_id)
        if not machine:
            return jsonify({"error": "Machine not found"}), 404

        if machine["status"] == "offline":
            return jsonify({"error": "Cannot send command to offline machine"}), 409

        # Cancel any existing pending commands for this machine
        db.execute_query(
            """
            UPDATE machine_commands SET status = 'expired'
            WHERE machine_id = %s AND status = 'pending'
            """,
            (machine_id,),
        )

        # Queue the new command
        row = db.execute_one(
            """
            INSERT INTO machine_commands (machine_id, command, created_by)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (machine_id, command, g.user_id),
        )

        logger.info(
            f"Command '{command}' queued for machine {machine_id} "
            f"by user {g.user_id} (cmd_id={row['id']})"
        )

        return jsonify({
            "message": f"Command '{command}' queued. Agent will execute on next poll.",
            "command_id": row["id"],
            "machine_id": machine_id,
        }), 202

    except Exception as exc:
        logger.error(f"Queue command error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
