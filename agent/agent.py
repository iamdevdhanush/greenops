#!/usr/bin/env python3
"""
GreenOps Agent — Cross-Platform
=================================
Supports: Linux (GUI + headless), Windows

Architecture:
    IdleDetector (abstract)
      ├── LinuxIdleDetector  (xprintidle → CPU heuristic fallback)
      └── WindowsIdleDetector (GetLastInputInfo via ctypes)

    MetricsCollector
      └── Uses platform-appropriate IdleDetector

    Agent
      └── Collects metrics every HEARTBEAT_INTERVAL seconds
      └── POSTs to /api/heartbeat
      └── Executes commands returned by server (sleep/shutdown)

Usage:
    python agent.py --server http://localhost:5000 --interval 60

Configuration via ENV (agent-specific only):
    GREENOPS_SERVER_URL   (required)
    GREENOPS_INTERVAL     (optional, default: 60)
    GREENOPS_MACHINE_ID   (optional, auto-detected from MAC)
"""

import argparse
import ctypes
import logging
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import psutil
import requests

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("greenops-agent")


# ═══════════════════════════════════════════════════════════════════════════════
# IDLE DETECTION — ABSTRACT BASE
# ═══════════════════════════════════════════════════════════════════════════════

class IdleDetector(ABC):
    """Abstract base for OS-specific idle detection."""

    @abstractmethod
    def get_idle_seconds(self) -> int:
        """
        Return seconds since last user input.
        Must always return a non-negative integer.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable name for logging."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# LINUX IDLE DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class LinuxIdleDetector(IdleDetector):
    """
    Linux idle detection with two modes:

    1. GUI mode (X11/Wayland session present):
       Uses `xprintidle` — most accurate, measures actual keyboard/mouse input.
       Falls back to CPU heuristic if xprintidle is unavailable.

    2. Headless mode (no DISPLAY env var):
       CPU utilization heuristic: if CPU% stays below threshold for an
       extended period, the system is considered idle.

    Limitations (documented):
    - xprintidle requires X11. Wayland requires xwayland or alternative.
    - CPU heuristic cannot detect background CPU from system processes.
    - Headless mode may classify a lightly-loaded server as idle.
    """

    CPU_IDLE_THRESHOLD = 10.0   # % — below this = potentially idle
    CPU_SAMPLE_INTERVAL = 2.0   # seconds for psutil sample
    CPU_IDLE_MIN_DURATION = 60  # must be below threshold for this long before counting

    def __init__(self) -> None:
        self._xprintidle_available: Optional[bool] = None
        self._has_display: bool = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        self._cpu_idle_since: Optional[float] = None
        logger.info(
            f"LinuxIdleDetector: display={'yes' if self._has_display else 'no'}"
        )

    def name(self) -> str:
        return "Linux"

    def get_idle_seconds(self) -> int:
        if self._has_display:
            result = self._try_xprintidle()
            if result is not None:
                return result
            logger.debug("xprintidle unavailable — falling back to CPU heuristic")

        return self._cpu_heuristic()

    def _try_xprintidle(self) -> Optional[int]:
        """Try xprintidle; returns idle milliseconds or None on failure."""
        if self._xprintidle_available is False:
            return None

        try:
            proc = subprocess.run(
                ["xprintidle"],
                capture_output=True,
                timeout=2,
                text=True,
            )
            if proc.returncode == 0:
                self._xprintidle_available = True
                idle_ms = int(proc.stdout.strip())
                return max(0, idle_ms // 1000)
        except FileNotFoundError:
            self._xprintidle_available = False
            logger.warning(
                "xprintidle not found. Install: apt install xprintidle | "
                "Falling back to CPU heuristic."
            )
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass

        return None

    def _cpu_heuristic(self) -> int:
        """
        CPU-based idle heuristic.
        Returns seconds since CPU went below idle threshold.
        """
        cpu = psutil.cpu_percent(interval=self.CPU_SAMPLE_INTERVAL)
        now = time.monotonic()

        if cpu < self.CPU_IDLE_THRESHOLD:
            if self._cpu_idle_since is None:
                self._cpu_idle_since = now
            idle_duration = now - self._cpu_idle_since
            return max(0, int(idle_duration))
        else:
            # System is active — reset idle timer
            self._cpu_idle_since = None
            return 0


# ═══════════════════════════════════════════════════════════════════════════════
# WINDOWS IDLE DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class WindowsIdleDetector(IdleDetector):
    """
    Windows idle detection using GetLastInputInfo (Win32 API).

    This is the canonical, accurate method on Windows:
    - Tracks keyboard + mouse input at the OS level
    - GetTickCount() gives elapsed time since system boot in milliseconds
    - Subtracting last input tick from current tick = idle time

    No external libraries required — uses ctypes (stdlib).
    """

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("dwTime", ctypes.c_uint),
        ]

    def name(self) -> str:
        return "Windows"

    def get_idle_seconds(self) -> int:
        try:
            lii = self._LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(self._LASTINPUTINFO)

            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                logger.warning("GetLastInputInfo returned False")
                return 0

            # GetTickCount() returns ms since boot (wraps at ~49 days — handle it)
            current_tick = ctypes.windll.kernel32.GetTickCount()
            elapsed_ms = (current_tick - lii.dwTime) & 0xFFFFFFFF  # handle wraparound
            return max(0, elapsed_ms // 1000)

        except OSError as exc:
            logger.error(f"Windows idle detection error: {exc}")
            return 0


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class MetricsCollector:
    """Collects system metrics using the OS-appropriate idle detector."""

    def __init__(self) -> None:
        self._idle_detector = self._create_detector()
        logger.info(f"Idle detector: {self._idle_detector.name()}")

    def _create_detector(self) -> IdleDetector:
        os_name = platform.system()
        if os_name == "Windows":
            return WindowsIdleDetector()
        elif os_name == "Linux":
            return LinuxIdleDetector()
        else:
            # macOS and others: use CPU heuristic via Linux detector
            logger.warning(f"OS '{os_name}' not fully supported; using CPU heuristic")
            return LinuxIdleDetector()

    def collect(self, machine_id: str) -> dict:
        """
        Returns unified payload dict.
        All values are in their canonical units:
          idle_seconds, uptime_seconds → seconds (int)
          cpu_usage, memory_usage       → percent (float 0-100)
        """
        idle_seconds   = self._idle_detector.get_idle_seconds()
        cpu_usage      = psutil.cpu_percent(interval=1)
        memory_usage   = psutil.virtual_memory().percent
        uptime_seconds = self._get_uptime_seconds()
        hostname       = socket.gethostname()
        os_type        = platform.system()

        return {
            "machine_id":     machine_id,
            "hostname":       hostname,
            "os_type":        os_type,
            "idle_seconds":   idle_seconds,
            "cpu_usage":      round(cpu_usage, 1),
            "memory_usage":   round(memory_usage, 1),
            "uptime_seconds": uptime_seconds,
        }

    @staticmethod
    def _get_uptime_seconds() -> int:
        """Returns system uptime in seconds (integer)."""
        boot_ts = psutil.boot_time()
        return max(0, int(time.time() - boot_ts))


# ═══════════════════════════════════════════════════════════════════════════════
# MACHINE ID
# ═══════════════════════════════════════════════════════════════════════════════

def get_machine_id() -> str:
    """
    Returns a stable machine identifier.
    Priority:
      1. GREENOPS_MACHINE_ID env var (explicit override)
      2. First non-loopback MAC address
      3. UUID based on machine hostname (last resort)
    """
    if override := os.environ.get("GREENOPS_MACHINE_ID"):
        return override.strip()

    # Try to get a real MAC address
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac = addr.address
                # Skip loopback and null MACs
                if mac and mac != "00:00:00:00:00:00" and not iface.startswith("lo"):
                    return mac.lower()

    # Last resort — deterministic UUID from hostname
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname()))


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════════════════════════

class Agent:
    def __init__(self, server_url: str, interval: int) -> None:
        self.server_url  = server_url.rstrip("/")
        self.interval    = interval
        self.machine_id  = get_machine_id()
        self.metrics     = MetricsCollector()
        self._running    = True
        self._session    = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._fail_count = 0
        self._token: Optional[str] = None   # ADD THIS
        
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _register(self) -> None:
        """Register with server and obtain auth token."""
        hostname = socket.gethostname()
        resp = self._session.post(
            f"{self.server_url}/api/agents/register",
            json={
                "mac_address": self.machine_id,
                "hostname": hostname,
                "os_type": platform.system(),
                "os_version": platform.version(),
            },
            timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        self._session.headers.update(
            {"Authorization": f"Bearer {self._token}"}
        )
        logger.info(f"Registered: machine_id={data['machine_id']}")

    def run(self) -> None:
        # Register first
        self._register()
        while self._running:
            start = time.monotonic()
            try:
                self._tick()
                self._fail_count = 0
            except Exception as exc:
                self._fail_count += 1
                wait = min(self.interval * self._fail_count, self.MAX_BACKOFF)
                logger.error(f"Tick error (attempt {self._fail_count}): {exc}; retry in {wait}s")
                time.sleep(wait)
                continue
            elapsed = time.monotonic() - start
            time.sleep(max(0, self.interval - elapsed))

    def _tick(self) -> None:
        payload = self.metrics.collect(self.machine_id)
        resp = self._session.post(
            f"{self.server_url}/api/agents/heartbeat",  # FIXED PATH
            json=payload,
            timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
        )
        resp.raise_for_status()
        data = resp.json()
        command = data.get("command")
        if command:
            self._execute_command(command)

    def _execute_command(self, command: str) -> None:
        """Execute a server-issued command."""
        os_name = platform.system()

        if command == "sleep":
            if os_name == "Linux":
                # systemctl suspend — requires appropriate sudoers rule or polkit
                # Alternatives: pm-suspend, s2ram
                subprocess.Popen(["systemctl", "suspend"])
            elif os_name == "Windows":
                subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            else:
                logger.warning(f"Sleep not implemented for OS: {os_name}")

        elif command == "shutdown":
            if os_name == "Linux":
                subprocess.Popen(["shutdown", "-h", "now"])
            elif os_name == "Windows":
                subprocess.Popen(["shutdown", "/s", "/t", "0"])
            else:
                logger.warning(f"Shutdown not implemented for OS: {os_name}")

        else:
            logger.warning(f"Unknown command received: {command!r}")

    def _handle_signal(self, sig, frame) -> None:
        logger.info(f"Received signal {sig} — shutting down gracefully")
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="GreenOps Agent")
    parser.add_argument(
        "--server",
        default=os.environ.get("GREENOPS_SERVER_URL", ""),
        help="Server base URL, e.g. http://192.168.1.100:5000",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("GREENOPS_INTERVAL", "60")),
        help="Heartbeat interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.server:
        logger.error("Server URL is required. Set --server or GREENOPS_SERVER_URL env var.")
        sys.exit(1)

    if args.interval < 5:
        logger.error("Interval must be at least 5 seconds.")
        sys.exit(1)

    agent = Agent(server_url=args.server, interval=args.interval)
    agent.run()


if __name__ == "__main__":
    main()
