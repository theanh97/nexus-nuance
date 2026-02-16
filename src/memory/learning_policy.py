"""Convenience API for selecting/updating learning policy."""

from typing import Any, Dict

from .policy_bandit import get_policy_bandit


def select_learning_policy() -> Dict[str, str]:
    return get_policy_bandit().select_policy()


def update_learning_policy(feedback: Dict[str, Any]) -> Dict[str, Any]:
    verdict = str((feedback or {}).get("verdict", "inconclusive")).strip().lower()
    selected = (feedback or {}).get("selected")
    return get_policy_bandit().update(verdict=verdict, selected=selected)


def get_learning_policy_state() -> Dict[str, Any]:
    return get_policy_bandit().get_state()
