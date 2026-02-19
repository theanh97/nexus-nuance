import src.brain.auto_evolution as ae


def test_categories_are_persisted(monkeypatch, tmp_path):
    data_dir = tmp_path / "data" / "brain"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(ae, "DATA_DIR", data_dir)
    monkeypatch.setattr(ae.AutoDiscoveryEngine, "_start_discovery_thread", lambda self: None)

    engine = ae.AutoDiscoveryEngine()
    engine.categories["custom_test"] = ae.Category(
        name="custom_test",
        keywords=["custom"],
        priority=5,
        sources_count=0,
        last_discovery="2026-01-01T00:00:00",
        expansion_potential=0.5,
    )
    engine._save()

    reloaded = ae.AutoDiscoveryEngine()
    assert "custom_test" in reloaded.categories
