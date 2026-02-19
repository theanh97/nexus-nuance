"""Tests for ProcessSingleton runtime guard."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.core.runtime_guard import ProcessSingleton


class TestProcessSingleton:
    def test_acquire_and_release(self, tmp_path):
        lock_file = str(tmp_path / "test.lock")
        ps = ProcessSingleton("test", lock_file)
        acquired, meta = ps.acquire()
        assert acquired is True
        assert meta["pid"] == os.getpid()
        assert meta["name"] == "test"
        ps.release()

    def test_double_acquire_same_instance(self, tmp_path):
        lock_file = str(tmp_path / "test.lock")
        ps = ProcessSingleton("test", lock_file)
        ok1, _ = ps.acquire()
        ok2, _ = ps.acquire()
        # Second acquire on same instance should succeed (idempotent)
        assert ok1 is True
        assert ok2 is True
        ps.release()

    def test_metadata_contains_required_fields(self, tmp_path):
        lock_file = str(tmp_path / "test.lock")
        ps = ProcessSingleton("test", lock_file)
        acquired, meta = ps.acquire()
        assert acquired is True
        assert "pid" in meta
        assert "name" in meta
        assert "started_at" in meta
        assert "cwd" in meta
        ps.release()

    def test_extra_metadata(self, tmp_path):
        lock_file = str(tmp_path / "test.lock")
        ps = ProcessSingleton("test", lock_file)
        acquired, meta = ps.acquire(extra={"version": "1.0"})
        assert acquired is True
        assert meta.get("version") == "1.0"
        ps.release()

    def test_release_cleans_up(self, tmp_path):
        lock_file = tmp_path / "test.lock"
        ps = ProcessSingleton("test", str(lock_file))
        ps.acquire()
        ps.release()
        # Lock file may or may not exist after release (implementation detail)
        # but the singleton should not be acquired
        assert ps._acquired is False

    def test_pid_exists_with_current_pid(self):
        result = ProcessSingleton._pid_exists(os.getpid())
        assert result is True

    def test_pid_exists_with_invalid_pid(self):
        result = ProcessSingleton._pid_exists(-1)
        assert result is None

    def test_pid_exists_with_none(self):
        result = ProcessSingleton._pid_exists(None)
        assert result is None

    def test_pid_exists_with_string(self):
        result = ProcessSingleton._pid_exists("not_a_pid")
        assert result is None

    def test_build_metadata(self, tmp_path):
        lock_file = str(tmp_path / "test.lock")
        ps = ProcessSingleton("mylock", lock_file)
        meta = ps._build_metadata(extra={"custom": "field"})
        assert meta["name"] == "mylock"
        assert meta["pid"] == os.getpid()
        assert meta["custom"] == "field"

    def test_read_existing_metadata_no_file(self, tmp_path):
        lock_file = str(tmp_path / "nonexistent.lock")
        ps = ProcessSingleton("test", lock_file)
        meta = ps._read_existing_metadata()
        assert meta == {}

    def test_read_existing_metadata_valid_file(self, tmp_path):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": 12345, "name": "old"}), encoding="utf-8")
        ps = ProcessSingleton("test", str(lock_file))
        meta = ps._read_existing_metadata()
        assert meta["pid"] == 12345

    def test_read_existing_metadata_corrupted(self, tmp_path):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text("not json{{{", encoding="utf-8")
        ps = ProcessSingleton("test", str(lock_file))
        meta = ps._read_existing_metadata()
        assert meta == {}

    def test_inspect_no_file(self, tmp_path):
        lock_file = str(tmp_path / "nonexistent.lock")
        info = ProcessSingleton.inspect(lock_file)
        assert isinstance(info, dict)
        assert info.get("exists") is False

    def test_inspect_with_file(self, tmp_path):
        lock_file = tmp_path / "test.lock"
        lock_file.write_text(json.dumps({"pid": os.getpid(), "name": "test"}), encoding="utf-8")
        info = ProcessSingleton.inspect(str(lock_file))
        assert isinstance(info, dict)
        assert info.get("exists") is True

    def test_name_defaults_to_runtime(self, tmp_path):
        lock_file = str(tmp_path / "test.lock")
        ps = ProcessSingleton(None, lock_file)
        assert ps.name == "runtime"

    def test_lock_path_parent_created(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "test.lock"
        ps = ProcessSingleton("test", str(deep_path))
        assert deep_path.parent.exists()
