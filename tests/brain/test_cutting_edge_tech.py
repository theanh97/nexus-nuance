"""Tests for CuttingEdgeIntegrator, AdvancedRAGSystem, HierarchicalMemorySystem."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import src.brain.cutting_edge_tech as _mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    """Redirect DATA_DIR and reset singletons."""
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_integrator", None)
    monkeypatch.setattr(_mod, "_rag", None)
    monkeypatch.setattr(_mod, "_memory", None)
    # Ensure LLM fallback (deterministic)
    monkeypatch.setattr(_mod, "_LLM_AVAILABLE", False)


@pytest.fixture
def integrator(tmp_path):
    with patch.object(_mod, "DATA_DIR", tmp_path):
        obj = _mod.CuttingEdgeIntegrator(brain=None)
        obj.data_dir = tmp_path
        obj.innovations_file = tmp_path / "tech_innovations.json"
        return obj


@pytest.fixture
def rag(tmp_path):
    with patch.object(_mod, "DATA_DIR", tmp_path):
        obj = _mod.AdvancedRAGSystem()
        obj.data_dir = tmp_path
        obj.index_file = tmp_path / "advanced_rag_index.json"
        return obj


@pytest.fixture
def memory(tmp_path):
    with patch.object(_mod, "DATA_DIR", tmp_path):
        obj = _mod.HierarchicalMemorySystem()
        obj.data_dir = tmp_path
        obj.memory_file = tmp_path / "hierarchical_memory.json"
        return obj


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _mod.cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _mod.cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        assert _mod.cosine_similarity([0, 0], [1, 1]) == 0.0


# ---------------------------------------------------------------------------
# CuttingEdgeIntegrator
# ---------------------------------------------------------------------------

class TestIntegrator:
    def test_innovations_not_empty(self, integrator):
        assert len(integrator.innovations) > 0

    def test_get_priority_implementations(self, integrator):
        result = integrator.get_priority_implementations()
        assert isinstance(result, list)
        assert all(isinstance(i, _mod.TechInnovation) for i in result)

    def test_priority_returns_list(self, integrator):
        result = integrator.get_priority_implementations()
        assert len(result) >= 1
        # implementation_complexity is a string (low/medium/high), so just check ordering exists
        assert all(hasattr(i, "nexus_applicability") for i in result)

    def test_generate_implementation_plan(self, integrator):
        plan = integrator.generate_implementation_plan()
        assert isinstance(plan, dict)
        assert "phases" in plan
        assert "generated_at" in plan

    def test_plan_phases_not_empty(self, integrator):
        plan = integrator.generate_implementation_plan()
        assert len(plan["phases"]) > 0


# ---------------------------------------------------------------------------
# AdvancedRAGSystem
# ---------------------------------------------------------------------------

class TestAdvancedRAG:
    def test_index_document(self, rag):
        rag.index_document("doc1", "Hello world of quantum computing")
        assert "doc1" in rag.documents

    def test_hybrid_search_returns_results(self, rag):
        rag.index_document("d1", "quantum computing advances")
        rag.index_document("d2", "classical music theory")
        results = rag.hybrid_search("quantum")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_search_result_fields(self, rag):
        rag.index_document("d1", "test content here")
        results = rag.hybrid_search("test")
        assert len(results) > 0
        r = results[0]
        assert "doc_id" in r
        assert "score" in r

    def test_search_empty_index(self, rag):
        results = rag.hybrid_search("anything")
        assert results == []

    def test_index_with_metadata(self, rag):
        rag.index_document("d1", "content", metadata={"author": "test"})
        assert rag.documents["d1"]["metadata"]["author"] == "test"


# ---------------------------------------------------------------------------
# HierarchicalMemorySystem
# ---------------------------------------------------------------------------

class TestHierarchicalMemory:
    def test_add_memory(self, memory):
        memory.add_memory("test fact", memory_type="fact", importance=0.8)
        stats = memory.get_stats()
        assert stats["total_items"] >= 1

    def test_retrieve(self, memory):
        memory.add_memory("quantum entanglement breakthrough")
        results = memory.retrieve("quantum")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_retrieve_empty(self, memory):
        results = memory.retrieve("nothing here")
        assert results == []

    def test_get_stats_structure(self, memory):
        stats = memory.get_stats()
        assert "total_items" in stats

    def test_add_multiple_types(self, memory):
        memory.add_memory("event 1", memory_type="event")
        memory.add_memory("fact 1", memory_type="fact")
        stats = memory.get_stats()
        assert stats["total_items"] >= 2


# ---------------------------------------------------------------------------
# Singleton factories
# ---------------------------------------------------------------------------

class TestFactories:
    def test_get_integrator_singleton(self):
        a = _mod.get_integrator()
        b = _mod.get_integrator()
        assert a is b

    def test_get_advanced_rag_singleton(self):
        a = _mod.get_advanced_rag()
        b = _mod.get_advanced_rag()
        assert a is b

    def test_get_hierarchical_memory_singleton(self):
        a = _mod.get_hierarchical_memory()
        b = _mod.get_hierarchical_memory()
        assert a is b
