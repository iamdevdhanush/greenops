"""
GreenOps Authentication Service
Handles JWT generation/verification and agent token management.
"""
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from server.config import config
from server.database import db

logger = logging.getLogger(__name__)

ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=4)

_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=2,p=4"
    "$dGVzdHNhbHRkdW1teXZhbHVl"
    "$ZTqFgJXCm3f7q+v4aB3kDgHfQv1CxLm8sPqW3nRk2oo"
)


class AuthService:

    @staticmethod
    def authenticate_user(username: str, password: str) -> Optional[dict]:
        query = """
            SELECT id, username, password_hash, role
            FROM users
            WHERE username = %s
        """
        user = db.execute_one(query, (username,))

        if not user:
            try:
                ph.verify(_DUMMY_HASH, password)
            except Exception:
                pass
            logger.warning("Login attempt for unknown user (username withheld)")
            return None

        try:
            ph.verify(user["password_hash"], password)
        except VerifyMismatchError:
            logger.warning(f"Failed login attempt for user id={user['id']}")
            return None
        except (VerificationError, InvalidHashError) as exc:
            logger.error(f"Password verification error for user id={user['id']}: {exc}")
            return None

        if ph.check_needs_rehash(user["password_hash"]):
            try:
                new_hash = ph.hash(password)
                db.execute_query(
                    "UPDATE users SET password_hash = %s WHERE id = %s",
                    (new_hash, user["id"]),
                )
                logger.info(f"Rehashed password for user id={user['id']}")
            except Exception as exc:
                logger.error(f"Failed to rehash password for user id={user['id']}: {exc}")

        logger.info(f"Successful login for user id={user['id']}")
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
        }

    @staticmethod
    def generate_jwt(user_id: int, username: str, role: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "user_id": user_id,
            "username": username,
            "role": role,
            "iat": now,
            "exp": now + timedelta(hours=config.JWT_EXPIRATION_HOURS),
        }
        return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)

    @staticmethod
    def verify_jwt(token: str) -> Optional[dict]:
        try:
            return jwt.decode(
                token,
                config.JWT_SECRET_KEY,
                algorithms=[config.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            logger.debug("Rejected expired JWT")
            return None
        except jwt.InvalidTokenError as exc:
            logger.debug(f"Rejected invalid JWT: {exc}")
            return None

    @staticmethod
    def create_agent_token(machine_id: int) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        db.execute_query(
            """
            INSERT INTO agent_tokens (machine_id, token_hash, issued_at, revoked)
            VALUES (%s, %s, NOW(), FALSE)
            ON CONFLICT (machine_id) DO UPDATE
                SET token_hash = EXCLUDED.token_hash,
                    issued_at  = NOW(),
                    revoked    = FALSE
            """,
            (machine_id, token_hash),
        )

        return token

    @staticmethod
    def verify_agent_token(token: str) -> Optional[int]:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        record = db.execute_one(
            """
            SELECT machine_id
            FROM agent_tokens
            WHERE token_hash = %s
              AND revoked = FALSE
            """,
            (token_hash,),
        )

        if not record:
            logger.debug("Agent token not found or revoked")
            return None

        return record["machine_id"]

    @staticmethod
    def hash_password(password: str) -> str:
        return ph.hash(password)
