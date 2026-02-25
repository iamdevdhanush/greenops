"""
GreenOps Machine Service — Production Hardened

Key fixes:
  - process_heartbeat: separate SQL for with/without uptime_seconds instead
    of f-string injection (eliminates the fragile tuple construction).
  - Uptime logging: uses `is not None` instead of truthiness check so
    uptime_seconds=0 is not silently dropped from log output.
  - list_machines / get_machine: return plain dicts, not RealDictRow objects
    (RealDictRow is not JSON-serializable; dict() conversion explicit).
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2.extras as pg_extras

from server.auth import AuthService
from server.config import config
from server.database import db
from server.services.energy import EnergyService

logger = logging.getLogger(__name__)


class MachineService:

    @staticmethod
    def register_machine(
        mac_address: str,
        hostname:    str,
        os_type:     str,
        os_version:  str = None,
    ) -> dict:
        # Normalise MAC to uppercase colon-separated format
        mac_address = mac_address.upper().replace("-", ":")

        result = db.execute_one(
            """
            INSERT INTO machines
                (mac_address, hostname, os_type, os_version,
                 first_seen, last_seen, status)
            VALUES (%s, %s, %s, %s, NOW(), NOW(), 'online')
            ON CONFLICT (mac_address) DO UPDATE
                SET hostname   = EXCLUDED.hostname,
                    os_version = EXCLUDED.os_version,
                    last_seen  = NOW(),
                    status     = 'online',
                    updated_at = NOW()
            RETURNING id, (xmax = 0) AS inserted
            """,
            (mac_address, hostname, os_type, os_version),
        )

        machine_id   = result["id"]
        was_inserted = result["inserted"]
        token        = AuthService.create_agent_token(machine_id)
        message = (
            "Machine registered successfully"
            if was_inserted
            else "Machine already registered — token refreshed"
        )

        logger.info(
            f"Registration: mac={mac_address[:8]}*** "
            f"id={machine_id} new={was_inserted}"
        )
        return {"machine_id": machine_id, "token": token, "message": message}

    @staticmethod
    def process_heartbeat(
        machine_id:     int,
        idle_seconds:   int,
        cpu_usage:      float   = None,
        memory_usage:   float   = None,
        uptime_seconds: int     = None,
        timestamp:      datetime = None,
    ) -> dict:
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        is_idle = idle_seconds >= config.IDLE_THRESHOLD_SECONDS
        status  = "idle" if is_idle else "online"

        # Calculate incremental idle time (avoids double-counting across reboots)
        last_hb = db.execute_one(
            """
            SELECT timestamp, idle_seconds
            FROM   heartbeats
            WHERE  machine_id = %s
            ORDER  BY timestamp DESC
            LIMIT  1
            """,
            (machine_id,),
        )

        if last_hb and last_hb["timestamp"]:
            last_ts = last_hb["timestamp"]
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)

            time_since_last   = (timestamp - last_ts).total_seconds()
            was_idle_before   = last_hb["idle_seconds"] >= config.IDLE_THRESHOLD_SECONDS
            if is_idle and was_idle_before:
                incremental_idle = min(max(time_since_last, 0), idle_seconds)
            else:
                incremental_idle = 0
        else:
            incremental_idle = idle_seconds if is_idle else 0

        energy_waste = EnergyService.calculate_idle_energy_waste(int(incremental_idle))

        # Use two separate SQL statements instead of f-string construction.
        # This eliminates the fragile tuple-count-must-match-placeholder pattern.
        with db.get_connection() as conn:
            with conn.cursor(cursor_factory=pg_extras.RealDictCursor) as cur:
                if uptime_seconds is not None:
                    cur.execute(
                        """
                        UPDATE machines
                        SET    last_seen          = %s,
                               status             = %s,
                               total_idle_seconds = total_idle_seconds + %s,
                               energy_wasted_kwh  = energy_wasted_kwh  + %s,
                               uptime_seconds     = %s,
                               updated_at         = NOW()
                        WHERE  id = %s
                        RETURNING energy_wasted_kwh
                        """,
                        (
                            timestamp, status,
                            int(incremental_idle), energy_waste,
                            uptime_seconds,
                            machine_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE machines
                        SET    last_seen          = %s,
                               status             = %s,
                               total_idle_seconds = total_idle_seconds + %s,
                               energy_wasted_kwh  = energy_wasted_kwh  + %s,
                               updated_at         = NOW()
                        WHERE  id = %s
                        RETURNING energy_wasted_kwh
                        """,
                        (
                            timestamp, status,
                            int(incremental_idle), energy_waste,
                            machine_id,
                        ),
                    )

                updated = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO heartbeats
                        (machine_id, timestamp, idle_seconds,
                         cpu_usage, memory_usage, is_idle)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        machine_id, timestamp, idle_seconds,
                        cpu_usage, memory_usage, is_idle,
                    ),
                )

        if not updated:
            raise ValueError(f"Machine id={machine_id} not found — heartbeat rejected.")

        uptime_log = (
            f" uptime={uptime_seconds}s"
            if uptime_seconds is not None   # explicit None check, not truthiness
            else ""
        )
        logger.info(
            f"Heartbeat: id={machine_id} status={status} "
            f"idle={idle_seconds}s increment={int(incremental_idle)}s "
            f"energy_delta={energy_waste} kWh{uptime_log}"
        )

        return {
            "status":            "ok",
            "machine_status":    status,
            "energy_wasted_kwh": float(updated["energy_wasted_kwh"]),
            "is_idle":           is_idle,
        }

    @staticmethod
    def get_machine(machine_id: int) -> dict | None:
        row = db.execute_one(
            """
            SELECT id, mac_address, hostname, os_type, os_version,
                   first_seen, last_seen, total_idle_seconds,
                   total_active_seconds, energy_wasted_kwh,
                   status, uptime_seconds, created_at, updated_at
            FROM   machines
            WHERE  id = %s
            """,
            (machine_id,),
        )
        return dict(row) if row else None

    @staticmethod
    def list_machines(
        status_filter: str = None,
        limit:  int = 100,
        offset: int = 0,
    ) -> list[dict]:
        if status_filter:
            rows = db.execute_query(
                """
                SELECT id, mac_address, hostname, os_type, status, last_seen,
                       energy_wasted_kwh, total_idle_seconds,
                       total_active_seconds, uptime_seconds
                FROM   machines
                WHERE  status = %s
                ORDER  BY last_seen DESC
                LIMIT  %s OFFSET %s
                """,
                (status_filter, limit, offset),
                fetch=True,
            )
        else:
            rows = db.execute_query(
                """
                SELECT id, mac_address, hostname, os_type, status, last_seen,
                       energy_wasted_kwh, total_idle_seconds,
                       total_active_seconds, uptime_seconds
                FROM   machines
                ORDER  BY last_seen DESC
                LIMIT  %s OFFSET %s
                """,
                (limit, offset),
                fetch=True,
            )
        return [dict(r) for r in (rows or [])]

    @staticmethod
    def get_dashboard_stats() -> dict:
        stats = db.execute_one(
            """
            SELECT
                COUNT(*)                                          AS total_machines,
                COUNT(CASE WHEN status = 'online'  THEN 1 END)   AS online_machines,
                COUNT(CASE WHEN status = 'idle'    THEN 1 END)   AS idle_machines,
                COUNT(CASE WHEN status = 'offline' THEN 1 END)   AS offline_machines,
                COALESCE(SUM(energy_wasted_kwh),    0)           AS total_energy_wasted_kwh,
                COALESCE(SUM(total_idle_seconds),   0)           AS total_idle_seconds,
                COALESCE(SUM(total_active_seconds), 0)           AS total_active_seconds
            FROM machines
            """
        )

        if not stats or int(stats["total_machines"]) == 0:
            return {
                "total_machines":          0,
                "online_machines":         0,
                "idle_machines":           0,
                "offline_machines":        0,
                "total_energy_wasted_kwh": 0.0,
                "estimated_cost_usd":      0.0,
                "average_idle_percentage": 0.0,
            }

        cost = EnergyService.calculate_cost(
            Decimal(str(stats["total_energy_wasted_kwh"]))
        )
        total_time = (
            int(stats["total_idle_seconds"]) + int(stats["total_active_seconds"])
        )
        avg_idle_pct = (
            round(int(stats["total_idle_seconds"]) / total_time * 100, 1)
            if total_time > 0
            else 0.0
        )

        return {
            "total_machines":          int(stats["total_machines"]),
            "online_machines":         int(stats["online_machines"]),
            "idle_machines":           int(stats["idle_machines"]),
            "offline_machines":        int(stats["offline_machines"]),
            "total_energy_wasted_kwh": float(stats["total_energy_wasted_kwh"]),
            "estimated_cost_usd":      float(cost),
            "average_idle_percentage": avg_idle_pct,
        }

    @staticmethod
    def update_offline_machines() -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=config.HEARTBEAT_TIMEOUT_SECONDS
        )
        updated = db.execute_query(
            """
            UPDATE machines
            SET    status     = 'offline',
                   updated_at = NOW()
            WHERE  last_seen  < %s
              AND  status    != 'offline'
            RETURNING id, hostname
            """,
            (cutoff,),
            fetch=True,
        )
        count = len(updated) if updated else 0
        for m in (updated or []):
            logger.info(f"Machine offline: id={m['id']} hostname={m['hostname']}")
        return count
