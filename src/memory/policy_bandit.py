"""Lightweight Thompson Sampling policy tuner for learning loop."""

import copy
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
        stored_state = self.storage.get_policy_state()
        self.state = stored_state if isinstance(stored_state, dict) else {}
        if "arms" not in self.state:
            self.state = copy.deepcopy(self.DEFAULT_STATE)

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

    def update(
        self,
        verdict: str,
        selected: Optional[Dict[str, str]] = None,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if verdict not in {"win", "loss", "inconclusive"}:
            return self.state
        if verdict == "inconclusive":
            return self.state
        chosen = selected if isinstance(selected, dict) else self.state.get("selected", {})
        if not isinstance(chosen, dict):
            chosen = {}
        sanitized_chosen = {str(family): str(arm) for family, arm in chosen.items()}
        reward = 1.0 if verdict == "win" else 0.0
        try:
            update_weight = float(weight)
        except (TypeError, ValueError):
            update_weight = 1.0
        update_weight = max(0.1, min(4.0, update_weight))

        for family, arm in sanitized_chosen.items():
            family_state = self.state.get("arms", {}).get(family, {})
            if arm not in family_state:
                continue
            if reward >= 1.0:
                family_state[arm]["a"] = float(family_state[arm].get("a", 1.0)) + update_weight
            else:
                family_state[arm]["b"] = float(family_state[arm].get("b", 1.0)) + update_weight

        history = self.state.get("history", [])
        history.append(
            {
                "ts": datetime.now().isoformat(),
                "selected": sanitized_chosen,
                "verdict": verdict,
                "weight": round(update_weight, 4),
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
        self.state["history"] = history[-1000:]
        self.storage.save_policy_state(self.state)
        return self.state

    def get_state(self) -> Dict[str, Any]:
        return self.state

    def apply_drift_guard(
        self,
        max_posterior_total: float = 400.0,
        min_mean: float = 0.08,
        max_mean: float = 0.92,
        shrink_ratio: float = 0.35,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Prevent runaway overconfidence by shrinking extreme/overgrown posteriors.
        Returns adjustment summary for observability.
        """
        try:
            total_cap = max(50.0, float(max_posterior_total))
        except (TypeError, ValueError):
            total_cap = 400.0
        try:
            lo = max(0.01, min(0.49, float(min_mean)))
        except (TypeError, ValueError):
            lo = 0.08
        try:
            hi = min(0.99, max(0.51, float(max_mean)))
        except (TypeError, ValueError):
            hi = 0.92
        try:
            shrink = min(0.9, max(0.05, float(shrink_ratio)))
        except (TypeError, ValueError):
            shrink = 0.35

        families_adjusted = 0
        arms_adjusted = 0
        detail: Dict[str, int] = {}
        arms_state = self.state.get("arms", {}) if isinstance(self.state.get("arms"), dict) else {}
        for family, family_state in arms_state.items():
            if not isinstance(family_state, dict):
                continue
            family_adjusted = 0
            for arm, beta in family_state.items():
                if not isinstance(beta, dict):
                    continue
                a = max(1e-6, float(beta.get("a", 1.0)))
                b = max(1e-6, float(beta.get("b", 1.0)))
                total = a + b
                mean = a / total if total > 0 else 0.5
                if total <= total_cap and lo <= mean <= hi:
                    continue
                family_adjusted += 1
                arms_adjusted += 1
                if dry_run:
                    continue
                # Shrink evidence toward weak prior (1,1) while preserving direction.
                beta["a"] = max(1.0, 1.0 + (a - 1.0) * (1.0 - shrink))
                beta["b"] = max(1.0, 1.0 + (b - 1.0) * (1.0 - shrink))
            if family_adjusted > 0:
                families_adjusted += 1
                detail[str(family)] = family_adjusted

        if arms_adjusted > 0 and not dry_run:
            history = self.state.get("history", [])
            history.append(
                {
                    "ts": datetime.now().isoformat(),
                    "selected": self.state.get("selected", {}),
                    "verdict": "drift_guard",
                    "weight": 0.0,
                    "metadata": {
                        "families_adjusted": families_adjusted,
                        "arms_adjusted": arms_adjusted,
                        "detail": detail,
                        "max_posterior_total": total_cap,
                        "min_mean": lo,
                        "max_mean": hi,
                        "shrink_ratio": shrink,
                    },
                }
            )
            self.state["history"] = history[-1000:]
            self.storage.save_policy_state(self.state)

        return {
            "families_adjusted": families_adjusted,
            "arms_adjusted": arms_adjusted,
            "detail": detail,
            "max_posterior_total": total_cap,
            "min_mean": lo,
            "max_mean": hi,
            "shrink_ratio": shrink,
            "dry_run": bool(dry_run),
        }


_bandit: Optional[PolicyBandit] = None


def get_policy_bandit() -> PolicyBandit:
    global _bandit
    if _bandit is None:
        _bandit = PolicyBandit()
    return _bandit
