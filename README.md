# GreenOps — Green IT Infrastructure Monitoring Platform

> Monitor organizational computers, detect idle machines, quantify energy waste, and reduce infrastructure costs — all from a self-hosted web dashboard.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Folder Structure](#4-folder-structure)
5. [First-Time Setup](#5-first-time-setup)
6. [Running the Application](#6-running-the-application)
7. [Stopping the Application](#7-stopping-the-application)
8. [Development Mode](#8-development-mode)
9. [Build / Production Mode](#9-build--production-mode)
10. [Configuration](#10-configuration)
11. [Troubleshooting](#11-troubleshooting)
12. [Functional Flow](#12-functional-flow)
13. [Security & Privacy](#13-security--privacy)
14. [Future Improvements](#14-future-improvements)

---

## 1. Project Overview

GreenOps is a self-hosted SaaS-style platform that answers one question: **how much energy and money is your organization wasting on idle computers?**

It works in three parts:

- A **lightweight Python agent** runs on each monitored machine. It detects when the machine is idle (no keyboard/mouse input), and sends regular heartbeats to the central server.
- A **Flask API server** receives those heartbeats, tracks idle time per machine, calculates energy waste in kWh and cost in USD using a documented power consumption model, and stores everything in PostgreSQL.
- A **browser-based dashboard** displays all machines, their status (online / idle / offline), uptime hours, idle time, and cumulative energy wasted — with live filtering and auto-refresh.

The entire stack runs locally via Docker Compose. No external services, no cloud dependencies, no telemetry.

---

## 2. Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Monitored Machines                   │
│                                                         │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────┐  │
│  │  agent.py    │   │  agent.py    │   │  agent.py  │  │
│  │  (Linux)     │   │  (Windows)   │   │  (macOS)   │  │
│  └──────┬───────┘   └──────┬───────┘   └─────┬──────┘  │
└─────────┼─────────────────┼─────────────────┼──────────┘
          │  HTTP POST /api/agents/heartbeat   │
          ▼                 ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│                      Docker Host                        │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │                nginx :80                         │   │
│  │   /          → serve dashboard/                  │   │
│  │   /api/*     → proxy to server:8000              │   │
│  │   /health    → proxy to server:8000              │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │          Flask + Gunicorn (4 workers) :8000       │   │
│  │                                                   │   │
│  │   /api/auth/*         Authentication              │   │
│  │   /api/agents/*       Agent registration/HB      │   │
│  │   /api/machines/*     Dashboard data             │   │
│  │   /api/dashboard/*    Aggregate stats            │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │ psycopg2 connection pool       │
│  ┌──────────────────────▼───────────────────────────┐   │
│  │              PostgreSQL 15 :5432                  │   │
│  │                                                   │   │
│  │   users       admin accounts                      │   │
│  │   machines    one row per MAC address             │   │
│  │   heartbeats  raw heartbeat history               │   │
│  │   agent_tokens  hashed agent auth tokens         │   │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
          ▲
          │  browser → http://localhost
┌─────────┴──────────┐
│  Dashboard (HTML/  │
│  CSS/Vanilla JS)   │
└────────────────────┘
```

### Data Flow — Step by Step

**Agent → Server:**

1. Agent boots, reads `~/.greenops/config.json` and environment variables for server URL and intervals.
2. Agent calls `GET /api/agents/register` with MAC address, hostname, OS type/version.
3. Server upserts a row in `machines` (idempotent on MAC address conflict) and returns a UUID agent token.
4. Agent saves the token to `~/.greenops/token` (permissions `0600`) and begins the heartbeat loop.
5. Every 60 seconds, agent calls the platform-specific idle detector (Win32 `GetLastInputInfo`, Linux `xprintidle`, macOS `ioreg HIDIdleTime`) to get idle seconds.
6. Agent POST `/api/agents/heartbeat` with `idle_seconds`, `cpu_usage`, `memory_usage`, timestamp.
7. Server middleware verifies the Bearer token (SHA-256 hash lookup in `agent_tokens`).
8. Server computes `incremental_idle` by comparing with the previous heartbeat timestamp — this prevents double-counting idle time across heartbeat intervals.
9. Server calls `EnergyService.calculate_idle_energy_waste(incremental_idle)` → `(idle_seconds / 3600) * (IDLE_POWER_WATTS / 1000)`.
10. Server atomically UPDATEs `machines.total_idle_seconds` and `machines.energy_wasted_kwh`, then INSERTs a row into `heartbeats`.

**Dashboard → Server:**

1. Browser loads static files served by nginx from `dashboard/`.
2. User submits login form → `POST /api/auth/login` → Argon2id password verification → JWT returned (24h expiry).
3. JWT stored in `localStorage`. All subsequent requests send `Authorization: Bearer <jwt>`.
4. Dashboard polls `GET /api/dashboard/stats` and `GET /api/machines` every 30 seconds.
5. Server filters/paginates machine records, serialises datetime fields, returns JSON.
6. Dashboard renders status badges, uptime hours, idle time, energy kWh per machine.

**Background — Offline Detection:**

A single daemon thread in the gunicorn master process runs every 60 seconds and marks machines `offline` if their `last_seen` is older than `HEARTBEAT_TIMEOUT_SECONDS` (default 180s).

### Energy Calculation Model

```
idle_hours   = idle_seconds / 3600
energy_kWh   = idle_hours × (IDLE_POWER_WATTS / 1000)   # default: 65W
cost_USD     = energy_kWh × ELECTRICITY_COST_PER_KWH     # default: $0.12
CO₂_kg       = energy_kWh × 0.42                         # US EPA 2023 average
```

Assumptions: desktop PCs, modern hardware (2015+), standard monitors, no high-power GPUs.

---

## 3. Technology Stack

### Server

| Component | Technology |
|---|---|
| Web framework | Flask 3.0.3 |
| WSGI server | Gunicorn 21.2.0 (gthread worker, 4 workers × 2 threads) |
| Database | PostgreSQL 15 |
| DB driver | psycopg2-binary 2.9.9 (threaded connection pool) |
| Auth — admin | PyJWT 2.8.0 (HS256, 24h expiry) |
| Auth — agents | UUID tokens, SHA-256 hashed in DB |
| Password hashing | argon2-cffi 23.1.0 (Argon2id, m=65536, t=2, p=4) |
| CORS | flask-cors 4.0.1 |
| Env config | python-dotenv 1.0.1 |

### Agent

| Component | Technology |
|---|---|
| Runtime | Python 3.9+ |
| HTTP client | requests 2.31.0 |
| Idle detection (Windows) | ctypes / Win32 `GetLastInputInfo` |
| Idle detection (Linux) | `xprintidle` subprocess |
| Idle detection (macOS) | `ioreg` subprocess (IOHIDSystem HIDIdleTime) |

### Infrastructure

| Component | Technology |
|---|---|
| Containerisation | Docker + Docker Compose v2 |
| Reverse proxy | nginx:alpine |
| Database container | postgres:15-alpine |
| Migrations | Plain SQL, auto-run by postgres `docker-entrypoint-initdb.d` |

### Dashboard

| Component | Technology |
|---|---|
| Structure | Vanilla HTML5 |
| Styling | Vanilla CSS (CSS custom properties) |
| Logic | Vanilla JavaScript (ES6 class, fetch API) |
| Build | None — static files served directly by nginx |

---

## 4. Folder Structure

```
greenops/
│
├── agent/                        # Monitoring agent (runs on each target machine)
│   ├── agent.py                  # Main loop: register → heartbeat → retry
│   ├── config.py                 # Config from env vars + ~/.greenops/config.json
│   ├── idle_detector.py          # Platform-specific idle time detection
│   ├── requirements.txt          # requests==2.31.0
│   └── __init__.py
│
├── server/                       # Flask API server
│   ├── main.py                   # App factory (create_app), logging setup, offline checker
│   ├── auth.py                   # AuthService: JWT, Argon2id, agent token CRUD
│   ├── config.py                 # Config class reading env vars
│   ├── database.py               # ThreadedConnectionPool wrapper (db singleton)
│   ├── middleware.py             # Decorators: require_jwt, require_agent_token, rate_limit_login
│   ├── __init__.py
│   ├── routes/
│   │   ├── auth.py               # POST /api/auth/login, GET /api/auth/verify
│   │   ├── agents.py             # POST /api/agents/register, POST /api/agents/heartbeat
│   │   ├── dashboard.py          # GET /api/machines, GET /api/dashboard/stats, DELETE
│   │   └── __init__.py
│   └── services/
│       ├── energy.py             # Energy/cost/CO₂ calculation (Decimal precision)
│       ├── machine.py            # MachineService: register, heartbeat, list, stats
│       └── __init__.py
│
├── dashboard/                    # Browser dashboard (static files, served by nginx)
│   ├── index.html                # Login + dashboard screens
│   ├── app.js                    # GreenOpsApp class: auth, fetch, render
│   └── styles.css                # Enterprise-style CSS (CSS vars, responsive grid)
│
├── migrations/                   # SQL migration files (run once on first DB volume init)
│   ├── 001_initial_schema.sql    # Tables: users, machines, heartbeats, agent_tokens
│   └── 002_indexes_and_retention.sql  # Additional indexes, retention function, updated_at trigger
│
├── gunicorn.conf.py              # Gunicorn config: preload_app=True, post_fork DB reinit
├── Dockerfile.server             # Server image: python:3.11-slim + app code
├── docker-compose.yml            # Orchestrates: db, server, nginx
├── nginx.conf                    # Nginx: serve dashboard, proxy /api/* to server:8000
├── requirements.txt              # Server Python dependencies
├── .env                          # Runtime secrets (JWT key, DB password) — DO NOT COMMIT
├── .env.example                  # Template for .env with all variable documentation
└── start_agent.sh                # Convenience script to run the agent in a venv
```

---

## 5. First-Time Setup

### Prerequisites

Install these before anything else:

```bash
# Docker Engine + Compose plugin (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin

# Add your user to the docker group (avoids sudo on every command)
sudo usermod -aG docker $USER
newgrp docker          # apply in current shell without logout

# Python 3.9+ for running the agent locally
python3 --version      # must be 3.9 or higher

# pip
sudo apt-get install -y python3-pip
```

### Clone and Configure

```bash
git clone https://github.com/iamdevdhanush/greenops.git
cd greenops
```

Generate secrets and create your `.env` file:

```bash
# Generate cryptographically secure keys
JWT_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

cat > .env <<EOF
JWT_SECRET_KEY=${JWT_KEY}
POSTGRES_PASSWORD=${DB_PASS}
DATABASE_URL=postgresql://greenops:${DB_PASS}@db:5432/greenops
EOF

echo ".env created:"
cat .env
```

> **Never commit `.env` to version control.** It is listed in `.gitignore` — verify with `git status`.

---

## 6. Running the Application

### Start the Server Stack

```bash
cd ~/greenops

# First run: build the server image and start all three containers
docker compose up -d --build

# Watch logs until you see "GreenOps server initialised and ready."
docker compose logs -f server
```

Expected startup sequence in logs:

```
create_app() starting (pid=1, debug=False …)
Database pool initialised (minconn=1, maxconn=20).
Database connectivity verified.
[gunicorn] Waiting for PostgreSQL …
[gunicorn] PostgreSQL is ready (attempt 1).
Offline checker started (interval=60s …)
GreenOps server initialised and ready.
Worker 8: DB pool initialised.
Worker 9: DB pool initialised.
Worker 10: DB pool initialised.
Worker 11: DB pool initialised.
```

### Verify the Stack is Healthy

```bash
# All three containers should show "Up"
docker compose ps

# API health check
curl http://localhost:8000/health
# Expected: {"status": "healthy", "database": "connected"}
```

### Access the Dashboard

Open **http://localhost** in your browser.

Default credentials: `admin` / `admin123`

> Change the admin password immediately after first login using the method below.

### Change the Admin Password

```bash
# Generate hash inside the container (uses the exact same library as the server)
docker compose exec server python3 -c "
from argon2 import PasswordHasher
ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=4)
print(ph.hash('YOUR_NEW_PASSWORD'))
"

# Apply the hash (paste the output from above)
docker compose exec db psql -U greenops -c \
  "UPDATE users SET password_hash = 'PASTE_HASH_HERE' WHERE username = 'admin';"
```

### Run the Agent on a Machine

The agent can be run on any machine that has network access to the server.

```bash
cd ~/greenops/agent

# Create and activate isolated virtualenv
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# Install dependencies (offline after this step)
pip install -r requirements.txt

# Run against your server
GREENOPS_SERVER_URL=http://localhost:8000 python3 agent.py
```

On Linux, install `xprintidle` for accurate idle detection:

```bash
sudo apt-get install -y xprintidle
```

The agent will register itself automatically on first run and save its token to `~/.greenops/token`. Your machine will appear in the dashboard within 60 seconds.

---

## 7. Stopping the Application

### Stop containers (preserves database data)

```bash
docker compose down
```

### Stop and wipe all data (full reset)

```bash
docker compose down -v      # -v removes the postgres_data volume
```

### Stop the agent

Press `Ctrl+C` in the terminal running `agent.py`. It handles `SIGINT` and `SIGTERM` gracefully.

---

## 8. Development Mode

### Run the Server Locally (without Docker)

```bash
cd ~/greenops

# Install server dependencies
pip install -r requirements.txt

# Start only the database container
docker compose up -d db

# Export environment for local run
export DATABASE_URL="postgresql://greenops:$(grep POSTGRES_PASSWORD .env | cut -d= -f2)@localhost:5433/greenops"
export JWT_SECRET_KEY="$(grep JWT_SECRET_KEY .env | cut -d= -f2)"
export DEBUG=true
export LOG_FILE="./logs/greenops.log"

mkdir -p logs

# Run Flask dev server (single process, auto-reload)
python3 -m server.main
```

The server starts at `http://localhost:8000`. Flask's built-in reloader watches for file changes.

### Watching Logs Live

```bash
# Server container logs
docker compose logs -f server

# Application log file (inside container)
docker compose exec server tail -f /app/logs/greenops.log

# On host (if ./logs is bind-mounted)
tail -f ~/greenops/logs/greenops.log
```

### Database Access During Development

```bash
# Interactive psql shell
docker compose exec db psql -U greenops

# Useful queries
SELECT id, hostname, status, last_seen, energy_wasted_kwh FROM machines;
SELECT COUNT(*) FROM heartbeats;
SELECT username, role FROM users;
```

---

## 9. Build / Production Mode

### Rebuild After Code Changes

```bash
docker compose build --no-cache
docker compose up -d
```

### Production Hardening Checklist

- [ ] Generate a fresh `JWT_SECRET_KEY` (at least 32 bytes, `secrets.token_urlsafe(32)`)
- [ ] Set a strong `POSTGRES_PASSWORD` (never use the generated dev default in production)
- [ ] Change the admin password from `admin123`
- [ ] Set `DEBUG=false` (already the default)
- [ ] Set `CORS_ORIGINS` to your actual domain instead of `*`
- [ ] Set up HTTPS via nginx + Let's Encrypt (Certbot)
- [ ] Restrict firewall: only expose ports 80 and 443 externally; keep 8000 and 5432 internal
- [ ] Set `LOG_LEVEL=WARNING` or `ERROR` in production to reduce I/O
- [ ] Configure log rotation (`logrotate` or Docker's `--log-opt max-size`)
- [ ] Set up automated PostgreSQL backups (see Operations below)

### Database Backup and Restore

```bash
# Backup
docker compose exec db pg_dump -U greenops greenops > backup_$(date +%Y%m%d).sql

# Restore
docker compose exec -T db psql -U greenops greenops < backup_20260101.sql
```

---

## 10. Configuration

### Environment Variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | *(required)* | Secret for signing admin JWTs. Min 32 chars. |
| `POSTGRES_PASSWORD` | *(required)* | PostgreSQL password for the `greenops` user. |
| `DATABASE_URL` | *(required)* | Full PostgreSQL DSN. Must use `@db:5432` inside Docker. |
| `DEBUG` | `false` | Enable Flask debug mode and relax JWT key validation. |
| `LOG_LEVEL` | `INFO` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_FILE` | `<cwd>/logs/greenops.log` | Absolute path for the rotating log file. |
| `CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins. Lock down in production. |
| `JWT_EXPIRATION_HOURS` | `24` | Admin JWT lifetime in hours. |
| `LOGIN_RATE_LIMIT` | `5` | Max login attempts per IP per window. |
| `LOGIN_RATE_WINDOW` | `900` | Rate limit window in seconds (15 minutes). |
| `IDLE_POWER_WATTS` | `65` | Assumed idle power draw per machine (watts). |
| `ACTIVE_POWER_WATTS` | `120` | Assumed active power draw per machine (watts). |
| `ELECTRICITY_COST_PER_KWH` | `0.12` | Local electricity rate in USD/kWh. |
| `IDLE_THRESHOLD_SECONDS` | `300` | Seconds without input before a machine is considered idle. |
| `HEARTBEAT_TIMEOUT_SECONDS` | `180` | Seconds without a heartbeat before marking machine offline. |
| `OFFLINE_CHECK_INTERVAL_SECONDS` | `60` | How often the background thread checks for offline machines. |
| `DB_POOL_SIZE` | `20` | Max DB connections per worker process. |
| `ADMIN_INITIAL_PASSWORD` | *(unset)* | If set, updates the admin password hash on startup then clears itself. |

### Agent Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GREENOPS_SERVER_URL` | `http://localhost:8000` | URL of the GreenOps API server. |
| `GREENOPS_HEARTBEAT_INTERVAL` | `60` | Seconds between heartbeats. |
| `GREENOPS_IDLE_THRESHOLD` | `300` | Seconds of inactivity before reporting idle. |
| `GREENOPS_RETRY_BASE` | `5` | Initial retry delay in seconds (exponential backoff). |
| `GREENOPS_RETRY_MAX` | `300` | Maximum retry delay in seconds. |
| `GREENOPS_MAX_RETRIES` | `5` | Consecutive failures before logging a warning. |

### Agent Config File

Agents also read `~/.greenops/config.json` (Linux/macOS) or `C:\ProgramData\GreenOps\config.json` (Windows):

```json
{
  "server_url": "http://your-server:8000",
  "heartbeat_interval": 60,
  "idle_threshold": 300,
  "retry_backoff_base": 5,
  "retry_backoff_max": 300
}
```

### Ports

| Port | Service | Notes |
|---|---|---|
| `80` | nginx (dashboard + API proxy) | Main user-facing port |
| `8000` | Gunicorn (Flask API) | Internal; proxied by nginx |
| `5433` | PostgreSQL (host-side) | Mapped from container's 5432; for local dev access |

---

## 11. Troubleshooting

### `docker compose` command not found

Your system has Docker Compose v2 (plugin), not v1. Use `docker compose` (space, no hyphen):

```bash
docker compose up -d      # correct
docker-compose up -d      # wrong on this system
```

### Server container restarts in a loop

Check for connection exhaustion:

```bash
docker compose logs server | grep -E "error|Error|FATAL"
docker compose exec db psql -U greenops -c \
  "SELECT count(*) FROM pg_stat_activity;"
```

If count climbs above 80, `gunicorn.conf.py` may still have `preload_app = False` or be missing entirely. Ensure `preload_app = True` is set.

### "Invalid credentials" on admin login

The default password hash in the migration may not match your runtime. Reset it:

```bash
docker compose exec server python3 -c "
from argon2 import PasswordHasher
ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=4)
print(ph.hash('admin123'))
"
# Copy the output, then:
docker compose exec db psql -U greenops -c \
  "UPDATE users SET password_hash = 'PASTE_OUTPUT_HERE' WHERE username = 'admin';"
```

### `curl: (7) Failed to connect to localhost port 8000`

The server container hasn't started. Check:

```bash
docker compose ps              # is server Up or Exited?
docker compose logs server     # what happened?
```

### Agent reports `0` idle seconds on Linux

`xprintidle` is not installed or the agent is running without an X11 session:

```bash
sudo apt-get install -y xprintidle
# Test it:
xprintidle
```

On headless servers, idle detection returns 0 by design (no display = no user input to detect).

### Machine stays "online" even after agent stops

The offline checker marks machines offline after 180 seconds (`HEARTBEAT_TIMEOUT_SECONDS`) without a heartbeat. Wait 3 minutes after stopping the agent and refresh the dashboard.

### `permission denied` on `docker compose restart`

Use `docker compose down && docker compose up -d` instead of `restart`. The restart command has a known permission issue with certain container states on some Docker versions.

### Postgres volume already initialized but schema is wrong

The SQL migration files in `migrations/` only run once — on the first boot of an empty volume. To reapply them:

```bash
docker compose down -v        # destroys the postgres_data volume
docker compose up -d          # re-runs all migrations on fresh volume
```

---

## 12. Functional Flow

### Complete User Journey

```
1. Admin opens http://localhost
   └── nginx serves dashboard/index.html + app.js + styles.css

2. Admin logs in (username/password)
   └── POST /api/auth/login
       └── Argon2id password verify
       └── JWT generated (24h)
       └── Stored in browser localStorage

3. Dashboard loads
   └── GET /api/machines         → machine table
   └── GET /api/dashboard/stats  → summary cards
   └── Auto-refreshes every 30s

4. On a monitored machine, agent boots
   └── POST /api/agents/register (MAC address)
       └── Upsert in machines table
       └── UUID token created, SHA-256 hashed in agent_tokens
       └── Plaintext token returned to agent, saved to ~/.greenops/token

5. Agent heartbeat loop (every 60s)
   └── Detects idle_seconds via platform API
   └── POST /api/agents/heartbeat
       Bearer token verified (SHA-256 lookup)
       └── Incremental idle computed vs previous heartbeat
       └── Energy waste calculated (Decimal precision)
       └── machines table updated atomically
       └── heartbeats row inserted

6. Background thread (every 60s in server master)
   └── UPDATE machines SET status='offline'
       WHERE last_seen < NOW() - 180s AND status != 'offline'

7. Admin sees in dashboard
   └── Machine status: online / idle / offline
   └── Energy wasted: cumulative kWh
   └── Uptime hours = (total_idle_seconds + total_active_seconds) / 3600
```

---

## 13. Security & Privacy

### What is Collected

| Data | Stored Where | Retained |
|---|---|---|
| MAC address | `machines` table | Permanently (machine identity) |
| Hostname | `machines` table | Updated on each registration |
| OS type and version | `machines` table | Updated on each registration |
| Seconds since last keyboard/mouse input | `heartbeats` table | Indefinitely (use retention function) |
| CPU usage (placeholder — always 0.0) | `heartbeats` table | Indefinitely |
| Memory usage (placeholder — always 0.0) | `heartbeats` table | Indefinitely |
| Heartbeat timestamp | `heartbeats` table | Indefinitely |

### What is NOT Collected

- Keystrokes, mouse movements, or any input content
- Screen captures or screenshots
- Process names or application usage
- File system contents
- Network traffic
- Location data
- Any personally identifiable information beyond hostname

### Security Controls

- Admin passwords hashed with Argon2id (the current recommended standard), never stored plaintext.
- Agent tokens are UUID4, stored as SHA-256 hashes. The plaintext is never logged.
- JWT tokens expire after 24 hours and are signed with HS256.
- Login endpoint is rate-limited (5 attempts / 15 minutes per IP).
- All DB inputs are parameterised (no string interpolation in SQL).
- `ADMIN_INITIAL_PASSWORD` is cleared from memory immediately after use.

### Data Retention

The `migrations/002_indexes_and_retention.sql` file creates a SQL function `prune_old_heartbeats(retain_days)` for deleting old heartbeat rows. Call it manually or schedule via cron:

```sql
SELECT prune_old_heartbeats(90);  -- delete heartbeats older than 90 days
```

---

## 14. Future Improvements

The following are improvements identified from the codebase structure but not yet implemented:

- **CPU and memory monitoring** — `agent.py` sends `cpu_usage=0.0` and `memory_usage=0.0` as placeholders. Implementing `psutil` would provide real values.
- **Per-machine power profiles** — Currently all machines use global watt settings. A per-machine profile table would improve accuracy for mixed hardware fleets.
- **Redis-backed rate limiting** — The current login rate limiter is per-process in-memory. With 4 Gunicorn workers the effective limit is 4× the configured value. Redis would enforce a true global limit.
- **Prometheus metrics endpoint** — `/metrics` is referenced in documentation but not implemented.
- **Shutdown schedule enforcement** — Alerting or automatic shutdown for machines idle beyond a threshold.
- **Weekly/monthly energy reports** — PDF or CSV export of energy data per machine or department.
- **Slack / email / webhook alerts** — Notify when machines go offline unexpectedly or idle rate exceeds a threshold.
- **Heartbeat data pruning automation** — Schedule `prune_old_heartbeats()` via pg_cron rather than requiring manual SQL calls.
- **HTTPS / TLS** — nginx configuration for Let's Encrypt certificates is not included.
- **Multi-user support** — Only a single `admin` role exists; a `viewer` role schema column exists but no viewer-specific access controls are implemented.
