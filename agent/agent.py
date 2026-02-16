"""
GreenOps Authentication Module
- Argon2 password hashing
- JWT token generation/validation
- Agent token management
"""
import jwt
import hashlib
import secrets
from datetime import datetime, timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from typing import Optional, Dict
import logging

from server.config import config
from server.database import db

logger = logging.getLogger(__name__)

# Argon2 hasher with secure defaults
ph = PasswordHasher(
    time_cost=2,
    memory_cost=65536,  # 64 MB
    parallelism=4
)

class AuthService:
    """Authentication service"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password with argon2"""
        return ph.hash(password)
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify password against hash"""
        try:
            ph.verify(password_hash, password)
            # Rehash if parameters changed
            if ph.check_needs_rehash(password_hash):
                return password_hash  # Return new hash if needed
            return True
        except VerifyMismatchError:
            return False
    
    @staticmethod
    def generate_jwt(user_id: int, username: str, role: str) -> str:
        """Generate JWT token for user"""
        payload = {
            'user_id': user_id,
            'username': username,
            'role': role,
            'exp': datetime.utcnow() + timedelta(hours=config.JWT_EXPIRATION_HOURS),
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
    
    @staticmethod
    def verify_jwt(token: str) -> Optional[Dict]:
        """Verify JWT token and return payload"""
        try:
            payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            return None
    
    @staticmethod
    def authenticate_user(username: str, password: str) -> Optional[Dict]:
        """Authenticate user with username/password"""
        query = "SELECT id, username, password_hash, role FROM users WHERE username = %s"
        user = db.execute_one(query, (username,))
        
        if not user:
            logger.warning(f"Login attempt for non-existent user: {username}")
            return None
        
        if not AuthService.verify_password(password, user['password_hash']):
            logger.warning(f"Failed login attempt for user: {username}")
            return None
        
        logger.info(f"Successful login: {username}")
        return {
            'id': user['id'],
            'username': user['username'],
            'role': user['role']
        }
    
    @staticmethod
    def generate_agent_token() -> str:
        """Generate secure random token for agent"""
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def hash_agent_token(token: str) -> str:
        """Hash agent token for storage"""
        return hashlib.sha256(token.encode()).hexdigest()
    
    @staticmethod
    def verify_agent_token(token: str) -> Optional[int]:
        """Verify agent token and return machine_id"""
        token_hash = AuthService.hash_agent_token(token)
        
        query = """
            SELECT m.id, m.mac_address 
            FROM machines m
            JOIN agent_tokens at ON at.machine_id = m.id
            WHERE at.token_hash = %s AND at.revoked = FALSE
        """
        result = db.execute_one(query, (token_hash,))
        
        if not result:
            logger.warning(f"Invalid agent token attempt (hash: {token_hash[:8]}...)")
            return None
        
        return result['id']
    
    @staticmethod
    def create_agent_token(machine_id: int) -> str:
        """Create and store agent token for machine"""
        token = AuthService.generate_agent_token()
        token_hash = AuthService.hash_agent_token(token)
        
        # Upsert token (idempotent)
        query = """
            INSERT INTO agent_tokens (machine_id, token_hash, issued_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (machine_id) DO UPDATE
            SET token_hash = EXCLUDED.token_hash, issued_at = NOW()
        """
        db.execute_query(query, (machine_id, token_hash))
        
        logger.info(f"Agent token created for machine_id: {machine_id}")
        return token
