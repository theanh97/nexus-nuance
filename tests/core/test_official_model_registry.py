import json
from pathlib import Path

from src.core.official_model_registry import (
    get_official_sources,
    load_model_registry_snapshot,
    refresh_model_registry_from_profile,
)


def test_official_sources_have_required_docs():
    sources = get_official_sources()
    assert "google" in sources
    assert "anthropic" in sources
    assert "openai_compatible" in sources
    assert str(sources["google"].get("models_doc", "")).startswith("https://")


def test_refresh_registry_from_profile_writes_snapshot(monkeypatch, tmp_path):
    def _fake_fetch(provider_id, provider_cfg, timeout_sec=8):
        if provider_id == "google":
            return ["gemini-2.0-flash", "gemini-2.5-pro"], ""
        return [], "provider_not_supported_for_api_sync"

    monkeypatch.setattr("src.core.official_model_registry._fetch_provider_models", _fake_fetch)
    profile = {
        "providers": {
            "google": {"enabled": True, "api_key": "x", "api_base": "https://generativelanguage.googleapis.com/v1beta"},
            "glm": {"enabled": False},
        }
    }
    out = tmp_path / "registry.json"
    snapshot = refresh_model_registry_from_profile(profile=profile, timeout_sec=3, path=out)
    assert out.exists()
    assert snapshot["providers"]["google"]["status"] == "ok"
    assert snapshot["providers"]["google"]["count"] == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["providers"]["google"]["models"][0] == "gemini-2.0-flash"


def test_load_registry_snapshot_defaults_when_missing(tmp_path):
    missing = tmp_path / "missing.json"
    payload = load_model_registry_snapshot(path=missing)
    assert payload["providers"] == {}
    assert "official_sources" in payload
