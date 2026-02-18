"""
Audit Logger for NEXUS Learning System
Records all learning events for transparency and analysis
"""

import os
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional


class AuditLogger:
    """Singleton audit logger for tracking all learning events."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.log_dir = Path("data/audit")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "audit_log.jsonl"
        self._lock = threading.Lock()
        self._initialized = True

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Write an event to the audit log."""
        with self._lock:
            event = {
                "timestamp": datetime.now().isoformat(),
                "type": event_type,
                "data": data,
            }
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def log_learning(self, learning_type: str, content: str, result: Dict[str, Any]) -> None:
        """Log a learning event."""
        self._log_event("learning", {
            "learning_type": learning_type,
            "content": content[:200],
            "result": result,
        })

    def log_decision(self, decision_type: str, context: Dict[str, Any], decision: str, confidence: float) -> None:
        """Log a decision event."""
        self._log_event("decision", {
            "decision_type": decision_type,
            "context": context,
            "decision": decision,
            "confidence": confidence,
        })

    def log_user_feedback(self, feedback_type: str, content: str, metadata: Optional[Dict] = None) -> None:
        """Log user feedback."""
        self._log_event("user_feedback", {
            "feedback_type": feedback_type,
            "content": content,
            "metadata": metadata or {},
        })

    def log_approval(self, approval_type: str, item: str, approved: bool, reason: Optional[str] = None) -> None:
        """Log an approval event."""
        self._log_event("approval", {
            "approval_type": approval_type,
            "item": item,
            "approved": approved,
            "reason": reason,
        })

    def get_audit_stats(self) -> Dict[str, int]:
        """Get audit statistics."""
        stats = {
            "learning": 0,
            "decision": 0,
            "user_feedback": 0,
            "approval": 0,
        }
        if not self.log_file.exists():
            return stats

        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        event_type = event.get("type")
                        if event_type in stats:
                            stats[event_type] += 1
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

        return stats


# Singleton instance
_audit_logger = None
_audit_logger_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """Get the audit logger singleton."""
    global _audit_logger
    if _audit_logger is None:
        with _audit_logger_lock:
            if _audit_logger is None:
                _audit_logger = AuditLogger()
    return _audit_logger


# Convenience functions
def audit_log(event_type: str, data: Dict[str, Any]) -> None:
    """Log an event."""
    get_audit_logger()._log_event(event_type, data)


def log_learning(learning_type: str, content: str, result: Dict[str, Any]) -> None:
    """Log a learning event."""
    get_audit_logger().log_learning(learning_type, content, result)


def log_decision(decision_type: str, context: Dict[str, Any], decision: str, confidence: float) -> None:
    """Log a decision event."""
    get_audit_logger().log_decision(decision_type, context, decision, confidence)


def log_user_feedback(feedback_type: str, content: str, metadata: Optional[Dict] = None) -> None:
    """Log user feedback."""
    get_audit_logger().log_user_feedback(feedback_type, content, metadata)


def log_approval(approval_type: str, item: str, approved: bool, reason: Optional[str] = None) -> None:
    """Log an approval event."""
    get_audit_logger().log_approval(approval_type, item, approved, reason)


def get_audit_stats() -> Dict[str, int]:
    """Get audit statistics."""
    return get_audit_logger().get_audit_stats()
