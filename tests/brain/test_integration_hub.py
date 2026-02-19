"""Tests for NexusIntegrationHub core functionality."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _make_hub(tmp_path):
    """Create a hub instance with isolated data dir."""
    with patch("src.brain.integration_hub.DATA_DIR", tmp_path):
        from src.brain.integration_hub import NexusIntegrationHub
        hub = NexusIntegrationHub()
        hub.data_dir = tmp_path
        hub.status_file = tmp_path / "hub_status.json"
        return hub


class TestHubInit:
    def test_hub_creates_data_dir(self, tmp_path):
        hub = _make_hub(tmp_path)
        assert hub.data_dir.exists()

    def test_hub_initial_status(self, tmp_path):
        hub = _make_hub(tmp_path)
        assert hub.status["initialized"] is False
        assert hub.status["total_tasks"] == 0
        assert hub.status["total_knowledge"] == 0

    def test_hub_loads_existing_status(self, tmp_path):
        status_file = tmp_path / "hub_status.json"
        status_file.write_text(json.dumps({
            "initialized": True,
            "total_tasks": 42,
            "systems": {"brain": "active"},
        }), encoding="utf-8")
        hub = _make_hub(tmp_path)
        assert hub.status["total_tasks"] == 42
        assert hub.status["systems"]["brain"] == "active"


class TestSaveStatus:
    def test_save_creates_file(self, tmp_path):
        hub = _make_hub(tmp_path)
        hub.status["total_tasks"] = 5
        hub._save_status()
        assert hub.status_file.exists()
        data = json.loads(hub.status_file.read_text(encoding="utf-8"))
        assert data["total_tasks"] == 5
        assert "last_updated" in data


class TestLoadComponent:
    def test_load_success(self, tmp_path):
        hub = _make_hub(tmp_path)
        result = hub._load_component("test_sys", lambda: {"ok": True})
        assert result == {"ok": True}
        assert hub.status["systems"]["test_sys"] == "active"

    def test_load_returns_none(self, tmp_path):
        hub = _make_hub(tmp_path)
        result = hub._load_component("test_sys", lambda: None)
        assert result is None
        assert hub.status["systems"]["test_sys"] == "unavailable"
        assert hub.status["errors"]["test_sys"] == "loader_returned_none"

    def test_load_raises_exception(self, tmp_path):
        hub = _make_hub(tmp_path)
        result = hub._load_component("test_sys", lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert result is None
        assert hub.status["systems"]["test_sys"] == "error"
        assert "ValueError" in hub.status["errors"]["test_sys"]

    def test_load_reraises_memory_error(self, tmp_path):
        hub = _make_hub(tmp_path)
        with pytest.raises(MemoryError):
            hub._load_component("test_sys", lambda: (_ for _ in ()).throw(MemoryError()))


class TestQueryCache:
    def test_cache_stores_and_retrieves(self, tmp_path):
        hub = _make_hub(tmp_path)
        import hashlib
        from datetime import datetime
        query = "test query"
        cache_key = hashlib.md5(query.encode()).hexdigest()
        hub._query_cache[cache_key] = {
            "timestamp": datetime.now().isoformat(),
            "result": {"query": query, "sources": [], "cache_hit": False}
        }
        cached = hub._query_cache.get(cache_key)
        assert cached is not None
        assert cached["result"]["query"] == query

    def test_cache_ttl_default(self, tmp_path):
        hub = _make_hub(tmp_path)
        assert hub._cache_ttl == 300


class TestInitialize:
    def test_initialize_marks_initialized(self, tmp_path):
        hub = _make_hub(tmp_path)
        # All lazy components will fail to import in test env - that's fine
        results = hub.initialize()
        assert hub.initialized is True
        assert hub.status["initialized"] is True
        assert "systems" in results
        assert "timestamp" in results

    def test_initialize_saves_status(self, tmp_path):
        hub = _make_hub(tmp_path)
        hub.initialize()
        assert hub.status_file.exists()


class TestExecute:
    def test_execute_returns_result_dict(self, tmp_path):
        hub = _make_hub(tmp_path)
        result = hub.execute("test task", use_react=False)
        assert isinstance(result, dict)
        assert result["task"] == "test task"
        assert "timestamp" in result
        assert "steps" in result
        assert "completed_at" in result

    def test_execute_increments_task_count(self, tmp_path):
        hub = _make_hub(tmp_path)
        assert hub.status["total_tasks"] == 0
        hub.execute("task1", use_react=False)
        assert hub.status["total_tasks"] == 1
        hub.execute("task2", use_react=False)
        assert hub.status["total_tasks"] == 2

    def test_execute_with_mock_memory(self, tmp_path):
        hub = _make_hub(tmp_path)
        mock_memory = MagicMock()
        hub._memory = mock_memory
        hub.status["systems"]["memory"] = "active"
        result = hub.execute("remember this", use_react=False)
        mock_memory.add_memory.assert_called_once()
        steps = [s["step"] for s in result["steps"]]
        assert "memory_add" in steps


class TestLearn:
    def test_learn_returns_result_dict(self, tmp_path):
        hub = _make_hub(tmp_path)
        result = hub.learn("new knowledge", source="test")
        assert isinstance(result, dict)
        assert result["source"] == "test"
        assert result["content"] == "new knowledge"

    def test_learn_increments_knowledge_count(self, tmp_path):
        hub = _make_hub(tmp_path)
        hub.learn("fact 1")
        assert hub.status["total_knowledge"] == 1
        hub.learn("fact 2")
        assert hub.status["total_knowledge"] == 2


class TestMaintenance:
    def test_maintenance_returns_actions(self, tmp_path):
        hub = _make_hub(tmp_path)
        result = hub.run_maintenance()
        assert isinstance(result, dict)
        assert "actions" in result
        assert "timestamp" in result
        assert any(a["type"] == "cache_prune" for a in result["actions"])

    def test_maintenance_prunes_expired_cache(self, tmp_path):
        hub = _make_hub(tmp_path)
        import hashlib
        from datetime import datetime, timedelta
        # Add an expired cache entry
        old_time = (datetime.now() - timedelta(seconds=hub._cache_ttl + 60)).isoformat()
        key = hashlib.md5(b"old_query").hexdigest()
        hub._query_cache[key] = {"timestamp": old_time, "result": {"query": "old"}}
        assert len(hub._query_cache) == 1
        hub.run_maintenance()
        assert len(hub._query_cache) == 0

    def test_maintenance_keeps_fresh_cache(self, tmp_path):
        hub = _make_hub(tmp_path)
        import hashlib
        from datetime import datetime
        key = hashlib.md5(b"fresh_query").hexdigest()
        hub._query_cache[key] = {"timestamp": datetime.now().isoformat(), "result": {"query": "fresh"}}
        hub.run_maintenance()
        assert len(hub._query_cache) == 1
