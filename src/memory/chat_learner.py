"""
Chat Learning System - Learn from Conversation
NEXUS learns directly from chat interactions.

This module automatically learns from the conversation:
- User feedback, corrections
- Bugs, issues mentioned
- Feature requests
- Preferences expressed
- Questions asked
- Ideas shared
"""

import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path


class ChatLearner:
    """
    Learns directly from chat conversation.
    ONLY learns HIGH-VALUE information - selective learning.
    """

    # MINIMUM VALUE SCORE to learn (0-1)
    # Only learn if value >= 0.7 (HIGH VALUE)
    MIN_VALUE_THRESHOLD = 0.7

    # HIGH-VALUE Patterns Only - Specific and clear
    PATTERNS = {
        # Bug/Issue - Must have specific context
        "bug": [
            r"bị lỗi\s+(.+)", r"lỗi\s+(.+)", r"bug:\s*(.+)",
            r"error:\s*(.+)", r"crash", r"không hoạt động\s+(.+)",
            r"fix\s+(.+)", r"sửa\s+(.+)", r"issue:\s*(.+)",
        ],
        # Feature - Must indicate specific feature
        "feature": [
            r"thêm\s+(.+)", r"add\s+(.+)", r"tính năng\s+(.+)",
            r"want\s+(.+)", r"need\s+(.+)", r"cần\s+(.+)",
            r"muốn\s+(.+)", r"feature:\s*(.+)",
        ],
        # Preference - Must express clear preference
        "preference": [
            r"tôi thích\s+(.+)", r"I\s+prefer\s+(.+)",
            r"luôn\s+(.+)", r"always\s+(.+)",
            r"nên\s+(.+)", r"should\s+(.+)",
            r"偏好\s+(.+)",  # Chinese
        ],
        # Feedback - Must be evaluative
        "feedback": [
            r"(tốt|xấu|hay|great|bad|good|awesome)\s+(quá|much|very)",
            r"hoạt động\s+(tốt|không tốt)", r"work\s+(well|not)",
        ],
        # Correction - User correcting me
        "correction": [
            r"không phải\s+(.+)", r"sai\s+(.+)", r"đáng lẽ\s+(.+)",
            r"should be\s+(.+)", r"thực ra\s+(.+)",
            r"nhầm\s+(.+)", r"lỗi\s+(.+)", r"wrong\s+(.+)",
        ],
    }

    # DO NOT LEARN these patterns (noise filter)
    IGNORE_PATTERNS = [
        r"^có\s+",  # Just "have/has"
        r"^được\s+",  # Just "can/be able to"
        r"^là\s+",  # Just "is/are"
        r"^vâng",  # Just "yes"
        r"^okay",  # Just acknowledgment
        r"^ok",  # Just acknowledgment
        r"^yes",  # Just yes
        r"^no$",  # Just no
        r"^thanks",  # Just thanks
        r"^cảm ơn",  # Just thanks
    ]

    # Sentiment - Only strong sentiment
    STRONG_POSITIVE = ["tuyệt vời", "awesome", "perfect", "amazing", "👍", "✅", "tốt lắm", "great!"]
    STRONG_NEGATIVE = ["tệ lắm", "terrible", "suck", "👎", "❌", "không thể nào", "fail completely"]

    def __init__(self):
        # Data path
        try:
            project_root = Path(__file__).parent.parent.parent
            self.data_path = project_root / "data" / "memory"
            self.data_path.mkdir(parents=True, exist_ok=True)
        except:
            self.data_path = Path.cwd() / "data" / "memory"

        self.chat_file = self.data_path / "chat_learnings.json"

        # In-memory
        self.conversation_history: List[Dict] = []
        self.learned_items: List[Dict] = []
        self.session_start = datetime.now()

        # Load from disk
        self._load()

    def _load(self):
        """Load learnings from disk."""
        if self.chat_file.exists():
            try:
                with open(self.chat_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.learned_items = data.get("learned_items", [])
                    self.conversation_history = data.get("history", [])
            except:
                pass

    def _save(self):
        """Save learnings to disk."""
        try:
            with open(self.chat_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "learned_items": self.learned_items[-500:],
                    "history": self.conversation_history[-1000:],
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)
        except:
            pass

    def learn_from_message(self, message: str, role: str = "user") -> Optional[Dict]:
        """
        Learn from a chat message.
        Returns learning if有价值 content found.
        """
        if not message or role != "user":
            return None

        message = message.strip()
        if len(message) < 3:
            return None

        # Store in conversation history
        entry = {
            "role": role,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        self.conversation_history.append(entry)

        # Extract learning opportunities
        learnings = self._extract_learnings(message)

        if learnings:
            # Store learnings
            for learning in learnings:
                learning["from_message"] = message[:100]
                learning["learned_at"] = datetime.now().isoformat()
                self.learned_items.append(learning)

            self._save()  # Persist to disk
            return learnings

        return None

    def learn_from_response(self, response: str, message: str) -> Optional[Dict]:
        """
        Learn from my own response - for correction purposes.
        If user corrects me, learn from it.
        """
        # Check if there's a correction in next user message
        is_correction = False
        correction_patterns = [
            r"không phải", r"not", r"sai", r"wrong",
            r"đáng lẽ", r"should be", r"actually", r"thực ra",
        ]

        for pattern in correction_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                is_correction = True
                break

        if is_correction:
            learning = {
                "type": "correction",
                "original_response": response[:100],
                "user_correction": message,
                "learned_at": datetime.now().isoformat(),
            }
            self.learned_items.append(learning)
            return learning

        return None

    def _extract_learnings(self, message: str) -> List[Dict]:
        """Extract HIGH-VALUE learning opportunities ONLY."""
        learnings = []
        message_lower = message.lower()
        message_hash = hash(message)  # For deduplication

        # FIRST: Check if should IGNORE (noise filter)
        for ignore_pattern in self.IGNORE_PATTERNS:
            if re.match(ignore_pattern, message_lower):
                return []  # Skip this message entirely

        # Check each pattern type
        for learn_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, message_lower)
                if match:
                    # Calculate value score
                    value_score = self._calculate_value(message, learn_type, match)

                    # SELECTIVE: Only learn if above threshold
                    if value_score >= self.MIN_VALUE_THRESHOLD:
                        # Check for duplicates
                        if not self._is_duplicate(message, learn_type):
                            learning = {
                                "type": learn_type,
                                "content": message,
                                "value_score": value_score,
                                "matched_pattern": pattern,
                                "message_hash": message_hash,
                            }
                            learnings.append(learning)
                    break  # Only one per type

        return learnings

    def _is_duplicate(self, message: str, learn_type: str) -> bool:
        """Check if we've already learned something similar recently."""
        recent = [l for l in self.learned_items[-10:] if l.get("type") == learn_type]
        msg_start = message[:20].lower()
        for r in recent:
            if r.get("content", "")[:20].lower() == msg_start:
                return True
        return False

    def _calculate_value(self, message: str, learn_type: str, match=None) -> float:
        """Calculate value score - ONLY high value gets learned."""
        base_values = {
            "bug": 0.90,
            "correction": 0.95,
            "feature": 0.75,
            "preference": 0.80,
            "feedback": 0.70,
        }

        value = base_values.get(learn_type, 0.5)

        # Vague indicators - REDUCE
        vague_words = ["gì đó", "cái gì", "something", "anything"]
        if any(v in message.lower() for v in vague_words):
            value -= 0.2

        # Specific indicators - INCREASE
        specific_words = ["cụ thể", "specifically", "exact", ":"]
        if any(s in message.lower() for s in specific_words):
            value += 0.1

        # Strong sentiment - INCREASE
        if any(p in message for p in self.STRONG_POSITIVE):
            value += 0.1
        if any(n in message for n in self.STRONG_NEGATIVE):
            value += 0.1

        # Has context/description - INCREASE
        if len(message) > 30:
            value += 0.05

        return min(1.0, max(0.0, value))

    def _detect_sentiment(self, message: str) -> str:
        """Detect sentiment in message."""
        message_lower = message.lower()

        pos_count = sum(1 for p in self.POSITIVE if p in message_lower)
        neg_count = sum(1 for p in self.NEGATIVE if p in message_lower)

        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        return "neutral"

    def get_session_summary(self) -> Dict:
        """Get summary of what was learned in this session."""
        by_type = {}
        for item in self.learned_items:
            t = item.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "session_duration": (datetime.now() - self.session_start).total_seconds(),
            "total_messages": len(self.conversation_history),
            "learned_items": len(self.learned_items),
            "by_type": by_type,
            "items": self.learned_items[-20:],
        }

    def get_recent_learnings(self, limit: int = 10) -> List[Dict]:
        """Get recent learnings."""
        return self.learned_items[-limit:]


