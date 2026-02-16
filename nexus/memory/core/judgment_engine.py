"""
Judgment Engine
Analyzes situations and provides recommendations/decision support.

THE CORE PRINCIPLE:
"AI that thinks, not just remembers."
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from collections import defaultdict
import threading


class JudgmentEngine:
    """
    Provides AI judgment and recommendations.
    Analyzes situations and suggests optimal actions.
    """

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "memory"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "memory"

        self.decisions_file = self.base_path / "judgments.json"

        # In-memory state
        self.judgments: List[Dict] = []
        self.decision_history: List[Dict] = []
        self.criteria_weights: Dict[str, float] = {}  # Criteria -> weight

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load()

    def _load(self):
        """Load judgment history."""
        if self.decisions_file.exists():
            try:
                with open(self.decisions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.judgments = data.get("judgments", [])
                    self.decision_history = data.get("decisions", [])
                    self.criteria_weights = data.get("criteria_weights", {})
            except Exception:
                pass

    def _save(self):
        """Save judgment data."""
        with self._lock:
            with open(self.decisions_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "judgments": self.judgments[-1000:],
                    "decisions": self.decision_history[-1000:],
                    "criteria_weights": self.criteria_weights,
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)

    # ==================== JUDGMENT ANALYSIS ====================

    def analyze_situation(
        self,
        situation: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> Dict:
        """
        Analyze a situation and provide recommendations.

        Args:
            situation: Description of the current situation
            options: List of possible actions/options
            context: Additional context for analysis

        Returns:
            Analysis with recommendations
        """
        with self._lock:
            analysis = {
                "id": f"judge_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.judgments)}",
                "situation": situation,
                "options": options,
                "context": context or {},
                "timestamp": datetime.now().isoformat(),
            }

            # Score each option based on criteria
            scored_options = []
            for option in options:
                score = self._score_option(option, situation, context or {})
                scored_options.append({
                    "option": option,
                    "score": score["total_score"],
                    "criteria_scores": score["criteria"],
                    "reasoning": score["reasoning"],
                })

            # Sort by score
            scored_options.sort(key=lambda x: x["score"], reverse=True)

            analysis["recommendations"] = scored_options
            analysis["recommended"] = scored_options[0] if scored_options else None
            analysis["confidence"] = self._calculate_confidence(scored_options)

            self.judgments.append(analysis)
            self._save()

            return analysis

    def _score_option(
        self,
        option: str,
        situation: str,
        context: Dict,
    ) -> Dict:
        """Score a single option based on multiple criteria."""
        criteria = {
            "safety": self._evaluate_safety(option, context),
            "efficiency": self._evaluate_efficiency(option, context),
            "reliability": self._evaluate_reliability(option, context),
            "user_preference": self._evaluate_user_preference(option, context),
            "risk": self._evaluate_risk(option, context),
        }

        # Calculate weighted score
        total_score = 0
        weights_sum = 0
        for criterion, (score, reasoning) in criteria.items():
            weight = self.criteria_weights.get(criterion, 1.0)
            total_score += score * weight
            weights_sum += weight

        if weights_sum > 0:
            total_score /= weights_sum
        else:
            total_score = 5.0  # Neutral default

        return {
            "total_score": round(total_score, 2),
            "criteria": {k: v[0] for k, v in criteria.items()},
            "reasoning": {k: v[1] for k, v in criteria.items()},
        }

    def _evaluate_safety(self, option: str, context: Dict) -> Tuple[float, str]:
        """Evaluate safety of an option."""
        dangerous_keywords = ["delete", "remove", "destroy", "drop", "truncate", "force"]
        safe_keywords = ["read", "view", "check", "analyze", "test", "create"]

        option_lower = option.lower()

        for kw in dangerous_keywords:
            if kw in option_lower:
                return 2.0, f"Contains dangerous keyword: {kw}"

        for kw in safe_keywords:
            if kw in option_lower:
                return 8.0, f"Contains safe keyword: {kw}"

        return 5.0, "No specific safety concerns"

    def _evaluate_efficiency(self, option: str, context: Dict) -> Tuple[float, str]:
        """Evaluate efficiency of an option."""
        efficient_keywords = ["cache", "batch", "parallel", "async", "optimize"]
        inefficient_keywords = ["loop", "sequential", "sync", "wait"]

        option_lower = option.lower()

        score = 5.0
        reasons = []

        for kw in efficient_keywords:
            if kw in option_lower:
                score += 1.5
                reasons.append(f"Efficient: {kw}")

        for kw in inefficient_keywords:
            if kw in option_lower:
                score -= 1.0
                reasons.append(f"Potentially slow: {kw}")

        if reasons:
            return min(10, max(1, score)), ", ".join(reasons)
        return 5.0, "Standard efficiency"

    def _evaluate_reliability(self, option: str, context: Dict) -> Tuple[float, str]:
        """Evaluate reliability of an option."""
        reliable_keywords = ["retry", "fallback", "backup", "validate", "verify"]
        risky_keywords = ["experimental", "beta", "untested"]

        option_lower = option.lower()

        score = 5.0
        reasons = []

        for kw in reliable_keywords:
            if kw in option_lower:
                score += 2.0
                reasons.append(f"Reliable: {kw}")

        for kw in risky_keywords:
            if kw in option_lower:
                score -= 2.0
                reasons.append(f"Risky: {kw}")

        # Check history for this option type
        if self._has_successful_history(option):
            score += 1.0
            reasons.append("Has successful history")

        if reasons:
            return min(10, max(1, score)), ", ".join(reasons)
        return 5.0, "No specific reliability concerns"

    def _evaluate_user_preference(self, option: str, context: Dict) -> Tuple[float, str]:
        """Evaluate based on user preferences."""
        # Import feedback manager
        try:
            from .user_feedback import get_feedback_manager
            fm = get_feedback_manager()
            pref = fm.get_preference(option)
            if pref > 0.3:
                return 8.0 + pref * 2, f"User prefers this (score: {pref:.2f})"
            elif pref < -0.3:
                return 2.0 + pref * 2, f"User dislikes this (score: {pref:.2f})"
        except Exception:
            pass

        return 5.0, "No specific user preference"

    def _evaluate_risk(self, option: str, context: Dict) -> Tuple[float, str]:
        """Evaluate risk level of an option."""
        high_risk_keywords = ["production", "deploy", "main", "master", "sudo", "root"]
        low_risk_keywords = ["test", "staging", "dev", "backup", "copy"]

        option_lower = option.lower()

        score = 5.0
        reasons = []

        for kw in high_risk_keywords:
            if kw in option_lower:
                score -= 3.0
                reasons.append(f"High risk: {kw}")

        for kw in low_risk_keywords:
            if kw in option_lower:
                score += 2.0
                reasons.append(f"Low risk: {kw}")

        # Check if user has approved similar actions before
        try:
            from .user_feedback import get_feedback_manager
            fm = get_feedback_manager()
            if fm.should_auto_approve(option):
                score += 1.5
                reasons.append("User has approved similar before")
            elif fm.should_auto_deny(option):
                score -= 2.0
                reasons.append("User has denied similar before")
        except Exception:
            pass

        if reasons:
            return min(10, max(1, score)), ", ".join(reasons)
        return 5.0, "Standard risk level"

    def _has_successful_history(self, option: str) -> bool:
        """Check if option type has successful history."""
        for decision in self.decision_history[-100:]:
            if decision.get("option") == option and decision.get("outcome") == "success":
                return True
        return False

    def _calculate_confidence(self, scored_options: List[Dict]) -> float:
        """Calculate confidence in the recommendation."""
        if len(scored_options) < 2:
            return 0.5

        top_score = scored_options[0].get("score", 0)
        second_score = scored_options[1].get("score", 0)

        gap = top_score - second_score

        if gap > 3:
            return 0.9
        elif gap > 2:
            return 0.7
        elif gap > 1:
            return 0.5
        else:
            return 0.3

    # ==================== DECISION TRACKING ====================

    def record_decision(
        self,
        situation: str,
        option: str,
        outcome: str,
        context: Optional[Dict] = None,
    ) -> Dict:
        """Record the outcome of a decision for learning."""
        with self._lock:
            decision = {
                "id": f"dec_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.decision_history)}",
                "situation": situation,
                "option": option,
                "outcome": outcome,  # "success", "failure", "partial"
                "context": context or {},
                "timestamp": datetime.now().isoformat(),
            }

            self.decision_history.append(decision)

            # Update criteria weights based on outcome
            self._update_weights(option, outcome)

            self._save()
            return decision

    def _update_weights(self, option: str, outcome: str):
        """Update criteria weights based on decision outcomes."""
        # Increase weight of criteria that led to success
        # This is a simplified version - could be enhanced with more sophisticated learning
        if outcome == "success":
            for criterion in self.criteria_weights:
                self.criteria_weights[criterion] = min(
                    2.0,
                    self.criteria_weights.get(criterion, 1.0) * 1.1
                )
        elif outcome == "failure":
            for criterion in self.criteria_weights:
                self.criteria_weights[criterion] = max(
                    0.5,
                    self.criteria_weights.get(criterion, 1.0) * 0.9
                )

    # ==================== QUERY METHODS ====================

    def should_proceed(self, action: str, context: Dict = None) -> Dict:
        """
        Quick check if an action should proceed.
        Returns recommendation with reasoning.
        """
        return self.analyze_situation(
            situation=f"Should I {action}?",
            options=["proceed", "skip", "get_more_info"],
            context=context or {},
        )

    def compare_options(
        self,
        option_a: str,
        option_b: str,
        context: Dict = None,
    ) -> Dict:
        """Compare two options and recommend one."""
        return self.analyze_situation(
            situation=f"Compare {option_a} vs {option_b}",
            options=[option_a, option_b],
            context=context or {},
        )

    def get_advice(self, question: str) -> Dict:
        """Get general advice for a question."""
        # Map common questions to advice
        advice_map = {
            "default": {
                "advice": "Consider the safety, efficiency, and user preferences when making decisions.",
                "confidence": 0.7,
            }
        }

        # Simple keyword matching
        for key, advice in advice_map.items():
            if key in question.lower():
                return {
                    "question": question,
                    "advice": advice["advice"],
                    "confidence": advice["confidence"],
                    "timestamp": datetime.now().isoformat(),
                }

        return {
            "question": question,
            "advice": advice_map["default"]["advice"],
            "confidence": advice_map["default"]["confidence"],
            "timestamp": datetime.now().isoformat(),
        }

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get judgment engine statistics."""
        total = len(self.decision_history)
        successes = sum(1 for d in self.decision_history if d.get("outcome") == "success")
        failures = sum(1 for d in self.decision_history if d.get("outcome") == "failure")

        return {
            "total_judgments": len(self.judgments),
            "total_decisions": total,
            "success_rate": successes / total if total > 0 else 0,
            "criteria_weights": self.criteria_weights,
        }

    def get_recent_judgments(self, limit: int = 10) -> List[Dict]:
        """Get recent judgments."""
        return self.judgments[-limit:]

    def get_decision_history(self, limit: int = 20) -> List[Dict]:
        """Get decision history."""
        return self.decision_history[-limit:]

    # ==================== CONTROL LEVEL INTEGRATION ====================

    def requires_approval(self, action: str, context: Dict = None) -> bool:
        """
        Determine if action requires user approval.
        Based on control level and action risk.
        """
        try:
            from .user_feedback import get_feedback_manager
            fm = get_feedback_manager()
            control_level = fm.get_control_level()
        except Exception:
            control_level = "NOTIFY"

        if control_level == "APPROVE":
            return True

        if control_level == "NOTIFY":
            # Check if it's high risk
            judgment = self.should_proceed(action, context or {})
            recommended = judgment.get("recommended", {})
            if recommended.get("score", 5) < 4:
                return True

        # AUTONOMOUS - no approval needed (but learn from outcomes)
        return False


