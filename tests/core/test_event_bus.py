"""Tests for event bus module."""

import pytest

from src.core.event_bus import (
    emit_event,
    subscribe,
    unsubscribe,
    get_recent_events,
    clear,
)


@pytest.fixture(autouse=True)
def _clean():
    clear()
    yield
    clear()


class TestEmitAndSubscribe:
    def test_subscriber_receives_event(self):
        received = []
        subscribe("test.event", received.append)
        emit_event("test.event", {"key": "value"})
        assert len(received) == 1
        assert received[0]["type"] == "test.event"
        assert received[0]["data"]["key"] == "value"
        assert "timestamp" in received[0]

    def test_wildcard_subscriber(self):
        received = []
        subscribe("*", received.append)
        emit_event("any.event")
        emit_event("other.event")
        assert len(received) == 2

    def test_no_subscribers_no_error(self):
        emit_event("nobody.listening")  # should not raise

    def test_subscriber_error_swallowed(self):
        def bad_handler(event):
            raise RuntimeError("handler crash")

        subscribe("test.event", bad_handler)
        emit_event("test.event")  # should not raise

    def test_multiple_subscribers(self):
        a, b = [], []
        subscribe("test.event", a.append)
        subscribe("test.event", b.append)
        emit_event("test.event")
        assert len(a) == 1
        assert len(b) == 1


class TestUnsubscribe:
    def test_unsubscribe_stops_delivery(self):
        received = []
        subscribe("test.event", received.append)
        emit_event("test.event")
        assert len(received) == 1
        unsubscribe("test.event", received.append)
        emit_event("test.event")
        assert len(received) == 1

    def test_unsubscribe_nonexistent_no_error(self):
        unsubscribe("no.such", lambda e: None)


class TestRecentEvents:
    def test_stores_recent(self):
        for i in range(5):
            emit_event("test", {"i": i})
        events = get_recent_events()
        assert len(events) == 5

    def test_limit(self):
        for i in range(10):
            emit_event("test", {"i": i})
        events = get_recent_events(limit=3)
        assert len(events) == 3
        # Should be the last 3
        assert events[0]["data"]["i"] == 7

    def test_filter_by_type(self):
        emit_event("a.event")
        emit_event("b.event")
        emit_event("a.event")
        events = get_recent_events(event_type="a.event")
        assert len(events) == 2

    def test_max_recent_cap(self):
        for i in range(250):
            emit_event("flood", {"i": i})
        events = get_recent_events(limit=300)
        assert len(events) <= 200


class TestClear:
    def test_clear_removes_all(self):
        subscribe("test", lambda e: None)
        emit_event("test")
        clear()
        assert get_recent_events() == []
