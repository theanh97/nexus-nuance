"""Basic regression tests for self-learning v2 pipeline."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.memory.storage_v2 import get_storage_v2
from src.memory.proposal_engine import get_proposal_engine_v2
from src.memory.experiment_executor import get_experiment_executor
from src.memory.outcome_verifier import get_outcome_verifier
from src.memory.learning_policy import update_learning_policy, get_learning_policy_state
from src.memory.learning_loop import get_learning_loop


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


if __name__ == "__main__":
    test_event_to_proposal_v2_flow()
    test_non_production_events_filtered_by_default()
    test_execute_verify_policy_update_flow()
    test_status_exposes_execution_guardrail()
    print("test_learning_v2: OK")
