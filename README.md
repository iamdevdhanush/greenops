# GreenOps - Green IT Infrastructure Monitoring Platform

**Professional SaaS platform for monitoring organizational computers, tracking idle machines, estimating energy waste, and optimizing infrastructure utilization.**

## Quick Start (< 10 minutes)

### Prerequisites
- Docker and Docker Compose installed
- Python 3.9+ (for running agent)
- Port 80, 5432, and 8000 available

### 1. Start the Server

```bash
# Clone or navigate to the project directory
cd greenops

# Generate secure JWT secret
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(32))" > .env

# Start all services (database, server, nginx)
docker-compose up -d

# Wait for services to be healthy (~30 seconds)
docker-compose ps

# Check logs if needed
docker-compose logs -f server
```

### 2. Access the Dashboard

Open browser: **http://localhost**

**Default credentials:**
- Username: `admin`
- Password: `admin123`

### 3. Run the Agent

```bash
# Navigate to agent directory
cd agent

# Install dependencies
pip install -r requirements.txt

# Run agent
python agent.py
```

The agent will:
1. Auto-detect your machine (MAC, hostname, OS)
2. Register with the server
3. Start sending heartbeats every 60 seconds
4. Track idle time and report energy waste

### 4. Verify

1. Check dashboard - your machine should appear in the table
2. Wait 5 minutes without using the machine
3. Refresh dashboard - status should change to "IDLE"
4. Energy waste should start accumulating

---

## Architecture

```
┌──────────────┐         ┌──────────────────────┐         ┌─────────────┐
│  Agent       │────────▶│  Server (FastAPI)    │◀────────│  Dashboard  │
│  (Python)    │         │  + PostgreSQL        │         │  (Web)      │
└──────────────┘         └──────────────────────┘         └─────────────┘
   Multiple               Authentication                   Real-time
   Machines              Energy Calculation                Monitoring
   Auto-detect           Idempotent APIs
   Retry Logic           Connection Pooling
```

### Components

**Server:**
- FastAPI web framework
- PostgreSQL database (ACID compliance)
- JWT authentication (admin)
- Token authentication (agents)
- Connection pooling
- Graceful shutdown
- Structured logging

**Agent:**
- Cross-platform (Windows/Linux/macOS)
- Auto-detect system info
- Platform-specific idle detection
- Offline queue with retry
- Exponential backoff
- Token-based auth

**Dashboard:**
- Enterprise-style UI
- Real-time updates
- Machine filtering
- Energy tracking
- Cost estimation

---

## Configuration

### Server Environment Variables

See `.env.example` for all options. Key settings:

```bash
# Security (REQUIRED in production)
JWT_SECRET_KEY=your_secure_random_key

# Database
DATABASE_URL=postgresql://user:pass@host:port/dbname

# Energy Calculation
IDLE_POWER_WATTS=65         # Average desktop idle power
ACTIVE_POWER_WATTS=120      # Average desktop active power
ELECTRICITY_COST_PER_KWH=0.12  # Local electricity rate
```

### Agent Configuration

Agent can be configured via:
1. Environment variables (see `.env.example`)
2. Config file: `~/.greenops/config.json` (Linux/macOS) or `C:\ProgramData\GreenOps\config.json` (Windows)

```json
{
  "server_url": "http://your-server:8000",
  "heartbeat_interval": 60,
  "idle_threshold": 300
}
```

---

## API Documentation

### Authentication Endpoints

#### POST `/api/auth/login`
Login with username/password, receive JWT token.

**Request:**
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response:**
```json
{
  "token": "eyJ0eXAiOiJKV1QiLCJh...",
  "expires_at": "2026-02-16T12:00:00Z",
  "role": "admin",
  "username": "admin"
}
```

#### GET `/api/auth/verify`
Verify JWT token validity.

**Headers:** `Authorization: Bearer <jwt>`

### Agent Endpoints

