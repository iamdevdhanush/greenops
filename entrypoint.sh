#!/bin/bash
set -e

echo "Waiting for DB..."
until pg_isready -h db -p 5432 -U greenops; do
    sleep 1
done

echo "Running migrations..."
psql "$DATABASE_URL" < /app/migrations/001_initial_schema.sql

echo "Starting server..."
exec gunicorn --config /app/gunicorn.conf.py server.main:app
