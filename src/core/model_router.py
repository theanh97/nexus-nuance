"""
Adaptive Model Router
Rule-based model selection with multi-provider fallback and budget awareness.
"""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from src.core.routing_telemetry import read_recent_routing_events

load_dotenv()


class TaskComplexity(Enum):
    """Task complexity levels."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class TaskType(Enum):
    """Task categories."""

    # Code tasks
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    REFACTORING = "refactoring"

    # Vision tasks
    UI_ANALYSIS = "ui_analysis"
    SCREENSHOT_ANALYSIS = "screenshot_analysis"

    # Security tasks
    SECURITY_AUDIT = "security_audit"
    VULNERABILITY_SCAN = "vulnerability_scan"

    # QA tasks
    TEST_GENERATION = "test_generation"
    TEST_EXECUTION = "test_execution"

    # DevOps tasks
    DEPLOYMENT = "deployment"
    MONITORING = "monitoring"

    # Orchestration
    PLANNING = "planning"
    DECISION_MAKING = "decision_making"
    PERMISSION_REVIEW = "permission_review"


@dataclass
class ModelConfig:
    """Model metadata used by routing policy."""

    name: str
    api_source: str
    api_key_env: str
    api_base: str
    cost_per_1k_tokens: float
    best_for: List[TaskType]
    max_complexity: TaskComplexity
    quality_tier: int = 2  # 1=low, 2=mid, 3=high
    speed_tier: int = 2  # 1=slow, 2=normal, 3=fast
    supports_vision: bool = False
    supports_api: bool = True
    is_subscription: bool = False


# ============================================
# ROUTING CONFIGURATION
# ============================================

ROUTING_MODE = os.getenv("MODEL_ROUTING_MODE", "adaptive").strip().lower()
SINGLE_MODEL_MODE = os.getenv("SINGLE_MODEL_MODE", "false").strip().lower() == "true"
DEFAULT_SINGLE_MODEL = os.getenv("DEFAULT_SINGLE_MODEL", "glm-5")
DEFAULT_GLM_API_BASE = os.getenv("GLM_API_BASE") or os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")

DAILY_BUDGET_USD = float(os.getenv("ROUTER_DAILY_BUDGET_USD", "15"))
SOFT_BUDGET_RATIO = float(os.getenv("ROUTER_SOFT_BUDGET_RATIO", "0.85"))
ENABLE_MODEL_PERFORMANCE_LEARNING = os.getenv("ENABLE_MODEL_PERFORMANCE_LEARNING", "true").strip().lower() in {"1", "true", "yes", "on"}
MODEL_PERFORMANCE_WINDOW = max(50, int(os.getenv("MODEL_PERFORMANCE_WINDOW", "240")))
MODEL_PERFORMANCE_MIN_CALLS = max(1, int(os.getenv("MODEL_PERFORMANCE_MIN_CALLS", "4")))
STATE_DIR = Path(os.getenv("ROUTER_STATE_DIR", "data/state"))

_COMPLEXITY_RANK = {
    TaskComplexity.SIMPLE: 1,
    TaskComplexity.MEDIUM: 2,
    TaskComplexity.COMPLEX: 3,
}

_VISION_TASKS = {TaskType.UI_ANALYSIS, TaskType.SCREENSHOT_ANALYSIS}
_SECURITY_TASKS = {TaskType.SECURITY_AUDIT, TaskType.VULNERABILITY_SCAN}
_CRITICAL_TASKS = {
    TaskType.PLANNING,
    TaskType.DECISION_MAKING,
    TaskType.PERMISSION_REVIEW,
    TaskType.SECURITY_AUDIT,
    TaskType.DEPLOYMENT,
}


# ============================================
# AVAILABLE MODELS
# ============================================

MODELS: Dict[str, ModelConfig] = {
    # GLM family
    "glm-5": ModelConfig(
        name="glm-5",
        api_source="glm",
        api_key_env="GLM_API_KEY",
        api_base=DEFAULT_GLM_API_BASE,
        cost_per_1k_tokens=0.02,
        best_for=[
            TaskType.PLANNING,
            TaskType.DECISION_MAKING,
            TaskType.PERMISSION_REVIEW,
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
            TaskType.SECURITY_AUDIT,
            TaskType.VULNERABILITY_SCAN,
            TaskType.DEPLOYMENT,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
    ),
    "glm-4.7": ModelConfig(
        name="glm-4.7",
        api_source="glm",
        api_key_env="GLM_API_KEY",
        api_base=DEFAULT_GLM_API_BASE,
        cost_per_1k_tokens=0.012,
        best_for=[
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
            TaskType.TEST_GENERATION,
            TaskType.MONITORING,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
    ),
    "glm-4-flash": ModelConfig(
        name=os.getenv("GLM_FLASH_MODEL", "glm-4-flash").strip() or "glm-4-flash",
        api_source="glm",
        api_key_env="GLM_API_KEY",
        api_base=DEFAULT_GLM_API_BASE,
        cost_per_1k_tokens=0.005,
        best_for=[
            TaskType.TEST_GENERATION,
            TaskType.TEST_EXECUTION,
            TaskType.MONITORING,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
        ],
        max_complexity=TaskComplexity.MEDIUM,
        quality_tier=2,
        speed_tier=3,
    ),
    "glm-4.6v": ModelConfig(
        name="glm-4.6v",
        api_source="glm",
        api_key_env="GLM_API_KEY",
        api_base=DEFAULT_GLM_API_BASE,
        cost_per_1k_tokens=0.03,
        best_for=[TaskType.UI_ANALYSIS, TaskType.SCREENSHOT_ANALYSIS],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
        supports_vision=True,
    ),
    # Gemini family
    "gemini-2.0-flash": ModelConfig(
        name="gemini-2.0-flash",
        api_source="google",
        api_key_env="GOOGLE_API_KEY",
        api_base="https://generativelanguage.googleapis.com/v1beta",
        cost_per_1k_tokens=0.01,
        best_for=[
            TaskType.TEST_GENERATION,
            TaskType.TEST_EXECUTION,
            TaskType.MONITORING,
            TaskType.UI_ANALYSIS,
            TaskType.SCREENSHOT_ANALYSIS,
        ],
        max_complexity=TaskComplexity.MEDIUM,
        quality_tier=2,
        speed_tier=3,
        supports_vision=True,
    ),
    # Gemini subscription CLI profiles
    "gemini-3.0-flash-subscription": ModelConfig(
        name=os.getenv("GEMINI_SUBSCRIPTION_FLASH_MODEL", "gemini-3.0-flash"),
        api_source="subscription_gemini",
        api_key_env="",
        api_base="",
        cost_per_1k_tokens=0.0,
        best_for=[
            TaskType.UI_ANALYSIS,
            TaskType.SCREENSHOT_ANALYSIS,
            TaskType.TEST_GENERATION,
            TaskType.TEST_EXECUTION,
            TaskType.MONITORING,
        ],
        max_complexity=TaskComplexity.MEDIUM,
        quality_tier=2,
        speed_tier=3,
        supports_vision=True,
        supports_api=False,
        is_subscription=True,
    ),
    "gemini-3.0-pro-subscription": ModelConfig(
        name=os.getenv("GEMINI_SUBSCRIPTION_PRO_MODEL", "gemini-3.0-pro"),
        api_source="subscription_gemini",
        api_key_env="",
        api_base="",
        cost_per_1k_tokens=0.0,
        best_for=[
            TaskType.UI_ANALYSIS,
            TaskType.SCREENSHOT_ANALYSIS,
            TaskType.CODE_REVIEW,
            TaskType.PLANNING,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
        supports_vision=True,
        supports_api=False,
        is_subscription=True,
    ),
    # MiniMax (Anthropic-compatible gateway)
    "minimax-m2.5": ModelConfig(
        name=os.getenv("MINIMAX_MODEL", "MiniMax-M2.5"),
        api_source="anthropic",
        api_key_env="MINIMAX_API_KEY",
        api_base=os.getenv("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic"),
        cost_per_1k_tokens=0.008,
        best_for=[
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
            TaskType.PLANNING,
            TaskType.DECISION_MAKING,
            TaskType.TEST_GENERATION,
            TaskType.MONITORING,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
    ),
    "codex-api": ModelConfig(
        name=os.getenv("CODAXER_MODEL", "gpt-5.1-codex-max"),
        api_source="codaxer",
        api_key_env="CODAXER_API_KEY",
        api_base=os.getenv("CODAXER_BASE_URL", "https://api.codaxer.com/v1"),
        cost_per_1k_tokens=0.015,
        best_for=[
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
            TaskType.PLANNING,
            TaskType.DECISION_MAKING,
            TaskType.TEST_GENERATION,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
    ),
    "minimax-text": ModelConfig(
        name=os.getenv("MINIMAX_TEXT_MODEL", "MiniMax-Text-01"),
        api_source="anthropic",
        api_key_env="MINIMAX_API_KEY",
        api_base=os.getenv("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic"),
        cost_per_1k_tokens=0.004,
        best_for=[
            TaskType.TEST_GENERATION,
            TaskType.TEST_EXECUTION,
            TaskType.MONITORING,
            TaskType.CODE_REVIEW,
        ],
        max_complexity=TaskComplexity.MEDIUM,
        quality_tier=2,
        speed_tier=3,
    ),
    # Subscription profiles (routing-aware, not direct API callable in this runtime)
    "codex-5.3-x2-subscription": ModelConfig(
        name=os.getenv("CODEX_SUBSCRIPTION_MODEL", "codex-5.3-x2"),
        api_source="subscription_codex",
        api_key_env="",
        api_base="",
        cost_per_1k_tokens=0.0,
        best_for=[
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
            TaskType.PLANNING,
            TaskType.DECISION_MAKING,
            TaskType.SECURITY_AUDIT,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
        supports_api=False,
        is_subscription=True,
    ),
    "codex-5.3-mini-subscription": ModelConfig(
        name=os.getenv("CODEX_SUBSCRIPTION_LOW_MODEL", "codex-5.3-mini"),
        api_source="subscription_codex",
        api_key_env="",
        api_base="",
        cost_per_1k_tokens=0.0,
        best_for=[
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.REFACTORING,
            TaskType.TEST_GENERATION,
            TaskType.TEST_EXECUTION,
            TaskType.MONITORING,
        ],
        max_complexity=TaskComplexity.MEDIUM,
        quality_tier=2,
        speed_tier=3,
        supports_api=False,
        is_subscription=True,
    ),
    "claude-code-subscription": ModelConfig(
        name=os.getenv("CLAUDE_CODE_MODEL", "claude-code"),
        api_source="subscription_claude",
        api_key_env="",
        api_base="",
        cost_per_1k_tokens=0.0,
        best_for=[
            TaskType.CODE_GENERATION,
            TaskType.CODE_REVIEW,
            TaskType.PLANNING,
            TaskType.DECISION_MAKING,
            TaskType.SECURITY_AUDIT,
            TaskType.UI_ANALYSIS,
        ],
        max_complexity=TaskComplexity.COMPLEX,
        quality_tier=3,
        speed_tier=2,
        supports_vision=True,
        supports_api=False,
        is_subscription=True,
    ),
}


class ModelRouter:
    """Route tasks to model chains, balancing quality, speed, and token budget."""

    def __init__(self):
        def _env_bool(name: str, default: bool = False) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        self.api_keys = {
            "glm": os.getenv("GLM_API_KEY"),
            "google": os.getenv("GOOGLE_API_KEY"),
            "anthropic": (
                os.getenv("MINIMAX_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("CLAUDE_CODE_API_KEY")
            ),
        }
        self.api_keys["codaxer"] = os.getenv("CODAXER_API_KEY") or os.getenv("CLAUDE_CODE_API_KEY")
        codex_cli = os.getenv("CODEX_CLI_CMD", "codex")
        claude_cli = os.getenv("CLAUDE_CLI_CMD", "claude")
        gemini_cli = os.getenv("GEMINI_CLI_CMD", "gemini")
        self.subscription_flags = {
            "subscription_codex": _env_bool("CODEX_SUBSCRIPTION_AVAILABLE", False) or bool(shutil.which(codex_cli)),
            "subscription_claude": _env_bool("CLAUDE_CODE_SUBSCRIPTION_AVAILABLE", False) or bool(shutil.which(claude_cli)),
            "subscription_gemini": _env_bool("GEMINI_SUBSCRIPTION_AVAILABLE", False) or bool(shutil.which(gemini_cli)),
        }
        self.usage_state = self._load_usage_state()

    def _usage_state_path(self) -> Path:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        return STATE_DIR / f"model_usage_{datetime.now().strftime('%Y%m%d')}.json"

    def _load_usage_state(self) -> Dict[str, Any]:
        path = self._usage_state_path()
        if not path.exists():
            return {"total_cost_usd": 0.0, "models": {}}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"total_cost_usd": 0.0, "models": {}}

    def _save_usage_state(self) -> None:
        path = self._usage_state_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.usage_state, f, indent=2)

    def record_usage(self, model_name: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """Record usage for budget-aware routing decisions."""
        cfg = MODELS.get(model_name)
        if not cfg:
            cfg = next((m for m in MODELS.values() if m.name == model_name), None)
        if not cfg:
            return

        total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)
        cost = (total_tokens / 1000.0) * cfg.cost_per_1k_tokens

        models = self.usage_state.setdefault("models", {})
        model_data = models.setdefault(model_name, {"tokens": 0, "cost_usd": 0.0, "calls": 0})
        model_data["tokens"] += total_tokens
        model_data["cost_usd"] += cost
        model_data["calls"] += 1

        self.usage_state["total_cost_usd"] = self.usage_state.get("total_cost_usd", 0.0) + cost
        self._save_usage_state()

    def get_usage_summary(self) -> Dict[str, Any]:
        """Return current daily usage summary."""
        total_cost = float(self.usage_state.get("total_cost_usd", 0.0))
        budget = DAILY_BUDGET_USD
        return {
            "total_cost_usd": round(total_cost, 6),
            "daily_budget_usd": budget,
            "budget_ratio": (total_cost / budget) if budget > 0 else 0.0,
            "models": self.usage_state.get("models", {}),
        }

    def get_budget_projection(self) -> Dict[str, Any]:
        """Project budget usage for the rest of the day based on current spend rate."""
        from datetime import datetime, timedelta

        now = datetime.now()
        # Hours elapsed since midnight
        hours_elapsed = now.hour + now.minute / 60.0
        hours_remaining = max(0.01, 24.0 - hours_elapsed)

        total_cost = float(self.usage_state.get('total_cost_usd', 0.0))
        budget = DAILY_BUDGET_USD

        # Calculate hourly spend rate
        hourly_rate = total_cost / max(0.01, hours_elapsed) if hours_elapsed > 0.1 else 0.0
        projected_eod = total_cost + (hourly_rate * hours_remaining)

        # Budget status
        if projected_eod <= budget * 0.7:
            status = 'healthy'
        elif projected_eod <= budget:
            status = 'caution'
        else:
            status = 'over_budget'

        # Per-model breakdown
        model_usage = self.usage_state.get('models', {})
        model_rates = {}
        for model_name, info in model_usage.items():
            model_cost = float(info.get('cost_usd', 0.0)) if isinstance(info, dict) else 0.0
            model_rates[model_name] = {
                'spent': round(model_cost, 6),
                'hourly_rate': round(model_cost / max(0.01, hours_elapsed), 6) if hours_elapsed > 0.1 else 0.0,
                'projected_eod': round(model_cost + (model_cost / max(0.01, hours_elapsed) * hours_remaining), 6) if hours_elapsed > 0.1 else 0.0,
            }

        return {
            'total_spent': round(total_cost, 6),
            'hourly_rate': round(hourly_rate, 6),
            'projected_eod': round(projected_eod, 6),
            'daily_budget': budget,
            'remaining_budget': round(max(0, budget - total_cost), 6),
            'status': status,
            'hours_elapsed': round(hours_elapsed, 2),
            'hours_remaining': round(hours_remaining, 2),
            'model_breakdown': model_rates,
        }

    def _budget_soft_limited(self, estimated_tokens: int) -> bool:
        budget = DAILY_BUDGET_USD
        if budget <= 0:
            return False

        current = float(self.usage_state.get('total_cost_usd', 0.0))
        estimate = (max(0, estimated_tokens) / 1000.0) * 0.02

        # Check immediate limit
        if (current + estimate) >= (budget * SOFT_BUDGET_RATIO):
            return True

        # Check projected overspend - throttle if on pace to exceed budget
        try:
            projection = self.get_budget_projection()
            if projection['status'] == 'over_budget' and (current + estimate) >= (budget * 0.5):
                return True
        except Exception:
            pass

        return False

    def _supports_complexity(self, model: ModelConfig, complexity: Optional[TaskComplexity]) -> bool:
        if complexity is None:
            return True
        return _COMPLEXITY_RANK[model.max_complexity] >= _COMPLEXITY_RANK[complexity]

    def is_model_available(self, model: ModelConfig) -> bool:
        """Check model availability from keys/subscription flags."""
        if model.api_source.startswith("subscription_"):
            return self.subscription_flags.get(model.api_source, False)
        return bool(self.api_keys.get(model.api_source))

    def get_api_key(self, model: ModelConfig) -> Optional[str]:
        """Get API key for selected model."""
        if model.api_source.startswith("subscription_"):
            return None
        return self.api_keys.get(model.api_source)

    def is_runtime_callable(self, model: ModelConfig) -> bool:
        """Return True if this runtime can call the model via API."""
        return model.supports_api and self.is_model_available(model)

    def _base_chain(
        self,
        task_type: TaskType,
        complexity: Optional[TaskComplexity],
        importance: str,
    ) -> List[str]:
        if (
            importance != "critical"
            and task_type in {
                TaskType.CODE_GENERATION,
                TaskType.CODE_REVIEW,
                TaskType.REFACTORING,
                TaskType.TEST_GENERATION,
                TaskType.TEST_EXECUTION,
                TaskType.MONITORING,
            }
            and complexity in {TaskComplexity.SIMPLE, TaskComplexity.MEDIUM, None}
        ):
            # Prefer lower-cost chains for low/medium code-like tasks.
            # Keep medium tasks on stronger mid-tier model before flash to avoid malformed outputs.
            if complexity == TaskComplexity.SIMPLE:
                return [
                    "codex-5.3-mini-subscription",
                    "minimax-text",
                    "glm-4-flash",
                    "glm-4.7",
                    "minimax-m2.5",
                    "gemini-2.0-flash",
                    "glm-5",
                ]
            return [
                "codex-5.3-mini-subscription",
                "minimax-text",
                "glm-4.7",
                "minimax-m2.5",
                "glm-4-flash",
                "gemini-2.0-flash",
                "glm-5",
            ]

        if task_type in _VISION_TASKS:
            return [
                "glm-4.6v",
                "gemini-2.0-flash",
                "gemini-3.0-flash-subscription",
                "gemini-3.0-pro-subscription",
                "claude-code-subscription",
                "glm-5",
            ]

        if importance == "critical" or task_type in _CRITICAL_TASKS or complexity == TaskComplexity.COMPLEX:
            return [
                "codex-5.3-x2-subscription",
                "codex-api",
                "claude-code-subscription",
                "glm-5",
                "minimax-m2.5",
                "glm-4.7",
                "gemini-2.0-flash",
                "glm-4-flash",
            ]

        if importance == "low" or complexity == TaskComplexity.SIMPLE:
            return [
                "glm-4-flash",
                "codex-5.3-mini-subscription",
                "gemini-2.0-flash",
                "minimax-m2.5",
                "glm-4.7",
                "glm-5",
            ]

        if task_type in _SECURITY_TASKS:
            return [
                "codex-5.3-x2-subscription",
                "claude-code-subscription",
                "glm-5",
                "glm-4.7",
                "minimax-m2.5",
            ]

        return [
            "glm-4.7",
            "codex-5.3-mini-subscription",
            "minimax-text",
            "minimax-m2.5",
            "codex-api",
            "glm-5",
            "gemini-2.0-flash",
            "glm-4-flash",
        ]

    def _sort_by_preferences(
        self,
        candidates: List[ModelConfig],
        prefer_speed: bool,
        prefer_cost: bool,
    ) -> List[ModelConfig]:
        if prefer_cost:
            return sorted(candidates, key=lambda m: m.cost_per_1k_tokens)
        if prefer_speed:
            return sorted(candidates, key=lambda m: (m.speed_tier, -m.quality_tier), reverse=True)
        return sorted(candidates, key=lambda m: (m.quality_tier, -m.cost_per_1k_tokens), reverse=True)

    def _label(self, cfg: ModelConfig) -> str:
        """Human-readable model label for diagnostics."""
        if cfg.api_source.startswith("subscription_"):
            return f"{cfg.api_source}:{cfg.name}"
        return cfg.name

    def _model_performance_scores(self, task_type: TaskType) -> Dict[str, float]:
        """
        Estimate per-model utility from recent routing telemetry.
        Higher is better.
        """
        scores: Dict[str, float] = {}
        if not ENABLE_MODEL_PERFORMANCE_LEARNING:
            return scores
        events = read_recent_routing_events(limit=MODEL_PERFORMANCE_WINDOW)
        if not events:
            return scores

        grouped: Dict[str, Dict[str, float]] = {}
        for item in events:
            if str(item.get("task_type", "")) != task_type.value:
                continue
            selected = str(item.get("selected_model", "")).strip()
            if not selected:
                continue
            row = grouped.setdefault(selected, {"calls": 0.0, "success": 0.0, "latency_ms": 0.0, "cost_usd": 0.0})
            row["calls"] += 1.0
            if bool(item.get("success", False)):
                row["success"] += 1.0
            row["latency_ms"] += max(0.0, float(item.get("duration_ms", 0.0) or 0.0))
            row["cost_usd"] += max(0.0, float(item.get("estimated_cost_usd", 0.0) or 0.0))

        if not grouped:
            return scores

        # Normalize against observed maxima so score is stable by window.
        max_latency = max((v["latency_ms"] / max(1.0, v["calls"]) for v in grouped.values()), default=1.0)
        max_cost = max((v["cost_usd"] / max(1.0, v["calls"]) for v in grouped.values()), default=1.0)
        max_latency = max(1.0, max_latency)
        max_cost = max(1e-6, max_cost)

        for model_name, row in grouped.items():
            calls = row["calls"]
            if calls < float(MODEL_PERFORMANCE_MIN_CALLS):
                continue
            success_ratio = row["success"] / calls
            avg_latency = row["latency_ms"] / calls
            avg_cost = row["cost_usd"] / calls
            latency_score = 1.0 - min(1.0, avg_latency / max_latency)
            cost_score = 1.0 - min(1.0, avg_cost / max_cost)
            # Weighted toward reliability, then cost, then latency.
            utility = (0.62 * success_ratio) + (0.25 * cost_score) + (0.13 * latency_score)
            scores[model_name] = utility

        return scores

    def get_fallback_chain(
        self,
        task_type: TaskType,
        complexity: Optional[TaskComplexity] = None,
        importance: str = "normal",
        prefer_speed: bool = False,
        prefer_cost: bool = False,
        estimated_tokens: int = 1200,
        runtime_only: bool = True,
    ) -> List[ModelConfig]:
        """Get ordered fallback chain for a task."""
        importance = (importance or "normal").lower()
        chain_names = self._base_chain(task_type, complexity, importance)

        candidates: List[ModelConfig] = []
        seen = set()
        seen_identity = set()

        for model_name in chain_names:
            cfg = MODELS.get(model_name)
            if not cfg or model_name in seen:
                continue
            seen.add(model_name)
            identity = (cfg.api_source, cfg.name, cfg.api_base)
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            if task_type not in cfg.best_for:
                continue
            if not self._supports_complexity(cfg, complexity):
                continue
            if runtime_only and not self.is_runtime_callable(cfg):
                continue
            if not runtime_only and not self.is_model_available(cfg):
                continue
            candidates.append(cfg)

        if self._budget_soft_limited(estimated_tokens) and importance in {"low", "normal"}:
            candidates = sorted(candidates, key=lambda m: m.cost_per_1k_tokens)
        elif prefer_cost or prefer_speed:
            candidates = self._sort_by_preferences(candidates, prefer_speed, prefer_cost)
        # Default behavior: preserve chain order from policy for deterministic routing.
        elif ENABLE_MODEL_PERFORMANCE_LEARNING:
            perf = self._model_performance_scores(task_type)
            if perf:
                def _perf_key(cfg: ModelConfig) -> float:
                    base = perf.get(cfg.name, 0.0)
                    # tiny deterministic prior by configured quality tier
                    return base + (float(cfg.quality_tier) * 0.001)
                candidates = sorted(candidates, key=_perf_key, reverse=True)

        if candidates:
            return candidates

        # Last resort: any available runtime model that can do this task
        fallback = []
        fallback_seen = set()
        for cfg in MODELS.values():
            identity = (cfg.api_source, cfg.name, cfg.api_base)
            if identity in fallback_seen:
                continue
            fallback_seen.add(identity)
            if task_type not in cfg.best_for:
                continue
            if not self._supports_complexity(cfg, complexity):
                continue
            if runtime_only and not self.is_runtime_callable(cfg):
                continue
            if not runtime_only and not self.is_model_available(cfg):
                continue
            fallback.append(cfg)

        return self._sort_by_preferences(fallback, prefer_speed, True)

    def get_model_for_task(
        self,
        task_type: TaskType,
        complexity: Optional[TaskComplexity] = None,
        prefer_speed: bool = False,
        prefer_cost: bool = False,
        importance: str = "normal",
        estimated_tokens: int = 1200,
    ) -> ModelConfig:
        """Return the best model for the task."""
        if SINGLE_MODEL_MODE or ROUTING_MODE == "single":
            return MODELS.get(DEFAULT_SINGLE_MODEL, MODELS["glm-5"])

        chain = self.get_fallback_chain(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
            prefer_speed=prefer_speed,
            prefer_cost=prefer_cost,
            estimated_tokens=estimated_tokens,
            runtime_only=True,
        )
        if chain:
            return chain[0]
        return MODELS["glm-5"]

    def get_routing_decision(
        self,
        task_type: TaskType,
        complexity: Optional[TaskComplexity] = None,
        importance: str = "normal",
        prefer_speed: bool = False,
        prefer_cost: bool = False,
        estimated_tokens: int = 1200,
    ) -> Dict[str, Any]:
        """Explain which chain is selected for a task."""
        chain = self.get_fallback_chain(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
            prefer_speed=prefer_speed,
            prefer_cost=prefer_cost,
            estimated_tokens=estimated_tokens,
            runtime_only=False,
        )
        return {
            "task_type": task_type.value,
            "complexity": complexity.value if complexity else None,
            "importance": importance,
            "prefer_speed": prefer_speed,
            "prefer_cost": prefer_cost,
            "chain": [self._label(m) for m in chain],
            "runtime_chain": [
                self._label(m)
                for m in self.get_fallback_chain(
                    task_type=task_type,
                    complexity=complexity,
                    importance=importance,
                    prefer_speed=prefer_speed,
                    prefer_cost=prefer_cost,
                    estimated_tokens=estimated_tokens,
                    runtime_only=True,
                )
            ],
            "budget": self.get_usage_summary(),
        }


# ============================================
# TASK DELEGATION RULES
# ============================================

DELEGATION_RULES = {
    "orion": {
        TaskType.CODE_GENERATION: {
            "complexity >= MEDIUM": "nova",
            "complexity == SIMPLE": "nova-lite",
        },
        TaskType.UI_ANALYSIS: {"always": "pixel"},
        TaskType.SECURITY_AUDIT: {"always": "cipher"},
        TaskType.TEST_GENERATION: {"always": "echo"},
        TaskType.DEPLOYMENT: {"always": "flux"},
    },
    "guardian": {
        TaskType.PERMISSION_REVIEW: {"always": "self"},
    },
}


def estimate_task_cost(task_type: TaskType, estimated_tokens: int = 1000) -> Dict[str, Dict[str, Any]]:
    """Estimate cost for a task across all matching models."""
    router = ModelRouter()
    costs: Dict[str, Dict[str, Any]] = {}

    for model_name, cfg in MODELS.items():
        if task_type not in cfg.best_for:
            continue
        costs[model_name] = {
            "cost_usd": (estimated_tokens / 1000.0) * cfg.cost_per_1k_tokens,
            "available": router.is_model_available(cfg),
            "runtime_callable": router.is_runtime_callable(cfg),
        }

    return costs


def get_cheapest_model_for_task(task_type: TaskType) -> Optional[ModelConfig]:
    """Get the cheapest runtime-callable model for a task."""
    router = ModelRouter()
    candidates = [
        cfg
        for cfg in MODELS.values()
        if task_type in cfg.best_for and router.is_runtime_callable(cfg)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda m: m.cost_per_1k_tokens)[0]


if __name__ == "__main__":
    router = ModelRouter()
    examples = [
        ("Code Generation (Critical)", TaskType.CODE_GENERATION, TaskComplexity.COMPLEX, "critical"),
        ("Code Generation (Normal)", TaskType.CODE_GENERATION, TaskComplexity.MEDIUM, "normal"),
        ("Monitoring (Low)", TaskType.MONITORING, TaskComplexity.SIMPLE, "low"),
        ("UI Analysis", TaskType.UI_ANALYSIS, TaskComplexity.MEDIUM, "normal"),
    ]

    print("=" * 70)
    print("ADAPTIVE ROUTING PREVIEW")
    print("=" * 70)
    for title, task_type, complexity, importance in examples:
        decision = router.get_routing_decision(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
        )
        print(f"\n{title}")
        print(f"  chain: {decision['chain']}")
        print(f"  runtime_chain: {decision['runtime_chain']}")
    print("\nBudget summary:", router.get_usage_summary())
