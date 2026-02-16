"""
User Feedback Learner
Learns from user interactions to improve the system.

Sources of learning:
1. Terminal commands executed
2. Error messages encountered
3. Code changes made
4. Chat/conversation interactions
"""

import json
import os
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import threading
import re


class UserFeedbackLearner:
    """
    Learns from user feedback and interactions.
    Converts user behavior into actionable learning.
    """

    def __init__(self, base_path: str = None):
        self._lock = threading.RLock()

        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "learning"
            except:
                self.base_path = Path.cwd() / "data" / "learning"

        self.base_path.mkdir(parents=True, exist_ok=True)

        self.feedback_file = self.base_path / "user_feedback.json"
        self.learnings_file = self.base_path / "learned_from_user.json"
        self.patterns_file = self.base_path / "detected_patterns.json"

        # In-memory storage
        self.feedback_history: List[Dict] = []
        self.learned_items: List[Dict] = []
        self.detected_patterns: List[Dict] = []

        # Pattern detection
        self.command_frequency: Dict[str, int] = {}
        self.error_frequency: Dict[str, int] = {}
        self.preference_indicators: Dict[str, List[str]] = {}

        self._load_data()

        # Learning thresholds
        self.min_value_score = float(os.getenv("LEARNING_VALUE_MIN", "0.5"))
        self.pattern_threshold = 3  # Min occurrences to detect pattern

    def _load_data(self):
        """Load existing data."""
        for file_path, attr, key in [
            (self.feedback_file, "feedback_history", "feedback"),
            (self.learnings_file, "learned_items", "learnings"),
            (self.patterns_file, "detected_patterns", "patterns"),
        ]:
            if file_path.exists():
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            loaded = data.get(key, data.get("items", []))
                            setattr(self, attr, loaded if isinstance(loaded, list) else [])
                except Exception:
                    pass

    def _save_data(self):
        """Save all data."""
        with self._lock:
            for file_path, attr, key in [
                (self.feedback_file, "feedback_history", "feedback"),
                (self.learnings_file, "learned_items", "learnings"),
                (self.patterns_file, "detected_patterns", "patterns")
            ]:
                try:
                    with open(file_path, 'w') as f:
                        json.dump({
                            key: getattr(self, attr)[-1000:],  # Keep last 1000
                            "last_updated": datetime.now().isoformat()
                        }, f, indent=2)
                except Exception:
                    pass

    def learn_from_command(self, command: str, context: Dict = None) -> Dict:
        """
        Learn from a terminal command executed by user.
        Detects patterns and preferences.
        """
        with self._lock:
            command_hash = hashlib.md5(command.encode()).hexdigest()[:8]

            # Track frequency
            self.command_frequency[command_hash] = self.command_frequency.get(command_hash, 0) + 1

            # Detect command type
            cmd_type = self._classify_command(command)

            learning = {
                "id": f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{command_hash}",
                "type": "terminal_command",
                "command": command[:200],
                "command_type": cmd_type,
                "context": context or {},
                "frequency": self.command_frequency[command_hash],
                "timestamp": datetime.now().isoformat(),
                "value_score": self._calculate_command_value(command, cmd_type)
            }

            self.feedback_history.append(learning)

            # Generate improvement if valuable
            if learning["value_score"] >= self.min_value_score:
                self._generate_improvement_from_command(learning)

            # Check for pattern
            if self.command_frequency[command_hash] >= self.pattern_threshold:
                self._detect_command_pattern(command, cmd_type)

            self._save_data()
            return learning

    def learn_from_error(self, error_message: str, context: Dict = None) -> Dict:
        """
        Learn from an error message.
        High value for debugging and prevention.
        """
        with self._lock:
            error_hash = hashlib.md5(error_message.encode()).hexdigest()[:8]

            # Track frequency
            self.error_frequency[error_hash] = self.error_frequency.get(error_hash, 0) + 1

            # Classify error
            error_type = self._classify_error(error_message)

            learning = {
                "id": f"err_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{error_hash}",
                "type": "error_message",
                "error": error_message[:500],
                "error_type": error_type,
                "context": context or {},
                "frequency": self.error_frequency[error_hash],
                "timestamp": datetime.now().isoformat(),
                "value_score": 0.8  # Errors are high value
            }

            self.feedback_history.append(learning)

            # Always generate improvement from errors
            self._generate_improvement_from_error(learning)

            self._save_data()
            return learning

    def learn_from_code_change(self, file_path: str, change_type: str, description: str = "") -> Dict:
        """
        Learn from code changes.
        Tracks what kind of changes user makes.
        """
        learning = {
            "id": f"code_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "type": "code_change",
            "file_path": file_path,
            "change_type": change_type,  # created, modified, deleted
            "description": description[:200],
            "timestamp": datetime.now().isoformat(),
            "value_score": 0.7 if change_type == "created" else 0.5
        }

        self.feedback_history.append(learning)

        if learning["value_score"] >= self.min_value_score:
            self._generate_improvement_from_code(learning)

        self._save_data()
        return learning

    def learn_from_chat(self, message: str, response: str = "", intent: str = "") -> Dict:
        """
        Learn from chat interactions.
        Highest value for understanding user preferences.
        """
        # Detect intent if not provided
        if not intent:
            intent = self._detect_intent(message)

        # Extract preferences
        preferences = self._extract_preferences(message)

        learning = {
            "id": f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "type": "chat_interaction",
            "message": message[:500],
            "response_summary": response[:200] if response else "",
            "intent": intent,
            "preferences_detected": preferences,
            "timestamp": datetime.now().isoformat(),
            "value_score": 0.8 if intent in ["feedback", "preference", "correction"] else 0.5
        }

        self.feedback_history.append(learning)

        # Update preference indicators
        for pref in preferences:
            if pref not in self.preference_indicators:
                self.preference_indicators[pref] = []
            self.preference_indicators[pref].append(message[:100])

        if learning["value_score"] >= self.min_value_score:
            self._generate_improvement_from_chat(learning)

        self._save_data()
        return learning

    def _classify_command(self, command: str) -> str:
        """Classify the type of command."""
        cmd_lower = command.lower()

        if any(x in cmd_lower for x in ["git ", "commit", "push", "pull"]):
            return "git"
        elif any(x in cmd_lower for x in ["npm ", "yarn ", "pip ", "pip3 "]):
            return "package_manager"
        elif any(x in cmd_lower for x in ["python", "python3", "node"]):
            return "runtime"
        elif any(x in cmd_lower for x in ["test", "pytest", "jest"]):
            return "testing"
        elif any(x in cmd_lower for x in ["docker", "kubectl"]):
            return "devops"
        elif any(x in cmd_lower for x in ["ls", "cat", "grep", "find"]):
            return "exploration"
        else:
            return "general"

    def _classify_error(self, error: str) -> str:
        """Classify the type of error."""
        error_lower = error.lower()

        if "syntaxerror" in error_lower or "syntax" in error_lower:
            return "syntax"
        elif "typeerror" in error_lower or "type" in error_lower:
            return "type"
        elif "importerror" in error_lower or "module" in error_lower:
            return "import"
        elif "permission" in error_lower or "access" in error_lower:
            return "permission"
        elif "timeout" in error_lower:
            return "timeout"
        elif "connection" in error_lower or "network" in error_lower:
            return "network"
        else:
            return "general"

    def _detect_intent(self, message: str) -> str:
        """Detect user intent from message."""
        msg_lower = message.lower()

        if any(x in msg_lower for x in ["không thích", "don't like", "bad", "tệ"]):
            return "negative_feedback"
        elif any(x in msg_lower for x in ["tốt", "hay", "good", "great", "excellent"]):
            return "positive_feedback"
        elif any(x in msg_lower for x in ["sửa", "fix", "change", "thay đổi"]):
            return "correction"
        elif any(x in msg_lower for x in ["muốn", "want", "need", "cần"]):
            return "request"
        elif "?" in message:
            return "question"
        else:
            return "statement"

    def _extract_preferences(self, message: str) -> List[str]:
        """Extract user preferences from message."""
        preferences = []
        msg_lower = message.lower()

        # Style preferences
        if any(x in msg_lower for x in ["ngắn gọn", "concise", "short"]):
            preferences.append("concise_responses")
        if any(x in msg_lower for x in ["chi tiết", "detailed", "thorough"]):
            preferences.append("detailed_responses")

        # Language preferences
        if any(x in msg_lower for x in ["tiếng việt", "vietnamese"]):
            preferences.append("vietnamese_language")
        if any(x in msg_lower for x in ["english"]):
            preferences.append("english_language")

        # Technical preferences
        if any(x in msg_lower for x in ["code", "mã"]):
            preferences.append("code_focused")
        if any(x in msg_lower for x in ["explain", "giải thích"]):
            preferences.append("explanation_focused")

        return preferences

    def _calculate_command_value(self, command: str, cmd_type: str) -> float:
        """Calculate learning value of a command."""
        base_value = 0.3

        # High value commands
        if cmd_type in ["git", "devops"]:
            base_value += 0.3
        elif cmd_type in ["testing", "package_manager"]:
            base_value += 0.2

        # Complex commands are more valuable
        if "|" in command or "&&" in command:
            base_value += 0.2

        return min(base_value, 1.0)

    def _generate_improvement_from_command(self, learning: Dict):
        """Generate improvement suggestion from command learning."""
        improvement = {
            "id": f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "source": "user_command",
            "source_id": learning["id"],
            "type": "workflow_optimization",
            "title": f"Optimize {learning['command_type']} workflow",
            "description": f"User frequently uses: {learning['command'][:100]}",
            "suggestion": f"Consider automating or optimizing {learning['command_type']} commands",
            "value_score": learning["value_score"],
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }

        self.learned_items.append(improvement)

    def _generate_improvement_from_error(self, learning: Dict):
        """Generate improvement from error."""
        improvement = {
            "id": f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "source": "user_error",
            "source_id": learning["id"],
            "type": "error_prevention",
            "title": f"Prevent {learning['error_type']} errors",
            "description": f"Recurring error: {learning['error'][:100]}",
            "suggestion": "Add validation/error handling to prevent this error",
            "value_score": learning["value_score"],
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }

        self.learned_items.append(improvement)

    def _generate_improvement_from_code(self, learning: Dict):
        """Generate improvement from code change."""
        improvement = {
            "id": f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "source": "code_change",
            "source_id": learning["id"],
            "type": "code_pattern",
            "title": f"Track {learning['change_type']} pattern",
            "description": f"User {learning['change_type']}: {learning['file_path']}",
            "suggestion": "Learn from user's coding patterns",
            "value_score": learning["value_score"],
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }

        self.learned_items.append(improvement)

    def _generate_improvement_from_chat(self, learning: Dict):
        """Generate improvement from chat interaction."""
        if learning.get("preferences_detected"):
            improvement = {
                "id": f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "source": "chat_preference",
                "source_id": learning["id"],
                "type": "user_preference",
                "title": f"Update preferences: {', '.join(learning['preferences_detected'])}",
                "description": f"User indicated preferences in chat",
                "suggestion": "Adjust response style based on preferences",
                "value_score": learning["value_score"],
                "status": "pending",
                "created_at": datetime.now().isoformat()
            }

            self.learned_items.append(improvement)

    def _detect_command_pattern(self, command: str, cmd_type: str):
        """Detect and record command patterns."""
        pattern = {
            "id": f"pat_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "type": "command_pattern",
            "pattern": command[:100],
            "category": cmd_type,
            "frequency": self.command_frequency.get(hashlib.md5(command.encode()).hexdigest()[:8], 0),
            "detected_at": datetime.now().isoformat()
        }

        # Check if pattern already exists
        existing = [p for p in self.detected_patterns if p.get("pattern") == pattern["pattern"]]
        if not existing:
            self.detected_patterns.append(pattern)

    def get_pending_improvements(self) -> List[Dict]:
        """Get all pending improvements."""
        return [item for item in self.learned_items if item.get("status") == "pending"]

    def get_user_preferences(self) -> Dict[str, Any]:
        """Get detected user preferences."""
        return {
            "command_patterns": self.detected_patterns,
            "preference_indicators": self.preference_indicators,
            "top_commands": sorted(self.command_frequency.items(), key=lambda x: x[1], reverse=True)[:10],
            "common_errors": sorted(self.error_frequency.items(), key=lambda x: x[1], reverse=True)[:5]
        }

    def get_stats(self) -> Dict:
        """Get learner statistics."""
        return {
            "total_feedback": len(self.feedback_history),
            "total_learned": len(self.learned_items),
            "pending_improvements": len(self.get_pending_improvements()),
            "patterns_detected": len(self.detected_patterns),
            "unique_commands": len(self.command_frequency),
            "unique_errors": len(self.error_frequency)
        }


# Singleton
_learner: Optional[UserFeedbackLearner] = None


def get_user_learner() -> UserFeedbackLearner:
    """Get singleton user feedback learner."""
    global _learner
    if _learner is None:
        _learner = UserFeedbackLearner()
    return _learner


def learn_command(command: str, context: Dict = None) -> Dict:
    """Convenience function to learn from command."""
    return get_user_learner().learn_from_command(command, context)


def learn_error(error: str, context: Dict = None) -> Dict:
    """Convenience function to learn from error."""
    return get_user_learner().learn_from_error(error, context)


def learn_chat(message: str, response: str = "", intent: str = "") -> Dict:
    """Convenience function to learn from chat."""
    return get_user_learner().learn_from_chat(message, response, intent)
