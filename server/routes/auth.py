"""
GreenOps Authentication Routes
Adds:
  - must_change_password flag returned on login
  - POST /api/auth/change-password endpoint
"""
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request

from server.auth import AuthService
from server.config import config
from server.database import db
from server.middleware import rate_limit_login, require_jwt

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.route("/login", methods=["POST"])
@rate_limit_login
def login():
    """
    POST /api/auth/login
    Body: {"username": "...", "password": "..."}
    Response includes must_change_password flag.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "JSON body required"}), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"error": "Username and password required"}), 400

        if len(username) > 255 or len(password) > 1024:
            return jsonify({"error": "Invalid credentials"}), 401

        user = AuthService.authenticate_user(username, password)
        if not user:
            return jsonify({"error": "Invalid credentials"}), 401

        # Fetch must_change_password flag (added by migration 003)
        row = db.execute_one(
            "SELECT must_change_password FROM users WHERE id = %s",
            (user["id"],),
        )
        must_change = bool(row["must_change_password"]) if row else False

        token = AuthService.generate_jwt(user["id"], user["username"], user["role"])
        expires_at = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRATION_HOURS)

        return jsonify({
            "token": token,
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "role": user["role"],
            "username": user["username"],
            "must_change_password": must_change,
        }), 200

    except Exception as exc:
        logger.error(f"Login error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route("/change-password", methods=["POST"])
@require_jwt
def change_password():
    """
    POST /api/auth/change-password
    Headers: Authorization: Bearer <jwt>
    Body: {"current_password": "...", "new_password": "..."}

    Requires the current password for verification.
    Clears must_change_password on success.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "JSON body required"}), 400

        current_password = data.get("current_password", "")
        new_password = data.get("new_password", "")

        if not current_password or not new_password:
            return jsonify({"error": "current_password and new_password are required"}), 400

        if len(new_password) < 8:
            return jsonify({"error": "New password must be at least 8 characters"}), 422

        if len(new_password) > 1024:
            return jsonify({"error": "New password too long"}), 422

        # Verify current password
        user = AuthService.authenticate_user(g.username, current_password)
        if not user:
            return jsonify({"error": "Current password is incorrect"}), 401

        new_hash = AuthService.hash_password(new_password)
        db.execute_query(
            """
            UPDATE users
            SET password_hash = %s, must_change_password = FALSE
            WHERE id = %s
            """,
            (new_hash, g.user_id),
        )

        logger.info(f"Password changed for user id={g.user_id}")
        return jsonify({"message": "Password changed successfully"}), 200

    except Exception as exc:
        logger.error(f"Change password error: {exc}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route("/verify", methods=["GET"])
@require_jwt
def verify():
    """GET /api/auth/verify — verify JWT validity."""
    return jsonify({
        "valid": True,
        "username": g.username,
        "role": g.role,
        "user_id": g.user_id,
    }), 200
