from src.brain.nexus_brain import MemoryHub


def test_memory_stats_counts_sources_and_types():
    hub = MemoryHub()
    stats = hub.get_stats()

    total_by_source = sum(stats["knowledge_by_source"].values())
    total_by_type = sum(stats["knowledge_by_type"].values())

    assert total_by_source == stats["total_knowledge"]
    assert total_by_type == stats["total_knowledge"]
