"""
Inter-ORION Messaging System
============================

Allows multiple ORION instances to communicate:
- Direct messages (1-to-1)
- Broadcast messages (1-to-all)
- Help requests
- Task handoff notifications
- Status updates

Usage:
    from src.core.orion_messenger import get_messenger, send_message, get_messages

    # Send a message
    send_message(
        from_orion="orion-alpha",
        to_orion="orion-beta",
        message_type="help_request",
        content="Need help with security audit"
    )

    # Get messages for an ORION
    messages = get_messages(orion_id="orion-beta", unread_only=True)
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import contextmanager

try:
    import fcntl
except Exception:
    fcntl = None


class OrionMessenger:
    """Messaging system for inter-ORION communication."""

    MESSAGE_TYPES = {
        "direct": "Direct message between ORIONs",
        "broadcast": "Message to all ORIONs",
        "help_request": "Request for assistance",
        "help_response": "Response to help request",
        "task_handoff": "Transfer task to another ORION",
        "task_update": "Status update on shared task",
        "status_update": "General status update",
        "alert": "Important alert/warning",
        "sync_request": "Request state sync",
        "sync_response": "State sync response",
    }

    PRIORITY_LEVELS = ["low", "normal", "high", "urgent"]

    def __init__(self, state_path: Optional[str] = None):
        self.messages_file = Path(state_path or "data/orion_messages.jsonl")
        self.lock_file = self.messages_file.with_suffix(".lock")
        self._local_lock = threading.RLock()

        self.max_messages = max(100, int(os.getenv("ORION_MESSAGES_MAX", "500")))
        self.message_ttl_sec = max(300, int(os.getenv("ORION_MESSAGE_TTL_SEC", "86400")))  # 24h default

        self._ensure_storage()

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    def _ensure_storage(self) -> None:
        """Ensure message storage exists."""
        self.messages_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.messages_file.exists():
            self.messages_file.touch()

    def _normalize_orion_id(self, orion_id: Any) -> str:
        text = str(orion_id or "").strip().lower()
        return text.replace(" ", "-").replace("_", "-")[:60] or "unknown"

    def _normalize_message_type(self, msg_type: Any) -> str:
        text = str(msg_type or "direct").strip().lower()
        return text if text in self.MESSAGE_TYPES else "direct"

    def _normalize_priority(self, priority: Any) -> str:
        text = str(priority or "normal").strip().lower()
        return text if text in self.PRIORITY_LEVELS else "normal"

    def _safe_text(self, value: Any, max_len: int = 2000) -> str:
        text = str(value or "").strip()
        return text[:max_len] if max_len > 0 else text

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _read_messages(self) -> List[Dict[str, Any]]:
        """Read all messages from storage."""
        messages: List[Dict[str, Any]] = []
        if not self.messages_file.exists():
            return messages

        try:
            with open(self.messages_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        if isinstance(msg, dict):
                            messages.append(msg)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        return messages

    def _write_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Write messages to storage (atomic write)."""
        tmp_file = self.messages_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        tmp_file.replace(self.messages_file)

    def _append_message(self, msg: Dict[str, Any]) -> None:
        """Append a single message (efficient append)."""
        with open(self.messages_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def _prune_expired(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove expired messages."""
        now = self._now()
        cutoff = now - timedelta(seconds=self.message_ttl_sec)
        pruned: List[Dict[str, Any]] = []

        for msg in messages:
            try:
                ts_str = msg.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts > cutoff:
                        pruned.append(msg)
                else:
                    pruned.append(msg)
            except Exception:
                pruned.append(msg)

        return pruned

    @contextmanager
    def _exclusive_access(self):
        """Exclusive file access with locking."""
        with self._local_lock:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock_file, "a+", encoding="utf-8") as lock_fh:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def send_message(
        self,
        from_orion: str,
        to_orion: Optional[str],
        message_type: str,
        content: str,
        priority: str = "normal",
        metadata: Optional[Dict[str, Any]] = None,
        related_task_id: Optional[str] = None,
        requires_response: bool = False,
        response_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message from one ORION to another (or broadcast)."""

        from_id = self._normalize_orion_id(from_orion)
        to_id = self._normalize_orion_id(to_orion) if to_orion else None
        msg_type = self._normalize_message_type(message_type)
        msg_priority = self._normalize_priority(priority)
        msg_content = self._safe_text(content, 4000)
        msg_meta = self._safe_dict(metadata)
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        now_iso = self._now_iso()

        # For broadcast, to_orion should be None or "*"
        if msg_type == "broadcast":
            to_id = "*"

        msg = {
            "id": msg_id,
            "from_orion": from_id,
            "to_orion": to_id,
            "message_type": msg_type,
            "content": msg_content,
            "priority": msg_priority,
            "timestamp": now_iso,
            "read": False,
            "read_at": None,
            "read_by": None,
            "requires_response": bool(requires_response),
            "responded": False,
            "response_to": self._safe_text(response_to, 60) if response_to else None,
            "related_task_id": self._safe_text(related_task_id, 60) if related_task_id else None,
            "metadata": msg_meta,
        }

        with self._exclusive_access():
            # Prune and write
            messages = self._read_messages()
            messages.append(msg)
            messages = self._prune_expired(messages)

            # Keep under limit
            if len(messages) > self.max_messages:
                messages = messages[-self.max_messages:]

            self._write_messages(messages)

        return {
            "success": True,
            "message": msg,
            "message_id": msg_id,
        }

    def get_messages(
        self,
        orion_id: str,
        unread_only: bool = False,
        message_type: Optional[str] = None,
        limit: int = 50,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get messages for an ORION (both direct and broadcast)."""

        target_id = self._normalize_orion_id(orion_id)
        limit = max(1, min(200, limit))

        with self._exclusive_access():
            messages = self._read_messages()
            messages = self._prune_expired(messages)

        # Filter messages for this ORION
        filtered: List[Dict[str, Any]] = []
        for msg in messages:
            to_id = str(msg.get("to_orion") or "")
            # Message is for this ORION if:
            # 1. Direct message to this ORION
            # 2. Broadcast message (*)
            # 3. Help request that this ORION might respond to
            if to_id == target_id or to_id == "*":
                if unread_only and msg.get("read"):
                    continue
                if message_type and msg.get("message_type") != message_type:
                    continue
                if since:
                    try:
                        msg_ts = datetime.fromisoformat(msg.get("timestamp", ""))
                        since_ts = datetime.fromisoformat(since)
                        if msg_ts <= since_ts:
                            continue
                    except Exception:
                        pass
                filtered.append(msg)

        # Sort by priority then timestamp
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        filtered.sort(key=lambda m: (
            priority_order.get(m.get("priority", "normal"), 2),
            -(datetime.fromisoformat(m.get("timestamp", "2000-01-01")).timestamp() if m.get("timestamp") else 0)
        ))

        return {
            "success": True,
            "orion_id": target_id,
            "messages": filtered[:limit],
            "total": len(filtered),
            "unread_count": len([m for m in filtered if not m.get("read")]),
        }

    def mark_read(
        self,
        message_id: str,
        read_by: str,
    ) -> Dict[str, Any]:
        """Mark a message as read."""

        msg_id = self._safe_text(message_id, 60)
        reader_id = self._normalize_orion_id(read_by)

        with self._exclusive_access():
            messages = self._read_messages()
            found = None
            for msg in messages:
                if msg.get("id") == msg_id:
                    msg["read"] = True
                    msg["read_at"] = self._now_iso()
                    msg["read_by"] = reader_id
                    found = msg
                    break

            if found:
                self._write_messages(messages)
                return {"success": True, "message": found}
            else:
                return {"success": False, "error": "Message not found", "error_code": "NOT_FOUND"}

    def respond_to_message(
        self,
        message_id: str,
        from_orion: str,
        response_content: str,
        response_type: str = "direct",
    ) -> Dict[str, Any]:
        """Respond to a message."""

        original_id = self._safe_text(message_id, 60)
        responder_id = self._normalize_orion_id(from_orion)

        with self._exclusive_access():
            messages = self._read_messages()
            original = None
            for msg in messages:
                if msg.get("id") == original_id:
                    original = msg
                    break

        if not original:
            return {"success": False, "error": "Original message not found", "error_code": "NOT_FOUND"}

        # Mark original as responded
        with self._exclusive_access():
            messages = self._read_messages()
            for msg in messages:
                if msg.get("id") == original_id:
                    msg["responded"] = True
                    break
            self._write_messages(messages)

        # Send response
        return self.send_message(
            from_orion=responder_id,
            to_orion=original.get("from_orion"),
            message_type=response_type,
            content=response_content,
            response_to=original_id,
            related_task_id=original.get("related_task_id"),
        )

    def get_conversation(
        self,
        orion_a: str,
        orion_b: str,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Get conversation between two ORIONs."""

        id_a = self._normalize_orion_id(orion_a)
        id_b = self._normalize_orion_id(orion_b)
        limit = max(1, min(100, limit))

        with self._exclusive_access():
            messages = self._read_messages()

        conversation: List[Dict[str, Any]] = []
        for msg in messages:
            from_id = msg.get("from_orion")
            to_id = msg.get("to_orion")
            # Message is part of conversation if between these two ORIONs
            if (from_id == id_a and to_id == id_b) or (from_id == id_b and to_id == id_a):
                conversation.append(msg)

        # Sort by timestamp
        conversation.sort(key=lambda m: m.get("timestamp", ""))

        return {
            "success": True,
            "participants": [id_a, id_b],
            "messages": conversation[-limit:],
            "total": len(conversation),
        }

    def broadcast_status(
        self,
        from_orion: str,
        status: str,
        current_task: Optional[str] = None,
        workload: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Broadcast status update to all ORIONs."""

        content_parts = [f"Status: {status}"]
        if current_task:
            content_parts.append(f"Current task: {current_task}")
        if workload is not None:
            content_parts.append(f"Workload: {workload} tasks")

        return self.send_message(
            from_orion=from_orion,
            to_orion="*",
            message_type="broadcast",
            content=" | ".join(content_parts),
            priority="low",
            metadata={
                "status": status,
                "current_task": current_task,
                "workload": workload,
                **(metadata or {})
            },
        )

    def request_help(
        self,
        from_orion: str,
        help_type: str,
        description: str,
        related_task_id: Optional[str] = None,
        urgency: str = "normal",
    ) -> Dict[str, Any]:
        """Send help request to all ORIONs."""

        priority = "high" if urgency in ["high", "urgent"] else "normal"

        return self.send_message(
            from_orion=from_orion,
            to_orion="*",
            message_type="help_request",
            content=f"[{help_type}] {description}",
            priority=priority,
            related_task_id=related_task_id,
            requires_response=True,
            metadata={
                "help_type": help_type,
                "urgency": urgency,
            },
        )

    def handoff_task(
        self,
        from_orion: str,
        to_orion: str,
        task_id: str,
        task_title: str,
        handoff_reason: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Hand off a task to another ORION."""

        return self.send_message(
            from_orion=from_orion,
            to_orion=to_orion,
            message_type="task_handoff",
            content=f"Task handoff: {task_title}\nReason: {handoff_reason}",
            priority="high",
            related_task_id=task_id,
            requires_response=True,
            metadata={
                "task_title": task_title,
                "handoff_reason": handoff_reason,
                "context": context or {},
            },
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get messaging statistics."""

        with self._exclusive_access():
            messages = self._read_messages()
            messages = self._prune_expired(messages)

        total = len(messages)
        by_type: Dict[str, int] = {}
        by_priority: Dict[str, int] = {}
        unread = 0
        pending_responses = 0

        for msg in messages:
            msg_type = str(msg.get("message_type") or "direct")
            by_type[msg_type] = by_type.get(msg_type, 0) + 1

            priority = str(msg.get("priority") or "normal")
            by_priority[priority] = by_priority.get(priority, 0) + 1

            if not msg.get("read"):
                unread += 1

            if msg.get("requires_response") and not msg.get("responded"):
                pending_responses += 1

        return {
            "total_messages": total,
            "unread": unread,
            "pending_responses": pending_responses,
            "by_type": by_type,
            "by_priority": by_priority,
            "message_types_available": self.MESSAGE_TYPES,
        }

    def cleanup(self) -> Dict[str, Any]:
        """Clean up expired messages."""

        with self._exclusive_access():
            messages = self._read_messages()
            before = len(messages)
            messages = self._prune_expired(messages)
            after = len(messages)
            self._write_messages(messages)

        return {
            "success": True,
            "removed": before - after,
            "remaining": after,
        }


# Singleton
_messenger: Optional[OrionMessenger] = None
_messenger_lock = threading.Lock()


def get_messenger() -> OrionMessenger:
    """Get the singleton messenger instance."""
    global _messenger
    with _messenger_lock:
        if _messenger is None:
            _messenger = OrionMessenger()
        return _messenger


def send_message(
    from_orion: str,
    to_orion: Optional[str],
    message_type: str,
    content: str,
    **kwargs,
) -> Dict[str, Any]:
    """Convenience function to send a message."""
    return get_messenger().send_message(
        from_orion=from_orion,
        to_orion=to_orion,
        message_type=message_type,
        content=content,
        **kwargs,
    )


def get_messages(
    orion_id: str,
    unread_only: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    """Convenience function to get messages for an ORION."""
    return get_messenger().get_messages(
        orion_id=orion_id,
        unread_only=unread_only,
        limit=limit,
    )


def broadcast_status(from_orion: str, status: str, **kwargs) -> Dict[str, Any]:
    """Convenience function to broadcast status."""
    return get_messenger().broadcast_status(from_orion=from_orion, status=status, **kwargs)


def request_help(from_orion: str, help_type: str, description: str, **kwargs) -> Dict[str, Any]:
    """Convenience function to request help."""
    return get_messenger().request_help(
        from_orion=from_orion,
        help_type=help_type,
        description=description,
        **kwargs,
    )


def handoff_task(
    from_orion: str,
    to_orion: str,
    task_id: str,
    task_title: str,
    handoff_reason: str,
    **kwargs,
) -> Dict[str, Any]:
    """Convenience function to hand off a task."""
    return get_messenger().handoff_task(
        from_orion=from_orion,
        to_orion=to_orion,
        task_id=task_id,
        task_title=task_title,
        handoff_reason=handoff_reason,
        **kwargs,
    )


def get_message_stats() -> Dict[str, Any]:
    """Convenience function to get messaging stats."""
    return get_messenger().get_stats()
