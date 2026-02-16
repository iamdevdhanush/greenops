"""
GreenOps Agent
Lightweight monitoring agent for tracking machine usage and energy waste
"""
import sys
import time
import logging
import platform
import requests
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
import signal

from config import config
from idle_detector import IdleDetector

# Configure logging
log_file = config.config_dir / 'agent.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            log_file,
            maxBytes=5*1024*1024,  # 5 MB
            backupCount=3
        ),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class GreenOpsAgent:
    """Main agent class"""
    
    def __init__(self):
        self.config = config
        self.idle_detector = IdleDetector()
        self.token = None
        self.machine_id = None
        self.running = True
        self.retry_delay = self.config.retry_backoff_base
        self.consecutive_failures = 0
        
        # System information
        self.mac_address = self.get_mac_address()
        self.hostname = platform.node()
        self.os_type = platform.system()
        self.os_version = platform.version()
        
        logger.info(f"GreenOps Agent initialized")
        logger.info(f"System: {self.hostname} ({self.os_type} {self.os_version})")
        logger.info(f"MAC: {self.mac_address}")
        logger.info(f"Server: {self.config.server_url}")
    
    @staticmethod
    def get_mac_address() -> str:
        """
        Get primary MAC address of the machine
        
        Returns:
            MAC address in format: XX:XX:XX:XX:XX:XX
        """
        try:
            # Get MAC from uuid.getnode()
            mac_int = uuid.getnode()
            mac_hex = f"{mac_int:012x}"
            mac_formatted = ':'.join(mac_hex[i:i+2] for i in range(0, 12, 2))
            return mac_formatted.upper()
        except Exception as e:
            logger.error(f"Failed to get MAC address: {e}")
            # Fallback: generate fake MAC for testing
            return "00:00:00:00:00:00"
    
    def register(self) -> bool:
        """
        Register agent with server (idempotent)
        
        Returns:
            True if registration successful, False otherwise
        """
        logger.info("Attempting to register with server...")
        
        try:
            url = f"{self.config.server_url}/api/agents/register"
            payload = {
                'mac_address': self.mac_address,
                'hostname': self.hostname,
                'os_type': self.os_type,
                'os_version': self.os_version
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                self.token = data['token']
                self.machine_id = data['machine_id']
                
                # Save token to file
                self.config.save_token(self.token)
                
                logger.info(f"Registration successful! Machine ID: {self.machine_id}")
                logger.info(f"Message: {data.get('message', 'N/A')}")
                
                return True
            else:
                logger.error(f"Registration failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to server at {self.config.server_url}")
            return False
        except requests.exceptions.Timeout:
            logger.error("Registration request timed out")
            return False
        except Exception as e:
            logger.error(f"Registration error: {e}")
            return False
    
    def send_heartbeat(self) -> bool:
        """
        Send heartbeat to server
        
        Returns:
            True if successful, False otherwise
        """
        if not self.token:
            logger.error("No token available, cannot send heartbeat")
            return False
        
        try:
            # Get idle time
            idle_seconds = self.idle_detector.get_idle_seconds()
            
            # Get system stats (placeholder - can be enhanced)
            cpu_usage = 0.0  # TODO: Implement CPU monitoring
            memory_usage = 0.0  # TODO: Implement memory monitoring
            
            url = f"{self.config.server_url}/api/agents/heartbeat"
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
            payload = {
                'idle_seconds': idle_seconds,
                'cpu_usage': cpu_usage,
                'memory_usage': memory_usage,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Heartbeat sent: idle={idle_seconds}s, status={data.get('machine_status')}")
                return True
            elif response.status_code == 401:
                logger.error("Authentication failed - token may be invalid")
                # Try to re-register
                self.token = None
                return False
            else:
                logger.error(f"Heartbeat failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.ConnectionError:
            logger.warning(f"Cannot connect to server (will retry)")
            return False
        except requests.exceptions.Timeout:
            logger.warning("Heartbeat request timed out")
            return False
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            return False
    
    def run(self):
        """
        Main agent loop
        
        Lifecycle:
        1. Load token or register
        2. Send heartbeat every interval
        3. Retry with exponential backoff on failure
        4. Never give up
        """
        logger.info("Starting GreenOps Agent...")
        
        # Try to load existing token
        self.token = self.config.load_token()
        
        if self.token:
            logger.info("Loaded existing token from file")
        else:
            logger.info("No existing token found, will register on first heartbeat")
        
        while self.running:
            try:
                # Register if no token
                if not self.token:
                    if self.register():
                        self.retry_delay = self.config.retry_backoff_base
                        self.consecutive_failures = 0
                    else:
                        # Registration failed, retry with backoff
                        self.consecutive_failures += 1
                        self._handle_failure()
                        continue
                
                # Send heartbeat
                if self.send_heartbeat():
                    # Success - reset retry parameters
                    self.retry_delay = self.config.retry_backoff_base
                    self.consecutive_failures = 0
                    
                    # Wait for next interval
                    time.sleep(self.config.heartbeat_interval)
                else:
                    # Failure - apply backoff
                    self.consecutive_failures += 1
                    self._handle_failure()
                    
            except KeyboardInterrupt:
                logger.info("Received interrupt signal, shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
                self.consecutive_failures += 1
                self._handle_failure()
        
        logger.info("GreenOps Agent stopped")
    
    def _handle_failure(self):
        """Handle failure with exponential backoff"""
        if self.consecutive_failures >= self.config.max_retry_attempts:
            logger.warning(f"Failed {self.consecutive_failures} consecutive times, continuing with backoff...")
        
        logger.info(f"Retrying in {self.retry_delay} seconds...")
        time.sleep(self.retry_delay)
        
        # Exponential backoff
        self.retry_delay = min(
            self.retry_delay * 2,
            self.config.retry_backoff_max
        )
    
    def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down agent...")
        self.running = False
        
        # Send final heartbeat (best effort)
        try:
            self.send_heartbeat()
        except:
            pass

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}")
    if 'agent' in globals():
        agent.shutdown()
    sys.exit(0)

def main():
    """Main entry point"""
    global agent
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and run agent
    agent = GreenOpsAgent()
    agent.run()

if __name__ == '__main__':
    main()
