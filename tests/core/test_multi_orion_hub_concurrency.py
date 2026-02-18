from src.core.multi_orion_hub import MultiOrionHub


def test_multi_orion_hub_version_conflict_on_create_task(tmp_path):
    state_path = tmp_path / "multi_orion_state.json"
    hub = MultiOrionHub(state_path=str(state_path))

    first = hub.create_task_safe(
        title="Task A",
        description="first",
        priority="high",
        expected_version=0,
    )
    assert first["success"] is True
    assert first["version"] >= 1

    stale = hub.create_task_safe(
        title="Task B",
        description="stale",
        expected_version=0,
    )
    assert stale["success"] is False
    assert stale["error_code"] == "VERSION_CONFLICT"


def test_multi_orion_hub_lease_conflict_and_release(tmp_path):
    state_path = tmp_path / "multi_orion_state.json"
    hub = MultiOrionHub(state_path=str(state_path))

    created = hub.create_task_safe(title="Task lease", description="lease", priority="medium")
    assert created["success"] is True
    task_id = created["task"]["id"]

    first_claim = hub.claim_task(task_id=task_id, owner_id="orion:A", lease_sec=120)
    assert first_claim["success"] is True
    lease_token = first_claim["lease"]["lease_token"]

    second_claim = hub.claim_task(task_id=task_id, owner_id="orion:B", lease_sec=120)
    assert second_claim["success"] is False
    assert second_claim["error_code"] == "LEASE_CONFLICT"

    release = hub.release_task_lease(task_id=task_id, owner_id="orion:A", lease_token=lease_token, next_status="todo")
    assert release["success"] is True

    third_claim = hub.claim_task(task_id=task_id, owner_id="orion:B", lease_sec=120)
    assert third_claim["success"] is True


def test_multi_orion_hub_update_requires_lease_owner(tmp_path):
    state_path = tmp_path / "multi_orion_state.json"
    hub = MultiOrionHub(state_path=str(state_path))

    created = hub.create_task_safe(title="Task update", description="update", priority="medium")
    task_id = created["task"]["id"]

    claim = hub.claim_task(task_id=task_id, owner_id="orion:owner", lease_sec=120)
    assert claim["success"] is True

    wrong_update = hub.update_task_status_safe(
        task_id=task_id,
        new_status="review",
        owner_id="orion:other",
        lease_token="wrong-token",
    )
    assert wrong_update["success"] is False
    assert wrong_update["error_code"] == "LEASE_CONFLICT"

    right_update = hub.update_task_status_safe(
        task_id=task_id,
        new_status="review",
        owner_id="orion:owner",
        lease_token=claim["lease"]["lease_token"],
    )
    assert right_update["success"] is True
    assert right_update["task"]["status"] == "review"


def test_multi_orion_hub_orion_heartbeat_updates_lease(tmp_path):
    state_path = tmp_path / "multi_orion_state.json"
    hub = MultiOrionHub(state_path=str(state_path))

    reg = hub.register_orion("orion-1", {"region": "local"})
    assert reg["success"] is True

    hb = hub.heartbeat_orion("orion-1", status="active", metadata={"queue_depth": 2})
    assert hb["success"] is True
    assert hb["orion"]["status"] == "active"
    assert hb["orion"]["metadata"]["queue_depth"] == 2

    snapshot = hub.get_snapshot()
    assert "orion-1" in snapshot["orions"]
    assert snapshot["orions"]["orion-1"].get("lease_expires_at")


def test_multi_orion_hub_force_release_expired_lease(tmp_path):
    state_path = tmp_path / "multi_orion_state.json"
    hub = MultiOrionHub(state_path=str(state_path))
    created = hub.create_task_safe(title="Task expired lease", description="lease", priority="medium")
    task_id = created["task"]["id"]

    claim = hub.claim_task(task_id=task_id, owner_id="orion:A", lease_sec=120)
    assert claim["success"] is True

    with hub._exclusive_state() as state:
        task = next(t for t in state["tasks"] if t["id"] == task_id)
        task["lease_expires_at"] = "2001-01-01T00:00:00"
        hub._commit_state(state)

    release = hub.release_task_lease(
        task_id=task_id,
        owner_id="orion:A",
        lease_token="",
        next_status="todo",
        force_if_expired=True,
        actor_id="autopilot",
        reason="stale_lease_reclaim",
    )
    assert release["success"] is True
    assert release["task"]["status"] == "todo"