#### POST `/api/agents/register`
Register new agent or return existing (idempotent).

**Request:**
```json
{
  "mac_address": "00:1A:2B:3C:4D:5E",
  "hostname": "workstation-01",
  "os_type": "Linux",
  "os_version": "Ubuntu 22.04"
}
```

**Response:**
```json
{
  "token": "agent_token_here",
  "machine_id": 42,
  "message": "Machine registered successfully"
}
```

#### POST `/api/agents/heartbeat`
Submit agent heartbeat.

**Headers:** `Authorization: Bearer <agent_token>`

**Request:**
```json
{
  "idle_seconds": 600,
  "cpu_usage": 15.5,
  "memory_usage": 42.3,
  "timestamp": "2026-02-15T10:30:00Z"
}
```

**Response:**
```json
{
  "status": "ok",
  "machine_status": "idle",
  "energy_wasted_kwh": 12.456
}
```

### Dashboard Endpoints

All require JWT authentication via `Authorization: Bearer <jwt>` header.

#### GET `/api/machines`
List all machines with optional filtering.

**Query params:** `status`, `limit`, `offset`

#### GET `/api/machines/{id}`
Get machine details.

#### GET `/api/machines/{id}/heartbeats`
Get recent heartbeats for a machine.

#### GET `/api/dashboard/stats`
Get aggregate statistics.

**Response:**
```json
{
  "total_machines": 150,
  "online_machines": 120,
  "idle_machines": 45,
  "offline_machines": 5,
  "total_energy_wasted_kwh": 1234.56,
  "estimated_cost_usd": 148.15,
  "average_idle_percentage": 35.5
}
```

---

## Energy Calculation Model

GreenOps uses explainable, documented energy estimation:

### Power Consumption Assumptions

- **Idle Desktop:** 65W average (monitor low-power, CPU idle, disks spun down)
- **Active Desktop:** 120W average (typical office usage)
- Based on modern hardware (2015+), standard monitors, no high-power GPUs

### Formula

```python
# Energy wasted during idle time
idle_hours = idle_seconds / 3600
energy_kwh = idle_hours * (IDLE_POWER_WATTS / 1000)

# Cost estimation
cost_usd = energy_kwh * ELECTRICITY_COST_PER_KWH
```

### Customization

Adjust power consumption for your environment:

```bash
# High-performance workstations
IDLE_POWER_WATTS=85
ACTIVE_POWER_WATTS=200

# Energy-efficient laptops
IDLE_POWER_WATTS=25
ACTIVE_POWER_WATTS=45

# Local electricity rate (find yours online)
ELECTRICITY_COST_PER_KWH=0.15  # $0.15/kWh
```

---

## Security

### Production Deployment Checklist

