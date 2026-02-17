"""Basic regression tests for self-learning v2 pipeline."""

import atexit
import sys
import uuid
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.memory.storage_v2 as storage_v2_module
import src.memory.proposal_engine as proposal_engine_module
import src.memory.experiment_executor as experiment_executor_module
import src.memory.outcome_verifier as outcome_verifier_module
import src.memory.policy_bandit as policy_bandit_module
import src.memory.learning_loop as learning_loop_module

from src.memory.storage_v2 import get_storage_v2
from src.memory.proposal_engine import get_proposal_engine_v2
from src.memory.experiment_executor import get_experiment_executor
from src.memory.outcome_verifier import get_outcome_verifier
from src.memory.learning_policy import (
    update_learning_policy,
    get_learning_policy_state,
    apply_policy_drift_guard,
)
from src.memory.learning_loop import get_learning_loop, start_learning_resilient
from src.memory.cafe_calibrator import get_cafe_calibrator
from src.memory.policy_bandit import get_policy_bandit

_TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="nexus_learning_v2_test_")


def _setup_isolated_runtime() -> None:
    data_dir = Path(_TEST_RUNTIME_DIR) / "data"
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    storage_v2_module._storage_v2 = storage_v2_module.LearningStorageV2(base_path=str(data_dir))
    proposal_engine_module._engine_v2 = None
    experiment_executor_module._executor = None
    outcome_verifier_module._verifier = None
    policy_bandit_module._bandit = None
    learning_loop_module._loop = learning_loop_module.LearningLoop(base_path=str(state_dir))


def _teardown_isolated_runtime() -> None:
    try:
        shutil.rmtree(_TEST_RUNTIME_DIR)
    except Exception:
        pass


_setup_isolated_runtime()
atexit.register(_teardown_isolated_runtime)


def test_event_to_proposal_v2_flow():
    storage = get_storage_v2()
    engine = get_proposal_engine_v2()

    event_id = storage.record_learning_event(
        {
            "source": "unit_test",
            "event_type": "scan_insight",
            "content": "High confidence optimization idea",
            "title": "Unit proposal",
            "novelty_score": 0.9,
            "value_score": 0.9,
            "risk_score": 0.1,
            "confidence": 0.9,
        }
    )
    assert event_id

    proposals = engine.generate_from_events(
        storage.list_learning_events(limit=20),
        limit=20,
        include_non_production=True,
    )
    assert isinstance(proposals, list)


def test_cafe_scores_attached_to_event():
    if os.getenv("ENABLE_CAFE_LOOP", "true").strip().lower() != "true":
        return
    storage = get_storage_v2()
    event_id = storage.record_learning_event(
        {
            "source": "unit_test",
            "event_type": "scan_insight",
            "content": "CAFEScoring sanity check",
            "title": "Cafe event",
            "novelty_score": 0.7,
            "value_score": 0.8,
            "risk_score": 0.2,
            "confidence": 0.9,
        }
    )
    assert event_id
    events = storage.list_learning_events(limit=20)
    matched = [e for e in events if e.get("id") == event_id]
    assert matched
    assert isinstance(matched[0].get("cafe"), dict)


def test_non_production_events_filtered_by_default():
    engine = get_proposal_engine_v2()
    suffix = uuid.uuid4().hex[:10]
    events = [
        {
            "id": f"evt_nonprod_{suffix}",
            "source": "unit_test",
            "event_type": "scan_insight",
            "content": f"non production filtered event {suffix}",
            "title": "Unit-only event",
            "novelty_score": 0.95,
            "value_score": 0.95,
            "risk_score": 0.1,
            "confidence": 0.95,
        }
    ]
    proposals_default = engine.generate_from_events(events, limit=5)
    assert proposals_default == []
    proposals_override = engine.generate_from_events(events, limit=5, include_non_production=True)
    assert isinstance(proposals_override, list)


