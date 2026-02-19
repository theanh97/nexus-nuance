"""Integration tests: Brain API → AutonomousAgent → ActionExecutor full cycle.

Tests the complete request flow from API endpoint through agent execution
down to the action executor, verifying the full integration chain.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import src.api.brain_api as brain_api
import src.brain.action_executor as ae_mod
import src.brain.autonomous_agent as agent_mod
from src.brain.action_executor import ActionExecutor, ActionResult, ActionStatus


# ── Fakes ────────────────────────────────────────────────────────

class _FakeRequest:
    """Minimal Request-like object for endpoint tests."""

    class _Client:
        host = "127.0.0.1"

    client = _Client()


class _FakeAgent:
    """Agent that delegates to a real ActionExecutor in tmp workspace."""

    def __init__(self, executor: ActionExecutor):
        self.executor = executor
        self.calls = []

    def execute_autonomously(self, task: str, max_cycles: int = 10):
        self.calls.append({"task": task, "max_cycles": max_cycles})
        # Simulate a simple write+read cycle through the real executor
        w = self.executor.execute("write_file", {
            "path": "workspace/agent_output.txt",
            "content": f"Result of: {task}",
        })
        return {
            "task": task,
            "success": w.status == ActionStatus.SUCCESS,
            "cycles": [{"action": "write_file", "result": w.output}],
            "actions_taken": ["write_file"],
            "grounded": True,
            "completion_reason": "criteria_met",
            "done_criteria_met": ["output_written"],
            "done_criteria_missed": [],
        }


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
def executor(tmp_path, monkeypatch):
    """Isolated ActionExecutor with workspace in tmp."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    data = tmp_path / "data" / "brain"
    data.mkdir(parents=True)

    monkeypatch.setattr(ae_mod, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(ae_mod, "DATA_DIR", data)
    monkeypatch.setattr(ae_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ae_mod, "_LLM_AVAILABLE", False)

    ex = ActionExecutor()
    ex.workspace = ws
    ex.history_file = data / "action_history.jsonl"
    ex.allowed_roots = [ws, data, tmp_path / "src"]
    return ex


@pytest.fixture()
def fake_agent(executor):
    return _FakeAgent(executor)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear rate limiter state before each test."""
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


# ── API → Agent → Executor full cycle ───────────────────────────

class TestAPIToExecutorCycle:
    """Verify the full POST /execute pipeline."""

    def test_execute_endpoint_calls_agent_and_executor(self, monkeypatch, fake_agent):
        """API → Agent → Executor → ActionResult → API response."""
        monkeypatch.setattr(brain_api, "get_autonomous_agent", lambda: fake_agent)
        req = brain_api.ExecuteRequest(task="write test output", max_cycles=3, verification_required=False)
        result = asyncio.run(brain_api.execute_task(req, _FakeRequest()))

        assert result["success"] is True
        assert result["result"]["task"] == "write test output"
        assert fake_agent.calls[0]["max_cycles"] == 3

    def test_execute_verification_failure_returns_409(self, monkeypatch):
        """When verification_required=True and agent fails → 409."""
        class _FailAgent:
            def execute_autonomously(self, task, max_cycles=10):
                return {"success": False, "error": "could not complete"}

        monkeypatch.setattr(brain_api, "get_autonomous_agent", lambda: _FailAgent())
        req = brain_api.ExecuteRequest(task="impossible task", max_cycles=1, verification_required=True)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(brain_api.execute_task(req, _FakeRequest()))
        assert exc_info.value.status_code == 409

    def test_execute_no_verification_returns_failure(self, monkeypatch):
        """When verification_required=False and agent fails → still returns result."""
        class _FailAgent:
            def execute_autonomously(self, task, max_cycles=10):
                return {"success": False, "error": "not done"}

        monkeypatch.setattr(brain_api, "get_autonomous_agent", lambda: _FailAgent())
        req = brain_api.ExecuteRequest(task="test", max_cycles=1, verification_required=False)
        result = asyncio.run(brain_api.execute_task(req, _FakeRequest()))
        assert result["success"] is False


# ── API → Brain learning cycle ───────────────────────────────────

class TestAPIToBrainLearning:
    """POST /learn → Brain storage → GET /search retrieval."""

    def test_learn_then_search(self, monkeypatch):
        """Learn knowledge via API, then search and find it."""
        learned_items = {}

        def mock_learn(source, type, title, content, url=None, relevance=0.7, tags=None):
            item = MagicMock()
            item.id = "test-item-1"
            learned_items[item.id] = {
                "source": source, "type": type, "title": title,
                "content": content, "tags": tags or [],
            }
            return item

        class _FakeBrain:
            def search(self, query):
                return [v for v in learned_items.values()
                        if query.lower() in v["content"].lower()]

        monkeypatch.setattr(brain_api, "learn", mock_learn)
        monkeypatch.setattr(brain_api, "get_brain", lambda: _FakeBrain())

        # Step 1: Learn
        learn_req = brain_api.LearnRequest(
            source="test", type="article", title="Python Tips",
            content="Python async/await is powerful for I/O-bound tasks",
            relevance=0.9, tags=["python", "async"],
        )
        learn_result = asyncio.run(brain_api.learn_knowledge(learn_req, _FakeRequest()))
        assert learn_result["success"] is True
        assert learn_result["id"] == "test-item-1"

        # Step 2: Search
        search_req = brain_api.SearchRequest(query="python async", limit=10)
        search_result = asyncio.run(brain_api.search_knowledge(search_req, _FakeRequest()))
        assert search_result["count"] >= 1
        assert "python" in search_result["results"][0]["content"].lower()

    def test_learn_invalid_input_rejected(self):
        """Empty content should be rejected by Pydantic validation."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="", type="article", title="Bad",
                content="x", relevance=0.5,
            )

    def test_learn_tag_truncation(self):
        """Tags longer than 100 chars get truncated."""
        req = brain_api.LearnRequest(
            source="test", type="article", title="T",
            content="content", tags=["a" * 200],
        )
        assert len(req.tags[0]) == 100

    def test_learn_relevance_bounds(self):
        """Relevance outside 0-1 should be rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            brain_api.LearnRequest(
                source="s", type="t", title="T",
                content="c", relevance=1.5,
            )


# ── API → Executor stats and safety ─────────────────────────────

class TestAPIToExecutorSafety:
    """GET /safety and /trust-metrics read from executor.history."""

    def test_safety_endpoint_reads_executor(self, monkeypatch, executor):
        """GET /safety uses executor.history to count policy blocks."""
        monkeypatch.setattr(brain_api, "get_executor", lambda: executor)

        # Execute a few actions to build history
        executor.execute("write_file", {"path": "workspace/a.txt", "content": "hi"})
        executor.execute("write_file", {"path": "workspace/b.txt", "content": "bye"})

        result = asyncio.run(brain_api.get_safety())
        assert result["execution_mode"] == executor.execution_mode
        assert result["recent_actions"] >= 2
        assert "policy_blocked_recent" in result

    def test_trust_metrics_accurate(self, monkeypatch, executor):
        """GET /trust-metrics reflects real executor history."""
        monkeypatch.setattr(brain_api, "get_executor", lambda: executor)

        # Successful action
        executor.execute("write_file", {"path": "workspace/ok.txt", "content": "data"})
        # Policy-blocked action (try to write outside roots)
        executor.execution_mode = "SAFE"
        executor.execute("read_file", {"path": "/tmp/outside.txt"})
        executor.execution_mode = "FULL_AUTO"

        result = asyncio.run(brain_api.get_trust_metrics())
        assert result["sample_size"] >= 2
        assert "failure_rate" in result
        assert "policy_block_rate" in result


# ── API → Feedback → Task recording ─────────────────────────────

class TestAPIFeedbackAndTask:
    def test_feedback_success(self, monkeypatch):
        called = {}

        def mock_feedback(content, is_positive, context):
            called["content"] = content
            called["is_positive"] = is_positive

        monkeypatch.setattr(brain_api, "feedback", mock_feedback)
        req = brain_api.FeedbackRequest(content="Great job!", is_positive=True)
        result = asyncio.run(brain_api.submit_feedback(req))
        assert result["success"] is True
        assert called["is_positive"] is True

    def test_task_recording(self, monkeypatch):
        recorded = {}

        def mock_task(name, dur, success, details):
            recorded["name"] = name
            recorded["success"] = success

        monkeypatch.setattr(brain_api, "task_executed", mock_task)
        req = brain_api.TaskExecutionRequest(
            task_name="deploy", duration_ms=1500.0, success=True,
        )
        result = asyncio.run(brain_api.record_task(req))
        assert result["success"] is True
        assert recorded["name"] == "deploy"


# ── Executor → History persistence ───────────────────────────────

class TestExecutorHistoryIntegration:
    def test_action_history_persisted_across_operations(self, executor):
        """Multiple actions should all be persisted to JSONL."""
        executor.execute("write_file", {"path": "workspace/one.txt", "content": "1"})
        executor.execute("write_file", {"path": "workspace/two.txt", "content": "2"})
        executor.execute("read_file", {"path": "workspace/one.txt"})
        executor.execute("delete_file", {"path": "workspace/two.txt"})

        assert executor.history_file.exists()
        lines = executor.history_file.read_text().strip().split("\n")
        assert len(lines) == 4

        # Verify JSON validity and action types
        types = [json.loads(line)["action_type"] for line in lines]
        assert types == ["write_file", "write_file", "read_file", "delete_file"]

    def test_stats_reflect_mixed_results(self, executor):
        """Stats correctly count success and failures."""
        executor.execute("write_file", {"path": "workspace/ok.txt", "content": "ok"})
        executor.execute("read_file", {"path": "workspace/nonexistent_xyz.txt"})

        stats = executor.get_stats()
        assert stats["total"] == 2
        assert stats["success"] >= 1
        assert stats["failed"] >= 1
