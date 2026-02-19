"""
Centralized OpenClaw Configuration
===================================
Consolidates all OpenClaw-related environment variables and settings
into a single, well-documented module.

Instead of 80+ scattered os.getenv() calls in monitor/app.py,
this module provides:
1. Typed, validated configuration via dataclass
2. Sensible defaults with documentation
3. Adaptive tuning based on runtime state
4. A single import for all OpenClaw settings
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Set


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    return max(minimum, int(os.getenv(name, str(default))))


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    return max(minimum, float(os.getenv(name, str(default))))


def _env_set(name: str, default: str, separator: str = ",") -> FrozenSet[str]:
    raw = os.getenv(name, default) or ""
    return frozenset(item.strip().lower() for item in raw.split(separator) if item.strip())


@dataclass(frozen=True)
class OpenClawExecutionConfig:
    """Core execution mode settings."""
    mode: str = "headless"                           # headless | hybrid | browser
    browser_mode_enabled: bool = False               # hybrid or browser
    flow_mode_enabled: bool = False                  # browser only

    @classmethod
    def from_env(cls) -> "OpenClawExecutionConfig":
        mode = str(os.getenv("OPENCLAW_EXECUTION_MODE", "headless") or "headless").strip().lower()
        if mode not in {"headless", "hybrid", "browser"}:
            mode = "headless"
        return cls(
            mode=mode,
            browser_mode_enabled=mode in {"hybrid", "browser"},
            flow_mode_enabled=mode == "browser",
        )


@dataclass(frozen=True)
class OpenClawBrowserConfig:
    """Browser-specific settings."""
    auto_start: bool = False
    timeout_ms: int = 30000
    sync_wait_timeout_ms: int = 4500
    default_async_actions: FrozenSet[str] = frozenset({"open", "navigate", "click", "type", "press", "snapshot", "hard_refresh"})
    dashboard_url: str = "http://127.0.0.1:5050/"
    chrome_app: str = "Google Chrome"
    force_new_instance: bool = False
    chrome_bounds: str = "0,24,1440,900"
    launch_with_extension: bool = False

    @classmethod
    def from_env(cls, exec_cfg: OpenClawExecutionConfig) -> "OpenClawBrowserConfig":
        ext_enabled = _env_bool("OPENCLAW_EXTENSION_AUTOMATION_ENABLED", False)
        return cls(
            auto_start=_env_bool("OPENCLAW_BROWSER_AUTO_START", False),
            timeout_ms=_env_int("OPENCLAW_BROWSER_TIMEOUT_MS", 30000, minimum=5000),
            sync_wait_timeout_ms=_env_int("OPENCLAW_SYNC_WAIT_TIMEOUT_MS", 4500, minimum=1200),
            default_async_actions=_env_set(
                "OPENCLAW_DEFAULT_ASYNC_BROWSER_ACTIONS",
                "open,navigate,click,type,press,snapshot,hard_refresh",
            ),
            dashboard_url=os.getenv("OPENCLAW_DASHBOARD_URL", "http://127.0.0.1:5050/"),
            chrome_app=os.getenv("OPENCLAW_CHROME_APP", "Google Chrome"),
            force_new_instance=_env_bool("OPENCLAW_FORCE_NEW_CHROME_INSTANCE", exec_cfg.flow_mode_enabled),
            chrome_bounds=os.getenv("OPENCLAW_CHROME_BOUNDS", "0,24,1440,900"),
            launch_with_extension=_env_bool(
                "OPENCLAW_LAUNCH_WITH_EXTENSION",
                exec_cfg.flow_mode_enabled and ext_enabled,
            ),
        )


@dataclass(frozen=True)
class OpenClawSelfHealConfig:
    """Self-healing configuration."""
    enabled: bool = False
    interval_sec: int = 20
    fallback_enabled: bool = True
    flow_command: str = "auto unstick run one cycle resume"
    bootstrap: bool = False
    open_dashboard: bool = False
    open_cooldown_sec: int = 300
    fallback_cooldown_sec: int = 90

    @classmethod
    def from_env(cls, ext_enabled: bool) -> "OpenClawSelfHealConfig":
        enabled = _env_bool("OPENCLAW_SELF_HEAL_ENABLED", False) and ext_enabled
        bootstrap = _env_bool("OPENCLAW_SELF_HEAL_BOOTSTRAP", False) and ext_enabled
        return cls(
            enabled=enabled,
            interval_sec=_env_int("OPENCLAW_SELF_HEAL_INTERVAL_SEC", 20, minimum=5),
            fallback_enabled=_env_bool("OPENCLAW_SELF_HEAL_FALLBACK", True),
            flow_command=os.getenv("OPENCLAW_SELF_HEAL_FLOW_COMMAND", "auto unstick run one cycle resume"),
            bootstrap=bootstrap,
            open_dashboard=_env_bool("OPENCLAW_SELF_HEAL_OPEN_DASHBOARD", False),
            open_cooldown_sec=_env_int("OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC", 300, minimum=30),
            fallback_cooldown_sec=_env_int("OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC", 90, minimum=15),
        )


@dataclass(frozen=True)
class OpenClawAttachConfig:
    """Auto-attach settings."""
    enabled: bool = False
    retries: int = 2
    delay_sec: float = 1.2
    heuristic: bool = True
    icon_match: bool = True
    cooldown_sec: float = 30.0

    @classmethod
    def from_env(cls, exec_cfg: OpenClawExecutionConfig, ext_enabled: bool) -> "OpenClawAttachConfig":
        enabled = _env_bool(
            "OPENCLAW_AUTO_ATTACH_ENABLED",
            exec_cfg.flow_mode_enabled and ext_enabled,
        ) and ext_enabled
        return cls(
            enabled=enabled,
            retries=_env_int("OPENCLAW_AUTO_ATTACH_RETRIES", 2, minimum=1),
            delay_sec=_env_float("OPENCLAW_AUTO_ATTACH_DELAY_SEC", 1.2),
            heuristic=_env_bool("OPENCLAW_AUTO_ATTACH_HEURISTIC", True),
            icon_match=_env_bool("OPENCLAW_AUTO_ATTACH_ICON_MATCH", True),
            cooldown_sec=_env_float("OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC", 30.0, minimum=5.0),
        )


@dataclass(frozen=True)
class OpenClawRuntimeGuardConfig:
    """Runtime guard / fail-fast settings."""
    fail_fast_on_streak: bool = True
    streak_threshold: int = 2
    disable_sec: int = 600

    @classmethod
    def from_env(cls) -> "OpenClawRuntimeGuardConfig":
        return cls(
            fail_fast_on_streak=_env_bool("OPENCLAW_FAIL_FAST_ON_ATTACH_STREAK", True),
            streak_threshold=_env_int("OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD", 2, minimum=1),
            disable_sec=_env_int("OPENCLAW_RUNTIME_DISABLE_SEC", 600, minimum=30),
        )


@dataclass(frozen=True)
class OpenClawCircuitBreakerConfig:
    """Circuit breaker for command failures."""
    trigger_count: int = 3
    open_sec: int = 60
    class_block_sec: int = 300
    escalate_after: int = 2

    @classmethod
    def from_env(cls) -> "OpenClawCircuitBreakerConfig":
        return cls(
            trigger_count=_env_int("OPENCLAW_CIRCUIT_TRIGGER_COUNT", 3, minimum=2),
            open_sec=_env_int("OPENCLAW_CIRCUIT_OPEN_SEC", 60, minimum=20),
            class_block_sec=_env_int("OPENCLAW_CIRCUIT_CLASS_BLOCK_SEC", 300, minimum=60),
            escalate_after=_env_int("OPENCLAW_CIRCUIT_ESCALATE_AFTER", 2, minimum=1),
        )


@dataclass(frozen=True)
class OpenClawQueueConfig:
    """Command queue settings."""
    enabled: bool = True
    session_max: int = 80
    manual_hold_sec: int = 30
    idempotency_ttl_sec: int = 120
    wal_path: str = ""

    @classmethod
    def from_env(cls) -> "OpenClawQueueConfig":
        default_wal = str(Path(__file__).parent.parent.parent / "data" / "state" / "openclaw_commands_wal.jsonl")
        return cls(
            enabled=_env_bool("OPENCLAW_QUEUE_ENABLED", True),
            session_max=_env_int("OPENCLAW_SESSION_QUEUE_MAX", 80, minimum=10),
            manual_hold_sec=_env_int("OPENCLAW_MANUAL_HOLD_SEC", 30, minimum=3),
            idempotency_ttl_sec=_env_int("OPENCLAW_IDEMPOTENCY_TTL_SEC", 120, minimum=30),
            wal_path=os.getenv("OPENCLAW_COMMANDS_WAL_PATH", default_wal),
        )


@dataclass(frozen=True)
class OpenClawCLIConfig:
    """CLI retry settings."""
    max_retries: int = 2
    retry_delay_sec: float = 0.4

    @classmethod
    def from_env(cls) -> "OpenClawCLIConfig":
        return cls(
            max_retries=_env_int("OPENCLAW_CLI_MAX_RETRIES", 2, minimum=1),
            retry_delay_sec=_env_float("OPENCLAW_CLI_RETRY_DELAY_SEC", 0.4),
        )


@dataclass(frozen=True)
class OpenClawPolicyConfig:
    """Policy and safety settings."""
    mostly_auto: bool = True
    require_task_lease: bool = False
    lease_heartbeat_sec: int = 90
    autonomous_ui_enabled: bool = False
    action_debounce_sec: float = 6.0
    auto_idempotency_enabled: bool = True
    auto_idempotency_window_sec: int = 8
    rate_limit_per_min: int = 120
    high_risk_allowlist: FrozenSet[str] = frozenset()
    critical_keywords: FrozenSet[str] = frozenset()
    hard_deny_keywords: FrozenSet[str] = frozenset()

    @classmethod
    def from_env(cls) -> "OpenClawPolicyConfig":
        return cls(
            mostly_auto=_env_bool("OPENCLAW_POLICY_MOSTLY_AUTO", True),
            require_task_lease=_env_bool("OPENCLAW_REQUIRE_TASK_LEASE", False),
            lease_heartbeat_sec=_env_int("OPENCLAW_LEASE_HEARTBEAT_SEC", 90, minimum=15),
            autonomous_ui_enabled=_env_bool("OPENCLAW_AUTONOMOUS_UI_ENABLED", False),
            action_debounce_sec=_env_float("OPENCLAW_ACTION_DEBOUNCE_SEC", 6.0, minimum=2.0),
            auto_idempotency_enabled=_env_bool("OPENCLAW_AUTO_IDEMPOTENCY_ENABLED", True),
            auto_idempotency_window_sec=_env_int("OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC", 8, minimum=3),
            rate_limit_per_min=_env_int("API_RATE_LIMIT_OPENCLAW_PER_MIN", 120, minimum=10),
            high_risk_allowlist=_env_set("OPENCLAW_HIGH_RISK_ALLOWLIST", ""),
            critical_keywords=_env_set("OPENCLAW_CRITICAL_KEYWORDS", ""),
            hard_deny_keywords=_env_set("OPENCLAW_HARD_DENY_KEYWORDS", ""),
        )


@dataclass(frozen=True)
class OpenClawFlowConfig:
    """Dashboard flow settings."""
    open_cooldown_sec: int = 20
    chat_placeholders: tuple = (
        "nhập lệnh", "nhập yêu cầu", "send command", "run_cycle",
        "status / pause", "lệnh tùy chỉnh", "lệnh cho các agent",
        "nhập tự nhiên", "type natural command",
    )
    autopilot_enabled: bool = False
    autopilot_cooldown_sec: int = 120
    autopilot_chat: str = "auto unstick run one cycle resume"

    @classmethod
    def from_env(cls) -> "OpenClawFlowConfig":
        return cls(
            open_cooldown_sec=_env_int("OPENCLAW_FLOW_OPEN_COOLDOWN_SEC", 20, minimum=5),
            autopilot_enabled=_env_bool("MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", False),
            autopilot_cooldown_sec=_env_int("MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC", 120, minimum=20),
            autopilot_chat=str(os.getenv("MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT", "auto unstick run one cycle resume") or "").strip(),
        )


class OpenClawConfig:
    """
    Master configuration container for all OpenClaw settings.

    Usage:
        config = OpenClawConfig.load()
        if config.execution.browser_mode_enabled:
            ...
        if config.circuit_breaker.trigger_count > 5:
            ...
    """

    def __init__(
        self,
        execution: OpenClawExecutionConfig,
        browser: OpenClawBrowserConfig,
        self_heal: OpenClawSelfHealConfig,
        attach: OpenClawAttachConfig,
        runtime_guard: OpenClawRuntimeGuardConfig,
        circuit_breaker: OpenClawCircuitBreakerConfig,
        queue: OpenClawQueueConfig,
        cli: OpenClawCLIConfig,
        policy: OpenClawPolicyConfig,
        flow: OpenClawFlowConfig,
    ):
        self.execution = execution
        self.browser = browser
        self.self_heal = self_heal
        self.attach = attach
        self.runtime_guard = runtime_guard
        self.circuit_breaker = circuit_breaker
        self.queue = queue
        self.cli = cli
        self.policy = policy
        self.flow = flow

    @classmethod
    def load(cls) -> "OpenClawConfig":
        """Load all configuration from environment variables."""
        ext_enabled = _env_bool("OPENCLAW_EXTENSION_AUTOMATION_ENABLED", False)
        exec_cfg = OpenClawExecutionConfig.from_env()
        return cls(
            execution=exec_cfg,
            browser=OpenClawBrowserConfig.from_env(exec_cfg),
            self_heal=OpenClawSelfHealConfig.from_env(ext_enabled),
            attach=OpenClawAttachConfig.from_env(exec_cfg, ext_enabled),
            runtime_guard=OpenClawRuntimeGuardConfig.from_env(),
            circuit_breaker=OpenClawCircuitBreakerConfig.from_env(),
            queue=OpenClawQueueConfig.from_env(),
            cli=OpenClawCLIConfig.from_env(),
            policy=OpenClawPolicyConfig.from_env(),
            flow=OpenClawFlowConfig.from_env(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all config for API/debug inspection."""
        import dataclasses
        result: Dict[str, Any] = {}
        for section_name in [
            "execution", "browser", "self_heal", "attach",
            "runtime_guard", "circuit_breaker", "queue", "cli",
            "policy", "flow",
        ]:
            section = getattr(self, section_name)
            d = dataclasses.asdict(section)
            # Convert frozensets to sorted lists for JSON serialization
            for k, v in d.items():
                if isinstance(v, frozenset):
                    d[k] = sorted(v)
            result[section_name] = d
        return result

    def summary(self) -> Dict[str, Any]:
        """Short summary for health endpoints."""
        return {
            "mode": self.execution.mode,
            "browser_enabled": self.execution.browser_mode_enabled,
            "self_heal_enabled": self.self_heal.enabled,
            "auto_attach_enabled": self.attach.enabled,
            "queue_enabled": self.queue.enabled,
            "policy_auto": self.policy.mostly_auto,
            "circuit_trigger": self.circuit_breaker.trigger_count,
            "rate_limit_rpm": self.policy.rate_limit_per_min,
        }


# ── Module-level singleton ──────────────────────────────────────

_config: Optional[OpenClawConfig] = None
_config_lock = threading.Lock()


def get_openclaw_config() -> OpenClawConfig:
    """Get or create the singleton OpenClawConfig."""
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = OpenClawConfig.load()
    return _config


def reload_config() -> OpenClawConfig:
    """Force reload configuration from environment."""
    global _config
    with _config_lock:
        _config = OpenClawConfig.load()
    return _config
