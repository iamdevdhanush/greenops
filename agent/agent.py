"""
GreenOps Agent v2.0
Heartbeat interval: 60 seconds (configurable)
Sends uptime_seconds from /proc/uptime (accurate system uptime)
Polls for remote commands (sleep / shutdown) on every heartbeat
Graceful shutdown on SIGINT/SIGTERM
"""
import os
import sys
import time
import logging
import platform
import signal
import subprocess
import uuid
import requests
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from config import config
from idle_detector import IdleDetector

# ── Logging ───────────────────────────────────────────────────────────────────
log_file = config.config_dir / "agent.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── Remote command execution ──────────────────────────────────────────────────

def _execute_command(command: str) -> tuple[bool, str]:
    """Execute a remote command (sleep or shutdown). Returns (success, message)."""
    os_name = platform.system()

    if command == "sleep":
        if os_name == "Linux":
            cmds = [["systemctl", "suspend"], ["pm-suspend"]]
        elif os_name == "Windows":
            cmds = [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]]
        elif os_name == "Darwin":
            cmds = [["pmset", "sleepnow"]]
        else:
            return False, f"Unsupported OS: {os_name}"

    elif command == "shutdown":
        if os_name == "Linux":
            cmds = [["systemctl", "poweroff"], ["shutdown", "-h", "now"]]
        elif os_name == "Windows":
            cmds = [["shutdown", "/s", "/t", "0"]]
        elif os_name == "Darwin":
            cmds = [["shutdown", "-h", "now"]]
        else:
            return False, f"Unsupported OS: {os_name}"

    else:
        return False, f"Unknown command: {command}"

    for cmd in cmds:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.info(f"Command '{command}' executed via {cmd[0]}")
                return True, f"Executed: {' '.join(cmd)}"
            else:
                logger.warning(f"Command {cmd[0]} returned {result.returncode}: {result.stderr.strip()}")
        except FileNotFoundError:
            logger.debug(f"{cmd[0]} not found, trying next")
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {' '.join(cmd)}")
        except Exception as exc:
            logger.error(f"Command error {cmd[0]}: {exc}")

    return False, f"All methods to execute '{command}' failed"


# ── Agent ─────────────────────────────────────────────────────────────────────