# ==================== CONVENIENCE FUNCTIONS ====================

_judgment_engine = None


def get_judgment_engine() -> JudgmentEngine:
    """Get singleton judgment engine instance."""
    global _judgment_engine
    if _judgment_engine is None:
        _judgment_engine = JudgmentEngine()
    return _judgment_engine


def analyze_situation(situation: str, options: List[str], context: Optional[Dict] = None) -> Dict:
    """Analyze a situation and get recommendations."""
    return get_judgment_engine().analyze_situation(situation, options, context)


def should_proceed(action: str, context: Dict = None) -> Dict:
    """Quick check if an action should proceed."""
    return get_judgment_engine().should_proceed(action, context)


def record_decision(situation: str, option: str, outcome: str, context: Optional[Dict] = None) -> Dict:
    """Record a decision outcome for learning."""
    return get_judgment_engine().record_decision(situation, option, outcome, context)


def requires_approval(action: str, context: Dict = None) -> bool:
    """Check if action requires user approval."""
    return get_judgment_engine().requires_approval(action, context)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Judgment Engine")
    print("=" * 50)

    je = JudgmentEngine()

    # Test analysis
    print("\nAnalyzing situation...")

    result = je.analyze_situation(
        situation="Need to deploy to production",
        options=["Deploy now", "Wait for approval", "Deploy to staging first"],
        context={"risk_level": "high"}
    )

    print(f"\nRecommended: {result['recommended']['option']}")
    print(f"Confidence: {result['confidence']}")
    print(f"Score: {result['recommended']['score']}")
    print(f"Reasoning: {result['recommended']['reasoning']}")

    # Test quick check
    print("\n--- Should proceed check ---")
    check = je.should_proceed("deploy to production", {"risk_level": "high"})
    print(f"Should proceed: {check['recommended']['option']}")

    # Record outcome
    je.record_decision(
        situation="Need to deploy to production",
        option="Deploy to staging first",
        outcome="success",
    )

    print("\nStats:", je.get_stats())
