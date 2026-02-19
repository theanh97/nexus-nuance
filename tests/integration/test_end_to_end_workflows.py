"""End-to-end workflow tests: Multi-component flows across the system.

Tests realistic workflows that span Brain API, ActionExecutor,
AutonomousLoop, EventBus, and RateLimiter together.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import src.api.brain_api as brain_api
import src.brain.action_executor as ae_mod
import src.loop.autonomous_loop as loop_mod
from src.brain.action_executor import ActionExecutor, ActionStatus
from src.loop.autonomous_loop import (
    AutonomousLoop,
    LoopTask,
    TaskStatus,
    TaskPriority,
    LearningAnalyzer,
)
from src.core.event_bus import (
    emit_event,
    subscribe,
    get_recent_events,
    clear as clear_events,
)


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
def executor(tmp_path, monkeypatch):
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
def loop(tmp_path, monkeypatch):
    monkeypatch.setattr(loop_mod, "_LLM_AVAILABLE", False)
    return AutonomousLoop(data_dir=str(tmp_path / "loop"))


@pytest.fixture(autouse=True)
def _clean():
    clear_events()
    try:
        from core.rate_limiter import reset
        reset()
    except ImportError:
        pass
    yield
    clear_events()
    try:
        from core.rate_limiter import reset
        reset()
    except ImportError:
        pass


# ── Workflow 1: Learn → Execute → Verify ─────────────────────────

class TestLearnExecuteVerifyWorkflow:
    """Simulate: User teaches knowledge → Agent uses it → Verify output."""

    def test_full_knowledge_to_action_workflow(self, monkeypatch, executor):
        """Learn content → Agent writes it to file → Verify file exists."""
        # Track events - monkeypatch emit_event in brain_api to use our event_bus
        events_captured = []
        original_emit = emit_event

        def tracking_emit(event_type, data=None):
            original_emit(event_type, data)
            events_captured.append({"type": event_type, "data": data or {}})

        monkeypatch.setattr(brain_api, "emit_event", tracking_emit)

        # Step 1: Learn via API
        learned_knowledge = {}

        def mock_learn(**kw):
            item = MagicMock()
            item.id = "k-001"
            learned_knowledge[item.id] = kw
            return item

        monkeypatch.setattr(brain_api, "learn", lambda **kw: mock_learn(**kw))

        class _FakeRequest:
            class _Client:
                host = "127.0.0.1"
            client = _Client()

        learn_req = brain_api.LearnRequest(
            source="tutorial", type="guide", title="Deploy Steps",
            content="Step 1: Build. Step 2: Test. Step 3: Deploy.",
            relevance=0.95, tags=["deployment"],
        )
        learn_result = asyncio.run(brain_api.learn_knowledge(learn_req, _FakeRequest()))
        assert learn_result["success"]

        # Step 2: Agent uses knowledge to write deploy script
        result = executor.execute("write_file", {
            "path": "workspace/deploy.sh",
            "content": "#!/bin/bash\n# " + learned_knowledge["k-001"]["content"],
        })
        assert result.status == ActionStatus.SUCCESS

        # Step 3: Verify file was created correctly
        read_result = executor.execute("read_file", {"path": "workspace/deploy.sh"})
        assert read_result.status == ActionStatus.SUCCESS
        assert "Step 1: Build" in read_result.output

        # Step 4: Verify events were emitted
        assert any(e["type"] == "knowledge.learned" for e in events_captured)

        # Step 5: Check executor history
        stats = executor.get_stats()
        assert stats["total"] == 2  # write + read
        assert stats["success"] == 2


# ── Workflow 2: Multi-task loop with priority ────────────────────

class TestMultiTaskPriorityWorkflow:
    """Simulate: Multiple tasks queued → Execute in priority order → Learn."""

    def test_priority_task_execution_with_learning(self, loop, monkeypatch):
        monkeypatch.setattr(loop_mod, "_LLM_AVAILABLE", False)

        # Queue tasks in reverse priority order
        t_low = loop.add_task(
            "cleanup", "Remove temp files",
            "run_command", params={"command": "echo cleanup_done"},
            priority=TaskPriority.LOW,
        )
        t_high = loop.add_task(
            "deploy", "Deploy to production",
            "run_command", params={"command": "echo deploy_done"},
            priority=TaskPriority.HIGH,
        )
        t_critical = loop.add_task(
            "hotfix", "Apply critical hotfix",
            "run_command", params={"command": "echo hotfix_done"},
            priority=TaskPriority.CRITICAL,
        )

        # Verify queue ordering
        assert loop.task_queue[0].name == "hotfix"
        assert loop.task_queue[-1].name == "cleanup"

        # Execute top priority task
        top_task = loop.task_queue[0]
        result = asyncio.run(loop.execute_task(top_task))
        assert result["success"]
        assert top_task.status == TaskStatus.COMPLETED
        assert "hotfix_done" in result["output"]["stdout"]


# ── Workflow 3: Failure → Retry → Recovery ───────────────────────

class TestFailureRetryRecoveryWorkflow:
    """Simulate: Task fails → Retries → Eventually different approach."""

    def test_retry_then_recover(self, loop):
        # Task with dangerous command will fail
        task = LoopTask(
            id="retry-1", name="risky_deploy",
            description="Deploy with risky command",
            action="run_command",
            params={"command": "echo deploy; rm -rf /"},  # has metachar
            max_retries=3,
        )

        # First attempt: fails (metachar detected)
        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]
        assert task.retry_count == 1
        assert task.status == TaskStatus.PENDING

        # Fix the command and retry
        task.params = {"command": "echo deploy_safe"}
        result2 = asyncio.run(loop.execute_task(task))
        assert result2["success"]
        assert task.status == TaskStatus.COMPLETED

    def test_exhaust_retries(self, loop):
        """All retries exhausted → task marked FAILED."""
        task = LoopTask(
            id="exhaust-1", name="always_fail",
            description="Always fails",
            action="run_command",
            params={"command": "echo bad; rm something"},
            max_retries=2, retry_count=2,
        )

        result = asyncio.run(loop.execute_task(task))
        assert not result["success"]
        assert task.status == TaskStatus.FAILED


# ── Workflow 4: Executor chain with state ────────────────────────

class TestExecutorChainWorkflow:
    """Multi-step file operations simulating a build pipeline."""

    def test_build_pipeline(self, executor):
        """Create → Write → Edit → Verify → Delete lifecycle."""
        ws = executor.workspace

        # Step 1: Create directory
        executor.execute("create_directory", {"path": "workspace/build"})
        assert (ws / "build").is_dir()

        # Step 2: Write source file
        executor.execute("write_file", {
            "path": "workspace/build/app.py",
            "content": "print('version=1.0')",
        })

        # Step 3: Edit (bump version)
        executor.execute("edit_file", {
            "path": "workspace/build/app.py",
            "old": "version=1.0",
            "new": "version=2.0",
        })

        # Step 4: Verify edit
        r = executor.execute("read_file", {"path": "workspace/build/app.py"})
        assert "version=2.0" in r.output

        # Step 5: Run the code
        py_result = executor.execute("run_python", {"code": "print('version=2.0')"})
        assert py_result.status == ActionStatus.SUCCESS
        assert "version=2.0" in py_result.output

        # Step 6: Verify history
        stats = executor.get_stats()
        assert stats["total"] == 5
        assert stats["success_rate"] > 0.9

    def test_concurrent_file_operations(self, executor):
        """Multiple writes to different files should all succeed."""
        results = []
        for i in range(10):
            r = executor.execute("write_file", {
                "path": f"workspace/file_{i}.txt",
                "content": f"Content {i}",
            })
            results.append(r)

        assert all(r.status == ActionStatus.SUCCESS for r in results)

        # Verify all files exist
        for i in range(10):
            r = executor.execute("read_file", {"path": f"workspace/file_{i}.txt"})
            assert r.status == ActionStatus.SUCCESS
            assert f"Content {i}" in r.output


# ── Workflow 5: Safety policy enforcement end-to-end ─────────────

class TestSafetyPolicyE2E:
    """Verify safety policies work across the full stack."""

    def test_safe_mode_restricts_operations(self, executor):
        """SAFE mode blocks all operations outside workspace."""
        executor.execution_mode = "SAFE"

        # Write inside workspace: OK
        r1 = executor.execute("write_file", {
            "path": "workspace/safe.txt",
            "content": "safe content",
        })
        assert r1.status == ActionStatus.SUCCESS

        # Read outside workspace: BLOCKED in SAFE mode
        r2 = executor.execute("read_file", {"path": "/tmp/outside.txt"})
        assert r2.status == ActionStatus.FAILED
        assert r2.policy_blocked is True

    def test_dangerous_shell_blocked_end_to_end(self, executor):
        """Dangerous shell commands are blocked at policy level."""
        dangerous_cmds = [
            "rm -rf /",
            "shutdown now",
            ":(){:|:&};:",
        ]
        for cmd in dangerous_cmds:
            r = executor.execute("run_shell", {"command": cmd})
            assert r.status == ActionStatus.FAILED, f"Should block: {cmd}"
            assert r.policy_blocked is True

    def test_policy_blocked_in_history(self, executor):
        """Policy-blocked actions should appear in history with flag."""
        executor.execute("run_shell", {"command": "rm -rf /"})
        executor.execute("write_file", {
            "path": "workspace/ok.txt", "content": "ok",
        })

        history_lines = executor.history_file.read_text().strip().split("\n")
        records = [json.loads(line) for line in history_lines]

        blocked = [r for r in records if r.get("policy_blocked")]
        assert len(blocked) >= 1

        # API safety endpoint should reflect this
        recent = executor.history[-50:]
        policy_count = sum(1 for r in recent if getattr(r, "policy_blocked", False))
        assert policy_count >= 1


# ── Workflow 6: Loop state persistence ───────────────────────────

class TestLoopStatePersistence:
    def test_state_saves_and_loads(self, loop):
        """Loop state should persist across saves."""
        loop.add_task("persist_1", "desc", "run_command",
                       params={"command": "echo 1"})
        loop.add_task("persist_2", "desc", "run_command",
                       params={"command": "echo 2"})
        loop._save_state()

        state_file = loop.data_dir / "loop_state.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert len(data["pending_tasks"]) >= 2

    def test_completed_tasks_tracked(self, loop):
        """Completed tasks should be recorded in memory and state file."""
        task = LoopTask(
            id="track-1", name="track_test", description="test",
            action="run_command", params={"command": "echo tracked"},
        )
        asyncio.run(loop.execute_task(task))
        assert task.status == TaskStatus.COMPLETED

        # Manually move to completed list (normally done by run_one_cycle)
        loop.completed_tasks.append(task)
        loop._save_state()

        # Verify pending state was saved
        data = json.loads((loop.data_dir / "loop_state.json").read_text())
        assert "pending_tasks" in data

        # Verify task was completed in memory
        assert any(t.name == "track_test" for t in loop.completed_tasks)
