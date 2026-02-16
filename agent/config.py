"""
GreenOps Server Configuration
All settings configurable via environment variables
"""
import os
from typing import Optional

class Config:
    """Server configuration - NO hardcoded values"""
    
    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://greenops:greenops@localhost:5432/greenops"
    )
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    
    # Security
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "CHANGE_THIS_IN_PRODUCTION")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
    
    # Agent
    AGENT_TOKEN_EXPIRATION_DAYS: Optional[int] = None  # No expiration for now
    
    # Rate Limiting
    LOGIN_RATE_LIMIT: int = int(os.getenv("LOGIN_RATE_LIMIT", "5"))
    LOGIN_RATE_WINDOW: int = int(os.getenv("LOGIN_RATE_WINDOW", "900"))  # 15 minutes
    
    # Energy Calculation Constants
    # Desktop PC power consumption estimates based on industry averages
    IDLE_POWER_WATTS: float = float(os.getenv("IDLE_POWER_WATTS", "65"))  # 65W average idle desktop
    ACTIVE_POWER_WATTS: float = float(os.getenv("ACTIVE_POWER_WATTS", "120"))  # 120W average active desktop
    ELECTRICITY_COST_PER_KWH: float = float(os.getenv("ELECTRICITY_COST_PER_KWH", "0.12"))  # $0.12/kWh US average
    
    # Agent Configuration
    HEARTBEAT_TIMEOUT_SECONDS: int = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "180"))  # 3 minutes
    IDLE_THRESHOLD_SECONDS: int = int(os.getenv("IDLE_THRESHOLD_SECONDS", "300"))  # 5 minutes
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "greenops.log")
    
    # CORS
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")
    
    @classmethod
    def validate(cls):
        """Validate critical configuration"""
        if cls.JWT_SECRET_KEY == "CHANGE_THIS_IN_PRODUCTION" and not cls.DEBUG:
            raise ValueError("JWT_SECRET_KEY must be set in production!")
        if not cls.DATABASE_URL:
            raise ValueError("DATABASE_URL must be set!")

config = Config()
