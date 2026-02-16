"""
GreenOps Authentication Routes
"""
from flask import Blueprint, request, jsonify
import logging

from server.auth import AuthService
from server.middleware import rate_limit_login, require_jwt

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

@auth_bp.route('/login', methods=['POST'])
@rate_limit_login
def login():
    """
    Admin login endpoint
    
    POST /api/auth/login
    Body: {"username": "admin", "password": "password"}
    Returns: {"token": "jwt_token", "expires_at": "timestamp", "role": "admin"}
    """
    try:
        data = request.get_json()
        
        if not data or 'username' not in data or 'password' not in data:
            return jsonify({'error': 'Username and password required'}), 400
        
        username = data['username']
        password = data['password']
        
        # Authenticate user
        user = AuthService.authenticate_user(username, password)
        
        if not user:
            return jsonify({'error': 'Invalid credentials'}), 401
        
        # Generate JWT
        token = AuthService.generate_jwt(user['id'], user['username'], user['role'])
        
        # Calculate expiration
        from datetime import datetime, timedelta
        from server.config import config
        expires_at = datetime.utcnow() + timedelta(hours=config.JWT_EXPIRATION_HOURS)
        
        return jsonify({
            'token': token,
            'expires_at': expires_at.isoformat() + 'Z',
            'role': user['role'],
            'username': user['username']
        }), 200
        
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@auth_bp.route('/verify', methods=['GET'])
@require_jwt
def verify():
    """
    Verify JWT token
    
    GET /api/auth/verify
    Headers: Authorization: Bearer <jwt>
    Returns: {"valid": true, "username": "admin", "role": "admin"}
    """
    from flask import g
    
    return jsonify({
        'valid': True,
        'username': g.username,
        'role': g.role,
        'user_id': g.user_id
    }), 200
