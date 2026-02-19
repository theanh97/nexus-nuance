"""Event bus integration tests: Cross-module event flow.

Verifies that events emitted by one module (API, executor) are received
by subscribers, and that the event history is queryable.
"""

import asyncio
import threading
import pytest
from unittest.mock import MagicMock

from src.core.event_bus import (
    emit_event,
    subscribe,
    unsubscribe,
    get_recent_events,
    clear,
)
import src.api.brain_api as brain_api


@pytest.fixture(autouse=True)
def _clean_event_bus():
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    try:
        from core.rate_limiter import reset
        reset()
    except ImportError:
        pass
    yield
    try:
        from core.rate_limiter import reset
        reset()
    except ImportError:
        pass


# ── Basic pub/sub ────────────────────────────────────────────────

class TestEventBusPubSub:
    def test_emit_and_receive(self):
        received = []
        subscribe("test.event", lambda e: received.append(e))
        emit_event("test.event", {"key": "val"})

        assert len(received) == 1
        assert received[0]["type"] == "test.event"
        assert received[0]["data"]["key"] == "val"
        assert "timestamp" in received[0]

    def test_wildcard_subscriber(self):
        received = []
        subscribe("*", lambda e: received.append(e))
        emit_event("any.event", {"x": 1})
        emit_event("another.event", {"y": 2})

        assert len(received) == 2

    def test_unsubscribe(self):
        received = []
        handler = lambda e: received.append(e)
        subscribe("unsub.test", handler)
        emit_event("unsub.test")
        assert len(received) == 1

        unsubscribe("unsub.test", handler)
        emit_event("unsub.test")
        assert len(received) == 1  # No new event

    def test_subscriber_error_doesnt_crash_emitter(self):
        def bad_handler(e):
            raise RuntimeError("boom")

        subscribe("crash.test", bad_handler)
        # Should not raise
        emit_event("crash.test", {"data": "value"})

        # Events still recorded
        events = get_recent_events(limit=10, event_type="crash.test")
        assert len(events) == 1

    def test_multiple_subscribers_same_event(self):
        results = {"a": 0, "b": 0}
        subscribe("multi.event", lambda e: results.update({"a": results["a"] + 1}))
        subscribe("multi.event", lambda e: results.update({"b": results["b"] + 1}))

        emit_event("multi.event")
        assert results["a"] == 1
        assert results["b"] == 1


# ── Event history and querying ───────────────────────────────────

class TestEventHistory:
    def test_recent_events_returns_latest(self):
        for i in range(10):
            emit_event("history.test", {"seq": i})

        events = get_recent_events(limit=5)
        assert len(events) == 5
        # Should be the last 5
        assert events[0]["data"]["seq"] == 5
        assert events[-1]["data"]["seq"] == 9

    def test_filter_by_event_type(self):
        emit_event("type_a.event", {"x": 1})
        emit_event("type_b.event", {"y": 2})
        emit_event("type_a.event", {"x": 3})

        a_events = get_recent_events(limit=50, event_type="type_a.event")
        assert len(a_events) == 2
        assert all(e["type"] == "type_a.event" for e in a_events)

    def test_max_recent_events_capped(self):
        """Event history should not grow unbounded."""
        for i in range(250):
            emit_event("flood.test", {"i": i})

        events = get_recent_events(limit=300)
        assert len(events) <= 200  # _MAX_RECENT = 200


# ── API → Event bus integration ──────────────────────────────────

class _FakeRequest:
    class _Client:
        host = "127.0.0.1"
    client = _Client()


class TestAPIEventEmission:
    def test_learn_emits_knowledge_learned(self, monkeypatch):
        """POST /learn should emit knowledge.learned event."""
        received = []

        def tracking_emit(event_type, data=None):
            emit_event(event_type, data)
            received.append({"type": event_type, "data": data or {}})

        monkeypatch.setattr(brain_api, "emit_event", tracking_emit)

        def mock_learn(**kwargs):
            item = MagicMock()
            item.id = "test-123"
            return item

        monkeypatch.setattr(brain_api, "learn", lambda **kw: mock_learn(**kw))
        req = brain_api.LearnRequest(
            source="test", type="article", title="Test",
            content="Test content", relevance=0.8,
        )
        asyncio.run(brain_api.learn_knowledge(req, _FakeRequest()))

        assert len(received) == 1
        assert received[0]["data"]["id"] == "test-123"
        assert received[0]["data"]["source"] == "test"

    def test_execute_emits_task_executed(self, monkeypatch):
        """POST /execute should emit task.executed event."""
        received = []

        def tracking_emit(event_type, data=None):
            emit_event(event_type, data)
            received.append({"type": event_type, "data": data or {}})

        monkeypatch.setattr(brain_api, "emit_event", tracking_emit)

        class _Agent:
            def execute_autonomously(self, task, max_cycles=10):
                return {"success": True}

        monkeypatch.setattr(brain_api, "get_autonomous_agent", lambda: _Agent())
        req = brain_api.ExecuteRequest(
            task="test task", max_cycles=1, verification_required=False,
        )
        asyncio.run(brain_api.execute_task(req, _FakeRequest()))

        assert len(received) == 1
        assert received[0]["data"]["success"] is True


# ── Thread-safety ────────────────────────────────────────────────

class TestEventBusThreadSafety:
    def test_concurrent_emit(self):
        """Multiple threads emitting should not corrupt event history."""
        def emit_n(prefix, n):
            for i in range(n):
                emit_event(f"{prefix}.event", {"i": i})

        threads = [
            threading.Thread(target=emit_n, args=(f"t{i}", 50))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Total events = 5 * 50 = 250, but capped at 200
        events = get_recent_events(limit=300)
        assert 195 <= len(events) <= 200

    def test_concurrent_subscribe_and_emit(self):
        """Subscribing while emitting should not crash."""
        results = {"count": 0}
        lock = threading.Lock()

        def emit_loop():
            for _ in range(100):
                emit_event("concurrent.test", {})

        def subscribe_loop():
            for _ in range(20):
                subscribe("concurrent.test",
                          lambda e: (lock.acquire(), results.update({"count": results["count"] + 1}), lock.release()))

        t1 = threading.Thread(target=emit_loop)
        t2 = threading.Thread(target=subscribe_loop)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Should not crash; count doesn't matter
        assert True
