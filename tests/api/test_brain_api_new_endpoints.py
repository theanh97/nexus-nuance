import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import src.api.brain_api as brain_api


class _FakeAgent:
    def execute_autonomously(self, task: str, max_cycles: int = 10):
        return {
            "task": task,
            "success": True,
            "cycles": [],
            "actions_taken": [],
            "grounded": True,
            "completion_reason": "criteria_met",
            "done_criteria_met": ["x"],
            "done_criteria_missed": [],
        }


class _FakeExecutor:
    def __init__(self):
        self.execution_mode = "FULL_AUTO"
        self.history = [
            SimpleNamespace(status=SimpleNamespace(value="success"), objective_success=True, policy_blocked=False),
            SimpleNamespace(status=SimpleNamespace(value="failed"), objective_success=False, policy_blocked=True),
        ]


class _FakeRequest:
    """Minimal Request-like object for endpoint tests."""
    class _Client:
        host = "127.0.0.1"
    client = _Client()


def test_execute_endpoint_success(monkeypatch):
    monkeypatch.setattr(brain_api, "get_autonomous_agent", lambda: _FakeAgent())
    req = brain_api.ExecuteRequest(task="hello", max_cycles=2, verification_required=True)
    result = asyncio.run(brain_api.execute_task(req, _FakeRequest()))
    assert result["success"] is True
    assert result["result"]["completion_reason"] == "criteria_met"


def test_safety_and_trust_metrics(monkeypatch):
    monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())

    safety = asyncio.run(brain_api.get_safety())
    trust = asyncio.run(brain_api.get_trust_metrics())

    assert safety["execution_mode"] == "FULL_AUTO"
    assert safety["policy_blocked_recent"] == 1
    assert trust["sample_size"] == 2
    assert trust["policy_block_rate"] == 0.5


def test_health_check(monkeypatch):
    monkeypatch.setattr(brain_api, "get_brain", lambda: True)
    monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
    result = asyncio.run(brain_api.health_check())
    assert "status" in result
    assert "checks" in result
    assert result["checks"]["brain"] == "ok"
    assert result["checks"]["executor"] == "ok"


def test_self_diagnostic(monkeypatch):
    monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
    monkeypatch.setattr(brain_api, "brain_stats", lambda: {"running": True, "memory": {"total_knowledge": 50}})
    result = asyncio.run(brain_api.self_diagnostic())
    assert "score" in result
    assert "verdict" in result
    assert "issues" in result
    assert isinstance(result["issues"], list)
    assert result["score"] >= 0
    assert result["verdict"] in ("excellent", "good", "needs_attention", "critical")


# ==================== INPUT VALIDATION ====================

class TestInputValidation:
    def test_learn_request_rejects_empty_content(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(source="s", type="t", title="T", content="")

    def test_learn_request_rejects_oversized_content(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(source="s", type="t", title="T", content="x" * 50_001)

    def test_learn_request_valid(self):
        req = brain_api.LearnRequest(source="web", type="article", title="Test", content="Hello")
        assert req.source == "web"
        assert req.relevance == 0.7

    def test_learn_request_relevance_bounds(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(source="s", type="t", title="T", content="c", relevance=1.5)

    def test_search_request_rejects_empty_query(self):
        with pytest.raises(ValidationError):
            brain_api.SearchRequest(query="")

    def test_search_request_limit_bounds(self):
        with pytest.raises(ValidationError):
            brain_api.SearchRequest(query="test", limit=0)
        with pytest.raises(ValidationError):
            brain_api.SearchRequest(query="test", limit=101)

    def test_execute_request_max_cycles_bounds(self):
        with pytest.raises(ValidationError):
            brain_api.ExecuteRequest(task="do something", max_cycles=0)
        with pytest.raises(ValidationError):
            brain_api.ExecuteRequest(task="do something", max_cycles=101)

    def test_feedback_request_rejects_empty(self):
        with pytest.raises(ValidationError):
            brain_api.FeedbackRequest(content="", is_positive=True)

    def test_tags_truncated(self):
        long_tag = "x" * 200
        req = brain_api.LearnRequest(source="s", type="t", title="T", content="c", tags=[long_tag])
        assert len(req.tags[0]) == 100
