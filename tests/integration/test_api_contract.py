"""API contract tests: Validate request/response schemas and status codes.

Ensures all Brain API endpoints return expected structure and handle
edge cases (missing fields, invalid types, boundary values) correctly.
"""

import asyncio
import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

import src.api.brain_api as brain_api


# ── Fake dependencies ────────────────────────────────────────────

class _FakeRequest:
    class _Client:
        host = "127.0.0.1"
    client = _Client()


class _FakeBrain:
    def __init__(self):
        self.skill_tracker = MagicMock()
        self.skill_tracker.get_skill_recommendation.return_value = {
            "recommendation": "run_python", "confidence": 0.85, "reason": "test",
        }

    def search(self, query):
        return [{"title": "test", "content": "test content"}]


class _FakeExecutor:
    def __init__(self):
        self.history = []
        self.execution_mode = "FULL_AUTO"

    def get_stats(self):
        return {"total": 0, "success": 0, "failed": 0, "success_rate": 0.0}


@pytest.fixture(autouse=True)
def _reset():
    try:
        from core.rate_limiter import reset
        reset()
    except ImportError:
        pass
    yield
    try:
        from core.rate_limiter import reset
        reset()
    except ImportError:
        pass


# ── Pydantic model validation ───────────────────────────────────

class TestRequestModelContracts:
    """Verify Pydantic models enforce field constraints."""

    # LearnRequest
    def test_learn_request_valid(self):
        req = brain_api.LearnRequest(
            source="web", type="article", title="Test",
            content="Some content", relevance=0.8, tags=["python"],
        )
        assert req.source == "web"
        assert req.relevance == 0.8

    def test_learn_request_source_too_long(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="x" * 201, type="t", title="T", content="c",
            )

    def test_learn_request_content_too_long(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="s", type="t", title="T", content="x" * 50_001,
            )

    def test_learn_request_empty_source(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="", type="t", title="T", content="c",
            )

    def test_learn_request_relevance_min(self):
        req = brain_api.LearnRequest(
            source="s", type="t", title="T", content="c", relevance=0.0,
        )
        assert req.relevance == 0.0

    def test_learn_request_relevance_max(self):
        req = brain_api.LearnRequest(
            source="s", type="t", title="T", content="c", relevance=1.0,
        )
        assert req.relevance == 1.0

    def test_learn_request_relevance_over(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="s", type="t", title="T", content="c", relevance=1.1,
            )

    def test_learn_request_relevance_under(self):
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="s", type="t", title="T", content="c", relevance=-0.1,
            )

    def test_learn_request_tags_truncated(self):
        long_tag = "a" * 200
        req = brain_api.LearnRequest(
            source="s", type="t", title="T", content="c", tags=[long_tag],
        )
        assert len(req.tags[0]) == 100

    # SearchRequest
    def test_search_request_valid(self):
        req = brain_api.SearchRequest(query="python", limit=50)
        assert req.limit == 50

    def test_search_request_limit_too_high(self):
        with pytest.raises(ValidationError):
            brain_api.SearchRequest(query="q", limit=101)

    def test_search_request_limit_zero(self):
        with pytest.raises(ValidationError):
            brain_api.SearchRequest(query="q", limit=0)

    def test_search_request_empty_query(self):
        with pytest.raises(ValidationError):
            brain_api.SearchRequest(query="", limit=10)

    # ExecuteRequest
    def test_execute_request_valid(self):
        req = brain_api.ExecuteRequest(
            task="do something", max_cycles=5, verification_required=True,
        )
        assert req.max_cycles == 5

    def test_execute_request_cycles_too_high(self):
        with pytest.raises(ValidationError):
            brain_api.ExecuteRequest(task="t", max_cycles=101)

    def test_execute_request_cycles_zero(self):
        with pytest.raises(ValidationError):
            brain_api.ExecuteRequest(task="t", max_cycles=0)

    def test_execute_request_task_too_long(self):
        with pytest.raises(ValidationError):
            brain_api.ExecuteRequest(task="x" * 5001, max_cycles=1)

    # FeedbackRequest
    def test_feedback_request_valid(self):
        req = brain_api.FeedbackRequest(content="good", is_positive=True)
        assert req.is_positive is True

    def test_feedback_request_empty_content(self):
        with pytest.raises(ValidationError):
            brain_api.FeedbackRequest(content="", is_positive=True)

    def test_feedback_request_content_too_long(self):
        with pytest.raises(ValidationError):
            brain_api.FeedbackRequest(content="x" * 10_001, is_positive=True)

    # TaskExecutionRequest
    def test_task_request_valid(self):
        req = brain_api.TaskExecutionRequest(
            task_name="deploy", duration_ms=500.0, success=True,
        )
        assert req.task_name == "deploy"

    def test_task_request_negative_duration(self):
        with pytest.raises(ValidationError):
            brain_api.TaskExecutionRequest(
                task_name="t", duration_ms=-1.0, success=True,
            )


