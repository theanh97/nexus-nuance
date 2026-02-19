"""Tests for ScreenMonitor - Continuous Screen Observation Engine."""

import threading
import time
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from core.screen_monitor import (
    ScreenMonitor,
    ScreenState,
    ScreenEvent,
    get_screen_monitor,
    reset_instance,
)


class TestScreenState:
    def test_default_state(self):
        state = ScreenState(timestamp="2026-01-01T00:00:00")
        assert state.timestamp == "2026-01-01T00:00:00"
        assert state.screenshot_path is None
        assert state.image_hash is None
        assert state.terminal_content == ""
        assert state.changed is False
        assert state.change_type == ""

    def test_changed_state(self):
        state = ScreenState(
            timestamp="2026-01-01T00:00:00",
            changed=True,
            change_type="screen+terminal",
            active_app="Terminal",
        )
        assert state.changed is True
        assert state.change_type == "screen+terminal"
        assert state.active_app == "Terminal"


class TestScreenEvent:
    def test_event_creation(self):
        event = ScreenEvent(
            event_type="screen_changed",
            timestamp="2026-01-01T00:00:00",
            details={"change_type": "screen"},
        )
        assert event.event_type == "screen_changed"
        assert event.details["change_type"] == "screen"
        assert event.screen_state is None


class TestScreenMonitorInit:
    def test_default_init(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False, detect_active_app=False)
        assert monitor.interval_sec >= 0.5
        assert monitor.running is False
        assert monitor.total_captures == 0

    def test_min_interval(self):
        monitor = ScreenMonitor(interval_sec=0.1, capture_screenshots=False, read_terminal=False)
        assert monitor.interval_sec == 0.5  # Min clamped

    def test_custom_interval(self):
        monitor = ScreenMonitor(interval_sec=10, capture_screenshots=False, read_terminal=False)
        assert monitor.interval_sec == 10

    def test_screenshot_dir_created(self, tmp_path):
        monitor = ScreenMonitor(
            screenshot_dir=str(tmp_path / "screenshots"),
            capture_screenshots=False,
            read_terminal=False,
        )
        assert (tmp_path / "screenshots").exists()


class TestScreenMonitorCallbacks:
    def test_register_callback(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)
        cb = MagicMock()
        monitor.on_change(cb)
        assert len(monitor._callbacks) == 1

    def test_emit_calls_callback(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)
        cb = MagicMock()
        monitor.on_change(cb)

        event = ScreenEvent(event_type="test", timestamp="now")
        monitor._emit(event)
        cb.assert_called_once_with(event)

    def test_emit_handles_callback_error(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)

        def bad_cb(event):
            raise ValueError("boom")

        monitor.on_change(bad_cb)
        # Should not raise
        monitor._emit(ScreenEvent(event_type="test", timestamp="now"))


class TestScreenMonitorObserve:
    def test_observe_once_no_capture(self):
        monitor = ScreenMonitor(
            capture_screenshots=False,
            read_terminal=False,
            detect_active_app=False,
        )
        state = monitor._observe_once()
        assert state.timestamp
        assert state.screenshot_path is None
        assert state.change_type == "none"
        assert monitor.total_captures == 1

    def test_observe_detects_no_change_on_second(self):
        monitor = ScreenMonitor(
            capture_screenshots=False,
            read_terminal=False,
            detect_active_app=False,
        )
        state1 = monitor._observe_once()
        state2 = monitor._observe_once()
        assert not state2.changed
        assert monitor.total_captures == 2


class TestScreenMonitorStartStop:
    def test_start_stop(self):
        monitor = ScreenMonitor(
            interval_sec=0.5,
            capture_screenshots=False,
            read_terminal=False,
            detect_active_app=False,
        )
        monitor.start()
        assert monitor.running is True
        time.sleep(0.8)
        assert monitor.total_captures > 0
        monitor.stop()
        assert monitor.running is False

    def test_double_start(self):
        monitor = ScreenMonitor(
            interval_sec=0.5,
            capture_screenshots=False,
            read_terminal=False,
            detect_active_app=False,
        )
        monitor.start()
        monitor.start()  # Should not create second thread
        assert monitor.running is True
        monitor.stop()


class TestScreenMonitorStatus:
    def test_get_status(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)
        status = monitor.get_status()
        assert "running" in status
        assert "total_captures" in status
        assert "interval_sec" in status
        assert status["running"] is False

    def test_get_current_state_none(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)
        assert monitor.get_current_state() is None

    def test_get_recent_history_empty(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)
        assert monitor.get_recent_history() == []


class TestScreenMonitorHashImage:
    def test_hash_nonexistent_file(self):
        monitor = ScreenMonitor(capture_screenshots=False, read_terminal=False)
        result = monitor._hash_image("/nonexistent/file.png")
        assert result == ""


class TestScreenMonitorSingleton:
    def test_get_instance(self):
        reset_instance()
        m1 = get_screen_monitor(read_terminal=False, detect_active_app=False, capture_screenshots=False)
        m2 = get_screen_monitor()
        assert m1 is m2
        reset_instance()

    def test_reset_instance(self):
        reset_instance()
        m1 = get_screen_monitor(read_terminal=False, detect_active_app=False, capture_screenshots=False)
        reset_instance()
        m2 = get_screen_monitor(read_terminal=False, detect_active_app=False, capture_screenshots=False)
        assert m1 is not m2
        reset_instance()
