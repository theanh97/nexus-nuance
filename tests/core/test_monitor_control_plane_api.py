from datetime import datetime

import pytest

import monitor.app as monitor_app


def _healthy_token_info() -> monitor_app._OpenClawTokenInfo:
    return monitor_app._OpenClawTokenInfo(
        token="test-token",
        checked_at=datetime.now(),
        source="env",
        health_ok=True,
        health_error=None,
        last_error=None,
    )


@pytest.fixture
def client(monkeypatch):
    monitor_app.app.config["TESTING"] = True
    monkeypatch.setattr(monitor_app, "DASHBOARD_ACCESS_TOKEN", "")
    monkeypatch.setattr(monitor_app, "_autonomy_profile", "balanced")
    monkeypatch.setattr(monitor_app, "_monitor_supervisor_enabled", True)
    monkeypatch.setattr(monitor_app, "_monitor_autopilot_pause_until", None)
    monkeypatch.setattr(monitor_app, "GUARDIAN_CONTROL_TOKEN", "")
    monkeypatch.setattr(monitor_app, "_guardian_control_audit", [])
    with monitor_app.app.test_client() as app_client:
        yield app_client


def test_control_plane_worker_register_and_heartbeat(client):
    register = client.post(
        "/api/control-plane/workers/register",
        json={
            "worker_id": "orion-test",
            "capabilities": ["openclaw", "orion"],
            "region": "us-east",
            "mode": "remote",
            "base_url": "http://127.0.0.1:5059",
        },
    )
    assert register.status_code == 200
    payload = register.get_json()
    assert payload["success"] is True
    assert payload["worker"]["worker_id"] == "orion-test"
    assert "openclaw" in payload["worker"]["capabilities"]

    heartbeat = client.post(
        "/api/control-plane/workers/heartbeat",
        json={
            "worker_id": "orion-test",
            "health": "ok",
            "queue_depth": 3,
            "running_tasks": 1,
            "backpressure_level": "normal",
        },
    )
    assert heartbeat.status_code == 200
    hb = heartbeat.get_json()
    assert hb["success"] is True
    assert hb["worker"]["queue_depth"] == 3


def test_openclaw_commands_manual_required_for_critical(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    resp = client.post(
        "/api/openclaw/commands",
        json={
            "command_type": "browser",
            "payload": {"action": "status", "risk_level": "critical", "route_mode": "direct"},
        },
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["success"] is False
    assert body["error_code"] == "MANUAL_REQUIRED"
    assert body["decision"] == "manual_required"


def test_openclaw_commands_auto_approved_low_risk_sync(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    monkeypatch.setattr(monitor_app, "OPENCLAW_QUEUE_ENABLED", False)
    monkeypatch.setattr(
        monitor_app,
        "_openclaw_queue_executor",
        lambda command_type, payload: {
            "success": True,
            "action": payload.get("action", ""),
            "result": {"ok": True, "command_type": command_type},
            "dispatch": {"mode": "local", "worker_id": monitor_app.LOCAL_INSTANCE_ID},
        },
    )
    resp = client.post(
        "/api/openclaw/commands",
        json={
            "command_type": "browser",
            "payload": {"action": "status", "risk_level": "low", "route_mode": "direct"},
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["decision"] == "auto_approved"
    assert body["risk_level"] == "low"


def test_guardian_control_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "GUARDIAN_CONTROL_TOKEN", "secret-token")
    denied = client.post(
        "/api/guardian/control",
        json={"action": "pause_autopilot", "reason": "maintenance", "actor": "test-suite"},
    )
    assert denied.status_code == 401
    denied_body = denied.get_json()
    assert denied_body["success"] is False

    allowed = client.post(
        "/api/guardian/control",
        headers={"X-Guardian-Control-Token": "secret-token"},
        json={"action": "resume_autopilot", "reason": "maintenance_done", "actor": "test-suite"},
    )
    assert allowed.status_code == 200
    allowed_body = allowed.get_json()
    assert allowed_body["success"] is True
    assert allowed_body["action"] == "resume_autopilot"


def test_autonomy_profile_full_auto_applies_runtime_and_autopilot(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [{"id": "orion-1", "online": True}, {"id": "orion-2", "online": True}],
    )
    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append(
            {
                "instance_id": instance_id,
                "target": target,
                "command": command,
                "priority": priority,
                "options": options or {},
            }
        )
        return {"success": True, "target": target, "command": command}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    resp = client.post("/api/autonomy/profile", json={"profile": "full_auto"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["autonomy"]["profile"] == "full_auto"
    assert body["monitor_supervisor"]["enabled"] is True

    set_profile_calls = [row for row in calls if row["command"] == "set_autonomy_profile full_auto"]
    assert len(set_profile_calls) == 2
    autopilot_calls = [row for row in calls if row["target"] == "guardian" and row["command"] == "autopilot_on"]
    assert len(autopilot_calls) == 2
    assert "orion-1" in body["instance_apply_results"]
    assert "orion-2" in body["instance_apply_results"]


def test_guardian_control_pause_full_auto_creates_temporary_lease(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_autonomy_profile", "full_auto")
    monkeypatch.setattr(monitor_app, "_monitor_supervisor_enabled", True)
    monkeypatch.setattr(monitor_app, "_monitor_autopilot_pause_until", None)

    resp = client.post(
        "/api/guardian/control",
        json={
            "action": "pause_autopilot",
            "reason": "maintenance",
            "actor": "test-suite",
            "lease_sec": 45,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["temporary_pause"] is True
    assert body["lease_sec"] == 45
    assert body["supervisor"]["enabled"] is False
    assert body["supervisor"]["autopilot_pause_until"] is not None


def test_monitor_autopilot_disable_full_auto_uses_temporary_pause(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_autonomy_profile", "full_auto")
    monkeypatch.setattr(monitor_app, "_monitor_supervisor_enabled", True)
    monkeypatch.setattr(monitor_app, "_monitor_autopilot_pause_until", None)

    resp = client.post("/api/monitor/autopilot", json={"enabled": False, "lease_sec": 50})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["requested_enabled"] is False
    assert body["temporary_pause"] is True
    assert body["lease_sec"] == 50
    assert body["enabled"] is False
    assert body["pause_until"] is not None


def test_slo_summary_endpoint(client):
    resp = client.get("/api/slo/summary")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert "slo" in payload
    assert "detail" in payload["slo"]
