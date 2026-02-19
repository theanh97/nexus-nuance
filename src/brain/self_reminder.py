"""
Self-Reminder Engine
====================
Periodically re-reads core rules, principles, and guardrails to prevent
the system from drifting away from established guidelines.

Inspired by the user's insight: just like a human needs periodic reminders
to stay on track, the AI system benefits from automatically refreshing its
understanding of core principles at regular intervals.

Integration:
    - Called from brain_daemon.py heartbeat loop
    - Can be invoked manually via brain API
    - Generates reminder events on the event bus
    - Logs all reminder cycles for audit
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.nexus_logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
REMINDER_LOG_PATH = PROJECT_ROOT / "data" / "brain" / "self_reminder_log.jsonl"


@dataclass
class PrincipleSource:
    """A source file containing principles/rules to periodically re-read."""
    path: str
    name: str
    priority: int = 5          # 1-10, higher = more critical
    interval_sec: int = 1800   # How often to remind (default 30 min)
    category: str = "general"
    last_read_at: Optional[float] = None
    content_hash: Optional[str] = None


@dataclass
class ReminderEvent:
    """A single reminder event record."""
    timestamp: str
    source_name: str
    source_path: str
    category: str
    priority: int
    content_hash: str
    key_points_count: int
    changed_since_last: bool
    elapsed_since_last_sec: int


# Default principle sources (relative to PROJECT_ROOT)
_DEFAULT_SOURCES: List[Dict[str, Any]] = [
    {
        "path": "docs/memory/USER_FEEDBACK_GUARDRAILS.md",
        "name": "User Feedback Guardrails",
        "priority": 10,
        "interval_sec": 900,        # 15 min - highest priority
        "category": "guardrails",
    },
    {
        "path": "docs/memory/learning-principles.md",
        "name": "Learning Principles",
        "priority": 8,
        "interval_sec": 1800,        # 30 min
        "category": "learning",
    },
    {
        "path": "docs/memory/SELF_LEARNING_RUNBOOK.md",
        "name": "Self-Learning Runbook",
        "priority": 7,
        "interval_sec": 3600,        # 1 hour
        "category": "operations",
    },
    {
        "path": "docs/memory/FEEDBACK_TRACK_LOG.md",
        "name": "Feedback Track Log",
        "priority": 6,
        "interval_sec": 3600,        # 1 hour
        "category": "feedback",
    },
    {
        "path": "docs/memory/AUTO_PROGRESS.md",
        "name": "Auto Progress",
        "priority": 4,
        "interval_sec": 7200,        # 2 hours
        "category": "progress",
    },
]


class SelfReminderEngine:
    """
    Periodically re-reads principle/rule files and generates reminder events.

    The engine uses a priority-weighted interval system:
    - Priority 10 sources are checked every 15 minutes
    - Priority 5 sources are checked every 30 minutes
    - Priority 1 sources are checked every 2 hours

    When a source file has changed since the last read, the engine flags it
    as "changed" in the reminder event, indicating the system should pay
    extra attention to updates.
    """

    def __init__(
        self,
        sources: Optional[List[Dict[str, Any]]] = None,
        log_path: Optional[Path] = None,
        enabled: bool = True,
    ):
        self._enabled = enabled
        self._lock = threading.Lock()
        self._log_path = log_path or REMINDER_LOG_PATH
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        # Build source list
        raw_sources = sources if sources is not None else _DEFAULT_SOURCES
        self._sources: List[PrincipleSource] = []
        for src in raw_sources:
            self._sources.append(PrincipleSource(
                path=str(src.get("path", "")),
                name=str(src.get("name", "")),
                priority=int(src.get("priority", 5)),
                interval_sec=int(src.get("interval_sec", 1800)),
                category=str(src.get("category", "general")),
            ))

        # Stats
        self._total_reminders = 0
        self._total_changes_detected = 0
        self._last_cycle_at: Optional[float] = None
        self._last_cycle_results: List[Dict[str, Any]] = []

        # Event callback (optional - for event bus integration)
        self._event_callback: Optional[Any] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def set_event_callback(self, callback: Any) -> None:
        """Set a callback for reminder events (e.g., event bus emit)."""
        self._event_callback = callback

    def add_source(self, source: Dict[str, Any]) -> None:
        """Add a new principle source at runtime."""
        with self._lock:
            self._sources.append(PrincipleSource(
                path=str(source.get("path", "")),
                name=str(source.get("name", "")),
                priority=int(source.get("priority", 5)),
                interval_sec=int(source.get("interval_sec", 1800)),
                category=str(source.get("category", "general")),
            ))

    def remove_source(self, name: str) -> bool:
        """Remove a source by name."""
        with self._lock:
            before = len(self._sources)
            self._sources = [s for s in self._sources if s.name != name]
            return len(self._sources) < before

    def check_and_remind(self) -> List[Dict[str, Any]]:
        """
        Check all sources and generate reminders for those due.

        Returns a list of reminder results (one per source that was due).
        This is the main method to call from the daemon heartbeat.
        """
        if not self._enabled:
            return []

        now = time.time()
        results: List[Dict[str, Any]] = []

        with self._lock:
            for source in self._sources:
                if not self._is_due(source, now):
                    continue
                result = self._process_source(source, now)
                if result:
                    results.append(result)

            self._last_cycle_at = now
            self._last_cycle_results = results

        return results

    def force_remind_all(self) -> List[Dict[str, Any]]:
        """Force a reminder for all sources regardless of schedule."""
        if not self._enabled:
            return []

        now = time.time()
        results: List[Dict[str, Any]] = []

        with self._lock:
            for source in self._sources:
                result = self._process_source(source, now)
                if result:
                    results.append(result)

            self._last_cycle_at = now
            self._last_cycle_results = results

        return results

    def get_status(self) -> Dict[str, Any]:
        """Get current status of the reminder engine."""
        with self._lock:
            sources_status = []
            now = time.time()
            for s in self._sources:
                elapsed = int(now - s.last_read_at) if s.last_read_at else None
                next_due = max(0, s.interval_sec - elapsed) if elapsed is not None else 0
                sources_status.append({
                    "name": s.name,
                    "path": s.path,
                    "priority": s.priority,
                    "interval_sec": s.interval_sec,
                    "category": s.category,
                    "last_read_at": datetime.fromtimestamp(s.last_read_at).isoformat() if s.last_read_at else None,
                    "next_due_in_sec": next_due,
                    "content_hash": s.content_hash,
                })

            return {
                "enabled": self._enabled,
                "total_sources": len(self._sources),
                "total_reminders": self._total_reminders,
                "total_changes_detected": self._total_changes_detected,
                "last_cycle_at": datetime.fromtimestamp(self._last_cycle_at).isoformat() if self._last_cycle_at else None,
                "last_cycle_results_count": len(self._last_cycle_results),
                "sources": sources_status,
            }

    def get_stats(self) -> Dict[str, Any]:
        """Get summary statistics."""
        return {
            "enabled": self._enabled,
            "total_sources": len(self._sources),
            "total_reminders": self._total_reminders,
            "total_changes_detected": self._total_changes_detected,
            "last_cycle_at": self._last_cycle_at,
            "categories": list({s.category for s in self._sources}),
        }

    # ── Internal ───────────────────────────────────────────────

    def _is_due(self, source: PrincipleSource, now: float) -> bool:
        """Check if a source is due for a reminder."""
        if source.last_read_at is None:
            return True  # Never read before
        elapsed = now - source.last_read_at
        return elapsed >= source.interval_sec

    def _process_source(self, source: PrincipleSource, now: float) -> Optional[Dict[str, Any]]:
        """Read a source file and generate a reminder event."""
        full_path = PROJECT_ROOT / source.path
        if not full_path.exists():
            logger.warning(f"Self-reminder source not found: {source.path}")
            return None

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error(f"Failed to read reminder source {source.path}: {exc}")
            return None

        # Compute content hash
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        changed = source.content_hash is not None and source.content_hash != new_hash
        elapsed_since_last = int(now - source.last_read_at) if source.last_read_at else 0

        # Extract key points (simple heuristic: count non-empty lines starting with - or *)
        key_points = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and (line.strip().startswith("-") or line.strip().startswith("*") or line.strip().startswith("#"))
        ]

        # Update source state
        source.last_read_at = now
        old_hash = source.content_hash
        source.content_hash = new_hash

        if changed:
            self._total_changes_detected += 1
            logger.info(
                f"[Self-Reminder] Source CHANGED: {source.name} "
                f"(hash {old_hash} -> {new_hash})"
            )

        self._total_reminders += 1

        # Build event
        event = ReminderEvent(
            timestamp=datetime.now().isoformat(),
            source_name=source.name,
            source_path=source.path,
            category=source.category,
            priority=source.priority,
            content_hash=new_hash,
            key_points_count=len(key_points),
            changed_since_last=changed,
            elapsed_since_last_sec=elapsed_since_last,
        )

        result = {
            "source_name": event.source_name,
            "source_path": event.source_path,
            "category": event.category,
            "priority": event.priority,
            "content_hash": event.content_hash,
            "key_points_count": event.key_points_count,
            "changed": event.changed_since_last,
            "elapsed_since_last_sec": event.elapsed_since_last_sec,
            "timestamp": event.timestamp,
        }

        # Log
        self._write_log(result)

        # Emit event if callback set
        if self._event_callback:
            try:
                self._event_callback("self_reminder.triggered", result)
            except Exception as exc:
                logger.debug(f"Event callback error: {exc}")

        if changed:
            logger.info(
                f"[Self-Reminder] {source.name}: {len(key_points)} key points, "
                f"CONTENT CHANGED (priority {source.priority})"
            )
        else:
            logger.debug(
                f"[Self-Reminder] {source.name}: {len(key_points)} key points, "
                f"no changes (priority {source.priority})"
            )

        return result

    def _write_log(self, event: Dict[str, Any]) -> None:
        """Append event to the reminder log file."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug(f"Failed to write reminder log: {exc}")


# ── Module-level singleton ──────────────────────────────────────

_instance: Optional[SelfReminderEngine] = None
_instance_lock = threading.Lock()


def get_self_reminder() -> SelfReminderEngine:
    """Get or create the singleton SelfReminderEngine."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                enabled = os.getenv("SELF_REMINDER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
                _instance = SelfReminderEngine(enabled=enabled)
                # Try to connect to event bus
                try:
                    from core.event_bus import emit_event
                    _instance.set_event_callback(emit_event)
                except ImportError:
                    pass
    return _instance


def reset_instance() -> None:
    """Reset the singleton (for testing)."""
    global _instance
    with _instance_lock:
        _instance = None
