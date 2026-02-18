"""
Human-Like Learning Integration
Brings together all the new learning components.

This module integrates:
- User Feedback Learning
- Judgment Engine
- Audit Logger
- Web Search Learning
- Learning Prioritizer
- Daily Intelligence
- Control Center
- Daily Reports
"""

import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class HumanLikeLearningSystem:
    """
    Central integration point for all human-like learning components.
    Coordinates all subsystems for unified learning.
    """

    def __init__(self):
        print("🧠 Initializing Human-Like Learning System...")

        # Initialize all components
        self._init_components()

        print("✅ All components initialized")
        print("")

    def _init_components(self):
        """Initialize all learning components."""
        # Core memory (already exists)
        from src.memory import get_memory
        self.memory = get_memory()

        # User Feedback
        from src.memory import get_feedback_manager
        self.feedback_manager = get_feedback_manager()

        # Judgment Engine
        from src.memory import get_judgment_engine
        self.judgment = get_judgment_engine()

        # Audit Logger
        from src.memory import get_audit_logger
        self.audit = get_audit_logger()

        # Web Search Learning
        from src.memory import get_web_learner
        self.web_learner = get_web_learner()

        # Learning Prioritizer
        from src.memory import get_prioritizer
        self.prioritizer = get_prioritizer()

        # Daily Intelligence
        from src.memory import get_daily_intelligence
        self.daily = get_daily_intelligence()

        # Control Center
        from src.memory import get_control_center
        self.control = get_control_center()

        # Daily Report
        from src.memory import get_report_generator
        self.reporter = get_report_generator()

    # ==================== PUBLIC API ====================

    def learn_from_user(
        self,
        feedback_type: str,
        target: str,
        value: int = 1,
        context: Optional[Dict] = None,
    ):
        """Learn from user feedback."""
        if feedback_type == "thumbs_up":
            result = self.feedback_manager.record_explicit_feedback(
                "thumbs_up", target, 1, context
            )
            self.audit.log_user_feedback("thumbs_up", target, 1)
        elif feedback_type == "thumbs_down":
            result = self.feedback_manager.record_explicit_feedback(
                "thumbs_down", target, -1, context
            )
            self.audit.log_user_feedback("thumbs_down", target, -1)
        elif feedback_type == "correction":
            original = context.get("original", "") if context else ""
            corrected = context.get("corrected", "") if context else ""
            result = self.feedback_manager.record_correction(original, corrected, context)
            self.audit.log_user_feedback("correction", target, 0)

        return result

    def analyze_and_decide(
        self,
        situation: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> Dict:
        """Analyze situation and provide recommendation."""
        # Get judgment
        judgment = self.judgment.analyze_situation(situation, options, context)

        # Check if approval is needed
        recommended = judgment.get("recommended", {})
        action = recommended.get("option", "")

        needs_approval = self.judgment.requires_approval(action, context)

        if needs_approval:
            # Request approval
            request_id = self.control.request_approval(action, context)
            judgment["approval_requested"] = True
            judgment["request_id"] = request_id

        # Log the decision
        self.audit.log_decision(
            decision=action,
            reasoning=recommended.get("reasoning", ""),
            context=context,
        )

        return judgment

    def run_daily_learning(self) -> Dict:
        """Run daily learning cycle."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "components": {},
        }

        # 1. Morning query
        try:
            morning = self.daily.morning_query()
            results["components"]["morning"] = morning
        except Exception as e:
            results["components"]["morning"] = {"error": str(e)}

        # 2. Web discovery
        try:
            discovery = self.web_learner.run_auto_search()
            results["components"]["discovery"] = discovery
        except Exception as e:
            results["components"]["discovery"] = {"error": str(e)}

        # 3. Get priorities
        try:
            priorities = self.prioritizer.prioritize(max_topics=5)
            results["components"]["priorities"] = priorities[:5]
        except Exception as e:
            results["components"]["priorities"] = {"error": str(e)}

        # 4. Evening reflection (if appropriate time)
        if datetime.now().hour >= 18:
            try:
                reflection = self.daily.evening_reflection()
                results["components"]["evening"] = reflection
            except Exception as e:
                results["components"]["evening"] = {"error": str(e)}

        return results

    def get_status(self) -> Dict:
        """Get system status."""
        return {
            "feedback": self.feedback_manager.get_feedback_summary(),
            "judgment": self.judgment.get_stats(),
            "audit": self.audit.get_stats(),
            "web_learning": self.web_learner.get_stats(),
            "priorities": self.prioritizer.get_stats(),
            "daily": self.daily.get_stats(),
            "control": self.control.get_stats(),
        }

    def get_full_report(self) -> str:
        """Generate full daily report."""
        return self.reporter.generate_report()

    def set_control_level(self, level: str):
        """Set user control level."""
        self.control.set_control_level(level)
        self.feedback_manager.set_control_level(level)

    def get_pending_approvals(self) -> List[Dict]:
        """Get pending approvals."""
        return self.control.get_pending()

    def approve_action(self, request_id: str, reason: Optional[str] = None) -> bool:
        """Approve an action."""
        return self.control.approve(request_id, reason)

    def deny_action(self, request_id: str, reason: Optional[str] = None) -> bool:
        """Deny an action."""
        return self.control.deny(request_id, reason)


# ==================== CONVENIENCE FUNCTIONS ====================

_hls_instance = None


def get_hls() -> HumanLikeLearningSystem:
    """Get singleton HLS instance."""
    global _hls_instance
    if _hls_instance is None:
        _hls_instance = HumanLikeLearningSystem()
    return _hls_instance


def learn_from_user(feedback_type: str, target: str, value: int = 1, context: Optional[Dict] = None):
    """Learn from user feedback."""
    return get_hls().learn_from_user(feedback_type, target, value, context)


def analyze_and_decide(situation: str, options: List[str], context: Optional[Dict] = None) -> Dict:
    """Analyze situation and decide."""
    return get_hls().analyze_and_decide(situation, options, context)


def run_daily_cycle() -> Dict:
    """Run daily learning cycle."""
    return get_hls().run_daily_learning()


def get_status() -> Dict:
    """Get system status."""
    return get_hls().get_status()


def full_report() -> str:
    """Get full report."""
    return get_hls().get_full_report()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 60)
    print("🧠 NEXUS Human-Like Learning System")
    print("=" * 60)
    print("")

    hls = HumanLikeLearningSystem()

    # Test user feedback
    print("Testing user feedback...")
    hls.learn_from_user("thumbs_up", "concise_code", 1)
    hls.learn_from_user("thumbs_down", "verbose_output", -1)
    print("✅ User feedback recorded")

    # Test judgment
    print("\nTesting judgment engine...")
    decision = hls.analyze_and_decide(
        "Should I deploy to production?",
        ["Deploy now", "Deploy to staging first", "Wait for approval"],
        {"risk": "high"}
    )
    print(f"✅ Recommended: {decision.get('recommended', {}).get('option')}")
    print(f"   Confidence: {decision.get('confidence')}")

    # Test status
    print("\nGetting system status...")
    status = hls.get_status()
    print(f"✅ Feedback: {status.get('feedback', {}).get('total_feedback', 0)} items")
    print(f"✅ Audit: {status.get('audit', {}).get('total_logs', 0)} logs")

    # Test report
    print("\nGenerating full report...")
    report = hls.get_full_report()
    print(report[:500] + "...")

    print("\n" + "=" * 60)
    print("✅ All systems operational!")
    print("=" * 60)
