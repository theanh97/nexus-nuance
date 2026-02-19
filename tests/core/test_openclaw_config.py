"""Tests for the centralized OpenClaw configuration module.

Validates environment-based config loading, defaults, type safety,
serialization, and singleton behavior.
"""

import os
import pytest

from src.core.openclaw_config import (
    OpenClawConfig,
    OpenClawExecutionConfig,
    OpenClawBrowserConfig,
    OpenClawSelfHealConfig,
    OpenClawAttachConfig,
    OpenClawRuntimeGuardConfig,
    OpenClawCircuitBreakerConfig,
    OpenClawQueueConfig,
    OpenClawCLIConfig,
    OpenClawPolicyConfig,
    OpenClawFlowConfig,
    get_openclaw_config,
    reload_config,
    _env_bool,
    _env_int,
    _env_float,
    _env_set,
)


class TestEnvHelpers:
    """Test environment variable parsing helpers."""

    def test_env_bool_true_values(self, monkeypatch):
        for val in ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"]:
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL") is True

    def test_env_bool_false_values(self, monkeypatch):
        for val in ["0", "false", "no", "off", "random"]:
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL") is False

    def test_env_bool_missing_uses_default(self, monkeypatch):
        monkeypatch.delenv("TEST_BOOL_MISS", raising=False)
        assert _env_bool("TEST_BOOL_MISS", True) is True
        assert _env_bool("TEST_BOOL_MISS", False) is False

    def test_env_int_with_minimum(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "3")
        assert _env_int("TEST_INT", 10, minimum=5) == 5  # clamped to minimum

        monkeypatch.setenv("TEST_INT", "20")
        assert _env_int("TEST_INT", 10, minimum=5) == 20

    def test_env_int_default(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_MISS", raising=False)
        assert _env_int("TEST_INT_MISS", 42) == 42

    def test_env_float_with_minimum(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "0.5")
        assert _env_float("TEST_FLOAT", 1.0, minimum=1.0) == 1.0

    def test_env_set(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "a, b, c, d")
        result = _env_set("TEST_SET", "")
        assert result == frozenset({"a", "b", "c", "d"})

    def test_env_set_default(self, monkeypatch):
        monkeypatch.delenv("TEST_SET_MISS", raising=False)
        result = _env_set("TEST_SET_MISS", "x,y")
        assert result == frozenset({"x", "y"})


class TestExecutionConfig:
    def test_default_mode_headless(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_EXECUTION_MODE", raising=False)
        cfg = OpenClawExecutionConfig.from_env()
        assert cfg.mode == "headless"
        assert cfg.browser_mode_enabled is False
        assert cfg.flow_mode_enabled is False

    def test_browser_mode(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "browser")
        cfg = OpenClawExecutionConfig.from_env()
        assert cfg.mode == "browser"
        assert cfg.browser_mode_enabled is True
        assert cfg.flow_mode_enabled is True

    def test_hybrid_mode(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "hybrid")
        cfg = OpenClawExecutionConfig.from_env()
        assert cfg.mode == "hybrid"
        assert cfg.browser_mode_enabled is True
        assert cfg.flow_mode_enabled is False

    def test_invalid_mode_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_EXECUTION_MODE", "invalid")
        cfg = OpenClawExecutionConfig.from_env()
        assert cfg.mode == "headless"


class TestCircuitBreakerConfig:
    def test_defaults(self, monkeypatch):
        for key in ["OPENCLAW_CIRCUIT_TRIGGER_COUNT", "OPENCLAW_CIRCUIT_OPEN_SEC",
                     "OPENCLAW_CIRCUIT_CLASS_BLOCK_SEC", "OPENCLAW_CIRCUIT_ESCALATE_AFTER"]:
            monkeypatch.delenv(key, raising=False)
        cfg = OpenClawCircuitBreakerConfig.from_env()
        assert cfg.trigger_count == 3
        assert cfg.open_sec == 60
        assert cfg.class_block_sec == 300
        assert cfg.escalate_after == 2

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CIRCUIT_TRIGGER_COUNT", "5")
        monkeypatch.setenv("OPENCLAW_CIRCUIT_OPEN_SEC", "120")
        cfg = OpenClawCircuitBreakerConfig.from_env()
        assert cfg.trigger_count == 5
        assert cfg.open_sec == 120

    def test_minimum_enforcement(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CIRCUIT_TRIGGER_COUNT", "0")
        cfg = OpenClawCircuitBreakerConfig.from_env()
        assert cfg.trigger_count == 2  # minimum is 2


class TestQueueConfig:
    def test_defaults(self, monkeypatch):
        for key in ["OPENCLAW_QUEUE_ENABLED", "OPENCLAW_SESSION_QUEUE_MAX"]:
            monkeypatch.delenv(key, raising=False)
        cfg = OpenClawQueueConfig.from_env()
        assert cfg.enabled is True
        assert cfg.session_max == 80
        assert cfg.manual_hold_sec == 30

    def test_wal_path_exists(self):
        cfg = OpenClawQueueConfig.from_env()
        assert "openclaw_commands_wal.jsonl" in cfg.wal_path


class TestRuntimeGuardConfig:
    def test_defaults(self, monkeypatch):
        for key in ["OPENCLAW_FAIL_FAST_ON_ATTACH_STREAK",
                     "OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD",
                     "OPENCLAW_RUNTIME_DISABLE_SEC"]:
            monkeypatch.delenv(key, raising=False)
        cfg = OpenClawRuntimeGuardConfig.from_env()
        assert cfg.fail_fast_on_streak is True
        assert cfg.streak_threshold == 2
        assert cfg.disable_sec == 600


class TestPolicyConfig:
    def test_defaults(self, monkeypatch):
        for key in ["OPENCLAW_POLICY_MOSTLY_AUTO", "API_RATE_LIMIT_OPENCLAW_PER_MIN"]:
            monkeypatch.delenv(key, raising=False)
        cfg = OpenClawPolicyConfig.from_env()
        assert cfg.mostly_auto is True
        assert cfg.rate_limit_per_min == 120


class TestSelfHealConfig:
    def test_disabled_without_extension(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_EXTENSION_AUTOMATION_ENABLED", "false")
        cfg = OpenClawSelfHealConfig.from_env(ext_enabled=False)
        assert cfg.enabled is False
        assert cfg.bootstrap is False


class TestAttachConfig:
    def test_disabled_without_extension(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_EXTENSION_AUTOMATION_ENABLED", "false")
        exec_cfg = OpenClawExecutionConfig(mode="browser", browser_mode_enabled=True, flow_mode_enabled=True)
        cfg = OpenClawAttachConfig.from_env(exec_cfg, ext_enabled=False)
        assert cfg.enabled is False


class TestMasterConfig:
    def test_load_creates_all_sections(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_EXECUTION_MODE", raising=False)
        config = OpenClawConfig.load()
        assert config.execution.mode == "headless"
        assert config.circuit_breaker.trigger_count >= 2
        assert config.queue.enabled is True
        assert config.cli.max_retries >= 1

    def test_to_dict_serializable(self):
        config = OpenClawConfig.load()
        d = config.to_dict()
        assert "execution" in d
        assert "circuit_breaker" in d
        assert "queue" in d
        # Should be JSON-serializable
        import json
        json.dumps(d)  # Should not raise

    def test_summary(self):
        config = OpenClawConfig.load()
        summary = config.summary()
        assert "mode" in summary
        assert "queue_enabled" in summary
        assert "rate_limit_rpm" in summary


class TestSingleton:
    def test_get_returns_same_instance(self, monkeypatch):
        import src.core.openclaw_config as mod
        mod._config = None
        c1 = get_openclaw_config()
        c2 = get_openclaw_config()
        assert c1 is c2

    def test_reload_creates_new_instance(self, monkeypatch):
        import src.core.openclaw_config as mod
        mod._config = None
        c1 = get_openclaw_config()
        c2 = reload_config()
        assert c1 is not c2