def test_execute_verify_policy_update_flow():
    engine = get_proposal_engine_v2()
    executor = get_experiment_executor()
    verifier = get_outcome_verifier()

    approved = [p for p in engine.list_pending() if p.get("status") == "approved"]
    if not approved:
        return

    result = executor.execute_proposal(str(approved[0].get("id")), mode="safe")
    assert result.get("ok") is True

    verify = verifier.verify_experiment(str(result.get("run_id")))
    assert verify.get("ok") is True
    assert "pending_recheck" in verify

    evidence = verify.get("evidence", {}) if isinstance(verify.get("evidence"), dict) else {}
    verdict = evidence.get("verdict", "inconclusive")
    assert verdict in {"win", "loss", "inconclusive"}
    delta = evidence.get("delta", {}) if isinstance(evidence.get("delta"), dict) else {}
    assert "avg_duration_ms" in delta
    assert "total_errors" in delta
    execution = evidence.get("execution", {}) if isinstance(evidence.get("execution"), dict) else {}
    assert "duration_ms" in execution
    assert "estimated_cost_usd" in execution
    assert "pending_recheck" in evidence
    assert "attempt" in evidence
    state = update_learning_policy({"verdict": verdict})
    assert isinstance(state, dict)
    assert isinstance(get_learning_policy_state(), dict)


def test_status_exposes_execution_guardrail():
    report = get_learning_loop().get_status_report()
    guardrail = report.get("execution_guardrail", {}) if isinstance(report, dict) else {}
    assert isinstance(guardrail, dict)
    assert "canary_enabled" in guardrail
    assert "normal_mode_runs_last_hour" in guardrail
    assert "normal_mode_cooldown_remaining_sec" in guardrail
    assert "anomaly_autopause_enabled" in guardrail
    assert "anomaly_last_reason" in guardrail
    assert "anomaly_pressure_scale" in guardrail
    assert "anomaly_policy_penalty_applied" in guardrail
    assert "anomaly_recovery_wins_streak" in guardrail
    assert "anomaly_recovery_wins_required" in guardrail
    assert "rollback_lock_remaining_sec" in guardrail
    assert "verification_loss_streak" in guardrail


def test_calibrator_emits_verification_thresholds():
    calibrator = get_cafe_calibrator()
    evidences = []
    for i in range(8):
        evidences.append(
            {
                "id": f"evd_unit_cal_{i}",
                "verdict": "inconclusive" if i < 5 else "win",
                "model": "claude-sonnet-4-5",
                "delta": {
                    "health_score": 0.25 if i < 5 else 0.9,
                    "avg_duration_ms": 40.0 if i < 5 else -120.0,
                    "success_rate": 0.004 if i < 5 else 0.03,
                },
            }
        )

    result = calibrator.calibrate(evidences=evidences)
    thresholds = result.get("verification_thresholds", {}) if isinstance(result, dict) else {}
    assert isinstance(thresholds, dict)
    assert "health_improve" in thresholds
    assert "pending_recheck_confidence_max" in thresholds
    assert 0.6 <= float(thresholds.get("health_improve", 0.0)) <= 1.2
    assert 0.52 <= float(thresholds.get("pending_recheck_confidence_max", 0.0)) <= 0.68


def test_status_exposes_adaptive_daily_cadence():
    report = get_learning_loop().get_status_report()
    daily = report.get("daily_self_learning", {}) if isinstance(report, dict) else {}
    assert isinstance(daily, dict)
    assert "effective_interval_seconds" in daily
    assert "cadence_reason" in daily
    assert "cadence_min_switch_seconds" in daily
    assert "cadence_last_reason" in daily
    assert int(daily.get("effective_interval_seconds", 0) or 0) >= int(daily.get("min_interval_seconds", 0) or 0)


def test_policy_update_with_guardrail_weighting():
    bandit = get_policy_bandit()
    selected = {"approve_threshold": "0.82", "scan_min_score": "6.0", "focus_policy": "reliability_first"}

    before = bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 1.0)
    update_learning_policy(
        {
            "verdict": "loss",
            "selected": selected,
            "guardrail_penalty": "cooldown_active",
            "recovery_latency_seconds": 3600,
            "guardrail_pressure": 0.8,
        }
    )
    after = bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 1.0)
    assert abs(float(after) - float(before) - 3.5) < 1e-6


def test_policy_update_handles_non_numeric_payload():
    state = update_learning_policy(
        {
            "verdict": "loss",
            "selected": {"approve_threshold": "0.82"},
            "weight": "invalid",
            "guardrail_penalty": "cooldown_active",
            "recovery_latency_seconds": "invalid",
            "guardrail_pressure": "invalid",
            "metadata": {"source": "unit_test"},
        }
    )
    assert isinstance(state, dict)


