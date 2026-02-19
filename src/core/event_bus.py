"""
Lightweight in-process event bus for NEXUS.

Allows any module to emit events (action executed, knowledge learned, errors)
which listeners (dashboard, logging, metrics) can subscribe to.

Usage:
    from core.event_bus import emit_event, subscribe

    # Subscribe
    subscribe("action.completed", my_handler)

    # Emit
    emit_event("action.completed", {"action": "run_shell", "success": True})
"""

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


_lock = threading.Lock()
_subscribers: Dict[str, List[Callable]] = {}
_recent_events: List[Dict] = []
_MAX_RECENT = 200


def subscribe(event_type: str, handler: Callable[[Dict], None]) -> None:
    """Register a handler for an event type. Handlers run synchronously."""
    with _lock:
        _subscribers.setdefault(event_type, []).append(handler)


def unsubscribe(event_type: str, handler: Callable) -> None:
    """Remove a handler."""
    with _lock:
        handlers = _subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)


def emit_event(event_type: str, data: Optional[Dict] = None) -> None:
    """Emit an event to all subscribers. Non-blocking: handler errors are swallowed."""
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }

    with _lock:
        _recent_events.append(event)
        if len(_recent_events) > _MAX_RECENT:
            del _recent_events[: len(_recent_events) - _MAX_RECENT]
        handlers = list(_subscribers.get(event_type, []))
        # Also notify wildcard subscribers
        handlers.extend(_subscribers.get("*", []))

    for handler in handlers:
        try:
            handler(event)
        except Exception:
            pass  # Never let a subscriber crash the emitter


def get_recent_events(limit: int = 50, event_type: Optional[str] = None) -> List[Dict]:
    """Return recent events, optionally filtered by type."""
    with _lock:
        events = list(_recent_events)
    if event_type:
        events = [e for e in events if e["type"] == event_type]
    return events[-limit:]


def clear() -> None:
    """Clear all subscribers and recent events (for testing)."""
    with _lock:
        _subscribers.clear()
        _recent_events.clear()
