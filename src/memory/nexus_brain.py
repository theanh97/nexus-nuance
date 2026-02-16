"""
NEXUS BRAIN - Unified Learning from Chat
The brain that learns EVERYTHING from conversation.

This is the core that integrates all learning systems:
- Chat learning (from conversation)
- Autonomous improvement (from inputs)
- Human-like learning (from feedback)
- Web learning (from search)

Every user message is an opportunity to learn.
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Any

# Import all learning systems
from .chat_learner import get_chat_learner, learn_from_chat, learn_correction, get_chat_summary
from .autonomous_improver import get_autonomous_improver, learn_from_input, run_auto_improvement
from .user_feedback import get_feedback_manager
from .judgment_engine import get_judgment_engine
from .audit_logger import get_audit_logger


class NexusBrain:
    """
    The unified brain that learns from everything.
    - Monitors chat conversation
    - Extracts learnings
    - Generates improvements
    - Updates system
    """

    def __init__(self):
        self.chat = get_chat_learner()
        self.improver = get_autonomous_improver()
        self.feedback = get_feedback_manager()
        self.judgment = get_judgment_engine()
        self.audit = get_audit_logger()

    def process_message(self, message: str) -> Optional[Dict]:
        """
        Process a user message and learn from it.
        This is called for EVERY message.
        """
        if not message or len(message.strip()) < 3:
            return None

        # 1. Learn from chat (extract patterns, bugs, preferences, etc.)
        chat_learnings = learn_from_chat(message)

        # 2. Also learn as general input
        input_result = learn_from_input(
            input_type="chat",
            content=message,
            value_score=0.6,  # Default value for chat
            context={"source": "conversation"}
        )

        # 3. Log the learning
        self.audit.log_learning(
            learning_type="chat_message",
            content=message[:200],
            source="conversation",
            details={"chat_learnings": chat_learnings}
        )

        return {
            "chat_learnings": chat_learnings,
            "input_learned": input_result,
            "timestamp": datetime.now().isoformat(),
        }

    def process_correction(self, my_response: str, user_correction: str) -> Dict:
        """
        Process when user corrects me.
        This is high-value learning!
        """
        # Learn from correction
        correction = learn_correction(my_response, user_correction)

        # Also learn as input
        learn_from_input(
            input_type="correction",
            content=f"Original: {my_response[:100]} | Corrected: {user_correction}",
            value_score=0.95,  # Corrections are very valuable
            context={"source": "correction"}
        )

        # Log
        self.audit.log_user_feedback(
            feedback_type="correction",
            target="my_response",
            value=user_correction
        )

        return {
            "correction_learned": correction,
            "timestamp": datetime.now().isoformat(),
        }

    def process_feedback(self, feedback_type: str, target: str, value: int = 1) -> Dict:
        """
        Process explicit feedback (thumbs up/down, etc.)
        """
        if feedback_type == "thumbs_up":
            result = self.feedback.record_explicit_feedback(
                feedback_type="thumbs_up",
                target=target,
                value=1
            )
        elif feedback_type == "thumbs_down":
            result = self.feedback.record_explicit_feedback(
                feedback_type="thumbs_down",
                target=target,
                value=-1
            )
        else:
            result = self.feedback.record_explicit_feedback(
                feedback_type=feedback_type,
                target=target,
                value=value
            )

        # Also learn as input
        learn_from_input(
            input_type="feedback",
            content=f"{feedback_type}: {target}",
            value_score=0.8,
            context={"source": "explicit_feedback"}
        )

        return result

    def run_improvement_cycle(self) -> Dict:
        """Run improvement cycle - called periodically or on demand."""
        # Run auto improvement
        improvement = run_auto_improvement()

        # Also get chat learnings summary
        chat_summary = get_chat_summary()

        return {
            "improvement": improvement,
            "chat_summary": chat_summary,
            "timestamp": datetime.now().isoformat(),
        }

    def get_status(self) -> Dict:
        """Get comprehensive learning status."""
        chat_summary = get_chat_summary()
        improver_stats = self.improver.get_stats()
        feedback_stats = self.feedback.get_feedback_summary()

        return {
            "chat": {
                "messages": chat_summary.get("total_messages", 0),
                "learned": chat_summary.get("learned_items", 0),
                "by_type": chat_summary.get("by_type", {}),
            },
            "autonomous": {
                "total_learnings": improver_stats.get("total_learnings", 0),
                "high_value": improver_stats.get("high_value", 0),
                "improvements": improver_stats.get("total_improvements", 0),
            },
            "feedback": {
                "total": feedback_stats.get("total_feedback", 0),
                "patterns": feedback_stats.get("patterns_learned", 0),
            },
        }

    def get_recent_learnings(self, limit: int = 10) -> List[Dict]:
        """Get all recent learnings."""
        all_learnings = []

        # Chat learnings
        chat = get_chat_learner()
        for l in chat.get_recent_learnings(limit):
            l["source"] = "chat"
            all_learnings.append(l)

        # Autonomous learnings
        improver = get_autonomous_improver()
        for l in improver.get_all_learnings(limit):
            l["source"] = "autonomous"
            all_learnings.append(l)

        # Sort by time
        all_learnings.sort(key=lambda x: x.get("learned_at", ""), reverse=True)

        return all_learnings[:limit]


# ==================== CONVENIENCE FUNCTIONS ====================

_brain = None


def get_nexus_brain() -> NexusBrain:
    """Get singleton brain instance."""
    global _brain
    if _brain is None:
        _brain = NexusBrain()
    return _brain


def process_user_message(message: str) -> Optional[Dict]:
    """Process a user message - main entry point."""
    return get_nexus_brain().process_message(message)


def process_user_correction(my_response: str, user_correction: str) -> Dict:
    """Process when user corrects you."""
    return get_nexus_brain().process_correction(my_response, user_correction)


def process_user_feedback(feedback_type: str, target: str, value: int = 1) -> Dict:
    """Process explicit feedback."""
    return get_nexus_brain().process_feedback(feedback_type, target, value)


def run_improvement() -> Dict:
    """Run improvement cycle."""
    return get_nexus_brain().run_improvement_cycle()


def nexus_status() -> Dict:
    """Get NEXUS status."""
    return get_nexus_brain().get_status()


def get_all_learnings(limit: int = 10) -> List[Dict]:
    """Get all recent learnings."""
    return get_nexus_brain().get_recent_learnings(limit)


# ==================== MAIN ====================

if __name__ == "__main__":
    print("🧠 NEXUS BRAIN - Testing")
    print("=" * 50)

    brain = get_nexus_brain()

    # Simulate conversation
    print("\n1. Processing messages...")
    messages = [
        "fix the login bug",
        "tôi muốn thêm dark mode",
        "output này quá dài",
        "cái này hay đấy!",
    ]

    for msg in messages:
        result = brain.process_message(msg)
        if result:
            print(f"   ✅ Learned from: {msg[:30]}...")

    # Check status
    print("\n2. Status:")
    status = brain.get_status()
    print(f"   Chat learnings: {status['chat']['learned']}")
    print(f"   Autonomous: {status['autonomous']['total_learnings']}")

    # Run improvement
    print("\n3. Running improvement...")
    improvement = brain.run_improvement_cycle()
    print(f"   Improvements: {improvement['improvement']['improvements_generated']}")

    print("\n✅ NEXUS BRAIN operational!")
