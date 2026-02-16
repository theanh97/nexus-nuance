"""
NEXUS Integration Layer
Tích hợp vào mọi nơi trong hệ thống để học từ mọi thứ.

Usage:
    from src.memory.nexus_integration import NexusIntegration

    nexus = NexusIntegration()

    # Học từ message của user
    nexus.learn(user_message)

    # Học từ response của tôi
    nexus.learn_response(my_response)

    # Check status
    nexus.status()
"""

import sys
from datetime import datetime
from typing import Dict, List, Optional

# Import all learning systems
from .chat_learner import get_chat_learner
from .autonomous_improver import get_autonomous_improver, run_auto_improvement
from .user_feedback import get_feedback_manager
from .judgment_engine import get_judgment_engine
from .audit_logger import get_audit_logger


class NexusIntegration:
    """
    Integration layer - học từ mọi nơi.
    """

    def __init__(self):
        self.chat = get_chat_learner()
        self.improver = get_autonomous_improver()
        self.feedback_manager = get_feedback_manager()
        self.judgment = get_judgment_engine()
        self.audit = get_audit_logger()

    def learn(self, message: str) -> Optional[Dict]:
        """
        Học từ message của user.
        Gọi hàm này cho MỌI message của user.
        """
        if not message or len(message.strip()) < 3:
            return None

        # 1. Learn from chat (selective - có chọn lọc)
        learnings = self.chat.learn_from_message(message, role="user")

        # 2. Also track in audit
        if learnings:
            self.audit.log_learning(
                learning_type="chat_selective",
                content=message[:200],
                source="conversation",
                details={"learnings": len(learnings)}
            )

        return learnings

    def learn_response(self, my_response: str, user_correction: str = None) -> Optional[Dict]:
        """
        Học từ response của tôi.
        Nếu user correction → học correction đó.
        """
        if user_correction:
            # User corrected me - HIGH VALUE
            correction = self.chat.learn_from_response(my_response, user_correction)

            if correction:
                self.audit.log_user_feedback(
                    feedback_type="correction",
                    target="my_response",
                    value=user_correction
                )

            return correction

        return None

    def feedback(self, feedback_type: str, target: str, value: int = 1):
        """
        Học từ explicit feedback.
        """
        if feedback_type == "thumbs_up":
            self.feedback_manager.record_explicit_feedback("thumbs_up", target, 1)
        elif feedback_type == "thumbs_down":
            self.feedback_manager.record_explicit_feedback("thumbs_down", target, -1)

        self.audit.log_user_feedback(feedback_type, target, value)

    def improve(self) -> Dict:
        """
        Chạy improvement cycle.
        Gọi định kỳ hoặc khi có learnings mới.
        """
        result = run_auto_improvement()

        self.audit.log_learning(
            learning_type="auto_improvement",
            content=f"Generated {result.get('improvements_generated', 0)} improvements",
            source="system"
        )

        return result

    def status(self) -> Dict:
        """
        Lấy status của học tập.
        """
        chat_summary = self.chat.get_session_summary()
        improver_stats = self.improver.get_stats()
        feedback_stats = self.feedback_manager.get_feedback_summary()

        return {
            "chat": {
                "messages": chat_summary.get("total_messages", 0),
                "learned": chat_summary.get("learned_items", 0),
                "by_type": chat_summary.get("by_type", {}),
            },
            "autonomous": {
                "total": improver_stats.get("total_learnings", 0),
                "high_value": improver_stats.get("high_value", 0),
                "improvements": improver_stats.get("total_improvements", 0),
            },
            "feedback": {
                "total": feedback_stats.get("total_feedback", 0),
                "patterns": feedback_stats.get("patterns_learned", 0),
            },
        }

    def report(self) -> str:
        """
        Tạo report.
        """
        status = self.status()

        lines = [
            "🧠 NEXUS INTEGRATION REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Chat Learning (Selective)",
            f"- Messages: {status['chat']['messages']}",
            f"- Learned: {status['chat']['learned']}",
            f"- By type: {status['chat']['by_type']}",
            "",
            "## Autonomous",
            f"- Total learnings: {status['autonomous']['total']}",
            f"- High value: {status['autonomous']['high_value']}",
            f"- Improvements: {status['autonomous']['improvements']}",
            "",
            "## Feedback",
            f"- Total: {status['feedback']['total']}",
            f"- Patterns: {status['feedback']['patterns']}",
        ]

        return "\n".join(lines)


# Singleton
_nexus = None


def get_nexus() -> NexusIntegration:
    """Get singleton instance."""
    global _nexus
    if _nexus is None:
        _nexus = NexusIntegration()
    return _nexus


# Convenience functions
def nexus_learn(message: str) -> Optional[Dict]:
    """Learn from user message."""
    return get_nexus().learn(message)


def nexus_learn_response(my_response: str, correction: str = None) -> Optional[Dict]:
    """Learn from my response."""
    return get_nexus().learn_response(my_response, correction)


def nexus_improve() -> Dict:
    """Run improvement."""
    return get_nexus().improve()


def nexus_status() -> Dict:
    """Get status."""
    return get_nexus().status()


def nexus_report() -> str:
    """Get report."""
    return get_nexus().report()
