"""
Human Notification System
=========================

Allows NEXUS/ORIONs to notify humans via multiple channels:
- Webhooks (Slack, Discord, custom)
- Email via webhook services (SendGrid, Mailgun, etc.)
- Dashboard notifications
- Rate-limited to prevent spam

Usage:
    from src.core.human_notifier import get_notifier, notify_humans, notify_urgent

    # Send notification
    notify_humans(
        title="ORION needs help",
        message="Security audit requires human review",
        level="warning",
        channels=["slack", "dashboard"]
    )

    # Urgent notification (bypasses rate limit)
    notify_urgent("Critical issue detected!", details={"error": "..."})
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import urllib.request
import urllib.error
import ssl

# ============================================
# Data Classes
# ============================================

@dataclass
class Notification:
    id: str
    title: str
    message: str
    level: str  # info, success, warning, error, critical
    source: str  # which agent/component sent it
    channels: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    sent_at: Optional[str] = None
    delivered: bool = False
    error: Optional[str] = None
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "level": self.level,
            "source": self.source,
            "channels": self.channels,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "sent_at": self.sent_at,
            "delivered": self.delivered,
            "error": self.error,
            "acknowledged": self.acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }


@dataclass
class NotificationChannel:
    name: str
    enabled: bool
    webhook_url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    template: Optional[str] = None
    rate_limit_per_hour: int = 10
    min_level: str = "info"  # Minimum level to send


# ============================================
# Notification Manager
# ============================================

class HumanNotifier:
    """Manages notifications to human team members."""

    LEVELS = ["info", "success", "warning", "error", "critical"]
    LEVEL_PRIORITY = {level: i for i, level in enumerate(LEVELS)}

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = Path(state_path or "data/human_notifications.jsonl")
        self._lock = threading.RLock()

        # Rate limiting
        self.rate_limit_window_sec = int(os.getenv("NOTIFICATION_RATE_LIMIT_WINDOW_SEC", "3600"))
        self.max_notifications_per_hour = int(os.getenv("NOTIFICATION_MAX_PER_HOUR", "20"))
        self.max_per_channel_per_hour = int(os.getenv("NOTIFICATION_MAX_PER_CHANNEL_HOUR", "10"))
        self.min_interval_sec = int(os.getenv("NOTIFICATION_MIN_INTERVAL_SEC", "60"))

        # Deduplication
        self.dedupe_window_sec = int(os.getenv("NOTIFICATION_DEDUPE_WINDOW_SEC", "300"))

        # Channel tracking
        self._channel_sends: Dict[str, List[float]] = {}
        self._recent_hashes: Dict[str, float] = {}
        self._last_notification_at: float = 0.0

        # Storage
        self._ensure_storage()

        # Configure channels from environment
        self.channels = self._load_channels()

    def _now(self) -> datetime:
        return datetime.now()

    def _now_iso(self) -> str:
        return datetime.now().isoformat()

    def _now_ts(self) -> float:
        return time.time()

    def _ensure_storage(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.state_path.touch()

    def _load_channels(self) -> Dict[str, NotificationChannel]:
        """Load channel configurations from environment."""
        channels: Dict[str, NotificationChannel] = {}

        # Slack webhook
        slack_url = os.getenv("NOTIFICATION_SLACK_WEBHOOK_URL", "")
        if slack_url:
            channels["slack"] = NotificationChannel(
                name="slack",
                enabled=True,
                webhook_url=slack_url,
                headers={"Content-Type": "application/json"},
                rate_limit_per_hour=int(os.getenv("NOTIFICATION_SLACK_RATE_LIMIT", "10")),
                min_level=os.getenv("NOTIFICATION_SLACK_MIN_LEVEL", "warning"),
            )

        # Discord webhook
        discord_url = os.getenv("NOTIFICATION_DISCORD_WEBHOOK_URL", "")
        if discord_url:
            channels["discord"] = NotificationChannel(
                name="discord",
                enabled=True,
                webhook_url=discord_url,
                headers={"Content-Type": "application/json"},
                rate_limit_per_hour=int(os.getenv("NOTIFICATION_DISCORD_RATE_LIMIT", "10")),
                min_level=os.getenv("NOTIFICATION_DISCORD_MIN_LEVEL", "warning"),
            )

        # Generic webhook (for email services, custom integrations)
        generic_url = os.getenv("NOTIFICATION_GENERIC_WEBHOOK_URL", "")
        if generic_url:
            channels["webhook"] = NotificationChannel(
                name="webhook",
                enabled=True,
                webhook_url=generic_url,
                headers=json.loads(os.getenv("NOTIFICATION_GENERIC_HEADERS", "{}")),
                rate_limit_per_hour=int(os.getenv("NOTIFICATION_GENERIC_RATE_LIMIT", "20")),
                min_level=os.getenv("NOTIFICATION_GENERIC_MIN_LEVEL", "info"),
            )

        # Dashboard (always available)
        channels["dashboard"] = NotificationChannel(
            name="dashboard",
            enabled=True,
            rate_limit_per_hour=100,  # High limit for dashboard
            min_level="info",
        )

        return channels

    def _hash_notification(self, title: str, message: str, level: str) -> str:
        """Create hash for deduplication."""
        content = f"{title}|{message}|{level}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _is_duplicate(self, hash_key: str) -> bool:
        """Check if notification was recently sent."""
        now = self._now_ts()
        if hash_key in self._recent_hashes:
            if now - self._recent_hashes[hash_key] < self.dedupe_window_sec:
                return True
        return False

    def _check_rate_limit(self, channel: str) -> bool:
        """Check if rate limit allows sending to channel."""
        now = self._now_ts()
        cutoff = now - self.rate_limit_window_sec

        # Clean old entries
        if channel in self._channel_sends:
            self._channel_sends[channel] = [ts for ts in self._channel_sends[channel] if ts > cutoff]
        else:
            self._channel_sends[channel] = []

        # Check global rate limit
        total_sends = sum(len(sends) for sends in self._channel_sends.values())
        if total_sends >= self.max_notifications_per_hour:
            return False

        # Check channel rate limit
        ch = self.channels.get(channel)
        if ch and len(self._channel_sends[channel]) >= ch.rate_limit_per_hour:
            return False

        # Check minimum interval
        if now - self._last_notification_at < self.min_interval_sec:
            return False

        return True

    def _record_send(self, channel: str) -> None:
        """Record a send for rate limiting."""
        now = self._now_ts()
        if channel not in self._channel_sends:
            self._channel_sends[channel] = []
        self._channel_sends[channel].append(now)
        self._last_notification_at = now

    def _should_send_to_channel(self, channel_name: str, level: str) -> bool:
        """Check if notification should be sent to channel."""
        channel = self.channels.get(channel_name)
        if not channel or not channel.enabled:
            return False

        level_priority = self.LEVEL_PRIORITY.get(level, 0)
        min_priority = self.LEVEL_PRIORITY.get(channel.min_level, 0)

        return level_priority >= min_priority

    def _format_slack_payload(self, notification: Notification) -> Dict[str, Any]:
        """Format notification for Slack webhook."""
        color_map = {
            "info": "#3b82f6",
            "success": "#22c55e",
            "warning": "#f97316",
            "error": "#ef4444",
            "critical": "#dc2626",
        }

        emoji_map = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨",
        }

        return {
            "attachments": [
                {
                    "color": color_map.get(notification.level, "#6b7280"),
                    "title": f"{emoji_map.get(notification.level, '📌')} {notification.title}",
                    "text": notification.message,
                    "fields": [
                        {"title": "Source", "value": notification.source, "short": True},
                        {"title": "Level", "value": notification.level.upper(), "short": True},
                        {"title": "Time", "value": notification.created_at, "short": True},
                    ],
                    "footer": "NEXUS AI Employee",
                    "ts": int(self._now_ts()),
                }
            ]
        }

    def _format_discord_payload(self, notification: Notification) -> Dict[str, Any]:
        """Format notification for Discord webhook."""
        color_map = {
            "info": 3447003,  # Blue
            "success": 5763719,  # Green
            "warning": 15105570,  # Orange
            "error": 15548997,  # Red
            "critical": 13632027,  # Dark red
        }

        return {
            "embeds": [
                {
                    "title": notification.title,
                    "description": notification.message,
                    "color": color_map.get(notification.level, 7506394),
                    "fields": [
                        {"name": "Source", "value": notification.source, "inline": True},
                        {"name": "Level", "value": notification.level.upper(), "inline": True},
                    ],
                    "timestamp": notification.created_at,
                    "footer": {"text": "NEXUS AI Employee"},
                }
            ]
        }

    def _format_generic_payload(self, notification: Notification) -> Dict[str, Any]:
        """Format notification for generic webhook."""
        return {
            "id": notification.id,
            "title": notification.title,
            "message": notification.message,
            "level": notification.level,
            "source": notification.source,
            "timestamp": notification.created_at,
            "metadata": notification.metadata,
        }

    def _send_webhook(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> bool:
        """Send webhook request."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")

            # Create SSL context that doesn't verify (for local testing)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
                return 200 <= response.status < 300
        except Exception as e:
            print(f"Webhook send failed: {e}")
            return False

    def _save_notification(self, notification: Notification) -> None:
        """Save notification to storage."""
        try:
            with open(self.state_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(notification.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"Failed to save notification: {e}")

    def notify(
        self,
        title: str,
        message: str,
        level: str = "info",
        source: str = "nexus",
        channels: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        bypass_rate_limit: bool = False,
    ) -> Dict[str, Any]:
        """Send a notification to humans."""

        # Normalize level
        level = str(level or "info").lower().strip()
        if level not in self.LEVELS:
            level = "info"

        # Default channels
        if not channels:
            channels = ["dashboard"]

        # Create notification
        notification = Notification(
            id=f"notif_{uuid.uuid4().hex[:12]}",
            title=str(title or "Notification")[:200],
            message=str(message or "")[:2000],
            level=level,
            source=str(source or "nexus")[:100],
            channels=channels,
            metadata=metadata or {},
            created_at=self._now_iso(),
        )

        # Check deduplication
        hash_key = self._hash_notification(notification.title, notification.message, notification.level)
        if self._is_duplicate(hash_key) and not bypass_rate_limit:
            return {
                "success": False,
                "error": "Duplicate notification (recently sent)",
                "error_code": "DUPLICATE",
                "notification": notification.to_dict(),
            }

        # Check rate limit
        if not bypass_rate_limit and not self._check_rate_limit("global"):
            return {
                "success": False,
                "error": "Rate limit exceeded",
                "error_code": "RATE_LIMITED",
                "notification": notification.to_dict(),
            }

        # Send to each channel
        results: Dict[str, Any] = {}
        any_sent = False

        for channel_name in channels:
            if not self._should_send_to_channel(channel_name, level):
                results[channel_name] = {"sent": False, "reason": "level_below_minimum"}
                continue

            if not bypass_rate_limit and not self._check_rate_limit(channel_name):
                results[channel_name] = {"sent": False, "reason": "channel_rate_limited"}
                continue

            channel = self.channels.get(channel_name)
            if not channel:
                results[channel_name] = {"sent": False, "reason": "channel_not_configured"}
                continue

            sent = False
            error = None

            try:
                if channel_name == "slack" and channel.webhook_url:
                    payload = self._format_slack_payload(notification)
                    sent = self._send_webhook(channel.webhook_url, payload, channel.headers)

                elif channel_name == "discord" and channel.webhook_url:
                    payload = self._format_discord_payload(notification)
                    sent = self._send_webhook(channel.webhook_url, payload, channel.headers)

                elif channel_name == "webhook" and channel.webhook_url:
                    payload = self._format_generic_payload(notification)
                    sent = self._send_webhook(channel.webhook_url, payload, channel.headers)

                elif channel_name == "dashboard":
                    # Dashboard notifications are just stored
                    sent = True

                if sent:
                    self._record_send(channel_name)
                    any_sent = True
                    self._recent_hashes[hash_key] = self._now_ts()

            except Exception as e:
                error = str(e)

            results[channel_name] = {"sent": sent, "error": error}

        # Update notification
        notification.sent_at = self._now_iso()
        notification.delivered = any_sent
        notification.error = None if any_sent else "All channels failed"

        # Save to storage
        self._save_notification(notification)

        return {
            "success": any_sent,
            "notification": notification.to_dict(),
            "results": results,
        }

    def notify_urgent(
        self,
        title: str,
        message: str,
        source: str = "nexus",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send urgent notification (bypasses rate limits)."""
        channels = [ch for ch in self.channels.keys() if ch != "dashboard"]
        channels.append("dashboard")

        return self.notify(
            title=title,
            message=message,
            level="critical",
            source=source,
            channels=channels,
            metadata=metadata,
            bypass_rate_limit=True,
        )

    def notify_help_needed(
        self,
        from_orion: str,
        help_type: str,
        description: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send help request notification to humans."""
        return self.notify(
            title=f"🆘 Help Needed: {help_type}",
            message=f"From: {from_orion}\n\n{description}",
            level="warning",
            source=from_orion,
            channels=[ch for ch in self.channels.keys() if self.channels[ch].enabled],
            metadata={"help_type": help_type, "context": context or {}},
        )

    def notify_task_blocked(
        self,
        task_id: str,
        task_title: str,
        blocker: str,
        orion_id: str,
    ) -> Dict[str, Any]:
        """Notify humans that a task is blocked."""
        return self.notify(
            title=f"🚧 Task Blocked: {task_title[:50]}",
            message=f"Task ID: {task_id}\nOrion: {orion_id}\nBlocker: {blocker}",
            level="error",
            source=orion_id,
            channels=[ch for ch in self.channels.keys() if self.channels[ch].enabled],
            metadata={"task_id": task_id, "blocker": blocker},
        )

    def notify_milestone(
        self,
        milestone: str,
        details: str,
        source: str = "nexus",
    ) -> Dict[str, Any]:
        """Notify humans of a milestone/reached goal."""
        return self.notify(
            title=f"🎯 Milestone: {milestone}",
            message=details,
            level="success",
            source=source,
            channels=["dashboard"],
        )

    def list_notifications(
        self,
        limit: int = 50,
        level: Optional[str] = None,
        unacknowledged_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List recent notifications."""
        notifications: List[Dict[str, Any]] = []

        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        notif = json.loads(line)
                        if isinstance(notif, dict):
                            notifications.append(notif)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        # Filter
        if level:
            notifications = [n for n in notifications if n.get("level") == level]
        if unacknowledged_only:
            notifications = [n for n in notifications if not n.get("acknowledged")]

        # Sort by created_at descending
        notifications.sort(key=lambda n: n.get("created_at", ""), reverse=True)

        return notifications[:limit]

    def acknowledge_notification(self, notification_id: str, acknowledged_by: str = "user") -> Dict[str, Any]:
        """Mark a notification as acknowledged."""
        notifications = self.list_notifications(limit=1000)
        found = None

        for notif in notifications:
            if notif.get("id") == notification_id:
                notif["acknowledged"] = True
                notif["acknowledged_by"] = acknowledged_by
                notif["acknowledged_at"] = self._now_iso()
                found = notif
                break

        if found:
            # Rewrite file with updated notification
            try:
                with open(self.state_path, "w", encoding="utf-8") as f:
                    for notif in notifications:
                        f.write(json.dumps(notif, ensure_ascii=False) + "\n")
            except Exception:
                pass

            return {"success": True, "notification": found}

        return {"success": False, "error": "Notification not found"}

    def get_stats(self) -> Dict[str, Any]:
        """Get notification statistics."""
        notifications = self.list_notifications(limit=500)

        by_level: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        unacknowledged = 0
        delivered = 0

        for notif in notifications:
            level = notif.get("level", "unknown")
            by_level[level] = by_level.get(level, 0) + 1

            source = notif.get("source", "unknown")
            by_source[source] = by_source.get(source, 0) + 1

            if not notif.get("acknowledged"):
                unacknowledged += 1
            if notif.get("delivered"):
                delivered += 1

        return {
            "total": len(notifications),
            "delivered": delivered,
            "unacknowledged": unacknowledged,
            "by_level": by_level,
            "by_source": by_source,
            "channels_available": list(self.channels.keys()),
            "channels_enabled": [ch for ch, c in self.channels.items() if c.enabled],
        }

    def cleanup(self, max_age_hours: int = 168) -> Dict[str, Any]:
        """Clean up old notifications (default: 7 days)."""
        cutoff = self._now() - timedelta(hours=max_age_hours)
        cutoff_iso = cutoff.isoformat()

        notifications = self.list_notifications(limit=10000)
        before = len(notifications)

        notifications = [n for n in notifications if n.get("created_at", "") >= cutoff_iso]

        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                for notif in notifications:
                    f.write(json.dumps(notif, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return {
            "success": True,
            "removed": before - len(notifications),
            "remaining": len(notifications),
        }


# ============================================
# Singleton
# ============================================

_notifier: Optional[HumanNotifier] = None
_notifier_lock = threading.Lock()


def get_notifier() -> HumanNotifier:
    """Get the singleton notifier instance."""
    global _notifier
    with _notifier_lock:
        if _notifier is None:
            _notifier = HumanNotifier()
        return _notifier


def notify_humans(
    title: str,
    message: str,
    level: str = "info",
    source: str = "nexus",
    channels: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Convenience function to send notification."""
    return get_notifier().notify(
        title=title,
        message=message,
        level=level,
        source=source,
        channels=channels,
        **kwargs,
    )


def notify_urgent(title: str, message: str, **kwargs) -> Dict[str, Any]:
    """Convenience function for urgent notifications."""
    return get_notifier().notify_urgent(title=title, message=message, **kwargs)


def notify_help_needed(from_orion: str, help_type: str, description: str, **kwargs) -> Dict[str, Any]:
    """Convenience function for help requests."""
    return get_notifier().notify_help_needed(
        from_orion=from_orion,
        help_type=help_type,
        description=description,
        **kwargs,
    )
