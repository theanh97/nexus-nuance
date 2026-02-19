import os

from src.brain.action_executor import ActionExecutor, ActionStatus


def test_full_auto_blocks_sensitive_paths(monkeypatch):
    monkeypatch.setenv("NEXUS_EXECUTION_MODE", "FULL_AUTO")
    executor = ActionExecutor()
    result = executor.execute("read_file", {"path": "/etc/hosts"})
    assert result.status == ActionStatus.FAILED
    assert result.policy_blocked is True


def test_safe_mode_blocks_paths_outside_allowed_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_EXECUTION_MODE", "SAFE")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("hello", encoding="utf-8")
    executor = ActionExecutor()
    result = executor.execute("read_file", {"path": str(outside_file)})
    assert result.status == ActionStatus.FAILED
    assert result.policy_blocked is True
