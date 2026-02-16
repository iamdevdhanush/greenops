"""
GreenOps Middleware
- JWT validation
- Agent token validation
- Rate limiting
- Error handling
"""
from functools import wraps
from flask import request, jsonify, g
from collections import defaultdict
from datetime import datetime, timedelta
import logging

from server.auth import AuthService
from server.config import config

logger = logging.getLogger(__name__)

# Simple in-memory rate limiter (use Redis in production)
login_attempts = defaultdict(list)

def require_jwt(f):
    """Middleware: Require valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid authorization header'}), 401
        
        token = auth_header.split(' ')[1]
        payload = AuthService.verify_jwt(token)
        
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        # Store user info in request context
        g.user_id = payload['user_id']
        g.username = payload['username']
        g.role = payload['role']
        
        return f(*args, **kwargs)
    
    return decorated

def require_admin(f):
    """Middleware: Require admin role"""
    @wraps(f)
    @require_jwt
    def decorated(*args, **kwargs):
        if g.role != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    
    return decorated

def require_agent_token(f):
    """Middleware: Require valid agent token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid authorization header'}), 401
        
        token = auth_header.split(' ')[1]
        machine_id = AuthService.verify_agent_token(token)
        
        if not machine_id:
            return jsonify({'error': 'Invalid agent token'}), 401
        
        # Store machine_id in request context
        g.machine_id = machine_id
        
        return f(*args, **kwargs)
    
    return decorated

def rate_limit_login(f):
    """Middleware: Rate limit login attempts"""
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr
        now = datetime.utcnow()
        
        # Clean old attempts
        cutoff = now - timedelta(seconds=config.LOGIN_RATE_WINDOW)
        login_attempts[ip] = [t for t in login_attempts[ip] if t > cutoff]
        
        # Check rate limit
        if len(login_attempts[ip]) >= config.LOGIN_RATE_LIMIT:
            logger.warning(f"Rate limit exceeded for IP: {ip}")
            return jsonify({'error': 'Too many login attempts. Please try again later.'}), 429
        
        # Record attempt
        login_attempts[ip].append(now)
        
        return f(*args, **kwargs)
    
    return decorated

def handle_errors(app):
    """Register global error handlers"""
    
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({'error': 'Bad request', 'detail': str(e)}), 400
    
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Not found'}), 404
    
    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"Internal server error: {e}")
        return jsonify({'error': 'Internal server error'}), 500
    
    @app.errorhandler(Exception)
    def handle_exception(e):
        logger.error(f"Unhandled exception: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500
