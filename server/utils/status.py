"""
GreenOps — Status Computation
==============================
Single, deterministic function for computing machine status.

Rules (non-negotiable):
  ONLINE:  heartbeat within timeout  AND  idle_seconds < idle_threshold
  IDLE:    heartbeat within timeout  AND  idle_seconds >= idle_threshold
  OFFLINE: no heartbeat within timeout  (regardless of idle_seconds)

Always uses timezone-aware UTC. Never called with naive datetimes.

Usage:
    from server.utils.status import compute_status, utcnow
"""

from datetime import datetime, timedelta, timezone
from typing import Optional


def utcnow() -> datetime:
    """Return current time as timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Convert a naive datetime to UTC-aware.
    If already aware, return as-is.
    If None, return None.

    PostgreSQL TIMESTAMPTZ returns aware datetimes.
    PostgreSQL TIMESTAMP (without TZ) returns naive datetimes.
    This function handles both safely.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume naive datetimes stored in DB are UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt


def compute_status(
    last_seen: Optional[datetime],
    idle_seconds: Optional[float],
    heartbeat_timeout_seconds: int,
    idle_threshold_seconds: int,
) -> str:
    """
    Compute machine status deterministically.

    Args:
        last_seen: Last heartbeat timestamp (aware or naive — both handled).
        idle_seconds: Seconds of inactivity from the agent.
        heartbeat_timeout_seconds: Seconds before machine is considered offline.
        idle_threshold_seconds: Seconds of idle before marking as idle.

    Returns:
        'online' | 'idle' | 'offline'
    """
    now = utcnow()

    # ── Check heartbeat freshness ─────────────────────────────────────────────
    if last_seen is None:
        return "offline"

    last_seen_aware = ensure_aware(last_seen)
    seconds_since_heartbeat = (now - last_seen_aware).total_seconds()

    if seconds_since_heartbeat > heartbeat_timeout_seconds:
        return "offline"

    # ── Heartbeat is fresh — check idle state ─────────────────────────────────
    idle = float(idle_seconds) if idle_seconds is not None else 0.0
    if idle >= idle_threshold_seconds:
        return "idle"

    return "online"


def compute_status_from_row(row: dict, heartbeat_timeout_seconds: int, idle_threshold_seconds: int) -> str:
    """
    Convenience wrapper that accepts a machine dict/row directly.

    Expects keys: last_seen, idle_seconds (or total_idle_seconds for legacy compat).
    """
    last_seen = row.get("last_seen")
    idle_seconds = row.get("idle_seconds") or row.get("total_idle_seconds") or 0
    return compute_status(
        last_seen=last_seen,
        idle_seconds=idle_seconds,
        heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        idle_threshold_seconds=idle_threshold_seconds,
    )
