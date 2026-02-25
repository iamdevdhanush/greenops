-- ============================================================
-- GreenOps Migration 004 — App Settings Table
-- ============================================================
-- Replaces ENV-based runtime configuration with DB-persisted settings.
-- After this migration, the following ENV vars are NO LONGER USED:
--   IDLE_THRESHOLD_SECONDS, IDLE_POWER_WATTS, ELECTRICITY_COST_PER_KWH, etc.
--
-- Safe to run multiple times (fully idempotent).
-- ============================================================

CREATE TABLE IF NOT EXISTS app_settings (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT         NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Seed defaults (only insert if key not already present) ──────────────────
INSERT INTO app_settings (key, value, description) VALUES
    ('electricity_cost_per_kwh',  '0.12',     'Electricity rate for cost calculations (USD/kWh)'),
    ('idle_power_watts',           '65',       'Assumed idle power draw per machine (watts)'),
    ('currency',                   'USD',      'Display currency for cost calculations'),
    ('idle_threshold_seconds',     '300',      'Seconds of inactivity before marking machine as idle'),
    ('heartbeat_timeout_seconds',  '180',      'Seconds without heartbeat before marking machine offline'),
    ('agent_heartbeat_interval',   '60',       'Recommended agent heartbeat interval (informational, seconds)'),
    ('organization_name',          'GreenOps', 'Display name for the organization'),
    ('log_level',                  'INFO',     'Server log verbosity: DEBUG | INFO | WARNING | ERROR')
ON CONFLICT (key) DO NOTHING;

-- ── Auto-update updated_at on any change ───────────────────────────────────
CREATE OR REPLACE FUNCTION set_app_setting_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_app_settings_updated_at ON app_settings;
CREATE TRIGGER trg_app_settings_updated_at
    BEFORE UPDATE ON app_settings
    FOR EACH ROW
    EXECUTE FUNCTION set_app_setting_updated_at();

-- ── Ensure machines table uses TIMESTAMPTZ for last_seen ───────────────────
-- This makes timezone-aware comparisons safe.
DO $$
BEGIN
    -- Check if last_seen column exists and is not already TIMESTAMPTZ
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'machines'
          AND column_name = 'last_seen'
          AND data_type = 'timestamp without time zone'
    ) THEN
        -- Convert naive timestamps to UTC-aware
        ALTER TABLE machines
            ALTER COLUMN last_seen TYPE TIMESTAMPTZ
            USING last_seen AT TIME ZONE 'UTC';
        RAISE NOTICE 'Converted machines.last_seen to TIMESTAMPTZ';
    END IF;
END;
$$;

-- ── Add pending_command column if missing ──────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'machines'
          AND column_name = 'pending_command'
    ) THEN
        ALTER TABLE machines ADD COLUMN pending_command VARCHAR(50);
        RAISE NOTICE 'Added machines.pending_command column';
    END IF;
END;
$$;

-- ── Add uptime_seconds if missing (replaces uptime_hours) ─────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'machines'
          AND column_name = 'uptime_seconds'
    ) THEN
        ALTER TABLE machines ADD COLUMN uptime_seconds BIGINT DEFAULT 0;
        -- Migrate from uptime_hours if it existed
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'machines'
              AND column_name = 'uptime_hours'
        ) THEN
            UPDATE machines SET uptime_seconds = COALESCE(uptime_hours, 0) * 3600;
            RAISE NOTICE 'Migrated uptime_hours → uptime_seconds';
        END IF;
    END IF;
END;
$$;
