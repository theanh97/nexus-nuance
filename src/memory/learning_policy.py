"""Convenience API for selecting/updating learning policy."""

from typing import Any, Dict

from .policy_bandit import get_policy_bandit


def select_learning_policy() -> Dict[str, str]:
    return get_policy_bandit().select_policy()


def update_learning_policy(feedback: Dict[str, Any]) -> Dict[str, Any]:
    payload = feedback if isinstance(feedback, dict) else {}
    verdict = str(payload.get("verdict", "inconclusive")).strip().lower()
    selected = payload.get("selected")
    state = get_policy_bandit().update(verdict=verdict, selected=selected)

    guardrail_penalty = str(payload.get("guardrail_penalty", "")).strip().lower()
    if guardrail_penalty in {"autopause", "rollback_lock", "cooldown_active"}:
        # Treat guardrail-triggered pauses as loss-like feedback so policy converges toward safer arms.
        state = get_policy_bandit().update(verdict="loss", selected=selected)

    return state


def get_learning_policy_state() -> Dict[str, Any]:
    return get_policy_bandit().get_state()
