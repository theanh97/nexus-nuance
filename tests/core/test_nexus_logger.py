"""Tests for centralized nexus_logger module."""

import json
import logging
import os

import pytest

import src.core.nexus_logger as _mod


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch):
    """Reset the configured flag so each test reconfigures."""
    monkeypatch.setattr(_mod, "_CONFIGURED", False)
    # Clean up any handlers added during test
    nexus_root = logging.getLogger("nexus")
    old_handlers = list(nexus_root.handlers)
    yield
    nexus_root.handlers = old_handlers


class TestGetLogger:
    def test_returns_logger(self):
        lg = _mod.get_logger("test_module")
        assert isinstance(lg, logging.Logger)

    def test_prefixes_with_nexus(self):
        lg = _mod.get_logger("mymod")
        assert lg.name == "nexus.mymod"

    def test_already_prefixed(self):
        lg = _mod.get_logger("nexus.already")
        assert lg.name == "nexus.already"

    def test_idempotent_setup(self):
        _mod.setup_logging()
        count1 = len(logging.getLogger("nexus").handlers)
        _mod.setup_logging()
        count2 = len(logging.getLogger("nexus").handlers)
        assert count1 == count2


class TestConsoleFormatter:
    def test_format_contains_level(self):
        fmt = _mod._ConsoleFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "INFO" in result
        assert "hello world" in result

    def test_format_error_level(self):
        fmt = _mod._ConsoleFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="fail", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "ERROR" in result


class TestJSONFormatter:
    def test_format_valid_json(self):
        fmt = _mod._JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="warning msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        data = json.loads(result)
        assert data["level"] == "WARNING"
        assert data["msg"] == "warning msg"
        assert "ts" in data

    def test_format_with_exception(self):
        fmt = _mod._JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="err", args=(), exc_info=exc_info,
        )
        result = fmt.format(record)
        data = json.loads(result)
        assert "exc" in data
        assert "ValueError" in data["exc"]


class TestLogFileEnv:
    def test_file_handler_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "_CONFIGURED", False)
        log_file = tmp_path / "test.log"
        monkeypatch.setenv("NEXUS_LOG_FILE", str(log_file))
        _mod.setup_logging()
        lg = _mod.get_logger("file_test")
        lg.info("file test message")
        # Flush handlers
        for h in logging.getLogger("nexus").handlers:
            h.flush()
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "file test message" in content


class TestLogLevel:
    def test_default_info(self, monkeypatch):
        monkeypatch.setattr(_mod, "_CONFIGURED", False)
        monkeypatch.delenv("NEXUS_LOG_LEVEL", raising=False)
        _mod.setup_logging()
        root = logging.getLogger("nexus")
        assert root.level == logging.INFO

    def test_debug_level(self, monkeypatch):
        monkeypatch.setattr(_mod, "_CONFIGURED", False)
        monkeypatch.setenv("NEXUS_LOG_LEVEL", "DEBUG")
        _mod.setup_logging()
        root = logging.getLogger("nexus")
        assert root.level == logging.DEBUG
