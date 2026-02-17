"""
User Feedback Learning System
Tracks user preferences, corrections, approvals and learns from explicit/implicit feedback.

THE CORE PRINCIPLE:
"Every user interaction is a learning opportunity."
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from collections import defaultdict
import threading
from .storage_v2 import record_learning_event


class UserFeedbackManager:
    """
    Manages all user feedback learning.
    Tracks preferences, corrections, approvals, and implicit signals.
    """

    # Feedback types
    FEEDBACK_EXPLICIT = "explicit"  # Thumbs up/down, ratings
    FEEDBACK_CORRECTION = "correction"  # User corrections
    FEEDBACK_APPROVAL = "approval"  # User approved something
    FEEDBACK_DENIAL = "denial"  # User denied something
    FEEDBACK_IMPLICIT = "implicit"  # Implicit signals (repeats, abandonment)

    # Feedback sources
    SOURCE_DASHBOARD = "dashboard"
    SOURCE_COMMAND = "command"
    SOURCE_INTERACTION = "interaction"
    SOURCE_AUTO = "auto"

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

        self.feedback_file = self.base_path / "user_feedback.json"
        self.preferences_file = self.base_path / "user_preferences.json"
        self.learning_file = self.base_path / "feedback_learning.json"

        # In-memory state
        self.feedback_history: List[Dict] = []
        self.user_preferences: Dict[str, Any] = {}
        self.learning_rules: Dict[str, Any] = {}
        self.preference_patterns: Dict[str, float] = {}  # pattern -> weight

        # Statistics
        self.stats = {
            "total_feedback": 0,
            "explicit_feedback": 0,
            "implicit_feedback": 0,
            "corrections": 0,
            "approvals": 0,
            "denials": 0,
            "patterns_learned": 0,
            "preferences_adjusted": 0,
        }

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load()

    def _load(self):
        """Load feedback history and preferences."""
        # Load feedback history
        if self.feedback_file.exists():
            try:
                with open(self.feedback_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.feedback_history = data.get("history", [])
                    stats = data.get("stats", {})
                    if isinstance(stats, dict):
                        self.stats.update(stats)
            except Exception:
                pass

        # Load user preferences
        if self.preferences_file.exists():
            try:
                with open(self.preferences_file, 'r', encoding='utf-8') as f:
                    self.user_preferences = json.load(f)
            except Exception:
                pass

        # Load learning rules
        if self.learning_file.exists():
            try:
                with open(self.learning_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and ("rules" in data or "patterns" in data):
                        rules = data.get("rules", {})
                        patterns = data.get("patterns", {})
                        self.learning_rules = rules if isinstance(rules, dict) else {}
                        self.preference_patterns = patterns if isinstance(patterns, dict) else {}
                    elif isinstance(data, dict):
                        # Backward-compatible legacy shape where rules were stored directly.
                        self.learning_rules = data
            except Exception:
                pass

        self.stats["patterns_learned"] = len(self.preference_patterns)

    def _save(self):
        """Save feedback data to disk."""
        with self._lock:
            # Save feedback history
            with open(self.feedback_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "history": self.feedback_history[-5000:],  # Keep last 5000
                    "stats": self.stats,
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2, ensure_ascii=False)
            # Save preferences
            with open(self.preferences_file, 'w', encoding='utf-8') as f:
                json.dump(self.user_preferences, f, indent=2, ensure_ascii=False)
            # Save learning rules
            with open(self.learning_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "rules": self.learning_rules,
                    "patterns": self.preference_patterns,
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2, ensure_ascii=False)

    def _emit_learning_event(self, event_type: str, content: str, value: float, risk: float, context: Optional[Dict] = None):
        try:
            record_learning_event(
                {
                    "ts": datetime.now().isoformat(),
                    "source": "user_feedback",
                    "event_type": event_type,
                    "content": str(content)[:500],
                    "context": context or {},
                    "novelty_score": 0.5,
                    "value_score": max(0.0, min(1.0, float(value))),
                    "risk_score": max(0.0, min(1.0, float(risk))),
                    "confidence": 0.85,
                }
            )
        except Exception:
            return

    # ==================== FEEDBACK COLLECTION ====================

    def record_explicit_feedback(
        self,
        feedback_type: str,
        target: str,
        value: int,  # -1 to 1 (negative, neutral, positive)
        context: Optional[Dict] = None,
        source: str = SOURCE_COMMAND,
    ) -> Dict:
        """
        Record explicit feedback from user.
        feedback_type: e.g., "thumbs_up", "thumbs_down", "rating", "correction"
        target: What the feedback is about
        value: -1 (negative), 0 (neutral), 1 (positive)
        """
        with self._lock:
            entry = {
                "id": f"fb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.feedback_history)}",
                "type": self.FEEDBACK_EXPLICIT,
                "feedback_type": feedback_type,
                "target": target,
                "value": value,
                "context": context or {},
                "source": source,
                "timestamp": datetime.now().isoformat(),
            }

            self.feedback_history.append(entry)
            self.stats["total_feedback"] += 1
            self.stats["explicit_feedback"] += 1

            # Learn from this feedback
            self._learn_from_feedback(entry)
            self._emit_learning_event(
                event_type=f"explicit_{feedback_type}",
                content=target,
                value=0.8 if value > 0 else 0.6,
                risk=0.2 if value > 0 else 0.35,
                context=context,
            )

            self._save()
            return entry

    def record_correction(
        self,
        original: str,
        corrected: str,
        context: Optional[Dict] = None,
        source: str = SOURCE_COMMAND,
    ) -> Dict:
        """
        Record a user correction.
        original: What the system did/said
        corrected: What the user corrected it to be
        """
        with self._lock:
            entry = {
                "id": f"corr_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.feedback_history)}",
                "type": self.FEEDBACK_CORRECTION,
                "original": original,
                "corrected": corrected,
                "context": context or {},
                "source": source,
                "timestamp": datetime.now().isoformat(),
            }

            self.feedback_history.append(entry)
            self.stats["total_feedback"] += 1
            self.stats["corrections"] += 1

            # Learn correction pattern
            self._learn_correction(entry)
            self._emit_learning_event(
                event_type="user_correction",
                content=f"{original[:120]} -> {corrected[:120]}",
                value=0.95,
                risk=0.25,
                context=context,
            )

            self._save()
            return entry

    def record_approval(
        self,
        action: str,
        details: Optional[Dict] = None,
        source: str = SOURCE_COMMAND,
    ) -> Dict:
        """Record user approval of an action."""
        with self._lock:
            entry = {
                "id": f"approve_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.feedback_history)}",
                "type": self.FEEDBACK_APPROVAL,
                "action": action,
                "details": details or {},
                "source": source,
                "timestamp": datetime.now().isoformat(),
            }

            self.feedback_history.append(entry)
            self.stats["total_feedback"] += 1
            self.stats["approvals"] += 1

            # Learn approval pattern
            self._learn_approval(entry)
            self._emit_learning_event(
                event_type="user_approval",
                content=action,
                value=0.85,
                risk=0.2,
                context=details,
            )

            self._save()
            return entry

    def record_denial(
        self,
        action: str,
        reason: Optional[str] = None,
        details: Optional[Dict] = None,
        source: str = SOURCE_COMMAND,
    ) -> Dict:
        """Record user denial of an action."""
        with self._lock:
            entry = {
                "id": f"deny_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.feedback_history)}",
                "type": self.FEEDBACK_DENIAL,
                "action": action,
                "reason": reason,
                "details": details or {},
                "source": source,
                "timestamp": datetime.now().isoformat(),
            }

            self.feedback_history.append(entry)
            self.stats["total_feedback"] += 1
            self.stats["denials"] += 1

            # Learn denial pattern
            self._learn_denial(entry)
            self._emit_learning_event(
                event_type="user_denial",
                content=action,
                value=0.75,
                risk=0.4,
                context={"reason": reason, **(details or {})},
            )

            self._save()
            return entry

    def record_implicit_feedback(
        self,
        signal: str,
        target: str,
        context: Optional[Dict] = None,
    ) -> Dict:
        """
        Record implicit feedback (learned from user behavior).
        signal: "repeat", "abandon", "ignore", "engage", "extend"
        """
        with self._lock:
            # Map signals to values
            signal_values = {
                "repeat": 1,      # User repeated action - they liked it
                "abandon": -1,   # User abandoned - they didn't like it
                "ignore": -0.5,  # User ignored - neutral to negative
                "engage": 1,     # User engaged - positive
                "extend": 1,     # User extended - positive
                "shorten": -1,   # User shortened - negative
            }

            entry = {
                "id": f"impl_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.feedback_history)}",
                "type": self.FEEDBACK_IMPLICIT,
                "signal": signal,
                "target": target,
                "value": signal_values.get(signal, 0),
                "context": context or {},
                "source": self.SOURCE_AUTO,
                "timestamp": datetime.now().isoformat(),
            }

            self.feedback_history.append(entry)
            self.stats["total_feedback"] += 1
            self.stats["implicit_feedback"] += 1

            # Learn implicit pattern
            self._learn_from_feedback(entry)
            self._emit_learning_event(
                event_type=f"implicit_{signal}",
                content=target,
                value=0.65 if entry.get("value", 0) > 0 else 0.45,
                risk=0.3,
                context=context,
            )

            self._save()
            return entry

    # ==================== LEARNING ====================

    def _learn_from_feedback(self, entry: Dict):
        """Extract patterns from feedback and update preferences."""
        target = entry.get("target", "")
        value = entry.get("value", 0)
        feedback_type = entry.get("feedback_type", "")

        if not target:
            return

        # Update preference pattern
        current = self.preference_patterns.get(target, 0)
        # Exponential moving average
        self.preference_patterns[target] = current * 0.7 + value * 0.3
        self.stats["patterns_learned"] = len(self.preference_patterns)

    def _learn_correction(self, entry: Dict):
        """Learn from user corrections."""
        original = entry.get("original", "")
        corrected = entry.get("corrected", "")

        if not original or not corrected:
            return

        # Store correction rule
        correction_key = f"correction:{original[:100]}"
        self.learning_rules[correction_key] = {
            "original": original,
            "corrected": corrected,
            "count": self.learning_rules.get(correction_key, {}).get("count", 0) + 1,
            "last_seen": entry["timestamp"],
        }

    def _learn_approval(self, entry: Dict):
        """Learn from user approvals."""
        action = entry.get("action", "")

        if not action:
            return

        # Update approval count for this action type
        approval_key = f"approval:{action}"
        current = self.learning_rules.get(approval_key, {})
        self.learning_rules[approval_key] = {
            "action": action,
            "count": current.get("count", 0) + 1,
            "last_approved": entry["timestamp"],
        }

    def _learn_denial(self, entry: Dict):
        """Learn from user denials."""
        action = entry.get("action", "")
        reason = entry.get("reason", "")

        if not action:
            return

        # Store denial pattern
        denial_key = f"denial:{action}"
        current = self.learning_rules.get(denial_key, {})
        reasons = current.get("reasons", [])
        if reason:
            reasons.append(reason)
        self.learning_rules[denial_key] = {
            "action": action,
            "count": current.get("count", 0) + 1,
            "last_denied": entry["timestamp"],
            "reasons": reasons[-10:],  # Keep last 10 reasons
        }

    # ==================== PREFERENCE QUERIES ====================

    def get_preference(self, target: str) -> float:
        """Get preference value for a target (-1 to 1)."""
        return self.preference_patterns.get(target, 0)

    def get_preferred_actions(self) -> List[Dict]:
        """Get list of actions the user prefers, sorted by preference."""
        approvals = []
        for key, value in self.learning_rules.items():
            if key.startswith("approval:"):
                approvals.append({
                    "action": value.get("action"),
                    "count": value.get("count", 0),
                    "last_approved": value.get("last_approved"),
                })
        return sorted(approvals, key=lambda x: x["count"], reverse=True)

    def get_disliked_actions(self) -> List[Dict]:
        """Get list of actions the user dislikes."""
        denials = []
        for key, value in self.learning_rules.items():
            if key.startswith("denial:"):
                denials.append({
                    "action": value.get("action"),
                    "count": value.get("count", 0),
                    "reasons": value.get("reasons", []),
                })
        return sorted(denials, key=lambda x: x["count"], reverse=True)

    def get_corrections(self) -> List[Dict]:
        """Get list of corrections made by user."""
        corrections = []
        for key, value in self.learning_rules.items():
            if key.startswith("correction:"):
                corrections.append(value)
        return sorted(corrections, key=lambda x: x.get("count", 0), reverse=True)

    def get_correction_for(self, original: str) -> Optional[str]:
        """Get correction for a specific original text."""
        correction_key = f"correction:{original[:100]}"
        rule = self.learning_rules.get(correction_key)
        if rule:
            return rule.get("corrected")
        return None

    def should_auto_approve(self, action: str) -> bool:
        """Check if action should be auto-approved based on history."""
        approval_key = f"approval:{action}"
        rule = self.learning_rules.get(approval_key, {})
        count = rule.get("count", 0)
        # Auto-approve if user approved 3+ times
        return count >= 3

    def should_auto_deny(self, action: str) -> bool:
        """Check if action should be auto-denied based on history."""
        denial_key = f"denial:{action}"
        rule = self.learning_rules.get(denial_key, {})
        count = rule.get("count", 0)
        # Auto-deny if user denied 3+ times
        return count >= 3

    # ==================== PREFERENCE MANAGEMENT ====================

    def set_preference(self, key: str, value: Any):
        """Set a user preference explicitly."""
        with self._lock:
            self.user_preferences[key] = {
                "value": value,
                "updated_at": datetime.now().isoformat(),
            }
            self.stats["preferences_adjusted"] += 1
            self._save()

    def get_preference_setting(self, key: str, default: Any = None) -> Any:
        """Get a user preference setting."""
        pref = self.user_preferences.get(key)
        if pref:
            return pref.get("value", default)
        return default

    def set_control_level(self, level: str):
        """Set user control level: APPROVE, NOTIFY, AUTONOMOUS."""
        valid_levels = ["APPROVE", "NOTIFY", "AUTONOMOUS"]
        if level not in valid_levels:
            raise ValueError(f"Invalid level. Must be one of: {valid_levels}")
        self.set_preference("control_level", level)

    def get_control_level(self) -> str:
        """Get current control level."""
        return self.get_preference_setting("control_level", "NOTIFY")

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get feedback statistics."""
        return dict(self.stats)

    def get_recent_feedback(self, limit: int = 20) -> List[Dict]:
        """Get recent feedback entries."""
        return self.feedback_history[-limit:]

    def get_feedback_summary(self) -> Dict:
        """Get summary of feedback learning."""
        return {
            "total_feedback": self.stats["total_feedback"],
            "explicit": self.stats["explicit_feedback"],
            "implicit": self.stats["implicit_feedback"],
            "corrections": self.stats["corrections"],
            "approvals": self.stats["approvals"],
            "denials": self.stats["denials"],
            "patterns_learned": self.stats["patterns_learned"],
            "preferences_set": len(self.user_preferences),
        }

    def generate_feedback_report(self) -> str:
        """Generate human-readable feedback report."""
        lines = [
            "# User Feedback Learning Report",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Feedback Summary",
            f"- Total Feedback: {self.stats['total_feedback']}",
            f"- Explicit: {self.stats['explicit_feedback']}",
            f"- Implicit: {self.stats['implicit_feedback']}",
            f"- Corrections: {self.stats['corrections']}",
            f"- Approvals: {self.stats['approvals']}",
            f"- Denials: {self.stats['denials']}",
            "",
            "## Learned Patterns",
            f"- Preference Patterns: {self.stats['patterns_learned']}",
            f"- Preferences Set: {len(self.user_preferences)}",
            "",
            "## Control Level",
            f"- Current: {self.get_control_level()}",
            "",
        ]

        # Add top preferred actions
        preferred = self.get_preferred_actions()[:5]
        if preferred:
            lines.append("## Top Preferred Actions")
            for p in preferred:
                lines.append(f"- {p['action']}: {p['count']} approvals")
            lines.append("")

        # Add top disliked actions
        disliked = self.get_disliked_actions()[:5]
        if disliked:
            lines.append("## Top Disliked Actions")
            for d in disliked:
                lines.append(f"- {d['action']}: {d['count']} denials")
            lines.append("")

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_feedback_manager = None