# ==================== CONVENIENCE FUNCTIONS ====================

_chat_learner = None


def get_chat_learner() -> ChatLearner:
    """Get singleton chat learner."""
    global _chat_learner
    if _chat_learner is None:
        _chat_learner = ChatLearner()
    return _chat_learner


def learn_from_chat(message: str) -> Optional[List[Dict]]:
    """Learn from a chat message."""
    return get_chat_learner().learn_from_message(message, "user")


def learn_correction(response: str, correction: str) -> Optional[Dict]:
    """Learn from a correction."""
    return get_chat_learner().learn_from_response(response, correction)


def get_chat_summary() -> Dict:
    """Get session summary."""
    return get_chat_learner().get_session_summary()


# ==================== MAIN ====================

if __name__ == "__main__":
    learner = ChatLearner()

    # Test learning from messages
    print("Testing chat learning...")

    # Simulate conversation
    messages = [
        "fix the login bug please",
        "tôi muốn thêm dark mode",
        "output này quá dài, làm ngắn lại",
        "tôi thích câu trả lời ngắn gọn",
        "hệ thống bị lỗi khi network down",
        "cái này hay đấy!",
        "không đúng, phải là như thế này...",
    ]

    for msg in messages:
        result = learner.learn_from_message(msg)
        if result:
            print(f"✅ Learned from: {msg[:40]}...")
            for r in result:
                print(f"   Type: {r['type']}, Value: {r['value_score']:.2f}")

    print("\n" + "=" * 50)
    print(learner.get_session_summary())
