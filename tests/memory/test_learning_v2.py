"""Basic regression tests for self-learning v2 pipeline."""

import sys
import uuid
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.memory.storage_v2 import get_storage_v2
from src.memory.proposal_engine import get_proposal_engine_v2
from src.memory.experiment_executor import get_experiment_executor
from src.memory.outcome_verifier import get_outcome_verifier
from src.memory.learning_policy import update_learning_policy, get_learning_policy_state
from src.memory.learning_loop import get_learning_loop
from src.memory.cafe_calibrator import get_cafe_calibrator


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


if __name__ == "__main__":
    test_event_to_proposal_v2_flow()
    test_cafe_scores_attached_to_event()
    test_non_production_events_filtered_by_default()
    test_execute_verify_policy_update_flow()
    test_status_exposes_execution_guardrail()
    test_calibrator_emits_verification_thresholds()
    test_status_exposes_adaptive_daily_cadence()
    print("test_learning_v2: OK")
