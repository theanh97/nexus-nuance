"""Tests for the Self-Reminder Engine.

Validates periodic rule/principle re-reading, change detection,
interval scheduling, and event emission.
"""

import json
import time
import threading
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.brain.self_reminder import (
    SelfReminderEngine,
    PrincipleSource,
    get_self_reminder,
    reset_instance,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_instance()
    yield
    reset_instance()


@pytest.fixture()
def principle_dir(tmp_path):
    """Create temporary principle files."""
    rules = tmp_path / "docs" / "memory"
    rules.mkdir(parents=True)

    (rules / "guardrails.md").write_text(
        "# Guardrails\n- Rule 1: Never break production\n- Rule 2: Auto-resolve safe decisions\n",
        encoding="utf-8",
    )
    (rules / "learning.md").write_text(
        "# Learning\n- Daily cycle: Collect, Digest, Filter, Apply\n* Spaced repetition\n",
        encoding="utf-8",
    )
    (rules / "operations.md").write_text(
        "# Ops Runbook\n- Check health\n- Monitor budget\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_sources(base_path: Path, intervals: dict = None) -> list:
    """Build source configs relative to base_path."""
    intervals = intervals or {}
    return [
        {
            "path": "docs/memory/guardrails.md",
            "name": "Guardrails",
            "priority": 10,
            "interval_sec": intervals.get("guardrails", 5),
            "category": "guardrails",
        },
        {
            "path": "docs/memory/learning.md",
            "name": "Learning Principles",
            "priority": 8,
            "interval_sec": intervals.get("learning", 10),
            "category": "learning",
        },
        {
            "path": "docs/memory/operations.md",
            "name": "Operations",
            "priority": 5,
            "interval_sec": intervals.get("operations", 20),
            "category": "operations",
        },
    ]


class TestSelfReminderBasic:
    """Basic functionality tests."""

    def test_initial_check_reads_all_sources(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        results = engine.check_and_remind()
        assert len(results) == 3  # All sources read on first check
        assert all(r["content_hash"] for r in results)
        assert all(r["key_points_count"] > 0 for r in results)

    def test_second_check_respects_intervals(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir, intervals={"guardrails": 1, "learning": 100, "operations": 100})
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        # First check: all 3 sources
        engine.check_and_remind()

        # Immediate second check: nothing due (intervals not elapsed)
        results2 = engine.check_and_remind()
        assert len(results2) == 0

        # Wait 1.1 seconds - only guardrails should be due
        time.sleep(1.1)
        results3 = engine.check_and_remind()
        assert len(results3) == 1
        assert results3[0]["source_name"] == "Guardrails"

    def test_disabled_engine_returns_empty(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        engine = SelfReminderEngine(
            sources=_make_sources(principle_dir),
            log_path=principle_dir / "log.jsonl",
            enabled=False,
        )
        assert engine.check_and_remind() == []
        assert engine.force_remind_all() == []

    def test_force_remind_all(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir, intervals={"guardrails": 9999, "learning": 9999, "operations": 9999})
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        # First run
        engine.check_and_remind()

        # Force remind should bypass intervals
        results = engine.force_remind_all()
        assert len(results) == 3

    def test_missing_source_file_skipped(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = [
            {"path": "docs/memory/nonexistent.md", "name": "Missing", "priority": 5, "interval_sec": 1, "category": "test"},
        ]
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")
        results = engine.check_and_remind()
        assert len(results) == 0


class TestChangeDetection:
    """Test content change detection."""

    def test_detects_content_change(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir, intervals={"guardrails": 0, "learning": 9999, "operations": 9999})
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        # First read
        r1 = engine.check_and_remind()
        hash1 = r1[0]["content_hash"]
        assert r1[0]["changed"] is False  # First read is never "changed"

        # Modify file
        gf = principle_dir / "docs" / "memory" / "guardrails.md"
        gf.write_text(
            "# Guardrails V2\n- Rule 1: NEW RULE\n- Rule 2: UPDATED\n- Rule 3: ADDED\n",
            encoding="utf-8",
        )

        # Second read should detect change
        r2 = engine.check_and_remind()
        assert len(r2) == 1
        assert r2[0]["changed"] is True
        assert r2[0]["content_hash"] != hash1

    def test_no_change_flag_when_content_same(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir, intervals={"guardrails": 0, "learning": 9999, "operations": 9999})
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        engine.check_and_remind()
        r2 = engine.check_and_remind()
        assert len(r2) == 1
        assert r2[0]["changed"] is False


class TestEventCallback:
    """Test event bus integration."""

    def test_callback_called_on_remind(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        events = []
        engine.set_event_callback(lambda event_type, data: events.append((event_type, data)))

        engine.check_and_remind()
        assert len(events) == 3
        assert all(e[0] == "self_reminder.triggered" for e in events)
        assert all("source_name" in e[1] for e in events)

    def test_callback_error_does_not_crash(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        def bad_callback(event_type, data):
            raise RuntimeError("callback boom")

        engine.set_event_callback(bad_callback)

        # Should NOT raise
        results = engine.check_and_remind()
        assert len(results) == 3


class TestSourceManagement:
    """Test dynamic source add/remove."""

    def test_add_source_at_runtime(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        engine = SelfReminderEngine(sources=[], log_path=principle_dir / "log.jsonl")

        assert len(engine._sources) == 0
        engine.add_source({
            "path": "docs/memory/guardrails.md",
            "name": "Dynamic Source",
            "priority": 7,
            "interval_sec": 60,
            "category": "test",
        })
        assert len(engine._sources) == 1

        results = engine.check_and_remind()
        assert len(results) == 1
        assert results[0]["source_name"] == "Dynamic Source"

    def test_remove_source(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        assert len(engine._sources) == 3
        removed = engine.remove_source("Learning Principles")
        assert removed is True
        assert len(engine._sources) == 2

        removed2 = engine.remove_source("NonExistent")
        assert removed2 is False


class TestStatus:
    """Test status and stats reporting."""

    def test_get_status_structure(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        status = engine.get_status()
        assert status["enabled"] is True
        assert status["total_sources"] == 3
        assert status["total_reminders"] == 0
        assert len(status["sources"]) == 3

        engine.check_and_remind()
        status2 = engine.get_status()
        assert status2["total_reminders"] == 3
        assert all(s["last_read_at"] is not None for s in status2["sources"])

    def test_get_stats_categories(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        stats = engine.get_stats()
        assert set(stats["categories"]) == {"guardrails", "learning", "operations"}


class TestLogPersistence:
    """Test reminder log writing."""

    def test_log_written_after_remind(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        log_path = principle_dir / "reminder_log.jsonl"
        sources = _make_sources(principle_dir)
        engine = SelfReminderEngine(sources=sources, log_path=log_path)

        engine.check_and_remind()

        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

        for line in lines:
            record = json.loads(line)
            assert "source_name" in record
            assert "timestamp" in record
            assert "content_hash" in record


class TestThreadSafety:
    """Test concurrent access."""

    def test_concurrent_check_and_remind(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        sources = _make_sources(principle_dir, intervals={"guardrails": 0, "learning": 0, "operations": 0})
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        all_results = []
        lock = threading.Lock()

        def worker():
            results = engine.check_and_remind()
            with lock:
                all_results.extend(results)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have results without errors
        assert engine.get_stats()["total_reminders"] > 0


class TestPriorityOrdering:
    """Test that higher priority sources get shorter intervals."""

    def test_high_priority_checked_more_often(self, principle_dir, monkeypatch):
        monkeypatch.setattr("src.brain.self_reminder.PROJECT_ROOT", principle_dir)
        # guardrails: priority 10, interval 0.1s
        # operations: priority 5, interval 100s
        sources = _make_sources(principle_dir, intervals={"guardrails": 0, "learning": 100, "operations": 100})
        engine = SelfReminderEngine(sources=sources, log_path=principle_dir / "log.jsonl")

        # First run: all
        engine.check_and_remind()

        # Subsequent runs: only guardrails (interval=0)
        for _ in range(5):
            results = engine.check_and_remind()
            if results:
                assert all(r["source_name"] == "Guardrails" for r in results)
