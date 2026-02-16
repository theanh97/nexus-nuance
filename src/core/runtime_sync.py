"""
Runtime synchronization utilities for provider/env/model routing.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from src.core.model_router import ModelRouter, TaskType, TaskComplexity


def _read_env_file(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _append_missing_env_vars(env_path: Path, values: Dict[str, str]) -> List[str]:
    existing = _read_env_file(env_path)
    appended: List[str] = []
    lines: List[str] = []

    for key, value in values.items():
        if key in existing:
            continue
        lines.append(f"{key}={value}")
        appended.append(key)

    if lines:
        prefix = "\n\n# Runtime sync generated values\n"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        if env_path.exists():
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(prefix + "\n".join(lines) + "\n")
        else:
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return appended


def sync_provider_env(write_env: bool = False, env_path: str = ".env") -> Dict[str, Any]:
    """
    Synchronize provider settings (process env, optional .env append for missing keys).
    """
    load_dotenv()

    resolved: Dict[str, str] = {}
    actions: List[str] = []

    mapping: List[Tuple[str, str]] = [
        ("ZAI_API_KEY", "GLM_API_KEY"),
        ("ZAI_OPENAI_BASE_URL", "GLM_API_BASE"),
        ("MINIMAX_API_KEY", "CLAUDE_CODE_API_KEY"),
        ("MINIMAX_ANTHROPIC_BASE_URL", "CLAUDE_CODE_BASE_URL"),
        ("MINIMAX_MODEL", "CLAUDE_CODE_MODEL"),
    ]

    for src_key, dst_key in mapping:
        src_val = os.getenv(src_key, "").strip()
        dst_val = os.getenv(dst_key, "").strip()
        if src_val and not dst_val:
            os.environ[dst_key] = src_val
            resolved[dst_key] = src_val
            actions.append(f"{dst_key} <= {src_key}")

    if not os.getenv("CLAUDE_CODE_PROVIDER"):
        os.environ["CLAUDE_CODE_PROVIDER"] = "minimax"
        resolved["CLAUDE_CODE_PROVIDER"] = "minimax"
        actions.append("CLAUDE_CODE_PROVIDER <= minimax")

    # Ensure GLM/OpenAI-compatible provider defaults to Z.ai if not explicitly set.
    if not os.getenv("GLM_API_BASE"):
        default_base = os.getenv("ZAI_OPENAI_BASE_URL", "https://api.z.ai/api/openai/v1")
        os.environ["GLM_API_BASE"] = default_base
        resolved["GLM_API_BASE"] = default_base
        actions.append("GLM_API_BASE <= ZAI default")

    codex_cmd = os.getenv("CODEX_CLI_CMD", "codex")
    claude_cmd = os.getenv("CLAUDE_CLI_CMD", "claude")
    gemini_cmd = os.getenv("GEMINI_CLI_CMD", "gemini")
    codex_available = bool(shutil.which(codex_cmd))
    claude_available = bool(shutil.which(claude_cmd))
    gemini_available = bool(shutil.which(gemini_cmd))

    if codex_available and not os.getenv("CODEX_SUBSCRIPTION_AVAILABLE"):
        os.environ["CODEX_SUBSCRIPTION_AVAILABLE"] = "true"
        resolved["CODEX_SUBSCRIPTION_AVAILABLE"] = "true"
        actions.append("CODEX_SUBSCRIPTION_AVAILABLE <= auto-detect")

    if claude_available and not os.getenv("CLAUDE_CODE_SUBSCRIPTION_AVAILABLE"):
        os.environ["CLAUDE_CODE_SUBSCRIPTION_AVAILABLE"] = "true"
        resolved["CLAUDE_CODE_SUBSCRIPTION_AVAILABLE"] = "true"
        actions.append("CLAUDE_CODE_SUBSCRIPTION_AVAILABLE <= auto-detect")

    if gemini_available and not os.getenv("GEMINI_SUBSCRIPTION_AVAILABLE"):
        os.environ["GEMINI_SUBSCRIPTION_AVAILABLE"] = "true"
        resolved["GEMINI_SUBSCRIPTION_AVAILABLE"] = "true"
        actions.append("GEMINI_SUBSCRIPTION_AVAILABLE <= auto-detect")

    written: List[str] = []
    if write_env and resolved:
        written = _append_missing_env_vars(Path(env_path), resolved)

    return {
        "actions": actions,
        "resolved": resolved,
        "written": written,
        "subscription_cli": {
            "codex_cmd": codex_cmd,
            "codex_available": codex_available,
            "claude_cmd": claude_cmd,
            "claude_available": claude_available,
            "gemini_cmd": gemini_cmd,
            "gemini_available": gemini_available,
        },
    }


def build_routing_matrix(router: ModelRouter) -> List[Dict[str, Any]]:
    """Build a practical routing matrix for major task classes."""
    cases = [
        ("planning_critical", TaskType.PLANNING, TaskComplexity.COMPLEX, "critical"),
        ("codegen_critical", TaskType.CODE_GENERATION, TaskComplexity.COMPLEX, "critical"),
        ("codegen_normal", TaskType.CODE_GENERATION, TaskComplexity.MEDIUM, "normal"),
        ("security_critical", TaskType.SECURITY_AUDIT, TaskComplexity.COMPLEX, "critical"),
        ("qa_low", TaskType.TEST_GENERATION, TaskComplexity.SIMPLE, "low"),
        ("vision_normal", TaskType.UI_ANALYSIS, TaskComplexity.MEDIUM, "normal"),
        ("monitor_low", TaskType.MONITORING, TaskComplexity.SIMPLE, "low"),
    ]

    matrix: List[Dict[str, Any]] = []
    for label, task_type, complexity, importance in cases:
        decision = router.get_routing_decision(
            task_type=task_type,
            complexity=complexity,
            importance=importance,
        )
        matrix.append(
            {
                "label": label,
                "task_type": task_type.value,
                "complexity": complexity.value,
                "importance": importance,
                "chain": decision["chain"],
                "runtime_chain": decision["runtime_chain"],
            }
        )
    return matrix


def run_full_sync(
    write_env: bool = False,
    env_path: str = ".env",
    output_path: str = "data/state/runtime_sync.json",
) -> Dict[str, Any]:
    """
    Run full runtime sync and persist a snapshot for dashboard/ops visibility.
    """
    provider_sync = sync_provider_env(write_env=write_env, env_path=env_path)
    router = ModelRouter()
    matrix = build_routing_matrix(router)

    report = {
        "timestamp": datetime.now().isoformat(),
        "provider_sync": provider_sync,
        "routing_flags": {
            "model_routing_mode": os.getenv("MODEL_ROUTING_MODE", "adaptive"),
            "enable_smart_routing": os.getenv("ENABLE_SMART_ROUTING", "true"),
            "enable_subscription_fallback": os.getenv("ENABLE_SUBSCRIPTION_FALLBACK", "false"),
            "subscription_fallback_critical_only": os.getenv("SUBSCRIPTION_FALLBACK_CRITICAL_ONLY", "false"),
            "subscription_first_for_critical": os.getenv("SUBSCRIPTION_FIRST_FOR_CRITICAL", "false"),
            "enable_subscription_primary_routing": os.getenv("ENABLE_SUBSCRIPTION_PRIMARY_ROUTING", "false"),
            "subscription_primary_cost_only": os.getenv("SUBSCRIPTION_PRIMARY_COST_ONLY", "true"),
            "codex_subscription_model": os.getenv("CODEX_SUBSCRIPTION_MODEL", "codex-5.3-x2"),
            "codex_subscription_low_model": os.getenv("CODEX_SUBSCRIPTION_LOW_MODEL", "codex-5.3-mini"),
            "gemini_subscription_flash_model": os.getenv("GEMINI_SUBSCRIPTION_FLASH_MODEL", "gemini-3.0-flash"),
            "gemini_subscription_pro_model": os.getenv("GEMINI_SUBSCRIPTION_PRO_MODEL", "gemini-3.0-pro"),
        },
        "provider_status": {
            "glm_api": bool(os.getenv("GLM_API_KEY")),
            "gemini_api": bool(os.getenv("GOOGLE_API_KEY")),
            "minimax_api": bool(os.getenv("MINIMAX_API_KEY")),
            "claude_profile": bool(os.getenv("CLAUDE_CODE_API_KEY") or os.getenv("MINIMAX_API_KEY")),
            "codex_subscription": bool(router.subscription_flags.get("subscription_codex")),
            "claude_subscription": bool(router.subscription_flags.get("subscription_claude")),
            "gemini_subscription": bool(router.subscription_flags.get("subscription_gemini")),
        },
        "routing_matrix": matrix,
        "usage_summary": router.get_usage_summary(),
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report
