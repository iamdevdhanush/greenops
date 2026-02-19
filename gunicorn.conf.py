import subprocess
import sys

bind = "0.0.0.0:8000"
workers = 4
worker_class = "gthread"
threads = 2
timeout = 120
keepalive = 5
preload_app = False

def on_starting(server):
    """Wait for DB and reinitialize pool before forking"""
    import time
    import psycopg2
    import os
    
    db_url = os.environ.get("DATABASE_URL", "")
    print("Waiting for DB...", flush=True)
    
    for i in range(30):
        try:
            conn = psycopg2.connect(db_url)
            conn.close()
            print("DB ready.", flush=True)
            return
        except Exception:
            time.sleep(2)
    
    print("DB never became ready, exiting.", flush=True)
    sys.exit(1)

def post_fork(server, worker):
    """Reinitialize DB pool in each worker after fork"""
    from server.database import db
    db.initialize()
