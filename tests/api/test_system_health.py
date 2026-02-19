"""Tests for /api/nexus/system-health endpoint."""

import asyncio
from types import SimpleNamespace

import src.api.brain_api as brain_api


class _FakeExecutor:
    def __init__(self):
        self.execution_mode = "FULL_AUTO"
        self.history = [
            SimpleNamespace(status=SimpleNamespace(value="success"), objective_success=True, policy_blocked=False),
        ]


def test_system_health_returns_all_keys(monkeypatch):
    monkeypatch.setattr(brain_api, "get_brain", lambda: True)
    monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
    result = asyncio.run(brain_api.system_health())
    assert "timestamp" in result
    assert "subsystems" in result
    assert "overall" in result
    assert "issues" in result
    assert result["subsystems"]["brain"] == "ok"


def test_system_health_degraded_when_no_brain(monkeypatch):
    monkeypatch.setattr(brain_api, "get_brain", lambda: None)
    monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
    result = asyncio.run(brain_api.system_health())
    assert result["subsystems"]["brain"] == "unavailable"


def test_system_health_executor_stats(monkeypatch):
    monkeypatch.setattr(brain_api, "get_brain", lambda: True)
    monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
    result = asyncio.run(brain_api.system_health())
    executor = result["subsystems"]["executor"]
    assert executor["total_actions"] == 1
    assert executor["mode"] == "FULL_AUTO"
    assert executor["recent_fail_rate"] == 0.0
