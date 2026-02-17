"""
Official model registry sync.

Fetches available model lists from official provider APIs and stores a local
snapshot for routing/ops visibility.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


DEFAULT_SNAPSHOT_PATH = Path("data/state/official_model_registry.json")


def _now_iso() -> str:
    return datetime.now().isoformat()


def get_official_sources() -> Dict[str, Dict[str, str]]:
    """Official references used by sync process."""
    return {
        "openai_compatible": {
            "models_api_pattern": "{api_base}/models",
            "models_doc": "https://platform.openai.com/docs/api-reference/models/list",
            "models_overview": "https://platform.openai.com/docs/models",
            "pricing_doc": "https://platform.openai.com/docs/pricing",
        },
        "google": {
            "models_api_pattern": "https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            "models_doc": "https://ai.google.dev/api/rest/generativelanguage/models/list",
            "models_overview": "https://ai.google.dev/gemini-api/docs/models",
            "pricing_doc": "https://ai.google.dev/pricing",
        },
        "anthropic": {
            "models_api_pattern": "{api_base}/models",
            "models_doc": "https://docs.anthropic.com/en/api/models-list",
            "models_overview": "https://docs.anthropic.com/en/docs/models-overview",
            "pricing_doc": "https://www.anthropic.com/pricing",
        },
        "glm": {
            "models_api_pattern": "{api_base}/models",
            "models_doc": "https://platform.openai.com/docs/api-reference/models/list",
            "models_overview": "https://platform.openai.com/docs/models",
            "pricing_doc": "https://platform.openai.com/docs/pricing",
            "note": "GLM is treated as OpenAI-compatible endpoint for model-list sync.",
        },
    }


def _safe_base_url(url: str) -> str:
    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/v1"):
        return text
    return f"{text}/v1"


def _http_json(url: str, headers: Dict[str, str], timeout_sec: int = 8) -> Dict[str, Any]:
    req = urlrequest.Request(url, headers=headers, method="GET")
    with urlrequest.urlopen(req, timeout=max(2, int(timeout_sec))) as resp:
        raw = resp.read()
    payload = json.loads(raw.decode("utf-8", errors="ignore"))
    return payload if isinstance(payload, dict) else {}


def _extract_model_ids(payload: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    if not isinstance(payload, dict):
        return ids

    # OpenAI/Anthropic style
    data = payload.get("data")
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("id", "")).strip()
            if model_id:
                ids.append(model_id)

    # Gemini style
    models = payload.get("models")
    if isinstance(models, list):
        for row in models:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            # Normalize models/<id> -> <id>
            if name.startswith("models/"):
                name = name.split("/", 1)[1]
            ids.append(name)

    # unique + deterministic order
    seen = set()
    ordered: List[str] = []
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _fetch_provider_models(provider_id: str, provider_cfg: Dict[str, Any], timeout_sec: int = 8) -> Tuple[List[str], str]:
    provider = str(provider_id or "").strip().lower()
    cfg = provider_cfg if isinstance(provider_cfg, dict) else {}
    key = str(cfg.get("api_key", "")).strip()
    base = _safe_base_url(str(cfg.get("api_base", "")).strip())
    if provider in {"openai_compatible", "glm"}:
        if not key or not base:
            return [], "missing_api_key_or_base_url"
        url = f"{base}/models"
        payload = _http_json(url, headers={"Authorization": f"Bearer {key}"}, timeout_sec=timeout_sec)
        return _extract_model_ids(payload), ""
    if provider == "anthropic":
        if not key:
            return [], "missing_api_key"
        base = base or "https://api.anthropic.com/v1"
        url = f"{base}/models"
        payload = _http_json(
            url,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout_sec=timeout_sec,
        )
        return _extract_model_ids(payload), ""
    if provider == "google":
        if not key:
            return [], "missing_api_key"
        base_url = str(cfg.get("api_base", "https://generativelanguage.googleapis.com/v1beta")).strip().rstrip("/")
        query = urlparse.urlencode({"key": key})
        url = f"{base_url}/models?{query}"
        payload = _http_json(url, headers={}, timeout_sec=timeout_sec)
        return _extract_model_ids(payload), ""
    return [], "provider_not_supported_for_api_sync"


def load_model_registry_snapshot(path: Path | None = None) -> Dict[str, Any]:
    target = Path(path or DEFAULT_SNAPSHOT_PATH)
    if not target.exists():
        return {
            "timestamp": None,
            "providers": {},
            "official_sources": get_official_sources(),
        }
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("timestamp", None)
    payload.setdefault("providers", {})
    payload.setdefault("official_sources", get_official_sources())
    return payload


def refresh_model_registry_from_profile(
    profile: Dict[str, Any],
    timeout_sec: int = 8,
    path: Path | None = None,
) -> Dict[str, Any]:
    providers = profile.get("providers") if isinstance(profile.get("providers"), dict) else {}
    snapshot: Dict[str, Any] = {
        "timestamp": _now_iso(),
        "providers": {},
        "official_sources": get_official_sources(),
    }
    for provider_id, row in providers.items():
        provider_key = str(provider_id or "").strip()
        provider_cfg = row if isinstance(row, dict) else {}
        enabled = bool(provider_cfg.get("enabled", False))
        if not enabled:
            snapshot["providers"][provider_key] = {
                "enabled": False,
                "status": "disabled",
                "models": [],
                "count": 0,
                "updated_at": _now_iso(),
            }
            continue
        try:
            models, warning = _fetch_provider_models(provider_key, provider_cfg, timeout_sec=timeout_sec)
            status = "ok" if models else ("warning" if warning else "empty")
            snapshot["providers"][provider_key] = {
                "enabled": True,
                "status": status,
                "warning": warning,
                "models": models[:300],
                "count": len(models),
                "updated_at": _now_iso(),
            }
        except urlerror.HTTPError as exc:
            snapshot["providers"][provider_key] = {
                "enabled": True,
                "status": "error",
                "error": f"http_{exc.code}",
                "models": [],
                "count": 0,
                "updated_at": _now_iso(),
            }
        except Exception as exc:
            snapshot["providers"][provider_key] = {
                "enabled": True,
                "status": "error",
                "error": str(exc)[:180],
                "models": [],
                "count": 0,
                "updated_at": _now_iso(),
            }

    target = Path(path or DEFAULT_SNAPSHOT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot

