"""Tests for API request metrics collector."""

import src.api.brain_api as brain_api


class TestRequestMetrics:
    def test_record_and_snapshot(self):
        m = brain_api._RequestMetrics()
        m.record("/api/nexus/status", "GET", 200, 15.3)
        m.record("/api/nexus/status", "GET", 200, 25.7)
        snap = m.snapshot()
        entry = snap["GET /api/nexus/status"]
        assert entry["count"] == 2
        assert entry["errors"] == 0
        assert entry["min_ms"] == 15.3
        assert entry["max_ms"] == 25.7
        assert entry["avg_ms"] == pytest.approx(20.5, abs=0.1)

    def test_error_counting(self):
        m = brain_api._RequestMetrics()
        m.record("/api/nexus/execute", "POST", 200, 100)
        m.record("/api/nexus/execute", "POST", 409, 50)
        m.record("/api/nexus/execute", "POST", 500, 30)
        snap = m.snapshot()
        entry = snap["POST /api/nexus/execute"]
        assert entry["count"] == 3
        assert entry["errors"] == 2

    def test_empty_snapshot(self):
        m = brain_api._RequestMetrics()
        assert m.snapshot() == {}

    def test_multiple_endpoints(self):
        m = brain_api._RequestMetrics()
        m.record("/a", "GET", 200, 10)
        m.record("/b", "POST", 201, 20)
        snap = m.snapshot()
        assert len(snap) == 2
        assert "GET /a" in snap
        assert "POST /b" in snap


import pytest
