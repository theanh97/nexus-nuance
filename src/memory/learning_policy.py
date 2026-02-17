"""Convenience API for selecting/updating learning policy."""

from typing import Any, Dict

from .policy_bandit import get_policy_bandit


def select_learning_policy() -> Dict[str, str]:
    return get_policy_bandit().select_policy()


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def update_learning_policy(feedback: Dict[str, Any]) -> Dict[str, Any]:
    payload = feedback if isinstance(feedback, dict) else {}
    verdict = str(payload.get("verdict", "inconclusive")).strip().lower()
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else None
    bandit = get_policy_bandit()
    base_weight = _safe_float(payload.get("weight", 1.0) or 1.0, 1.0)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    guardrail_states = {
        "autopause",
        "rollback_lock",
        "cooldown_active",
        "open_issues_threshold_exceeded",
        "health_score_below_threshold",
        "runtime_error_spike",
    }
    guardrail_penalty = str(payload.get("guardrail_penalty", "")).strip().lower()
    if guardrail_penalty in guardrail_states:
        recovery_latency_seconds = max(0.0, _safe_float(payload.get("recovery_latency_seconds", 0.0) or 0.0, 0.0))
        pressure = max(0.0, _safe_float(payload.get("guardrail_pressure", 0.0) or 0.0, 0.0))
        penalty_weight = 1.0 + min(2.5, (recovery_latency_seconds / 1800.0) + pressure)
        # Guardrail incidents are recorded as a single weighted loss event.
        merged_metadata = dict(metadata)
        merged_metadata.update(
            {
                "guardrail_penalty": guardrail_penalty,
                "recovery_latency_seconds": recovery_latency_seconds,
                "guardrail_pressure": pressure,
            }
        )
        return bandit.update(
            verdict="loss",
            selected=selected,
            weight=max(base_weight, penalty_weight),
            metadata=merged_metadata,
        )

    return bandit.update(
        verdict=verdict,
        selected=selected,
        weight=base_weight,
        metadata=metadata,
    )


def get_learning_policy_state() -> Dict[str, Any]:
    return get_policy_bandit().get_state()


def apply_policy_drift_guard(config: Dict[str, Any]) -> Dict[str, Any]:
    payload = config if isinstance(config, dict) else {}
    return get_policy_bandit().apply_drift_guard(
        max_posterior_total=_safe_float(payload.get("max_posterior_total", 400.0), 400.0),
        min_mean=_safe_float(payload.get("min_mean", 0.08), 0.08),
        max_mean=_safe_float(payload.get("max_mean", 0.92), 0.92),
        shrink_ratio=_safe_float(payload.get("shrink_ratio", 0.35), 0.35),
        dry_run=bool(payload.get("dry_run", False)),
    )
