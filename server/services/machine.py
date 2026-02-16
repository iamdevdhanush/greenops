"""
GreenOps Machine Service
Handles machine registration, heartbeat processing, and status updates
"""
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from server.database import db
from server.auth import AuthService
from server.services.energy import EnergyService
from server.config import config

logger = logging.getLogger(__name__)

class MachineService:
    """Machine management service"""
    
    @staticmethod
    def register_machine(mac_address: str, hostname: str, os_type: str, os_version: str = None) -> dict:
        """
        Register new machine or return existing machine (idempotent).
        
        Args:
            mac_address: MAC address (primary identity)
            hostname: Machine hostname
            os_type: Operating system type
            os_version: Operating system version
            
        Returns:
            Dictionary with machine_id and token
        """
        # Normalize MAC address
        mac_address = mac_address.upper().replace('-', ':')
        
        # Check if machine already exists
        query = "SELECT id FROM machines WHERE mac_address = %s"
        existing = db.execute_one(query, (mac_address,))
        
        if existing:
            machine_id = existing['id']
            logger.info(f"Machine already registered: {mac_address} (id: {machine_id})")
            
            # Return existing token
            token_query = "SELECT token_hash FROM agent_tokens WHERE machine_id = %s"
            token_record = db.execute_one(token_query, (machine_id,))
            
            if token_record:
                # Cannot return original token (it's hashed), so generate new one
                # This is acceptable for idempotent registration
                token = AuthService.create_agent_token(machine_id)
            else:
                token = AuthService.create_agent_token(machine_id)
            
            return {
                'machine_id': machine_id,
                'token': token,
                'message': 'Machine already registered'
            }
        
        # Create new machine record
        insert_query = """
            INSERT INTO machines (mac_address, hostname, os_type, os_version, first_seen, last_seen, status)
            VALUES (%s, %s, %s, %s, NOW(), NOW(), 'online')
            RETURNING id
        """
        result = db.execute_one(insert_query, (mac_address, hostname, os_type, os_version))
        machine_id = result['id']
        
        # Generate agent token
        token = AuthService.create_agent_token(machine_id)
        
        logger.info(f"New machine registered: {mac_address} (id: {machine_id})")
        
        return {
            'machine_id': machine_id,
            'token': token,
            'message': 'Machine registered successfully'
        }
    
    @staticmethod
    def process_heartbeat(machine_id: int, idle_seconds: int, cpu_usage: float = None, 
                         memory_usage: float = None, timestamp: datetime = None) -> dict:
        """
        Process agent heartbeat (idempotent).
        
        Args:
            machine_id: Machine database ID
            idle_seconds: Seconds since last user activity
            cpu_usage: CPU usage percentage
            memory_usage: Memory usage percentage
            timestamp: Heartbeat timestamp (default: now)
            
        Returns:
            Dictionary with processing result
        """
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        # Determine if machine is idle
        is_idle = idle_seconds >= config.IDLE_THRESHOLD_SECONDS
        status = 'idle' if is_idle else 'online'
        
        # Calculate energy waste for this heartbeat interval
        # Note: We only count incremental idle time, not total idle time from boot
        # This requires tracking last heartbeat, which we'll do by comparing timestamps
        
        # Get last heartbeat time
        last_hb_query = """
            SELECT timestamp, idle_seconds FROM heartbeats 
            WHERE machine_id = %s 
            ORDER BY timestamp DESC LIMIT 1
        """
        last_hb = db.execute_one(last_hb_query, (machine_id,))
        
        # Calculate incremental idle time
        if last_hb and last_hb['timestamp']:
            time_since_last = (timestamp - last_hb['timestamp']).total_seconds()
            # If machine was idle before and still idle, count the interval
            if is_idle and last_hb['idle_seconds'] >= config.IDLE_THRESHOLD_SECONDS:
                incremental_idle = min(time_since_last, idle_seconds)
            else:
                incremental_idle = 0
        else:
            # First heartbeat or no history
            incremental_idle = idle_seconds if is_idle else 0
        
        # Calculate energy waste
        energy_waste = EnergyService.calculate_idle_energy_waste(int(incremental_idle))
        
        # Update machine record (cumulative totals)
        update_query = """
            UPDATE machines 
            SET last_seen = %s,
                status = %s,
                total_idle_seconds = total_idle_seconds + %s,
                energy_wasted_kwh = energy_wasted_kwh + %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING energy_wasted_kwh
        """
        result = db.execute_one(update_query, 
                               (timestamp, status, int(incremental_idle), energy_waste, machine_id))
        
        # Insert heartbeat record
        insert_hb_query = """
            INSERT INTO heartbeats (machine_id, timestamp, idle_seconds, cpu_usage, memory_usage, is_idle)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        db.execute_query(insert_hb_query, (machine_id, timestamp, idle_seconds, cpu_usage, memory_usage, is_idle))
        
        logger.info(f"Heartbeat processed for machine {machine_id}: status={status}, idle={idle_seconds}s, "
                   f"energy_waste={energy_waste} kWh")
        
        return {
            'status': 'ok',
            'machine_status': status,
            'energy_wasted_kwh': float(result['energy_wasted_kwh']),
            'is_idle': is_idle
        }
    
    @staticmethod
    def get_machine(machine_id: int) -> dict:
        """Get machine details by ID"""
        query = """
            SELECT id, mac_address, hostname, os_type, os_version,
                   first_seen, last_seen, total_idle_seconds, total_active_seconds,
                   energy_wasted_kwh, status, created_at, updated_at
            FROM machines
            WHERE id = %s
        """
        machine = db.execute_one(query, (machine_id,))
        
        if not machine:
            return None
        
        return dict(machine)
    
    @staticmethod
    def list_machines(status_filter: str = None, limit: int = 100, offset: int = 0) -> list:
        """List machines with optional filtering"""
        params = []
        where_clause = ""
        
        if status_filter:
            where_clause = "WHERE status = %s"
            params.append(status_filter)
        
        query = f"""
            SELECT id, mac_address, hostname, os_type, status, last_seen,
                   energy_wasted_kwh, total_idle_seconds, total_active_seconds
            FROM machines
            {where_clause}
            ORDER BY last_seen DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
        
        machines = db.execute_query(query, tuple(params), fetch=True)
        return [dict(m) for m in machines]
    
    @staticmethod
    def get_dashboard_stats() -> dict:
        """Get aggregate statistics for dashboard"""
        query = """
            SELECT 
                COUNT(*) as total_machines,
                COUNT(CASE WHEN status = 'online' THEN 1 END) as online_machines,
                COUNT(CASE WHEN status = 'idle' THEN 1 END) as idle_machines,
                COUNT(CASE WHEN status = 'offline' THEN 1 END) as offline_machines,
                COALESCE(SUM(energy_wasted_kwh), 0) as total_energy_wasted_kwh,
                COALESCE(SUM(total_idle_seconds), 0) as total_idle_seconds
            FROM machines
        """
        stats = db.execute_one(query)
        
        if not stats:
            return {
                'total_machines': 0,
                'online_machines': 0,
                'idle_machines': 0,
                'offline_machines': 0,
                'total_energy_wasted_kwh': 0.0,
                'estimated_cost_usd': 0.0
            }
        
        # Calculate cost
        cost = EnergyService.calculate_cost(Decimal(str(stats['total_energy_wasted_kwh'])))
        
        # Calculate average idle percentage
        total_time = stats['total_idle_seconds'] + sum(
            m.get('total_active_seconds', 0) for m in 
            db.execute_query("SELECT total_active_seconds FROM machines", fetch=True)
        )
        avg_idle_pct = (stats['total_idle_seconds'] / total_time * 100) if total_time > 0 else 0
        
        return {
            'total_machines': stats['total_machines'],
            'online_machines': stats['online_machines'],
            'idle_machines': stats['idle_machines'],
            'offline_machines': stats['offline_machines'],
            'total_energy_wasted_kwh': float(stats['total_energy_wasted_kwh']),
            'estimated_cost_usd': float(cost),
            'average_idle_percentage': round(avg_idle_pct, 1)
        }
    
    @staticmethod
    def update_offline_machines():
        """Mark machines as offline if no heartbeat within timeout"""
        timeout = datetime.utcnow() - timedelta(seconds=config.HEARTBEAT_TIMEOUT_SECONDS)
        
        query = """
            UPDATE machines
            SET status = 'offline', updated_at = NOW()
            WHERE last_seen < %s AND status != 'offline'
            RETURNING id, hostname
        """
        updated = db.execute_query(query, (timeout,), fetch=True)
        
        if updated:
            logger.info(f"Marked {len(updated)} machines as offline")
            for machine in updated:
                logger.debug(f"Machine offline: {machine['hostname']} (id: {machine['id']})")
        
        return len(updated)
