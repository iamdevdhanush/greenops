"""
GreenOps Test Suite
====================
Run: pytest tests/test_greenops.py -v

Install test deps:
    pip install pytest==8.2.0 pytest-mock==3.14.0
"""

import hashlib
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta


# ─────────────────────────────────────────────────────────────────────────────
# EnergyService
# ─────────────────────────────────────────────────────────────────────────────

class TestEnergyService:

    def test_zero_idle_seconds_returns_zero(self):
        from server.services.energy import EnergyService
        with patch("server.services.energy.config") as cfg:
            cfg.IDLE_POWER_WATTS = 65
            result = EnergyService.calculate_idle_energy_waste(0)
            assert result == Decimal("0.000")

    def test_negative_idle_seconds_returns_zero(self):
        from server.services.energy import EnergyService
        with patch("server.services.energy.config") as cfg:
            cfg.IDLE_POWER_WATTS = 65
            result = EnergyService.calculate_idle_energy_waste(-100)
            assert result == Decimal("0.0")

    def test_one_hour_at_65w_equals_0065_kwh(self):
        """1 hour at 65W = 0.065 kWh"""
        from server.services.energy import EnergyService
        with patch("server.services.energy.config") as cfg:
            cfg.IDLE_POWER_WATTS = 65
            result = EnergyService.calculate_idle_energy_waste(3600)
            assert result == Decimal("0.065")

    def test_cost_calculation(self):
        """0.065 kWh × $0.12/kWh = $0.01"""
        from server.services.energy import EnergyService
        with patch("server.services.energy.config") as cfg:
            cfg.ELECTRICITY_COST_PER_KWH = 0.12
            result = EnergyService.calculate_cost(Decimal("0.065"))
            assert result == Decimal("0.01")

    def test_co2_at_us_average(self):
        """1 kWh → 0.42 kg CO2"""
        from server.services.energy import EnergyService
        result = EnergyService.estimate_co2_emissions(Decimal("1.000"))
        assert result == Decimal("0.420")

    def test_result_has_3_decimal_places(self):
        from server.services.energy import EnergyService
        with patch("server.services.energy.config") as cfg:
            cfg.IDLE_POWER_WATTS = 65
            result = EnergyService.calculate_idle_energy_waste(3600)
            decimals = str(result).split(".")[-1]
            assert len(decimals) == 3

    def test_no_division_by_zero_on_zero_machines(self):
        from server.services.energy import EnergyService
        with patch("server.services.energy.config") as cfg:
            cfg.IDLE_POWER_WATTS = 65
            cfg.ELECTRICITY_COST_PER_KWH = 0.12
            result = EnergyService.calculate_potential_savings(0, 0)
            assert result["avg_idle_hours_per_machine"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# AuthService
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthService:

    def test_hash_password_produces_argon2id(self):
        from server.auth import AuthService
        h = AuthService.hash_password("testpassword")
        assert h.startswith("$argon2id$")

    def test_verify_agent_token_uses_sha256(self):
        plaintext = "my-test-token-abc123"
        expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()

        mock_db = MagicMock()
        mock_db.execute_one.return_value = {"machine_id": 42}

        with patch("server.auth.db", mock_db):
            from server.auth import AuthService
            result = AuthService.verify_agent_token(plaintext)
            assert result == 42
            call_params = str(mock_db.execute_one.call_args)
            assert expected_hash in call_params

    def test_verify_agent_token_none_for_unknown(self):
        mock_db = MagicMock()
        mock_db.execute_one.return_value = None

        with patch("server.auth.db", mock_db):
            from server.auth import AuthService
            assert AuthService.verify_agent_token("bad-token") is None

    def test_generate_jwt_fields(self):
        with patch("server.auth.config") as cfg:
            cfg.JWT_SECRET_KEY = "a" * 32
            cfg.JWT_ALGORITHM = "HS256"
            cfg.JWT_EXPIRATION_HOURS = 24

            from server.auth import AuthService
            import jwt as pyjwt

            token = AuthService.generate_jwt(1, "admin", "admin")
            payload = pyjwt.decode(token, "a" * 32, algorithms=["HS256"])
            assert payload["user_id"] == 1
            assert payload["username"] == "admin"
            assert payload["role"] == "admin"
            assert "exp" in payload

    def test_expired_jwt_returns_none(self):
        with patch("server.auth.config") as cfg:
            cfg.JWT_SECRET_KEY = "a" * 32
            cfg.JWT_ALGORITHM = "HS256"

            import jwt as pyjwt
            past = datetime.now(timezone.utc) - timedelta(hours=25)
            token = pyjwt.encode(
                {"exp": past, "user_id": 1}, "a" * 32, algorithm="HS256"
            )

            from server.auth import AuthService
            assert AuthService.verify_jwt(token) is None

    def test_authenticate_unknown_user_returns_none(self):
        mock_db = MagicMock()
        mock_db.execute_one.return_value = None

        with patch("server.auth.db", mock_db):
            from server.auth import AuthService
            result = AuthService.authenticate_user("ghost", "pass")
            assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# MachineService
# ─────────────────────────────────────────────────────────────────────────────

class TestMachineService:

    def test_register_normalises_mac(self):
        mock_db = MagicMock()
        mock_db.execute_one.return_value = {"id": 1, "inserted": True}
        mock_auth = MagicMock()
        mock_auth.create_agent_token.return_value = "tok"

        with patch("server.services.machine.db", mock_db), \
             patch("server.services.machine.AuthService", mock_auth):
            from server.services.machine import MachineService
            MachineService.register_machine("aa-bb-cc-dd-ee-ff", "h", "Linux")
            call_params = mock_db.execute_one.call_args[0][1]
            assert call_params[0] == "AA:BB:CC:DD:EE:FF"

    def test_heartbeat_above_threshold_is_idle(self):
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = {"energy_wasted_kwh": Decimal("0.001")}

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        mock_db = MagicMock()
        mock_db.execute_one.return_value = None
        mock_db.get_connection.return_value = mock_conn

        mock_cfg = MagicMock()
        mock_cfg.IDLE_THRESHOLD_SECONDS = 300
        mock_cfg.IDLE_POWER_WATTS = 65
        mock_cfg.ELECTRICITY_COST_PER_KWH = 0.12

        with patch("server.services.machine.db", mock_db), \
             patch("server.services.machine.config", mock_cfg), \
             patch("server.services.energy.config", mock_cfg):
            from server.services.machine import MachineService
            result = MachineService.process_heartbeat(machine_id=1, idle_seconds=400)
            assert result["machine_status"] == "idle"
            assert result["is_idle"] is True

    def test_heartbeat_below_threshold_is_online(self):
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = {"energy_wasted_kwh": Decimal("0.000")}

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        mock_db = MagicMock()
        mock_db.execute_one.return_value = None
        mock_db.get_connection.return_value = mock_conn

        mock_cfg = MagicMock()
        mock_cfg.IDLE_THRESHOLD_SECONDS = 300
        mock_cfg.IDLE_POWER_WATTS = 65
        mock_cfg.ELECTRICITY_COST_PER_KWH = 0.12

        with patch("server.services.machine.db", mock_db), \
             patch("server.services.machine.config", mock_cfg), \
             patch("server.services.energy.config", mock_cfg):
            from server.services.machine import MachineService
            result = MachineService.process_heartbeat(machine_id=1, idle_seconds=50)
            assert result["machine_status"] == "online"
            assert result["is_idle"] is False

    def test_dashboard_stats_all_zeros_when_no_machines(self):
        mock_db = MagicMock()
        mock_db.execute_one.return_value = {"total_machines": 0}

        with patch("server.services.machine.db", mock_db):
            from server.services.machine import MachineService
            result = MachineService.get_dashboard_stats()
            assert result["total_machines"] == 0
            assert result["estimated_cost_usd"] == 0.0
            assert result["average_idle_percentage"] == 0.0

    def test_list_machines_returns_list(self):
        mock_db = MagicMock()
        mock_db.execute_query.return_value = []

        with patch("server.services.machine.db", mock_db):
            from server.services.machine import MachineService
            result = MachineService.list_machines()
            assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# Config validation
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigValidation:

    def _fresh_config(self, **overrides):
        from server.config import Config
        cfg = Config()
        cfg.DATABASE_URL = "postgresql://x:y@z/d"
        cfg.JWT_SECRET_KEY = "a" * 32
        cfg.DEBUG = False
        cfg.DB_POOL_SIZE = 10
        cfg.JWT_EXPIRATION_HOURS = 24
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_missing_jwt_raises(self):
        cfg = self._fresh_config(JWT_SECRET_KEY="")
        with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
            cfg.validate()

    def test_missing_db_url_raises(self):
        cfg = self._fresh_config(DATABASE_URL="")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            cfg.validate()

    def test_short_jwt_in_prod_raises(self):
        cfg = self._fresh_config(JWT_SECRET_KEY="short", DEBUG=False)
        with pytest.raises(ValueError, match="too short"):
            cfg.validate()

    def test_valid_config_passes(self):
        cfg = self._fresh_config()
        cfg.validate()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimiter:

    def test_blocks_after_limit_exceeded(self):
        from server.middleware import _login_attempts, _rate_lock
        from server.config import config
        from flask import Flask
        from server.middleware import rate_limit_login

        with _rate_lock:
            _login_attempts.clear()

        ip = "10.0.0.99"
        with _rate_lock:
            from datetime import datetime
            now = datetime.utcnow()
            _login_attempts[ip] = [now] * config.LOGIN_RATE_LIMIT

        app = Flask(__name__)

        @app.route("/login-test", methods=["POST"])
        @rate_limit_login
        def fake_login():
            return "ok", 200

        with app.test_client() as client:
            resp = client.post(
                "/login-test",
                environ_base={"REMOTE_ADDR": ip},
            )
            assert resp.status_code == 429

        with _rate_lock:
            _login_attempts.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Health endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:

    def _make_app(self, mock_db):
        with patch("server.database.db", mock_db), \
             patch("server.main.db", mock_db), \
             patch("server.config.config.validate"), \
             patch("server.main._ensure_schema"), \
             patch("server.main._apply_admin_password"), \
             patch("server.main._start_offline_checker"):
            from server.main import create_app
            return create_app()

    def test_200_when_db_ok(self):
        mock_db = MagicMock()
        mock_db.execute_one.return_value = {"ok": 1}
        mock_db.initialize.return_value = None
        mock_db.pool = MagicMock()

        app = self._make_app(mock_db)
        app.config["TESTING"] = True

        with app.test_client() as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "healthy"

    def test_503_when_db_down(self):
        mock_db = MagicMock()
        mock_db.execute_one.side_effect = Exception("connection refused")
        mock_db.initialize.return_value = None
        mock_db.pool = MagicMock()

        app = self._make_app(mock_db)
        app.config["TESTING"] = True

        with app.test_client() as client:
            resp = client.get("/health")
            assert resp.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# Agent routes
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRoutes:

    def _make_app(self, mock_db):
        with patch("server.database.db", mock_db), \
             patch("server.main.db", mock_db), \
             patch("server.auth.db", mock_db), \
             patch("server.config.config.validate"), \
             patch("server.main._ensure_schema"), \
             patch("server.main._apply_admin_password"), \
             patch("server.main._start_offline_checker"):
            from server.main import create_app
            return create_app()

    def test_register_missing_mac_returns_400(self):
        mock_db = MagicMock()
        mock_db.initialize.return_value = None
        mock_db.pool = MagicMock()

        app = self._make_app(mock_db)
        app.config["TESTING"] = True

        with app.test_client() as client:
            resp = client.post(
                "/api/agents/register",
                json={"hostname": "test", "os_type": "Linux"},
                content_type="application/json",
            )
            assert resp.status_code == 400
            assert "mac_address" in resp.get_json().get("error", "")

    def test_heartbeat_no_token_returns_401(self):
        mock_db = MagicMock()
        mock_db.initialize.return_value = None
        mock_db.pool = MagicMock()

        app = self._make_app(mock_db)
        app.config["TESTING"] = True

        with app.test_client() as client:
            resp = client.post(
                "/api/agents/heartbeat",
                json={"idle_seconds": 0},
                content_type="application/json",
            )
            assert resp.status_code == 401

    def test_heartbeat_negative_idle_returns_422(self):
        mock_db = MagicMock()
        mock_db.initialize.return_value = None
        mock_db.pool = MagicMock()

        app = self._make_app(mock_db)
        app.config["TESTING"] = True

        with app.test_client() as client:
            with patch("server.middleware.AuthService.verify_agent_token", return_value=1):
                resp = client.post(
                    "/api/agents/heartbeat",
                    json={"idle_seconds": -5},
                    content_type="application/json",
                    headers={"Authorization": "Bearer fake-token"},
                )
                assert resp.status_code == 422
