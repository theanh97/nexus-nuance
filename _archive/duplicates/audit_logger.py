"""
Audit Logger
Tracks all actions for transparency and reporting.

THE CORE PRINCIPLE:
"Full transparency - every decision is logged."
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from collections import defaultdict
import threading
import hashlib


class AuditLogger:
    """
    Comprehensive audit logging for all system actions.
    Ensures transparency and enables reporting.
    """

    # Action types
    ACTION_LEARNING = "learning"
    ACTION_DECISION = "decision"
    ACTION_USER_FEEDBACK = "user_feEDBACK"
    ACTION_WEB_SEARCH = "web_search"
    ACTION_MEMORY = "memory"
    ACTION_SYSTEM = "system"

    # Log levels
    LEVEL_INFO = "info"
    LEVEL_WARNING = "warning"
    LEVEL_ERROR = "error"
    LEVEL_CRITICAL = "critical"

    def __init__(self, base_path: str = None):
        # Data path
        if base_path:
            self.base_path = Path(base_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "audit"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "audit"

        self.log_file = self.base_path / "audit_log.jsonl"
        self.daily_dir = self.base_path / "daily"
        self.daily_dir.mkdir(parents=True, exist_ok=True)

        # In-memory buffer
        self.buffer: List[Dict] = []
        self.buffer_size = 100
        self.buffer_lock = threading.RLock()

        # Statistics
        self.stats = {
            "total_logs": 0,
            "by_type": defaultdict(int),
            "by_level": defaultdict(int),
            "by_action": defaultdict(int),
        }

        # Thread safety
        self._lock = threading.RLock()

    def _get_daily_file(self, date: Optional[datetime] = None) -> Path:
        """Get daily log file path."""
        dt = date or datetime.now()
        return self.daily_dir / f"audit_{dt.strftime('%Y%m%d')}.jsonl"

    def log(
        self,
        action_type: str,
        action: str,
        details: Optional[Dict] = None,
        level: str = LEVEL_INFO,
        user_initiated: bool = False,
        requires_approval: bool = False,
        approved: Optional[bool] = None,
        context: Optional[Dict] = None,
    ) -> str:
        """
        Log an action.

        Args:
            action_type: Type of action (learning, decision, user_feedback, etc.)
            action: Description of the action
            details: Additional details
            level: Log level (info, warning, error, critical)
            user_initiated: Whether user initiated this
            requires_approval: Whether approval was required
            approved: Whether it was approved (None if not required)
            context: Additional context

        Returns:
            Log entry ID
        """
        with self._lock:
            # Generate ID
            timestamp = datetime.now()
            entry_id = f"log_{timestamp.strftime('%Y%m%d_%H%M%S')}_{self.stats['total_logs']}"

            # Create entry
            entry = {
                "id": entry_id,
                "timestamp": timestamp.isoformat(),
                "action_type": action_type,
                "action": action,
                "details": details or {},
                "level": level,
                "user_initiated": user_initiated,
                "requires_approval": requires_approval,
                "approved": approved,
                "context": context or {},
            }

            # Add to buffer
            self.buffer.append(entry)
            self.stats["total_logs"] += 1
            self.stats["by_type"][action_type] += 1
            self.stats["by_level"][level] += 1
            self.stats["by_action"][action] += 1

            # Flush if buffer is full
            if len(self.buffer) >= self.buffer_size:
                self._flush()

            # Also write to daily file
            self._write_daily(entry)

            return entry_id

    def _flush(self):
        """Flush buffer to disk."""
        if not self.buffer:
            return

        with self.buffer_lock:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    for entry in self.buffer:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self.buffer.clear()
            except Exception:
                pass

    def _write_daily(self, entry: Dict):
        """Write entry to daily file."""
        try:
            daily_file = self._get_daily_file()
            with open(daily_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ==================== SPECIALIZED LOGGING ====================

    def log_learning(
        self,
        learning_type: str,
        content: str,
        source: Optional[str] = None,
        details: Optional[Dict] = None,
    ):
        """Log a learning event."""
        return self.log(
            action_type=self.ACTION_LEARNING,
            action=f"learned_{learning_type}",
            details={
                "content": content[:500],  # Truncate for readability
                "source": source,
                **(details or {})
            },
            level=self.LEVEL_INFO,
        )

    def log_decision(
        self,
        decision: str,
        reasoning: str,
        outcome: Optional[str] = None,
        context: Optional[Dict] = None,
    ):
        """Log a decision made by the system."""
        return self.log(
            action_type=self.ACTION_DECISION,
            action=decision,
            details={
                "reasoning": reasoning,
                "outcome": outcome,
                **(context or {})
            },
            level=self.LEVEL_INFO,
        )

    def log_user_feedback(
        self,
        feedback_type: str,
        target: str,
        value: Any,
        context: Optional[Dict] = None,
    ):
        """Log user feedback."""
        return self.log(
            action_type=self.ACTION_USER_FEEDBACK,
            action=f"feedback_{feedback_type}",
            details={
                "target": target,
                "value": value,
                **(context or {})
            },
            level=self.LEVEL_INFO,
            user_initiated=True,
        )

    def log_web_search(
        self,
        query: str,
        results_count: int,
        sources: List[str],
        context: Optional[Dict] = None,
    ):
        """Log a web search."""
        return self.log(
            action_type=self.ACTION_WEB_SEARCH,
            action=f"search_{query[:50]}",
            details={
                "query": query,
                "results_count": results_count,
                "sources": sources,
                **(context or {})
            },
            level=self.LEVEL_INFO,
        )

    def log_approval_request(
        self,
        action: str,
        details: Optional[Dict] = None,
    ):
        """Log an approval request."""
        return self.log(
            action_type=self.ACTION_SYSTEM,
            action="approval_requested",
            details={
                "action": action,
                **(details or {})
            },
            level=self.LEVEL_INFO,
            requires_approval=True,
            approved=None,
        )

    def log_approval(
        self,
        action: str,
        approved: bool,
        reason: Optional[str] = None,
        context: Optional[Dict] = None,
    ):
        """Log approval decision."""
        return self.log(
            action_type=self.ACTION_SYSTEM,
            action=f"approval_{'granted' if approved else 'denied'}",
            details={
                "action": action,
                "reason": reason,
                **(context or {})
            },
            level=self.LEVEL_INFO,
            requires_approval=True,
            approved=approved,
        )

    def log_error(
        self,
        error: str,
        context: Optional[Dict] = None,
        level: str = LEVEL_ERROR,
    ):
        """Log an error."""
        return self.log(
            action_type=self.ACTION_SYSTEM,
            action="error",
            details={
                "error": error,
                **(context or {})
            },
            level=level,
        )

    # ==================== QUERY METHODS ====================

    def get_logs(
        self,
        action_type: Optional[str] = None,
        action: Optional[str] = None,
        level: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Query logs with filters."""
        results = []

        # Try to read from daily files first
        if since:
            # Read from daily files in range
            current = since
            now = datetime.now()
            while current <= now:
                daily_file = self._get_daily_file(current)
                if daily_file.exists():
                    try:
                        with open(daily_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                try:
                                    entry = json.loads(line)
                                    if self._matches_filters(entry, action_type, action, level):
                                        results.append(entry)
                                except json.JSONDecodeError:
                                    continue
                    except Exception:
                        pass
                current += timedelta(days=1)
        else:
            # Read from main log file
            if self.log_file.exists():
                try:
                    with open(self.log_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        for line in lines[-limit:]:
                            try:
                                entry = json.loads(line)
                                if self._matches_filters(entry, action_type, action, level):
                                    results.append(entry)
                            except json.JSONDecodeError:
                                continue
                except Exception:
                    pass

            # Also check buffer
            with self.buffer_lock:
                for entry in self.buffer:
                    if self._matches_filters(entry, action_type, action, level):
                        results.append(entry)

        return results[-limit:]

    def _matches_filters(
        self,
        entry: Dict,
        action_type: Optional[str],
        action: Optional[str],
        level: Optional[str],
    ) -> bool:
        """Check if entry matches filters."""
        if action_type and entry.get("action_type") != action_type:
            return False
        if action and entry.get("action") != action:
            return False
        if level and entry.get("level") != level:
            return False
        return True

    def get_logs_by_date(self, date: datetime, limit: int = 500) -> List[Dict]:
        """Get logs for a specific date."""
        daily_file = self._get_daily_file(date)
        results = []

        if daily_file.exists():
            try:
                with open(daily_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

        return results[-limit:]

    def get_today_logs(self, limit: int = 100) -> List[Dict]:
        """Get today's logs."""
        return self.get_logs(since=datetime.now() - timedelta(days=1), limit=limit)

    # ==================== REPORTING ====================

    def get_stats(self) -> Dict:
        """Get audit statistics."""
        return {
            "total_logs": self.stats["total_logs"],
            "by_type": dict(self.stats["by_type"]),
            "by_level": dict(self.stats["by_level"]),
            "buffer_size": len(self.buffer),
        }

    def generate_daily_report(self, date: Optional[datetime] = None) -> str:
        """Generate daily audit report."""
        dt = date or datetime.now()
        logs = self.get_logs_by_date(dt, limit=1000)

        lines = [
            f"# 📋 NEXUS Daily Audit Report - {dt.strftime('%Y-%m-%d')}",
            "",
            f"Total entries: {len(logs)}",
            "",
        ]

        # Group by type
        by_type = defaultdict(list)
        for log in logs:
            by_type[log.get("action_type", "unknown")].append(log)

        lines.append("## By Action Type")
        for atype, entries in sorted(by_type.items()):
            lines.append(f"- {atype}: {len(entries)}")
        lines.append("")

        # Group by level
        by_level = defaultdict(list)
        for log in logs:
            by_level[log.get("level", "info")].append(log)

        lines.append("## By Level")
        for level, entries in sorted(by_level.items()):
            lines.append(f"- {level}: {len(entries)}")
        lines.append("")

        # Approval stats
        requires_approval = [l for l in logs if l.get("requires_approval")]
        approved = [l for l in requires_approval if l.get("approved") is True]
        denied = [l for l in requires_approval if l.get("approved") is False]
        pending = [l for l in requires_approval if l.get("approved") is None]

        lines.append("## Approval Statistics")
        lines.append(f"- Total requiring approval: {len(requires_approval)}")
        lines.append(f"- Approved: {len(approved)}")
        lines.append(f"- Denied: {len(denied)}")
        lines.append(f"- Pending: {len(pending)}")
        lines.append("")

        # Recent entries
        lines.append("## Recent Entries")
        for log in logs[-10:]:
            lines.append(f"- [{log.get('level', 'info')}] {log.get('action_type')}: {log.get('action')}")

        return "\n".join(lines)

    # ==================== EXPORT ====================

    def export_logs(
        self,
        start_date: datetime,
        end_date: datetime,
        format: str = "jsonl",
    ) -> Path:
        """Export logs for a date range."""
        output_file = self.base_path / f"export_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.{format}"

        entries = []
        current = start_date
        while current <= end_date:
            entries.extend(self.get_logs_by_date(current, limit=10000))
            current += timedelta(days=1)

        if format == "jsonl":
            with open(output_file, 'w', encoding='utf-8') as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        elif format == "json":
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)

        return output_file


# ==================== CONVENIENCE FUNCTIONS ====================

_audit_logger = None


def get_audit_logger() -> AuditLogger:
    """Get singleton audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def audit_log(
    action_type: str,
    action: str,
    details: Optional[Dict] = None,
    level: str = "info",
) -> str:
    """Log an action."""
    return get_audit_logger().log(action_type, action, details, level)


def log_learning(learning_type: str, content: str, source: Optional[str] = None) -> str:
    """Log a learning event."""
    return get_audit_logger().log_learning(learning_type, content, source)


def log_decision(decision: str, reasoning: str, outcome: Optional[str] = None) -> str:
    """Log a decision."""
    return get_audit_logger().log_decision(decision, reasoning, outcome)


def log_user_feedback(feedback_type: str, target: str, value: Any) -> str:
    """Log user feedback."""
    return get_audit_logger().log_user_feedback(feedback_type, target, value)


def log_approval(action: str, approved: bool, reason: Optional[str] = None) -> str:
    """Log approval decision."""
    return get_audit_logger().log_approval(action, approved, reason)


def get_audit_stats() -> Dict:
    """Get audit statistics."""
    return get_audit_logger().get_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Audit Logger")
    print("=" * 50)

    logger = AuditLogger()

    # Test logging
    print("\nTesting audit logging...")

    logger.log_learning("pattern", "Discovered new pattern", "web_search")
    logger.log_decision("Deploy to staging", "Lower risk", "success")
    logger.log_user_feedback("thumbs_up", "code_style", 1)
    logger.log_approval_request("delete_files", {"risk": "high"})
    logger.log_approval("delete_files", False, "Too risky")

    print(f"\nTotal logs: {logger.get_stats()['total_logs']}")
    print("\n" + logger.generate_daily_report())
