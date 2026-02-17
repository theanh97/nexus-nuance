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


def _install_openclaw_test_queue(monkeypatch) -> None:
    manager = monitor_app._OpenClawCommandManager()
    manager.set_executor(
        lambda command_type, payload: {
            "success": True,
            "result": {"ok": True, "command_type": command_type, "action": payload.get("action", "")},
        }
    )
    monkeypatch.setattr(monitor_app, "_openclaw_command_manager", manager)
    monkeypatch.setattr(monitor_app, "OPENCLAW_QUEUE_ENABLED", True)
    monkeypatch.setattr(monitor_app, "API_RATE_LIMIT_OPENCLAW_PER_MIN", 10_000)
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_IDEMPOTENCY_ENABLED", True)
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC", 120)


def _idempotent_hits(client) -> int:
    metrics = client.get("/api/openclaw/metrics")
    assert metrics.status_code == 200
    payload = metrics.get_json()
    return int(payload["metrics"]["counters"]["queue_idempotent_hit"])


def test_openclaw_browser_burst_reuses_idempotent_request(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    _install_openclaw_test_queue(monkeypatch)

    before_hits = _idempotent_hits(client)
    session_id = f"burst-browser-{int(datetime.now().timestamp() * 1000)}"
    payload = {
        "action": "open",
        "url": "http://127.0.0.1:5050/",
        "mode": "async",
        "source": "manual",
        "session_id": session_id,
        "route_mode": "direct",
    }

    first = client.post("/api/openclaw/browser", json=payload)
    second = client.post("/api/openclaw/browser", json=payload)
    third = client.post("/api/openclaw/browser", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert third.status_code == 202

    first_body = first.get_json()
    second_body = second.get_json()
    third_body = third.get_json()

    assert first_body["success"] is True
    assert first_body["accepted"] is True
    assert first_body["request_id"]
    assert first_body["idempotent_replay"] is False
    assert second_body["request_id"] == first_body["request_id"]
    assert third_body["request_id"] == first_body["request_id"]
    assert second_body["idempotent_replay"] is True or third_body["idempotent_replay"] is True

    after_hits = _idempotent_hits(client)
    assert after_hits >= before_hits + 1


def test_openclaw_flow_dashboard_burst_uses_queue_idempotency(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    _install_openclaw_test_queue(monkeypatch)

    before_hits = _idempotent_hits(client)
    session_id = f"burst-flow-{int(datetime.now().timestamp() * 1000)}"
    payload = {
        "chat_text": "status",
        "approve": True,
        "deny": False,
        "mode": "async",
        "source": "manual",
        "session_id": session_id,
        "route_mode": "direct",
    }

    responses = [client.post("/api/openclaw/flow/dashboard", json=payload) for _ in range(3)]

    assert all(resp.status_code == 202 for resp in responses)
    bodies = [resp.get_json() for resp in responses]
    assert all(body["success"] is True for body in bodies)
    assert all(body["accepted"] is True for body in bodies)
    request_ids = [body["request_id"] for body in bodies]
    assert request_ids[0]
    assert request_ids[1] == request_ids[0]
    assert request_ids[2] == request_ids[0]

    after_hits = _idempotent_hits(client)
    assert after_hits >= before_hits + 1


def test_hub_task_create_returns_version(client):
    response = client.post(
        "/api/hub/tasks",
        json={
            "title": "Leaseable task",
            "description": "Ensure version is returned",
            "priority": "high",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["task"]["id"]
    assert isinstance(payload.get("version"), int)


def test_hub_task_create_rejects_stale_expected_version(client):
    first = client.post(
        "/api/hub/tasks",
        json={
            "title": "Task 1",
            "description": "fresh",
            "expected_version": 0,
        },
    )
    assert first.status_code == 200

    stale = client.post(
        "/api/hub/tasks",
        json={
            "title": "Task 2",
            "description": "stale",
            "expected_version": 0,
        },
    )
    assert stale.status_code == 409
    stale_payload = stale.get_json()
    assert stale_payload["success"] is False
    assert stale_payload["error_code"] == "VERSION_CONFLICT"


def test_hub_task_lease_claim_heartbeat_release_flow(client):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Claim me", "description": "lease flow"},
    )
    task_payload = created.get_json()
    task_id = task_payload["task"]["id"]

    claim = client.post(
        f"/api/hub/tasks/{task_id}/claim",
        json={"owner_id": "orion:A", "lease_sec": 90},
    )
    assert claim.status_code == 200
    claim_body = claim.get_json()
    lease_token = claim_body["lease"]["lease_token"]

    conflict = client.post(
        f"/api/hub/tasks/{task_id}/claim",
        json={"owner_id": "orion:B", "lease_sec": 90},
    )
    assert conflict.status_code == 409
    conflict_body = conflict.get_json()
    assert conflict_body["error_code"] == "LEASE_CONFLICT"

    heartbeat = client.post(
        f"/api/hub/tasks/{task_id}/heartbeat",
        json={"owner_id": "orion:A", "lease_token": lease_token, "lease_sec": 120},
    )
    assert heartbeat.status_code == 200
    hb_body = heartbeat.get_json()
    assert hb_body["success"] is True

    release = client.post(
        f"/api/hub/tasks/{task_id}/release",
        json={"owner_id": "orion:A", "lease_token": lease_token, "next_status": "todo"},
    )
    assert release.status_code == 200

    reclaim = client.post(
        f"/api/hub/tasks/{task_id}/claim",
        json={"owner_id": "orion:B", "lease_sec": 90},
    )
    assert reclaim.status_code == 200


def test_hub_task_update_respects_lease_owner(client):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Protected task", "description": "lease update guard"},
    )
    task_id = created.get_json()["task"]["id"]

    claim = client.post(
        f"/api/hub/tasks/{task_id}/claim",
        json={"owner_id": "orion:owner", "lease_sec": 120},
    )
    lease_token = claim.get_json()["lease"]["lease_token"]

    blocked = client.put(
        f"/api/hub/tasks/{task_id}",
        json={
            "status": "review",
            "owner_id": "orion:other",
            "lease_token": "bad-token",
        },
    )
    assert blocked.status_code == 423
    blocked_payload = blocked.get_json()
    assert blocked_payload["error_code"] == "LEASE_CONFLICT"

    allowed = client.put(
        f"/api/hub/tasks/{task_id}",
        json={
            "status": "review",
            "owner_id": "orion:owner",
            "lease_token": lease_token,
        },
    )
    assert allowed.status_code == 200
    allowed_payload = allowed.get_json()
    assert allowed_payload["success"] is True
    assert allowed_payload["task"]["status"] == "review"
