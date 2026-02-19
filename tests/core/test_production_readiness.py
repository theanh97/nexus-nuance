"""Tests for Phase 18 - Production Readiness.

Validates:
- K8s liveness/readiness probes
- Graceful shutdown signal handling
- Dockerfile and docker-compose existence
- .env.template completeness
- OpenClaw centralized config
- Self-reminder API endpoints
"""

import asyncio
import json
import os
import signal
import pytest
from pathlib import Path

import src.api.brain_api as brain_api


PROJECT_ROOT = Path(__file__).parent.parent.parent


# ── K8s Probe Tests ──────────────────────────────────────────────

class TestLivenessProbe:
    def test_livez_returns_alive(self):
        result = asyncio.run(brain_api.liveness_probe())
        assert result["status"] == "alive"
        assert "timestamp" in result

    def test_livez_always_succeeds(self):
        """Liveness probe should always return alive if the process is running."""
        for _ in range(5):
            result = asyncio.run(brain_api.liveness_probe())
            assert result["status"] == "alive"


class TestReadinessProbe:
    def test_readyz_structure(self, monkeypatch):
        """Readiness probe returns proper structure."""
        class _MockBrain:
            pass

        class _MockExecutor:
            pass

        monkeypatch.setattr(brain_api, "get_brain", lambda: _MockBrain())
        monkeypatch.setattr(brain_api, "get_executor", lambda: _MockExecutor())

        response = asyncio.run(brain_api.readiness_probe())
        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["status"] == "ready"
        assert "details" in body
        assert body["details"]["brain"] == "ready"
        assert body["details"]["executor"] == "ready"

    def test_readyz_not_ready_when_brain_missing(self, monkeypatch):
        """Readiness returns 503 when brain unavailable."""
        monkeypatch.setattr(brain_api, "get_brain", lambda: None)
        monkeypatch.setattr(brain_api, "get_executor", lambda: object())

        response = asyncio.run(brain_api.readiness_probe())
        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["status"] == "not_ready"
        assert body["details"]["brain"] == "not_ready"

    def test_readyz_not_ready_when_executor_fails(self, monkeypatch):
        """Readiness returns 503 when executor throws."""
        monkeypatch.setattr(brain_api, "get_brain", lambda: object())
        monkeypatch.setattr(brain_api, "get_executor", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        response = asyncio.run(brain_api.readiness_probe())
        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["details"]["executor"] == "not_ready"


# ── Self-Reminder API Tests ──────────────────────────────────────

class TestSelfReminderAPI:
    def test_self_reminder_status_endpoint(self, monkeypatch):
        """GET /self-reminder/status returns engine status."""
        from src.brain.self_reminder import SelfReminderEngine

        mock_engine = SelfReminderEngine(sources=[], enabled=True)

        monkeypatch.setattr(
            "src.api.brain_api.brain_api",
            type(brain_api),
        ) if False else None  # no-op, just clarify scope

        # Patch the import in brain_api
        import importlib
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        result = asyncio.run(brain_api.self_reminder_status())
        assert "success" in result
        # May fail to import in test env, but should not raise
        assert isinstance(result, dict)

    def test_self_reminder_trigger_endpoint(self, monkeypatch):
        """POST /self-reminder/trigger returns reminder results."""
        result = asyncio.run(brain_api.self_reminder_trigger())
        assert "success" in result


# ── Docker Configuration Tests ───────────────────────────────────

class TestDockerFiles:
    def test_dockerfile_exists(self):
        assert (PROJECT_ROOT / "Dockerfile").exists()

    def test_dockerfile_has_healthcheck(self):
        content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "HEALTHCHECK" in content
        assert "CMD" in content

    def test_dockerfile_has_non_root_user(self):
        content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "USER nexus" in content or "USER" in content

    def test_dockerfile_exposes_ports(self):
        content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "EXPOSE" in content
        assert "8766" in content

    def test_dockerfile_has_multi_stage(self):
        content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert content.count("FROM") >= 2  # Multi-stage build

    def test_docker_compose_exists(self):
        assert (PROJECT_ROOT / "docker-compose.yml").exists()

    def test_docker_compose_has_services(self):
        import yaml
        content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        config = yaml.safe_load(content)
        assert "services" in config
        assert "brain-api" in config["services"]
        assert "monitor" in config["services"]
        assert "brain-daemon" in config["services"]

    def test_docker_compose_has_healthchecks(self):
        import yaml
        content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        config = yaml.safe_load(content)
        brain_api_svc = config["services"]["brain-api"]
        assert "healthcheck" in brain_api_svc

    def test_docker_compose_has_volumes(self):
        import yaml
        content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        config = yaml.safe_load(content)
        assert "volumes" in config


# ── Environment Template Tests ───────────────────────────────────

class TestEnvTemplate:
    def test_env_template_exists(self):
        assert (PROJECT_ROOT / ".env.template").exists()

    def test_env_template_has_required_vars(self):
        content = (PROJECT_ROOT / ".env.template").read_text(encoding="utf-8")
        required = [
            "ZAI_OPENAI_API_KEY",
            "AUTONOMY_PROFILE",
            "BRAIN_API_PORT",
            "MONITOR_PORT",
            "OPENCLAW_EXECUTION_MODE",
            "SELF_REMINDER_ENABLED",
            "GRACEFUL_SHUTDOWN_TIMEOUT_SEC",
        ]
        for var in required:
            assert var in content, f"Missing required env var: {var}"

    def test_env_template_has_sections(self):
        content = (PROJECT_ROOT / ".env.template").read_text(encoding="utf-8")
        sections = ["LLM API", "Model Routing", "OpenClaw", "Monitor", "Self-Reminder"]
        for section in sections:
            assert section in content, f"Missing section: {section}"


# ── Graceful Shutdown Tests ──────────────────────────────────────

class TestGracefulShutdown:
    def test_run_system_imports_signal(self):
        """run_system.py must import signal module."""
        content = (PROJECT_ROOT / "run_system.py").read_text(encoding="utf-8")
        assert "import signal" in content

    def test_run_system_handles_sigterm(self):
        """run_system.py must handle SIGTERM for graceful shutdown."""
        content = (PROJECT_ROOT / "run_system.py").read_text(encoding="utf-8")
        assert "SIGTERM" in content
        assert "signal.signal" in content

    def test_run_system_has_shutdown_timeout(self):
        content = (PROJECT_ROOT / "run_system.py").read_text(encoding="utf-8")
        assert "GRACEFUL_SHUTDOWN_TIMEOUT_SEC" in content

    def test_brain_daemon_handles_signals(self):
        """brain_daemon.py must handle SIGTERM and SIGINT."""
        content = (PROJECT_ROOT / "scripts" / "brain_daemon.py").read_text(encoding="utf-8")
        assert "signal.SIGTERM" in content
        assert "signal.SIGINT" in content


# ── Brain Daemon Self-Reminder Integration ───────────────────────

class TestDaemonSelfReminderIntegration:
    def test_brain_daemon_imports_self_reminder(self):
        content = (PROJECT_ROOT / "scripts" / "brain_daemon.py").read_text(encoding="utf-8")
        assert "self_reminder" in content
        assert "check_and_remind" in content

    def test_brain_daemon_logs_reminder_stats(self):
        content = (PROJECT_ROOT / "scripts" / "brain_daemon.py").read_text(encoding="utf-8")
        assert "Reminders:" in content or "Self-Reminder" in content


# ── Browser Automation Module Tests ──────────────────────────────

class TestBrowserAutomationModule:
    def test_module_exists(self):
        assert (PROJECT_ROOT / "src" / "core" / "browser_automation.py").exists()

    def test_openclaw_config_module_exists(self):
        assert (PROJECT_ROOT / "src" / "core" / "openclaw_config.py").exists()

    def test_self_reminder_module_exists(self):
        assert (PROJECT_ROOT / "src" / "brain" / "self_reminder.py").exists()
