"""Lightweight Thompson Sampling policy tuner for learning loop."""

from datetime import datetime
from typing import Any, Dict, Optional
import random

from .storage_v2 import get_storage_v2


class PolicyBandit:
    DEFAULT_STATE = {
        "arms": {
            "approve_threshold": {
                "0.78": {"a": 1.0, "b": 1.0},
                "0.82": {"a": 1.0, "b": 1.0},
                "0.86": {"a": 1.0, "b": 1.0},
            },
            "scan_min_score": {
                "5.8": {"a": 1.0, "b": 1.0},
                "6.0": {"a": 1.0, "b": 1.0},
                "6.2": {"a": 1.0, "b": 1.0},
            },
            "focus_policy": {
                "reliability_first": {"a": 1.0, "b": 1.0},
                "execution_first": {"a": 1.0, "b": 1.0},
                "learning_first": {"a": 1.0, "b": 1.0},
            },
        },
        "selected": {
            "approve_threshold": "0.82",
            "scan_min_score": "6.0",
            "focus_policy": "reliability_first",
        },
        "history": [],
    }

    def __init__(self):
        self.storage = get_storage_v2()
        self.state = self.storage.get_policy_state() or {}
        if "arms" not in self.state:
            self.state = {k: (v.copy() if isinstance(v, dict) else v) for k, v in self.DEFAULT_STATE.items()}

    def _sample_arm(self, family: str) -> str:
        family_state = self.state.get("arms", {}).get(family, {})
        best_arm = None
        best_sample = -1.0
        for arm, beta in family_state.items():
            a = max(1e-6, float(beta.get("a", 1.0)))
            b = max(1e-6, float(beta.get("b", 1.0)))
            sample = random.betavariate(a, b)
            if sample > best_sample:
                best_sample = sample
                best_arm = arm
        return str(best_arm)

    def select_policy(self) -> Dict[str, str]:
        selected = {
            "approve_threshold": self._sample_arm("approve_threshold"),
            "scan_min_score": self._sample_arm("scan_min_score"),
            "focus_policy": self._sample_arm("focus_policy"),
        }
        self.state["selected"] = selected
        self.state["selected_at"] = datetime.now().isoformat()
        self.storage.save_policy_state(self.state)
        return selected

    def update(self, verdict: str, selected: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        if verdict not in {"win", "loss", "inconclusive"}:
            return self.state
        if verdict == "inconclusive":
            return self.state
        chosen = selected or self.state.get("selected", {})
        reward = 1.0 if verdict == "win" else 0.0

        for family, arm in chosen.items():
            family_state = self.state.get("arms", {}).get(family, {})
            if arm not in family_state:
                continue
            if reward >= 1.0:
                family_state[arm]["a"] = float(family_state[arm].get("a", 1.0)) + 1.0
            else:
                family_state[arm]["b"] = float(family_state[arm].get("b", 1.0)) + 1.0

        history = self.state.get("history", [])
        history.append({"ts": datetime.now().isoformat(), "selected": chosen, "verdict": verdict})
        self.state["history"] = history[-1000:]
        self.storage.save_policy_state(self.state)
        return self.state

    def get_state(self) -> Dict[str, Any]:
        return self.state


_bandit: Optional[PolicyBandit] = None


def get_policy_bandit() -> PolicyBandit:
    global _bandit
    if _bandit is None:
        _bandit = PolicyBandit()
    return _bandit