def get_feedback_manager() -> UserFeedbackManager:
    """Get singleton feedback manager instance."""
    global _feedback_manager
    if _feedback_manager is None:
        _feedback_manager = UserFeedbackManager()
    return _feedback_manager


def record_thumbs_up(target: str, context: Optional[Dict] = None) -> Dict:
    """Record a thumbs up."""
    return get_feedback_manager().record_explicit_feedback(
        feedback_type="thumbs_up",
        target=target,
        value=1,
        context=context,
    )


def record_thumbs_down(target: str, context: Optional[Dict] = None) -> Dict:
    """Record a thumbs down."""
    return get_feedback_manager().record_explicit_feedback(
        feedback_type="thumbs_down",
        target=target,
        value=-1,
        context=context,
    )


def record_correction(original: str, corrected: str, context: Optional[Dict] = None) -> Dict:
    """Record a user correction."""
    return get_feedback_manager().record_correction(original, corrected, context)


def record_approval(action: str, details: Optional[Dict] = None) -> Dict:
    """Record user approval."""
    return get_feedback_manager().record_approval(action, details)


def record_denial(action: str, reason: Optional[str] = None, details: Optional[Dict] = None) -> Dict:
    """Record user denial."""
    return get_feedback_manager().record_denial(action, reason, details)


def should_auto_approve(action: str) -> bool:
    """Check if action should be auto-approved."""
    return get_feedback_manager().should_auto_approve(action)


def get_preference(target: str) -> float:
    """Get user preference for a target."""
    return get_feedback_manager().get_preference(target)


def feedback_stats() -> Dict:
    """Get feedback statistics."""
    return get_feedback_manager().get_feedback_summary()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("User Feedback Learning System")
    print("=" * 50)

    fm = UserFeedbackManager()

    # Test feedback recording
    print("\nTesting feedback recording...")

    fm.record_explicit_feedback("thumbs_up", "code_style", 1, {"style": "concise"})
    fm.record_explicit_feedback("thumbs_down", "verbose_output", -1)
    fm.record_correction("Use var x = 1", "Use let x = 1", {"language": "javascript"})
    fm.record_approval("deploy_production", {"timestamp": "now"})
    fm.record_denial("delete_files", "Too risky", {"files": ["important.txt"]})
    fm.record_implicit_feedback("repeat", "generate_tests")

    print(fm.generate_feedback_report())
