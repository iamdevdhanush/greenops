"""
GreenOps Agent Routes
"""
from flask import Blueprint, request, jsonify, g
from datetime import datetime
import logging

from server.services.machine import MachineService
from server.middleware import require_agent_token

logger = logging.getLogger(__name__)

agents_bp = Blueprint('agents', __name__, url_prefix='/api/agents')

@agents_bp.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint (no auth required)
    
    GET /api/agents/health
    Returns: {"status": "healthy", "database": "connected", "timestamp": "..."}
    """
    try:
        from server.database import db
        
        # Test database connection
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }), 200
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }), 503

@agents_bp.route('/register', methods=['POST'])
def register():
    """
    Register new agent or return existing registration (idempotent)
    
    POST /api/agents/register
    Body: {
        "mac_address": "00:1A:2B:3C:4D:5E",
        "hostname": "workstation-01",
        "os_type": "Linux",
        "os_version": "Ubuntu 22.04"
    }
    Returns: {
        "token": "agent_token",
        "machine_id": 42,
        "message": "Machine registered successfully"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Request body required'}), 400
        
        # Validate required fields
        required_fields = ['mac_address', 'hostname', 'os_type']
        missing_fields = [f for f in required_fields if f not in data]
        
        if missing_fields:
            return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400
        
        # Register machine
        result = MachineService.register_machine(
            mac_address=data['mac_address'],
            hostname=data['hostname'],
            os_type=data['os_type'],
            os_version=data.get('os_version')
        )
        
        logger.info(f"Agent registration: {data['mac_address']} - {result['message']}")
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Agent registration error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@agents_bp.route('/heartbeat', methods=['POST'])
@require_agent_token
def heartbeat():
    """
    Submit agent heartbeat (requires agent token)
    
    POST /api/agents/heartbeat
    Headers: Authorization: Bearer <agent_token>
    Body: {
        "idle_seconds": 600,
        "cpu_usage": 15.5,
        "memory_usage": 42.3,
        "timestamp": "2026-02-15T10:30:00Z"  # optional
    }
    Returns: {
        "status": "ok",
        "machine_status": "idle",
        "energy_wasted_kwh": 12.456
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Request body required'}), 400
        
        # Validate required fields
        if 'idle_seconds' not in data:
            return jsonify({'error': 'idle_seconds required'}), 400
        
        # Parse timestamp
        timestamp = None
        if 'timestamp' in data:
            try:
                timestamp = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid timestamp format. Use ISO 8601'}), 400
        
        # Process heartbeat
        result = MachineService.process_heartbeat(
            machine_id=g.machine_id,
            idle_seconds=int(data['idle_seconds']),
            cpu_usage=float(data.get('cpu_usage', 0)),
            memory_usage=float(data.get('memory_usage', 0)),
            timestamp=timestamp
        )
        
        return jsonify(result), 200
        
    except ValueError as e:
        return jsonify({'error': f'Invalid data format: {str(e)}'}), 422
    except Exception as e:
        logger.error(f"Heartbeat error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500
