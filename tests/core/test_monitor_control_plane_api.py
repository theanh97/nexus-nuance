from datetime import datetime, timedelta

import pytest

import monitor.app as monitor_app
from src.memory.team_persona import TeamPersonaStore


def _healthy_token_info() -> monitor_app._OpenClawTokenInfo:
    return monitor_app._OpenClawTokenInfo(
        token="test-token",
        checked_at=datetime.now(),
        source="env",
        health_ok=True,
        health_error=None,
        last_error=None,
    )


def _create_hub_task(client, title: str = "Hub task") -> str:
    created = client.post(
        "/api/hub/tasks",
        json={"title": title, "description": "lease guarded task"},
    )
    assert created.status_code == 200
    payload = created.get_json()
    assert payload["success"] is True
    return payload["task"]["id"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monitor_app.app.config["TESTING"] = True
    monkeypatch.setattr(monitor_app, "DASHBOARD_ACCESS_TOKEN", "")
    monkeypatch.setattr(monitor_app, "_autonomy_profile", "balanced")
    monkeypatch.setattr(monitor_app, "_monitor_supervisor_enabled", True)
    monkeypatch.setattr(monitor_app, "_monitor_autopilot_pause_until", None)
    monkeypatch.setattr(monitor_app, "_agent_coordination_recent", {})
    monkeypatch.setattr(monitor_app, "_automation_recent_tasks", monitor_app.deque(maxlen=monitor_app.AUTOMATION_TASK_HISTORY_MAX))
    monkeypatch.setattr(monitor_app, "_automation_tasks_by_id", {})
    monkeypatch.setattr(monitor_app, "_automation_sessions", {})
    monkeypatch.setattr(monitor_app, "_automation_poll_last_seen", {})
    monkeypatch.setattr(monitor_app, "_automation_scheduled_tasks", {})
    monkeypatch.setattr(monitor_app, "_automation_scheduler_running", False)
    monkeypatch.setattr(monitor_app, "OPENCLAW_EXECUTION_MODE", "browser")
    monkeypatch.setattr(monitor_app, "_bridge_terminal_sessions", {})
    monkeypatch.setattr(
        monitor_app,
        "_bridge_state",
        {
            "jackos_connected": False,
            "last_sync": None,
            "synced_contacts": 0,
            "synced_sessions": 0,
            "synced_terminals": 0,
            "terminal_last_sync": None,
            "notifications_received": 0,
        },
    )
    monkeypatch.setattr(monitor_app, "GUARDIAN_CONTROL_TOKEN", "")
    monkeypatch.setattr(monitor_app, "_guardian_control_audit", [])
    monkeypatch.setattr(
        monitor_app,
        "_team_persona_store",
        TeamPersonaStore(
            state_path=tmp_path / "team_personas.json",
            events_path=tmp_path / "team_persona_events.jsonl",
        ),
    )
    with monitor_app._hub._exclusive_state() as state:
        state.clear()
        state.update(monitor_app._hub._default_state())
        monitor_app._hub._commit_state(state, increment_version=False)
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


def test_openclaw_browser_endpoint_rejects_headless_mode(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "OPENCLAW_EXECUTION_MODE", "headless")

    resp = client.post(
        "/api/openclaw/browser",
        json={"action": "status"},
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["success"] is False
    assert body["error_code"] == "BROWSER_MODE_DISABLED"


def test_openclaw_flow_endpoint_requires_browser_mode(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "OPENCLAW_EXECUTION_MODE", "hybrid")

    resp = client.post(
        "/api/openclaw/flow/dashboard",
        json={"chat_text": "status"},
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["success"] is False
    assert body["error_code"] == "FLOW_MODE_DISABLED"


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


def test_hub_next_action_autopilot_status(client):
    """Test hub next-action autopilot status endpoint."""
    resp = client.get("/api/hub/next-action/autopilot/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "enabled" in body
    assert "interval_sec" in body
    assert "running" in body
    assert "allowed_types" in body
    assert isinstance(body["allowed_types"], list)

    policy_resp = client.get("/api/hub/autopilot/status")
    assert policy_resp.status_code == 200
    policy_body = policy_resp.get_json()
    assert policy_body["success"] is True
    assert "active_executor" in policy_body
    assert "executor_conflict" in policy_body


def test_hub_next_action_autopilot_control_trigger(client):
    """Test hub next-action autopilot trigger action."""
    resp = client.post("/api/hub/next-action/autopilot/control", json={"action": "trigger"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "executed" in body


def test_hub_next_action_autopilot_control_start(client):
    """Test hub next-action autopilot start."""
    resp = client.post("/api/hub/next-action/autopilot/control", json={"action": "start"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "started" in body["message"].lower()


def test_hub_next_action_autopilot_control_stop(client):
    """Test hub next-action autopilot stop."""
    resp = client.post("/api/hub/next-action/autopilot/control", json={"action": "stop"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True


def test_hub_next_action_autopilot_control_unknown(client):
    """Test hub next-action autopilot unknown action."""
    resp = client.post("/api/hub/next-action/autopilot/control", json={"action": "unknown"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False


def test_central_file_lock_reclaims_expired_lock(client):
    monitor_app._global_file_locks.clear()
    monitor_app._global_file_locks["src/demo.py"] = monitor_app.FileLock(
        file_path="src/demo.py",
        orion_id="orion-old",
        agent_id="agent-x",
        locked_at="2001-01-01T00:00:00",
        expires_at="2001-01-01T00:05:00",
    )

    resp = client.post(
        "/api/central/files/lock",
        json={"file_path": "src/demo.py", "orion_id": "orion-new", "ttl_sec": 120},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["expired_lock_reclaimed"] is True


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


def test_openclaw_browser_auto_attach_uses_non_force_mode(monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    monkeypatch.setattr(monitor_app, "OPENCLAW_EXECUTION_MODE", "browser")
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_ATTACH_ENABLED", True)
    monitor_app._OPENCLAW_ATTACH_CONTEXT.depth = 0
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_ATTACH_ENABLED", True)

    calls = {"count": 0}

    def fake_cli(args, timeout_ms=0, retries=0):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"success": False, "stderr": "No tab is connected", "stdout": "", "returncode": 1}
        return {"success": True, "stdout": "{\"running\": true}", "stderr": "", "returncode": 0}

    attach_force_args = []

    def fake_attach(force=False):
        attach_force_args.append(bool(force))
        return {"success": True, "method": "stub", "steps": []}

    monkeypatch.setattr(monitor_app, "_run_openclaw_cli", fake_cli)
    monkeypatch.setattr(monitor_app, "_openclaw_attempt_attach", fake_attach)

    result = monitor_app._openclaw_browser_cmd(["start"], auto_recover=False)

    assert result["success"] is True
    assert attach_force_args == [False]


def test_openclaw_browser_auto_attach_skips_inside_attach_scope(monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    monkeypatch.setattr(
        monitor_app,
        "_run_openclaw_cli",
        lambda args, timeout_ms=0, retries=0: {
            "success": False,
            "stderr": "No tab is connected",
            "stdout": "",
            "returncode": 1,
        },
    )
    attach_calls = []
    monkeypatch.setattr(
        monitor_app,
        "_openclaw_attempt_attach",
        lambda force=False: attach_calls.append(bool(force)) or {"success": True},
    )

    monitor_app._openclaw_attach_scope_enter()
    try:
        result = monitor_app._openclaw_browser_cmd(["status"], auto_recover=False)
    finally:
        monitor_app._openclaw_attach_scope_exit()

    assert result["success"] is False
    assert attach_calls == []


def test_openclaw_attempt_attach_verification_disables_nested_auto_attach(monkeypatch):
    monkeypatch.setattr(monitor_app, "pyautogui", object())
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_ATTACH_RETRIES", 1)
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_ATTACH_HEURISTIC", False)
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_ATTACH_ICON_MATCH", True)
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTO_ATTACH_DELAY_SEC", 0.0)
    monkeypatch.setattr(monitor_app, "OPENCLAW_LAUNCH_WITH_EXTENSION", False)
    monkeypatch.setattr(monitor_app, "OPENCLAW_CHROME_APP", "Google Chrome")
    monkeypatch.setattr(monitor_app, "OPENCLAW_DASHBOARD_URL", "http://127.0.0.1:5050/")

    monkeypatch.setattr(monitor_app, "_openclaw_extension_path", lambda: monitor_app.Path("."))
    monkeypatch.setattr(monitor_app, "_activate_chrome_window", lambda: {"success": True})
    monkeypatch.setattr(monitor_app, "_ensure_chrome_tab", lambda url: {"success": True, "url": url})
    monkeypatch.setattr(monitor_app, "_find_extension_icon", lambda extension_path: monitor_app.Path("icon.png"))
    monkeypatch.setattr(monitor_app, "_click_extension_icon", lambda icon_path: {"success": True})
    monkeypatch.setattr(monitor_app.subprocess, "run", lambda *args, **kwargs: None)

    auto_attach_flags = []

    def fake_browser_cmd(args, **kwargs):
        auto_attach_flags.append(bool(kwargs.get("auto_attach", True)))
        if args and args[0] == "status":
            return {"success": True, "json": {"running": True}}
        return {"success": True, "json": {}}

    monkeypatch.setattr(monitor_app, "_openclaw_browser_cmd", fake_browser_cmd)

    result = monitor_app._openclaw_attempt_attach(force=True)

    assert result["success"] is True
    assert auto_attach_flags
    assert all(flag is False for flag in auto_attach_flags)


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


def test_hub_task_release_supports_force_expired(client):
    task_id = _create_hub_task(client, title="Force release")
    claim = client.post(
        f"/api/hub/tasks/{task_id}/claim",
        json={"owner_id": "orion:A", "lease_sec": 120},
    )
    assert claim.status_code == 200

    with monitor_app._hub._exclusive_state() as state:
        task = next(row for row in state["tasks"] if row["id"] == task_id)
        task["lease_expires_at"] = "2001-01-01T00:00:00"
        monitor_app._hub._commit_state(state)

    release = client.post(
        f"/api/hub/tasks/{task_id}/release",
        json={
            "owner_id": "orion:A",
            "lease_token": "",
            "next_status": "todo",
            "force_if_expired": True,
            "actor_id": "autopilot",
            "reason": "stale_lease_reclaim",
        },
    )
    assert release.status_code == 200
    payload = release.get_json()
    assert payload["success"] is True
    assert payload["task"]["status"] == "todo"


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


def test_openclaw_lease_enforcement_rejects_missing_context(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    _install_openclaw_test_queue(monkeypatch)
    monkeypatch.setattr(monitor_app, "OPENCLAW_REQUIRE_TASK_LEASE", True)

    response = client.post(
        "/api/openclaw/browser",
        json={
            "action": "open",
            "url": "http://127.0.0.1:5050/",
            "mode": "async",
            "source": "manual",
            "session_id": "lease-missing",
        },
    )
    assert response.status_code == 423
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error_code"] == "LEASE_CONTEXT_REQUIRED"


def test_openclaw_lease_enforcement_accepts_valid_context(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "_collect_token_info", lambda force_refresh=False: _healthy_token_info())
    _install_openclaw_test_queue(monkeypatch)
    monkeypatch.setattr(monitor_app, "OPENCLAW_REQUIRE_TASK_LEASE", True)

    task_id = _create_hub_task(client, title="OpenClaw lease")
    claim = client.post(
        f"/api/hub/tasks/{task_id}/claim",
        json={"owner_id": "orion:test", "lease_sec": 120},
    )
    assert claim.status_code == 200
    lease_token = claim.get_json()["lease"]["lease_token"]

    response = client.post(
        "/api/openclaw/browser",
        json={
            "action": "open",
            "url": "http://127.0.0.1:5050/",
            "mode": "async",
            "source": "manual",
            "session_id": "lease-valid",
            "task_id": task_id,
            "task_owner_id": "orion:test",
            "task_lease_token": lease_token,
        },
    )
    assert response.status_code == 202
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["accepted"] is True


def test_control_plane_workers_exposes_lease_enforcement(client):
    response = client.get("/api/control-plane/workers")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "lease_enforcement" in payload
    assert "require_task_lease" in payload["lease_enforcement"]
    assert "lease_heartbeat_sec" in payload["lease_enforcement"]


def test_monitor_recovery_enqueues_openclaw_autopilot_flow(monkeypatch):
    monkeypatch.setattr(monitor_app, "OPENCLAW_EXECUTION_MODE", "browser")
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTONOMOUS_UI_ENABLED", True)
    monkeypatch.setattr(monitor_app, "MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", True)
    monkeypatch.setattr(monitor_app, "MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC", 120)
    monkeypatch.setattr(monitor_app, "OPENCLAW_REQUIRE_TASK_LEASE", False)
    monkeypatch.setattr(monitor_app, "_monitor_process_started_at", datetime.now() - timedelta(seconds=600))
    monkeypatch.setattr(monitor_app, "_monitor_last_recovery_at", {})
    monkeypatch.setattr(monitor_app, "_monitor_last_reason", {})
    monkeypatch.setattr(monitor_app, "_monitor_last_openclaw_flow_at", {})

    queued_calls = []

    def fake_enqueue(command_type, payload):
        queued_calls.append({"command_type": command_type, "payload": dict(payload)})
        return {"success": True, "request_id": "oc-autopilot-1"}

    guardian_calls = []

    def fake_guardian(instance_id, target, command, priority, options=None):
        guardian_calls.append(
            {
                "instance_id": instance_id,
                "target": target,
                "command": command,
                "priority": priority,
                "options": dict(options or {}),
            }
        )
        return {"success": True}

    monkeypatch.setattr(monitor_app, "_enqueue_openclaw_command", fake_enqueue)
    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_guardian)

    monitor_app._monitor_maybe_recover_instance(
        {"id": "orion-test", "online": True, "running": False, "paused": False}
    )

    assert len(queued_calls) == 1
    queued = queued_calls[0]
    assert queued["command_type"] == "flow"
    assert queued["payload"]["source"] == "autopilot"
    assert queued["payload"]["mode"] == "async"
    assert queued["payload"]["session_id"] == "autopilot:orion-test"
    assert len(guardian_calls) == 1
    assert guardian_calls[0]["command"] == "unstick_orion"


def test_monitor_recovery_skips_openclaw_flow_when_execution_mode_headless(monkeypatch):
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTONOMOUS_UI_ENABLED", True)
    monkeypatch.setattr(monitor_app, "MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", True)
    monkeypatch.setattr(monitor_app, "OPENCLAW_EXECUTION_MODE", "headless")
    result = monitor_app._monitor_maybe_enqueue_openclaw_flow("orion-test", "not_running")
    assert result["attempted"] is False
    assert result["reason"] == "execution_mode_headless"


def test_monitor_recovery_skips_openclaw_autopilot_when_lease_required(monkeypatch):
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTONOMOUS_UI_ENABLED", True)
    monkeypatch.setattr(monitor_app, "MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", True)
    monkeypatch.setattr(monitor_app, "OPENCLAW_REQUIRE_TASK_LEASE", True)
    monkeypatch.setattr(monitor_app, "_monitor_process_started_at", datetime.now() - timedelta(seconds=600))
    monkeypatch.setattr(monitor_app, "_monitor_last_recovery_at", {})
    monkeypatch.setattr(monitor_app, "_monitor_last_reason", {})
    monkeypatch.setattr(monitor_app, "_monitor_last_openclaw_flow_at", {})

    queued_calls = []
    monkeypatch.setattr(
        monitor_app,
        "_enqueue_openclaw_command",
        lambda command_type, payload: queued_calls.append((command_type, payload)) or {"success": True},
    )

    guardian_calls = []
    monkeypatch.setattr(
        monitor_app,
        "_call_instance_command",
        lambda *args, **kwargs: guardian_calls.append((args, kwargs)) or {"success": True},
    )

    monitor_app._monitor_maybe_recover_instance(
        {"id": "orion-test", "online": True, "running": False, "paused": False}
    )

    assert queued_calls == []
    assert len(guardian_calls) == 1


def test_monitor_recovery_skips_openclaw_autopilot_when_autonomous_ui_disabled(monkeypatch):
    monkeypatch.setattr(monitor_app, "OPENCLAW_AUTONOMOUS_UI_ENABLED", False)
    monkeypatch.setattr(monitor_app, "MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", True)
    result = monitor_app._monitor_maybe_enqueue_openclaw_flow("orion-test", "not_running")
    assert result["attempted"] is False
    assert result["reason"] == "autonomous_ui_disabled"


def test_interventions_queue_and_defer(client):
    decision = monitor_app.state.add_pending_decision(
        {
            "action": "command_intervention",
            "kind": "command_intervention",
            "title": "Need manual check",
            "risk_level": "high",
            "command": "ls -la",
            "instance_id": "orion-test",
        }
    )
    decision_id = decision["id"]

    queue = client.get("/api/interventions/queue")
    assert queue.status_code == 200
    queue_payload = queue.get_json()
    assert queue_payload["success"] is True
    ids = [row["id"] for row in queue_payload["queue"]]
    assert decision_id in ids

    deferred = client.post(
        f"/api/interventions/{decision_id}/defer",
        json={"reason": "wait_for_window", "defer_sec": 120},
    )
    assert deferred.status_code == 200
    deferred_payload = deferred.get_json()
    assert deferred_payload["success"] is True
    assert deferred_payload["item"]["deferred"] is True


def test_interventions_force_approve_runs_command(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "_execute_approved_shell_command",
        lambda command: {"success": True, "command": command, "output": "ok"},
    )
    decision = monitor_app.state.add_pending_decision(
        {
            "action": "command_intervention",
            "kind": "command_intervention",
            "title": "Execute command",
            "risk_level": "medium",
            "command": "ls",
            "instance_id": "orion-test",
            "auto_execute_on_approve": True,
        }
    )
    decision_id = decision["id"]

    approved = client.post(
        f"/api/interventions/{decision_id}/force-approve",
        json={"actor": "test-suite", "reason": "forced"},
    )
    assert approved.status_code == 200
    approved_payload = approved.get_json()
    assert approved_payload["success"] is True
    assert approved_payload["item"]["decision"] == "approved"
    assert approved_payload["execution"]["success"] is True


def test_hub_tasks_batch_update_and_history(client):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Batch update me", "description": "batch"},
    )
    assert created.status_code == 200
    task_id = created.get_json()["task"]["id"]

    updated = client.post(
        "/api/hub/tasks/batch-update",
        json={"updates": [{"task_id": task_id, "status": "review", "assigned_to": "orion-1"}]},
    )
    assert updated.status_code == 200
    updated_payload = updated.get_json()
    assert updated_payload["success"] is True
    assert updated_payload["summary"]["applied"] == 1

    history = client.get(f"/api/hub/tasks/{task_id}/history")
    assert history.status_code == 200
    history_payload = history.get_json()
    assert history_payload["success"] is True
    assert history_payload["task_id"] == task_id
    assert history_payload["count"] >= 1


def test_hub_task_dispatch_assigns_and_runs_cycle(client, monkeypatch):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Dispatch me", "description": "handoff test", "priority": "high"},
    )
    assert created.status_code == 200
    task_id = created.get_json()["task"]["id"]

    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append(
            {
                "instance_id": instance_id,
                "target": target,
                "command": command,
                "priority": priority,
                "options": dict(options or {}),
            }
        )
        return {"success": True, "instance_id": instance_id, "target": target, "command": command}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    dispatched = client.post(
        f"/api/hub/tasks/{task_id}/dispatch",
        json={"instance_id": "orion-2"},
    )
    assert dispatched.status_code == 200
    payload = dispatched.get_json()
    assert payload["success"] is True
    assert payload["instance_id"] == "orion-2"
    assert payload["dispatch"]["success"] is True

    assert len(calls) == 1
    assert calls[0]["instance_id"] == "orion-2"
    assert calls[0]["target"] == "orion"
    assert calls[0]["command"] == "run_cycle"
    assert calls[0]["options"]["dispatch_task_id"] == task_id

    tasks = client.get("/api/hub/tasks")
    assert tasks.status_code == 200
    task_rows = tasks.get_json()["tasks"]
    updated_task = next(row for row in task_rows if row["id"] == task_id)
    assert updated_task["assigned_to"] == "orion-2"


def test_hub_task_dispatch_rejects_finalized_task_without_force_reopen(client):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Finalized task", "description": "already done"},
    )
    assert created.status_code == 200
    task_id = created.get_json()["task"]["id"]

    moved = client.post(
        "/api/hub/tasks/batch-update",
        json={"updates": [{"task_id": task_id, "status": "done"}]},
    )
    assert moved.status_code == 200

    dispatched = client.post(f"/api/hub/tasks/{task_id}/dispatch", json={})
    assert dispatched.status_code == 409
    payload = dispatched.get_json()
    assert payload["success"] is False
    assert payload["error_code"] == "TASK_FINALIZED"


def test_hub_workload_includes_runtime_and_tool_sessions(client, monkeypatch):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Runtime task", "description": "status", "priority": "urgent", "assigned_to": "orion-1"},
    )
    assert created.status_code == 200
    task_id = created.get_json()["task"]["id"]

    moved = client.post(
        "/api/hub/tasks/batch-update",
        json={"updates": [{"task_id": task_id, "status": "in_progress", "assigned_to": "orion-1"}]},
    )
    assert moved.status_code == 200

    registered = client.post("/api/hub/register-orion", json={"instance_id": "orion-1", "config": {}})
    assert registered.status_code == 200

    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {
                "id": "orion-1",
                "name": "Orion 1",
                "online": True,
                "running": True,
                "paused": False,
                "pause_reason": "",
                "iteration": 7,
                "active_flow_count": 2,
                "last_progress_at": datetime.now().isoformat(),
            }
        ],
    )

    with monitor_app._AUTOMATION_TASKS_LOCK:
        monitor_app._automation_sessions["sess-runtime-1"] = {
            "session_id": "sess-runtime-1",
            "owner_id": "orion:orion-1",
            "queue_depth": 1,
            "lease_expires_at": (datetime.now() + timedelta(seconds=120)).isoformat(),
            "last_task_id": "auto-task-1",
            "last_error": "",
            "lease_token": "token-1",
        }

    synced = client.post(
        "/api/bridge/sync/terminals",
        json={
            "source": "vscode-test",
            "terminals": [
                {
                    "session_id": "term-1",
                    "owner_id": "orion:orion-1",
                    "title": "Deploy",
                    "cwd": "/tmp",
                    "command": "npm run deploy",
                    "status": "running",
                    "busy": True,
                }
            ],
        },
    )
    assert synced.status_code == 200

    workload = client.get("/api/hub/workload")
    assert workload.status_code == 200
    payload = workload.get_json()
    assert payload["success"] is True
    assert payload["automation_sessions"]["total"] >= 1
    assert payload["bridge_terminals"]["total"] >= 1
    assert payload["next_actions_preview"] is not None

    rows = payload["workload"]
    match = next(row for row in rows if row["assignee"] == "orion-1")
    assert match["runtime"]["iteration"] == 7
    assert match["runtime"]["running"] is True
    assert match["tool_sessions"]["owned"] >= 1
    assert match["tool_sessions"]["busy"] >= 1
    assert match["terminal_sessions"]["owned"] >= 1
    assert match["terminal_sessions"]["busy"] >= 1


def test_hub_next_actions_recommends_for_unassigned_todo(client):
    created = client.post(
        "/api/hub/tasks",
        json={"title": "Unassigned urgent", "description": "needs owner", "priority": "critical"},
    )
    assert created.status_code == 200
    task_id = created.get_json()["task"]["id"]

    actions_resp = client.get("/api/hub/next-actions?limit=5")
    assert actions_resp.status_code == 200
    payload = actions_resp.get_json()
    assert payload["success"] is True
    assert payload["count"] >= 1
    action_types = [row.get("action_type") for row in payload["actions"]]
    assert "assign_then_dispatch" in action_types
    assert any(row.get("task_id") == task_id for row in payload["actions"])


def test_bridge_sync_terminals_and_list(client):
    synced = client.post(
        "/api/bridge/sync/terminals",
        json={
            "source": "vscode",
            "terminals": [
                {
                    "session_id": "term-a",
                    "owner_id": "orion:orion-1",
                    "title": "Build",
                    "cwd": "/tmp/project",
                    "command": "npm run build",
                    "status": "running",
                },
                {
                    "session_id": "term-b",
                    "owner_id": "orion:orion-2",
                    "title": "Tests",
                    "cwd": "/tmp/project",
                    "command": "pytest -q",
                    "status": "error",
                    "last_error": "test failed",
                },
            ],
        },
    )
    assert synced.status_code == 200
    sync_payload = synced.get_json()
    assert sync_payload["success"] is True
    assert sync_payload["upserts"] == 2
    assert sync_payload["summary"]["total"] >= 2

    listed = client.get("/api/bridge/terminals?limit=10")
    assert listed.status_code == 200
    list_payload = listed.get_json()
    assert list_payload["success"] is True
    assert list_payload["summary"]["total"] >= 2
    assert any(row.get("session_id") == "term-b" for row in list_payload["terminals"])

    status = client.get("/api/bridge/status")
    assert status.status_code == 200
    status_payload = status.get_json()
    assert status_payload["success"] is True
    assert status_payload["bridge"]["stats"]["synced_terminals"] >= 2


def test_hub_next_actions_includes_terminal_error_signal(client):
    synced = client.post(
        "/api/bridge/sync/terminals",
        json={
            "source": "vscode",
            "terminals": [
                {
                    "session_id": "term-err-1",
                    "owner_id": "orion:orion-1",
                    "title": "Deploy",
                    "command": "deploy.sh",
                    "status": "error",
                    "last_error": "permission denied",
                }
            ],
        },
    )
    assert synced.status_code == 200

    actions_resp = client.get("/api/hub/next-actions?limit=5")
    assert actions_resp.status_code == 200
    payload = actions_resp.get_json()
    assert payload["success"] is True
    assert any(row.get("action_type") == "inspect_terminal_error" for row in payload["actions"])


def test_audit_timeline_and_export_endpoints(client):
    timeline = client.get("/api/audit/timeline?limit=60")
    assert timeline.status_code == 200
    timeline_payload = timeline.get_json()
    assert timeline_payload["success"] is True
    assert "items" in timeline_payload

    exported = client.get("/api/audit/export?format=jsonl&limit=20")
    assert exported.status_code == 200
    body = exported.get_data(as_text=True)
    assert isinstance(body, str)


def test_hub_spawn_endpoint(client):
    spawned = client.post("/api/hub/spawn", json={"goal": "test scaling"})
    assert spawned.status_code == 200
    payload = spawned.get_json()
    assert payload["success"] is True
    assert payload["spawn"]["instance_id"]


def test_team_persona_endpoints_support_self_learning(client):
    created = client.post(
        "/api/team/personas",
        json={
            "member_id": "pm-lan",
            "name": "Lan",
            "role": "product_manager",
            "preferences": {"response_structure": "steps"},
        },
    )
    assert created.status_code == 200
    created_payload = created.get_json()
    assert created_payload["success"] is True
    assert created_payload["member"]["member_id"] == "pm-lan"

    interaction = client.post(
        "/api/team/personas/pm-lan/interaction",
        json={
            "message": "Chỉ làm luôn, không cần hỏi lại. Gửi bản ngắn gọn.",
            "channel": "chat",
            "intent": "delivery_push",
        },
    )
    assert interaction.status_code == 200
    interaction_payload = interaction.get_json()
    assert interaction_payload["success"] is True
    assert interaction_payload["learned"]["signals"]["direct"] is True
    assert interaction_payload["adaptation"]["member_id"] == "pm-lan"

    adaptation = client.get("/api/team/personas/pm-lan/adaptation?intent=ship_release")
    assert adaptation.status_code == 200
    adaptation_payload = adaptation.get_json()
    assert adaptation_payload["success"] is True
    assert adaptation_payload["adaptation"]["known_member"] is True
    assert adaptation_payload["adaptation"]["communication"]["response_structure"] == "steps"


def test_orion_instances_broadcast_command(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 1},
            {"id": "orion-2", "name": "Orion 2", "online": True, "running": True, "paused": False, "active_flow_count": 0},
        ],
    )
    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append((instance_id, target, command, priority, dict(options or {})))
        return {"success": True, "instance_id": instance_id}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    response = client.post(
        "/api/orion/instances/broadcast",
        json={
            "member_id": "lead-dev",
            "target": "guardian",
            "command": "autopilot_on",
            "priority": "high",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["success"] == 2
    assert len(calls) == 2


def test_hub_orchestrate_generates_assignments_and_tasks(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 0},
            {"id": "orion-2", "name": "Orion 2", "online": True, "running": True, "paused": False, "active_flow_count": 1},
        ],
    )
    response = client.post(
        "/api/hub/orchestrate",
        json={
            "member_id": "pm-lan",
            "goal": "Ship autonomous triage",
            "task_count": 4,
            "create_tasks": True,
            "priority": "high",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["assignment_count"] == 4
    plan = payload["plan"]
    assert len(plan["assignments"]) == 4
    assert len(plan["created_tasks"]) == 4
    assert "token_budget" in payload
    assert payload["token_budget"]["estimated_tokens_total"] >= 1


def test_chat_ask_returns_member_adaptation_payload(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "_build_project_chat_reply",
        lambda message, instance_id=None, member_id=None: {
            "intent": "assistant_help",
            "executed": False,
            "reply": "Line 1\nLine 2\nLine 3\nLine 4",
            "result": {"reasoning": {}, "ai_summary": {}, "feedback_recent": []},
        },
    )
    response = client.post(
        "/api/chat/ask",
        json={
            "member_id": "pm-lan",
            "instance_id": "orion-1",
            "message": "Chỉ cần bản ngắn gọn, làm luôn.",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["member_id"] == "pm-lan"
    assert payload["member_adaptation"]["member_id"] == "pm-lan"


def test_chat_ask_auto_dispatches_agent_coordination(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 0},
            {"id": "orion-2", "name": "Orion 2", "online": True, "running": True, "paused": False, "active_flow_count": 1},
        ],
    )
    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append({"instance_id": instance_id, "target": target, "command": command, "priority": priority})
        return {"success": True, "instance_id": instance_id, "target": target}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    response = client.post(
        "/api/chat/ask",
        json={
            "member_id": "lead-dev",
            "instance_id": "orion-1",
            "message": "coordinate release and runtime monitor",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["intent"] == "agent_coordination_dispatch"
    assert payload["executed"] is True
    assert payload["member_id"] == "lead-dev"
    assert payload["result"]["task_materialization"]["enabled"] is True
    assert payload["result"]["task_materialization"]["created"] >= 1
    assert len(calls) >= 2


def test_agents_coordination_dispatch_compact_mode(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 0},
            {"id": "orion-2", "name": "Orion 2", "online": True, "running": True, "paused": False, "active_flow_count": 1},
        ],
    )
    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append({"instance_id": instance_id, "target": target, "command": command, "priority": priority})
        return {"success": True, "instance_id": instance_id, "target": target}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    response = client.post(
        "/api/agents/coordination/dispatch",
        json={
            "member_id": "lead-dev",
            "goal": "Coordinate release + security audit + runtime monitor",
            "compact_mode": True,
            "max_agents": 4,
            "execute": True,
            "multi_instance": True,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["execution"]["executed"] is True
    assert payload["execution"]["total_sent"] == len(calls)
    assert payload["plan"]["token_budget"]["estimated_tokens_saved"] >= 0
    assert payload["plan"]["token_budget"]["estimated_tokens_total"] > 0
    assert payload["task_materialization"]["enabled"] is True
    assert payload["task_materialization"]["created"] >= 1


def test_agents_coordination_dispatch_defaults_multi_instance(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 0},
            {"id": "orion-2", "name": "Orion 2", "online": True, "running": True, "paused": False, "active_flow_count": 0},
        ],
    )
    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append({"instance_id": instance_id, "target": target, "command": command, "priority": priority})
        return {"success": True, "instance_id": instance_id, "target": target}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    response = client.post(
        "/api/agents/coordination/dispatch",
        json={
            "goal": "Runtime monitor and logs",
            "compact_mode": True,
            "max_agents": 3,
            "execute": True,
        },
    )
    assert response.status_code == 200
    assert calls
    assert {row["instance_id"] for row in calls} == {"orion-1", "orion-2"}


def test_agents_coordination_dispatch_can_skip_task_materialization(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [
            {"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 0},
        ],
    )
    monkeypatch.setattr(
        monitor_app,
        "_call_instance_command",
        lambda instance_id, target, command, priority, options=None: {"success": True, "instance_id": instance_id, "target": target},
    )
    response = client.post(
        "/api/agents/coordination/dispatch",
        json={
            "goal": "single instance coordinate",
            "execute": True,
            "create_tasks": False,
            "compact_mode": True,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["task_materialization"]["enabled"] is False
    assert payload["task_materialization"]["created"] == 0


def test_agents_coordination_dispatch_deduplicates_repeated_goal(client, monkeypatch):
    monkeypatch.setattr(
        monitor_app,
        "get_orion_instances_snapshot",
        lambda: [{"id": "orion-1", "name": "Orion 1", "online": True, "running": True, "paused": False, "active_flow_count": 0}],
    )
    calls = []

    def fake_call(instance_id, target, command, priority, options=None):
        calls.append({"instance_id": instance_id, "target": target, "command": command, "priority": priority})
        return {"success": True}

    monkeypatch.setattr(monitor_app, "_call_instance_command", fake_call)

    first = client.post(
        "/api/agents/coordination/dispatch",
        json={
            "goal": "Coordinate release + security audit",
            "compact_mode": True,
            "max_agents": 3,
            "execute": True,
            "instance_id": "orion-1",
        },
    )
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["success"] is True
    sent_after_first = len(calls)
    assert sent_after_first > 0

    second = client.post(
        "/api/agents/coordination/dispatch",
        json={
            "goal": "Coordinate release + security audit",
            "compact_mode": True,
            "max_agents": 3,
            "execute": True,
            "instance_id": "orion-1",
        },
    )
    assert second.status_code == 202
    second_payload = second.get_json()
    assert second_payload["success"] is True
    assert second_payload["execution"]["executed"] is False
    assert second_payload["execution"]["deduplicated"] is True
    assert len(calls) == sent_after_first


# ============================================
# Inter-ORION Messaging Tests
# ============================================


def test_send_direct_message(client):
    """Test sending a direct message between ORIONs."""
    response = client.post(
        "/api/hub/messages",
        json={
            "from_orion": "orion-alpha",
            "to_orion": "orion-beta",
            "message_type": "direct",
            "content": "Hello from alpha!",
            "priority": "normal",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "message" in payload
    assert payload["message"]["from_orion"] == "orion-alpha"
    assert payload["message"]["to_orion"] == "orion-beta"
    assert payload["message"]["message_type"] == "direct"


def test_send_broadcast_message(client):
    """Test sending a broadcast message."""
    response = client.post(
        "/api/hub/messages",
        json={
            "from_orion": "orion-alpha",
            "to_orion": "*",
            "message_type": "broadcast",
            "content": "Status update: All systems operational",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"]["to_orion"] == "*"


def test_get_messages_for_orion(client):
    """Test getting messages for a specific ORION."""
    # Send a message first
    client.post(
        "/api/hub/messages",
        json={
            "from_orion": "orion-alpha",
            "to_orion": "orion-beta",
            "message_type": "direct",
            "content": "Test message",
        },
    )

    # Get messages for orion-beta
    response = client.get("/api/hub/messages?orion_id=orion-beta")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "messages" in payload
    assert len(payload["messages"]) >= 1


def test_help_request(client):
    """Test sending a help request."""
    response = client.post(
        "/api/hub/help-request",
        json={
            "from_orion": "orion-alpha",
            "help_type": "security",
            "description": "Need help with security audit",
            "urgency": "high",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"]["message_type"] == "help_request"
    assert payload["message"]["requires_response"] is True


def test_task_handoff(client):
    """Test handing off a task to another ORION."""
    response = client.post(
        "/api/hub/task-handoff",
        json={
            "from_orion": "orion-alpha",
            "to_orion": "orion-beta",
            "task_id": "task-123",
            "task_title": "Implement feature X",
            "handoff_reason": "Shifting focus to priority P0 task",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"]["message_type"] == "task_handoff"
    assert payload["message"]["related_task_id"] == "task-123"


def test_mark_message_read(client):
    """Test marking a message as read."""
    # Send a message first
    send_resp = client.post(
        "/api/hub/messages",
        json={
            "from_orion": "orion-alpha",
            "to_orion": "orion-beta",
            "message_type": "direct",
            "content": "Test for read marking",
        },
    )
    msg_id = send_resp.get_json()["message"]["id"]

    # Mark as read
    response = client.post(
        f"/api/hub/messages/{msg_id}/read",
        json={"read_by": "orion-beta"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["message"]["read"] is True


def test_message_stats(client):
    """Test getting message statistics."""
    # Send some messages
    client.post("/api/hub/messages", json={"from_orion": "a", "to_orion": "b", "message_type": "direct", "content": "1"})
    client.post("/api/hub/messages", json={"from_orion": "a", "to_orion": "*", "message_type": "broadcast", "content": "2"})

    response = client.get("/api/hub/messages/stats")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "stats" in payload
    assert payload["stats"]["total_messages"] >= 2


def test_get_conversation(client):
    """Test getting conversation between two ORIONs."""
    # Send messages both ways
    client.post("/api/hub/messages", json={"from_orion": "orion-a", "to_orion": "orion-b", "message_type": "direct", "content": "Hi B"})
    client.post("/api/hub/messages", json={"from_orion": "orion-b", "to_orion": "orion-a", "message_type": "direct", "content": "Hi A"})

    response = client.get("/api/hub/conversation/orion-a/orion-b")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "messages" in payload
    assert len(payload["messages"]) >= 2


# ============================================
# Human Notification Tests
# ============================================


def test_send_human_notification(client):
    """Test sending a notification."""
    response = client.post(
        "/api/hub/human-notifications",
        json={
            "title": "Test Notification",
            "message": "This is a test notification",
            "level": "info",
            "source": "test",
            "channels": ["dashboard"],
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "notification" in payload
    assert payload["notification"]["title"] == "Test Notification"


def test_list_human_notifications(client):
    """Test listing notifications."""
    # Send a notification first
    client.post(
        "/api/hub/human-notifications",
        json={
            "title": "List Test",
            "message": "Testing list",
            "level": "warning",
            "source": "test",
            "channels": ["dashboard"],
        },
    )

    response = client.get("/api/hub/human-notifications")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "notifications" in payload
    assert "stats" in payload
    assert len(payload["notifications"]) >= 1


def test_human_notification_levels(client):
    """Test different notification levels."""
    levels = ["info", "success", "warning", "error", "critical"]
    for level in levels:
        response = client.post(
            "/api/hub/human-notifications",
            json={
                "title": f"Level Test {level}",
                "message": f"Testing {level}",
                "level": level,
                "source": "test",
                "channels": ["dashboard"],
                "bypass_rate_limit": True,
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["success"] is True, f"Failed for level {level}: {payload}"
        assert payload["notification"]["level"] == level


def test_acknowledge_human_notification(client):
    """Test acknowledging a notification."""
    # Send a notification
    send_resp = client.post(
        "/api/hub/human-notifications",
        json={
            "title": "Ack Test",
            "message": "Test acknowledgment",
            "level": "warning",
            "source": "test",
            "channels": ["dashboard"],
            "bypass_rate_limit": True,
        },
    )
    notif_id = send_resp.get_json()["notification"]["id"]

    # Acknowledge it
    response = client.post(
        f"/api/hub/human-notifications/{notif_id}/acknowledge",
        json={"acknowledged_by": "tester"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["notification"]["acknowledged"] is True
    assert payload["notification"]["acknowledged_by"] == "tester"


def test_urgent_human_notification(client):
    """Test sending urgent notification."""
    response = client.post(
        "/api/hub/human-notifications/urgent",
        json={
            "title": "URGENT!",
            "message": "This is urgent",
            "source": "test",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["notification"]["level"] == "critical"


def test_help_human_notification(client):
    """Test help request notification."""
    response = client.post(
        "/api/hub/human-notifications/help",
        json={
            "from_orion": "orion-alpha",
            "help_type": "security",
            "description": "Need help with security audit",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True, f"Got: {payload}"
    assert "Help Needed" in payload["notification"]["title"]


def test_blocked_task_human_notification(client):
    """Test blocked task notification."""
    response = client.post(
        "/api/hub/human-notifications/blocked",
        json={
            "task_id": "task-123",
            "task_title": "Implement feature",
            "blocker": "Waiting for API key",
            "orion_id": "orion-alpha",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True, f"Got: {payload}"
    assert "Blocked" in payload["notification"]["title"]


def test_milestone_human_notification(client):
    """Test milestone notification."""
    response = client.post(
        "/api/hub/human-notifications/milestone",
        json={
            "milestone": "Phase 1 Complete",
            "details": "All tests passing",
            "source": "orion-alpha",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True, f"Got: {payload}"
    assert "Milestone" in payload["notification"]["title"]


def test_human_notification_stats(client):
    """Test getting notification stats."""
    # Send some notifications
    client.post("/api/hub/human-notifications", json={"title": "1", "message": "m1", "level": "info", "source": "a", "channels": ["dashboard"], "bypass_rate_limit": True})
    client.post("/api/hub/human-notifications", json={"title": "2", "message": "m2", "level": "warning", "source": "b", "channels": ["dashboard"], "bypass_rate_limit": True})

    response = client.get("/api/hub/human-notifications/stats")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "stats" in payload
    assert payload["stats"]["total"] >= 2
    assert "channels_available" in payload["stats"]


def test_automation_terminal_execute_and_status(client):
    run = client.post(
        "/api/automation/execute",
        json={
            "task_type": "terminal",
            "action": "execute",
            "params": {"command": "pwd"},
            "session_id": "term-a",
            "owner_id": "tester-a",
        },
    )
    assert run.status_code == 200
    body = run.get_json()
    assert body["success"] is True
    assert body["status"] == "completed"
    task_id = body["task_id"]
    assert task_id

    status = client.get(f"/api/automation/status/{task_id}")
    assert status.status_code == 200
    payload = status.get_json()
    assert payload["success"] is True
    assert payload["task"]["task_id"] == task_id
    assert payload["task"]["session_id"] == "term-a"


def test_automation_terminal_command_allowlist_denies_unknown_binary(client):
    run = client.post(
        "/api/automation/execute",
        json={
            "task_type": "terminal",
            "action": "execute",
            "params": {"command": "uname -a"},
            "session_id": "term-a",
            "owner_id": "tester-a",
        },
    )
    assert run.status_code == 200
    payload = run.get_json()
    assert payload["success"] is False
    assert payload["error_code"] in {"COMMAND_DENIED", "INVALID_COMMAND"}


def test_automation_execute_debounces_passive_browser_status(client, monkeypatch):
    monkeypatch.setattr(monitor_app, "AUTOMATION_PASSIVE_POLL_COOLDOWN_SEC", 60.0)
    calls = {"count": 0}

    def fake_browser(action, params):
        calls["count"] += 1
        return {"success": True, "action": action, "params": dict(params or {})}

    monkeypatch.setattr(monitor_app, "_automation_execute_browser", fake_browser)
    payload = {
        "task_type": "browser",
        "action": "status",
        "params": {"route_mode": "direct"},
        "session_id": "browser-main",
        "owner_id": "tester-a",
    }
    first = client.post("/api/automation/execute", json=payload)
    assert first.status_code == 200
    first_data = first.get_json()
    assert first_data["success"] is True
    assert first_data["status"] == "completed"

    second = client.post("/api/automation/execute", json=payload)
    assert second.status_code == 200
    second_data = second.get_json()
    assert second_data["success"] is True
    assert second_data["status"] == "skipped"
    assert second_data["reason"] == "debounced"
    assert calls["count"] == 1


def test_automation_session_lease_conflict(client):
    first = client.post(
        "/api/automation/sessions/lease",
        json={"action": "acquire", "session_id": "shared", "owner_id": "owner-a"},
    )
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["success"] is True

    second = client.post(
        "/api/automation/sessions/lease",
        json={"action": "acquire", "session_id": "shared", "owner_id": "owner-b"},
    )
    assert second.status_code == 409
    second_payload = second.get_json()
    assert second_payload["success"] is False
    assert second_payload["error_code"] == "LEASE_CONFLICT"


def test_automation_capabilities_endpoint(client):
    response = client.get("/api/automation/capabilities")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "capabilities" in payload
    assert "runtime" in payload
    assert "terminal" in payload["capabilities"]


def test_automation_schedule_create_and_list(client):
    created = client.post(
        "/api/automation/schedule",
        json={
            "schedule_id": "sched-test-1",
            "task_type": "terminal",
            "action": "execute",
            "params": {"command": "pwd"},
            "interval_sec": 30,
            "enabled": True,
        },
    )
    assert created.status_code == 200
    create_payload = created.get_json()
    assert create_payload["success"] is True
    assert create_payload["schedule"]["schedule_id"] == "sched-test-1"
    assert create_payload["schedule"]["next_run_at"] > 0

    listed = client.get("/api/automation/schedules")
    assert listed.status_code == 200
    list_payload = listed.get_json()
    assert list_payload["success"] is True
    assert any(item.get("schedule_id") == "sched-test-1" for item in list_payload["schedules"])