# ── Endpoint response format contracts ───────────────────────────

class TestEndpointResponseFormats:
    """Verify each endpoint returns the expected JSON structure."""

    def test_status_response_shape(self, monkeypatch):
        monkeypatch.setattr(brain_api, "brain_stats", lambda: {"running": True})
        result = asyncio.run(brain_api.get_status())
        assert "status" in result
        assert "stats" in result
        assert result["status"] in ("running", "stopped")

    def test_health_response_shape(self, monkeypatch):
        monkeypatch.setattr(brain_api, "get_brain", lambda: _FakeBrain())
        monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
        result = asyncio.run(brain_api.health_check())
        assert "status" in result
        assert "checks" in result
        assert "timestamp" in result
        assert result["status"] in ("healthy", "degraded")

    def test_safety_response_shape(self, monkeypatch):
        monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
        result = asyncio.run(brain_api.get_safety())
        assert "execution_mode" in result
        assert "policy_blocked_recent" in result
        assert "recent_actions" in result

    def test_trust_metrics_response_shape(self, monkeypatch):
        monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
        result = asyncio.run(brain_api.get_trust_metrics())
        assert "sample_size" in result
        assert "objective_success_rate" in result
        assert "policy_block_rate" in result
        assert "failure_rate" in result
        assert "generated_at" in result
        # Rates should be 0-1 floats
        assert 0 <= result["objective_success_rate"] <= 1
        assert 0 <= result["policy_block_rate"] <= 1
        assert 0 <= result["failure_rate"] <= 1

    def test_skill_recommendation_response_shape(self, monkeypatch):
        monkeypatch.setattr(brain_api, "get_brain", lambda: _FakeBrain())
        result = asyncio.run(brain_api.skill_recommendation("code_review"))
        assert "recommendation" in result
        assert "confidence" in result

    def test_metrics_response_shape(self):
        result = asyncio.run(brain_api.get_metrics())
        assert "timestamp" in result
        assert "endpoints" in result
        # Timestamp should be ISO format
        datetime.fromisoformat(result["timestamp"])

    def test_events_response_shape(self):
        result = asyncio.run(brain_api.get_events(limit=50, event_type=None))
        assert "events" in result
        assert isinstance(result["events"], list)

    def test_self_diagnostic_response_shape(self, monkeypatch):
        monkeypatch.setattr(brain_api, "get_executor", lambda: _FakeExecutor())
        result = asyncio.run(brain_api.self_diagnostic())
        assert "timestamp" in result
        assert "issues" in result
        assert "score" in result
        assert "verdict" in result
        assert isinstance(result["issues"], list)
        assert 0 <= result["score"] <= 100
        assert result["verdict"] in ("excellent", "good", "needs_attention", "critical")


# ── Request metrics tracking ─────────────────────────────────────

class TestRequestMetricsContract:
    def test_metrics_recording(self):
        metrics = brain_api._RequestMetrics()
        metrics.record("/api/nexus/learn", "POST", 200, 15.5)
        metrics.record("/api/nexus/learn", "POST", 200, 25.3)
        metrics.record("/api/nexus/learn", "POST", 429, 1.2)

        snap = metrics.snapshot()
        key = "POST /api/nexus/learn"
        assert key in snap
        assert snap[key]["count"] == 3
        assert snap[key]["errors"] == 1
        assert snap[key]["min_ms"] <= snap[key]["avg_ms"] <= snap[key]["max_ms"]

    def test_empty_metrics(self):
        metrics = brain_api._RequestMetrics()
        snap = metrics.snapshot()
        assert snap == {}


# ── Backup/restore contract ──────────────────────────────────────

class TestBackupRestoreContract:
    def test_restore_invalid_name_400(self):
        """Invalid backup name should return 400."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(brain_api.restore_backup("malicious_name.zip"))
        assert exc_info.value.status_code == 400

    def test_restore_valid_name_format(self):
        """Valid name format but nonexistent → 500 or error dict from module."""
        from fastapi import HTTPException
        try:
            result = asyncio.run(brain_api.restore_backup("nexus_backup_20260101.tar.gz"))
            # If no exception, module returned an error dict
            assert "error" in result or "success" in result
        except HTTPException as exc:
            assert exc.status_code == 500