- [ ] Change default admin password
- [ ] Generate secure `JWT_SECRET_KEY`
- [ ] Use strong database password
- [ ] Enable HTTPS (use nginx with Let's Encrypt)
- [ ] Configure firewall rules
- [ ] Set `DEBUG=false`
- [ ] Configure proper `CORS_ORIGINS`
- [ ] Use Redis for rate limiting (not in-memory)
- [ ] Setup database backups
- [ ] Configure log rotation
- [ ] Enable container resource limits

### Password Hashing

- Algorithm: Argon2id
- Parameters: time_cost=2, memory_cost=65536, parallelism=4
- Never logged or exposed

### Token Security

- JWT: HS256, 24h expiration
- Agent tokens: UUID4, SHA256 hashed storage
- Tokens never logged in plain text

---

## Database Schema

### Tables

**users** - Admin accounts
- `id`, `username` (unique), `password_hash`, `role`, `created_at`

**machines** - Registered machines (MAC = primary identity)
- `id`, `mac_address` (unique), `hostname`, `os_type`, `os_version`
- `first_seen`, `last_seen`, `status` (online/idle/offline)
- `total_idle_seconds`, `total_active_seconds`, `energy_wasted_kwh`
- `agent_token_hash`

**heartbeats** - Agent heartbeat history
- `id`, `machine_id` (FK), `timestamp`, `idle_seconds`, `cpu_usage`, `memory_usage`, `is_idle`

**agent_tokens** - Agent authentication tokens
- `id`, `machine_id` (FK, unique), `token_hash`, `issued_at`, `revoked`

### Migrations

```bash
# Apply migrations manually
psql $DATABASE_URL < migrations/001_initial_schema.sql

# Or let Docker Compose auto-apply on first start
docker-compose up
```

---

## Operations

### Health Checks

```bash
# Server health
curl http://localhost:8000/health

# Agent health
curl http://localhost:8000/api/agents/health
```

### Logs

```bash
# Server logs
docker-compose logs -f server

# Agent logs
tail -f ~/.greenops/agent.log  # Linux/macOS
# or
type C:\ProgramData\GreenOps\agent.log  # Windows
```

### Backup

```bash
# Database backup
docker exec greenops-db pg_dump -U greenops greenops > backup.sql

# Restore
docker exec -i greenops-db psql -U greenops greenops < backup.sql
```

### Monitoring

- **Prometheus:** Expose metrics at `/metrics` (requires implementation)
- **Grafana:** Create dashboards for energy trends
- **Alerts:** Setup alerts for offline machines, high idle rates

---

## Troubleshooting

### Server won't start

```bash
# Check logs
docker-compose logs server

# Verify database connection
docker exec greenops-db psql -U greenops -c "SELECT 1"

# Reset everything
docker-compose down -v
docker-compose up -d
```

### Agent can't connect

```bash
# Check server URL
echo $GREENOPS_SERVER_URL

# Test connection
curl http://your-server:8000/api/agents/health

# Check agent logs
tail -f ~/.greenops/agent.log
```

### Machine shows as offline

- Agents mark offline after 3 minutes without heartbeat
- Check agent is running: `ps aux | grep agent.py`
- Verify network connectivity
- Check agent logs for errors

### Energy numbers seem wrong

- Review power consumption settings
- Verify idle threshold (default 5 minutes)
- Check heartbeat frequency (default 60 seconds)
- Energy is cumulative - increases over time

---

## Development

### Running Tests

```bash
# Server tests (when implemented)
cd server
pytest

# Agent tests
cd agent
pytest
```

### Code Style

```bash
# Format code
black .
isort .

# Lint
flake8
pylint server/ agent/
```

---

## Extending GreenOps

### Adding Features

1. **Custom Power Profiles:** Add per-machine power consumption profiles
2. **Scheduling Policies:** Enforce shutdown schedules for idle machines
3. **Reporting:** Generate weekly/monthly energy reports
4. **Integrations:** Connect to Slack, email, or ticketing systems
5. **Advanced Analytics:** Machine learning for usage prediction

### Agent Enhancements

- CPU/memory monitoring (currently placeholder)
- Disk usage tracking
- Network activity detection
- Process monitoring
- GPU power consumption

---

## License

Copyright 2026. All rights reserved.

---

## Support

**Issues:** Report bugs or request features
**Documentation:** See `/docs` directory for detailed guides
**Community:** Join discussions and share configurations

---

## System Requirements

### Server
- CPU: 2+ cores
- RAM: 4GB minimum, 8GB recommended
- Disk: 20GB+ (grows with heartbeat history)
- OS: Linux (Docker compatible)

### Agent
- CPU: Minimal (< 1%)
- RAM: < 50MB
- Python: 3.9+
- OS: Windows 10+, Linux, macOS 10.14+

### Network
- Bandwidth: < 1KB per heartbeat (60 seconds)
- Latency: Tolerates intermittent connectivity
- Ports: Outbound HTTPS/HTTP to server

---

**Built for reliability. Designed for scale. Ready for production.**# greenops
