from src.brain import cutting_edge_tech as cet


def test_advanced_rag_persists_indexes(monkeypatch, tmp_path):
    data_dir = tmp_path / "data" / "brain"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(cet, "DATA_DIR", data_dir)

    rag = cet.AdvancedRAGSystem()
    rag.index_document("doc1", "autonomous memory persistence test", {"source": "test"})
    before = rag.hybrid_search("memory persistence", top_k=5)
    assert before

    rag_reloaded = cet.AdvancedRAGSystem()
    after = rag_reloaded.hybrid_search("memory persistence", top_k=5)

    assert after
    assert len(rag_reloaded.bm25_index) > 0
    assert len(rag_reloaded.vector_index) > 0
