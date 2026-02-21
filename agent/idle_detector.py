"""
GreenOps Agent — Idle Detection & System Info
Platform-specific implementations for idle time and system uptime.

Idle detection:
  Windows  — GetLastInputInfo (Win32, accurate to milliseconds)
  Linux    — xprintidle (X11, most accurate for GUI sessions)
             /proc/stat CPU delta (server/headless fallback)
  macOS    — ioreg IOHIDSystem HIDIdleTime (nanoseconds, accurate)

Uptime detection:
  Linux    — /proc/uptime (first field, kernel uptime in seconds)
  Windows  — GetTickCount64 (milliseconds since boot)
  macOS    — sysctl kern.boottime
"""

import logging
import os
import platform
import subprocess
import time

logger = logging.getLogger(__name__)


class IdleDetector:
    """Detect user idle time and system uptime across platforms."""

    def __init__(self):
        self.platform = platform.system()
        self._display = os.environ.get("DISPLAY", ":0")
        logger.info(
            f"IdleDetector initialised: platform={self.platform}, "
            f"DISPLAY={self._display}"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_idle_seconds(self) -> int:
        """
        Seconds since last keyboard/mouse input.
        Returns 0 on any failure (safe default — avoids false idle marking).
        """
        try:
            if self.platform == "Windows":
                return self._idle_windows()
            elif self.platform == "Linux":
                return self._idle_linux()
            elif self.platform == "Darwin":
                return self._idle_macos()
            else:
                logger.warning(f"Unsupported platform for idle detection: {self.platform}")
                return 0
        except Exception as exc:
            logger.error(f"Idle detection failed: {exc}")
            return 0

    def get_uptime_seconds(self) -> int:
        """
        Seconds since the system last booted.
        This is the accurate uptime — not based on heartbeat intervals.
        Returns 0 on failure.
        """
        try:
            if self.platform == "Linux":
                return self._uptime_linux()
            elif self.platform == "Windows":
                return self._uptime_windows()
            elif self.platform == "Darwin":
                return self._uptime_macos()
            else:
                return 0
        except Exception as exc:
            logger.error(f"Uptime detection failed: {exc}")
            return 0

    # ── Idle implementations ──────────────────────────────────────────────────

    def _idle_windows(self) -> int:
        """Win32 GetLastInputInfo — accurate to 1 ms."""
        import ctypes
        from ctypes import Structure, c_uint, sizeof, byref, windll

        class LASTINPUTINFO(Structure):
            _fields_ = [("cbSize", c_uint), ("dwTime", c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = sizeof(LASTINPUTINFO)
        windll.user32.GetLastInputInfo(byref(lii))
        millis = windll.kernel32.GetTickCount() - lii.dwTime
        return max(int(millis / 1000.0), 0)

    def _idle_linux(self) -> int:
        """
        Linux idle detection — tries three methods in order:
        1. xprintidle (X11, most accurate for GUI sessions)
        2. DBUS screensaver idle (Wayland / GUI alternative)
        3. Returns 0 with a startup warning for headless servers
           (correct — a headless server is never "user idle").
        """
        # Method 1: xprintidle (requires X11 + xprintidle package)
        try:
            env = {**os.environ, "DISPLAY": self._display}
            result = subprocess.run(
                ["xprintidle"],
                capture_output=True,
                text=True,
                timeout=2,
                env=env,
            )
            if result.returncode == 0:
                idle_ms = int(result.stdout.strip())
                return max(int(idle_ms / 1000.0), 0)
        except FileNotFoundError:
            logger.debug(
                "xprintidle not found. Install with: sudo apt install xprintidle"
            )
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass

        # Method 2: DBUS org.gnome.ScreenSaver / org.freedesktop.ScreenSaver
        try:
            result = subprocess.run(
                [
                    "dbus-send", "--session", "--dest=org.freedesktop.ScreenSaver",
                    "--type=method_call", "--print-reply",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver.GetSessionIdleTime",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                env={**os.environ, "DISPLAY": self._display},
            )
            if result.returncode == 0:
                # Output: '   uint32 12345'
                for part in result.stdout.split():
                    try:
                        idle_ms = int(part)
                        return max(int(idle_ms / 1000.0), 0)
                    except ValueError:
                        continue
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        # Method 3: headless/server — not idle (no user to be idle)
        logger.debug(
            "No GUI idle detection available. "
            "For GUI machines: sudo apt install xprintidle"
        )
        return 0

    def _idle_macos(self) -> int:
        """macOS ioreg HIDIdleTime — nanosecond precision."""
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "HIDIdleTime" in line:
                    try:
                        idle_ns = int(line.split("=")[-1].strip())
                        return max(int(idle_ns / 1_000_000_000), 0)
                    except ValueError:
                        pass
        return 0

    # ── Uptime implementations ────────────────────────────────────────────────

    def _uptime_linux(self) -> int:
        """
        /proc/uptime — first field is uptime in seconds (float).
        This is the most accurate and lightweight method on Linux.
        """
        with open("/proc/uptime", "r") as f:
            uptime_str = f.read().split()[0]
        return max(int(float(uptime_str)), 0)

    def _uptime_windows(self) -> int:
        """GetTickCount64 — milliseconds since boot, avoids 49-day rollover."""
        import ctypes
        millis = ctypes.windll.kernel32.GetTickCount64()
        return max(int(millis / 1000), 0)

    def _uptime_macos(self) -> int:
        """sysctl kern.boottime — returns boot time as a struct timeval."""
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            # Format: { sec = 1708000000, usec = 123456 } Wed Feb 15 10:00:00 2024
            import re
            match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
            if match:
                boot_time = int(match.group(1))
                return max(int(time.time()) - boot_time, 0)
        return 0
