"""
GreenOps — Dashboard Stats Route
==================================
GET /api/dashboard/stats

Returns aggregated fleet statistics.
All thresholds read from DB (via SettingsManager), never from ENV.
Status recomputed for each machine on every request.
"""

import logging

from flask import Blueprint, jsonify

from server.config import settings
from server.database import db
from server.middleware import require_jwt
from server.utils.status import compute_status

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@dashboard_bp.route("/stats", methods=["GET"])
@require_jwt
def get_stats():
    """GET /api/dashboard/stats"""
    try:
        rows = db.execute_query(
            """
            SELECT
                id, last_seen, idle_seconds, uptime_seconds,
                energy_wasted_kwh
            FROM machines
            """,
            fetch=True,
        )

        # Load all relevant settings once
        heartbeat_timeout    = settings.get_int("heartbeat_timeout_seconds")
        idle_threshold       = settings.get_int("idle_threshold_seconds")
        electricity_cost     = settings.get_float("electricity_cost_per_kwh")

        total = len(rows or [])
        online_count  = 0
        idle_count    = 0
        offline_count = 0
        total_energy_kwh   = 0.0
        total_idle_seconds = 0

        for row in (rows or []):
            status = compute_status(
                last_seen=row["last_seen"],
                idle_seconds=row["idle_seconds"],
                heartbeat_timeout_seconds=heartbeat_timeout,
                idle_threshold_seconds=idle_threshold,
            )
            if status == "online":
                online_count += 1
            elif status == "idle":
                idle_count += 1
            else:
                offline_count += 1

            total_energy_kwh   += float(row["energy_wasted_kwh"] or 0)
            total_idle_seconds += int(row["idle_seconds"] or 0)

        estimated_cost = total_energy_kwh * electricity_cost

        # Average idle percentage across fleet
        uptime_rows = db.execute_query(
            "SELECT uptime_seconds, idle_seconds FROM machines",
            fetch=True,
        ) or []
        avg_idle_pct = 0.0
        if uptime_rows:
            pcts = []
            for r in uptime_rows:
                up = int(r["uptime_seconds"] or 0)
                idle = int(r["idle_seconds"] or 0)
                if up > 0:
                    pcts.append(min(100.0, idle / up * 100))
            if pcts:
                avg_idle_pct = sum(pcts) / len(pcts)

        return jsonify({
            "total_machines":           total,
            "online_machines":          online_count,
            "idle_machines":            idle_count,
            "offline_machines":         offline_count,
            "total_energy_wasted_kwh":  round(total_energy_kwh, 4),
            "estimated_cost_usd":       round(estimated_cost, 4),
            "total_idle_seconds":       total_idle_seconds,
            "average_idle_percentage":  round(avg_idle_pct, 2),
        }), 200

    except Exception as exc:
        logger.error(f"get_stats error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