def test_guardrail_policy_penalty_applied_once_per_pause():
    loop = get_learning_loop()
    bandit = get_policy_bandit()
    selected = {"approve_threshold": "0.82", "scan_min_score": "6.0", "focus_policy": "reliability_first"}
    bandit.state["selected"] = selected

    before = float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 1.0))
    loop.enable_policy_bandit = True
    loop.anomaly_policy_penalty_applied = False
    loop.anomaly_autopause_started_at = (datetime.now() - timedelta(seconds=120)).isoformat()

    guardrail = {
        "paused": True,
        "reason": "cooldown_active",
        "dynamic_health_threshold": 80.0,
    }
    first = loop._apply_guardrail_policy_penalty_if_needed(guardrail)
    middle = float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 1.0))
    second = loop._apply_guardrail_policy_penalty_if_needed(guardrail)
    after = float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 1.0))

    assert first is True
    assert second is False
    assert middle > before
    assert after == middle


def test_autopause_reason_refreshes_started_at():
    loop = get_learning_loop()
    loop.anomaly_guard_enabled = True
    loop.anomaly_autopause_until = None
    loop.anomaly_autopause_started_at = "2000-01-01T00:00:00"
    loop.anomaly_policy_penalty_applied = True
    loop.anomaly_recovery_wins_streak = 0
    loop.no_improvement_streak = 0

    result = loop._should_autopause_v2_pipeline(
        health={"open_issues": 5, "health_score": 35},
        errors_count=0,
    )
    started_at = loop._parse_time(loop.anomaly_autopause_started_at)

    assert result.get("paused") is True
    assert started_at is not None
    assert (datetime.now() - started_at).total_seconds() < 10
    assert loop.anomaly_policy_penalty_applied is False


def test_status_exposes_recovery_playbook():
    report = get_learning_loop().get_status_report()
    playbook = report.get("recovery_playbook", {}) if isinstance(report, dict) else {}
    assert isinstance(playbook, dict)
    assert "autopause_active" in playbook
    assert "rollback_lock_active" in playbook
    assert "actions" in playbook


def test_resilient_entrypoint_exposed():
    assert callable(start_learning_resilient)


def test_policy_drift_guard_dry_run_and_apply():
    bandit = get_policy_bandit()
    arm = bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {})
    arm["a"] = 900.0
    arm["b"] = 5.0
    before = (float(arm.get("a", 0.0)), float(arm.get("b", 0.0)))

    dry = apply_policy_drift_guard({"max_posterior_total": 300.0, "min_mean": 0.12, "max_mean": 0.88, "shrink_ratio": 0.4, "dry_run": True})
    after_dry = (
        float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("a", 0.0)),
        float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 0.0)),
    )
    assert int(dry.get("arms_adjusted", 0) or 0) >= 1
    assert after_dry == before

    apply_summary = apply_policy_drift_guard({"max_posterior_total": 300.0, "min_mean": 0.12, "max_mean": 0.88, "shrink_ratio": 0.4, "dry_run": False})
    after_apply = (
        float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("a", 0.0)),
        float(bandit.get_state().get("arms", {}).get("approve_threshold", {}).get("0.82", {}).get("b", 0.0)),
    )
    assert int(apply_summary.get("arms_adjusted", 0) or 0) >= 1
    assert after_apply[0] < before[0]
    assert 1.0 <= after_apply[1] <= before[1]


if __name__ == "__main__":
    test_event_to_proposal_v2_flow()
    test_cafe_scores_attached_to_event()
    test_non_production_events_filtered_by_default()
    test_execute_verify_policy_update_flow()
    test_status_exposes_execution_guardrail()
    test_calibrator_emits_verification_thresholds()
    test_status_exposes_adaptive_daily_cadence()
    test_policy_update_with_guardrail_weighting()
    test_policy_update_handles_non_numeric_payload()
    test_guardrail_policy_penalty_applied_once_per_pause()
    test_autopause_reason_refreshes_started_at()
    test_status_exposes_recovery_playbook()
    test_resilient_entrypoint_exposed()
    test_policy_drift_guard_dry_run_and_apply()
    print("test_learning_v2: OK")
