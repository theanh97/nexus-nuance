"""
Auto Dev Loop - Complete System Runner
Runs both the autonomous system and the monitoring dashboard

Usage:
    python run_system.py
"""

import os
import sys
import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.agents.orion import Orion
from src.agents.nova import Nova
from src.agents.pixel import Pixel
from src.agents.cipher import Cipher
from src.agents.echo import Echo
from src.agents.flux import Flux
from src.agents.guardian import Guardian
from src.core.message import ProjectContext, AgentMessage, MessageType, Priority, TaskResult
from src.core.model_router import ModelRouter, MODELS
from src.core.provider_profile import ProviderProfileStore
from src.core.runtime_sync import run_full_sync
from src.core.runtime_guard import ProcessSingleton
from src.core.prompt_system import get_prompt_system


class AutoDevSystem:
    """
    Complete autonomous development system with monitoring

    Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                         USER                                     │
    │                    (via Dashboard UI)                           │
    └──────────────────────────────┬──────────────────────────────────┘
                                   │
           ┌───────────────────────┴───────────────────────┐
           │                                               │
           ▼                                               ▼
    ┌──────────────┐                              ┌──────────────┐
    │   GUARDIAN   │◄────────────────────────────►│    ORION     │
    │  (Supervisor)│    Permission Requests        │   (PM)       │
    │              │    Decision Feedback           │              │
    └──────────────┘                                └──────────────┘
           │                                               │
           │                              ┌────────────────┼────────────────┐
           │                              │                │                │
           │                              ▼                ▼                ▼
           │                       ┌──────────┐     ┌──────────┐     ┌──────────┐
           │                       │   NOVA   │     │  PIXEL   │     │  CIPHER  │
           │                       │  (Code)  │     │  (UI/UX) │     │(Security)│
           │                       └──────────┘     └──────────┘     └──────────┘
           │                              │                │                │
           │                              └────────────────┼────────────────┘
           │                                               │
           │                              ┌────────────────┴────────────────┐
           │                              ▼                                 ▼
           │                       ┌──────────┐                      ┌──────────┐
           │                       │   ECHO   │                      │   FLUX   │
           │                       │   (QA)   │                      │ (DevOps) │
           │                       └──────────┘                      └──────────┘
           │
           └──────────► All actions logged to Dashboard for user visibility
    """

    def __init__(self):
        self.orion = None
        self.guardian = None
        self.dashboard_thread = None
        self.running = False
        self.main_loop = None
        self.guardian_watchdog_task: Optional[asyncio.Task] = None
        self.guardian_auto_recovery = True
        self.guardian_watchdog_interval_sec = int(os.getenv("GUARDIAN_WATCHDOG_INTERVAL_SEC", "8"))
        self.guardian_stuck_threshold_sec = int(os.getenv("GUARDIAN_STUCK_THRESHOLD_SEC", "120"))
        self.guardian_recovery_cooldown_sec = int(os.getenv("GUARDIAN_RECOVERY_COOLDOWN_SEC", "25"))
        self.last_guardian_intervention_at: Optional[datetime] = None
        self.manual_override_sec = int(os.getenv("MANUAL_OVERRIDE_SEC", "90"))
        self.manual_override_until: Optional[datetime] = None
        self.manual_override_reason: str = ""
        self.autonomy_profile = self._normalize_autonomy_profile(os.getenv("AUTONOMY_PROFILE", "balanced"))
        self.full_auto_user_pause_max_sec = max(
            60,
            int(os.getenv("MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC", "240")),
        )
        self.full_auto_maintenance_lease_sec = max(
            30,
            int(os.getenv("MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC", "180")),
        )
        self.cost_guard_mode = str(os.getenv("DEFAULT_COST_GUARD_MODE", "balanced")).strip().lower() or "balanced"
        self.cost_guard_last_updated_at: Optional[datetime] = None
        self.auto_cost_guard_enabled = str(os.getenv("AUTO_COST_GUARD_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.auto_cost_guard_warn_ratio = max(
            0.05,
            float(os.getenv("AUTO_COST_GUARD_WARN_RATIO", "0.72")),
        )
        self.auto_cost_guard_hard_ratio = max(
            self.auto_cost_guard_warn_ratio,
            float(os.getenv("AUTO_COST_GUARD_HARD_RATIO", "0.9")),
        )
        self.auto_cost_guard_recover_ratio = min(
            self.auto_cost_guard_warn_ratio - 0.05 if self.auto_cost_guard_warn_ratio > 0.1 else 0.4,
            float(os.getenv("AUTO_COST_GUARD_RECOVER_RATIO", "0.45")),
        )
        self.auto_cost_guard_cooldown_sec = max(20, int(os.getenv("AUTO_COST_GUARD_COOLDOWN_SEC", "60")))
        self.last_auto_cost_guard_adjust_at: Optional[datetime] = None
        self.last_auto_cost_guard_reason: str = ""
        self.provider_store = ProviderProfileStore()
        self.supervision_principles = [
            "Ưu tiên liên tục tự động: Orion phải tiếp tục chạy trừ khi user yêu cầu dừng.",
            "Ưu tiên giá trị người dùng: lọc nhiễu, chỉ giữ tín hiệu quan trọng.",
            "Gỡ stuck nhanh và an toàn để duy trì tiến độ dự án.",
            "Giám sát toàn cục: hiểu mục tiêu dự án, trạng thái runtime và chất lượng đầu ra.",
        ]
        try:
            feedback_rows = get_prompt_system().get_feedback_snapshot(limit=5).get("recent_feedback", [])
            for row in feedback_rows[-2:]:
                text = str((row or {}).get("text", "")).strip()
                if text:
                    self.supervision_principles.append(f"Không lặp lỗi user đã báo: {text[:180]}")
        except Exception:
            pass

    def _normalize_autonomy_profile(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"safe", "balanced", "full_auto"}:
            return text
        return "balanced"

    def _is_full_auto_profile(self) -> bool:
        return self.autonomy_profile == "full_auto"

    def _set_autonomy_profile(self, profile: Any, source: str = "runtime") -> str:
        normalized = self._normalize_autonomy_profile(profile)
        self.autonomy_profile = normalized
        if normalized == "full_auto":
            self.guardian_auto_recovery = True
            self.manual_override_until = None
            self.manual_override_reason = ""
        if self.guardian:
            self.guardian._log(f"🧠 Runtime autonomy profile -> {normalized.upper()} ({source})")
        return normalized

    async def initialize(self):
        """Initialize all components"""
        run_full_sync(write_env=False)

        print("=" * 70)
        print("  AUTO DEV LOOP - Initializing Complete System")
        print("=" * 70)
        print()

        # Create all agents
        nova = Nova()
        pixel = Pixel()
        cipher = Cipher()
        echo = Echo()
        flux = Flux()
        guardian = Guardian()

        # Create Orion (PM)
        self.orion = Orion()

        # Register agents with Orion
        self.orion.register_agent(nova)
        self.orion.register_agent(pixel)
        self.orion.register_agent(cipher)
        self.orion.register_agent(echo)
        self.orion.register_agent(flux)
        self.orion.register_agent(guardian)

        self.guardian = guardian

        # Start all agents
        await self.orion.start()
        self._apply_provider_profile(
            profile=self.provider_store.get(masked=False),
            source="startup",
            announce=False,
        )
        self._apply_cost_guard_mode(self.cost_guard_mode, announce=False)

        print()
        print("┌─────────────────────────────────────────────────────────────────┐")
        print("│  THE DREAM TEAM + GUARDIAN - Ready for Action                   │")
        print("├─────────────────────────────────────────────────────────────────┤")
        print("│                                                                 │")
        print("│  🌟 ORION    - Supreme PM (GLM-5)                              │")
        print("│  🛡️ GUARDIAN - Supervisor (GLM-5)                              │")
        print("│  💻 NOVA     - Code Architect (GLM-5)                          │")
        print("│  🎨 PIXEL    - UI/UX Visionary (GLM-4.6V)                      │")
        print("│  🔐 CIPHER   - Security Master (GLM-5)                         │")
        print("│  🧪 ECHO     - QA Engineer (Gemini Flash)                      │")
        print("│  🚀 FLUX     - DevOps (GLM-5)                                  │")
        print("│                                                                 │")
        print("└─────────────────────────────────────────────────────────────────┘")
        print()

        self._check_api_keys()

    def _cost_guard_profiles(self) -> Dict[str, Dict[str, Any]]:
        balanced = {
            "nova_stable_interval": max(1, int(os.getenv("ORION_NOVA_STABLE_INTERVAL", "2"))),
            "echo_interval": max(1, int(os.getenv("ECHO_TEST_INTERVAL", "4"))),
            "security_audit_interval": max(1, int(os.getenv("SECURITY_AUDIT_INTERVAL", "8"))),
            "security_fallback_audit_interval": max(2, int(os.getenv("SECURITY_FALLBACK_AUDIT_INTERVAL", "20"))),
            "max_backoff_sec": max(2, int(os.getenv("ORION_MAX_BACKOFF_SEC", "20"))),
            "prompt_max_injected_directives": max(
                1, int(os.getenv("PROMPT_SYSTEM_MAX_INJECTED_DIRECTIVES", "4"))
            ),
            "prompt_low_importance_directives": max(
                1, int(os.getenv("PROMPT_SYSTEM_LOW_IMPORTANCE_DIRECTIVES", "2"))
            ),
            "prompt_low_importance_compact": str(
                os.getenv("PROMPT_SYSTEM_LOW_IMPORTANCE_COMPACT", "true")
            ).strip().lower() in {"1", "true", "yes", "on"},
            "prompt_include_profile_low_importance": str(
                os.getenv("PROMPT_SYSTEM_INCLUDE_PROFILE_LOW_IMPORTANCE", "false")
            ).strip().lower() in {"1", "true", "yes", "on"},
            "auto_subscription_cost_routing": str(
                os.getenv("ENABLE_AUTO_SUBSCRIPTION_COST_ROUTING", "true")
            ).strip().lower() in {"1", "true", "yes", "on"},
            "keep_current_model_first": str(
                os.getenv("ROUTER_KEEP_CURRENT_MODEL_FIRST", "false")
            ).strip().lower() in {"1", "true", "yes", "on"},
        }
        economy = {
            "nova_stable_interval": max(3, int(balanced["nova_stable_interval"]) + 1),
            "echo_interval": max(6, int(balanced["echo_interval"]) + 2),
            "security_audit_interval": max(12, int(balanced["security_audit_interval"]) + 4),
            "security_fallback_audit_interval": max(28, int(balanced["security_fallback_audit_interval"]) + 8),
            "max_backoff_sec": max(24, int(balanced["max_backoff_sec"]) + 4),
            "prompt_max_injected_directives": min(3, int(balanced["prompt_max_injected_directives"])),
            "prompt_low_importance_directives": 1,
            "prompt_low_importance_compact": True,
            "prompt_include_profile_low_importance": False,
            "auto_subscription_cost_routing": True,
            "keep_current_model_first": False,
        }
        aggressive = {
            "nova_stable_interval": 1,
            "echo_interval": 2,
            "security_audit_interval": 4,
            "security_fallback_audit_interval": 10,
            "max_backoff_sec": 12,
            "prompt_max_injected_directives": max(6, int(balanced["prompt_max_injected_directives"])),
            "prompt_low_importance_directives": max(2, int(balanced["prompt_low_importance_directives"])),
            "prompt_low_importance_compact": False,
            "prompt_include_profile_low_importance": True,
            "auto_subscription_cost_routing": False,
            "keep_current_model_first": True,
        }
        return {
            "balanced": balanced,
            "economy": economy,
            "aggressive": aggressive,
        }

    def _cost_guard_snapshot(self) -> Dict[str, Any]:
        profiles = self._cost_guard_profiles()
        mode = self.cost_guard_mode if self.cost_guard_mode in profiles else "balanced"
        profile = profiles.get(mode, profiles["balanced"])
        budget = self._model_budget_summary()
        return {
            "mode": mode,
            "profile": profile,
            "updated_at": self.cost_guard_last_updated_at.isoformat() if self.cost_guard_last_updated_at else None,
            "budget": budget,
            "auto": {
                "enabled": bool(self.auto_cost_guard_enabled),
                "warn_ratio": float(self.auto_cost_guard_warn_ratio),
                "hard_ratio": float(self.auto_cost_guard_hard_ratio),
                "recover_ratio": float(self.auto_cost_guard_recover_ratio),
                "cooldown_sec": int(self.auto_cost_guard_cooldown_sec),
                "last_adjusted_at": self.last_auto_cost_guard_adjust_at.isoformat()
                if self.last_auto_cost_guard_adjust_at
                else None,
                "last_reason": self.last_auto_cost_guard_reason,
            },
        }

    def _model_budget_summary(self) -> Dict[str, Any]:
        try:
            summary = ModelRouter().get_usage_summary()
        except Exception:
            summary = {}
        total_cost = float(summary.get("total_cost_usd", 0.0) or 0.0)
        daily_budget = float(summary.get("daily_budget_usd", 0.0) or 0.0)
        ratio = float(summary.get("budget_ratio", 0.0) or 0.0)
        return {
            "total_cost_usd": round(total_cost, 6),
            "daily_budget_usd": round(daily_budget, 6),
            "budget_ratio": ratio,
        }

    def _provider_profile_snapshot(self, masked: bool = True) -> Dict[str, Any]:
        try:
            return self.provider_store.get(masked=masked)
        except Exception as exc:
            return {
                "version": 1,
                "updated_at": datetime.now().isoformat(),
                "error": str(exc),
            }

    def _normalize_provider_name(self, provider_name: str) -> str:
        raw = str(provider_name or "").strip().lower()
        if not raw:
            return "glm"
        aliases = {
            "zai": "glm",
            "z.ai": "glm",
            "gemini": "google",
            "claude": "anthropic",
            "claude_api": "anthropic",
            "anthropic_api": "anthropic",
            "minimax": "anthropic",
            "openai": "openai_compatible",
            "openai-compat": "openai_compatible",
            "openai_compat": "openai_compatible",
            "codex": "codex_cli",
            "claude_code": "claude_cli",
            "claude-code": "claude_cli",
        }
        return aliases.get(raw, raw)

    def _provider_credentials(self, providers: Dict[str, Any], provider_name: str) -> Dict[str, str]:
        requested = str(provider_name or "").strip().lower()
        resolved = self._normalize_provider_name(requested)
        cfg = providers.get(resolved, {}) if isinstance(providers, dict) else {}
        if not isinstance(cfg, dict) and isinstance(providers, dict):
            cfg = providers.get(requested, {})
        if not isinstance(cfg, dict):
            cfg = {}

        enabled = bool(cfg.get("enabled", False))
        api_key = str(cfg.get("api_key", "") or "").strip()
        api_base = str(cfg.get("api_base", "") or "").strip()
        model = str(cfg.get("default_model", "") or cfg.get("model", "") or "").strip()

        if resolved == "glm":
            api_base = api_base or str(os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "")).strip()
            model = model or str(os.getenv("CODE_MODEL", "glm-5")).strip()
        elif resolved == "google":
            api_base = api_base or "https://generativelanguage.googleapis.com/v1beta"
            model = model or str(os.getenv("TEST_MODEL", "gemini-2.0-flash")).strip()
        elif resolved == "anthropic":
            api_base = api_base or str(
                os.getenv("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic")
            ).strip()
            model = model or str(os.getenv("MINIMAX_MODEL", "MiniMax-M2.5")).strip()
        elif resolved == "openai_compatible":
            api_base = api_base or "https://api.openai.com/v1"
            model = model or "gpt-4.1-mini"
        elif resolved == "codex_cli":
            model = model or str(os.getenv("CODEX_SUBSCRIPTION_MODEL", "codex-5.3-x2")).strip()
        elif resolved == "claude_cli":
            model = model or str(os.getenv("CLAUDE_CODE_MODEL", "claude-code")).strip()
        elif resolved == "gemini_cli":
            model = model or str(os.getenv("GEMINI_SUBSCRIPTION_FLASH_MODEL", "gemini-3.0-flash")).strip()

        return {
            "provider": resolved,
            "enabled": bool(enabled),
            "api_key": api_key,
            "api_base": api_base,
            "model": model,
        }

    def _agent_object(self, name: str):
        if not self.orion:
            return None
        normalized = str(name or "").strip().lower()
        if normalized == "orion":
            return self.orion
        return self.orion.agents.get(normalized)

    def _apply_provider_profile(
        self,
        profile: Dict[str, Any],
        source: str = "dashboard",
        announce: bool = True,
    ) -> Dict[str, Any]:
        if not self.orion:
            return {"success": False, "error": "Orion not initialized"}

        providers = profile.get("providers", {}) if isinstance(profile, dict) else {}
        bindings = profile.get("agent_bindings", {}) if isinstance(profile, dict) else {}
        routing = profile.get("routing", {}) if isinstance(profile, dict) else {}

        if not isinstance(providers, dict):
            providers = {}
        if not isinstance(bindings, dict):
            bindings = {}
        if not isinstance(routing, dict):
            routing = {}

        glm_creds = self._provider_credentials(providers, "glm")
        google_creds = self._provider_credentials(providers, "google")
        anthropic_creds = self._provider_credentials(providers, "anthropic")
        openai_creds = self._provider_credentials(providers, "openai_compatible")

        if glm_creds["api_key"]:
            os.environ["GLM_API_KEY"] = glm_creds["api_key"]
        if glm_creds["api_base"]:
            os.environ["GLM_API_BASE"] = glm_creds["api_base"]
            os.environ["ZAI_OPENAI_BASE_URL"] = glm_creds["api_base"]
        if google_creds["api_key"]:
            os.environ["GOOGLE_API_KEY"] = google_creds["api_key"]
        if anthropic_creds["api_key"]:
            os.environ["MINIMAX_API_KEY"] = anthropic_creds["api_key"]
            os.environ["CLAUDE_CODE_API_KEY"] = anthropic_creds["api_key"]
        if anthropic_creds["api_base"]:
            os.environ["MINIMAX_ANTHROPIC_BASE_URL"] = anthropic_creds["api_base"]
            os.environ["CLAUDE_CODE_BASE_URL"] = anthropic_creds["api_base"]
        if anthropic_creds["model"]:
            os.environ["MINIMAX_MODEL"] = anthropic_creds["model"]

        codex_cli = providers.get("codex_cli", {}) if isinstance(providers.get("codex_cli"), dict) else {}
        claude_cli = providers.get("claude_cli", {}) if isinstance(providers.get("claude_cli"), dict) else {}
        gemini_cli = providers.get("gemini_cli", {}) if isinstance(providers.get("gemini_cli"), dict) else {}

        codex_enabled = bool(codex_cli.get("enabled", False))
        claude_enabled = bool(claude_cli.get("enabled", False))
        gemini_enabled = bool(gemini_cli.get("enabled", False))
        os.environ["CODEX_SUBSCRIPTION_AVAILABLE"] = "true" if codex_enabled else "false"
        os.environ["CLAUDE_CODE_SUBSCRIPTION_AVAILABLE"] = "true" if claude_enabled else "false"
        os.environ["GEMINI_SUBSCRIPTION_AVAILABLE"] = "true" if gemini_enabled else "false"

        if str(codex_cli.get("cli_cmd", "")).strip():
            os.environ["CODEX_CLI_CMD"] = str(codex_cli.get("cli_cmd")).strip()
        if str(codex_cli.get("cli_args", "")).strip():
            os.environ["CODEX_CLI_ARGS"] = str(codex_cli.get("cli_args")).strip()
        if str(codex_cli.get("model_high", "")).strip():
            os.environ["CODEX_SUBSCRIPTION_MODEL"] = str(codex_cli.get("model_high")).strip()
        if str(codex_cli.get("model_low", "")).strip():
            os.environ["CODEX_SUBSCRIPTION_LOW_MODEL"] = str(codex_cli.get("model_low")).strip()

        if str(claude_cli.get("cli_cmd", "")).strip():
            os.environ["CLAUDE_CLI_CMD"] = str(claude_cli.get("cli_cmd")).strip()
        if str(claude_cli.get("cli_args", "")).strip():
            os.environ["CLAUDE_CLI_ARGS"] = str(claude_cli.get("cli_args")).strip()
        if str(claude_cli.get("model", "")).strip():
            os.environ["CLAUDE_CODE_MODEL"] = str(claude_cli.get("model")).strip()

        if str(gemini_cli.get("cli_cmd", "")).strip():
            os.environ["GEMINI_CLI_CMD"] = str(gemini_cli.get("cli_cmd")).strip()
        if str(gemini_cli.get("cli_args", "")).strip():
            os.environ["GEMINI_CLI_ARGS"] = str(gemini_cli.get("cli_args")).strip()
        if str(gemini_cli.get("model_flash", "")).strip():
            os.environ["GEMINI_SUBSCRIPTION_FLASH_MODEL"] = str(gemini_cli.get("model_flash")).strip()
        if str(gemini_cli.get("model_pro", "")).strip():
            os.environ["GEMINI_SUBSCRIPTION_PRO_MODEL"] = str(gemini_cli.get("model_pro")).strip()

        try:
            if "codex-5.3-x2-subscription" in MODELS and os.getenv("CODEX_SUBSCRIPTION_MODEL"):
                MODELS["codex-5.3-x2-subscription"].name = str(os.getenv("CODEX_SUBSCRIPTION_MODEL", "")).strip()
            if "codex-5.3-mini-subscription" in MODELS and os.getenv("CODEX_SUBSCRIPTION_LOW_MODEL"):
                MODELS["codex-5.3-mini-subscription"].name = str(os.getenv("CODEX_SUBSCRIPTION_LOW_MODEL", "")).strip()
            if "claude-code-subscription" in MODELS and os.getenv("CLAUDE_CODE_MODEL"):
                MODELS["claude-code-subscription"].name = str(os.getenv("CLAUDE_CODE_MODEL", "")).strip()
            if "gemini-3.0-flash-subscription" in MODELS and os.getenv("GEMINI_SUBSCRIPTION_FLASH_MODEL"):
                MODELS["gemini-3.0-flash-subscription"].name = str(
                    os.getenv("GEMINI_SUBSCRIPTION_FLASH_MODEL", "")
                ).strip()
            if "gemini-3.0-pro-subscription" in MODELS and os.getenv("GEMINI_SUBSCRIPTION_PRO_MODEL"):
                MODELS["gemini-3.0-pro-subscription"].name = str(
                    os.getenv("GEMINI_SUBSCRIPTION_PRO_MODEL", "")
                ).strip()
        except Exception:
            pass

        runtime_agents: Dict[str, Any] = {"orion": self.orion}
        runtime_agents.update(self.orion.agents)

        for agent in runtime_agents.values():
            router = getattr(agent, "router", None)
            if router and isinstance(getattr(router, "api_keys", None), dict):
                if glm_creds["api_key"]:
                    router.api_keys["glm"] = glm_creds["api_key"]
                if google_creds["api_key"]:
                    router.api_keys["google"] = google_creds["api_key"]
                if anthropic_creds["api_key"]:
                    router.api_keys["anthropic"] = anthropic_creds["api_key"]
            if router and isinstance(getattr(router, "subscription_flags", None), dict):
                router.subscription_flags["subscription_codex"] = codex_enabled
                router.subscription_flags["subscription_claude"] = claude_enabled
                router.subscription_flags["subscription_gemini"] = gemini_enabled

        if "smart_routing_enabled" in routing:
            enabled = bool(routing.get("smart_routing_enabled"))
            for agent in runtime_agents.values():
                if hasattr(agent, "smart_routing_enabled"):
                    setattr(agent, "smart_routing_enabled", enabled)
        if "auto_subscription_cost_routing" in routing:
            enabled = bool(routing.get("auto_subscription_cost_routing"))
            for agent in runtime_agents.values():
                if hasattr(agent, "auto_subscription_cost_routing"):
                    setattr(agent, "auto_subscription_cost_routing", enabled)
        if "keep_current_model_first" in routing:
            enabled = bool(routing.get("keep_current_model_first"))
            for agent in runtime_agents.values():
                if hasattr(agent, "keep_current_model_first"):
                    setattr(agent, "keep_current_model_first", enabled)

        env_model_map = {
            "orion": "ORCHESTRATOR_MODEL",
            "guardian": "GUARDIAN_MODEL",
            "nova": "CODE_MODEL",
            "pixel": "VISION_MODEL",
            "cipher": "SECURITY_MODEL",
            "flux": "DEVOPS_MODEL",
            "echo": "TEST_MODEL",
        }
        applied_agents: Dict[str, Dict[str, Any]] = {}
        for agent_name, binding in bindings.items():
            if not isinstance(binding, dict):
                continue
            target = self._agent_object(agent_name)
            if not target:
                continue

            provider_name = str(binding.get("provider", "glm")).strip().lower() or "glm"
            creds = self._provider_credentials(providers, provider_name)
            provider_name = str(creds.get("provider", provider_name)).strip().lower() or "glm"
            model = str(binding.get("model", "") or creds.get("model", "")).strip()
            api_key = str(binding.get("api_key", "") or creds.get("api_key", "")).strip()
            api_base = str(binding.get("api_base", "") or creds.get("api_base", "")).strip()

            if api_key:
                target.api_key = api_key
            if api_base:
                target.api_base = api_base
            if model:
                target.model = model

            if "smart_routing" in binding and hasattr(target, "smart_routing_enabled"):
                target.smart_routing_enabled = bool(binding.get("smart_routing"))
            elif provider_name == "openai_compatible" and hasattr(target, "smart_routing_enabled"):
                target.smart_routing_enabled = bool(binding.get("smart_routing", False))

            router = getattr(target, "router", None)
            if router and isinstance(getattr(router, "api_keys", None), dict):
                if provider_name == "glm" and api_key:
                    router.api_keys["glm"] = api_key
                elif provider_name == "google" and api_key:
                    router.api_keys["google"] = api_key
                elif provider_name == "anthropic" and api_key:
                    router.api_keys["anthropic"] = api_key
                elif provider_name == "openai_compatible" and api_key:
                    router.api_keys["glm"] = api_key

            env_key = env_model_map.get(str(agent_name).strip().lower())
            if env_key and model:
                os.environ[env_key] = model

            applied_agents[str(agent_name).strip().lower()] = {
                "provider": provider_name,
                "model": model,
                "api_base": api_base,
                "smart_routing": bool(getattr(target, "smart_routing_enabled", True)),
            }

        pixel_agent = self.orion.agents.get("pixel")
        if pixel_agent is not None and google_creds["api_key"] and hasattr(pixel_agent, "fallback_api_key"):
            setattr(pixel_agent, "fallback_api_key", google_creds["api_key"])

        summary = {
            "success": True,
            "source": source,
            "applied_agents": applied_agents,
            "subscriptions": {
                "codex_cli": codex_enabled,
                "claude_cli": claude_enabled,
                "gemini_cli": gemini_enabled,
            },
            "routing": {
                "smart_routing_enabled": bool(routing.get("smart_routing_enabled", True)),
                "auto_subscription_cost_routing": bool(
                    routing.get("auto_subscription_cost_routing", True)
                ),
                "keep_current_model_first": bool(routing.get("keep_current_model_first", False)),
            },
        }
        if announce and self.orion:
            self.orion._log(
                "🔌 Provider profile applied: "
                f"{len(applied_agents)} agent bindings, "
                f"subs(codex={codex_enabled}, claude={claude_enabled}, gemini={gemini_enabled})"
            )
        return summary

    def _maybe_auto_adjust_cost_guard(self) -> Dict[str, Any]:
        if not self.auto_cost_guard_enabled or not self.orion:
            return {"changed": False, "reason": "disabled"}

        budget = self._model_budget_summary()
        ratio = float(budget.get("budget_ratio", 0.0) or 0.0)
        desired_mode = self.cost_guard_mode
        reason = ""

        if ratio >= self.auto_cost_guard_hard_ratio:
            desired_mode = "economy"
            reason = f"budget_ratio={ratio:.2f} >= hard={self.auto_cost_guard_hard_ratio:.2f}"
        elif ratio >= self.auto_cost_guard_warn_ratio and self.cost_guard_mode == "aggressive":
            desired_mode = "balanced"
            reason = f"budget_ratio={ratio:.2f} >= warn={self.auto_cost_guard_warn_ratio:.2f}"
        elif ratio >= self.auto_cost_guard_warn_ratio and self.cost_guard_mode == "balanced":
            desired_mode = "economy"
            reason = f"budget_ratio={ratio:.2f} >= warn={self.auto_cost_guard_warn_ratio:.2f}"
        elif ratio <= self.auto_cost_guard_recover_ratio and self.cost_guard_mode == "economy":
            desired_mode = "balanced"
            reason = f"budget_ratio={ratio:.2f} <= recover={self.auto_cost_guard_recover_ratio:.2f}"

        if desired_mode == self.cost_guard_mode:
            return {"changed": False, "mode": self.cost_guard_mode, "budget": budget}

        now = datetime.now()
        if self.last_auto_cost_guard_adjust_at:
            elapsed = (now - self.last_auto_cost_guard_adjust_at).total_seconds()
            if elapsed < self.auto_cost_guard_cooldown_sec:
                return {"changed": False, "reason": "cooldown", "budget": budget}

        previous_mode = self.cost_guard_mode
        applied = self._apply_cost_guard_mode(desired_mode, announce=True)
        self.last_auto_cost_guard_adjust_at = now
        self.last_auto_cost_guard_reason = reason or "auto_adjust"
        if self.orion:
            self.orion._log(
                f"🤖 Auto cost guard: {previous_mode.upper()} -> {desired_mode.upper()} "
                f"({self.last_auto_cost_guard_reason})"
            )
        return {
            "changed": bool(applied.get("success")),
            "previous_mode": previous_mode,
            "mode": desired_mode,
            "reason": self.last_auto_cost_guard_reason,
            "budget": budget,
        }

    def _apply_cost_guard_mode(self, mode: str, announce: bool = True) -> Dict[str, Any]:
        if not self.orion:
            return {"success": False, "error": "Orion not initialized"}

        profiles = self._cost_guard_profiles()
        resolved_mode = str(mode or "balanced").strip().lower()
        if resolved_mode not in profiles:
            resolved_mode = "balanced"
        profile = profiles[resolved_mode]

        self.orion.nova_stable_interval = int(profile["nova_stable_interval"])
        self.orion.echo_interval = int(profile["echo_interval"])
        self.orion.security_audit_interval = int(profile["security_audit_interval"])
        self.orion.security_fallback_audit_interval = int(profile["security_fallback_audit_interval"])
        self.orion.max_backoff_sec = int(profile["max_backoff_sec"])

        prompt_system = get_prompt_system()
        prompt_system.max_injected_directives = int(profile["prompt_max_injected_directives"])
        prompt_system.low_importance_injected_directives = int(profile["prompt_low_importance_directives"])
        prompt_system.low_importance_compact = bool(profile["prompt_low_importance_compact"])
        prompt_system.include_profile_for_low_importance = bool(
            profile["prompt_include_profile_low_importance"]
        )

        for agent in self.orion.agents.values():
            if hasattr(agent, "auto_subscription_cost_routing"):
                setattr(agent, "auto_subscription_cost_routing", bool(profile["auto_subscription_cost_routing"]))
            if hasattr(agent, "keep_current_model_first"):
                setattr(agent, "keep_current_model_first", bool(profile["keep_current_model_first"]))

        self.cost_guard_mode = resolved_mode
        self.cost_guard_last_updated_at = datetime.now()
        snapshot = self._cost_guard_snapshot()

        if announce:
            self.orion._log(
                "💸 Cost guard mode -> "
                f"{resolved_mode.upper()} "
                f"(nova every {profile['nova_stable_interval']} cycle, "
                f"echo/{profile['echo_interval']}, sec/{profile['security_audit_interval']})"
            )
        return {"success": True, **snapshot}

    def _check_api_keys(self):
        """Check API keys"""

        keys = {
            "GLM_API_KEY": os.getenv("GLM_API_KEY"),
            "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY")
        }

        print("📋 API Configuration:")
        for key, value in keys.items():
            status = "✅ Configured" if value else "❌ Missing"
            print(f"   {key}: {status}")
        print(f"   GLM_API_BASE: {os.getenv('GLM_API_BASE', 'unset')}")
        print()

    def start_dashboard(self, host: str = "localhost", port: int = 5000):
        """Start the monitoring dashboard in a separate thread"""

        def run_dashboard():
            from monitor.app import run_dashboard as _run_dashboard
            _run_dashboard(host=host, port=port)

        self.dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
        self.dashboard_thread.start()

        print(f"📊 Dashboard started at: http://{host}:{port}")
        print()

    async def run(
        self,
        goal: str = None,
        max_iterations: int = None,
        dashboard_host: str = "localhost",
        dashboard_port: int = 5000,
    ):
        """Run the complete system"""

        await self.initialize()

        if max_iterations is not None and self.orion:
            self.orion.max_iterations = max_iterations

        # Start dashboard
        self.start_dashboard(host=dashboard_host, port=dashboard_port)

        # Default goal
        if not goal:
            goal = "Build a modern, beautiful, and functional web application with dark mode, responsive design, and smooth animations"

        print("=" * 70)
        print("  STARTING AUTONOMOUS SYSTEM")
        print("=" * 70)
        print()
        print(f"  Project Goal: {goal}")
        print(f"  Dashboard: http://{dashboard_host}:{dashboard_port}")
        print()
        print("  The system runs AUTONOMOUSLY.")
        print("  Guardian supervises all actions and makes decisions.")
        print("  Monitor progress via the dashboard.")
        print()
        print("  Press Ctrl+C to shutdown.")
        print("-" * 70)

        self.running = True
        self.main_loop = asyncio.get_running_loop()

        # Initialize project context
        self.orion.context = ProjectContext(
            project_name="AutoDevProject",
            project_goal=goal
        )
        if self.guardian:
            self.guardian.update_supervision_context(
                project_goal=goal,
                principles=self.supervision_principles,
            )

        # Setup dashboard callbacks
        self._setup_dashboard_callbacks()
        self.guardian_watchdog_task = asyncio.create_task(self._guardian_watchdog_loop())

        try:
            await self.orion.run_infinite_loop()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutdown requested...")
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
        finally:
            await self.shutdown()

    def _age_seconds(self, iso_timestamp: Optional[str]) -> Optional[int]:
        if not iso_timestamp:
            return None
        try:
            delta = datetime.now() - datetime.fromisoformat(str(iso_timestamp))
            return max(0, int(delta.total_seconds()))
        except Exception:
            return None

    def _manual_override_active(self) -> bool:
        return bool(self.manual_override_until and datetime.now() < self.manual_override_until)

    def _begin_manual_override(self, reason: str, duration_sec: Optional[int] = None) -> None:
        lease_sec = max(15, int(duration_sec or self.manual_override_sec))
        self.manual_override_until = datetime.now() + timedelta(seconds=lease_sec)
        self.manual_override_reason = str(reason or "manual_control")
        if self.guardian:
            self.guardian._log(f"🧷 Control lease active ({lease_sec}s): {self.manual_override_reason}")

    async def _guardian_watchdog_loop(self):
        """Auto-recovery loop: Guardian ensures Orion keeps moving unless user paused it."""
        while self.running:
            try:
                await asyncio.sleep(max(3, self.guardian_watchdog_interval_sec))
                if not self.orion:
                    continue
                if self.orion.running:
                    self._maybe_auto_adjust_cost_guard()
                if self._is_full_auto_profile() and not self.guardian_auto_recovery and not self._manual_override_active():
                    self.guardian_auto_recovery = True
                    if self.guardian:
                        self.guardian._log("♻️ Full-auto enforced Guardian autopilot ON after lease expiry")
                if not self.guardian_auto_recovery or not self.guardian:
                    continue
                if not self.orion.running:
                    continue

                status = self.orion._get_status()
                paused = bool(status.get("paused"))
                pause_reason = str(status.get("pause_reason", "") or "").strip().lower()
                last_progress_age = self._age_seconds(status.get("last_progress_at")) or 0
                paused_age = self._age_seconds(status.get("paused_since")) or 0
                user_pause_timeout_reached = (
                    paused
                    and pause_reason in {"user", "manual_user"}
                    and self._is_full_auto_profile()
                    and paused_age >= self.full_auto_user_pause_max_sec
                )
                if self._manual_override_active():
                    resume_after_sec = max(30, int(self.manual_override_sec))
                    dashboard_interrupt_ready = (
                        paused
                        and pause_reason == "dashboard_interrupt"
                        and paused_age >= resume_after_sec
                    )
                    if not (dashboard_interrupt_ready or user_pause_timeout_reached):
                        continue
                last_start_age = self._age_seconds(status.get("last_cycle_started_at"))
                last_finish_age = self._age_seconds(status.get("last_cycle_finished_at"))

                is_stuck_running = (
                    not paused
                    and last_start_age is not None
                    and last_start_age >= self.guardian_stuck_threshold_sec
                    and (
                        last_finish_age is None
                        or (last_finish_age > last_start_age)
                    )
                )
                is_paused_unexpected = paused and pause_reason not in {"user", "manual_user"}
                is_paused_user_timeout = bool(user_pause_timeout_reached)
                is_stuck_no_progress = (not paused) and last_progress_age >= self.guardian_stuck_threshold_sec

                if not (is_stuck_running or is_paused_unexpected or is_stuck_no_progress or is_paused_user_timeout):
                    continue

                reason = "stuck_no_progress"
                if is_paused_user_timeout:
                    reason = f"paused_user_timeout:{paused_age}s"
                elif is_paused_unexpected:
                    reason = f"paused_unexpected:{pause_reason or 'unknown'}"
                elif is_stuck_running:
                    reason = "stuck_running_cycle"

                await self._guardian_intervene(
                    reason=reason,
                    last_progress_age_sec=last_progress_age,
                    paused_age_sec=paused_age,
                    orion_status=status,
                    force=False,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self.guardian:
                    self.guardian._log(f"⚠️ Guardian watchdog error: {exc}")

    async def _guardian_intervene(
        self,
        reason: str,
        last_progress_age_sec: int,
        paused_age_sec: int = 0,
        orion_status: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        if not self.orion or not self.guardian:
            return {"success": False, "error": "Guardian/Orion unavailable"}

        if self._manual_override_active() and not force:
            return {"success": True, "skipped": True, "reason": "manual_override_active"}

        now = datetime.now()
        if not force and self.last_guardian_intervention_at:
            cooldown = (now - self.last_guardian_intervention_at).total_seconds()
            if cooldown < self.guardian_recovery_cooldown_sec:
                return {"success": True, "skipped": True, "reason": "cooldown"}

        status = orion_status or self.orion._get_status()
        message = AgentMessage(
            from_agent="runtime",
            to_agent="guardian",
            type=MessageType.TASK,
            priority=Priority.HIGH,
            content={
                "action": "orion_stuck",
                "reason": reason,
                "last_progress_age_sec": int(last_progress_age_sec or 0),
                "paused_age_sec": int(paused_age_sec or 0),
                "orion_status": status,
                "project_goal": (self.orion.context.project_goal if self.orion and self.orion.context else ""),
                "principles": self.supervision_principles,
                "autonomy_profile": self.autonomy_profile,
                "full_auto": self._is_full_auto_profile(),
                "full_auto_user_pause_max_sec": self.full_auto_user_pause_max_sec,
            },
        )
        decision = await self.guardian.process(message)
        decision_payload = decision.output if isinstance(decision, TaskResult) else {}
        action = str((decision_payload or {}).get("action", "none")).strip().lower()
        decision_text = str((decision_payload or {}).get("decision", "none")).strip().lower()

        handled = False
        if action == "resume" and self.orion.paused:
            self.orion.paused = False
            self.orion.pause_reason = ""
            self.orion.paused_since = None
            await self.orion.user_commands.put("resume")
            self.orion._log("▶️ Guardian auto-recovery: resumed Orion to keep automation continuous")
            handled = True
        elif action == "nudge":
            await self.orion.user_commands.put("resume")
            self.orion._log("🔁 Guardian auto-recovery: nudged Orion loop for continued progress")
            handled = True

        self.last_guardian_intervention_at = now
        return {
            "success": True,
            "reason": reason,
            "decision": decision_text,
            "action": action,
            "handled": handled,
            "guardian": decision_payload,
        }

    def _setup_dashboard_callbacks(self):
        """Connect agent logs to dashboard"""
        from monitor.app import register_runtime_bridge, register_agents, update_agent_log

        agents = {"orion": self.orion}
        agents.update(self.orion.agents)
        register_agents(list(agents.keys()))

        def infer_level(message: str) -> str:
            text = str(message).lower()
            if any(k in text for k in ["❌", "error", "failed", "veto"]):
                return "error"
            if any(k in text for k in ["⚠️", "warn", "caution"]):
                return "warning"
            if any(k in text for k in ["✅", "success", "deployed", "approved"]):
                return "success"
            return "info"

        for agent_name, agent in agents.items():
            original_log = agent._log

            def make_log_wrapper(current_name, current_log):
                def wrapper(message):
                    current_log(message)
                    try:
                        update_agent_log(current_name, message, infer_level(message))
                    except Exception:
                        pass
                return wrapper

            agent._log = make_log_wrapper(agent_name, original_log)

        register_runtime_bridge(
            command_handler=self._handle_dashboard_command,
            status_handler=self._runtime_status_snapshot,
        )

    def _runtime_status_snapshot(self) -> Dict[str, Any]:
        if not self.orion:
            return {"ready": False}
        project_summary: Dict[str, Any] = {}
        if self.orion.context:
            ctx = self.orion.context.to_dict()
            feedback_history = ctx.get("feedback_history") or []
            issues = ctx.get("issues") or []
            screenshots = ctx.get("screenshots") or []
            files = ctx.get("files") or {}
            project_summary = {
                "project_name": ctx.get("project_name"),
                "project_goal": ctx.get("project_goal"),
                "current_iteration": ctx.get("current_iteration"),
                "files_count": len(files) if isinstance(files, dict) else 0,
                "feedback_count": len(feedback_history) if isinstance(feedback_history, list) else 0,
                "issues_count": len(issues) if isinstance(issues, list) else 0,
                "screenshots_count": len(screenshots) if isinstance(screenshots, list) else 0,
            }

        agent_controls = self.orion.get_agent_controls() if hasattr(self.orion, "get_agent_controls") else {}
        agent_runtime: List[Dict[str, Any]] = []
        for name, agent in sorted(self.orion.agents.items(), key=lambda item: item[0]):
            current_task = None
            try:
                task = getattr(agent, "current_task", None)
                if task is not None:
                    task_type = getattr(task, "type", None)
                    if hasattr(task_type, "value"):
                        current_task = str(task_type.value)
                    elif task_type is not None:
                        current_task = str(task_type)
            except Exception:
                current_task = None
            control = agent_controls.get(name, {}) if isinstance(agent_controls, dict) else {}

            agent_runtime.append({
                "name": name,
                "role": getattr(agent, "role", ""),
                "running": bool(getattr(agent, "running", False)),
                "current_task": current_task,
                "enabled": bool(control.get("enabled", True)),
                "control_reason": str(control.get("reason", "")),
                "control_updated_at": control.get("updated_at"),
                "control_updated_by": str(control.get("updated_by", "")),
            })

        watchdog_active = bool(
            self.guardian_auto_recovery
            and self.guardian_watchdog_task
            and not self.guardian_watchdog_task.done()
        )
        active_flows = [
            {
                "id": "orion_loop",
                "name": "Orion Loop",
                "active": bool(self.orion.running),
                "detail": "Điều phối chu kỳ cải tiến tự động",
            },
            {
                "id": "guardian_watchdog",
                "name": "Guardian Watchdog",
                "active": watchdog_active,
                "detail": "Giám sát stuck và tự phục hồi",
            },
            {
                "id": "manual_override",
                "name": "Manual Control Lease",
                "active": bool(self._manual_override_active()),
                "detail": self.manual_override_reason or "Không có điều khiển thủ công",
            },
        ]
        active_flow_count = sum(1 for flow in active_flows if flow.get("active"))

        return {
            "ready": True,
            "running": self.orion.running,
            "paused": self.orion.paused,
            "pause_reason": self.orion.pause_reason,
            "paused_since": self.orion.paused_since.isoformat() if self.orion.paused_since else None,
            "iteration": self.orion.iteration,
            "last_cycle_started_at": self.orion.last_cycle_started_at.isoformat() if self.orion.last_cycle_started_at else None,
            "last_cycle_finished_at": self.orion.last_cycle_finished_at.isoformat() if self.orion.last_cycle_finished_at else None,
            "last_progress_at": self.orion.last_progress_at.isoformat() if self.orion.last_progress_at else None,
            "cycle_timeout_sec": self.orion.cycle_timeout_sec,
            "stability": {
                "fix_streak": int(getattr(self.orion, "consecutive_fix_cycles", 0)),
                "no_deploy_streak": int(getattr(self.orion, "consecutive_no_deploy_cycles", 0)),
                "nova_no_output_streak": int(getattr(self.orion, "consecutive_nova_no_output", 0)),
                "fix_streak_trigger": int(getattr(self.orion, "max_fix_streak_before_recovery", 0)),
                "no_output_trigger": int(getattr(self.orion, "max_no_output_before_recovery", 0)),
            },
            "guardian_auto_recovery": self.guardian_auto_recovery,
            "guardian_last_intervention_at": self.last_guardian_intervention_at.isoformat() if self.last_guardian_intervention_at else None,
            "guardian_principles": self.supervision_principles,
            "autonomy_profile": self.autonomy_profile,
            "full_auto": self._is_full_auto_profile(),
            "full_auto_user_pause_max_sec": self.full_auto_user_pause_max_sec,
            "full_auto_maintenance_lease_sec": self.full_auto_maintenance_lease_sec,
            "manual_override_active": self._manual_override_active(),
            "manual_override_until": self.manual_override_until.isoformat() if self.manual_override_until else None,
            "manual_override_reason": self.manual_override_reason,
            "cost_guard": self._cost_guard_snapshot(),
            "provider_profile": self._provider_profile_snapshot(masked=True),
            "project": project_summary or None,
            "agents": sorted(list(self.orion.agents.keys())),
            "agent_controls": agent_controls,
            "agent_runtime": agent_runtime,
            "active_flows": active_flows,
            "active_flow_count": active_flow_count,
        }

    def _handle_dashboard_command(
        self,
        target: str,
        command: str,
        priority: str = "medium",
        options: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        if not self.main_loop:
            return {"success": False, "error": "Main runtime loop not available"}

        coro = self._execute_dashboard_command(
            target=target,
            command=command,
            priority=priority,
            options=options or {},
        )
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.main_loop)
        except Exception as exc:
            # Prevent "coroutine was never awaited" when loop is already closing.
            try:
                coro.close()
            except Exception:
                pass
            return {"success": False, "error": str(exc)}
        try:
            return future.result(timeout=180)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _execute_dashboard_command(
        self,
        target: str,
        command: str,
        priority: str = "medium",
        options: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        target = (target or "").strip().lower()
        command_text = (command or "").strip()
        cmd = command_text.lower()
        options = options or {}
        control_mode = str(options.get("control_mode", "strict")).strip().lower() or "strict"
        resume_after = bool(options.get("resume_after", False))
        paused_by_interrupt = False

        if not self.orion:
            return {"success": False, "error": "Orion not initialized"}

        def _interrupt_if_running() -> bool:
            nonlocal paused_by_interrupt
            if self.orion.running and not self.orion.paused:
                self.orion.paused = True
                self.orion.pause_reason = "dashboard_interrupt"
                self.orion.paused_since = datetime.now()
                paused_by_interrupt = True
                self.orion._log("⏸️ Dashboard interrupt: paused orchestrator for manual control")
                return True
            return False

        async def _resume_if_needed() -> None:
            if paused_by_interrupt and resume_after:
                self.orion.paused = False
                self.orion.pause_reason = ""
                self.orion.paused_since = None
                self.orion._log("▶️ Dashboard interrupt: resumed orchestrator")

        if target in {"orion", "system"}:
            if cmd in {"autonomy_profile", "get_autonomy_profile"}:
                return {
                    "success": True,
                    "target": target,
                    "autonomy_profile": self.autonomy_profile,
                    "full_auto": self._is_full_auto_profile(),
                    "full_auto_user_pause_max_sec": self.full_auto_user_pause_max_sec,
                }
            if cmd.startswith("set_autonomy_profile"):
                parts = command_text.split()
                profile = parts[1] if len(parts) > 1 else str(options.get("profile", "balanced"))
                applied = self._set_autonomy_profile(profile, source="dashboard")
                return {
                    "success": True,
                    "target": target,
                    "autonomy_profile": applied,
                    "full_auto": applied == "full_auto",
                }
            if cmd in {"pause", "resume", "shutdown"}:
                self._begin_manual_override(f"{target}:{cmd}")
                if cmd == "pause":
                    self.orion.paused = True
                    self.orion.pause_reason = "user"
                    self.orion.paused_since = datetime.now()
                    return {"success": True, "target": target, "command": cmd, "queued": False}
                elif cmd == "resume":
                    self.orion.paused = False
                    self.orion.pause_reason = ""
                    self.orion.paused_since = None
                    return {"success": True, "target": target, "command": cmd, "queued": False}
                await self.orion.user_commands.put(cmd)
                return {"success": True, "target": target, "command": cmd, "queued": True}
            if cmd in {"status", "state"}:
                return {"success": True, "target": target, "status": self.orion._get_status()}
            if cmd in {"cost_mode", "get_cost_mode", "cost_guard", "cost_guard_status"}:
                return {"success": True, "target": target, "cost_guard": self._cost_guard_snapshot()}
            if cmd in {"provider_status", "providers_status", "model_providers", "provider_profile"}:
                return {
                    "success": True,
                    "target": target,
                    "provider_profile": self._provider_profile_snapshot(masked=True),
                    "provider_catalog": self.provider_store.get_catalog(),
                }
            if cmd in {"apply_provider_profile", "update_provider_profile"}:
                profile_patch = options.get("profile") if isinstance(options, dict) else None
                if not isinstance(profile_patch, dict):
                    return {
                        "success": False,
                        "target": target,
                        "error": "Missing profile patch. Use options.profile as JSON object.",
                    }
                updated_profile = self.provider_store.update(profile_patch)
                applied = self._apply_provider_profile(
                    profile=updated_profile,
                    source="dashboard",
                    announce=True,
                )
                return {
                    "success": bool(applied.get("success")),
                    "target": target,
                    "provider_profile": self._provider_profile_snapshot(masked=True),
                    "provider_catalog": self.provider_store.get_catalog(),
                    "applied": applied,
                }
            if cmd.startswith("set_cost_mode"):
                mode_parts = command_text.split()
                mode = mode_parts[1] if len(mode_parts) > 1 else str(options.get("mode", "")).strip()
                if not mode:
                    return {
                        "success": False,
                        "target": target,
                        "error": "Missing mode. Use: set_cost_mode economy|balanced|aggressive",
                    }
                applied = self._apply_cost_guard_mode(mode, announce=True)
                return {
                    "success": bool(applied.get("success")),
                    "target": target,
                    "cost_guard": applied,
                }
            if cmd in {"agent_controls", "agents_control", "get_agent_controls"}:
                controls = self.orion.get_agent_controls() if hasattr(self.orion, "get_agent_controls") else {}
                return {"success": True, "target": target, "agent_controls": controls}
            if cmd.startswith("set_agent "):
                parts = command_text.split()
                if len(parts) < 3:
                    return {
                        "success": False,
                        "target": target,
                        "error": "Usage: set_agent <name> <on|off>",
                    }
                agent_name = str(parts[1]).strip().lower()
                mode = str(parts[2]).strip().lower()
                if mode not in {"on", "off", "enable", "disable", "resume", "pause"}:
                    return {
                        "success": False,
                        "target": target,
                        "error": "Mode must be one of: on, off, enable, disable",
                    }
                enabled = mode in {"on", "enable", "resume"}
                if not hasattr(self.orion, "set_agent_enabled"):
                    return {"success": False, "target": target, "error": "Agent control plane unavailable"}
                control = self.orion.set_agent_enabled(
                    agent_name=agent_name,
                    enabled=enabled,
                    reason=str(options.get("reason", f"orion_set_agent_{mode}")),
                    updated_by="dashboard",
                )
                return {
                    "success": bool(control.get("exists")),
                    "target": target,
                    "agent": agent_name,
                    "agent_control": control,
                }
            if cmd in {"run_cycle", "cycle"}:
                self._begin_manual_override(f"{target}:run_cycle")
                if self.orion.running and not self.orion.paused:
                    if control_mode == "interrupt":
                        _interrupt_if_running()
                    else:
                        return {
                            "success": False,
                            "target": target,
                            "error": "System is already running. Pause first before manual run_cycle.",
                        }
                result = await asyncio.wait_for(
                    self.orion.run_parallel_cycle(),
                    timeout=max(30, self.orion.cycle_timeout_sec),
                )
                await _resume_if_needed()
                return {
                    "success": True,
                    "target": target,
                    "result": result,
                    "control": {"mode": control_mode, "auto_paused": paused_by_interrupt, "resume_after": resume_after},
                }
            return {
                "success": True,
                "target": target,
                "note": (
                    "Supported commands: pause, resume, shutdown, status, run_cycle, "
                    "cost_mode, set_cost_mode <economy|balanced|aggressive>, "
                    "agent_controls, set_agent <name> <on|off>, "
                    "provider_status, apply_provider_profile"
                ),
            }

        if target in {"guardian", "supervisor"}:
            if cmd in {"status", "state"}:
                return {
                    "success": True,
                    "target": target,
                    "status": self.guardian._get_status() if self.guardian else {},
                    "runtime": self.orion._get_status() if self.orion else {},
                    "guardian_auto_recovery": self.guardian_auto_recovery,
                }
            if cmd in {"set_preferences", "preferences", "update_preferences"}:
                prefs = options.get("preferences") if isinstance(options, dict) else None
                prefs = prefs or {}
                if self.guardian and isinstance(prefs, dict):
                    self.guardian.update_preferences(prefs)
                    return {"success": True, "target": target, "preferences": prefs}
                return {"success": False, "target": target, "error": "Guardian not available or invalid preferences"}
            if cmd.startswith("advise_command:"):
                raw_command = command_text.split(":", 1)[1].strip()
                message = AgentMessage(
                    from_agent="dashboard",
                    to_agent="guardian",
                    type=MessageType.TASK,
                    priority=Priority.HIGH,
                    content={
                        "action": "command_intervention",
                        "command": raw_command,
                        "reason": str(options.get("reason", "permission_prompt")),
                        "options": options.get("choices", []),
                    },
                )
                advice = await self.guardian.process(message) if self.guardian else None
                payload = advice.to_dict() if hasattr(advice, "to_dict") else {"raw": advice}
                return {"success": True, "target": target, "result": payload}
            if cmd in {"autopilot_on", "auto_on", "watchdog_on"}:
                self.guardian_auto_recovery = True
                self.manual_override_until = None
                self.manual_override_reason = ""
                if self.guardian:
                    self.guardian._log("✅ Guardian autopilot enabled")
                return {"success": True, "target": target, "note": "Guardian autopilot enabled"}
            if cmd in {"autopilot_off", "auto_off", "watchdog_off"}:
                lease_sec = (
                    self.full_auto_maintenance_lease_sec
                    if self._is_full_auto_profile()
                    else self.manual_override_sec
                )
                self._begin_manual_override("guardian:autopilot_off", duration_sec=lease_sec)
                self.guardian_auto_recovery = False
                if self.guardian:
                    if self._is_full_auto_profile():
                        self.guardian._log(f"⏸️ Guardian autopilot paused for maintenance lease ({lease_sec}s)")
                    else:
                        self.guardian._log("⏸️ Guardian autopilot disabled")
                return {
                    "success": True,
                    "target": target,
                    "note": "Guardian autopilot paused temporarily" if self._is_full_auto_profile() else "Guardian autopilot disabled",
                    "lease_sec": lease_sec if self._is_full_auto_profile() else None,
                }
            if cmd in {"unstick", "unstick_orion", "resume_orion", "continue", "continue_loop", "recover"}:
                self._begin_manual_override("guardian:unstick")
                runtime_status = self.orion._get_status()
                recovery = await self._guardian_intervene(
                    reason="manual_guardian_recovery",
                    last_progress_age_sec=self._age_seconds(runtime_status.get("last_progress_at")) or 0,
                    paused_age_sec=self._age_seconds(runtime_status.get("paused_since")) or 0,
                    orion_status=runtime_status,
                    force=True,
                )
                return {"success": True, "target": target, "result": recovery}
            return {
                "success": True,
                "target": target,
                "note": "Supported guardian commands: status, unstick_orion, autopilot_on, autopilot_off",
            }

        if target == "all":
            if cmd in {"pause", "resume", "shutdown"}:
                self._begin_manual_override(f"all:{cmd}")
                if cmd == "pause":
                    self.orion.paused = True
                    self.orion.pause_reason = "user"
                    self.orion.paused_since = datetime.now()
                    return {"success": True, "target": "all", "command": cmd, "queued": False}
                elif cmd == "resume":
                    self.orion.paused = False
                    self.orion.pause_reason = ""
                    self.orion.paused_since = None
                    return {"success": True, "target": "all", "command": cmd, "queued": False}
                await self.orion.user_commands.put(cmd)
                return {"success": True, "target": "all", "command": cmd, "queued": True}
            if cmd in {"status", "state"}:
                return {"success": True, "target": "all", "status": self.orion._get_status()}
            if cmd not in {"run_cycle", "cycle", "run"}:
                return {
                    "success": False,
                    "target": "all",
                    "error": "Supported ALL commands: status, pause, resume, shutdown, run_cycle",
                }
            if self.orion.running and not self.orion.paused:
                if control_mode == "interrupt":
                    _interrupt_if_running()
                else:
                    return {
                        "success": False,
                        "target": "all",
                        "error": "System is already running. Use PAUSE before coordinated manual run.",
                    }
            self._begin_manual_override("all:run_cycle")
            result = await asyncio.wait_for(
                self.orion.run_parallel_cycle(),
                timeout=max(30, self.orion.cycle_timeout_sec),
            )
            await _resume_if_needed()
            return {
                "success": True,
                "target": "all",
                "result": result,
                "note": "Executed coordinated cycle across all agents",
                "control": {"mode": control_mode, "auto_paused": paused_by_interrupt, "resume_after": resume_after},
            }

        if target in self.orion.agents:
            if cmd in {"status", "state"}:
                runtime_snapshot = self._runtime_status_snapshot()
                runtime_row = next(
                    (
                        row
                        for row in (runtime_snapshot.get("agent_runtime") or [])
                        if str((row or {}).get("name", "")).lower() == target
                    ),
                    {},
                )
                control = (runtime_snapshot.get("agent_controls") or {}).get(target, {})
                return {
                    "success": True,
                    "target": target,
                    "status": runtime_row,
                    "agent_control": control,
                }
            if cmd in {"pause", "disable", "off", "autopilot_off"}:
                if hasattr(self.orion, "set_agent_enabled"):
                    control = self.orion.set_agent_enabled(
                        agent_name=target,
                        enabled=False,
                        reason=str(options.get("reason", "dashboard_pause")),
                        updated_by="dashboard",
                    )
                    self._begin_manual_override(f"{target}:pause")
                    return {
                        "success": bool(control.get("exists")),
                        "target": target,
                        "command": cmd,
                        "agent_control": control,
                    }
            if cmd in {"resume", "enable", "on", "autopilot_on"}:
                if hasattr(self.orion, "set_agent_enabled"):
                    control = self.orion.set_agent_enabled(
                        agent_name=target,
                        enabled=True,
                        reason=str(options.get("reason", "dashboard_resume")),
                        updated_by="dashboard",
                    )
                    self._begin_manual_override(f"{target}:resume")
                    return {
                        "success": bool(control.get("exists")),
                        "target": target,
                        "command": cmd,
                        "agent_control": control,
                    }

        if target not in self.orion.agents:
            return {"success": False, "target": target, "error": f"Unknown agent: {target}"}

        if self.orion.running and not self.orion.paused:
            if control_mode == "interrupt":
                _interrupt_if_running()
            else:
                return {
                    "success": False,
                    "target": target,
                    "error": "Pause Orion first before running a direct manual agent command.",
                }

        self._begin_manual_override(f"{target}:manual_command")
        agent_result = await self._run_agent_once(target=target, command=command_text, priority=priority)
        await _resume_if_needed()
        return {
            "success": True,
            "target": target,
            "result": agent_result,
            "control": {"mode": control_mode, "auto_paused": paused_by_interrupt, "resume_after": resume_after},
        }

    async def _run_agent_once(self, target: str, command: str, priority: str) -> Dict[str, Any]:
        agent = self.orion.agents[target]

        priority_map = {
            "low": Priority.LOW,
            "medium": Priority.MEDIUM,
            "high": Priority.HIGH,
            "critical": Priority.CRITICAL,
        }
        resolved_priority = priority_map.get((priority or "").lower(), Priority.MEDIUM)

        context = self.orion.context.to_dict() if self.orion.context else {}
        content: Dict[str, Any] = {
            "context": context,
            "iteration": self.orion.iteration,
            "instruction": command,
            "dashboard_command": command,
        }
        message_type = MessageType.TASK

        if target == "pixel":
            screenshot = context.get("screenshots", [])[-1] if context.get("screenshots") else None
            content["screenshot"] = screenshot
        elif target == "cipher":
            code_payload = {"files": {}}
            app_output = Path("app-output")
            if app_output.exists():
                for file in app_output.glob("*"):
                    if file.is_file():
                        try:
                            code_payload["files"][file.name] = file.read_text(encoding="utf-8")
                        except Exception:
                            continue
            content["code"] = code_payload
            message_type = MessageType.REVIEW
        elif target == "flux":
            files = []
            app_output = Path("app-output")
            if app_output.exists():
                files = [str(p) for p in app_output.glob("*") if p.is_file()]
            content["code"] = {"files": files}
            message_type = MessageType.DEPLOY
        elif target == "guardian":
            content = {"action": "get_status", "dashboard_command": command}

        message = AgentMessage(
            from_agent="dashboard",
            to_agent=target,
            type=message_type,
            priority=resolved_priority,
            content=content,
        )

        result = await agent.process(message)
        if isinstance(result, TaskResult):
            return result.to_dict()
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return {"raw": result}

    async def shutdown(self):
        """Shutdown the system"""

        print()
        print("=" * 70)
        print("  SHUTDOWN")
        print("=" * 70)
        self.running = False

        if self.guardian_watchdog_task and not self.guardian_watchdog_task.done():
            self.guardian_watchdog_task.cancel()
            try:
                await self.guardian_watchdog_task
            except asyncio.CancelledError:
                pass

        if self.orion:
            await self.orion.stop()

        print()
        print("✅ System shutdown complete.")
        print("📊 Dashboard will remain available (if process still running)")
        print("   Refresh the page to see final state.")
        print()


async def main():
    """Main entry point"""

    import argparse

    parser = argparse.ArgumentParser(description="Auto Dev Loop - Complete System")
    parser.add_argument("--goal", "-g", type=str, default=None, help="Project goal")
    parser.add_argument("--demo", action="store_true", help="Run demo (1 iteration)")
    parser.add_argument("--host", type=str, default="localhost", help="Dashboard host")
    parser.add_argument("--port", type=int, default=5000, help="Dashboard port")

    args = parser.parse_args()

    lock_path = os.getenv("AUTODEV_RUNTIME_LOCK_PATH", "data/state/autodev_runtime.lock")
    runtime_guard = ProcessSingleton(name="autodev_runtime", lock_path=lock_path)
    acquired, owner = runtime_guard.acquire(extra={"entrypoint": "run_system.py"})
    if not acquired:
        owner_pid = owner.get("pid", "unknown") if isinstance(owner, dict) else "unknown"
        owner_started = owner.get("started_at", "unknown") if isinstance(owner, dict) else "unknown"
        print("⚠️ Another AutoDev runtime is already running.")
        print(f"   Lock: {lock_path}")
        print(f"   Owner PID: {owner_pid}")
        print(f"   Started: {owner_started}")
        raise SystemExit(1)
    runtime_guard.start_heartbeat(interval_seconds=15)

    system = AutoDevSystem()

    await system.run(
        goal=args.goal,
        max_iterations=1 if args.demo else None,
        dashboard_host=args.host,
        dashboard_port=args.port,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
