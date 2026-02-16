"""
Provider profile store (BYOK - Bring Your Own Key).

Stores client-facing model/provider configuration so users can attach their own
API keys and map providers per agent at runtime.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now().isoformat()


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}{'*' * (len(text) - 6)}{text[-2:]}"


def _merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged.get(key, {}), value)
        elif value is not None:
            merged[key] = value
    return merged


class ProviderProfileStore:
    """Persistent BYOK profile used by dashboard + runtime."""

    AGENTS = ["orion", "guardian", "nova", "pixel", "cipher", "flux", "echo"]

    def __init__(self, path: Path | None = None):
        self.path = path or Path("data/config/provider_profile.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default_profile(self) -> Dict[str, Any]:
        glm_key = str(os.getenv("GLM_API_KEY", "")).strip()
        glm_base = str(os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "")).strip()
        google_key = str(os.getenv("GOOGLE_API_KEY", "")).strip()
        minimax_key = str(os.getenv("MINIMAX_API_KEY", "")).strip()
        minimax_model = str(os.getenv("MINIMAX_MODEL", "MiniMax-M2.5")).strip() or "MiniMax-M2.5"
        minimax_base = str(
            os.getenv("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic")
        ).strip() or "https://api.minimax.io/anthropic"

        profile: Dict[str, Any] = {
            "version": 1,
            "updated_at": _now_iso(),
            "providers": {
                "glm": {
                    "label": "GLM (z.ai)",
                    "enabled": bool(glm_key),
                    "api_key": glm_key,
                    "api_base": glm_base or "https://api.z.ai/api/openai/v1",
                },
                "google": {
                    "label": "Google Gemini",
                    "enabled": bool(google_key),
                    "api_key": google_key,
                    "api_base": "https://generativelanguage.googleapis.com/v1beta",
                },
                "anthropic": {
                    "label": "Anthropic-compatible (MiniMax / Claude gateway)",
                    "enabled": bool(minimax_key),
                    "api_key": minimax_key,
                    "api_base": minimax_base,
                    "default_model": minimax_model,
                },
                "openai_compatible": {
                    "label": "OpenAI-compatible",
                    "enabled": False,
                    "api_key": "",
                    "api_base": "https://api.openai.com/v1",
                    "default_model": "gpt-4.1-mini",
                },
                "codex_cli": {
                    "label": "Codex CLI subscription",
                    "enabled": _bool_env("CODEX_SUBSCRIPTION_AVAILABLE", False),
                    "cli_cmd": str(os.getenv("CODEX_CLI_CMD", "codex")).strip() or "codex",
                    "cli_args": str(os.getenv("CODEX_CLI_ARGS", "-p")).strip() or "-p",
                    "model_high": str(os.getenv("CODEX_SUBSCRIPTION_MODEL", "codex-5.3-x2")).strip()
                    or "codex-5.3-x2",
                    "model_low": str(os.getenv("CODEX_SUBSCRIPTION_LOW_MODEL", "codex-5.3-mini")).strip()
                    or "codex-5.3-mini",
                },
                "claude_cli": {
                    "label": "Claude Code CLI subscription",
                    "enabled": _bool_env("CLAUDE_CODE_SUBSCRIPTION_AVAILABLE", False),
                    "cli_cmd": str(os.getenv("CLAUDE_CLI_CMD", "claude")).strip() or "claude",
                    "cli_args": str(os.getenv("CLAUDE_CLI_ARGS", "-p")).strip() or "-p",
                    "model": str(os.getenv("CLAUDE_CODE_MODEL", "claude-code")).strip() or "claude-code",
                },
                "gemini_cli": {
                    "label": "Gemini CLI subscription",
                    "enabled": _bool_env("GEMINI_SUBSCRIPTION_AVAILABLE", False),
                    "cli_cmd": str(os.getenv("GEMINI_CLI_CMD", "gemini")).strip() or "gemini",
                    "cli_args": str(os.getenv("GEMINI_CLI_ARGS", "-m {model} -p")).strip() or "-m {model} -p",
                    "model_flash": str(os.getenv("GEMINI_SUBSCRIPTION_FLASH_MODEL", "gemini-3.0-flash")).strip()
                    or "gemini-3.0-flash",
                    "model_pro": str(os.getenv("GEMINI_SUBSCRIPTION_PRO_MODEL", "gemini-3.0-pro")).strip()
                    or "gemini-3.0-pro",
                },
            },
            "agent_bindings": {
                "orion": {"provider": "glm", "model": str(os.getenv("ORCHESTRATOR_MODEL", "glm-5")), "smart_routing": True},
                "guardian": {"provider": "glm", "model": str(os.getenv("GUARDIAN_MODEL", "glm-5")), "smart_routing": True},
                "nova": {"provider": "glm", "model": str(os.getenv("CODE_MODEL", "glm-5")), "smart_routing": True},
                "pixel": {"provider": "glm", "model": str(os.getenv("VISION_MODEL", "glm-4.6v")), "smart_routing": True},
                "cipher": {"provider": "glm", "model": str(os.getenv("SECURITY_MODEL", "glm-5")), "smart_routing": True},
                "flux": {"provider": "glm", "model": str(os.getenv("DEVOPS_MODEL", "glm-5")), "smart_routing": True},
                "echo": {"provider": "google", "model": str(os.getenv("TEST_MODEL", "gemini-2.0-flash")), "smart_routing": True},
            },
            "routing": {
                "smart_routing_enabled": _bool_env("ENABLE_SMART_ROUTING", True),
                "auto_subscription_cost_routing": _bool_env("ENABLE_AUTO_SUBSCRIPTION_COST_ROUTING", True),
                "keep_current_model_first": _bool_env("ROUTER_KEEP_CURRENT_MODEL_FIRST", False),
            },
        }
        return profile

    def _normalize(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        defaults = self._default_profile()
        merged = _merge_dict(defaults, profile or {})

        providers = merged.get("providers")
        if not isinstance(providers, dict):
            merged["providers"] = defaults["providers"]
        agent_bindings = merged.get("agent_bindings")
        if not isinstance(agent_bindings, dict):
            merged["agent_bindings"] = defaults["agent_bindings"]
        for agent in self.AGENTS:
            if not isinstance(merged["agent_bindings"].get(agent), dict):
                merged["agent_bindings"][agent] = defaults["agent_bindings"].get(agent, {})
            row = merged["agent_bindings"][agent]
            row.setdefault("provider", defaults["agent_bindings"][agent].get("provider", "glm"))
            row.setdefault("model", defaults["agent_bindings"][agent].get("model", ""))
            row.setdefault("smart_routing", True)

        if not isinstance(merged.get("routing"), dict):
            merged["routing"] = defaults["routing"]
        merged["version"] = 1
        merged["updated_at"] = str(merged.get("updated_at") or _now_iso())
        return merged

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            default_profile = self._default_profile()
            self.save(default_profile)
            return default_profile
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            payload = self._default_profile()
            self.save(payload)
            return payload
        return self._normalize(payload if isinstance(payload, dict) else {})

    def save(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize(profile)
        normalized["updated_at"] = _now_iso()
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass
        return normalized

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        current = self.load()
        merged = _merge_dict(current, patch or {})
        return self.save(merged)

    def get(self, masked: bool = False) -> Dict[str, Any]:
        profile = self.load()
        if not masked:
            return profile

        redacted = json.loads(json.dumps(profile))
        providers = redacted.get("providers", {})
        if isinstance(providers, dict):
            for cfg in providers.values():
                if not isinstance(cfg, dict):
                    continue
                raw_key = str(cfg.get("api_key", "") or "")
                cfg["has_api_key"] = bool(raw_key)
                if raw_key:
                    cfg["api_key"] = _mask_secret(raw_key)
        return redacted

    def get_catalog(self) -> Dict[str, Any]:
        return {
            "providers": [
                {"id": "glm", "label": "GLM (z.ai)", "kind": "api", "aliases": ["zai", "z.ai"]},
                {"id": "google", "label": "Google Gemini", "kind": "api", "aliases": ["gemini"]},
                {
                    "id": "anthropic",
                    "label": "Claude API (Anthropic-compatible / MiniMax)",
                    "kind": "api",
                    "aliases": ["claude", "claude_api", "minimax"],
                },
                {"id": "openai_compatible", "label": "OpenAI-compatible", "kind": "api", "aliases": ["openai"]},
                {"id": "codex_cli", "label": "Codex CLI subscription", "kind": "subscription", "aliases": ["codex"]},
                {
                    "id": "claude_cli",
                    "label": "Claude Code CLI subscription",
                    "kind": "subscription",
                    "aliases": ["claude_code", "claude-code"],
                },
                {"id": "gemini_cli", "label": "Gemini CLI subscription", "kind": "subscription", "aliases": []},
            ],
            "agents": list(self.AGENTS),
        }
