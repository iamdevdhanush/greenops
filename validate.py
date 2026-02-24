#!/usr/bin/env python3
"""
GreenOps System Validation Script
Verifies all required files are present for a production deployment.
"""
import sys
from pathlib import Path

def chk(path, desc):
    p = Path(path)
    ok = p.exists()
    print(f"{'OK' if ok else 'MISSING':8} {desc:50} {path}")
    return ok

def main():
    base = Path(__file__).parent
    results = []

    print("=" * 70)
    print("GreenOps System Validation")
    print("=" * 70)

    print("\n--- Server ---")
    results += [
        chk(base / "server/main.py",               "Application factory"),
        chk(base / "server/database.py",           "Database layer"),
        chk(base / "server/config.py",             "Configuration"),
        chk(base / "server/auth.py",               "Auth service"),
        chk(base / "server/middleware.py",          "Middleware / decorators"),
        chk(base / "server/routes/auth.py",        "Auth routes"),
        chk(base / "server/routes/agents.py",      "Agent routes"),
        chk(base / "server/routes/dashboard.py",   "Dashboard routes"),
        chk(base / "server/services/machine.py",   "Machine service"),
        chk(base / "server/services/energy.py",    "Energy service"),
    ]

    print("\n--- Agent ---")
    results += [
        chk(base / "agent/agent.py",              "Agent main loop"),
        chk(base / "agent/config.py",             "Agent config"),
        chk(base / "agent/idle_detector.py",      "Idle detection"),
        chk(base / "agent/requirements.txt",      "Agent requirements"),
    ]

    print("\n--- Dashboard ---")
    results += [
        chk(base / "dashboard/index.html",        "Dashboard HTML"),
        chk(base / "dashboard/app.js",            "Dashboard JS"),
        chk(base / "dashboard/styles.css",        "Dashboard CSS"),
    ]

    print("\n--- Migrations ---")
    results += [
        chk(base / "migrations/001_initial_schema.sql",    "Schema migration"),
        chk(base / "migrations/002_indexes_and_retention.sql", "Indexes migration"),
        chk(base / "migrations/003_upgrades.sql",          "Upgrades migration"),
    ]

    print("\n--- Infrastructure ---")
    results += [
        chk(base / "docker-compose.yml",          "Docker Compose"),
        chk(base / "Dockerfile.server",           "Server Dockerfile"),
        chk(base / "nginx.conf",                  "Nginx config"),
        chk(base / "gunicorn.conf.py",            "Gunicorn config"),
        chk(base / "requirements.txt",            "Server requirements"),
        chk(base / ".env.example",                "Environment example"),
    ]

    print("\n" + "=" * 70)
    passed = sum(results)
    total  = len(results)
    if all(results):
        print(f"PASSED — {passed}/{total} checks passed")
        print("\nDeployment commands:")
        print("  Docker:  docker compose up -d --build")
        print("  Local:   bash dev-setup.sh")
        print("  Agent:   bash start_agent.sh")
        return 0
    else:
        print(f"FAILED — {passed}/{total} checks passed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
