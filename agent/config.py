"""
GreenOps Agent Configuration
"""
import os
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class AgentConfig:
    """Agent configuration management"""
    
    def __init__(self):
        # Determine config directory based on platform
        if os.name == 'nt':  # Windows
            self.config_dir = Path(os.getenv('PROGRAMDATA', 'C:/ProgramData')) / 'GreenOps'
        else:  # Linux/macOS
            self.config_dir = Path.home() / '.greenops'
        
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / 'config.json'
        self.token_file = self.config_dir / 'token'
        
        # Default configuration
        self.server_url = os.getenv('GREENOPS_SERVER_URL', 'http://localhost:8000')
        self.heartbeat_interval = int(os.getenv('GREENOPS_HEARTBEAT_INTERVAL', '60'))
        self.idle_threshold = int(os.getenv('GREENOPS_IDLE_THRESHOLD', '300'))
        self.retry_backoff_base = int(os.getenv('GREENOPS_RETRY_BASE', '5'))
        self.retry_backoff_max = int(os.getenv('GREENOPS_RETRY_MAX', '300'))
        self.max_retry_attempts = int(os.getenv('GREENOPS_MAX_RETRIES', '5'))
        
        # Load configuration file if exists
        self.load_config()
    
    def load_config(self):
        """Load configuration from file"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    config_data = json.load(f)
                
                # Override defaults with file config
                self.server_url = config_data.get('server_url', self.server_url)
                self.heartbeat_interval = config_data.get('heartbeat_interval', self.heartbeat_interval)
                self.idle_threshold = config_data.get('idle_threshold', self.idle_threshold)
                self.retry_backoff_base = config_data.get('retry_backoff_base', self.retry_backoff_base)
                self.retry_backoff_max = config_data.get('retry_backoff_max', self.retry_backoff_max)
                
                logger.info(f"Configuration loaded from {self.config_file}")
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}, using defaults")
    
    def save_config(self):
        """Save current configuration to file"""
        try:
            config_data = {
                'server_url': self.server_url,
                'heartbeat_interval': self.heartbeat_interval,
                'idle_threshold': self.idle_threshold,
                'retry_backoff_base': self.retry_backoff_base,
                'retry_backoff_max': self.retry_backoff_max
            }
            
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
            
            logger.info(f"Configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
    
    def load_token(self) -> str:
        """Load agent token from file"""
        if self.token_file.exists():
            try:
                with open(self.token_file, 'r') as f:
                    token = f.read().strip()
                logger.info("Agent token loaded from file")
                return token
            except Exception as e:
                logger.error(f"Failed to load token: {e}")
        return None
    
    def save_token(self, token: str):
        """Save agent token to file"""
        try:
            # Set restrictive permissions (owner read/write only)
            with open(self.token_file, 'w') as f:
                f.write(token)
            
            # Set file permissions (Unix only)
            if os.name != 'nt':
                os.chmod(self.token_file, 0o600)
            
            logger.info(f"Agent token saved to {self.token_file}")
        except Exception as e:
            logger.error(f"Failed to save token: {e}")
            raise

# Global config instance
config = AgentConfig()
