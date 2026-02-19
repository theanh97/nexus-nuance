"""Tests for OmniscientScout module."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import src.brain.omniscient_scout as _mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect DATA_DIR and reset singleton."""
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_scout", None)
    monkeypatch.setattr(_mod, "_LLM_AVAILABLE", False)


@pytest.fixture
def scout(tmp_path):
    with patch.object(_mod, "DATA_DIR", tmp_path):
        s = _mod.OmniscientScout(brain=None)
        s.data_dir = tmp_path
        s.sources_file = tmp_path / "sources.json"
        s.findings_file = tmp_path / "findings.jsonl"
        return s


# ---------------------------------------------------------------------------
# Source dataclass
# ---------------------------------------------------------------------------

class TestSourceDataclass:
    def test_default_fields(self):
        s = _mod.Source(name="test", category="ai", url="https://example.com",
                       scan_interval_minutes=60, parser_type="html")
        assert s.enabled is True
        assert s.total_findings == 0
        assert s.parser_type == "html"

    def test_custom_fields(self):
        s = _mod.Source(name="rss_src", category="news", url="https://rss.example.com",
                       parser_type="rss", scan_interval_minutes=30)
        assert s.parser_type == "rss"
        assert s.scan_interval_minutes == 30


# ---------------------------------------------------------------------------
# Init & source registration
# ---------------------------------------------------------------------------

class TestScoutInit:
    def test_sources_populated(self, scout):
        assert len(scout.sources) > 0

    def test_data_dir_created(self, scout):
        assert scout.data_dir.exists()

    def test_findings_cache_empty(self, scout):
        assert len(scout.findings_cache) == 0


# ---------------------------------------------------------------------------
# get_source_stats
# ---------------------------------------------------------------------------

class TestSourceStats:
    def test_returns_dict(self, scout):
        stats = scout.get_source_stats()
        assert isinstance(stats, dict)
        assert "total_sources" in stats
        assert "enabled_sources" in stats
        assert "by_category" in stats

    def test_total_matches_enabled(self, scout):
        stats = scout.get_source_stats()
        total = stats["total_sources"]
        enabled = stats["enabled_sources"]
        assert enabled <= total

    def test_by_category_not_empty(self, scout):
        stats = scout.get_source_stats()
        assert len(stats["by_category"]) > 0


# ---------------------------------------------------------------------------
# score_source_quality
# ---------------------------------------------------------------------------

class TestScoreSourceQuality:
    def test_known_source(self, scout):
        name = list(scout.sources.keys())[0]
        score = scout.score_source_quality(name)
        assert isinstance(score, dict)
        assert "quality_score" in score
        assert 0 <= score["quality_score"] <= 1
        assert "reasons" in score

    def test_unknown_source(self, scout):
        score = scout.score_source_quality("nonexistent_source_xyz")
        assert "error" in score


# ---------------------------------------------------------------------------
# get_ranked_sources
# ---------------------------------------------------------------------------

class TestRankedSources:
    def test_returns_list(self, scout):
        ranked = scout.get_ranked_sources()
        assert isinstance(ranked, list)
        assert len(ranked) > 0

    def test_sorted_by_score(self, scout):
        ranked = scout.get_ranked_sources()
        scores = [r["quality_score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# get_recent_findings
# ---------------------------------------------------------------------------

class TestRecentFindings:
    def test_empty_cache(self, scout):
        findings = scout.get_recent_findings(limit=10)
        assert findings == []

    def test_respects_limit(self, scout):
        # Manually add items to cache
        for i in range(5):
            scout.findings_cache.append({"title": f"finding_{i}", "source": "test"})
        findings = scout.get_recent_findings(limit=3)
        assert len(findings) == 3


# ---------------------------------------------------------------------------
# scan_source (mocked network)
# ---------------------------------------------------------------------------

class TestScanSource:
    def test_scan_unknown_source(self, scout):
        result = scout.scan_source("nonexistent_xyz")
        assert isinstance(result, list)
        assert len(result) == 0 or (len(result) == 1 and "error" in result[0])

    @patch("urllib.request.urlopen")
    def test_scan_handles_network_error(self, mock_urlopen, scout):
        mock_urlopen.side_effect = Exception("connection refused")
        name = list(scout.sources.keys())[0]
        result = scout.scan_source(name)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Singleton factories
# ---------------------------------------------------------------------------

class TestFactories:
    def test_get_scout_singleton(self):
        a = _mod.get_scout()
        b = _mod.get_scout()
        assert a is b

    def test_get_source_stats_wrapper(self):
        stats = _mod.get_source_stats()
        assert "total_sources" in stats
