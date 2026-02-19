import json

from src.brain import action_executor as ae


def test_action_history_load_parses_status_strings(monkeypatch, tmp_path):
    data_dir = tmp_path / "data" / "brain"
    data_dir.mkdir(parents=True)
    history_file = data_dir / "action_history.jsonl"
    sample = {
        "action_id": "a1",
        "action_type": "read_file",
        "status": "success",
        "output": "ok",
        "error": None,
        "data": {},
        "started_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:00:01",
        "duration_ms": 1000.0,
        "objective_success": True,
        "failure_code": None,
        "policy_blocked": False,
        "verification": {},
    }
    history_file.write_text(json.dumps(sample) + "\n", encoding="utf-8")

    monkeypatch.setattr(ae, "DATA_DIR", data_dir)
    monkeypatch.setattr(ae, "WORKSPACE_DIR", tmp_path / "workspace")

    executor = ae.ActionExecutor()
    assert len(executor.history) == 1
    assert executor.history[0].status == ae.ActionStatus.SUCCESS
