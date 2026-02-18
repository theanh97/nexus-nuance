# Lane B Integration Gate Handoff (2026-02-18)

## Scope lock
This lane only changes Orion/Monitor runtime stability and does not modify:
- `src/brain/*`
- `scripts/brain_daemon.py`
- `src/api/brain_api.py`
- `src/loop/autonomous_loop.py`

## Files changed (Lane B)
- `monitor/app.py`
- `run_system.py`
- `src/agents/orion.py`
- `src/core/multi_orion_hub.py`
- `tests/core/test_orion_token_guard.py`
- `tests/core/test_multi_orion_hub_concurrency.py`
- `tests/core/test_monitor_control_plane_api.py`
- `tests/core/test_lane_ownership_contract.py`

## Additive contract changes
- Orion cycle payload adds:
  - `deploy_outcome`
  - `progress_class`
- Hub release API supports optional:
  - `force_if_expired`
  - `actor_id`
  - `reason`
- Hub autopilot status adds:
  - `active_executor`
  - `executor_conflict`
- Central lock acquire response adds:
  - `expired_lock_reclaimed`

## Behavior fixes included
- Skip deploy due to unchanged fingerprint is no longer marked as deployed success.
- Stale lease reclaim supports force release for expired lease.
- Autopilot background execution no longer depends on `request.host_url`.
- Single active autopilot executor (`policy_loop` or `hub_loop`) to avoid double execution.
- Lock acquire now reclaims expired lock before conflict check.
- More conservative default thresholds to reduce command storm:
  - `GUARDIAN_STUCK_THRESHOLD_SEC=180` (default)
  - `MONITOR_STUCK_THRESHOLD_SEC=240` (default)
  - `MONITOR_RECOVERY_COOLDOWN_SEC=90` (default)

## Validation run
Executed on lane B branch:
- `pytest -q tests/core/test_orion_token_guard.py tests/core/test_multi_orion_hub_concurrency.py tests/core/test_monitor_control_plane_api.py tests/core/test_lane_ownership_contract.py`
  - Result: `96 passed, 1 skipped`
- `pytest -q tests/core`
  - Result: `107 passed, 1 skipped`

## Integration gate commands
Run after merging Lane A, then Lane B:
```bash
pytest -q tests/core
pytest -q tests/core/test_orion_token_guard.py tests/core/test_multi_orion_hub_concurrency.py tests/core/test_monitor_control_plane_api.py
```

Optional ownership check (Lane B only):
```bash
export LANE_B_DIFF_BASE=<base_ref>
pytest -q tests/core/test_lane_ownership_contract.py
```

## Merge order recommendation
1. Merge Lane A (Brain stack)
2. Run contract/core tests
3. Merge Lane B
4. Run contract/core tests again
5. Start soak and watch recovery/unstick metrics