class GreenOpsAgent:

    def __init__(self):
        self.config = config
        self.idle_detector = IdleDetector()
        self.token = None
        self.machine_id = None
        self.running = True
        self.retry_delay = self.config.retry_backoff_base
        self.consecutive_failures = 0

        self.mac_address = self._get_mac_address()
        self.hostname = platform.node()
        self.os_type = platform.system()
        self.os_version = platform.version()

        logger.info("GreenOps Agent v2.0 initialised")
        logger.info(f"System: {self.hostname} ({self.os_type})")
        logger.info(f"MAC: {self.mac_address}")
        logger.info(f"Server: {self.config.server_url}")
        logger.info(f"Heartbeat interval: {self.config.heartbeat_interval}s")

    @staticmethod
    def _get_mac_address() -> str:
        try:
            mac_int = uuid.getnode()
            mac_hex = f"{mac_int:012x}"
            return ":".join(mac_hex[i:i+2] for i in range(0, 12, 2)).upper()
        except Exception as exc:
            logger.error(f"Failed to get MAC: {exc}")
            return "00:00:00:00:00:00"

    def register(self) -> bool:
        logger.info("Registering with server …")
        try:
            resp = requests.post(
                f"{self.config.server_url}/api/agents/register",
                json={
                    "mac_address": self.mac_address,
                    "hostname": self.hostname,
                    "os_type": self.os_type,
                    "os_version": self.os_version,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.token = data["token"]
                self.machine_id = data["machine_id"]
                self.config.save_token(self.token)
                logger.info(
                    f"Registered successfully. Machine ID: {self.machine_id} "
                    f"({data.get('message', '')})"
                )
                return True
            else:
                logger.error(f"Registration failed: {resp.status_code} {resp.text}")
                return False
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to {self.config.server_url}")
            return False
        except Exception as exc:
            logger.error(f"Registration error: {exc}")
            return False

    def send_heartbeat(self) -> bool:
        if not self.token:
            return False
        try:
            idle_seconds = self.idle_detector.get_idle_seconds()
            uptime_seconds = self.idle_detector.get_uptime_seconds()

            resp = requests.post(
                f"{self.config.server_url}/api/agents/heartbeat",
                json={
                    "idle_seconds": idle_seconds,
                    "cpu_usage": 0.0,
                    "memory_usage": 0.0,
                    "uptime_seconds": uptime_seconds,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.debug(
                    f"Heartbeat OK — idle={idle_seconds}s, "
                    f"uptime={uptime_seconds}s, "
                    f"status={data.get('machine_status')}"
                )
                return True
            elif resp.status_code == 401:
                logger.error("Token rejected — re-registering")
                self.token = None
                return False
            else:
                logger.error(f"Heartbeat failed: {resp.status_code} {resp.text}")
                return False
        except requests.exceptions.ConnectionError:
            logger.warning("Cannot reach server (will retry)")
            return False
        except Exception as exc:
            logger.error(f"Heartbeat error: {exc}")
            return False

    def poll_commands(self) -> None:
        """Check for and execute pending remote commands."""
        if not self.token:
            return
        try:
            resp = requests.get(
                f"{self.config.server_url}/api/agents/commands",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
            )
            if resp.status_code != 200:
                return

            commands = resp.json().get("commands", [])
            for cmd in commands:
                cmd_id = cmd["id"]
                command = cmd["command"]
                logger.info(f"Executing remote command: {command} (id={cmd_id})")

                success, message = _execute_command(command)
                status = "executed" if success else "failed"

                try:
                    requests.post(
                        f"{self.config.server_url}/api/agents/commands/{cmd_id}/result",
                        json={"status": status, "message": message},
                        headers={"Authorization": f"Bearer {self.token}"},
                        timeout=5,
                    )
                except Exception as exc:
                    logger.error(f"Failed to report command result: {exc}")

        except requests.exceptions.ConnectionError:
            pass  # Server unreachable — not critical for command polling
        except Exception as exc:
            logger.error(f"Command poll error: {exc}")

    def run(self):
        logger.info("Agent starting …")
        self.token = self.config.load_token()
        if self.token:
            logger.info("Loaded existing token")
        else:
            logger.info("No token found — will register on first cycle")

        while self.running:
            try:
                if not self.token:
                    if self.register():
                        self.retry_delay = self.config.retry_backoff_base
                        self.consecutive_failures = 0
                    else:
                        self.consecutive_failures += 1
                        self._backoff()
                        continue

                if self.send_heartbeat():
                    self.poll_commands()
                    self.retry_delay = self.config.retry_backoff_base
                    self.consecutive_failures = 0
                    time.sleep(self.config.heartbeat_interval)
                else:
                    self.consecutive_failures += 1
                    self._backoff()

            except KeyboardInterrupt:
                logger.info("Interrupted — shutting down")
                break
            except Exception as exc:
                logger.error(f"Unexpected error: {exc}", exc_info=True)
                self.consecutive_failures += 1
                self._backoff()

        logger.info("Agent stopped.")

    def _backoff(self):
        if self.consecutive_failures >= self.config.max_retry_attempts:
            logger.warning(
                f"{self.consecutive_failures} consecutive failures. "
                f"Retrying in {self.retry_delay}s …"
            )
        else:
            logger.info(f"Retry in {self.retry_delay}s …")
        time.sleep(self.retry_delay)
        self.retry_delay = min(self.retry_delay * 2, self.config.retry_backoff_max)

    def shutdown(self):
        logger.info("Shutting down agent …")
        self.running = False


agent = None


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}")
    if agent:
        agent.shutdown()
    sys.exit(0)


def main():
    global agent
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    agent = GreenOpsAgent()
    agent.run()


if __name__ == "__main__":
    main()
