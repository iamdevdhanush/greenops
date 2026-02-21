"""
GreenOps Agent Routes
Adds:
  - GET  /api/agents/commands         — agent polls for pending commands
  - POST /api/agents/commands/{id}/result — agent reports execution result
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from server.database import db
from server.middleware import require_agent_token
from server.services.machine import MachineService

logger = logging.getLogger(__name__)

agents_bp = Blueprint("agents", __name__, url_prefix="/api/agents")


@agents_bp.route("/health", methods=["GET"])
def health():
    try:
        db.execute_one("SELECT 1")
        return jsonify({
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }), 200
    except Exception as exc:
        logger.error(f"Health check failed: {exc}")
        return jsonify({"status": "unhealthy", "database": "disconnected"}), 503


@agents_bp.route("/register", methods=["POST"])
def register():
    """
    POST /api/agents/register  (idempotent)
    Body: {mac_address, hostname, os_type, os_version}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "JSON body required"}), 400

        required = ["mac_address", "hostname", "os_type"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

        result = MachineService.register_machine(
            mac_address=str(data["mac_address"]).strip()[:17],
            hostname=str(data["hostname"]).strip()[:255],
            os_type=str(data["os_type"]).strip()[:50],
            os_version=str(data.get("os_version", "")).strip()[:100] or None,
        )

        logger.info(f"Agent registered: mac={data['mac_address'][:8]}*** msg={result['message']}")
        return jsonify(result), 200

    except Exception as exc:
        logger.error(f"Agent registration error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@agents_bp.route("/heartbeat", methods=["POST"])
@require_agent_token
def heartbeat():
    """
    POST /api/agents/heartbeat
    Headers: Authorization: Bearer <agent_token>
    Body: {idle_seconds, cpu_usage, memory_usage, uptime_seconds, timestamp}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "JSON body required"}), 400

        if "idle_seconds" not in data:
            return jsonify({"error": "idle_seconds required"}), 400

        try:
            idle_seconds = int(data["idle_seconds"])
            if idle_seconds < 0:
                return jsonify({"error": "idle_seconds must be non-negative"}), 422
        except (TypeError, ValueError):
            return jsonify({"error": "idle_seconds must be an integer"}), 422

        cpu_usage = None
        if data.get("cpu_usage") is not None:
            try:
                cpu_usage = float(data["cpu_usage"])
            except (TypeError, ValueError):
                pass

        memory_usage = None
        if data.get("memory_usage") is not None:
            try:
                memory_usage = float(data["memory_usage"])
            except (TypeError, ValueError):
                pass

        uptime_seconds = None
        if data.get("uptime_seconds") is not None:
            try:
                uptime_seconds = int(data["uptime_seconds"])
            except (TypeError, ValueError):
                pass

        timestamp = None
        if data.get("timestamp"):
            try:
                raw = str(data["timestamp"]).replace("Z", "+00:00")
                timestamp = datetime.fromisoformat(raw)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                return jsonify({"error": "Invalid timestamp format. Use ISO 8601."}), 422

        result = MachineService.process_heartbeat(
            machine_id=g.machine_id,
            idle_seconds=idle_seconds,
            cpu_usage=cpu_usage,
            memory_usage=memory_usage,
            uptime_seconds=uptime_seconds,
            timestamp=timestamp,
        )

        return jsonify(result), 200

    except ValueError as exc:
        return jsonify({"error": f"Invalid data: {exc}"}), 422
    except Exception as exc:
        logger.error(f"Heartbeat error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@agents_bp.route("/commands", methods=["GET"])
@require_agent_token
def get_commands():
    """
    GET /api/agents/commands
    Agent polls this every heartbeat interval for pending commands.
    Returns pending commands for the authenticated machine.
    Marks them 'pending' (they stay pending until the agent reports result).
    """
    try:
        commands = db.execute_query(
            """
            SELECT id, command
            FROM machine_commands
            WHERE machine_id = %s
              AND status = 'pending'
            ORDER BY created_at ASC
            LIMIT 5
            """,
            (g.machine_id,),
            fetch=True,
        )
        return jsonify({
            "commands": [{"id": c["id"], "command": c["command"]} for c in (commands or [])],
        }), 200
    except Exception as exc:
        logger.error(f"Get commands error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@agents_bp.route("/commands/<int:command_id>/result", methods=["POST"])
@require_agent_token
def report_command_result(command_id: int):
    """
    POST /api/agents/commands/{id}/result
    Body: {"status": "executed" | "failed", "message": "optional detail"}
    """
    try:
        data = request.get_json(silent=True) or {}
        status = data.get("status", "executed")
        if status not in ("executed", "failed"):
            status = "executed"

        message = str(data.get("message", ""))[:500] or None

        updated = db.execute_query(
            """
            UPDATE machine_commands
            SET status = %s, executed_at = NOW(), result_msg = %s
            WHERE id = %s AND machine_id = %s AND status = 'pending'
            """,
            (status, message, command_id, g.machine_id),
        )

        if not updated:
            return jsonify({"error": "Command not found or already processed"}), 404

        logger.info(
            f"Command {command_id} reported {status} by machine {g.machine_id}: {message}"
        )
        return jsonify({"message": "Result recorded"}), 200

    except Exception as exc:
        logger.error(f"Command result error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
