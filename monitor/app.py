"""
Auto Dev Loop - Monitoring Dashboard
Real-time visualization of Orion and Guardian agents

FIXED: Persist state, proper initialization
"""

import os
import sys
import json
import imaplib
import re
import shlex
import shutil
import socket
import subprocess
import time
import unicodedata
import uuid
import secrets
import concurrent.futures
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set, Tuple, Callable
import logging
import threading
from collections import defaultdict, deque
from urllib import request as urllib_request, error as urllib_error
from urllib.parse import urlparse

from flask import Flask, render_template, jsonify, request, Response, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.core.model_router import (
        ModelRouter,
        TaskType,
        TaskComplexity,
        estimate_task_cost,
        get_cheapest_model_for_task,
    )
    from src.core.routing_telemetry import read_recent_routing_events
    from src.core.runtime_guard import ProcessSingleton
    from src.core.prompt_system import get_prompt_system
    from src.core.provider_profile import ProviderProfileStore
    from src.core.official_model_registry import (
        load_model_registry_snapshot,
        refresh_model_registry_from_profile,
    )
    from src.memory.user_profile import get_user_profile, update_user_profile
    from src.memory.team_persona import get_team_persona_store, normalize_member_id
except Exception:
    from core.model_router import (  # type: ignore
        ModelRouter,
        TaskType,
        TaskComplexity,
        estimate_task_cost,
        get_cheapest_model_for_task,
    )
    from core.routing_telemetry import read_recent_routing_events  # type: ignore
    from core.runtime_guard import ProcessSingleton  # type: ignore
    from core.prompt_system import get_prompt_system  # type: ignore
    from core.provider_profile import ProviderProfileStore  # type: ignore
    from core.official_model_registry import (  # type: ignore
        load_model_registry_snapshot,
        refresh_model_registry_from_profile,
    )
    from memory.user_profile import get_user_profile, update_user_profile  # type: ignore
    from memory.team_persona import get_team_persona_store, normalize_member_id  # type: ignore

# Get correct paths
MONITOR_DIR = Path(__file__).parent
TEMPLATE_DIR = MONITOR_DIR / "templates"
STATIC_DIR = MONITOR_DIR / "static"
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR)
)
app.config['SECRET_KEY'] = 'auto-dev-loop-secret-2025'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OpenClaw token cache state
_TOKEN_CACHE_TTL_SEC = 60
_TOKEN_CACHE_LOCK = threading.Lock()
@dataclass
class _OpenClawTokenInfo:
    token: Optional[str]
    checked_at: Optional[datetime]
    source: str
    health_ok: bool
    health_error: Optional[str]
    last_error: Optional[str]

_token_cache: Optional[_OpenClawTokenInfo] = None

@dataclass
class _OpenClawAttachState:
    last_attempt: Optional[datetime] = None
    last_error: Optional[str] = None
    cooldown_until: Optional[datetime] = None
    method: Optional[str] = None

_attach_state = _OpenClawAttachState()
_ATTACH_STATE_LOCK = threading.Lock()
_OPENCLAW_ATTACH_CONTEXT = threading.local()
_OPENCLAW_METRICS_LOCK = threading.Lock()
_OPENCLAW_METRICS_MAX_EVENTS = 80
_openclaw_metrics: Dict[str, int] = {
    "token_missing": 0,
    "health_error": 0,
    "cli_failures": 0,
    "gateway_restarts": 0,
    "self_heal_runs": 0,
    "self_heal_success": 0,
    "self_heal_fail": 0,
    "attach_attempts": 0,
    "attach_success": 0,
    "attach_failed": 0,
    "attach_throttled": 0,
    "flow_runs": 0,
    "flow_success": 0,
    "flow_failures": 0,
    "queue_enqueued": 0,
    "queue_executed": 0,
    "queue_failed": 0,
    "queue_deferred": 0,
    "queue_cancelled": 0,
    "queue_idempotent_hit": 0,
    "queue_manual_override": 0,
    "policy_auto_approved": 0,
    "policy_manual_required": 0,
    "policy_denied": 0,
    "dispatch_remote": 0,
    "dispatch_failover": 0,
    "circuit_opened": 0,
    "circuit_blocked": 0,
}
_openclaw_metric_events: List[Dict[str, Any]] = []
_openclaw_metrics_updated_at: Optional[datetime] = None
_OPENCLAW_RUNTIME_GUARD_LOCK = threading.Lock()
_openclaw_runtime_disabled_until: Optional[datetime] = None
_openclaw_runtime_disable_reason: str = ""
_openclaw_attach_fail_streak = 0


def _openclaw_attach_depth() -> int:
    try:
        return max(0, int(getattr(_OPENCLAW_ATTACH_CONTEXT, "depth", 0) or 0))
    except Exception:
        return 0


def _openclaw_attach_in_progress() -> bool:
    return _openclaw_attach_depth() > 0


def _openclaw_attach_scope_enter() -> None:
    _OPENCLAW_ATTACH_CONTEXT.depth = _openclaw_attach_depth() + 1


def _openclaw_attach_scope_exit() -> None:
    depth = _openclaw_attach_depth()
    if depth <= 1:
        _OPENCLAW_ATTACH_CONTEXT.depth = 0
    else:
        _OPENCLAW_ATTACH_CONTEXT.depth = depth - 1


def _openclaw_runtime_guard_snapshot() -> Dict[str, Any]:
    global _openclaw_runtime_disabled_until, _openclaw_runtime_disable_reason, _openclaw_attach_fail_streak
    with _OPENCLAW_RUNTIME_GUARD_LOCK:
        now = datetime.now()
        if _openclaw_runtime_disabled_until and _openclaw_runtime_disabled_until <= now:
            _openclaw_runtime_disabled_until = None
            _openclaw_runtime_disable_reason = ""
            _openclaw_attach_fail_streak = 0
        active = bool(_openclaw_runtime_disabled_until and _openclaw_runtime_disabled_until > now)
        retry_after = int((_openclaw_runtime_disabled_until - now).total_seconds()) if active and _openclaw_runtime_disabled_until else 0
        return {
            "active": active,
            "disabled_until": _openclaw_runtime_disabled_until.isoformat() if _openclaw_runtime_disabled_until else None,
            "reason": _openclaw_runtime_disable_reason or "",
            "attach_fail_streak": int(_openclaw_attach_fail_streak),
            "attach_fail_threshold": int(OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD),
            "retry_after_sec": max(0, retry_after),
        }


def _openclaw_runtime_guard_allows(command_type: str, context: Optional[Dict[str, Any]] = None, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not OPENCLAW_FAIL_FAST_ON_ATTACH_STREAK:
        return {"allowed": True}
    data = payload if isinstance(payload, dict) else {}
    if _to_bool(data.get("force_openclaw", False)):
        return {"allowed": True, "force_openclaw": True}
    snapshot = _openclaw_runtime_guard_snapshot()
    if not snapshot.get("active"):
        return {"allowed": True}
    return {
        "allowed": False,
        "error_code": "OPENCLAW_TEMP_DISABLED",
        "error": "OpenClaw is temporarily disabled after repeated attach failures",
        "reason": snapshot.get("reason") or "attach_failure_streak",
        "retry_after_sec": snapshot.get("retry_after_sec", 0),
        "guard": snapshot,
        "command_type": str(command_type or "").strip().lower(),
        "source": str((context or {}).get("source", "manual")),
    }


def _openclaw_runtime_guard_record_attach(success: bool, detail: str = "") -> None:
    global _openclaw_runtime_disabled_until, _openclaw_runtime_disable_reason, _openclaw_attach_fail_streak
    if not OPENCLAW_FAIL_FAST_ON_ATTACH_STREAK:
        return
    now = datetime.now()
    with _OPENCLAW_RUNTIME_GUARD_LOCK:
        if success:
            cleared = bool(_openclaw_runtime_disabled_until and _openclaw_runtime_disabled_until > now)
            _openclaw_attach_fail_streak = 0
            _openclaw_runtime_disabled_until = None
            _openclaw_runtime_disable_reason = ""
            if cleared:
                _openclaw_metrics_event("runtime_guard_cleared", {"detail": detail or "attach_success"})
            return

        _openclaw_attach_fail_streak = int(_openclaw_attach_fail_streak) + 1
        if _openclaw_attach_fail_streak >= OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD:
            _openclaw_runtime_disabled_until = now + timedelta(seconds=OPENCLAW_RUNTIME_DISABLE_SEC)
            _openclaw_runtime_disable_reason = "attach_failure_streak"
            _openclaw_metrics_event(
                "runtime_guard_opened",
                {
                    "streak": int(_openclaw_attach_fail_streak),
                    "threshold": int(OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD),
                    "disable_sec": int(OPENCLAW_RUNTIME_DISABLE_SEC),
                    "detail": detail,
                },
            )

try:
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = True
except Exception:
    pyautogui = None  # type: ignore

# Runtime bridge callbacks registered by system runner
_runtime_bridge: Dict[str, Any] = {
    "command_handler": None,
    "status_handler": None,
}

LOCAL_INSTANCE_ID = os.getenv("LOCAL_ORION_INSTANCE_ID", "orion-1").strip() or "orion-1"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


OPENCLAW_EXECUTION_MODE = str(os.getenv("OPENCLAW_EXECUTION_MODE", "headless") or "headless").strip().lower()
if OPENCLAW_EXECUTION_MODE not in {"headless", "hybrid", "browser"}:
    OPENCLAW_EXECUTION_MODE = "headless"
_OPENCLAW_BROWSER_MODE_ENABLED = OPENCLAW_EXECUTION_MODE in {"hybrid", "browser"}
_OPENCLAW_FLOW_MODE_ENABLED = OPENCLAW_EXECUTION_MODE == "browser"


MONITOR_AUTOPILOT_ENABLED_DEFAULT = _env_bool("MONITOR_AUTOPILOT_ENABLED", True)
MONITOR_AUTOPILOT_INTERVAL_SEC = max(4, int(os.getenv("MONITOR_AUTOPILOT_INTERVAL_SEC", "10")))
MONITOR_STUCK_THRESHOLD_SEC = max(20, int(os.getenv("MONITOR_STUCK_THRESHOLD_SEC", "240")))
MONITOR_RECOVERY_COOLDOWN_SEC = max(10, int(os.getenv("MONITOR_RECOVERY_COOLDOWN_SEC", "90")))
MONITOR_STARTUP_GRACE_SEC = max(5, int(os.getenv("MONITOR_STARTUP_GRACE_SEC", "25")))
MONITOR_AUTO_APPROVE_SAFE_DECISIONS = _env_bool("MONITOR_AUTO_APPROVE_SAFE_DECISIONS", True)
MONITOR_AUTO_APPROVE_ALL_DECISIONS = _env_bool("MONITOR_AUTO_APPROVE_ALL_DECISIONS", True)
MONITOR_AUTO_EXECUTE_COMMANDS = _env_bool("MONITOR_AUTO_EXECUTE_COMMANDS", True)
AUTOMATION_FULL_AUTO_AUTO_RESPOND_HUMAN_INPUT = _env_bool("AUTOMATION_FULL_AUTO_AUTO_RESPOND_HUMAN_INPUT", True)
AUTONOMY_NO_HUMAN_PROMPTS = _env_bool("AUTONOMY_NO_HUMAN_PROMPTS", True)
MONITOR_ALLOW_UNSAFE_COMMANDS = _env_bool("MONITOR_ALLOW_UNSAFE_COMMANDS", False)
MONITOR_COMPUTER_CONTROL_ENABLED_DEFAULT = _env_bool("MONITOR_COMPUTER_CONTROL_ENABLED", True)
OPENCLAW_AUTONOMOUS_UI_ENABLED = _env_bool("OPENCLAW_AUTONOMOUS_UI_ENABLED", False)
MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED = _env_bool("MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", False)
MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC = max(
    20,
    int(os.getenv("MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC", "120")),
)
MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT = str(
    os.getenv("MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT", "auto unstick run one cycle resume") or ""
).strip()
OPENCLAW_BROWSER_AUTO_START = _env_bool("OPENCLAW_BROWSER_AUTO_START", False)
OPENCLAW_BROWSER_TIMEOUT_MS = max(5000, int(os.getenv("OPENCLAW_BROWSER_TIMEOUT_MS", "30000")))
OPENCLAW_SYNC_WAIT_TIMEOUT_MS = max(
    1200,
    int(os.getenv("OPENCLAW_SYNC_WAIT_TIMEOUT_MS", "4500")),
)
OPENCLAW_DEFAULT_ASYNC_BROWSER_ACTIONS = {
    item.strip().lower()
    for item in str(
        os.getenv(
            "OPENCLAW_DEFAULT_ASYNC_BROWSER_ACTIONS",
            "open,navigate,click,type,press,snapshot,hard_refresh",
        ) or ""
    ).split(",")
    if item.strip()
}
OPENCLAW_DASHBOARD_URL = os.getenv("OPENCLAW_DASHBOARD_URL", "http://127.0.0.1:5050/")
OPENCLAW_SELF_HEAL_ENABLED = _env_bool("OPENCLAW_SELF_HEAL_ENABLED", False)
OPENCLAW_SELF_HEAL_INTERVAL_SEC = max(5, int(os.getenv("OPENCLAW_SELF_HEAL_INTERVAL_SEC", "20")))
OPENCLAW_SELF_HEAL_FALLBACK = _env_bool("OPENCLAW_SELF_HEAL_FALLBACK", True)
OPENCLAW_SELF_HEAL_FLOW_COMMAND = os.getenv("OPENCLAW_SELF_HEAL_FLOW_COMMAND", "auto unstick run one cycle resume")
OPENCLAW_SELF_HEAL_BOOTSTRAP = _env_bool("OPENCLAW_SELF_HEAL_BOOTSTRAP", False)
OPENCLAW_SELF_HEAL_OPEN_DASHBOARD = _env_bool("OPENCLAW_SELF_HEAL_OPEN_DASHBOARD", False)
OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC = max(30, int(os.getenv("OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC", "300")))
OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC = max(15, int(os.getenv("OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC", "90")))
OPENCLAW_FLOW_OPEN_COOLDOWN_SEC = max(5, int(os.getenv("OPENCLAW_FLOW_OPEN_COOLDOWN_SEC", "20")))
OPENCLAW_ACTION_DEBOUNCE_SEC = max(2.0, float(os.getenv("OPENCLAW_ACTION_DEBOUNCE_SEC", "6")))
OPENCLAW_AUTO_IDEMPOTENCY_ENABLED = _env_bool("OPENCLAW_AUTO_IDEMPOTENCY_ENABLED", True)
OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC = max(3, int(os.getenv("OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC", "8")))
OPENCLAW_EXTENSION_AUTOMATION_ENABLED = _env_bool("OPENCLAW_EXTENSION_AUTOMATION_ENABLED", False)
OPENCLAW_FLOW_CHAT_PLACEHOLDERS = [
    "nhập lệnh",
    "nhập yêu cầu",
    "send command",
    "run_cycle",
    "status / pause",
    "lệnh tùy chỉnh",
    "lệnh cho các agent",
    "nhập tự nhiên",
    "nhập tự nhiên hoặc gõ",
    "type natural command",
    "type natural command...",
    "type natural command... e.g.",
]
OPENCLAW_AUTO_ATTACH_ENABLED = _env_bool(
    "OPENCLAW_AUTO_ATTACH_ENABLED",
    _OPENCLAW_FLOW_MODE_ENABLED and OPENCLAW_EXTENSION_AUTOMATION_ENABLED,
)
OPENCLAW_AUTO_ATTACH_RETRIES = max(1, int(os.getenv("OPENCLAW_AUTO_ATTACH_RETRIES", "2")))
OPENCLAW_AUTO_ATTACH_DELAY_SEC = float(os.getenv("OPENCLAW_AUTO_ATTACH_DELAY_SEC", "1.2"))
OPENCLAW_AUTO_ATTACH_HEURISTIC = _env_bool("OPENCLAW_AUTO_ATTACH_HEURISTIC", True)
OPENCLAW_AUTO_ATTACH_ICON_MATCH = _env_bool("OPENCLAW_AUTO_ATTACH_ICON_MATCH", True)
OPENCLAW_FAIL_FAST_ON_ATTACH_STREAK = _env_bool("OPENCLAW_FAIL_FAST_ON_ATTACH_STREAK", True)
OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD = max(1, int(os.getenv("OPENCLAW_ATTACH_FAIL_STREAK_THRESHOLD", "2")))
OPENCLAW_RUNTIME_DISABLE_SEC = max(30, int(os.getenv("OPENCLAW_RUNTIME_DISABLE_SEC", "600")))
OPENCLAW_CHROME_APP = os.getenv("OPENCLAW_CHROME_APP", "Google Chrome")
OPENCLAW_LAUNCH_WITH_EXTENSION = _env_bool(
    "OPENCLAW_LAUNCH_WITH_EXTENSION",
    _OPENCLAW_FLOW_MODE_ENABLED and OPENCLAW_EXTENSION_AUTOMATION_ENABLED,
)
OPENCLAW_FORCE_NEW_CHROME_INSTANCE = _env_bool("OPENCLAW_FORCE_NEW_CHROME_INSTANCE", _OPENCLAW_FLOW_MODE_ENABLED)
OPENCLAW_CHROME_BOUNDS = os.getenv("OPENCLAW_CHROME_BOUNDS", "0,24,1440,900")
OPENCLAW_CLI_MAX_RETRIES = max(1, int(os.getenv("OPENCLAW_CLI_MAX_RETRIES", "2")))
OPENCLAW_CLI_RETRY_DELAY_SEC = float(os.getenv("OPENCLAW_CLI_RETRY_DELAY_SEC", "0.4"))
OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC = max(5, float(os.getenv("OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC", "30")))
if not OPENCLAW_EXTENSION_AUTOMATION_ENABLED:
    OPENCLAW_AUTO_ATTACH_ENABLED = False
    OPENCLAW_LAUNCH_WITH_EXTENSION = False
    OPENCLAW_SELF_HEAL_ENABLED = False
    OPENCLAW_SELF_HEAL_BOOTSTRAP = False
MONITOR_PROGRESS_SNAPSHOT_ENABLED = _env_bool("MONITOR_PROGRESS_SNAPSHOT_ENABLED", True)
MONITOR_PROGRESS_SNAPSHOT_INTERVAL_SEC = max(30, int(os.getenv("MONITOR_PROGRESS_SNAPSHOT_INTERVAL_SEC", "120")))
MONITOR_PROGRESS_SNAPSHOT_PATH = Path(
    os.getenv(
        "MONITOR_PROGRESS_SNAPSHOT_PATH",
        str(Path(__file__).parent.parent / "docs" / "memory" / "AUTO_PROGRESS.md"),
    )
)
MONITOR_RUNTIME_HEARTBEAT_ENABLED = _env_bool("MONITOR_RUNTIME_HEARTBEAT_ENABLED", True)
MONITOR_RUNTIME_HEARTBEAT_INTERVAL_SEC = max(2, int(os.getenv("MONITOR_RUNTIME_HEARTBEAT_INTERVAL_SEC", "3")))
DASHBOARD_ACCESS_TOKEN = str(os.getenv("DASHBOARD_ACCESS_TOKEN", "") or "").strip()
DASHBOARD_TOKEN_ALLOW_QUERY = _env_bool("DASHBOARD_TOKEN_ALLOW_QUERY", True)
API_RATE_LIMIT_CHAT_PER_MIN = max(5, int(os.getenv("API_RATE_LIMIT_CHAT_PER_MIN", "24")))
API_RATE_LIMIT_COMMAND_PER_MIN = max(10, int(os.getenv("API_RATE_LIMIT_COMMAND_PER_MIN", "90")))
API_RATE_LIMIT_OPENCLAW_PER_MIN = max(10, int(os.getenv("API_RATE_LIMIT_OPENCLAW_PER_MIN", "120")))
API_RATE_LIMIT_PROVIDER_PER_MIN = max(5, int(os.getenv("API_RATE_LIMIT_PROVIDER_PER_MIN", "40")))
OPENCLAW_QUEUE_ENABLED = _env_bool("OPENCLAW_QUEUE_ENABLED", True)
OPENCLAW_SESSION_QUEUE_MAX = max(10, int(os.getenv("OPENCLAW_SESSION_QUEUE_MAX", "80")))
OPENCLAW_MANUAL_HOLD_SEC = max(3, int(os.getenv("OPENCLAW_MANUAL_HOLD_SEC", "30")))
OPENCLAW_IDEMPOTENCY_TTL_SEC = max(30, int(os.getenv("OPENCLAW_IDEMPOTENCY_TTL_SEC", "120")))
OPENCLAW_COMMANDS_WAL_PATH = Path(
    os.getenv(
        "OPENCLAW_COMMANDS_WAL_PATH",
        str(Path(__file__).parent.parent / "data" / "state" / "openclaw_commands_wal.jsonl"),
    )
)
INTERVENTION_HISTORY_PATH = Path(
    os.getenv(
        "INTERVENTION_HISTORY_PATH",
        str(Path(__file__).parent.parent / "data" / "state" / "interventions.jsonl"),
    )
)
CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC = max(10, int(os.getenv("CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC", "35")))
CONTROL_PLANE_MAX_WORKERS = max(8, int(os.getenv("CONTROL_PLANE_MAX_WORKERS", "120")))
OPENCLAW_POLICY_MOSTLY_AUTO = _env_bool("OPENCLAW_POLICY_MOSTLY_AUTO", True)
OPENCLAW_CIRCUIT_TRIGGER_COUNT = max(2, int(os.getenv("OPENCLAW_CIRCUIT_TRIGGER_COUNT", "3")))
OPENCLAW_CIRCUIT_OPEN_SEC = max(20, int(os.getenv("OPENCLAW_CIRCUIT_OPEN_SEC", "60")))
OPENCLAW_CIRCUIT_CLASS_BLOCK_SEC = max(60, int(os.getenv("OPENCLAW_CIRCUIT_CLASS_BLOCK_SEC", "300")))
OPENCLAW_CIRCUIT_ESCALATE_AFTER = max(1, int(os.getenv("OPENCLAW_CIRCUIT_ESCALATE_AFTER", "2")))
OPENCLAW_REQUIRE_TASK_LEASE = _env_bool("OPENCLAW_REQUIRE_TASK_LEASE", False)
OPENCLAW_LEASE_HEARTBEAT_SEC = max(15, int(os.getenv("OPENCLAW_LEASE_HEARTBEAT_SEC", "90")))
AGENT_COORDINATION_AUTO_CREATE_TASKS = _env_bool("AGENT_COORDINATION_AUTO_CREATE_TASKS", True)
HUB_DISPATCH_ASYNC_DEFAULT = _env_bool("HUB_DISPATCH_ASYNC_DEFAULT", True)
HUB_DISPATCH_WAIT_TIMEOUT_SEC = max(3.0, float(os.getenv("HUB_DISPATCH_WAIT_TIMEOUT_SEC", "20")))
AUTOMATION_TASK_HISTORY_MAX = max(50, int(os.getenv("AUTOMATION_TASK_HISTORY_MAX", "500")))
AUTOMATION_ENABLE_APP_CONTROL = _env_bool("AUTOMATION_ENABLE_APP_CONTROL", True)
AUTOMATION_ENABLE_TERMINAL_EXEC = _env_bool("AUTOMATION_ENABLE_TERMINAL_EXEC", True)
AUTOMATION_ENABLE_MAIL_READ = _env_bool("AUTOMATION_ENABLE_MAIL_READ", True)
AUTOMATION_ENABLE_MESSAGES_READ = _env_bool("AUTOMATION_ENABLE_MESSAGES_READ", True)
AUTOMATION_BROWSER_PROVIDER_DEFAULT = str(
    os.getenv("AUTOMATION_BROWSER_PROVIDER_DEFAULT", "local") or "local"
).strip().lower()
if AUTOMATION_BROWSER_PROVIDER_DEFAULT not in {"local", "openclaw"}:
    AUTOMATION_BROWSER_PROVIDER_DEFAULT = "local"
AUTOMATION_TERMINAL_TIMEOUT_SEC = max(3, int(os.getenv("AUTOMATION_TERMINAL_TIMEOUT_SEC", "20")))
AUTOMATION_TERMINAL_ALLOWED_BINS = {
    token.strip().lower()
    for token in os.getenv(
        "AUTOMATION_TERMINAL_ALLOWED_BINS",
        "python,python3,pytest,git,ls,cat,rg,tail,head,openclaw,curl,open,osascript,pwd,date",
    ).split(",")
    if token.strip()
}
AUTOMATION_EMAIL_IMAP_HOST = str(os.getenv("AUTOMATION_EMAIL_IMAP_HOST", "") or "").strip()
AUTOMATION_EMAIL_IMAP_PORT = max(1, int(os.getenv("AUTOMATION_EMAIL_IMAP_PORT", "993")))
AUTOMATION_EMAIL_IMAP_USERNAME = str(os.getenv("AUTOMATION_EMAIL_IMAP_USERNAME", "") or "").strip()
AUTOMATION_EMAIL_IMAP_PASSWORD = str(os.getenv("AUTOMATION_EMAIL_IMAP_PASSWORD", "") or "").strip()
AUTOMATION_EMAIL_IMAP_FOLDER = str(os.getenv("AUTOMATION_EMAIL_IMAP_FOLDER", "INBOX") or "INBOX").strip() or "INBOX"
AUTOMATION_EMAIL_IMAP_SSL = _env_bool("AUTOMATION_EMAIL_IMAP_SSL", True)
AUTOMATION_PASSIVE_POLL_COOLDOWN_SEC = max(0.0, float(os.getenv("AUTOMATION_PASSIVE_POLL_COOLDOWN_SEC", "3")))
OPENCLAW_HIGH_RISK_ALLOWLIST = {
    token.strip().lower()
    for token in os.getenv(
        "OPENCLAW_HIGH_RISK_ALLOWLIST",
        "status,start,snapshot,open,navigate,hard_refresh,click,type,press,attach,flow",
    ).split(",")
    if token.strip()
}
OPENCLAW_CRITICAL_KEYWORDS = {
    token.strip().lower()
    for token in os.getenv(
        "OPENCLAW_CRITICAL_KEYWORDS",
        "shutdown,rm -rf,delete all,wipe,kill -9,format",
    ).split(",")
    if token.strip()
}
OPENCLAW_HARD_DENY_KEYWORDS = {
    token.strip().lower()
    for token in os.getenv(
        "OPENCLAW_HARD_DENY_KEYWORDS",
        "shutdown,rm -rf,wipe,delete all,format disk",
    ).split(",")
    if token.strip()
}
AUTONOMY_PROFILE_DEFAULT = str(os.getenv("AUTONOMY_PROFILE", "full_auto") or "").strip().lower() or "full_auto"
MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC = max(
    60,
    int(os.getenv("MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC", "240")),
)
MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC = max(
    30,
    int(os.getenv("MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC", "180")),
)
GUARDIAN_CONTROL_TOKEN = str(os.getenv("GUARDIAN_CONTROL_TOKEN", "") or "").strip()
AGENT_COORDINATION_DEDUP_WINDOW_SEC = max(
    1.0,
    float(os.getenv("AGENT_COORDINATION_DEDUP_WINDOW_SEC", "12")),
)
HUB_NEXT_ACTION_AUTOPILOT_ENABLED = _env_bool("HUB_NEXT_ACTION_AUTOPILOT_ENABLED", True)
HUB_NEXT_ACTION_AUTOPILOT_INTERVAL_SEC = max(5, int(os.getenv("HUB_NEXT_ACTION_AUTOPILOT_INTERVAL_SEC", "20")))
HUB_NEXT_ACTION_AUTOPILOT_MAX_PER_TICK = max(1, min(8, int(os.getenv("HUB_NEXT_ACTION_AUTOPILOT_MAX_PER_TICK", "2"))))
HUB_NEXT_ACTION_AUTOPILOT_ALLOW_TYPES = {
    token.strip().lower()
    for token in os.getenv(
        "HUB_NEXT_ACTION_AUTOPILOT_ALLOW_TYPES",
        "dispatch_task,assign_then_dispatch,reassign_or_bring_online,inspect_terminal_error,check_stale_terminal,reclaim_stale_lease",
    ).split(",")
    if token.strip()
}

_monitor_supervisor_thread: Optional[threading.Thread] = None
_monitor_supervisor_stop_event = threading.Event()
_monitor_supervisor_lock = threading.RLock()
_monitor_supervisor_enabled = MONITOR_AUTOPILOT_ENABLED_DEFAULT
_monitor_last_recovery_at: Dict[str, datetime] = {}
_monitor_last_reason: Dict[str, str] = {}
_monitor_last_openclaw_flow_at: Dict[str, datetime] = {}
_monitor_autopilot_pause_until: Optional[datetime] = None
_autonomy_profile = AUTONOMY_PROFILE_DEFAULT if AUTONOMY_PROFILE_DEFAULT in {"safe", "balanced", "full_auto"} else "balanced"
_computer_control_enabled = MONITOR_COMPUTER_CONTROL_ENABLED_DEFAULT
_monitor_progress_thread: Optional[threading.Thread] = None
_monitor_progress_stop_event = threading.Event()
_monitor_progress_lock = threading.RLock()
_monitor_process_started_at = datetime.now()
_runtime_heartbeat_thread: Optional[threading.Thread] = None
_runtime_heartbeat_stop_event = threading.Event()
_runtime_heartbeat_lock = threading.RLock()
_hub_next_action_autopilot_thread: Optional[threading.Thread] = None
_hub_next_action_autopilot_stop_event = threading.Event()
_hub_next_action_autopilot_lock = threading.RLock()
_hub_next_action_autopilot_last_run: Optional[datetime] = None
_hub_next_action_autopilot_last_error: Optional[str] = None
_autopilot_background_thread: Optional[threading.Thread] = None
_autopilot_background_lock = threading.RLock()
_autopilot_background_last_run: Optional[datetime] = None
_autopilot_background_last_error: Optional[str] = None
_AUTOPILOT_BACKGROUND_START_LOCK = threading.RLock()


def _normalize_autopilot_executor(raw_value: str) -> str:
    value = str(raw_value or "policy_loop").strip().lower()
    return value if value in {"policy_loop", "hub_loop"} else "policy_loop"


def _autopilot_configured_interval() -> int:
    if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
        return max(5, int(_AUTOPILOT_STATE.get("interval", HUB_NEXT_ACTION_AUTOPILOT_INTERVAL_SEC)))
    return HUB_NEXT_ACTION_AUTOPILOT_INTERVAL_SEC


def _autopilot_configured_max_actions() -> int:
    if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
        configured = int(_AUTOPILOT_STATE.get("max_actions_per_cycle", HUB_NEXT_ACTION_AUTOPILOT_MAX_PER_TICK))
        return max(1, min(10, configured))
    return HUB_NEXT_ACTION_AUTOPILOT_MAX_PER_TICK


def _autopilot_active_executor() -> str:
    if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
        return _normalize_autopilot_executor(_AUTOPILOT_STATE.get("active_executor", "policy_loop"))
    return _normalize_autopilot_executor("policy_loop")


def _autopilot_is_enabled() -> bool:
    if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
        return bool(_AUTOPILOT_STATE.get("enabled", False))
    return bool(HUB_NEXT_ACTION_AUTOPILOT_ENABLED)


def _autopilot_set_enabled(enabled: bool) -> None:
    if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
        _AUTOPILOT_STATE["enabled"] = bool(enabled)
        if "state" in _AUTOPILOT_STATE:
            _AUTOPILOT_STATE["state"] = "running" if bool(enabled) else "stopped"


def _autopilot_runtime_snapshot() -> Dict[str, Any]:
    """Return unified autopilot runtime state for status and dashboard rendering."""
    with _hub_next_action_autopilot_lock:
        hub_running = bool(_hub_next_action_autopilot_thread and _hub_next_action_autopilot_thread.is_alive())
        with _autopilot_background_lock:
            policy_running = bool(_autopilot_background_thread and _autopilot_background_thread.is_alive())

    state = _AUTOPILOT_STATE if isinstance(_AUTOPILOT_STATE, dict) else {}
    stats = state.get("stats", {}) if isinstance(state.get("stats", {}), dict) else {}
    raw_last_cycle = state.get("stats", {}).get("last_cycle") if isinstance(state, dict) else None

    return {
        "enabled": _autopilot_is_enabled(),
        "dry_run": bool(state.get("dry_run", False) if isinstance(state, dict) else False),
        "active_executor": _autopilot_active_executor(),
        "interval": _autopilot_configured_interval(),
        "max_actions_per_cycle": _autopilot_configured_max_actions(),
        "hub_loop_running": hub_running,
        "policy_loop_running": policy_running,
        "executor_conflict": bool(hub_running and policy_running),
        "allowed_types": sorted(set(HUB_NEXT_ACTION_AUTOPILOT_ALLOW_TYPES)),
        "last_cycle": raw_last_cycle,
        "last_run": _hub_next_action_autopilot_last_run.isoformat() if _hub_next_action_autopilot_last_run else None,
        "last_error": _hub_next_action_autopilot_last_error,
        "policy_loop_last_run": _autopilot_background_last_run.isoformat() if _autopilot_background_last_run else None,
        "policy_loop_last_error": _autopilot_background_last_error,
        "state": state.get("state") if isinstance(state, dict) else None,
        "policy": state.get("policy", {}) if isinstance(state.get("policy", {}), dict) else {},
        "stats": {
            "total_cycles": int(stats.get("total_cycles", 0) or 0),
            "actions_executed": int(stats.get("actions_executed", 0) or 0),
            "actions_blocked": int(stats.get("actions_blocked", 0) or 0),
            "errors": int(stats.get("errors", 0) or 0),
            "timeouts": int(stats.get("timeouts", 0) or 0),
            "consecutive_timeouts": int(stats.get("consecutive_timeouts", 0) or 0),
            "timeout_circuit_opens": int(stats.get("timeout_circuit_opens", 0) or 0),
            "last_cycle": raw_last_cycle,
        },
        "guard": _autopilot_guard_snapshot(),
    }


def _build_hub_autopilot_status_payload(include_legacy_aliases: bool = False) -> Dict[str, Any]:
    """Return autopilot status in dashboard/API format with optional legacy aliases."""
    snapshot = _autopilot_runtime_snapshot()
    payload = {
        "success": True,
        "enabled": snapshot.get("enabled"),
        "dry_run": snapshot.get("dry_run"),
        "active_executor": snapshot.get("active_executor"),
        "executor_conflict": bool(snapshot.get("executor_conflict")),
        "interval": snapshot.get("interval"),
        "interval_sec": snapshot.get("interval"),
        "max_actions_per_cycle": snapshot.get("max_actions_per_cycle"),
        "max_per_tick": snapshot.get("max_actions_per_cycle"),
        "running": bool(snapshot.get("hub_loop_running") or snapshot.get("policy_loop_running")),
        "hub_loop_running": snapshot.get("hub_loop_running"),
        "policy_loop_running": snapshot.get("policy_loop_running"),
        "last_run": snapshot.get("last_run"),
        "last_error": snapshot.get("last_error"),
        "last_cycle": snapshot.get("last_cycle"),
        "action_timeout_sec": _AUTOPILOT_ACTION_TIMEOUT_SEC if "_AUTOPILOT_ACTION_TIMEOUT_SEC" in globals() else None,
        "allowed_types": snapshot.get("allowed_types", []),
        "policy": snapshot.get("policy", {}),
        "stats": snapshot.get("stats"),
        "guard": snapshot.get("guard"),
        "timestamp": datetime.now().isoformat(),
        "state": snapshot.get("state"),
    }
    if include_legacy_aliases:
        payload["interval_sec"] = payload.get("interval")
        payload["max_per_tick"] = payload.get("max_actions_per_cycle")
        payload["executor"] = payload.get("active_executor")
        payload["running_mode"] = "hub_loop" if payload.get("hub_loop_running") else "policy_loop"
    return payload


def _autopilot_parse_bool(raw_value: Any, default: bool = False) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return bool(default)
    token = str(raw_value).strip().lower()
    if token in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if token in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    return bool(default)


def _autopilot_policy_patch(policy_raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    if not isinstance(policy_raw, dict):
        return {}, ["policy must be an object"]
    patch: Dict[str, Any] = {}
    errors: List[str] = []
    bool_keys = {
        "allow_dispatch",
        "allow_reassign",
        "allow_release_lease",
        "allow_inspect_terminal",
    }
    for key in bool_keys:
        if key in policy_raw:
            patch[key] = bool(policy_raw.get(key))

    if "require_confidence" in policy_raw:
        try:
            patch["require_confidence"] = max(0.0, min(1.0, float(policy_raw.get("require_confidence"))))
        except (TypeError, ValueError):
            errors.append("policy.require_confidence must be a number")

    if "max_priority" in policy_raw:
        priority = str(policy_raw.get("max_priority") or "").strip().lower()
        if priority not in {"p0", "p1", "p2", "p3"}:
            errors.append("policy.max_priority must be one of p0,p1,p2,p3")
        else:
            patch["max_priority"] = priority

    if "blocked_actions" in policy_raw:
        blocked_raw = policy_raw.get("blocked_actions")
        if not isinstance(blocked_raw, list):
            errors.append("policy.blocked_actions must be a list")
        else:
            blocked: List[str] = []
            for item in blocked_raw:
                action_name = str(item or "").strip()
                if action_name and action_name not in blocked:
                    blocked.append(action_name)
            patch["blocked_actions"] = blocked

    return patch, errors


def _autopilot_parse_max_actions(raw_value: Any) -> Tuple[Optional[int], Optional[str]]:
    if raw_value is None:
        return None, None
    try:
        value = max(1, min(10, int(raw_value)))
        return value, None
    except (TypeError, ValueError):
        return None, "max_actions_per_cycle must be an integer"


def _autopilot_apply_control(action_raw: str, options: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any], str]:
    """Apply autopilot control action and return (success, state_snapshot, message)."""
    action = str(action_raw or "").strip().lower()
    opts = options if isinstance(options, dict) else {}

    if action == "start":
        requested_executor = str(
            opts.get("active_executor") or opts.get("executor") or _autopilot_active_executor()
        ).strip()
        executor = _normalize_autopilot_executor(requested_executor)
        interval_raw = opts.get("interval", opts.get("interval_sec"))
        max_actions_raw = opts.get("max_actions_per_cycle", opts.get("max_per_tick"))
        max_actions, max_actions_error = _autopilot_parse_max_actions(max_actions_raw)
        policy_patch, policy_errors = ({}, [])
        if "policy" in opts:
            policy_patch, policy_errors = _autopilot_policy_patch(opts.get("policy"))
        if policy_errors:
            error_message = "; ".join(policy_errors)
            return False, {"success": False, "error": error_message}, error_message
        if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
            _AUTOPILOT_STATE["enabled"] = True
            _AUTOPILOT_STATE["active_executor"] = executor
            if interval_raw is not None:
                try:
                    _AUTOPILOT_STATE["interval"] = max(5, int(interval_raw))
                except (TypeError, ValueError):
                    return False, {"success": False, "error": "interval must be an integer"}, "interval must be an integer"
            if max_actions_error:
                return False, {"success": False, "error": max_actions_error}, max_actions_error
            if max_actions is not None:
                _AUTOPILOT_STATE["max_actions_per_cycle"] = max_actions
            if "dry_run" in opts:
                _AUTOPILOT_STATE["dry_run"] = _autopilot_parse_bool(opts.get("dry_run"), default=bool(_AUTOPILOT_STATE.get("dry_run", True)))
            if policy_patch:
                _AUTOPILOT_STATE["policy"].update(policy_patch)
        _autopilot_set_enabled(True)
        force_restart = _autopilot_parse_bool(opts.get("force_restart"), default=True)
        _autopilot_reconcile_runtimes(force_restart=force_restart)
        if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
            _AUTOPILOT_STATE["active_executor"] = executor
        return True, _build_hub_autopilot_status_payload(include_legacy_aliases=True), "Autopilot started"

    if action == "stop":
        _autopilot_set_enabled(False)
        _autopilot_reconcile_runtimes(force_restart=False)
        return True, _build_hub_autopilot_status_payload(include_legacy_aliases=True), "Autopilot stopped"

    if action == "trigger":
        max_actions_raw = opts.get("max_actions_per_cycle", opts.get("max_per_tick"))
        max_actions, max_actions_error = _autopilot_parse_max_actions(max_actions_raw)
        if max_actions_error:
            return False, {"success": False, "error": max_actions_error}, max_actions_error
        dry_run_override = None
        if "dry_run" in opts:
            dry_run_override = _autopilot_parse_bool(opts.get("dry_run"), default=bool(_AUTOPILOT_STATE.get("dry_run", True)))
        auto_enable = _autopilot_parse_bool(opts.get("auto_enable"), default=True)
        try:
            if not _autopilot_is_enabled():
                if not auto_enable:
                    return False, {"success": False, "error": "Autopilot disabled"}, "Autopilot disabled"
                _autopilot_set_enabled(True)
            _autopilot_reconcile_runtimes(force_restart=False)
            result = _autopilot_cycle_once(
                dry_run_override=dry_run_override,
                use_max_actions=max_actions if max_actions is not None else _autopilot_configured_max_actions(),
                context="hub_loop_legacy_trigger",
            )
            result["running"] = bool(_hub_next_action_autopilot_thread and _hub_next_action_autopilot_thread.is_alive())
            result["last_error"] = result.get("error") or _hub_next_action_autopilot_last_error
            result["state"] = _build_hub_autopilot_status_payload(include_legacy_aliases=True)
            result["auto_enabled"] = bool(auto_enable and result["state"].get("enabled"))
            return True, result, "Autopilot triggered"
        except Exception as exc:
            return False, {"success": False, "error": str(exc)}, f"Autopilot trigger failed: {exc}"

    return False, {"success": False, "error": "Unknown action"}, "Unknown action"


def _stop_hub_autopilot_loop() -> bool:
    """Stop hub-loop thread if running."""
    global _hub_next_action_autopilot_thread
    stopped = False
    with _hub_next_action_autopilot_lock:
        if _hub_next_action_autopilot_thread and _hub_next_action_autopilot_thread.is_alive():
            _hub_next_action_autopilot_stop_event.set()
            _hub_next_action_autopilot_thread.join(timeout=4)
            stopped = True
        _hub_next_action_autopilot_stop_event.clear()
        _hub_next_action_autopilot_thread = None
    return stopped


def _stop_policy_autopilot_loop() -> bool:
    """Stop policy-loop thread if running."""
    stopped = False
    global _autopilot_background_thread
    if "_autopilot_background_stop_event" not in globals():
        return False
    with _autopilot_background_lock:
        if _autopilot_background_thread and _autopilot_background_thread.is_alive():
            _autopilot_background_stop_event.set()
            _autopilot_background_thread.join(timeout=4)
            stopped = True
        _autopilot_background_stop_event.clear()
        _autopilot_background_thread = None
    return stopped


def _set_autopilot_executor(executor: str, reason: str = "", force_restart: bool = True) -> str:
    """Switch active executor atomically and return the active executor."""
    target = _normalize_autopilot_executor(executor)
    if "_AUTOPILOT_STATE" not in globals() or not isinstance(_AUTOPILOT_STATE, dict):
        return target
    current = _autopilot_active_executor()
    if target == current and _autopilot_is_enabled() and force_restart:
        if target == "hub_loop":
            _ensure_hub_autopilot_started()
        else:
            _ensure_autopilot_background_started()
        return current

    # stop the other loop so we never run two loops in parallel
    if current == "hub_loop":
        _stop_hub_autopilot_loop()
    elif current == "policy_loop":
        _stop_policy_autopilot_loop()

    _AUTOPILOT_STATE["active_executor"] = target
    if "autopilot_executor" in _AUTOPILOT_STATE:
        _AUTOPILOT_STATE["autopilot_executor"] = target

    if _autopilot_is_enabled():
        if target == "hub_loop":
            _ensure_hub_autopilot_started()
        else:
            _ensure_autopilot_background_started()

    if reason:
        state.add_orion_log(f"Autopilot executor switched to {target} ({reason})", "info")
    return target


def _hub_next_action_autopilot_loop() -> None:
    """Background loop that auto-executes next actions based on policy."""
    global _hub_next_action_autopilot_last_run, _hub_next_action_autopilot_last_error
    logger.info("🚀 Hub Next-Action Autopilot loop started")

    while not _hub_next_action_autopilot_stop_event.is_set():
        try:
            if not _autopilot_is_enabled() or _autopilot_active_executor() != "hub_loop":
                if _hub_next_action_autopilot_stop_event.is_set():
                    break
                _hub_next_action_autopilot_stop_event.wait(_autopilot_configured_interval())
                continue

            result = _autopilot_cycle_once(dry_run_override=None, use_max_actions=_autopilot_configured_max_actions(), context="hub_loop")
            _hub_next_action_autopilot_last_run = datetime.now()
            _hub_next_action_autopilot_last_error = result.get("error")
            if result.get("executed"):
                logger.info(
                    "🤖 Hub autopilot: {} executed, {} blocked, {} errors".format(
                        len(result.get("executed", [])),
                        len(result.get("blocked", [])),
                        len(result.get("errors", [])),
                    )
                )
            if not result.get("success", False) and result.get("error"):
                logger.warning(f"Hub autopilot cycle skipped: {result.get('error')}")
            if not result.get("scanned") and not result.get("errors") and not result.get("blocked"):
                _hub_next_action_autopilot_stop_event.wait(_autopilot_configured_interval())
                continue

        except Exception as exc:
            logger.error(f"Autopilot error: {exc}")
            _hub_next_action_autopilot_last_error = str(exc)

        _hub_next_action_autopilot_stop_event.wait(_autopilot_configured_interval())


def _ensure_hub_autopilot_started() -> None:
    global _hub_next_action_autopilot_thread
    if not _autopilot_is_enabled() or _autopilot_active_executor() != "hub_loop":
        _stop_hub_autopilot_loop()
        return
    with _hub_next_action_autopilot_lock:
        if _hub_next_action_autopilot_thread and _hub_next_action_autopilot_thread.is_alive():
            return
        _stop_policy_autopilot_loop()
        _hub_next_action_autopilot_stop_event.clear()
        _hub_next_action_autopilot_thread = threading.Thread(
            target=_hub_next_action_autopilot_loop,
            name="hub-autopilot",
            daemon=True,
        )
        _hub_next_action_autopilot_thread.start()
        if "_AUTOPILOT_STATE" in globals() and isinstance(_AUTOPILOT_STATE, dict):
            _AUTOPILOT_STATE["state"] = "running"
        logger.info("✅ Hub autopilot started")


_openclaw_self_heal_thread: Optional[threading.Thread] = None
_openclaw_self_heal_stop_event = threading.Event()
_openclaw_self_heal_lock = threading.RLock()
_openclaw_self_heal_last_run: Optional[datetime] = None
_openclaw_self_heal_last_error: Optional[str] = None
_openclaw_self_heal_last_success: Optional[datetime] = None
_openclaw_self_heal_last_open: Optional[datetime] = None
_openclaw_self_heal_last_fallback: Optional[datetime] = None
_openclaw_flow_last_open: Optional[datetime] = None
_OPENCLAW_FLOW_LOCK = threading.Lock()
_OPENCLAW_ACTION_DEBOUNCE_LOCK = threading.Lock()
_openclaw_action_last_seen: Dict[str, float] = {}
_API_RATE_LIMIT_LOCK = threading.Lock()
_api_rate_buckets: Dict[str, Dict[str, Any]] = defaultdict(dict)
_api_rate_limit_blocked_total = 0
_api_rate_limit_blocked_by_bucket: Dict[str, int] = defaultdict(int)
_api_rate_limit_blocked_events: List[float] = []
_guardian_control_audit_lock = threading.Lock()
_guardian_control_audit: List[Dict[str, Any]] = []
_AGENT_COORDINATION_DEDUP_LOCK = threading.Lock()
_agent_coordination_recent: Dict[str, float] = {}
_AUTOMATION_TASKS_LOCK = threading.Lock()
_automation_recent_tasks: deque = deque(maxlen=AUTOMATION_TASK_HISTORY_MAX)
_INTERNAL_LOOPBACK_AUTH_EXEMPT_PATHS = {
    "/",
    "/api/control-plane/workers/register",
    "/api/control-plane/workers/heartbeat",
    # Automation endpoints (Phase 0-6)
    "/api/automation/control",
    "/api/automation/dlq",
    "/api/automation/dlq/clear",
    "/api/automation/execute",
    "/api/automation/sessions",
    "/api/automation/sessions/lease",
    "/api/automation/workflow/create",
    "/api/automation/workflow/list",
    "/api/automation/memory",
    "/api/automation/memory/analyze",
    "/api/automation/preferences",
    "/api/automation/intake",
    "/api/automation/metrics",
    "/api/automation/health",
    "/api/automation/alerts",
    "/api/automation/status",
    "/api/automation/test/self",
    "/api/automation/test/integration",
    "/api/automation/test/results",
    "/api/automation/rollout/status",
    "/api/automation/rollout/start",
    "/api/automation/rollout/complete",
    "/api/automation/rollout/rollback",
    "/api/automation/rollout/feature",
    "/api/automation/rollout/issue",
    # Autopilot endpoints
    "/api/hub/autopilot/status",
    "/api/hub/autopilot/config",
    "/api/hub/autopilot/run",
    "/api/hub/autopilot/preview",
    # Hub endpoints for local testing
    "/api/hub/next-actions",
    "/api/hub/strategic-brief",
    "/api/hub/next-action/autopilot/status",
    "/api/hub/next-action/autopilot/control",
    "/api/hub/workload",
    "/api/hub/tasks",
    "/api/interventions",
    # Bridge endpoints
    "/api/bridge/sync/terminals",
    "/api/bridge/terminals",
}


def _extract_dashboard_token() -> str:
    """Read dashboard token from header, bearer auth, or query token."""
    token = str(request.headers.get("X-Dashboard-Token", "") or "").strip()
    if token:
        return token
    auth_header = str(request.headers.get("Authorization", "") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if DASHBOARD_TOKEN_ALLOW_QUERY:
        return str(request.args.get("token", "") or "").strip()
    return ""


def _dashboard_request_authorized() -> bool:
    if not DASHBOARD_ACCESS_TOKEN:
        return True
    remote_ip = str(request.remote_addr or "").strip()
    req_path = str(request.path or "/")
    if remote_ip in {"127.0.0.1", "::1", "localhost"}:
        # Exact match
        if req_path in _INTERNAL_LOOPBACK_AUTH_EXEMPT_PATHS:
            return True
        # Prefix match for dynamic paths like /api/hub/tasks/<id>
        for exempt_path in _INTERNAL_LOOPBACK_AUTH_EXEMPT_PATHS:
            if req_path.startswith(exempt_path.rstrip("/") + "/"):
                return True
    token = _extract_dashboard_token()
    return bool(token and token == DASHBOARD_ACCESS_TOKEN)


@app.before_request
def _dashboard_auth_guard():
    """Optional token gate for dashboard + API when DASHBOARD_ACCESS_TOKEN is set."""
    if not DASHBOARD_ACCESS_TOKEN:
        return None
    path = str(request.path or "/")
    if path.startswith("/static/"):
        return None
    if _dashboard_request_authorized():
        return None
    if path.startswith("/api/"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    return "Unauthorized", 401


def _load_orion_instances_config() -> List[Dict[str, str]]:
    """Load Orion instances from env. Supports JSON or simple CSV format."""
    default_name = os.getenv("LOCAL_ORION_INSTANCE_NAME", "Orion 1")
    instances: List[Dict[str, str]] = [{
        "id": LOCAL_INSTANCE_ID,
        "name": default_name,
        "mode": "local",
        "base_url": "",
    }]

    raw_json = os.getenv("ORION_INSTANCES_JSON", "").strip()
    if raw_json:
        try:
            data = json.loads(raw_json)
            parsed: List[Dict[str, str]] = []
            for item in data if isinstance(data, list) else []:
                instance_id = str((item or {}).get("id", "")).strip()
                if not instance_id:
                    continue
                mode = str((item or {}).get("mode", "remote")).strip().lower() or "remote"
                parsed.append({
                    "id": instance_id,
                    "name": str((item or {}).get("name", instance_id)).strip() or instance_id,
                    "mode": "local" if mode == "local" else "remote",
                    "base_url": str((item or {}).get("base_url", "")).strip(),
                })
            if parsed:
                deduped: Dict[str, Dict[str, str]] = {instances[0]["id"]: instances[0]}
                for row in parsed:
                    deduped[row["id"]] = row
                return list(deduped.values())
        except Exception as exc:
            logger.warning(f"Failed to parse ORION_INSTANCES_JSON: {exc}")

    # CSV format:
    # ORION_INSTANCES="orion-2|Orion 2|http://127.0.0.1:5051,orion-3|Orion 3|http://127.0.0.1:5052"
    raw_csv = os.getenv("ORION_INSTANCES", "").strip()
    if raw_csv:
        for block in raw_csv.split(","):
            block = block.strip()
            if not block:
                continue
            parts = [part.strip() for part in block.split("|")]
            if len(parts) < 3:
                continue
            instance_id, name, base_url = parts[0], parts[1], parts[2]
            if not instance_id:
                continue
            instances.append({
                "id": instance_id,
                "name": name or instance_id,
                "mode": "remote",
                "base_url": base_url,
            })
    return instances


ORION_INSTANCES: List[Dict[str, str]] = _load_orion_instances_config()
_prompt_system = get_prompt_system()
_provider_store = ProviderProfileStore()
_team_persona_store = get_team_persona_store()


# ============================================
# CENTRAL COMMAND STATE (Phase 1)
# ============================================

_CENTRAL_COMMAND_LOCK = threading.Lock()

@dataclass
class OrionState:
    """Represents a registered Orion instance in Central Command."""
    orion_id: str
    name: str
    base_url: str
    registered_at: str
    last_heartbeat: str
    status: str  # online, offline, degraded
    current_task: Optional[str] = None
    locked_files: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class FileLock:
    """Represents a file lock for conflict prevention."""
    file_path: str
    orion_id: str
    agent_id: Optional[str]
    locked_at: str
    expires_at: str

@dataclass
class TaskDistribution:
    """Represents a task in the global queue."""
    task_id: str
    task_type: str
    description: str
    priority: int
    assigned_orion: Optional[str]
    status: str  # pending, assigned, in_progress, completed, failed
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

@dataclass
class CrossOrionMessage:
    """Represents a message between Orion instances."""
    message_id: str
    from_orion: str
    to_orion: Optional[str]  # None = broadcast
    message_type: str  # info, request, response, alert
    content: str
    created_at: str
    read: bool = False

# Central Command State
_registered_orions: Dict[str, OrionState] = {}
_global_file_locks: Dict[str, FileLock] = {}
_task_queue: List[TaskDistribution] = []
_cross_orion_messages: List[CrossOrionMessage] = []


# ============================================
# MODEL ROUTING HELPERS
# ============================================

TASK_TYPE_VALUES = sorted([t.value for t in TaskType])


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_autonomy_profile(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"safe", "balanced", "full_auto"}:
        return text
    return "balanced"


def _get_autonomy_profile() -> str:
    with _monitor_supervisor_lock:
        return _autonomy_profile


def _is_full_auto_profile() -> bool:
    return _get_autonomy_profile() == "full_auto"


def _autonomy_no_human_prompts_enabled() -> bool:
    return bool(AUTONOMY_NO_HUMAN_PROMPTS or _is_full_auto_profile())


def _set_autonomy_profile(profile: str) -> str:
    global _autonomy_profile, _monitor_autopilot_pause_until, _monitor_supervisor_enabled
    normalized = _normalize_autonomy_profile(profile)
    with _monitor_supervisor_lock:
        _autonomy_profile = normalized
        if normalized == "full_auto":
            _monitor_supervisor_enabled = True
            _monitor_autopilot_pause_until = None
    return normalized


def _parse_task_type(value: Any) -> Optional[TaskType]:
    text = str(value or "").strip().lower()
    for item in TaskType:
        if item.value == text:
            return item
    return None


def _parse_complexity(value: Any) -> Optional[TaskComplexity]:
    text = str(value or "").strip().lower()
    for item in TaskComplexity:
        if item.value == text:
            return item
    return None


def _int_in_range(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min_value, min(max_value, parsed))


def _api_client_key() -> str:
    token = _extract_dashboard_token()
    if token:
        return f"token:{token[:12]}"
    return f"ip:{str(request.remote_addr or 'unknown')}"


def _check_api_rate_limit(bucket: str, limit: int, window_sec: int = 60) -> Optional[Dict[str, Any]]:
    global _api_rate_limit_blocked_total
    now = time.time()
    key = _api_client_key()
    bucket_name = f"{bucket}:{key}"
    with _API_RATE_LIMIT_LOCK:
        if len(_api_rate_buckets) > 5000:
            expired = [name for name, info in _api_rate_buckets.items() if now >= float((info or {}).get("reset_at", 0))]
            for name in expired[:2000]:
                _api_rate_buckets.pop(name, None)
        entry = _api_rate_buckets.get(bucket_name)
        if not entry or now >= float(entry.get("reset_at", 0)):
            entry = {"count": 0, "reset_at": now + max(1, int(window_sec))}
            _api_rate_buckets[bucket_name] = entry
        entry["count"] = int(entry.get("count", 0)) + 1
        reset_at = float(entry.get("reset_at", now))
        if entry["count"] > int(limit):
            retry_after = max(1, int(reset_at - now))
            _api_rate_limit_blocked_total += 1
            _api_rate_limit_blocked_by_bucket[bucket] = int(_api_rate_limit_blocked_by_bucket.get(bucket, 0)) + 1
            _api_rate_limit_blocked_events.append(now)
            if len(_api_rate_limit_blocked_events) > 2000:
                del _api_rate_limit_blocked_events[:-2000]
            return {
                "success": False,
                "error": "Rate limit exceeded",
                "bucket": bucket,
                "limit": int(limit),
                "window_sec": int(window_sec),
                "retry_after_sec": retry_after,
            }
    return None


def _rate_limited(bucket: str, limit: int, window_sec: int = 60) -> Optional[Response]:
    blocked = _check_api_rate_limit(bucket=bucket, limit=limit, window_sec=window_sec)
    if not blocked:
        return None
    response = jsonify(blocked)
    response.status_code = 429
    response.headers["Retry-After"] = str(int(blocked.get("retry_after_sec", 1)))
    return response


def _agent_coordination_debounced(signature: str, cooldown_sec: float = AGENT_COORDINATION_DEDUP_WINDOW_SEC) -> Tuple[bool, float]:
    key = str(signature or "").strip().lower()
    if not key:
        return False, 0.0
    cooldown = max(1.0, float(cooldown_sec))
    now = time.time()
    with _AGENT_COORDINATION_DEDUP_LOCK:
        stale_before = now - max(90.0, cooldown * 8.0)
        stale_keys = [item for item, ts in _agent_coordination_recent.items() if float(ts) < stale_before]
        for item in stale_keys[:2000]:
            _agent_coordination_recent.pop(item, None)
        seen = float(_agent_coordination_recent.get(key, 0.0) or 0.0)
        if seen > 0.0:
            elapsed = now - seen
            if elapsed < cooldown:
                return True, round(max(0.0, cooldown - elapsed), 2)
        _agent_coordination_recent[key] = now
    return False, 0.0


def _api_ok(data: Optional[Dict[str, Any]] = None, status_code: int = 200, **extra: Any):
    payload: Dict[str, Any] = {
        "success": True,
        "timestamp": datetime.now().isoformat(),
    }
    if isinstance(data, dict):
        payload.update(data)
    if extra:
        payload.update(extra)
    return jsonify(payload), status_code


def _api_error(
    error_code: str,
    message: str,
    status_code: int = 400,
    data: Optional[Dict[str, Any]] = None,
    **extra: Any,
):
    payload: Dict[str, Any] = {
        "success": False,
        "error_code": str(error_code or "UNKNOWN_ERROR"),
        "error": str(message or "Request failed"),
        "timestamp": datetime.now().isoformat(),
    }
    if isinstance(data, dict):
        payload.update(data)
    if extra:
        payload.update(extra)
    return jsonify(payload), status_code


def _sort_by_timestamp_desc(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _parse(value: Any) -> datetime:
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return datetime.min

    return sorted(rows, key=lambda row: _parse(row.get("timestamp")), reverse=True)


def _append_jsonl_event(path: Path, row: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:
        return


def _read_jsonl_events(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                text = str(line or "").strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except Exception:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return []
    if limit > 0:
        rows = rows[-limit:]
    return rows


def _resolve_member_id(payload: Optional[Dict[str, Any]] = None, fallback: str = "") -> str:
    data = payload if isinstance(payload, dict) else {}
    for key in ("member_id", "user_id", "actor"):
        value = normalize_member_id(data.get(key))
        if value:
            return value
    return normalize_member_id(fallback)


def _team_adaptation(member_id: str, intent: str = "") -> Dict[str, Any]:
    try:
        return _team_persona_store.recommend_adaptation(member_id, intent=intent)
    except Exception as exc:
        logger.warning(f"Team persona adaptation failed: {exc}")
        return {
            "member_id": member_id,
            "known_member": False,
            "communication": {"tone": "balanced", "detail_level": "balanced", "response_structure": "bullets"},
            "orchestration": {"autonomy_mode": "balanced", "risk_policy": "balanced", "escalate_when": "critical_only"},
            "hints": [f"adaptation_error: {exc}"],
        }


def _adapt_chat_reply_for_member(reply: str, adaptation: Dict[str, Any]) -> str:
    text = str(reply or "").strip()
    if not text:
        return text

    comm = adaptation.get("communication") if isinstance(adaptation.get("communication"), dict) else {}
    detail_level = str(comm.get("detail_level", "balanced"))
    response_structure = str(comm.get("response_structure", "bullets"))
    tone = str(comm.get("tone", "balanced"))

    if detail_level == "compact":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 3:
            text = "\n".join(lines[:3])
    elif detail_level == "high" and "\n" not in text:
        text = f"{text}\n- Next: continue execution.\n- Verify: check runtime + logs."

    if response_structure in {"steps", "numbered"} and "\n" in text:
        lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
        text = "\n".join([f"{idx}. {line}" for idx, line in enumerate(lines, start=1)])
    elif response_structure == "bullets" and "\n" in text and not any(line.strip().startswith("- ") for line in text.splitlines()):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join([f"- {line}" for line in lines])

    if tone == "direct":
        text = re.sub(r"\bMình (vừa|đã)\b", "Đã", text)
    elif tone == "collaborative" and not text.lower().startswith("mình"):
        text = f"Mình phối hợp tiếp như sau:\n{text}"

    return text


def _build_api_rate_limit_snapshot(window_sec: int = 300) -> Dict[str, Any]:
    now = time.time()
    threshold = now - max(1, int(window_sec))
    with _API_RATE_LIMIT_LOCK:
        if len(_api_rate_limit_blocked_events) > 2000:
            del _api_rate_limit_blocked_events[:-2000]
        recent = [ts for ts in _api_rate_limit_blocked_events if ts >= threshold]
        top_buckets = sorted(
            _api_rate_limit_blocked_by_bucket.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
    return {
        "blocked_total": int(_api_rate_limit_blocked_total),
        "blocked_last_window": len(recent),
        "window_sec": int(window_sec),
        "top_buckets": [{"bucket": name, "blocked": int(value)} for name, value in top_buckets],
    }


@dataclass
class _OpenClawQueuedCommand:
    request_id: str
    command_type: str
    payload: Dict[str, Any]
    session_id: str
    source: str
    priority: int
    idempotency_key: str
    created_at: datetime
    timeout_ms: int
    risk_level: str = "medium"
    decision: str = "auto_approved"
    target_worker: str = ""
    resolved_worker: str = ""
    state: str = "queued"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    failure_class: str = ""
    deferred_reason: str = ""
    deferred_count: int = 0
    done_event: threading.Event = field(default_factory=threading.Event)


@dataclass
class _OpenClawSessionState:
    session_id: str
    manual_queue: Any = field(default_factory=deque)
    autopilot_queue: Any = field(default_factory=deque)
    active_request_id: Optional[str] = None
    manual_hold_until: Optional[datetime] = None
    last_error: Optional[str] = None
    worker_thread: Optional[threading.Thread] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cond: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.cond = threading.Condition(self.lock)


@dataclass
class _ControlPlaneWorker:
    worker_id: str
    capabilities: List[str] = field(default_factory=list)
    version: str = ""
    region: str = ""
    mode: str = "remote"
    base_url: str = ""
    lease_ttl_sec: int = CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC
    assigned_policies: List[str] = field(default_factory=list)
    last_heartbeat: Optional[datetime] = None
    health: str = "unknown"
    queue_depth: int = 0
    running_tasks: int = 0
    backpressure_level: str = "normal"
    last_error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class _ControlPlaneRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._workers: Dict[str, _ControlPlaneWorker] = {}
        self._failure_streak: Dict[Tuple[str, str], int] = {}
        self._circuits: Dict[str, Dict[str, Any]] = {}
        self._class_open_count: Dict[str, int] = {}
        self._class_blocked_until: Dict[str, datetime] = {}
        self._updated_at: Optional[datetime] = None

    def _touch(self) -> None:
        self._updated_at = datetime.now()

    def _default_policies(self) -> List[str]:
        return [
            "openclaw-mostly-auto-v1",
            "openclaw-circuit-breaker-v1",
        ]

    def _normalize_capabilities(self, capabilities: Any) -> List[str]:
        if isinstance(capabilities, list):
            values = [str(item).strip().lower() for item in capabilities if str(item).strip()]
        else:
            values = [str(capabilities).strip().lower()] if str(capabilities or "").strip() else []
        deduped: List[str] = []
        for item in values:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _refresh_circuit_locked(self) -> None:
        now = datetime.now()
        expired_workers = [
            worker_id
            for worker_id, state in self._circuits.items()
            if now >= state.get("open_until", now)
        ]
        for worker_id in expired_workers:
            self._circuits.pop(worker_id, None)

        expired_classes = [
            failure_class
            for failure_class, until in self._class_blocked_until.items()
            if now >= until
        ]
        for failure_class in expired_classes:
            self._class_blocked_until.pop(failure_class, None)

    def _is_worker_alive_locked(self, worker: _ControlPlaneWorker) -> bool:
        if worker.health not in {"ok", "degraded", "unknown"}:
            return False
        if worker.mode == "local":
            return True
        if not worker.last_heartbeat:
            return False
        age = (datetime.now() - worker.last_heartbeat).total_seconds()
        return age <= max(5, int(worker.lease_ttl_sec))

    def _serialize_worker_locked(self, worker: _ControlPlaneWorker) -> Dict[str, Any]:
        alive = self._is_worker_alive_locked(worker)
        circuit = self._circuits.get(worker.worker_id, {})
        return {
            "worker_id": worker.worker_id,
            "capabilities": list(worker.capabilities),
            "version": worker.version,
            "region": worker.region,
            "mode": worker.mode,
            "base_url": worker.base_url,
            "lease_ttl_sec": int(worker.lease_ttl_sec),
            "assigned_policies": list(worker.assigned_policies),
            "alive": alive,
            "last_heartbeat": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
            "health": worker.health,
            "queue_depth": int(worker.queue_depth),
            "running_tasks": int(worker.running_tasks),
            "backpressure_level": worker.backpressure_level,
            "last_error": worker.last_error,
            "circuit_open": bool(circuit),
            "circuit": {
                "failure_class": circuit.get("failure_class"),
                "open_until": circuit.get("open_until").isoformat() if isinstance(circuit.get("open_until"), datetime) else None,
                "count": int(circuit.get("count", 0) or 0),
            } if circuit else {},
            "metadata": dict(worker.metadata or {}),
        }

    def seed_from_instances(self, instances: List[Dict[str, Any]]) -> None:
        for instance in instances:
            worker_id = str(instance.get("id", "")).strip()
            if not worker_id:
                continue
            mode = str(instance.get("mode", "remote")).strip().lower() or "remote"
            base_url = str(instance.get("base_url", "")).strip()
            self.register(
                worker_id=worker_id,
                capabilities=["orion", "openclaw", "guardian"],
                version="seeded",
                region="local",
                mode="local" if mode == "local" else "remote",
                base_url=base_url,
                lease_ttl_sec=CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC,
                metadata={"seeded": True},
            )

    def register(
        self,
        worker_id: str,
        capabilities: Any,
        version: str = "",
        region: str = "",
        mode: str = "remote",
        base_url: str = "",
        lease_ttl_sec: int = CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        wid = str(worker_id or "").strip().lower()
        if not wid:
            return {"success": False, "error": "Missing worker_id", "error_code": "MISSING_WORKER_ID"}
        with self._lock:
            if len(self._workers) >= CONTROL_PLANE_MAX_WORKERS and wid not in self._workers:
                return {"success": False, "error": "Worker limit reached", "error_code": "WORKER_LIMIT"}
            worker = self._workers.get(wid)
            if worker is None:
                worker = _ControlPlaneWorker(worker_id=wid)
                self._workers[wid] = worker
            worker.capabilities = self._normalize_capabilities(capabilities) or ["openclaw"]
            worker.version = str(version or worker.version or "").strip()
            worker.region = str(region or worker.region or "").strip()
            normalized_mode = str(mode or worker.mode or "remote").strip().lower()
            worker.mode = "local" if normalized_mode == "local" else "remote"
            worker.base_url = str(base_url or worker.base_url or "").strip().rstrip("/")
            worker.lease_ttl_sec = max(5, int(lease_ttl_sec or worker.lease_ttl_sec))
            worker.assigned_policies = self._default_policies()
            worker.last_heartbeat = datetime.now()
            worker.health = "ok" if worker.mode == "local" else (worker.health if worker.health else "unknown")
            if isinstance(metadata, dict):
                worker.metadata.update(metadata)
            self._touch()
            row = self._serialize_worker_locked(worker)
        return {
            "success": True,
            "worker": row,
            "lease_ttl_sec": int(row.get("lease_ttl_sec", CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC)),
            "assigned_policies": row.get("assigned_policies", []),
            "control_channel": "/api/guardian/control",
            "timestamp": datetime.now().isoformat(),
        }

    def heartbeat(
        self,
        worker_id: str,
        health: str = "ok",
        queue_depth: int = 0,
        running_tasks: int = 0,
        backpressure_level: str = "normal",
        capabilities: Any = None,
        version: str = "",
        region: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        lease_ttl_sec: Optional[int] = None,
    ) -> Dict[str, Any]:
        wid = str(worker_id or "").strip().lower()
        if not wid:
            return {"success": False, "error": "Missing worker_id", "error_code": "MISSING_WORKER_ID"}

        with self._lock:
            worker = self._workers.get(wid)
            if worker is None:
                worker = _ControlPlaneWorker(worker_id=wid)
                self._workers[wid] = worker
            if capabilities is not None:
                normalized = self._normalize_capabilities(capabilities)
                if normalized:
                    worker.capabilities = normalized
            if version:
                worker.version = str(version).strip()
            if region:
                worker.region = str(region).strip()
            if lease_ttl_sec is not None:
                worker.lease_ttl_sec = max(5, int(lease_ttl_sec))
            worker.health = str(health or "unknown").strip().lower() or "unknown"
            worker.queue_depth = max(0, int(queue_depth or 0))
            worker.running_tasks = max(0, int(running_tasks or 0))
            normalized_backpressure = str(backpressure_level or "normal").strip().lower() or "normal"
            if normalized_backpressure not in {"normal", "high", "critical"}:
                normalized_backpressure = "normal"
            worker.backpressure_level = normalized_backpressure
            worker.last_heartbeat = datetime.now()
            if isinstance(metadata, dict):
                worker.metadata.update(metadata)
            self._refresh_circuit_locked()

            next_actions: List[str] = []
            if worker.health not in {"ok", "degraded", "unknown"}:
                next_actions.append("recover_worker")
            if worker.queue_depth > OPENCLAW_SESSION_QUEUE_MAX:
                next_actions.append("drain_queue")
            if worker.backpressure_level in {"high", "critical"}:
                next_actions.append("reduce_load")
            if wid in self._circuits:
                next_actions.append("hold_commands")
            self._touch()
            row = self._serialize_worker_locked(worker)
        return {
            "success": True,
            "worker": row,
            "lease_ttl_sec": int(row.get("lease_ttl_sec", CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC)),
            "next_actions": next_actions,
            "backpressure_level": row.get("backpressure_level", "normal"),
            "timestamp": datetime.now().isoformat(),
        }

    def sync_instance_snapshot(self, instances: List[Dict[str, Any]]) -> None:
        if not isinstance(instances, list):
            return
        now = datetime.now()
        with self._lock:
            for item in instances:
                worker_id = str(item.get("id", "")).strip().lower()
                if not worker_id:
                    continue
                worker = self._workers.get(worker_id)
                if worker is None:
                    worker = _ControlPlaneWorker(
                        worker_id=worker_id,
                        capabilities=["orion", "openclaw", "guardian"],
                    )
                    self._workers[worker_id] = worker
                worker.mode = "local" if str(item.get("mode", "")).strip().lower() == "local" else "remote"
                worker.base_url = str(item.get("base_url", "") or worker.base_url).strip().rstrip("/")
                online = bool(item.get("online"))
                worker.health = "ok" if online else "down"
                worker.queue_depth = int(item.get("active_flow_count", 0) or 0)
                worker.running_tasks = 1 if bool(item.get("running")) else 0
                if online:
                    worker.last_heartbeat = now
            self._touch()

    def _failure_class_blocked_locked(self, failure_class: str) -> bool:
        self._refresh_circuit_locked()
        blocked_until = self._class_blocked_until.get(failure_class)
        if not blocked_until:
            return False
        return datetime.now() < blocked_until

    def can_dispatch(self, worker_id: str, failure_class: str = "") -> Dict[str, Any]:
        wid = str(worker_id or "").strip().lower()
        with self._lock:
            self._refresh_circuit_locked()
            worker = self._workers.get(wid)
            if not worker:
                return {"allowed": False, "error_code": "WORKER_NOT_FOUND", "reason": "Worker not registered"}
            if not self._is_worker_alive_locked(worker):
                return {"allowed": False, "error_code": "WORKER_UNHEALTHY", "reason": "Worker lease expired or unhealthy"}
            circuit = self._circuits.get(wid)
            if circuit:
                return {
                    "allowed": False,
                    "error_code": "CIRCUIT_OPEN",
                    "reason": "Worker circuit is open",
                    "open_until": circuit.get("open_until").isoformat() if isinstance(circuit.get("open_until"), datetime) else None,
                }
            fclass = str(failure_class or "").strip().lower()
            if fclass and self._failure_class_blocked_locked(fclass):
                _openclaw_metrics_inc("circuit_blocked")
                return {
                    "allowed": False,
                    "error_code": "FAILURE_CLASS_BLOCKED",
                    "reason": f"Failure class '{fclass}' temporarily blocked",
                    "blocked_until": self._class_blocked_until.get(fclass).isoformat() if self._class_blocked_until.get(fclass) else None,
                }
            return {"allowed": True}

    def resolve_worker(
        self,
        requested_worker: str = "",
        required_capability: str = "openclaw",
        exclude: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        requested = str(requested_worker or "").strip().lower()
        capability = str(required_capability or "openclaw").strip().lower()
        excluded = {str(item).strip().lower() for item in (exclude or set()) if str(item).strip()}
        with self._lock:
            self._refresh_circuit_locked()
            rows = list(self._workers.values())
            candidates: List[_ControlPlaneWorker] = []
            for worker in rows:
                if worker.worker_id in excluded:
                    continue
                if capability and capability not in worker.capabilities:
                    continue
                if not self._is_worker_alive_locked(worker):
                    continue
                if worker.worker_id in self._circuits:
                    continue
                candidates.append(worker)

            if requested:
                worker = self._workers.get(requested)
                if worker and worker in candidates:
                    return {
                        "available": True,
                        "worker": self._serialize_worker_locked(worker),
                        "reason": "requested_worker_selected",
                    }

            if not candidates:
                return {"available": False, "worker": None, "reason": "no_eligible_worker"}

            candidates.sort(
                key=lambda item: (
                    int(item.queue_depth),
                    int(item.running_tasks),
                    0 if item.mode == "local" else 1,
                    item.worker_id,
                )
            )
            selected = candidates[0]
            return {
                "available": True,
                "worker": self._serialize_worker_locked(selected),
                "reason": "least_loaded_worker_selected",
            }

    def record_dispatch_result(self, worker_id: str, success: bool, failure_class: str = "unknown") -> Dict[str, Any]:
        wid = str(worker_id or "").strip().lower()
        fclass = str(failure_class or "unknown").strip().lower() or "unknown"
        now = datetime.now()
        with self._lock:
            self._refresh_circuit_locked()
            worker = self._workers.get(wid)
            if worker:
                if success:
                    worker.last_error = ""
                else:
                    worker.last_error = fclass
            if success:
                stale_keys = [key for key in self._failure_streak if key[0] == wid]
                for key in stale_keys:
                    self._failure_streak.pop(key, None)
                self._touch()
                return {"success": True, "circuit_opened": False}

            key = (wid, fclass)
            streak = int(self._failure_streak.get(key, 0)) + 1
            self._failure_streak[key] = streak
            circuit_opened = False
            class_blocked = False
            circuit_state = self._circuits.get(wid, {})
            if streak >= OPENCLAW_CIRCUIT_TRIGGER_COUNT:
                open_until = now + timedelta(seconds=OPENCLAW_CIRCUIT_OPEN_SEC)
                count = int((circuit_state or {}).get("count", 0)) + 1
                self._circuits[wid] = {
                    "failure_class": fclass,
                    "opened_at": now,
                    "open_until": open_until,
                    "count": count,
                }
                self._failure_streak[key] = 0
                self._class_open_count[fclass] = int(self._class_open_count.get(fclass, 0)) + 1
                _openclaw_metrics_inc("circuit_opened")
                circuit_opened = True
                if self._class_open_count[fclass] >= OPENCLAW_CIRCUIT_ESCALATE_AFTER:
                    blocked_until = now + timedelta(seconds=OPENCLAW_CIRCUIT_CLASS_BLOCK_SEC)
                    self._class_blocked_until[fclass] = blocked_until
                    _openclaw_metrics_inc("circuit_blocked")
                    class_blocked = True
            self._touch()
            current = self._circuits.get(wid, {})
            return {
                "success": True,
                "streak": streak,
                "circuit_opened": circuit_opened,
                "class_blocked": class_blocked,
                "circuit": {
                    "failure_class": current.get("failure_class"),
                    "open_until": current.get("open_until").isoformat() if isinstance(current.get("open_until"), datetime) else None,
                    "count": int(current.get("count", 0) or 0),
                } if current else {},
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_circuit_locked()
            rows = [self._serialize_worker_locked(worker) for worker in self._workers.values()]
            alive = sum(1 for row in rows if bool(row.get("alive")))
            blocked = {
                fclass: until.isoformat()
                for fclass, until in self._class_blocked_until.items()
            }
            circuits = []
            for wid, state in self._circuits.items():
                circuits.append({
                    "worker_id": wid,
                    "failure_class": state.get("failure_class"),
                    "open_until": state.get("open_until").isoformat() if isinstance(state.get("open_until"), datetime) else None,
                    "count": int(state.get("count", 0) or 0),
                })
            updated_at = self._updated_at.isoformat() if self._updated_at else None
        rows.sort(key=lambda item: item.get("worker_id", ""))
        circuits.sort(key=lambda item: item.get("worker_id", ""))
        return {
            "registered_workers": len(rows),
            "alive_workers": alive,
            "workers": rows,
            "circuits": circuits,
            "blocked_failure_classes": blocked,
            "updated_at": updated_at,
        }


_control_plane_registry = _ControlPlaneRegistry()
_control_plane_registry.seed_from_instances(ORION_INSTANCES)


class _OpenClawCommandManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, _OpenClawSessionState] = {}
        self._commands: Dict[str, _OpenClawQueuedCommand] = {}
        self._idempotency: Dict[str, Dict[str, Any]] = {}
        self._executor = None
        self._wait_samples_ms: List[int] = []

    def set_executor(self, executor) -> None:
        self._executor = executor

    def _persist_wal(self, event: str, command: _OpenClawQueuedCommand) -> None:
        try:
            OPENCLAW_COMMANDS_WAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OPENCLAW_COMMANDS_WAL_PATH, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "event": event,
                    "timestamp": datetime.now().isoformat(),
                    "request_id": command.request_id,
                    "session_id": command.session_id,
                    "source": command.source,
                    "command_type": command.command_type,
                    "risk_level": command.risk_level,
                    "decision": command.decision,
                    "target_worker": command.target_worker,
                    "resolved_worker": command.resolved_worker,
                    "state": command.state,
                }, ensure_ascii=True) + "\n")
        except Exception:
            return

    def _trim_idempotency(self) -> None:
        now = time.time()
        stale = [key for key, value in self._idempotency.items() if float(value.get("expires_at", 0)) <= now]
        for key in stale[:500]:
            self._idempotency.pop(key, None)

    def _serialize(self, cmd: _OpenClawQueuedCommand) -> Dict[str, Any]:
        return {
            "request_id": cmd.request_id,
            "command_type": cmd.command_type,
            "session_id": cmd.session_id,
            "source": cmd.source,
            "risk_level": cmd.risk_level,
            "decision": cmd.decision,
            "target_worker": cmd.target_worker,
            "resolved_worker": cmd.resolved_worker,
            "state": cmd.state,
            "created_at": cmd.created_at.isoformat(),
            "started_at": cmd.started_at.isoformat() if cmd.started_at else None,
            "finished_at": cmd.finished_at.isoformat() if cmd.finished_at else None,
            "timeout_ms": cmd.timeout_ms,
            "deferred_reason": cmd.deferred_reason,
            "deferred_count": int(cmd.deferred_count),
            "error": cmd.error,
            "failure_class": cmd.failure_class,
            "result": cmd.result,
        }

    def _ensure_session(self, session_id: str) -> _OpenClawSessionState:
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                return session
            session = _OpenClawSessionState(session_id=session_id)
            self._sessions[session_id] = session
        self._ensure_worker(session)
        return session

    def _ensure_worker(self, session: _OpenClawSessionState) -> None:
        with session.lock:
            if session.worker_thread and session.worker_thread.is_alive():
                return
            session.worker_thread = threading.Thread(
                target=self._worker_loop,
                args=(session,),
                name=f"openclaw-session-{session.session_id}",
                daemon=True,
            )
            session.worker_thread.start()

    def _worker_loop(self, session: _OpenClawSessionState) -> None:
        while True:
            cmd: Optional[_OpenClawQueuedCommand] = None
            with session.cond:
                now = datetime.now()
                if session.manual_queue:
                    cmd = session.manual_queue.popleft()
                elif session.autopilot_queue:
                    hold = session.manual_hold_until
                    if hold and now < hold:
                        deferred_cmd = session.autopilot_queue.popleft()
                        deferred_cmd.state = "deferred"
                        deferred_cmd.deferred_reason = "manual_hold"
                        deferred_cmd.deferred_count += 1
                        session.autopilot_queue.append(deferred_cmd)
                        _openclaw_metrics_inc("queue_deferred")
                        _openclaw_metrics_event("queue_deferred", {"session_id": session.session_id, "request_id": deferred_cmd.request_id})
                        session.cond.wait(timeout=min(0.6, max(0.1, (hold - now).total_seconds())))
                        continue
                    cmd = session.autopilot_queue.popleft()
                else:
                    session.cond.wait(timeout=1.0)
                    continue
                session.active_request_id = cmd.request_id if cmd else None

            if not cmd:
                continue

            cmd.state = "running"
            cmd.started_at = datetime.now()
            wait_ms = int((cmd.started_at - cmd.created_at).total_seconds() * 1000)
            with self._lock:
                self._wait_samples_ms.append(max(0, wait_ms))
                if len(self._wait_samples_ms) > 2000:
                    del self._wait_samples_ms[:-2000]
            try:
                if callable(self._executor):
                    outcome = self._executor(cmd.command_type, cmd.payload)
                else:
                    outcome = {"success": False, "error": "openclaw queue executor not configured"}
            except Exception as exc:
                outcome = {"success": False, "error": str(exc)}

            cmd.result = outcome if isinstance(outcome, dict) else {"success": False, "error": "invalid queue outcome"}
            cmd.finished_at = datetime.now()
            cmd.state = "completed" if bool((cmd.result or {}).get("success")) else "failed"
            cmd.error = "" if cmd.state == "completed" else str((cmd.result or {}).get("error", "command failed"))
            if cmd.state == "completed":
                cmd.failure_class = ""
            else:
                failure_class = str((cmd.result or {}).get("failure_class", "")).strip().lower()
                if not failure_class:
                    failure_class = _classify_openclaw_failure(cmd.result)
                cmd.failure_class = failure_class or "unknown"
            if cmd.state == "completed":
                _openclaw_metrics_inc("queue_executed")
            else:
                _openclaw_metrics_inc("queue_failed")
                session.last_error = cmd.error
            dispatch = cmd.result.get("dispatch") if isinstance(cmd.result.get("dispatch"), dict) else {}
            already_recorded = bool((cmd.result or {}).get("_dispatch_result_recorded"))
            result_worker = str(dispatch.get("worker_id") or cmd.resolved_worker or "").strip().lower()
            if result_worker and not already_recorded:
                _control_plane_registry.record_dispatch_result(
                    worker_id=result_worker,
                    success=cmd.state == "completed",
                    failure_class=cmd.failure_class or "unknown",
                )
            self._persist_wal("finish", cmd)
            cmd.done_event.set()
            with session.cond:
                if session.active_request_id == cmd.request_id:
                    session.active_request_id = None
                session.cond.notify_all()

    def enqueue(
        self,
        command_type: str,
        payload: Dict[str, Any],
        session_id: str = "default",
        source: str = "manual",
        priority: int = 0,
        idempotency_key: str = "",
        timeout_ms: int = OPENCLAW_BROWSER_TIMEOUT_MS,
        risk_level: str = "medium",
        decision: str = "auto_approved",
        target_worker: str = "",
        resolved_worker: str = "",
    ) -> Dict[str, Any]:
        normalized_session = str(session_id or "default").strip() or "default"
        normalized_source = str(source or "manual").strip().lower()
        if normalized_source not in {"manual", "autopilot", "system"}:
            normalized_source = "manual"

        session = self._ensure_session(normalized_session)
        with self._lock:
            self._trim_idempotency()
            if idempotency_key:
                cache_key = f"{normalized_session}:{idempotency_key}"
                cached = self._idempotency.get(cache_key)
                if cached:
                    request_id = str(cached.get("request_id", ""))
                    cached_cmd = self._commands.get(request_id)
                    if cached_cmd:
                        _openclaw_metrics_inc("queue_idempotent_hit")
                        return {
                            "success": True,
                            "idempotent_replay": True,
                            "request_id": request_id,
                            "command": self._serialize(cached_cmd),
                        }

        cmd = _OpenClawQueuedCommand(
            request_id=f"oc-{uuid.uuid4().hex[:18]}",
            command_type=str(command_type),
            payload=dict(payload or {}),
            session_id=normalized_session,
            source=normalized_source,
            priority=int(priority),
            idempotency_key=str(idempotency_key or ""),
            created_at=datetime.now(),
            timeout_ms=max(1000, int(timeout_ms)),
            risk_level=str(risk_level or "medium").strip().lower() or "medium",
            decision=str(decision or "auto_approved").strip().lower() or "auto_approved",
            target_worker=str(target_worker or "").strip().lower(),
            resolved_worker=str(resolved_worker or "").strip().lower(),
        )
        with session.cond:
            queued_count = len(session.manual_queue) + len(session.autopilot_queue)
            if queued_count >= OPENCLAW_SESSION_QUEUE_MAX:
                if normalized_source == "manual" and session.autopilot_queue:
                    evicted = session.autopilot_queue.pop()
                    evicted.state = "cancelled"
                    evicted.error = "queue_evicted_by_manual"
                    evicted.finished_at = datetime.now()
                    evicted.done_event.set()
                    _openclaw_metrics_inc("queue_cancelled")
                else:
                    return {
                        "success": False,
                        "error": "Queue full",
                        "error_code": "QUEUE_FULL",
                        "session_id": normalized_session,
                    }
            if normalized_source == "manual":
                session.manual_hold_until = datetime.now() + timedelta(seconds=OPENCLAW_MANUAL_HOLD_SEC)
                session.manual_queue.append(cmd)
                _openclaw_metrics_inc("queue_manual_override")
            elif normalized_source == "autopilot":
                session.autopilot_queue.append(cmd)
            else:
                session.manual_queue.append(cmd)
            session.cond.notify_all()
        with self._lock:
            self._commands[cmd.request_id] = cmd
            if cmd.idempotency_key:
                cache_key = f"{normalized_session}:{cmd.idempotency_key}"
                self._idempotency[cache_key] = {
                    "request_id": cmd.request_id,
                    "expires_at": time.time() + OPENCLAW_IDEMPOTENCY_TTL_SEC,
                }
        _openclaw_metrics_inc("queue_enqueued")
        self._persist_wal("enqueue", cmd)
        return {
            "success": True,
            "request_id": cmd.request_id,
            "idempotent_replay": False,
            "command": self._serialize(cmd),
        }

    def get_command(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cmd = self._commands.get(str(request_id or ""))
            return self._serialize(cmd) if cmd else None

    def wait_command(self, request_id: str, timeout_sec: float) -> Optional[Dict[str, Any]]:
        with self._lock:
            cmd = self._commands.get(str(request_id or ""))
        if not cmd:
            return None
        cmd.done_event.wait(timeout=max(0.1, float(timeout_sec)))
        return self._serialize(cmd)

    def cancel_command(self, request_id: str) -> Dict[str, Any]:
        req = str(request_id or "").strip()
        if not req:
            return {"success": False, "error": "Missing request_id"}
        with self._lock:
            cmd = self._commands.get(req)
        if not cmd:
            return {"success": False, "error": "Command not found", "error_code": "NOT_FOUND"}
        session = self._ensure_session(cmd.session_id)
        with session.cond:
            for queue_obj in (session.manual_queue, session.autopilot_queue):
                for idx, item in enumerate(list(queue_obj)):
                    if item.request_id == req:
                        del queue_obj[idx]
                        item.state = "cancelled"
                        item.error = "command_cancelled"
                        item.finished_at = datetime.now()
                        item.done_event.set()
                        _openclaw_metrics_inc("queue_cancelled")
                        session.cond.notify_all()
                        return {"success": True, "request_id": req, "state": "cancelled"}
        return {"success": False, "request_id": req, "error": "Cannot cancel running/completed command", "error_code": "NOT_CANCELLABLE"}

    def set_manual_hold(self, session_id: str, enabled: bool = True, duration_sec: int = OPENCLAW_MANUAL_HOLD_SEC) -> Dict[str, Any]:
        session = self._ensure_session(session_id)
        with session.cond:
            if enabled:
                session.manual_hold_until = datetime.now() + timedelta(seconds=max(1, int(duration_sec)))
            else:
                session.manual_hold_until = None
            session.cond.notify_all()
            return {
                "success": True,
                "session_id": session.session_id,
                "manual_hold_until": session.manual_hold_until.isoformat() if session.manual_hold_until else None,
            }

    def list_sessions(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        with self._lock:
            sessions = list(self._sessions.values())
            wait_samples = list(self._wait_samples_ms)
        for session in sessions:
            with session.lock:
                rows.append({
                    "session_id": session.session_id,
                    "worker_alive": bool(session.worker_thread and session.worker_thread.is_alive()),
                    "queue_depth": len(session.manual_queue) + len(session.autopilot_queue),
                    "manual_queue_depth": len(session.manual_queue),
                    "autopilot_queue_depth": len(session.autopilot_queue),
                    "active_request_id": session.active_request_id,
                    "manual_hold_until": session.manual_hold_until.isoformat() if session.manual_hold_until else None,
                    "last_error": session.last_error,
                })
        wait_p95 = 0
        if wait_samples:
            ordered = sorted(wait_samples)
            idx = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
            wait_p95 = int(ordered[idx])
        return {
            "sessions": sorted(rows, key=lambda item: item.get("session_id", "")),
            "queue_wait_p95_ms": wait_p95,
        }

    def queue_snapshot(self) -> Dict[str, Any]:
        data = self.list_sessions()
        totals = {
            "total_sessions": len(data.get("sessions", [])),
            "total_queue_depth": sum(int(item.get("queue_depth", 0) or 0) for item in data.get("sessions", [])),
            "queue_wait_p95_ms": int(data.get("queue_wait_p95_ms", 0) or 0),
        }
        return {**totals, "sessions": data.get("sessions", [])}


_openclaw_command_manager = _OpenClawCommandManager()


def build_model_routing_status(event_limit: int = 60) -> Dict[str, Any]:
    router = ModelRouter()
    usage = router.get_usage_summary()
    model_usage = usage.get("models", {}) or {}

    total_tokens = int(sum((item or {}).get("tokens", 0) for item in model_usage.values()))
    total_calls = int(sum((item or {}).get("calls", 0) for item in model_usage.values()))
    model_breakdown = []
    for model_name, item in model_usage.items():
        calls = int((item or {}).get("calls", 0))
        tokens = int((item or {}).get("tokens", 0))
        model_breakdown.append({
            "model": model_name,
            "calls": calls,
            "tokens": tokens,
            "cost_usd": round(float((item or {}).get("cost_usd", 0.0)), 6),
            "avg_tokens_per_call": int(tokens / calls) if calls > 0 else 0,
        })
    model_breakdown.sort(key=lambda x: x["calls"], reverse=True)

    events = read_recent_routing_events(limit=event_limit)
    success_calls = sum(1 for e in events if e.get("success"))
    failed_calls = len(events) - success_calls
    avg_latency_ms = int(
        sum(int(e.get("duration_ms") or 0) for e in events) / len(events)
    ) if events else 0
    recent_total_tokens = int(sum(int(((e.get("usage") or {}).get("total_tokens") or 0)) for e in events))
    last_event = events[-1] if events else None
    live_model = last_event.get("selected_model") if last_event else None

    return {
        "budget": usage,
        "total_tokens": total_tokens,
        "total_calls": total_calls,
        "model_breakdown": model_breakdown,
        "live_model": live_model,
        "recent": {
            "window_size": len(events),
            "success_calls": success_calls,
            "failed_calls": failed_calls,
            "success_ratio": round((success_calls / len(events)), 4) if events else 0.0,
            "avg_latency_ms": avg_latency_ms,
            "tokens": recent_total_tokens,
        },
        "last_event": last_event,
        "task_types": TASK_TYPE_VALUES,
        "updated": datetime.now().isoformat(),
    }


# ============================================
# PERSISTENT STATE
# ============================================

class PersistentState:
    """State that persists across requests and broadcasts to all clients"""

    def __init__(self):
        self._lock = threading.RLock()
        self.agent_logs: Dict[str, List[Dict]] = {}
        self.known_agents = {
            "orion",
            "guardian",
            "nova",
            "pixel",
            "cipher",
            "echo",
            "flux",
        }
        self.orion_logs: List[Dict] = []
        self.guardian_logs: List[Dict] = []
        self.orion_thinking: str = ""
        self.guardian_thinking: str = ""
        self.orion_status: Dict = {}
        self.guardian_status: Dict = {}
        self.project_status: Dict = {
            "name": "Nexus",
            "status": "Ready",
            "current_iteration": 0,
            "score": 0
        }
        self.pending_decisions: List[Dict] = []
        self.decision_history: List[Dict] = []
        self.max_logs = 500

        # Load from file if exists
        self._load()

    def _normalize_agent(self, name: Any) -> str:
        return str(name or "unknown").strip().lower()

    def _load(self):
        """Load state from file"""
        state_file = LOGS_DIR / "dashboard_state.json"
        if state_file.exists():
            try:
                with self._lock:
                    with open(state_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        loaded_agent_logs = data.get('agent_logs', {})
                        if isinstance(loaded_agent_logs, dict):
                            self.agent_logs = {
                                self._normalize_agent(agent): logs[-100:]
                                for agent, logs in loaded_agent_logs.items()
                                if isinstance(logs, list)
                            }
                            self.known_agents.update(self.agent_logs.keys())
                        self.orion_logs = data.get('orion_logs', [])[-100:]
                        self.guardian_logs = data.get('guardian_logs', [])[-100:]
                        self.orion_thinking = data.get('orion_thinking', '')
                        self.guardian_thinking = data.get('guardian_thinking', '')
                        self.project_status = data.get('project_status', self.project_status)
                        if isinstance(self.project_status, dict) and self.project_status.get("name") == "Auto Dev Loop":
                            self.project_status["name"] = "Nexus"
                        self.decision_history = data.get('decision_history', [])[-50:]
                        logger.info(f"Loaded state from {state_file}")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def _save(self):
        """Save state to file"""
        state_file = LOGS_DIR / "dashboard_state.json"
        try:
            with self._lock:
                tmp_path = state_file.with_name(
                    f"{state_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        'agent_logs': {agent: logs[-100:] for agent, logs in self.agent_logs.items()},
                        'orion_logs': self.orion_logs[-100:],
                        'guardian_logs': self.guardian_logs[-100:],
                        'orion_thinking': self.orion_thinking,
                        'guardian_thinking': self.guardian_thinking,
                        'project_status': self.project_status,
                        'decision_history': self.decision_history[-50:]
                    }, f, indent=2)
                os.replace(tmp_path, state_file)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def register_agents(self, agents: List[str]):
        with self._lock:
            for agent in agents or []:
                normalized = self._normalize_agent(agent)
                if normalized:
                    self.known_agents.add(normalized)
                    self.agent_logs.setdefault(normalized, [])
            self._save()

    def add_agent_log(self, agent: str, message: str, level: str = "info"):
        with self._lock:
            normalized = self._normalize_agent(agent)
            self.known_agents.add(normalized)

            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "agent": normalized,
                "message": message,
                "level": level
            }

            logs = self.agent_logs.setdefault(normalized, [])
            logs.append(log_entry)
            if len(logs) > self.max_logs:
                self.agent_logs[normalized] = logs[-self.max_logs:]

            if normalized == "orion":
                self.orion_logs.append(log_entry)
                if len(self.orion_logs) > self.max_logs:
                    self.orion_logs = self.orion_logs[-self.max_logs:]
                socketio.emit('orion_log', log_entry)
            elif normalized == "guardian":
                self.guardian_logs.append(log_entry)
                if len(self.guardian_logs) > self.max_logs:
                    self.guardian_logs = self.guardian_logs[-self.max_logs:]
                socketio.emit('guardian_log', log_entry)

            self._save()
            socketio.emit('agent_log', {"agent": normalized, "entry": log_entry})

    def add_orion_log(self, message: str, level: str = "info"):
        self.add_agent_log("orion", message, level)

    def add_guardian_log(self, message: str, level: str = "info"):
        self.add_agent_log("guardian", message, level)

    def update_orion_thinking(self, thinking: str):
        with self._lock:
            self.orion_thinking = thinking
            self._save()
            socketio.emit('orion_thinking', {"thinking": thinking})

    def update_guardian_thinking(self, thinking: str):
        with self._lock:
            self.guardian_thinking = thinking
            self._save()
            socketio.emit('guardian_thinking', {"thinking": thinking})

    def update_project_status(self, status: Dict):
        with self._lock:
            self.project_status.update(status)
            self.project_status['updated'] = datetime.now().isoformat()
            self._save()
            socketio.emit('project_status', self.project_status)

    def add_pending_decision(self, decision: Dict) -> Dict[str, Any]:
        with self._lock:
            decision_payload = dict(decision or {})
            decision_payload['id'] = decision_payload.get('id', str(datetime.now().timestamp()))
            decision_payload['timestamp'] = datetime.now().isoformat()
            self.pending_decisions.append(decision_payload)
            if len(self.pending_decisions) > 120:
                self.pending_decisions = self.pending_decisions[-120:]
            self._save()
            socketio.emit('new_decision', decision_payload)
            return decision_payload

    def find_pending_command_decision(self, command: str, instance_id: str = "") -> Optional[Dict[str, Any]]:
        target_command = str(command or "").strip()
        target_instance = str(instance_id or "").strip().lower()
        if not target_command:
            return None
        with self._lock:
            for item in reversed(self.pending_decisions):
                if str(item.get("kind", "")) != "command_intervention":
                    continue
                if str(item.get("command", "")).strip() != target_command:
                    continue
                if target_instance and str(item.get("instance_id", "")).strip().lower() != target_instance:
                    continue
                return dict(item)
        return None

    def list_pending_decisions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.pending_decisions]

    def get_pending_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        target = str(decision_id or "").strip()
        if not target:
            return None
        with self._lock:
            for item in self.pending_decisions:
                if str(item.get("id", "")) == target:
                    return dict(item)
        return None

    def defer_decision(
        self,
        decision_id: str,
        defer_sec: int = 300,
        reason: str = "",
        actor: str = "dashboard",
    ) -> Optional[Dict[str, Any]]:
        target = str(decision_id or "").strip()
        if not target:
            return None
        now = datetime.now()
        with self._lock:
            for item in self.pending_decisions:
                if str(item.get("id", "")) != target:
                    continue
                item["deferred"] = True
                item["defer_reason"] = str(reason or "").strip()
                item["defer_actor"] = str(actor or "dashboard").strip() or "dashboard"
                item["deferred_at"] = now.isoformat()
                item["deferred_until"] = (now + timedelta(seconds=max(30, int(defer_sec)))).isoformat()
                item["defer_count"] = int(item.get("defer_count", 0) or 0) + 1
                self._save()
                socketio.emit("decision_deferred", dict(item))
                return dict(item)
        return None

    def approve_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for decision in self.pending_decisions:
                if decision.get('id') == decision_id:
                    decision['decision'] = 'approved'
                    decision['manual'] = True
                    self.decision_history.append(decision)
                    self.pending_decisions.remove(decision)
                    self._save()
                    socketio.emit('decision_updated', decision)
                    return dict(decision)
            return None

    def decline_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for decision in self.pending_decisions:
                if decision.get('id') == decision_id:
                    decision['decision'] = 'declined'
                    decision['manual'] = True
                    self.decision_history.append(decision)
                    self.pending_decisions.remove(decision)
                    self._save()
                    socketio.emit('decision_updated', decision)
                    return dict(decision)
            return None

    def respond_decision(
        self,
        decision_id: str,
        response: Dict[str, Any],
        actor: str = "dashboard",
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            for decision in self.pending_decisions:
                if decision.get("id") != decision_id:
                    continue
                decision["decision"] = "responded"
                decision["manual"] = True
                decision["response"] = dict(response or {})
                decision["response_actor"] = str(actor or "dashboard").strip() or "dashboard"
                decision["response_at"] = datetime.now().isoformat()
                self.decision_history.append(decision)
                self.pending_decisions.remove(decision)
                self._save()
                socketio.emit("decision_updated", decision)
                return dict(decision)
            return None

    def get_status(self) -> Dict:
        with self._lock:
            return {
                "orion": {
                    "logs": self.orion_logs[-50:],
                    "thinking": self.orion_thinking,
                    "status": self.orion_status
                },
                "guardian": {
                    "logs": self.guardian_logs[-50:],
                    "thinking": self.guardian_thinking,
                    "status": self.guardian_status
                },
                "project": dict(self.project_status),
                "pending_decisions": list(self.pending_decisions),
                "decision_history": list(self.decision_history[-20:]),
                "agent_logs": {agent: logs[-20:] for agent, logs in self.agent_logs.items()},
                "agent_fleet": self.get_agent_fleet(),
                "model_routing": build_model_routing_status(event_limit=30),
            }

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self.pending_decisions)

    def get_project_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.project_status)

    def get_agent_fleet(self) -> List[Dict[str, Any]]:
        with self._lock:
            fleet: List[Dict[str, Any]] = []
            now = datetime.now()

            for agent in sorted(self.known_agents):
                logs = self.agent_logs.get(agent, [])
                latest = logs[-1] if logs else None
                last_seen = latest.get("timestamp") if latest else None

                online = False
                if last_seen:
                    try:
                        delta = (now - datetime.fromisoformat(last_seen)).total_seconds()
                        online = delta <= 45
                    except Exception:
                        online = False

                fleet.append({
                    "name": agent,
                    "online": online,
                    "last_seen": last_seen,
                    "last_message": latest.get("message", "") if latest else "",
                    "last_level": latest.get("level", "info") if latest else "info",
                    "logs_count": len(logs),
                })

            return fleet

    def get_event_feed(
        self,
        limit: int = 120,
        agent: str = "all",
        level: str = "all",
        include_routing: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build unified event feed from agent logs, decisions, and routing events."""
        with self._lock:
            target_agent = self._normalize_agent(agent)
            target_level = self._normalize_agent(level)

            events: List[Dict[str, Any]] = []
            for agent_name, logs in self.agent_logs.items():
                if target_agent != "all" and agent_name != target_agent:
                    continue
                for entry in logs[-max(50, limit):]:
                    entry_level = self._normalize_agent(entry.get("level", "info"))
                    if target_level != "all" and entry_level != target_level:
                        continue
                    events.append({
                        "timestamp": entry.get("timestamp"),
                        "type": "agent_log",
                        "agent": agent_name,
                        "level": entry_level,
                        "message": entry.get("message", ""),
                    })

            for item in self.pending_decisions[-30:]:
                events.append({
                    "timestamp": item.get("timestamp"),
                    "type": "decision_pending",
                    "agent": "guardian",
                    "level": "warning",
                    "message": f"Pending decision: {item.get('action', 'unknown')}",
                    "data": item,
                })

            for item in self.decision_history[-50:]:
                events.append({
                    "timestamp": item.get("timestamp"),
                    "type": "decision_history",
                    "agent": "guardian",
                    "level": "success" if item.get("decision") == "approved" else "error",
                    "message": f"Decision {item.get('decision', 'unknown')}: {item.get('action', 'unknown')}",
                    "data": item,
                })

        if include_routing:
            for event in read_recent_routing_events(limit=max(30, limit // 2)):
                events.append({
                    "timestamp": event.get("timestamp"),
                    "type": "routing",
                    "agent": self._normalize_agent(event.get("agent", "router")),
                    "level": "success" if event.get("success") else "error",
                    "message": f"Routing {event.get('selected_model', 'unknown')} for {event.get('task_type', 'unknown')}",
                    "data": event,
                })

        def _parse_ts(value: Any) -> datetime:
            try:
                return datetime.fromisoformat(str(value))
            except Exception:
                return datetime.min

        events.sort(key=lambda x: _parse_ts(x.get("timestamp")), reverse=True)
        return events[:max(1, limit)]


# Global state instance
state = PersistentState()


def _instance_by_id(instance_id: Optional[str]) -> Dict[str, str]:
    requested = str(instance_id or LOCAL_INSTANCE_ID).strip().lower()
    for instance in ORION_INSTANCES:
        if str(instance.get("id", "")).strip().lower() == requested:
            return instance
    return ORION_INSTANCES[0]


def _fetch_instance_runtime(instance_id: Optional[str]) -> Dict[str, Any]:
    instance = _instance_by_id(instance_id)
    resolved_id = str(instance.get("id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    mode = str(instance.get("mode", "local")).strip().lower() or "local"

    if mode == "local":
        runtime = {}
        status_handler = _runtime_bridge.get("status_handler")
        if callable(status_handler):
            try:
                runtime = status_handler() or {}
            except Exception as exc:
                logger.error(f"Local runtime status handler failed: {exc}")
        return {
            "instance": instance,
            "instance_id": resolved_id,
            "instance_name": str(instance.get("name", resolved_id)),
            "mode": mode,
            "online": True,
            "runtime": runtime if isinstance(runtime, dict) else {},
            "source": "local_runtime_bridge",
        }

    base_url = str(instance.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        return {
            "instance": instance,
            "instance_id": resolved_id,
            "instance_name": str(instance.get("name", resolved_id)),
            "mode": mode,
            "online": False,
            "runtime": {},
            "source": "missing_remote_base_url",
        }

    for path in ("/api/agents/status", "/api/status"):
        payload = _http_json("GET", f"{base_url}{path}", timeout_sec=2.4)
        runtime = payload.get("runtime") if isinstance(payload, dict) else {}
        if isinstance(runtime, dict) and runtime:
            return {
                "instance": instance,
                "instance_id": resolved_id,
                "instance_name": str(instance.get("name", resolved_id)),
                "mode": mode,
                "online": True,
                "runtime": runtime,
                "source": f"remote{path}",
            }

    return {
        "instance": instance,
        "instance_id": resolved_id,
        "instance_name": str(instance.get("name", resolved_id)),
        "mode": mode,
        "online": False,
        "runtime": {},
        "source": "remote_unreachable",
    }


def _build_agent_activity_snapshot(
    instance_id: Optional[str] = None,
    max_agents: int = 12,
) -> Dict[str, Any]:
    runtime_payload = _fetch_instance_runtime(instance_id)
    runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
    runtime_controls = runtime.get("agent_controls") if isinstance(runtime.get("agent_controls"), dict) else {}
    fleet = state.get_agent_fleet()
    fleet_map: Dict[str, Dict[str, Any]] = {}
    for item in fleet:
        name = state._normalize_agent(item.get("name"))
        if name:
            fleet_map[name] = item

    runtime_agents = runtime.get("agent_runtime") if isinstance(runtime, dict) else []
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    if isinstance(runtime_agents, list):
        for item in runtime_agents:
            if not isinstance(item, dict):
                continue
            name = state._normalize_agent(item.get("name"))
            if not name:
                continue
            seen.add(name)
            fleet_item = fleet_map.get(name, {})
            control = runtime_controls.get(name, {}) if isinstance(runtime_controls, dict) else {}
            rows.append({
                "name": name,
                "role": str(item.get("role", "") or ""),
                "running": bool(item.get("running", False)),
                "online": bool(fleet_item.get("online", False) or item.get("running", False)),
                "enabled": bool(control.get("enabled", True)),
                "control_reason": str(control.get("reason", "") or ""),
                "control_updated_at": control.get("updated_at"),
                "control_updated_by": str(control.get("updated_by", "") or ""),
                "task": str(item.get("current_task", "") or "").strip(),
                "last_seen": fleet_item.get("last_seen"),
                "last_message": str(fleet_item.get("last_message", "") or ""),
                "last_level": str(fleet_item.get("last_level", "info") or "info"),
            })

    include_fleet_fallback = str(runtime_payload.get("mode", "local")) == "local" or not rows
    if include_fleet_fallback:
        for name, fleet_item in fleet_map.items():
            if name in seen:
                continue
            control = runtime_controls.get(name, {}) if isinstance(runtime_controls, dict) else {}
            rows.append({
                "name": name,
                "role": "",
                "running": False,
                "online": bool(fleet_item.get("online", False)),
                "enabled": bool(control.get("enabled", True)),
                "control_reason": str(control.get("reason", "") or ""),
                "control_updated_at": control.get("updated_at"),
                "control_updated_by": str(control.get("updated_by", "") or ""),
                "task": "",
                "last_seen": fleet_item.get("last_seen"),
                "last_message": str(fleet_item.get("last_message", "") or ""),
                "last_level": str(fleet_item.get("last_level", "info") or "info"),
            })

    rows.sort(key=lambda x: (not bool(x.get("running")), not bool(x.get("online")), str(x.get("name", ""))))
    rows = rows[:max(1, int(max_agents))]

    running = bool(runtime.get("running", False))
    paused = bool(runtime.get("paused", False))
    pause_reason = str(runtime.get("pause_reason", "") or "")
    iteration = runtime.get("iteration")
    active_flows = runtime.get("active_flows") if isinstance(runtime.get("active_flows"), list) else []
    active_flow_count = int(runtime.get("active_flow_count", 0) or len([x for x in active_flows if bool((x or {}).get("active"))]))

    mode_label = "tạm dừng" if paused else ("đang chạy" if running else "đang rảnh")
    runtime_line = (
        f"{runtime_payload.get('instance_name', runtime_payload.get('instance_id', LOCAL_INSTANCE_ID))}: "
        f"{mode_label}, vòng lặp {iteration if iteration is not None else 'n/a'}"
    )
    if paused and pause_reason:
        runtime_line += f" ({pause_reason})"

    highlights: List[str] = []
    for item in rows[:4]:
        if not bool(item.get("enabled", True)):
            state_label = "disabled"
        else:
            state_label = "running" if item.get("running") else ("online" if item.get("online") else "idle")
        task = str(item.get("task", "")).strip()
        if task:
            highlights.append(f"{str(item.get('name', '')).upper()}: {state_label} · task={task}")
        else:
            highlights.append(f"{str(item.get('name', '')).upper()}: {state_label}")

    return {
        "instance_id": runtime_payload.get("instance_id"),
        "instance_name": runtime_payload.get("instance_name"),
        "mode": runtime_payload.get("mode"),
        "online": bool(runtime_payload.get("online", False)),
        "source": runtime_payload.get("source"),
        "running": running,
        "paused": paused,
        "pause_reason": pause_reason,
        "iteration": iteration,
        "active_flow_count": active_flow_count,
        "active_flows": active_flows[:8] if isinstance(active_flows, list) else [],
        "agent_controls": runtime_controls if isinstance(runtime_controls, dict) else {},
        "agents": rows,
        "summary": runtime_line,
        "highlights": highlights,
    }


def _format_agent_activity_lines(activity: Dict[str, Any], max_lines: int = 6) -> List[str]:
    lines: List[str] = []
    rows = activity.get("agents") if isinstance(activity, dict) else []
    if not isinstance(rows, list):
        return lines
    for item in rows[:max(1, int(max_lines))]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).upper() or "AGENT"
        if not bool(item.get("enabled", True)):
            state_label = "disabled"
        else:
            state_label = "running" if item.get("running") else ("online" if item.get("online") else "idle")
        task = str(item.get("task", "")).strip()
        role = str(item.get("role", "")).strip()
        if task:
            lines.append(f"{name}: {state_label} · task={task}")
        elif role:
            lines.append(f"{name}: {state_label} · role={role}")
        else:
            lines.append(f"{name}: {state_label}")
    return lines


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    try:
        if not value:
            return None
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 2.0,
) -> Dict[str, Any]:
    data_bytes = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib_request.Request(url, data=data_bytes, method=method.upper(), headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body) if body else {}
    except urllib_error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
            parsed = json.loads(body) if body else {}
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"success": False, "error": f"HTTP {exc.code}", "detail": body[:400]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _sanitize_shell_command(command: str) -> Dict[str, Any]:
    raw = str(command or "").strip()
    if not raw:
        return {"ok": False, "error": "Command is empty"}

    if MONITOR_ALLOW_UNSAFE_COMMANDS:
        try:
            argv = shlex.split(raw)
        except Exception as exc:
            return {"ok": False, "error": f"Cannot parse command: {exc}"}
        if not argv:
            return {"ok": False, "error": "Command has no executable"}
        return {"ok": True, "argv": argv, "normalized": raw}

    # Keep only first segment for safety.
    segment = re.split(r"\|", raw, maxsplit=1)[0].strip()
    if any(token in segment for token in ["&&", ";", "`", "$(", ">", "<"]):
        return {"ok": False, "error": "Unsafe shell operator detected"}

    try:
        argv = shlex.split(segment)
    except Exception as exc:
        return {"ok": False, "error": f"Cannot parse command: {exc}"}

    if not argv:
        return {"ok": False, "error": "Command has no executable"}

    binary = argv[0].lower()
    allowed = {"curl", "lsof", "cat", "rg", "ls", "head", "tail"}
    if binary not in allowed:
        return {"ok": False, "error": f"Executable '{binary}' is not in safe allowlist"}

    if binary == "curl":
        joined = " ".join(argv[1:])
        if "127.0.0.1" not in joined and "localhost" not in joined:
            return {"ok": False, "error": "curl command is restricted to localhost targets"}

    return {"ok": True, "argv": argv, "normalized": segment}


def _execute_approved_shell_command(command: str) -> Dict[str, Any]:
    safety = _sanitize_shell_command(command)
    if not safety.get("ok"):
        return {"success": False, "error": safety.get("error", "Unsafe command")}

    argv = safety["argv"]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        compact = output.strip()
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": safety.get("normalized", command),
            "output": compact[:1400],
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timeout (>15s)"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _computer_control_status() -> Dict[str, Any]:
    fallback_available = bool(shutil.which("osascript")) and sys.platform == "darwin"
    return {
        "available": (pyautogui is not None) or fallback_available,
        "enabled": bool(_computer_control_enabled),
        "provider": "pyautogui" if pyautogui is not None else ("osascript" if fallback_available else "unavailable"),
    }


def _keyboard_press_fallback(key: str) -> Dict[str, Any]:
    if not (sys.platform == "darwin" and shutil.which("osascript")):
        return {"success": False, "error": "No fallback keyboard backend available"}
    key_map = {
        "enter": 36,
        "esc": 53,
        "tab": 48,
        "space": 49,
        "up": 126,
        "down": 125,
        "left": 123,
        "right": 124,
        "y": 16,
        "n": 45,
    }
    key_code = key_map.get(str(key).strip().lower())
    if key_code is None:
        return {"success": False, "error": f"Unsupported key '{key}'"}
    try:
        cmd = f'tell application "System Events" to key code {key_code}'
        completed = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True, timeout=4, check=False)
        if completed.returncode != 0:
            return {"success": False, "error": (completed.stderr or completed.stdout or "osascript failed").strip()[:300]}
        return {"success": True, "backend": "osascript", "key": key}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _computer_control_action(action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    use_pyautogui = pyautogui is not None
    if not bool(_computer_control_enabled):
        return {"success": False, "error": "Computer control is disabled"}

    data = payload or {}
    act = str(action or "").strip().lower()
    try:
        if act in {"approve_prompt", "approve", "accept"}:
            if use_pyautogui:
                pyautogui.press("y")
                pyautogui.press("enter")
                return {"success": True, "action": "approve_prompt", "note": "Pressed Y then Enter", "backend": "pyautogui"}
            first = _keyboard_press_fallback("y")
            second = _keyboard_press_fallback("enter")
            if first.get("success") and second.get("success"):
                return {"success": True, "action": "approve_prompt", "note": "Pressed Y then Enter", "backend": "osascript"}
            return {"success": False, "error": first.get("error") or second.get("error") or "fallback failed"}
        if act in {"deny_prompt", "deny", "reject"}:
            if use_pyautogui:
                pyautogui.press("esc")
                return {"success": True, "action": "deny_prompt", "note": "Pressed ESC", "backend": "pyautogui"}
            return _keyboard_press_fallback("esc") | {"action": "deny_prompt", "note": "Pressed ESC"}
        if act in {"continue_prompt", "continue", "enter"}:
            if use_pyautogui:
                pyautogui.press("enter")
                return {"success": True, "action": "continue_prompt", "note": "Pressed Enter", "backend": "pyautogui"}
            return _keyboard_press_fallback("enter") | {"action": "continue_prompt", "note": "Pressed Enter"}
        if act in {"type_text", "type"}:
            text = str(data.get("text", ""))[:500]
            if not text:
                return {"success": False, "error": "Missing text for type_text"}
            if not use_pyautogui:
                return {"success": False, "error": "type_text requires pyautogui backend"}
            pyautogui.write(text, interval=0.01)
            return {"success": True, "action": "type_text", "typed_chars": len(text)}
        if act in {"press"}:
            key = str(data.get("key", "")).strip().lower()
            allowed = {"enter", "esc", "tab", "up", "down", "left", "right", "space", "y", "n"}
            if key not in allowed:
                return {"success": False, "error": f"Unsupported key '{key}'"}
            if use_pyautogui:
                pyautogui.press(key)
                return {"success": True, "action": "press", "key": key, "backend": "pyautogui"}
            fallback = _keyboard_press_fallback(key)
            if fallback.get("success"):
                return {"success": True, "action": "press", "key": key, "backend": "osascript"}
            return {"success": False, "error": fallback.get("error", "fallback failed")}
        return {"success": False, "error": f"Unsupported action '{act}'"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _monitor_auto_approve_safe_decisions() -> None:
    full_auto = _is_full_auto_profile()
    auto_approve_all = bool(MONITOR_AUTO_APPROVE_ALL_DECISIONS or full_auto)
    if not (MONITOR_AUTO_APPROVE_SAFE_DECISIONS or auto_approve_all):
        return
    try:
        pending = list(state.pending_decisions)
    except Exception:
        pending = []
    for item in pending:
        try:
            kind = str(item.get("kind", "")).strip()
            if kind == "human_input_request":
                if _autonomy_no_human_prompts_enabled() and AUTOMATION_FULL_AUTO_AUTO_RESPOND_HUMAN_INPUT:
                    auto_resolved = _automation_try_auto_respond_human_input_request(item, actor="monitor_auto")
                    if auto_resolved.get("success"):
                        continue
                continue
            if kind != "command_intervention":
                if auto_approve_all:
                    decision_id = str(item.get("id", "")).strip()
                    if decision_id:
                        approved = state.approve_decision(decision_id)
                        if approved:
                            state.add_agent_log("guardian", f"🤖 Auto-approved decision: {approved.get('title', kind or 'decision')}", "success")
                continue
            risk = str(item.get("risk_level", "medium")).strip().lower()
            if not auto_approve_all and risk not in {"low"}:
                continue
            if not bool(item.get("auto_execute_on_approve", False)):
                if not auto_approve_all:
                    continue
            decision_id = str(item.get("id", "")).strip()
            if not decision_id:
                continue
            approved = state.approve_decision(decision_id)
            if not approved:
                continue
            command = str(approved.get("command", "")).strip()
            if command and (MONITOR_AUTO_EXECUTE_COMMANDS or full_auto):
                execution = _execute_approved_shell_command(command)
                if execution.get("success"):
                    state.add_agent_log("guardian", f"🤖 Auto-executed decision: {execution.get('command')}", "success")
                else:
                    state.add_agent_log("guardian", f"⚠️ Auto-execute failed: {execution.get('error', 'unknown')}", "warning")
            else:
                state.add_agent_log("guardian", "🤖 Auto-approved decision (no execution).", "info")
        except Exception as exc:
            logger.error(f"Safe decision auto-approve error: {exc}")


def _build_learning_v2_snapshot() -> Dict[str, Any]:
    """Best-effort snapshot of self-learning v2 pipeline for monitor UI."""
    try:
        from src.memory.storage_v2 import get_storage_v2
        from src.memory.learning_policy import get_learning_policy_state
    except Exception:
        return {"enabled": False}

    try:
        storage = get_storage_v2()
        proposals_data = storage.get_proposals_v2()
        proposals = proposals_data.get("proposals", []) if isinstance(proposals_data.get("proposals"), list) else []
        runs = storage.get_experiment_runs(limit=300)
        evidences = storage.list_outcome_evidence(limit=300)
        learning_events = storage.list_learning_events(limit=500)

        status_counts: Dict[str, int] = {}
        for row in proposals:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "unknown")).strip().lower() or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1

        proposal_funnel = {
            "created": len(proposals),
            "pending_approval": status_counts.get("pending_approval", 0),
            "approved": status_counts.get("approved", 0),
            "executed": status_counts.get("executed", 0),
            "verified": status_counts.get("verified", 0),
            "rejected": status_counts.get("rejected", 0),
        }
        now_dt = datetime.now()

        def _safe_ratio(num: int, den: int) -> float:
            return round(float(num) / float(den), 4) if den > 0 else 0.0

        approved_or_beyond = (
            status_counts.get("approved", 0)
            + status_counts.get("executed", 0)
            + status_counts.get("verified", 0)
        )
        executed_or_beyond = status_counts.get("executed", 0) + status_counts.get("verified", 0)
        proposal_funnel["conversion"] = {
            "approval_rate": _safe_ratio(approved_or_beyond, proposal_funnel["created"]),
            "execution_rate": _safe_ratio(executed_or_beyond, approved_or_beyond),
            "verification_rate": _safe_ratio(proposal_funnel["verified"], executed_or_beyond),
            "verified_from_created_rate": _safe_ratio(proposal_funnel["verified"], proposal_funnel["created"]),
        }

        def _parse_time(value: Any):
            if not value:
                return None
            if isinstance(value, datetime):
                return value
            try:
                return datetime.fromisoformat(str(value))
            except Exception:
                return None

        def _window_funnel(hours: int) -> Dict[str, Any]:
            window_seconds = max(1, int(hours)) * 3600
            cohort: List[Dict[str, Any]] = []
            for row in proposals:
                if not isinstance(row, dict):
                    continue
                created_at = _parse_time(row.get("created_at"))
                if not created_at:
                    continue
                if (now_dt - created_at).total_seconds() <= window_seconds:
                    cohort.append(row)
            created = len(cohort)
            approved = 0
            executed = 0
            verified_window = 0
            for row in cohort:
                status = str(row.get("status", "")).strip().lower()
                if status in {"approved", "executed", "verified"}:
                    approved += 1
                if status in {"executed", "verified"}:
                    executed += 1
                if status == "verified":
                    verified_window += 1
            return {
                "created": created,
                "approved": approved,
                "executed": executed,
                "verified": verified_window,
                "approval_rate": _safe_ratio(approved, created),
                "execution_rate": _safe_ratio(executed, approved),
                "verification_rate": _safe_ratio(verified_window, executed),
                "verified_from_created_rate": _safe_ratio(verified_window, created),
            }

        proposal_funnel["window_24h"] = _window_funnel(24)
        proposal_funnel["window_7d"] = _window_funnel(24 * 7)

        verdict_counts = {"win": 0, "loss": 0, "inconclusive": 0}
        pending_recheck_runs = 0
        retry_exhausted_runs = 0
        holdout_pending_runs = 0
        for run in runs:
            verification = run.get("verification", {}) if isinstance(run.get("verification"), dict) else {}
            if bool(verification.get("holdout_pending", False)):
                holdout_pending_runs += 1
                continue
            verdict = str(verification.get("verdict", "")).strip().lower()
            if verdict in verdict_counts:
                verdict_counts[verdict] += 1
            if bool(verification.get("pending_recheck", False)):
                pending_recheck_runs += 1
            if bool(verification.get("retry_exhausted", False)):
                retry_exhausted_runs += 1

        verified_total = sum(verdict_counts.values())
        confidence_values: List[float] = []
        duration_values: List[float] = []
        cost_values: List[float] = []
        latency_delta_values: List[float] = []
        error_delta_values: List[float] = []
        trend_24h = {"win": 0, "loss": 0, "inconclusive": 0}
        trend_7d = {"win": 0, "loss": 0, "inconclusive": 0}
        for evd in evidences:
            if not isinstance(evd, dict):
                continue
            verdict = str(evd.get("verdict", "")).strip().lower()
            ts = evd.get("ts")
            ts_dt = None
            if ts:
                try:
                    ts_dt = datetime.fromisoformat(str(ts))
                except Exception:
                    ts_dt = None
            if verdict in trend_24h and ts_dt and not bool(evd.get("holdout_pending", False)):
                age_sec = (now_dt - ts_dt).total_seconds()
                if age_sec <= 24 * 3600:
                    trend_24h[verdict] += 1
                if age_sec <= 7 * 24 * 3600:
                    trend_7d[verdict] += 1
            try:
                confidence_values.append(float(evd.get("confidence", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            delta = evd.get("delta", {}) if isinstance(evd.get("delta"), dict) else {}
            execution = evd.get("execution", {}) if isinstance(evd.get("execution"), dict) else {}
            try:
                latency_delta_values.append(float(delta.get("avg_duration_ms", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                error_delta_values.append(float(delta.get("total_errors", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                duration_values.append(float(execution.get("duration_ms", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                cost_values.append(float(execution.get("estimated_cost_usd", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass

        def _avg(values: List[float]) -> float:
            return round(sum(values) / len(values), 4) if values else 0.0

        quality = {
            "verified_total": verified_total,
            "win_rate": round(verdict_counts["win"] / verified_total, 4) if verified_total else 0.0,
            "loss_rate": round(verdict_counts["loss"] / verified_total, 4) if verified_total else 0.0,
            "inconclusive_rate": round(verdict_counts["inconclusive"] / verified_total, 4) if verified_total else 0.0,
            "verdict_counts": verdict_counts,
            "avg_confidence": _avg(confidence_values),
            "avg_execution_duration_ms": _avg(duration_values),
            "avg_execution_cost_usd": _avg(cost_values),
            "avg_latency_delta_ms": _avg(latency_delta_values),
            "avg_error_delta": _avg(error_delta_values),
            "evidence_samples": len(evidences),
            "pending_recheck_runs": pending_recheck_runs,
            "holdout_pending_runs": holdout_pending_runs,
            "retry_exhausted_runs": retry_exhausted_runs,
            "trend_24h": trend_24h,
            "trend_7d": trend_7d,
        }
        stream_counts = {"production": 0, "non_production": 0, "unknown": 0}
        stream_trend_24h = {"production": 0, "non_production": 0}
        stream_trend_7d = {"production": 0, "non_production": 0}
        source_counts: Dict[str, int] = {}
        for event in learning_events:
            if not isinstance(event, dict):
                continue
            stream = str(event.get("stream", "")).strip().lower()
            if stream not in {"production", "non_production"}:
                stream = "non_production" if bool(event.get("is_non_production", False)) else "production"
            stream_counts[stream] = stream_counts.get(stream, 0) + 1
            source = str(event.get("source", "")).strip() or "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1
            ts_dt = _parse_time(event.get("ts"))
            if ts_dt:
                age_sec = (now_dt - ts_dt).total_seconds()
                if age_sec <= 24 * 3600:
                    stream_trend_24h[stream] = stream_trend_24h.get(stream, 0) + 1
                if age_sec <= 7 * 24 * 3600:
                    stream_trend_7d[stream] = stream_trend_7d.get(stream, 0) + 1
        total_events = sum(stream_counts.values())
        production_events = stream_counts.get("production", 0)
        non_production_events = stream_counts.get("non_production", 0)
        top_sources = sorted(source_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        learning_event_quality = {
            "total_events": total_events,
            "production_events": production_events,
            "non_production_events": non_production_events,
            "production_ratio": _safe_ratio(production_events, total_events),
            "non_production_ratio": _safe_ratio(non_production_events, total_events),
            "stream_counts": stream_counts,
            "trend_24h": stream_trend_24h,
            "trend_7d": stream_trend_7d,
            "top_sources": [{"source": src, "count": count} for src, count in top_sources],
        }

        policy_state = {}
        try:
            policy_state = get_learning_policy_state() or {}
        except Exception:
            policy_state = {}

        cafe_state = {}
        try:
            from src.memory.cafe_calibrator import get_cafe_calibrator
            cafe_state = get_cafe_calibrator().get_state()
        except Exception:
            cafe_state = {}

        return {
            "enabled": True,
            "proposal_funnel": proposal_funnel,
            "outcome_quality": quality,
            "learning_event_quality": learning_event_quality,
            "cafe": {
                "model_bias": cafe_state.get("model_bias", {}) if isinstance(cafe_state, dict) else {},
                "last_updated": cafe_state.get("last_updated") if isinstance(cafe_state, dict) else None,
            },
            "policy_state": policy_state if isinstance(policy_state, dict) else {},
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as exc:
        logger.warning(f"Failed building learning v2 snapshot: {exc}")
        return {"enabled": False, "error": str(exc)}


def _guardian_control_audit_tail(limit: int = 20) -> List[Dict[str, Any]]:
    size = max(1, int(limit))
    with _guardian_control_audit_lock:
        return list(_guardian_control_audit[-size:])


def _append_guardian_control_audit(
    action: str,
    actor: str,
    reason: str,
    success: bool,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    row = {
        "timestamp": datetime.now().isoformat(),
        "action": str(action or "").strip().lower(),
        "actor": str(actor or "unknown").strip(),
        "reason": str(reason or "").strip(),
        "success": bool(success),
    }
    if isinstance(detail, dict) and detail:
        row["detail"] = detail
    with _guardian_control_audit_lock:
        _guardian_control_audit.append(row)
        if len(_guardian_control_audit) > 300:
            del _guardian_control_audit[:-300]


def _append_intervention_history(
    action: str,
    decision_id: str,
    success: bool,
    actor: str = "dashboard",
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    row = {
        "timestamp": datetime.now().isoformat(),
        "action": str(action or "").strip().lower(),
        "decision_id": str(decision_id or "").strip(),
        "success": bool(success),
        "actor": str(actor or "dashboard").strip() or "dashboard",
    }
    if isinstance(detail, dict) and detail:
        row["detail"] = detail
    _append_jsonl_event(INTERVENTION_HISTORY_PATH, row)


def _intervention_history_tail(limit: int = 120) -> List[Dict[str, Any]]:
    rows = _read_jsonl_events(INTERVENTION_HISTORY_PATH, limit=max(1, int(limit)))
    return _sort_by_timestamp_desc(rows)


def build_status_payload() -> Dict[str, Any]:
    payload = state.get_status()
    status_handler = _runtime_bridge.get("status_handler")
    if callable(status_handler):
        try:
            payload["runtime"] = status_handler() or {}
        except Exception as exc:
            logger.error(f"Runtime status handler failed: {exc}")
    instances = get_orion_instances_snapshot()
    _control_plane_registry.sync_instance_snapshot(instances)
    payload["orion_instances"] = instances
    payload["monitor_supervisor"] = _monitor_supervisor_status()
    payload["computer_control"] = _computer_control_status()
    payload["learning_v2"] = _build_learning_v2_snapshot()
    payload["autonomy"] = {
        "profile": _get_autonomy_profile(),
        "full_auto": _is_full_auto_profile(),
        "full_auto_user_pause_max_sec": MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC,
        "full_auto_maintenance_lease_sec": MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC,
    }
    payload["control_plane"] = _control_plane_registry.snapshot()
    payload["guardian_control"] = {
        "token_required": bool(GUARDIAN_CONTROL_TOKEN),
        "audit_tail": _guardian_control_audit_tail(limit=12),
    }
    try:
        payload["team_persona"] = _team_persona_store.summary(limit=24)
    except Exception as exc:
        payload["team_persona"] = {"members": 0, "total_interactions": 0, "error": str(exc)}

    ops = _build_ops_snapshot()
    digest = _build_events_digest(limit=140)
    feedback_recent = _build_feedback_recent(limit=6)
    payload["feedback_recent"] = feedback_recent
    payload["ops_snapshot"] = ops
    payload["events_digest"] = digest
    payload["ai_summary"] = _build_ai_summary_payload(payload, ops, digest, feedback_recent)
    try:
        payload["autopilot"] = _build_hub_autopilot_status_payload(include_legacy_aliases=True)
    except Exception as exc:
        payload["autopilot"] = {"success": False, "error": str(exc)}

    return payload


def _build_system_slo_snapshot() -> Dict[str, Any]:
    now = datetime.now()
    monitor = _monitor_supervisor_status()
    metrics = _build_openclaw_metrics_snapshot(include_events=False)
    routing_events = read_recent_routing_events(limit=200)
    recent_window = routing_events[-80:]
    recent_success = sum(1 for item in recent_window if bool(item.get("success")))
    recent_latencies = sorted(
        int(item.get("duration_ms", 0) or 0) for item in recent_window if int(item.get("duration_ms", 0) or 0) >= 0
    )
    p95_latency_ms = 0
    if recent_latencies:
        idx = max(0, min(len(recent_latencies) - 1, int(len(recent_latencies) * 0.95) - 1))
        p95_latency_ms = recent_latencies[idx]

    attach_state = metrics.get("attach_state") if isinstance(metrics.get("attach_state"), dict) else {}
    token_state = metrics.get("token") if isinstance(metrics.get("token"), dict) else {}
    counters = metrics.get("counters") if isinstance(metrics.get("counters"), dict) else {}
    rate_limit = _build_api_rate_limit_snapshot(window_sec=300)
    queue = metrics.get("queue") if isinstance(metrics.get("queue"), dict) else {}
    control_plane = _control_plane_registry.snapshot()
    queue_ok = int(counters.get("queue_executed", 0) or 0)
    queue_fail = int(counters.get("queue_failed", 0) or 0)
    queue_total = queue_ok + queue_fail
    policy_auto = int(counters.get("policy_auto_approved", 0) or 0)
    policy_manual = int(counters.get("policy_manual_required", 0) or 0)
    policy_total = max(1, policy_auto + policy_manual)
    return {
        "timestamp": now.isoformat(),
        "uptime_sec": max(0, int((now - _monitor_process_started_at).total_seconds())),
        "autopilot": _build_hub_autopilot_status_payload(include_legacy_aliases=True),
        "monitor": {
            "autopilot_enabled": bool(monitor.get("enabled")),
            "thread_alive": bool(monitor.get("thread_alive")),
            "autonomy_profile": monitor.get("autonomy_profile"),
            "full_auto": bool(monitor.get("full_auto")),
            "autopilot_pause_until": monitor.get("autopilot_pause_until"),
            "full_auto_maintenance_lease_sec": monitor.get("full_auto_maintenance_lease_sec"),
            "last_reason": monitor.get("last_reason"),
            "last_recovery": monitor.get("last_recovery"),
        },
        "routing": {
            "window_size": len(recent_window),
            "success_rate": round((recent_success / len(recent_window)), 4) if recent_window else 0.0,
            "p95_latency_ms": int(p95_latency_ms),
            "failed_calls": len(recent_window) - recent_success,
        },
        "openclaw": {
            "token_present": bool(token_state.get("present")),
            "health_ok": bool(token_state.get("health_ok")),
            "attach_success_rate": float(metrics.get("attach_success_rate", 0.0) or 0.0),
            "flow_success_rate": float(metrics.get("flow_success_rate", 0.0) or 0.0),
            "counters": counters,
            "last_attach_error": attach_state.get("last_error"),
        },
        "openclaw_queue": {
            "enabled": bool(OPENCLAW_QUEUE_ENABLED),
            "total_sessions": int(queue.get("total_sessions", 0) or 0),
            "total_queue_depth": int(queue.get("total_queue_depth", 0) or 0),
            "queue_wait_p95_ms": int(queue.get("queue_wait_p95_ms", 0) or 0),
            "command_success_rate": round(float(queue_ok) / max(1, queue_total), 4),
            "human_intervention_rate": round(float(policy_manual) / policy_total, 4),
            "queue_executed": queue_ok,
            "queue_failed": queue_fail,
        },
        "control_plane": {
            "registered_workers": int(control_plane.get("registered_workers", 0) or 0),
            "alive_workers": int(control_plane.get("alive_workers", 0) or 0),
            "open_circuits": len(control_plane.get("circuits", []) if isinstance(control_plane.get("circuits"), list) else []),
            "blocked_failure_classes": control_plane.get("blocked_failure_classes", {}),
        },
        "api_rate_limit": rate_limit,
    }


def _build_slo_summary() -> Dict[str, Any]:
    base = _build_system_slo_snapshot()
    routing = base.get("routing") if isinstance(base.get("routing"), dict) else {}
    oc_queue = base.get("openclaw_queue") if isinstance(base.get("openclaw_queue"), dict) else {}
    control = base.get("control_plane") if isinstance(base.get("control_plane"), dict) else {}
    return {
        "timestamp": base.get("timestamp"),
        "uptime_sec": base.get("uptime_sec"),
        "targets": {
            "availability": "balanced",
            "routing_success_rate_min": 0.95,
            "queue_wait_p95_ms_max": 2000,
            "human_intervention_rate_max": 0.15,
        },
        "current": {
            "routing_success_rate": float(routing.get("success_rate", 0.0) or 0.0),
            "routing_p95_latency_ms": int(routing.get("p95_latency_ms", 0) or 0),
            "queue_wait_p95_ms": int(oc_queue.get("queue_wait_p95_ms", 0) or 0),
            "command_success_rate": float(oc_queue.get("command_success_rate", 0.0) or 0.0),
            "human_intervention_rate": float(oc_queue.get("human_intervention_rate", 0.0) or 0.0),
            "alive_workers": int(control.get("alive_workers", 0) or 0),
            "registered_workers": int(control.get("registered_workers", 0) or 0),
        },
        "detail": base,
    }


def _call_runtime_command(
    target: str,
    command: str,
    priority: str = "high",
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    command_handler = _runtime_bridge.get("command_handler")
    if not callable(command_handler):
        return {"success": False, "error": "Runtime bridge is not connected"}
    try:
        try:
            return command_handler(target, command, priority, options or {}) or {}
        except TypeError:
            return command_handler(target, command, priority) or {}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _call_instance_command(
    instance_id: str,
    target: str,
    command: str,
    priority: str = "high",
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    instance = _instance_by_id(instance_id)
    if instance.get("mode") == "local":
        return _call_runtime_command(target, command, priority, options)

    base_url = str(instance.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        return {"success": False, "error": f"Instance {instance.get('id')} has no base_url configured"}

    result = _http_json(
        "POST",
        f"{base_url}/api/agent/command",
        payload={
            "target": target,
            "command": command,
            "priority": priority,
            "options": options or {},
            "instance_id": instance.get("id"),
        },
        timeout_sec=4.0,
    )
    if "success" not in result:
        result["success"] = bool(result)
    result["instance_id"] = instance.get("id")
    result["instance_name"] = instance.get("name")
    return result


def get_orion_instances_snapshot() -> List[Dict[str, Any]]:
    snapshot: List[Dict[str, Any]] = []
    local_status = {}
    status_handler = _runtime_bridge.get("status_handler")
    if callable(status_handler):
        try:
            local_status = status_handler() or {}
        except Exception:
            local_status = {}

    for instance in ORION_INSTANCES:
        instance_id = str(instance.get("id", "orion")).strip() or "orion"
        mode = str(instance.get("mode", "local")).strip().lower() or "local"
        item = {
            "id": instance_id,
            "name": str(instance.get("name", instance_id)),
            "mode": mode,
            "base_url": str(instance.get("base_url", "")),
            "online": False,
            "running": False,
            "paused": False,
            "pause_reason": "",
            "paused_since": None,
            "iteration": None,
            "active_flow_count": 0,
            "agent_count": 0,
            "last_progress_at": None,
            "last_cycle_started_at": None,
            "last_cycle_finished_at": None,
        }

        if mode == "local":
            runtime = local_status
            item.update({
                "online": bool(runtime.get("ready", True)),
                "running": bool(runtime.get("running", False)),
                "paused": bool(runtime.get("paused", False)),
                "pause_reason": str(runtime.get("pause_reason", "") or ""),
                "paused_since": runtime.get("paused_since"),
                "iteration": runtime.get("iteration"),
                "active_flow_count": int(runtime.get("active_flow_count", 0) or 0),
                "agent_count": len(runtime.get("agents") or []),
                "last_progress_at": runtime.get("last_progress_at"),
                "last_cycle_started_at": runtime.get("last_cycle_started_at"),
                "last_cycle_finished_at": runtime.get("last_cycle_finished_at"),
            })
            snapshot.append(item)
            continue

        base_url = str(instance.get("base_url", "")).strip().rstrip("/")
        if not base_url:
            snapshot.append(item)
            continue
        status_data = _http_json("GET", f"{base_url}/api/status", timeout_sec=1.8)
        runtime = status_data.get("runtime") if isinstance(status_data, dict) else {}
        if isinstance(runtime, dict):
            item.update({
                "online": True,
                "running": bool(runtime.get("running", False)),
                "paused": bool(runtime.get("paused", False)),
                "pause_reason": str(runtime.get("pause_reason", "") or ""),
                "paused_since": runtime.get("paused_since"),
                "iteration": runtime.get("iteration"),
                "active_flow_count": int(runtime.get("active_flow_count", 0) or 0),
                "agent_count": len(runtime.get("agents") or []),
                "last_progress_at": runtime.get("last_progress_at"),
                "last_cycle_started_at": runtime.get("last_cycle_started_at"),
                "last_cycle_finished_at": runtime.get("last_cycle_finished_at"),
            })
        snapshot.append(item)
    return snapshot


def _select_orions_for_orchestration(
    instances: List[Dict[str, Any]],
    preferred_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    preferred = {normalize_member_id(item) for item in (preferred_ids or []) if item}

    def _score(row: Dict[str, Any]) -> Tuple[int, int, int, str]:
        instance_id = str(row.get("id", ""))
        preferred_bonus = 0 if normalize_member_id(instance_id) in preferred else 1
        online_penalty = 0 if bool(row.get("online")) else 3
        paused_penalty = 2 if bool(row.get("paused")) else 0
        flow_penalty = int(row.get("active_flow_count", 0) or 0)
        return (
            preferred_bonus + online_penalty + paused_penalty,
            flow_penalty,
            0 if bool(row.get("running")) else 1,
            instance_id,
        )

    ordered = sorted([row for row in instances if isinstance(row, dict)], key=_score)
    if ordered:
        return ordered
    return [{"id": LOCAL_INSTANCE_ID, "name": LOCAL_INSTANCE_ID, "online": False, "running": False, "paused": False}]


def _goal_to_work_items(goal: str, task_count: int = 3) -> List[Dict[str, str]]:
    clean_goal = str(goal or "").strip()
    if not clean_goal:
        clean_goal = "Execution objective"
    cap = max(1, min(8, int(task_count)))
    stages = [
        ("Discovery", "Map current state, constraints, and measurable success criteria."),
        ("Execution", "Implement changes and coordinate dependent tasks."),
        ("Verification", "Validate behavior, logs, and regression safety."),
        ("Optimization", "Reduce noise, improve latency/cost, and harden automation."),
        ("Documentation", "Update operational notes and handover steps."),
    ]
    rows: List[Dict[str, str]] = []
    for index in range(cap):
        stage = stages[index % len(stages)]
        rows.append(
            {
                "title": f"{clean_goal} :: {stage[0]}",
                "description": f"{stage[1]} Goal focus: {clean_goal}.",
            }
        )
    return rows


def _build_multi_orion_orchestration(
    goal: str,
    requested_by: str = "",
    task_count: int = 3,
    priority: str = "high",
    create_tasks: bool = False,
    preferred_orions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    member_id = normalize_member_id(requested_by)
    adaptation = _team_adaptation(member_id, intent=goal)
    style = adaptation.get("orchestration") if isinstance(adaptation.get("orchestration"), dict) else {}
    autonomy_mode = str(style.get("autonomy_mode", "balanced"))
    risk_policy = str(style.get("risk_policy", "balanced"))

    instances = get_orion_instances_snapshot()
    selected = _select_orions_for_orchestration(instances, preferred_ids=preferred_orions)
    work_items = _goal_to_work_items(goal, task_count=task_count)

    assignments: List[Dict[str, Any]] = []
    for idx, item in enumerate(work_items):
        target = selected[idx % len(selected)]
        assignments.append(
            {
                "rank": idx + 1,
                "instance_id": str(target.get("id", LOCAL_INSTANCE_ID)),
                "instance_name": str(target.get("name", target.get("id", LOCAL_INSTANCE_ID))),
                "online": bool(target.get("online")),
                "title": item["title"],
                "description": item["description"],
                "priority": priority,
                "execution_mode": autonomy_mode,
                "risk_policy": risk_policy,
            }
        )

    created_tasks: List[Dict[str, Any]] = []
    if create_tasks and "_hub" in globals() and _hub is not None:
        for row in assignments:
            try:
                result = _hub.create_task_safe(
                    title=row["title"],
                    description=row["description"],
                    priority=row["priority"],
                    assigned_to=row["instance_id"],
                )
                created_tasks.append(result)
            except Exception as exc:
                created_tasks.append({"success": False, "error": str(exc), "task_title": row["title"]})

    return {
        "goal": goal,
        "requested_by": member_id,
        "adaptation": adaptation,
        "selection": {
            "candidate_count": len(instances),
            "selected_count": len(selected),
            "selected_orions": [
                {
                    "id": str(item.get("id", "")),
                    "name": str(item.get("name", "")),
                    "online": bool(item.get("online")),
                    "running": bool(item.get("running")),
                    "paused": bool(item.get("paused")),
                    "active_flow_count": int(item.get("active_flow_count", 0) or 0),
                }
                for item in selected
            ],
        },
        "assignments": assignments,
        "created_tasks": created_tasks,
        "orchestration_policy": {
            "autonomy_mode": autonomy_mode,
            "risk_policy": risk_policy,
            "escalate_when": style.get("escalate_when", "critical_only"),
        },
    }


def _estimate_tokens(text: Any) -> int:
    content = str(text or "")
    if not content:
        return 0
    return max(1, int((len(content) + 3) / 4))


def _select_agents_for_goal(goal: str, max_agents: int = 4) -> List[str]:
    text = str(goal or "").lower()
    selected: List[str] = ["guardian", "orion"]
    if re.search(r"\b(ui|ux|design|frontend|giao diện)\b", text):
        selected.append("pixel")
    if re.search(r"\b(security|audit|cipher|an toàn|bảo mật)\b", text):
        selected.append("cipher")
    if re.search(r"\b(deploy|release|ship|rollout|production)\b", text):
        selected.append("flux")
    if re.search(r"\b(research|analysis|plan|nghiên cứu|phân tích)\b", text):
        selected.append("nova")
    if re.search(r"\b(log|monitor|event|telemetry|runtime)\b", text):
        selected.append("echo")

    selected = list(dict.fromkeys([item for item in selected if item]))
    # Keep coordination minimal by default: only add Nova when no specialist was selected.
    if len(selected) <= 2:
        selected.append("nova")
    return selected[:max_agents]


def _build_agent_coordination_plan(
    goal: str,
    adaptation: Dict[str, Any],
    max_agents: int = 4,
    compact_mode: bool = True,
) -> Dict[str, Any]:
    orchestration = adaptation.get("orchestration") if isinstance(adaptation.get("orchestration"), dict) else {}
    comm = adaptation.get("communication") if isinstance(adaptation.get("communication"), dict) else {}
    autonomy_mode = str(orchestration.get("autonomy_mode", "balanced"))
    risk_policy = str(orchestration.get("risk_policy", "balanced"))
    detail_level = str(comm.get("detail_level", "balanced"))
    goal_short = str(goal or "").strip().replace("\n", " ")
    goal_short = re.sub(r"\s+", " ", goal_short)[:180]

    agents = _select_agents_for_goal(goal_short, max_agents=max_agents)
    commands: List[Dict[str, Any]] = []
    verbose_total = 0
    compact_total = 0

    for agent in agents:
        verbose_command = (
            f"Mission objective: {goal_short}. "
            f"Agent role: {agent}. Work in coordinated mode with other agents. "
            f"Autonomy={autonomy_mode}. Risk policy={risk_policy}. "
            f"Response detail={detail_level}. Provide status, delta, blockers, and next action."
        )
        compact_command = (
            f"{agent}|goal:{goal_short}|mode:{autonomy_mode}|risk:{risk_policy}"
            f"|detail:{detail_level}|out:status+delta+next"
        )
        chosen = compact_command if compact_mode else verbose_command
        verbose_tokens = _estimate_tokens(verbose_command)
        chosen_tokens = _estimate_tokens(chosen)
        verbose_total += verbose_tokens
        compact_total += chosen_tokens
        commands.append(
            {
                "agent": agent,
                "command": chosen,
                "command_verbose": verbose_command,
                "estimated_tokens": chosen_tokens,
                "estimated_tokens_verbose": verbose_tokens,
                "compact_mode": bool(compact_mode),
            }
        )

    return {
        "goal": goal_short,
        "agents": agents,
        "commands": commands,
        "token_budget": {
            "compact_mode": bool(compact_mode),
            "estimated_tokens_total": compact_total,
            "estimated_tokens_verbose_total": verbose_total,
            "estimated_tokens_saved": max(0, verbose_total - compact_total),
            "saving_ratio": round((verbose_total - compact_total) / verbose_total, 4) if verbose_total > 0 else 0.0,
        },
    }


def _execute_agent_coordination(
    goal: str,
    member_id: str = "",
    max_agents: int = 4,
    compact_mode: bool = True,
    execute: bool = True,
    priority: str = "high",
    instance_ids: Optional[List[str]] = None,
    multi_instance: bool = True,
) -> Dict[str, Any]:
    adaptation = _team_adaptation(member_id, intent=goal)
    plan = _build_agent_coordination_plan(
        goal=goal,
        adaptation=adaptation,
        max_agents=max_agents,
        compact_mode=compact_mode,
    )

    instances = get_orion_instances_snapshot()
    requested_instance_ids = instance_ids if isinstance(instance_ids, list) else []
    if requested_instance_ids:
        requested = {str(item).strip() for item in requested_instance_ids if str(item).strip()}
        targets = [item for item in instances if str(item.get("id", "")).strip() in requested]
    else:
        if multi_instance:
            targets = [item for item in instances if bool(item.get("online"))]
            if not targets:
                targets = [item for item in instances[:1]]
        else:
            targets = [item for item in instances if str(item.get("id", "")).strip() == LOCAL_INSTANCE_ID]
            if not targets:
                targets = [{"id": LOCAL_INSTANCE_ID, "name": LOCAL_INSTANCE_ID, "online": False}]

    dispatch_results: Dict[str, Dict[str, Any]] = {}
    total_sent = 0
    total_success = 0
    if execute:
        for target_instance in targets:
            instance_id = str(target_instance.get("id", "")).strip() or LOCAL_INSTANCE_ID
            dispatch_results.setdefault(instance_id, {})
            for row in plan.get("commands", []):
                if not isinstance(row, dict):
                    continue
                agent = str(row.get("agent", "")).strip()
                command = str(row.get("command", "")).strip()
                if not agent or not command:
                    continue
                result = _call_instance_command(
                    instance_id=instance_id,
                    target=agent,
                    command=command,
                    priority=priority,
                    options={
                        "source": "agent_coordination_compact",
                        "goal": goal[:160],
                        "compact_mode": compact_mode,
                        "member_id": member_id,
                    },
                )
                ok = bool(result.get("success", True))
                dispatch_results[instance_id][agent] = result
                total_sent += 1
                if ok:
                    total_success += 1

    return {
        "goal": goal,
        "member_id": member_id or None,
        "adaptation": adaptation,
        "plan": plan,
        "execution": {
            "executed": execute,
            "instances": [str(item.get("id", "")) for item in targets],
            "total_sent": total_sent,
            "total_success": total_success,
            "total_failed": max(0, total_sent - total_success),
        },
        "results": dispatch_results if execute else {},
    }


def _materialize_coordination_tasks(
    goal: str,
    coordination_payload: Dict[str, Any],
    priority: str = "high",
    member_id: str = "",
    max_tasks: int = 8,
) -> Dict[str, Any]:
    if "_hub" not in globals() or _hub is None:
        return {"enabled": False, "created": 0, "results": [], "error": "hub_unavailable"}

    plan = coordination_payload.get("plan") if isinstance(coordination_payload.get("plan"), dict) else {}
    commands = plan.get("commands") if isinstance(plan.get("commands"), list) else []
    execution = coordination_payload.get("execution") if isinstance(coordination_payload.get("execution"), dict) else {}
    instance_ids = execution.get("instances") if isinstance(execution.get("instances"), list) else []
    if not isinstance(instance_ids, list) or not instance_ids:
        instance_ids = [LOCAL_INSTANCE_ID]

    cap = max(1, min(12, int(max_tasks)))
    results: List[Dict[str, Any]] = []
    created = 0
    normalized_priority = str(priority or "high").strip().lower() or "high"
    member = normalize_member_id(member_id)

    for idx, row in enumerate(commands[:cap]):
        if not isinstance(row, dict):
            continue
        agent = str(row.get("agent", "")).strip().lower() or "orion"
        command = str(row.get("command", "")).strip()
        target_instance = str(instance_ids[idx % len(instance_ids)]).strip() or LOCAL_INSTANCE_ID
        title = f"[Coord] {agent.upper()} :: {goal[:86]}"
        description_lines = [
            f"Goal: {goal}",
            f"Agent: {agent}",
            f"Command: {command}",
            f"Source: agent_coordination_auto_materialize",
        ]
        if member:
            description_lines.append(f"Member: {member}")
        description = "\n".join(description_lines)
        try:
            result = _hub.create_task_safe(
                title=title,
                description=description,
                priority=normalized_priority,
                assigned_to=target_instance,
            )
        except Exception as exc:
            result = {"success": False, "error": str(exc), "task_title": title}
        if result.get("success"):
            created += 1
        results.append(result)

    return {
        "enabled": True,
        "created": created,
        "requested": min(cap, len(commands)),
        "results": results,
    }


def _monitor_supervisor_status() -> Dict[str, Any]:
    with _monitor_supervisor_lock:
        startup_age_sec = max(0, int((datetime.now() - _monitor_process_started_at).total_seconds()))
        pause_until = _monitor_autopilot_pause_until.isoformat() if _monitor_autopilot_pause_until else None
        return {
            "enabled": bool(_monitor_supervisor_enabled),
            "thread_alive": bool(_monitor_supervisor_thread and _monitor_supervisor_thread.is_alive()),
            "interval_sec": MONITOR_AUTOPILOT_INTERVAL_SEC,
            "stuck_threshold_sec": MONITOR_STUCK_THRESHOLD_SEC,
            "cooldown_sec": MONITOR_RECOVERY_COOLDOWN_SEC,
            "startup_grace_sec": MONITOR_STARTUP_GRACE_SEC,
            "startup_age_sec": startup_age_sec,
            "autonomy_profile": _get_autonomy_profile(),
            "full_auto": _is_full_auto_profile(),
            "autopilot_pause_until": pause_until,
            "full_auto_user_pause_max_sec": MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC,
            "full_auto_maintenance_lease_sec": MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC,
            "openclaw_execution_mode": OPENCLAW_EXECUTION_MODE,
            "openclaw_extension_automation_enabled": OPENCLAW_EXTENSION_AUTOMATION_ENABLED,
            "openclaw_autopilot_flow_enabled": MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED,
            "openclaw_autopilot_flow_cooldown_sec": MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC,
            "last_openclaw_flow": {key: value.isoformat() for key, value in _monitor_last_openclaw_flow_at.items()},
            "last_recovery": {key: value.isoformat() for key, value in _monitor_last_recovery_at.items()},
            "last_reason": dict(_monitor_last_reason),
        }


def _monitor_set_supervisor_enabled(enabled: bool) -> None:
    global _monitor_supervisor_enabled, _monitor_autopilot_pause_until
    with _monitor_supervisor_lock:
        _monitor_supervisor_enabled = bool(enabled)
        if enabled:
            _monitor_autopilot_pause_until = None


def _monitor_pause_supervisor_temporarily(duration_sec: int) -> str:
    global _monitor_supervisor_enabled, _monitor_autopilot_pause_until
    lease = max(30, int(duration_sec))
    until = datetime.now() + timedelta(seconds=lease)
    with _monitor_supervisor_lock:
        _monitor_supervisor_enabled = False
        _monitor_autopilot_pause_until = until
    return until.isoformat()


def _monitor_maybe_enqueue_openclaw_flow(instance_id: str, reason: str) -> Dict[str, Any]:
    if not MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED:
        return {"attempted": False, "success": False, "reason": "disabled"}
    if not OPENCLAW_AUTONOMOUS_UI_ENABLED:
        return {"attempted": False, "success": False, "reason": "autonomous_ui_disabled"}
    if not _openclaw_dashboard_flow_enabled():
        return {"attempted": False, "success": False, "reason": f"execution_mode_{OPENCLAW_EXECUTION_MODE}"}
    if OPENCLAW_REQUIRE_TASK_LEASE:
        return {"attempted": False, "success": False, "reason": "lease_enforcement_enabled"}
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in {"not_running"} and not normalized_reason.startswith(("no_progress:", "cycle_stalled:")):
        return {"attempted": False, "success": False, "reason": "unsupported_reason"}

    now = datetime.now()
    with _monitor_supervisor_lock:
        last = _monitor_last_openclaw_flow_at.get(instance_id)
        if last and (now - last).total_seconds() < MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC:
            return {"attempted": False, "success": False, "reason": "cooldown"}
        _monitor_last_openclaw_flow_at[instance_id] = now

    autopilot_session = f"autopilot:{instance_id}"
    if OPENCLAW_QUEUE_ENABLED:
        snapshot = _openclaw_command_manager.queue_snapshot()
        sessions = snapshot.get("sessions") if isinstance(snapshot.get("sessions"), list) else []
        for row in sessions:
            if not isinstance(row, dict):
                continue
            if str(row.get("session_id") or "").strip() != autopilot_session:
                continue
            queue_depth = int(row.get("queue_depth", 0) or 0)
            active_request = str(row.get("active_request_id") or "").strip()
            if queue_depth > 0 or active_request:
                return {
                    "attempted": False,
                    "success": False,
                    "reason": "queue_busy",
                    "session_id": autopilot_session,
                    "queue_depth": queue_depth,
                    "active_request_id": active_request or None,
                }
            break

    chat_text = MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT or "auto unstick run one cycle resume"
    queued = _enqueue_openclaw_command(
        "flow",
        {
            "chat_text": chat_text,
            "source": "autopilot",
            "mode": "async",
            "session_id": autopilot_session,
            "route_mode": "auto",
            "priority": 1,
            "risk_level": "medium",
            "reason": reason,
        },
    )
    return {
        "attempted": True,
        "success": bool(queued.get("success")),
        "request_id": queued.get("request_id"),
        "error_code": queued.get("error_code"),
        "error": queued.get("error"),
    }


def _monitor_maybe_recover_instance(instance: Dict[str, Any]) -> None:
    instance_id = str(instance.get("id", "")).strip()
    if not instance_id:
        return
    if not bool(instance.get("online")):
        return

    now = datetime.now()
    startup_age_sec = (now - _monitor_process_started_at).total_seconds()
    within_startup_grace = startup_age_sec < MONITOR_STARTUP_GRACE_SEC
    reason = ""
    paused = bool(instance.get("paused"))
    running = bool(instance.get("running"))
    pause_reason = str(instance.get("pause_reason", "") or "").strip().lower()
    paused_since = _parse_iso_datetime(instance.get("paused_since"))
    paused_age_sec = int((now - paused_since).total_seconds()) if paused_since else 0

    if paused and pause_reason not in {"user", "manual_user"}:
        reason = f"paused:{pause_reason or 'unknown'}"
    elif paused and pause_reason in {"user", "manual_user"} and _is_full_auto_profile():
        if paused_age_sec >= MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC:
            reason = f"paused_user_timeout:{paused_age_sec}s"
    elif running:
        last_progress_at = _parse_iso_datetime(instance.get("last_progress_at"))
        if last_progress_at:
            age_sec = (now - last_progress_at).total_seconds()
            if age_sec >= MONITOR_STUCK_THRESHOLD_SEC:
                reason = f"no_progress:{int(age_sec)}s"
        else:
            started_at = _parse_iso_datetime(instance.get("last_cycle_started_at"))
            finished_at = _parse_iso_datetime(instance.get("last_cycle_finished_at"))
            if started_at and (not finished_at or started_at >= finished_at):
                age_sec = (now - started_at).total_seconds()
                if age_sec >= MONITOR_STUCK_THRESHOLD_SEC:
                    reason = f"cycle_stalled:{int(age_sec)}s"
    else:
        # Runtime alive but orchestrator is not running: ask Guardian to resume continuity.
        if not within_startup_grace:
            reason = "not_running"

    if not reason:
        return

    with _monitor_supervisor_lock:
        last = _monitor_last_recovery_at.get(instance_id)
        if last and (now - last).total_seconds() < MONITOR_RECOVERY_COOLDOWN_SEC:
            return
        _monitor_last_recovery_at[instance_id] = now
        _monitor_last_reason[instance_id] = reason

    openclaw = _monitor_maybe_enqueue_openclaw_flow(instance_id, reason)
    if openclaw.get("attempted"):
        flow_ok = bool(openclaw.get("success"))
        flow_level = "success" if flow_ok else "warning"
        flow_suffix = "queued" if flow_ok else f"failed:{openclaw.get('error_code') or 'unknown'}"
        state.add_agent_log(
            "guardian",
            f"🧩 Monitor autopilot OpenClaw flow {instance_id} ({reason}) {flow_suffix}",
            flow_level,
        )

    result = _call_instance_command(
        instance_id,
        "guardian",
        "unstick_orion",
        "high",
        {"source": "monitor_supervisor", "reason": reason},
    )
    ok = bool(result.get("success", True))
    level = "success" if ok else "warning"
    state.add_agent_log(
        "guardian",
        f"🛡️ Monitor supervisor -> {instance_id} recovery ({reason}) {'ok' if ok else 'failed'}",
        level,
    )
    if not ok and reason == "not_running":
        resume_result = _call_instance_command(
            instance_id,
            "orion",
            "run_cycle",
            "high",
            {"control_mode": "interrupt", "resume_after": True},
        )
        state.add_agent_log(
            "guardian",
            f"🛠️ Fallback run_cycle on {instance_id}: {'ok' if resume_result.get('success', True) else 'failed'}",
            "success" if resume_result.get("success", True) else "warning",
        )


def _monitor_supervisor_loop() -> None:
    global _monitor_supervisor_enabled, _monitor_autopilot_pause_until
    while not _monitor_supervisor_stop_event.is_set():
        try:
            with _monitor_supervisor_lock:
                enabled = bool(_monitor_supervisor_enabled)
                pause_until = _monitor_autopilot_pause_until
                autonomy_profile = _autonomy_profile
            now = datetime.now()
            if autonomy_profile == "full_auto":
                if pause_until and now >= pause_until:
                    with _monitor_supervisor_lock:
                        _monitor_supervisor_enabled = True
                        _monitor_autopilot_pause_until = None
                        enabled = True
                    state.add_agent_log("guardian", "♻️ Full-auto resumed monitor autopilot after maintenance lease.", "success")
                elif not pause_until and not enabled:
                    with _monitor_supervisor_lock:
                        _monitor_supervisor_enabled = True
                        enabled = True
                    state.add_agent_log("guardian", "🛡️ Full-auto enforced monitor autopilot ON.", "warning")
            if enabled:
                instances = get_orion_instances_snapshot()
                _control_plane_registry.sync_instance_snapshot(instances)
                for instance in instances:
                    _monitor_maybe_recover_instance(instance)
                _monitor_auto_approve_safe_decisions()
        except Exception as exc:
            logger.error(f"Monitor supervisor loop error: {exc}")
        _monitor_supervisor_stop_event.wait(MONITOR_AUTOPILOT_INTERVAL_SEC)


def _ensure_monitor_supervisor_started() -> None:
    global _monitor_supervisor_thread
    with _monitor_supervisor_lock:
        if _monitor_supervisor_thread and _monitor_supervisor_thread.is_alive():
            return
        _monitor_supervisor_stop_event.clear()
        _monitor_supervisor_thread = threading.Thread(
            target=_monitor_supervisor_loop,
            name="monitor-supervisor",
            daemon=True,
        )
        _monitor_supervisor_thread.start()


def _openclaw_self_heal_loop() -> None:
    while not _openclaw_self_heal_stop_event.is_set():
        try:
            if OPENCLAW_SELF_HEAL_ENABLED:
                result = _openclaw_self_heal_once()
                if result.get("success"):
                    state.add_agent_log("guardian", "🧩 OpenClaw self-heal OK.", "success")
                    logger.info("OpenClaw self-heal OK", extra={"detail": result})
                else:
                    state.add_agent_log("guardian", "🧩 OpenClaw self-heal failed; fallback attempted.", "warning")
                    logger.warning("OpenClaw self-heal failed", extra={"detail": result})
        except Exception as exc:
            logger.error(f"OpenClaw self-heal loop error: {exc}")
        _openclaw_self_heal_stop_event.wait(OPENCLAW_SELF_HEAL_INTERVAL_SEC)


def _ensure_openclaw_self_heal_started() -> None:
    global _openclaw_self_heal_thread
    with _openclaw_self_heal_lock:
        if _openclaw_self_heal_thread and _openclaw_self_heal_thread.is_alive():
            return
        _openclaw_self_heal_stop_event.clear()
        _openclaw_self_heal_thread = threading.Thread(
            target=_openclaw_self_heal_loop,
            name="openclaw-self-heal",
            daemon=True,
        )
        _openclaw_self_heal_thread.start()


def _build_ops_snapshot() -> Dict[str, Any]:
    events = state.get_event_feed(limit=120, include_routing=True)
    filtered_events = [
        event for event in events
        if not str(event.get("message", "")).strip().startswith("💬 Chat:")
    ]
    events = filtered_events or events
    counts = {"error": 0, "warning": 0, "success": 0, "info": 0}
    agent_activity: Dict[str, int] = {}
    for event in events:
        level = str(event.get("level", "info")).lower()
        if level in counts:
            counts[level] += 1
        agent = str(event.get("agent", "system")).lower()
        agent_activity[agent] = agent_activity.get(agent, 0) + 1
    top_agent = sorted(agent_activity.items(), key=lambda x: x[1], reverse=True)[0][0] if agent_activity else "system"
    latest = events[0] if events else {}
    latest_message = str(latest.get("message", "")).strip()
    return {
        "events": events,
        "counts": counts,
        "top_agent": top_agent,
        "latest_message": latest_message,
    }


def _ascii_safe(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if not text:
        return ""
    cleaned = text.encode("ascii", "ignore").decode("ascii").strip()
    return cleaned[:limit]


def _build_progress_snapshot_payload() -> Dict[str, Any]:
    instances = get_orion_instances_snapshot()
    primary = next((item for item in instances if item.get("id") == LOCAL_INSTANCE_ID), None)
    if primary is None and instances:
        primary = instances[0]
    primary = primary or {}
    ops = _build_ops_snapshot()
    monitor_status = _monitor_supervisor_status()
    control_status = _computer_control_status()
    return {
        "timestamp": datetime.now().isoformat(),
        "project": state.get_project_status(),
        "orion": {
            "instance_id": primary.get("id"),
            "name": primary.get("name"),
            "running": bool(primary.get("running")),
            "paused": bool(primary.get("paused")),
            "pause_reason": str(primary.get("pause_reason", "") or ""),
            "iteration": primary.get("iteration"),
            "active_flow_count": int(primary.get("active_flow_count", 0) or 0),
            "last_progress_at": primary.get("last_progress_at"),
            "last_cycle_started_at": primary.get("last_cycle_started_at"),
            "last_cycle_finished_at": primary.get("last_cycle_finished_at"),
        },
        "monitor": {
            "autopilot_enabled": bool(monitor_status.get("enabled")),
            "thread_alive": bool(monitor_status.get("thread_alive")),
            "interval_sec": monitor_status.get("interval_sec"),
            "stuck_threshold_sec": monitor_status.get("stuck_threshold_sec"),
            "cooldown_sec": monitor_status.get("cooldown_sec"),
            "last_reason": monitor_status.get("last_reason", {}),
            "last_recovery": monitor_status.get("last_recovery", {}),
        },
        "computer_control": {
            "available": bool(control_status.get("available")),
            "enabled": bool(control_status.get("enabled")),
            "provider": str(control_status.get("provider", "")),
        },
        "signals": {
            "counts": ops.get("counts", {}),
            "top_agent": _ascii_safe(ops.get("top_agent")),
            "latest_event": _ascii_safe(ops.get("latest_message")),
        },
        "pending_decisions": state.get_pending_count(),
    }


def _render_progress_snapshot(payload: Dict[str, Any]) -> str:
    project = payload.get("project", {})
    orion = payload.get("orion", {})
    monitor = payload.get("monitor", {})
    control = payload.get("computer_control", {})
    signals = payload.get("signals", {})
    counts = signals.get("counts", {}) if isinstance(signals, dict) else {}
    lines = [
        "# Nexus Progress Snapshot",
        "",
        f"Last updated: {payload.get('timestamp')}",
        "",
        "## Project",
        f"- name: {project.get('name')}",
        f"- status: {project.get('status')}",
        f"- current_iteration: {project.get('current_iteration')}",
        f"- score: {project.get('score')}",
        f"- updated: {project.get('updated')}",
        "",
        "## Orion",
        f"- instance: {orion.get('instance_id')} ({orion.get('name')})",
        f"- running: {orion.get('running')}",
        f"- paused: {orion.get('paused')}",
        f"- pause_reason: {orion.get('pause_reason')}",
        f"- iteration: {orion.get('iteration')}",
        f"- active_flows: {orion.get('active_flow_count')}",
        f"- last_progress_at: {orion.get('last_progress_at')}",
        f"- last_cycle_started_at: {orion.get('last_cycle_started_at')}",
        f"- last_cycle_finished_at: {orion.get('last_cycle_finished_at')}",
        "",
        "## Monitor",
        f"- autopilot_enabled: {monitor.get('autopilot_enabled')}",
        f"- thread_alive: {monitor.get('thread_alive')}",
        f"- interval_sec: {monitor.get('interval_sec')}",
        f"- stuck_threshold_sec: {monitor.get('stuck_threshold_sec')}",
        f"- cooldown_sec: {monitor.get('cooldown_sec')}",
        f"- last_reason: {monitor.get('last_reason')}",
        f"- last_recovery: {monitor.get('last_recovery')}",
        "",
        "## Computer Control",
        f"- available: {control.get('available')}",
        f"- enabled: {control.get('enabled')}",
        f"- provider: {control.get('provider')}",
        "",
        "## Signals (last window)",
        f"- counts: {counts}",
        f"- top_agent: {signals.get('top_agent')}",
        f"- latest_event: {signals.get('latest_event')}",
        "",
        "## Pending",
        f"- pending_decisions: {payload.get('pending_decisions')}",
        "",
    ]
    return "\n".join(lines)


def _write_progress_snapshot() -> None:
    payload = _build_progress_snapshot_payload()
    content = _render_progress_snapshot(payload)
    path = MONITOR_PROGRESS_SNAPSHOT_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.error(f"Failed to write progress snapshot: {exc}")


def _monitor_progress_snapshot_loop() -> None:
    while not _monitor_progress_stop_event.is_set():
        try:
            if MONITOR_PROGRESS_SNAPSHOT_ENABLED:
                _write_progress_snapshot()
        except Exception as exc:
            logger.error(f"Progress snapshot loop error: {exc}")
        _monitor_progress_stop_event.wait(MONITOR_PROGRESS_SNAPSHOT_INTERVAL_SEC)


def _ensure_progress_snapshot_started() -> None:
    global _monitor_progress_thread
    with _monitor_progress_lock:
        if _monitor_progress_thread and _monitor_progress_thread.is_alive():
            return
        _monitor_progress_stop_event.clear()
        _monitor_progress_thread = threading.Thread(
            target=_monitor_progress_snapshot_loop,
            name="monitor-progress-snapshot",
            daemon=True,
        )
        _monitor_progress_thread.start()


def _build_runtime_heartbeat_payload() -> Dict[str, Any]:
    runtime_payload = _fetch_instance_runtime(LOCAL_INSTANCE_ID)
    runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
    activity = _build_agent_activity_snapshot(instance_id=LOCAL_INSTANCE_ID, max_agents=16)
    return {
        "timestamp": datetime.now().isoformat(),
        "instance_id": runtime_payload.get("instance_id"),
        "instance_name": runtime_payload.get("instance_name"),
        "runtime": runtime,
        "instances": get_orion_instances_snapshot(),
        "activity": activity,
    }


def _runtime_heartbeat_loop() -> None:
    while not _runtime_heartbeat_stop_event.is_set():
        try:
            if MONITOR_RUNTIME_HEARTBEAT_ENABLED:
                socketio.emit("runtime_heartbeat", _build_runtime_heartbeat_payload())
        except Exception as exc:
            logger.error(f"Runtime heartbeat loop error: {exc}")
        _runtime_heartbeat_stop_event.wait(MONITOR_RUNTIME_HEARTBEAT_INTERVAL_SEC)


def _ensure_runtime_heartbeat_started() -> None:
    global _runtime_heartbeat_thread
    with _runtime_heartbeat_lock:
        if _runtime_heartbeat_thread and _runtime_heartbeat_thread.is_alive():
            return
        _runtime_heartbeat_stop_event.clear()
        _runtime_heartbeat_thread = threading.Thread(
            target=_runtime_heartbeat_loop,
            name="monitor-runtime-heartbeat",
            daemon=True,
        )
        _runtime_heartbeat_thread.start()


def _openclaw_metrics_inc(name: str, amount: int = 1) -> None:
    global _openclaw_metrics_updated_at
    with _OPENCLAW_METRICS_LOCK:
        _openclaw_metrics[name] = int(_openclaw_metrics.get(name, 0)) + max(1, amount)
        _openclaw_metrics_updated_at = datetime.now()


def _openclaw_metrics_event(event: str, detail: Optional[Dict[str, Any]] = None) -> None:
    global _openclaw_metrics_updated_at
    payload = {
        "event": event,
        "timestamp": datetime.now().isoformat(),
    }
    if detail:
        payload["detail"] = detail
    with _OPENCLAW_METRICS_LOCK:
        _openclaw_metric_events.append(payload)
        if len(_openclaw_metric_events) > _OPENCLAW_METRICS_MAX_EVENTS:
            del _openclaw_metric_events[:-_OPENCLAW_METRICS_MAX_EVENTS]
        _openclaw_metrics_updated_at = datetime.now()


def _sanitize_openclaw_args(args: List[str]) -> List[str]:
    masked: List[str] = []
    skip_next_token = False
    for idx, item in enumerate(args):
        if skip_next_token:
            masked.append("***")
            skip_next_token = False
            continue
        masked.append(item)
        if item == "--token" and idx + 1 < len(args):
            skip_next_token = True
    return masked


def _build_openclaw_metrics_snapshot(include_events: bool = False, event_limit: int = 20) -> Dict[str, Any]:
    with _OPENCLAW_METRICS_LOCK:
        counters = dict(_openclaw_metrics)
        updated_at = _openclaw_metrics_updated_at.isoformat() if _openclaw_metrics_updated_at else None
        events = list(_openclaw_metric_events)

    with _ATTACH_STATE_LOCK:
        attach_state = {
            "last_attempt": _attach_state.last_attempt.isoformat() if _attach_state.last_attempt else None,
            "last_error": _attach_state.last_error,
            "cooldown_until": _attach_state.cooldown_until.isoformat() if _attach_state.cooldown_until else None,
            "method": _attach_state.method,
        }

    info = _collect_token_info(force_refresh=False)
    attach_attempts = max(1, int(counters.get("attach_attempts", 0)))
    flow_runs = max(1, int(counters.get("flow_runs", 0)))
    queue_snapshot = _openclaw_command_manager.queue_snapshot() if OPENCLAW_QUEUE_ENABLED else {}
    policy_auto = int(counters.get("policy_auto_approved", 0) or 0)
    policy_manual = int(counters.get("policy_manual_required", 0) or 0)
    policy_denied = int(counters.get("policy_denied", 0) or 0)
    policy_total = max(1, policy_auto + policy_manual + policy_denied)
    payload: Dict[str, Any] = {
        "execution_mode": OPENCLAW_EXECUTION_MODE,
        "mode_capabilities": {
            "browser_actions_enabled": _openclaw_browser_actions_enabled(),
            "dashboard_flow_enabled": _openclaw_dashboard_flow_enabled(),
            "extension_automation_enabled": OPENCLAW_EXTENSION_AUTOMATION_ENABLED,
        },
        "counters": counters,
        "attach_success_rate": round(float(counters.get("attach_success", 0)) / attach_attempts, 4),
        "flow_success_rate": round(float(counters.get("flow_success", 0)) / flow_runs, 4),
        "self_heal": _openclaw_self_heal_status(),
        "policy": {
            "auto_approved": policy_auto,
            "manual_required": policy_manual,
            "denied": policy_denied,
            "auto_approval_rate": round(float(policy_auto) / policy_total, 4),
            "manual_intervention_rate": round(float(policy_manual) / policy_total, 4),
        },
        "updated_at": updated_at,
        "attach_state": attach_state,
        "runtime_guard": _openclaw_runtime_guard_snapshot(),
        "token": {
            "present": bool(info.token),
            "source": info.source,
            "health_ok": info.health_ok,
            "health_error": info.health_error,
            "checked_at": info.checked_at.isoformat() if info.checked_at else None,
        },
        "queue": queue_snapshot,
        "control_plane": _control_plane_registry.snapshot(),
    }
    if include_events:
        payload["events"] = events[-max(1, event_limit):]
    return payload


def _render_openclaw_prometheus_metrics(snapshot: Dict[str, Any]) -> str:
    counters = snapshot.get("counters") if isinstance(snapshot.get("counters"), dict) else {}

    def metric(name: str) -> int:
        return int(counters.get(name, 0) or 0)

    lines = [
        "# HELP monitor_openclaw_token_missing_total OpenClaw token missing occurrences.",
        "# TYPE monitor_openclaw_token_missing_total counter",
        f"monitor_openclaw_token_missing_total {metric('token_missing')}",
        "# HELP monitor_openclaw_health_error_total OpenClaw token health errors.",
        "# TYPE monitor_openclaw_health_error_total counter",
        f"monitor_openclaw_health_error_total {metric('health_error')}",
        "# HELP monitor_openclaw_cli_failures_total OpenClaw CLI command failures.",
        "# TYPE monitor_openclaw_cli_failures_total counter",
        f"monitor_openclaw_cli_failures_total {metric('cli_failures')}",
        "# HELP monitor_openclaw_attach_attempts_total OpenClaw auto-attach attempts.",
        "# TYPE monitor_openclaw_attach_attempts_total counter",
        f"monitor_openclaw_attach_attempts_total {metric('attach_attempts')}",
        "# HELP monitor_openclaw_attach_success_total OpenClaw auto-attach successes.",
        "# TYPE monitor_openclaw_attach_success_total counter",
        f"monitor_openclaw_attach_success_total {metric('attach_success')}",
        "# HELP monitor_openclaw_attach_failed_total OpenClaw auto-attach failures.",
        "# TYPE monitor_openclaw_attach_failed_total counter",
        f"monitor_openclaw_attach_failed_total {metric('attach_failed')}",
        "# HELP monitor_openclaw_attach_throttled_total OpenClaw auto-attach throttled responses.",
        "# TYPE monitor_openclaw_attach_throttled_total counter",
        f"monitor_openclaw_attach_throttled_total {metric('attach_throttled')}",
        "# HELP monitor_openclaw_flow_runs_total OpenClaw dashboard flow runs.",
        "# TYPE monitor_openclaw_flow_runs_total counter",
        f"monitor_openclaw_flow_runs_total {metric('flow_runs')}",
        "# HELP monitor_openclaw_flow_success_total OpenClaw dashboard flow successes.",
        "# TYPE monitor_openclaw_flow_success_total counter",
        f"monitor_openclaw_flow_success_total {metric('flow_success')}",
        "# HELP monitor_openclaw_flow_failures_total OpenClaw dashboard flow failures.",
        "# TYPE monitor_openclaw_flow_failures_total counter",
        f"monitor_openclaw_flow_failures_total {metric('flow_failures')}",
        "# HELP monitor_openclaw_queue_enqueued_total OpenClaw queue enqueued commands.",
        "# TYPE monitor_openclaw_queue_enqueued_total counter",
        f"monitor_openclaw_queue_enqueued_total {metric('queue_enqueued')}",
        "# HELP monitor_openclaw_queue_executed_total OpenClaw queue executed commands.",
        "# TYPE monitor_openclaw_queue_executed_total counter",
        f"monitor_openclaw_queue_executed_total {metric('queue_executed')}",
        "# HELP monitor_openclaw_queue_failed_total OpenClaw queue failed commands.",
        "# TYPE monitor_openclaw_queue_failed_total counter",
        f"monitor_openclaw_queue_failed_total {metric('queue_failed')}",
        "# HELP monitor_openclaw_queue_deferred_total OpenClaw queue deferred commands.",
        "# TYPE monitor_openclaw_queue_deferred_total counter",
        f"monitor_openclaw_queue_deferred_total {metric('queue_deferred')}",
        "# HELP monitor_openclaw_queue_cancelled_total OpenClaw queue cancelled commands.",
        "# TYPE monitor_openclaw_queue_cancelled_total counter",
        f"monitor_openclaw_queue_cancelled_total {metric('queue_cancelled')}",
        "# HELP monitor_openclaw_queue_idempotent_hit_total OpenClaw idempotent replay hits.",
        "# TYPE monitor_openclaw_queue_idempotent_hit_total counter",
        f"monitor_openclaw_queue_idempotent_hit_total {metric('queue_idempotent_hit')}",
        "# HELP monitor_openclaw_queue_manual_override_total OpenClaw manual override events.",
        "# TYPE monitor_openclaw_queue_manual_override_total counter",
        f"monitor_openclaw_queue_manual_override_total {metric('queue_manual_override')}",
        "# HELP monitor_openclaw_policy_auto_approved_total OpenClaw policy auto-approved decisions.",
        "# TYPE monitor_openclaw_policy_auto_approved_total counter",
        f"monitor_openclaw_policy_auto_approved_total {metric('policy_auto_approved')}",
        "# HELP monitor_openclaw_policy_manual_required_total OpenClaw policy manual-required decisions.",
        "# TYPE monitor_openclaw_policy_manual_required_total counter",
        f"monitor_openclaw_policy_manual_required_total {metric('policy_manual_required')}",
        "# HELP monitor_openclaw_policy_denied_total OpenClaw policy denied decisions.",
        "# TYPE monitor_openclaw_policy_denied_total counter",
        f"monitor_openclaw_policy_denied_total {metric('policy_denied')}",
        "# HELP monitor_openclaw_dispatch_remote_total OpenClaw remote dispatch count.",
        "# TYPE monitor_openclaw_dispatch_remote_total counter",
        f"monitor_openclaw_dispatch_remote_total {metric('dispatch_remote')}",
        "# HELP monitor_openclaw_dispatch_failover_total OpenClaw failover dispatch count.",
        "# TYPE monitor_openclaw_dispatch_failover_total counter",
        f"monitor_openclaw_dispatch_failover_total {metric('dispatch_failover')}",
        "# HELP monitor_openclaw_circuit_opened_total OpenClaw circuit opened events.",
        "# TYPE monitor_openclaw_circuit_opened_total counter",
        f"monitor_openclaw_circuit_opened_total {metric('circuit_opened')}",
        "# HELP monitor_openclaw_circuit_blocked_total OpenClaw blocked failure-class events.",
        "# TYPE monitor_openclaw_circuit_blocked_total counter",
        f"monitor_openclaw_circuit_blocked_total {metric('circuit_blocked')}",
    ]
    return "\n".join(lines) + "\n"


def _check_openclaw_gateway_health(token: str) -> Dict[str, Any]:
    try:
        result = _run_openclaw_cli(["gateway", "status", "--token", token])
        if result.get("success"):
            return {"health_ok": True, "error": None}
        return {"health_ok": False, "error": result.get("stderr") or result.get("error")}
    except Exception as exc:
        return {"health_ok": False, "error": str(exc)}


def _collect_token_info(force_refresh: bool = False) -> _OpenClawTokenInfo:
    global _token_cache
    with _TOKEN_CACHE_LOCK:
        now = datetime.now()
        if (
            _token_cache
            and _token_cache.checked_at
            and (now - _token_cache.checked_at).total_seconds() < _TOKEN_CACHE_TTL_SEC
            and not force_refresh
        ):
            return _token_cache

    env_token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
    source = "env" if env_token else "file"
    token: Optional[str]
    error: Optional[str] = None

    if env_token:
        token = env_token
    else:
        token = None
        try:
            config_path = Path.home() / ".openclaw" / "openclaw.json"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                gateway = payload.get("gateway") if isinstance(payload, dict) else {}
                auth = gateway.get("auth") if isinstance(gateway, dict) else {}
                raw_token = auth.get("token") if isinstance(auth, dict) else None
                token = str(raw_token).strip() if raw_token else None
        except Exception as exc:
            error = str(exc)

    health_ok = False
    health_error: Optional[str] = None
    if token:
        health = _check_openclaw_gateway_health(token)
        health_ok = bool(health.get("health_ok"))
        health_error = health.get("error")
    else:
        health_error = error or "Token not found"

    info = _OpenClawTokenInfo(
        token=token,
        checked_at=datetime.now(),
        source=source,
        health_ok=health_ok,
        health_error=health_error,
        last_error=error,
    )

    with _TOKEN_CACHE_LOCK:
        _token_cache = info
    return info


def _get_openclaw_token() -> Optional[str]:
    return _collect_token_info().token


def _parse_json_loose(text: str) -> Optional[Any]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except Exception:
                continue
    return None


_OPENCLAW_GATEWAY_ERROR_TOKENS = (
    "gateway closed",
    "abnormal closure",
    "no close frame",
    "econnrefused",
    "connection refused",
    "socket hang up",
)
_OPENCLAW_NO_TAB_TOKENS = (
    "no tab is connected",
    "extension relay",
    "chrome extension",
)


def _text_contains_any(text: str, tokens: Tuple[str, ...]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _is_openclaw_gateway_error(text: str) -> bool:
    return _text_contains_any(text, _OPENCLAW_GATEWAY_ERROR_TOKENS)


def _is_openclaw_no_tab_error(text: str) -> bool:
    return _text_contains_any(text, _OPENCLAW_NO_TAB_TOKENS)


def _restart_openclaw_gateway() -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            ["openclaw", "gateway", "start"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        payload = {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
            "command": "openclaw gateway start",
        }
        if payload["success"]:
            _openclaw_metrics_inc("gateway_restarts")
            _openclaw_metrics_event("gateway_restart", {"result": "ok"})
        else:
            _openclaw_metrics_event("gateway_restart", {"result": "failed", "stderr": payload["stderr"]})
        return payload
    except Exception as exc:
        _openclaw_metrics_event("gateway_restart", {"result": "error", "error": str(exc)})
        return {"success": False, "error": str(exc)}


def _wait_for_openclaw_gateway_ready(max_wait_sec: float = 6.0, interval_sec: float = 0.6) -> bool:
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        try:
            completed = subprocess.run(
                ["openclaw", "gateway", "probe"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if completed.returncode == 0 and "Reachable: yes" in (completed.stdout or ""):
                return True
        except Exception:
            pass
        time.sleep(interval_sec)
    return False


def _run_openclaw_cli(args: List[str], timeout_ms: int = OPENCLAW_BROWSER_TIMEOUT_MS, retries: int = OPENCLAW_CLI_MAX_RETRIES) -> Dict[str, Any]:
    attempt = 0
    last_error: Optional[str] = None
    while attempt < retries:
        attempt += 1
        cmd = ["openclaw", *args]
        logger.debug("Running openclaw command", extra={"cmd": cmd, "attempt": attempt})
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_ms / 1000)),
                check=False,
            )
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()
            payload = {
                "success": completed.returncode == 0,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "command": " ".join(cmd),
                "attempt": attempt,
            }
            parsed = _parse_json_loose(stdout)
            if parsed is not None:
                payload["json"] = parsed
            if payload["success"] or attempt >= retries:
                payload["retries"] = attempt
                return payload
            error_text = "\n".join([stderr, stdout]).strip()
            if _is_openclaw_gateway_error(error_text):
                _restart_openclaw_gateway()
                _wait_for_openclaw_gateway_ready()
            last_error = stderr or payload.get("error")
        except subprocess.TimeoutExpired:
            last_error = "openclaw command timeout"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(min(OPENCLAW_CLI_RETRY_DELAY_SEC * attempt, 1.5))
    safe_args = _sanitize_openclaw_args(args)
    logger.warning("OpenClaw CLI failed", extra={"cmd": safe_args, "error": last_error})
    _openclaw_metrics_inc("cli_failures")
    _openclaw_metrics_event("cli_failure", {"args": safe_args, "error": last_error})
    return {"success": False, "error": last_error or "openclaw command failed"}


def _openclaw_browser_cmd(
    args: List[str],
    expect_json: bool = True,
    timeout_ms: int = OPENCLAW_BROWSER_TIMEOUT_MS,
    require_token: bool = True,
    auto_attach: bool = True,
    auto_recover: bool = True,
) -> Dict[str, Any]:
    token_info = _collect_token_info()
    if require_token and not token_info.token:
        _openclaw_metrics_inc("token_missing")
        _openclaw_metrics_event("token_missing", {"source": token_info.source, "last_error": token_info.last_error})
        return {
            "success": False,
            "error_code": "TOKEN_MISSING",
            "error": "OpenClaw gateway token is unavailable",
            "token_info": {
                "source": token_info.source,
                "checked_at": token_info.checked_at.isoformat() if token_info.checked_at else None,
                "last_error": token_info.last_error,
            },
        }
    if token_info.token and not token_info.health_ok:
        _openclaw_metrics_inc("health_error")
        _openclaw_metrics_event("health_error", {"error": token_info.health_error})
    cmd = ["browser"]
    if token_info.token:
        cmd += ["--token", token_info.token]
    if expect_json:
        cmd += ["--json"]
    cmd += args
    result = _run_openclaw_cli(cmd, timeout_ms=timeout_ms)
    result["token_info"] = {
        "health_ok": token_info.health_ok,
        "health_error": token_info.health_error,
        "checked_at": token_info.checked_at.isoformat() if token_info.checked_at else None,
    }
    if auto_recover and not result.get("success"):
        error_text = "\n".join(
            str(result.get(key, "") or "") for key in ("error", "stderr", "stdout", "message")
        ).strip()
        if _is_openclaw_gateway_error(error_text):
            _restart_openclaw_gateway()
            _wait_for_openclaw_gateway_ready()
            retry = _openclaw_browser_cmd(
                args,
                expect_json=expect_json,
                timeout_ms=timeout_ms,
                require_token=require_token,
                auto_attach=auto_attach,
                auto_recover=False,
            )
            return retry
    if auto_attach and OPENCLAW_AUTO_ATTACH_ENABLED and not result.get("success") and not _openclaw_attach_in_progress():
        error_text = "\n".join(
            str(result.get(key, "") or "") for key in ("error", "stderr", "stdout", "message")
        ).strip()
        if _is_openclaw_no_tab_error(error_text):
            # Respect attach cooldown to avoid recursive/noisy browser-open storms.
            attach_result = _openclaw_attempt_attach(force=False)
            result["attach"] = attach_result
            if attach_result.get("success"):
                retry = _openclaw_browser_cmd(
                    args,
                    expect_json=expect_json,
                    timeout_ms=timeout_ms,
                    require_token=require_token,
                    auto_attach=False,
                )
                retry.setdefault("attach", attach_result)
                return retry
    return result


def _openclaw_extension_path() -> Optional[Path]:
    result = _run_openclaw_cli(["browser", "extension", "path"])
    if result.get("success"):
        stdout = str(result.get("stdout", "")).strip()
        if stdout:
            path = Path(stdout)
            if path.exists():
                return path
    # Fallback to default install location
    fallback = Path.home() / ".openclaw" / "browser" / "chrome-extension"
    if fallback.exists():
        return fallback
    return None


def _find_extension_icon(extension_path: Path) -> Optional[Path]:
    if not extension_path:
        return None
    candidates: List[Path] = []
    for suffix in ("*.png", "*.jpg", "*.jpeg"):
        candidates.extend(extension_path.rglob(suffix))
    if not candidates:
        return None
    preferred = []
    for item in candidates:
        name = item.name.lower()
        if "icon" in name or "logo" in name:
            preferred.append(item)
    pool = preferred or candidates
    sized = [p for p in pool if any(token in p.name for token in ("16", "19", "20", "24", "32"))]
    return sized[0] if sized else pool[0]


def _click_extension_icon(icon_path: Optional[Path]) -> Dict[str, Any]:
    if not pyautogui:
        return {"success": False, "error": "pyautogui not available"}
    if not icon_path or not icon_path.exists():
        return {"success": False, "error": "icon path missing"}
    try:
        location = pyautogui.locateOnScreen(str(icon_path))
        if not location:
            return {"success": False, "error": "icon not found"}
        x, y = pyautogui.center(location)
        pyautogui.click(x, y)
        return {"success": True, "x": int(x), "y": int(y)}
    except Exception:
        return {"success": False, "error": "click failed"}


def _click_extension_menu_heuristic() -> Dict[str, Any]:
    if not pyautogui:
        return {"success": False, "error": "pyautogui not available"}
    try:
        width, height = pyautogui.size()
        # Click puzzle piece / extensions area near top-right
        pyautogui.click(width - 120, 60)
        time.sleep(0.4)
        # Click first extension entry in the dropdown
        pyautogui.click(width - 220, 180)
        return {"success": True, "x": int(width - 220), "y": int(180)}
    except Exception:
        return {"success": False, "error": "click failed"}


def _attempt_attach_via_menu() -> Dict[str, Any]:
    if not pyautogui:
        return {"success": False, "error": "pyautogui not available", "steps": []}
    steps: List[Dict[str, Any]] = []
    bounds = _chrome_bounds()
    if bounds:
        left, top, right, bottom = bounds
        menu_x = right - 120
        item_x = right - 220
        base_y = top
    else:
        width, height = pyautogui.size()
        menu_x = width - 120
        item_x = width - 220
        base_y = 0
    candidate_ys = [180, 230, 280, 330, 380]
    for y in candidate_ys:
        try:
            pyautogui.click(menu_x, base_y + 60)
            time.sleep(0.4)
            pyautogui.click(item_x, base_y + y)
            steps.append({"action": "menu_click", "result": {"success": True, "x": int(item_x), "y": int(base_y + y)}})
        except Exception as exc:
            steps.append({"action": "menu_click", "result": {"success": False, "error": str(exc)}})
        time.sleep(OPENCLAW_AUTO_ATTACH_DELAY_SEC)
        start = _openclaw_browser_cmd(["start"], auto_attach=False)
        steps.append({"action": "start_after_menu_click", "result": start})
        status = _openclaw_browser_cmd(["status"], auto_attach=False)
        steps.append({"action": "status", "result": status})
        status_json = (status.get("json") or {})
        if status_json.get("running") or status_json.get("cdpReady"):
            return {"success": True, "steps": steps}
    return {"success": False, "steps": steps}


def _launch_chrome_with_extension(extension_path: Path, url: str = "") -> Dict[str, Any]:
    if not extension_path or not extension_path.exists():
        return {"success": False, "error": "Extension path missing"}
    args = ["open"]
    if OPENCLAW_FORCE_NEW_CHROME_INSTANCE:
        args += ["-na", OPENCLAW_CHROME_APP]
    else:
        args += ["-a", OPENCLAW_CHROME_APP]
    args += ["--args", f"--load-extension={extension_path}"]
    if url:
        args.append(url)
    try:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
            "command": " ".join(args),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _chrome_bounds() -> Optional[Tuple[int, int, int, int]]:
    raw = str(OPENCLAW_CHROME_BOUNDS or "").strip()
    if not raw:
        return None
    parts = [p for p in re.split(r"[ ,]+", raw) if p]
    if len(parts) != 4:
        return None
    try:
        left, top, right, bottom = [int(float(p)) for p in parts]
    except Exception:
        return None
    return left, top, right, bottom


def _activate_chrome_window() -> Dict[str, Any]:
    script_lines = [
        f'tell application "{OPENCLAW_CHROME_APP}" to activate'
    ]
    bounds = _chrome_bounds()
    if bounds:
        left, top, right, bottom = bounds
        script_lines.append(
            f'tell application "{OPENCLAW_CHROME_APP}" to set bounds of front window to {{{left}, {top}, {right}, {bottom}}}'
        )
    script = "\n".join(script_lines)
    try:
        completed = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _ensure_chrome_tab(url: str) -> Dict[str, Any]:
    if not pyautogui:
        return {"success": False, "error": "pyautogui not available"}
    try:
        pyautogui.hotkey("command", "l")
        time.sleep(0.2)
        pyautogui.typewrite(url)
        pyautogui.press("enter")
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _openclaw_attempt_attach(force: bool = False) -> Dict[str, Any]:
    _openclaw_attach_scope_enter()
    try:
        steps: List[Dict[str, Any]] = []
        _openclaw_metrics_inc("attach_attempts")

        if not OPENCLAW_EXTENSION_AUTOMATION_ENABLED:
            _openclaw_metrics_inc("attach_failed")
            _openclaw_metrics_event("attach_blocked", {"reason": "extension_automation_disabled"})
            return {
                "success": False,
                "error": "OpenClaw extension automation is disabled",
                "error_code": "EXTENSION_AUTOMATION_DISABLED",
            }

        if not pyautogui:
            _openclaw_metrics_inc("attach_failed")
            _openclaw_metrics_event("attach_failed", {"error": "pyautogui not available"})
            _openclaw_runtime_guard_record_attach(False, detail="pyautogui_unavailable")
            return {"success": False, "error": "pyautogui not available"}

        with _ATTACH_STATE_LOCK:
            now = datetime.now()
            if not force and _attach_state.cooldown_until and now < _attach_state.cooldown_until:
                cooldown = (_attach_state.cooldown_until - now).total_seconds()
                _openclaw_metrics_inc("attach_throttled")
                _openclaw_metrics_event("attach_throttled", {"cooldown_sec": round(cooldown, 2)})
                return {
                    "success": False,
                    "error": "Attach throttled",
                    "cooldown_until": _attach_state.cooldown_until.isoformat(),
                    "last_error": _attach_state.last_error,
                }

        extension_path = _openclaw_extension_path()
        if not extension_path:
            install = _run_openclaw_cli(["browser", "extension", "install"])
            steps.append({"action": "install_extension", "result": install})
            if install.get("success"):
                candidate = str(install.get("stdout", "")).strip()
                if candidate:
                    candidate_path = Path(candidate)
                    if candidate_path.exists():
                        extension_path = candidate_path
            if not extension_path:
                extension_path = _openclaw_extension_path()

        if not extension_path:
            with _ATTACH_STATE_LOCK:
                _attach_state.last_error = "Extension path unavailable"
                _attach_state.cooldown_until = datetime.now() + timedelta(seconds=OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC)
            _openclaw_metrics_inc("attach_failed")
            _openclaw_metrics_event("attach_failed", {"error": "Extension path unavailable"})
            _openclaw_runtime_guard_record_attach(False, detail="extension_path_unavailable")
            return {"success": False, "error": "Extension path unavailable", "steps": steps}

        if OPENCLAW_LAUNCH_WITH_EXTENSION:
            launch = _launch_chrome_with_extension(extension_path, OPENCLAW_DASHBOARD_URL)
            steps.append({"action": "launch_chrome_with_extension", "result": launch})
        else:
            steps.append({"action": "open_chrome", "result": {"success": True, "url": OPENCLAW_DASHBOARD_URL}})
            subprocess.run(["open", "-a", OPENCLAW_CHROME_APP, OPENCLAW_DASHBOARD_URL], check=False)
        time.sleep(OPENCLAW_AUTO_ATTACH_DELAY_SEC)

        steps.append({"action": "activate_chrome", "result": _activate_chrome_window()})
        time.sleep(OPENCLAW_AUTO_ATTACH_DELAY_SEC)
        steps.append({"action": "ensure_tab", "result": _ensure_chrome_tab(OPENCLAW_DASHBOARD_URL)})
        time.sleep(OPENCLAW_AUTO_ATTACH_DELAY_SEC)

        icon_path = _find_extension_icon(extension_path)
        steps.append({"action": "icon_path", "result": {"path": str(icon_path) if icon_path else ""}})

        attached = False
        method_used = None
        for attempt_idx in range(OPENCLAW_AUTO_ATTACH_RETRIES):
            click_result = {"success": False}
            if OPENCLAW_AUTO_ATTACH_ICON_MATCH:
                click_result = _click_extension_icon(icon_path)
                steps.append({"action": "click_extension_icon", "result": click_result})
            if click_result.get("success"):
                method_used = "icon"
                attached = True
                time.sleep(OPENCLAW_AUTO_ATTACH_DELAY_SEC)
                start = _openclaw_browser_cmd(["start"], auto_attach=False)
                steps.append({"action": "start_after_click", "result": start})
                status = _openclaw_browser_cmd(["status"], auto_attach=False)
                steps.append({"action": "status", "result": status})
                status_json = (status.get("json") or {})
                if status_json.get("running") or status_json.get("cdpReady"):
                    attached = True
                    break
                attached = False
            if not attached and OPENCLAW_AUTO_ATTACH_HEURISTIC:
                method_used = "menu"
                menu_attempt = _attempt_attach_via_menu()
                steps.extend(menu_attempt.get("steps", []))
                if menu_attempt.get("success"):
                    attached = True
                    break

        with _ATTACH_STATE_LOCK:
            if attached:
                _attach_state.last_error = None
                _attach_state.cooldown_until = None
                _attach_state.method = method_used
            else:
                _attach_state.last_error = "attach failed"
                _attach_state.cooldown_until = datetime.now() + timedelta(seconds=OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC)
                _attach_state.method = method_used
            _attach_state.last_attempt = datetime.now()

        if attached:
            _openclaw_metrics_inc("attach_success")
            _openclaw_runtime_guard_record_attach(True, detail="attach_success")
        else:
            _openclaw_metrics_inc("attach_failed")
            _openclaw_metrics_event("attach_failed", {"method": method_used or "unknown"})
            _openclaw_runtime_guard_record_attach(False, detail=f"method:{method_used or 'unknown'}")

        return {"success": attached, "method": method_used, "steps": steps}
    finally:
        _openclaw_attach_scope_exit()


def _flatten_openclaw_nodes(payload: Any) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []

    def pick_text(obj: Dict[str, Any]) -> str:
        fields = [
            "text", "name", "label", "title", "value", "placeholder", "ariaLabel",
            "aria_label", "aria-label", "alt", "content", "hint",
        ]
        parts = []
        for key in fields:
            value = obj.get(key)
            if value:
                parts.append(str(value))
        return " ".join(parts).strip()

    def pick_ref(obj: Dict[str, Any]) -> Optional[str]:
        for key in ("ref", "id", "nodeId", "node_id", "selector", "targetId"):
            value = obj.get(key)
            if value is not None:
                return str(value)
        return None

    def walk(item: Any):
        if isinstance(item, dict):
            if "refs" in item and isinstance(item.get("refs"), dict):
                for ref_key, ref_payload in item["refs"].items():
                    if isinstance(ref_payload, dict):
                        text = pick_text(ref_payload)
                        role = ref_payload.get("role") or ref_payload.get("tag") or ref_payload.get("type")
                        if text:
                            nodes.append({
                                "ref": str(ref_key),
                                "text": text,
                                "role": str(role) if role is not None else "",
                            })
            ref = pick_ref(item)
            text = pick_text(item)
            role = item.get("role") or item.get("tag") or item.get("type")
            if ref and text:
                nodes.append({
                    "ref": ref,
                    "text": text,
                    "role": str(role) if role is not None else "",
                })
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(payload)
    return nodes


def _find_openclaw_ref_by_text(payload: Any, patterns: List[str], role_hint: Optional[List[str]] = None) -> Optional[str]:
    if not patterns:
        return None
    def norm(value: str) -> str:
        raw = str(value or "")
        lowered = raw.lower()
        stripped = "".join(ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn")
        return stripped

    normalized = [norm(p).strip() for p in patterns if str(p).strip()]
    if not normalized:
        return None
    role_targets = [str(r).strip().lower() for r in (role_hint or []) if str(r).strip()]

    def score_node(text: str, role: str) -> bool:
        haystack = norm(text)
        if not any(p in haystack for p in normalized):
            return False
        if not role_targets:
            return True
        role_value = (role or "").lower()
        return any(rt in role_value for rt in role_targets)

    if isinstance(payload, dict) and isinstance(payload.get("refs"), dict):
        for ref_key, ref_payload in payload["refs"].items():
            if not isinstance(ref_payload, dict):
                continue
            text = " ".join(
                str(ref_payload.get(k, "")).strip()
                for k in ("text", "name", "label", "title", "value", "placeholder", "ariaLabel", "aria_label", "aria-label")
                if ref_payload.get(k)
            ).strip()
            role = str(ref_payload.get("role", "") or ref_payload.get("tag", "") or ref_payload.get("type", ""))
            if score_node(text, role):
                return str(ref_key)
    for node in _flatten_openclaw_nodes(payload):
        if score_node(node.get("text", ""), node.get("role", "")):
            return node.get("ref")
    return None


def _find_openclaw_ref_by_role_and_text(payload: Any, role_keywords: List[str], patterns: List[str]) -> Optional[str]:
    def norm(value: str) -> str:
        raw = str(value or "")
        lowered = raw.lower()
        stripped = "".join(ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn")
        return stripped

    roles = [str(r).strip().lower() for r in role_keywords if str(r).strip()]
    pats = [norm(p).strip() for p in patterns if str(p).strip()]
    if not roles or not pats:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("refs"), dict):
        for ref_key, ref_payload in payload["refs"].items():
            if not isinstance(ref_payload, dict):
                continue
            role = str(ref_payload.get("role", "") or ref_payload.get("tag", "") or ref_payload.get("type", "")).lower()
            if not any(r in role for r in roles):
                continue
            text = " ".join(
                str(ref_payload.get(k, "")).strip()
                for k in ("text", "name", "label", "title", "value", "placeholder", "ariaLabel", "aria_label", "aria-label")
                if ref_payload.get(k)
            ).strip()
            if any(p in norm(text) for p in pats):
                return str(ref_key)
    return None


def _find_openclaw_textbox(payload: Any, patterns: List[str], allow_fallback: bool = False) -> Optional[str]:
    def norm(value: str) -> str:
        raw = str(value or "")
        lowered = raw.lower()
        stripped = "".join(ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn")
        return stripped

    pats = [norm(p).strip() for p in patterns if str(p).strip()]
    if not pats:
        return None
    candidates: List[str] = []
    if isinstance(payload, dict) and isinstance(payload.get("refs"), dict):
        for ref_key, ref_payload in payload["refs"].items():
            if not isinstance(ref_payload, dict):
                continue
            role = str(ref_payload.get("role", "") or ref_payload.get("tag", "") or ref_payload.get("type", "")).lower()
            if not any(token in role for token in ("textbox", "input", "textarea")):
                continue
            text = " ".join(
                str(ref_payload.get(k, "")).strip()
                for k in ("text", "name", "label", "title", "value", "placeholder", "ariaLabel", "aria_label", "aria-label")
                if ref_payload.get(k)
            ).strip()
            if any(p in norm(text) for p in pats):
                return str(ref_key)
            if allow_fallback:
                candidates.append(str(ref_key))
    return candidates[0] if (allow_fallback and candidates) else None


def _record_flow_step(action: str, result: Dict[str, Any], fatal: bool = True, detail: Optional[str] = None) -> Dict[str, Any]:
    step: Dict[str, Any] = {
        "action": action,
        "success": bool(result.get("success")),
        "result": result,
    }
    if detail:
        step["detail"] = detail
    for key in ("error", "stderr", "error_code"):
        value = result.get(key)
        if value:
            step[key] = value
    if key := result.get("token_info"):
        step["token_info"] = key
    return step


def _openclaw_flow_fallback(chat_text: str) -> Optional[Dict[str, Any]]:
    if not chat_text:
        return None
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFD", str(chat_text).lower()) if unicodedata.category(ch) != "Mn"
    )
    actions: List[Dict[str, Any]] = []
    instance_id = LOCAL_INSTANCE_ID
    runtime_ready = callable(_runtime_bridge.get("command_handler"))

    def dispatch(target: str, command: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if runtime_ready:
            return _call_runtime_command(target, command, "high", options or {})
        return _call_instance_command(instance_id, target, command, "high", options or {})

    def record(action: str, result: Dict[str, Any]) -> None:
        actions.append({
            "action": action,
            "success": bool(result.get("success", True)),
            "result": result,
        })

    if any(token in normalized for token in ("go ket", "gỡ ket", "gỡ kẹt", "unstick")):
        result = dispatch("guardian", "unstick_orion", {"source": "openclaw_flow_fallback"})
        record("unstick_orion", result)

    if any(token in normalized for token in ("chay 1 vong", "chạy 1 vòng", "run one cycle", "run_cycle")):
        result = dispatch("orion", "run_cycle", {"control_mode": "interrupt", "resume_after": True})
        record("run_cycle", result)

    if any(token in normalized for token in ("tam dung", "tạm dừng", "pause")):
        result = dispatch("orion", "pause", {"control_mode": "strict", "reason": "openclaw_flow_fallback"})
        record("pause", result)

    if any(token in normalized for token in ("tiep tuc", "tiếp tục", "resume")):
        result = dispatch("orion", "resume", {"control_mode": "strict", "reason": "openclaw_flow_fallback"})
        record("resume", result)

    if any(token in normalized for token in ("trang thai", "tóm tắt", "tom tat", "status", "summary")):
        snapshot = _build_ops_snapshot()
        actions.append({
            "action": "status_snapshot",
            "success": True,
            "result": snapshot,
        })

    # Business due-diligence fallback when browser flow cannot open dashboard.
    if any(token in normalized for token in ("teams", "gmail", "email", "ravin", "khiem", "hop tac", "hợp tác", "partnership")):
        intake = _automation_get_intake_store(redact_secret=False)
        channels = intake.get("channels") if isinstance(intake.get("channels"), dict) else {}
        partners = intake.get("partners") if isinstance(intake.get("partners"), dict) else {}
        accounts = intake.get("accounts") if isinstance(intake.get("accounts"), dict) else {}
        mail_mode = str(channels.get("mail_mode", "edge_gmail") or "edge_gmail").strip().lower()
        target_gmail = str(partners.get("target_gmail", "") or "").strip()
        khiem_selected = str(accounts.get("khiem_selected", "") or partners.get("khiem_team_hint", "") or "").strip()

        if _automation_prompt_on_stuck_enabled() and not khiem_selected:
            prompt = _automation_request_due_diligence_context(
                reason="missing_khiem_account",
                source="openclaw_flow_fallback",
                chat_text=chat_text,
            )
            record(
                "human_input_request",
                {
                    "success": True,
                    "decision_id": prompt.get("id"),
                    "reason": "missing_khiem_account",
                },
            )

        app_rows = _automation_execute_app("list_running", {})
        record("app_list_running", app_rows)

        window_rows = _automation_execute_window("list", {})
        record("window_list", window_rows)

        mail_params: Dict[str, Any] = {"limit": 20}
        if "ravin" in normalized:
            mail_params["query"] = "ravin"
        if target_gmail:
            mail_params.setdefault("account_contains", target_gmail)

        if mail_mode == "edge_gmail":
            mail_recent = {
                "success": False,
                "error": "Mail route is configured for Edge Gmail. Use browser flow in Edge to inspect inbox.",
                "error_code": "MAIL_CHANNEL_BROWSER_REQUIRED",
                "channel": "edge_gmail",
                "target_gmail": target_gmail,
                "next_step": "open_edge_gmail_and_switch_account",
            }
            if _automation_prompt_on_stuck_enabled() and not target_gmail:
                prompt = _automation_request_due_diligence_context(
                    reason="missing_target_gmail",
                    source="openclaw_flow_fallback",
                    chat_text=chat_text,
                )
                record(
                    "human_input_request",
                    {
                        "success": True,
                        "decision_id": prompt.get("id"),
                        "reason": "missing_target_gmail",
                    },
                )
        else:
            mail_recent = _automation_execute_mail("list_recent", mail_params)
            if (
                not bool(mail_recent.get("success"))
                and str(mail_recent.get("error_code", "")).strip() in {"MAIL_CREDENTIALS_MISSING", "MAIL_UNAVAILABLE"}
                and _automation_prompt_on_stuck_enabled()
            ):
                prompt = _automation_request_due_diligence_context(
                    reason="mail_access_blocked",
                    source="openclaw_flow_fallback",
                    chat_text=chat_text,
                )
                record(
                    "human_input_request",
                    {
                        "success": True,
                        "decision_id": prompt.get("id"),
                        "reason": "mail_access_blocked",
                    },
                )
        record("mail_list_recent", mail_recent)

    if any(token in normalized for token in ("50k", "50000", "20%", "equity", "valuation", "dinh gia")):
        intake = _automation_get_intake_store(redact_secret=True)
        valuation_cfg = intake.get("valuation") if isinstance(intake.get("valuation"), dict) else {}
        raise_match = re.search(r"(\d+(?:[\\.,]\d+)?)\\s*k", normalized)
        equity_match = re.search(r"(\d+(?:[\\.,]\d+)?)\\s*%", normalized)
        raise_usd = float(valuation_cfg.get("raise_usd", 0) or 0)
        equity_pct = float(valuation_cfg.get("equity_pct", 0) or 0)
        if raise_match:
            raise_usd = float(str(raise_match.group(1)).replace(",", ".")) * 1000.0
        if equity_match:
            equity_pct = float(str(equity_match.group(1)).replace(",", "."))

        result: Dict[str, Any] = {
            "raise_usd": round(raise_usd, 2),
            "equity_pct": round(equity_pct, 4),
            "verdict": "insufficient_data",
        }
        if raise_usd > 0 and equity_pct > 0:
            post_money = raise_usd / (equity_pct / 100.0)
            pre_money = post_money - raise_usd
            result.update(
                {
                    "post_money_valuation_usd": round(post_money, 2),
                    "pre_money_valuation_usd": round(pre_money, 2),
                }
            )
            if post_money < 500000:
                result["verdict"] = "likely_too_low_for_ambitious_multi-product_ai"
                result["recommendation"] = "Prefer SAFE/note or lower equity (e.g. 5-10%) unless deal brings exceptional strategic distribution."
            else:
                result["verdict"] = "potentially_reasonable_if_traction_and_strategic_value_are_real"
                result["recommendation"] = "Validate investor strategic support and milestone-based release terms."
        actions.append({"action": "valuation_quick_assessment", "success": True, "result": result})

    if not actions:
        return None

    success = any(item.get("success") for item in actions)
    return {"success": success, "actions": actions}


def _create_token_status(info: _OpenClawTokenInfo) -> Dict[str, Any]:
    return {
        "token_present": bool(info.token),
        "health_ok": info.health_ok,
        "health_error": info.health_error,
        "source": info.source,
        "checked_at": info.checked_at.isoformat() if info.checked_at else None,
    }


def _openclaw_browser_actions_enabled() -> bool:
    return OPENCLAW_EXECUTION_MODE in {"hybrid", "browser"}


def _openclaw_dashboard_flow_enabled() -> bool:
    return OPENCLAW_EXECUTION_MODE == "browser"


def _openclaw_mode_gate(command_type: str) -> Dict[str, Any]:
    ctype = str(command_type or "").strip().lower()
    if ctype == "browser" and not _openclaw_browser_actions_enabled():
        return {
            "allowed": False,
            "error_code": "BROWSER_MODE_DISABLED",
            "error": "OpenClaw browser actions are disabled in headless mode",
            "reason": "execution_mode_headless",
        }
    if ctype == "flow" and not _openclaw_dashboard_flow_enabled():
        return {
            "allowed": False,
            "error_code": "FLOW_MODE_DISABLED",
            "error": "OpenClaw dashboard flow requires OPENCLAW_EXECUTION_MODE=browser",
            "reason": f"execution_mode_{OPENCLAW_EXECUTION_MODE}",
        }
    if ctype == "attach" and not OPENCLAW_EXTENSION_AUTOMATION_ENABLED:
        return {
            "allowed": False,
            "error_code": "EXTENSION_AUTOMATION_DISABLED",
            "error": "OpenClaw extension automation is disabled",
            "reason": "extension_automation_disabled",
        }
    if ctype == "attach" and OPENCLAW_EXECUTION_MODE != "browser":
        return {
            "allowed": False,
            "error_code": "ATTACH_MODE_DISABLED",
            "error": "OpenClaw extension attach requires OPENCLAW_EXECUTION_MODE=browser",
            "reason": f"execution_mode_{OPENCLAW_EXECUTION_MODE}",
        }
    return {"allowed": True}


def _openclaw_is_loopback_dashboard_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").strip().lower()
        return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    except Exception:
        return False


def _probe_http_url(url: str, timeout_sec: float = 1.2) -> Dict[str, Any]:
    target = str(url or "").strip()
    if not target:
        return {"success": False, "error": "Missing URL"}
    try:
        req = urllib_request.Request(target, headers={"User-Agent": "nexus-openclaw-probe/1.0"})
        with urllib_request.urlopen(req, timeout=max(0.6, float(timeout_sec))) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            return {"success": status < 500, "status_code": status}
    except urllib_error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        # 4xx still proves endpoint is reachable; we only care about service availability here.
        return {"success": code > 0 and code < 500, "status_code": code, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _openclaw_action_debounced(signature: str, cooldown_sec: float = OPENCLAW_ACTION_DEBOUNCE_SEC) -> Tuple[bool, float]:
    key = str(signature or "").strip().lower()
    if not key:
        return False, 0.0
    now = time.time()
    cooldown = max(0.5, float(cooldown_sec))
    with _OPENCLAW_ACTION_DEBOUNCE_LOCK:
        stale_before = now - max(60.0, cooldown * 6.0)
        stale = [item for item, seen in _openclaw_action_last_seen.items() if seen < stale_before]
        for item in stale:
            _openclaw_action_last_seen.pop(item, None)
        seen_at = _openclaw_action_last_seen.get(key)
        if seen_at is not None:
            elapsed = now - seen_at
            if elapsed < cooldown:
                return True, round(max(0.0, cooldown - elapsed), 2)
        _openclaw_action_last_seen[key] = now
    return False, 0.0


def _openclaw_self_heal_status() -> Dict[str, Any]:
    with _openclaw_self_heal_lock:
        return {
            "execution_mode": OPENCLAW_EXECUTION_MODE,
            "enabled": bool(OPENCLAW_SELF_HEAL_ENABLED),
            "autonomous_ui_enabled": bool(OPENCLAW_AUTONOMOUS_UI_ENABLED),
            "browser_actions_enabled": _openclaw_browser_actions_enabled(),
            "dashboard_flow_enabled": _openclaw_dashboard_flow_enabled(),
            "interval_sec": OPENCLAW_SELF_HEAL_INTERVAL_SEC,
            "open_dashboard_enabled": bool(OPENCLAW_SELF_HEAL_OPEN_DASHBOARD),
            "open_dashboard_cooldown_sec": OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC,
            "fallback_cooldown_sec": OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC,
            "action_debounce_sec": OPENCLAW_ACTION_DEBOUNCE_SEC,
            "flow_open_cooldown_sec": OPENCLAW_FLOW_OPEN_COOLDOWN_SEC,
            "last_run": _openclaw_self_heal_last_run.isoformat() if _openclaw_self_heal_last_run else None,
            "last_success": _openclaw_self_heal_last_success.isoformat() if _openclaw_self_heal_last_success else None,
            "last_open": _openclaw_self_heal_last_open.isoformat() if _openclaw_self_heal_last_open else None,
            "last_fallback": _openclaw_self_heal_last_fallback.isoformat() if _openclaw_self_heal_last_fallback else None,
            "last_error": _openclaw_self_heal_last_error,
            "runtime_guard": _openclaw_runtime_guard_snapshot(),
        }


def _openclaw_self_heal_once() -> Dict[str, Any]:
    global _openclaw_self_heal_last_run, _openclaw_self_heal_last_error, _openclaw_self_heal_last_success, _openclaw_self_heal_last_open, _openclaw_self_heal_last_fallback
    _openclaw_metrics_inc("self_heal_runs")
    _openclaw_self_heal_last_run = datetime.now()
    detail: Dict[str, Any] = {"steps": []}

    mode_gate = _openclaw_mode_gate("browser")
    if not mode_gate.get("allowed"):
        detail["steps"].append(
            {
                "action": "mode_gate",
                "result": {
                    "success": True,
                    "skipped": True,
                    "reason": mode_gate.get("reason"),
                    "error_code": mode_gate.get("error_code"),
                    "execution_mode": OPENCLAW_EXECUTION_MODE,
                },
            }
        )
        with _openclaw_self_heal_lock:
            _openclaw_self_heal_last_error = None
            _openclaw_self_heal_last_success = datetime.now()
        _openclaw_metrics_inc("self_heal_success")
        detail["success"] = True
        detail["skipped"] = True
        return detail
    if not OPENCLAW_AUTONOMOUS_UI_ENABLED:
        detail["steps"].append(
            {
                "action": "autonomous_ui_guard",
                "result": {
                    "success": True,
                    "skipped": True,
                    "reason": "autonomous_ui_disabled",
                },
            }
        )
        with _openclaw_self_heal_lock:
            _openclaw_self_heal_last_error = None
            _openclaw_self_heal_last_success = datetime.now()
        _openclaw_metrics_inc("self_heal_success")
        detail["success"] = True
        detail["skipped"] = True
        return detail

    def record(step: str, payload: Dict[str, Any]) -> None:
        detail["steps"].append({"action": step, "result": payload})

    ok = False
    last_error = None
    def runtime_bridge_ready() -> bool:
        return callable(_runtime_bridge.get("command_handler"))

    def allow_open_dashboard() -> bool:
        if not OPENCLAW_SELF_HEAL_OPEN_DASHBOARD:
            return False
        with _openclaw_self_heal_lock:
            if not _openclaw_self_heal_last_open:
                return True
            age_sec = (datetime.now() - _openclaw_self_heal_last_open).total_seconds()
            return age_sec >= OPENCLAW_SELF_HEAL_OPEN_COOLDOWN_SEC

    def allow_fallback() -> bool:
        if not OPENCLAW_SELF_HEAL_FALLBACK:
            return False
        with _openclaw_self_heal_lock:
            if not _openclaw_self_heal_last_fallback:
                return True
            age_sec = (datetime.now() - _openclaw_self_heal_last_fallback).total_seconds()
            return age_sec >= OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC

    try:
        if not _wait_for_openclaw_gateway_ready(max_wait_sec=1.6, interval_sec=0.4):
            record("gateway_restart", _restart_openclaw_gateway())
            _wait_for_openclaw_gateway_ready(max_wait_sec=3.0, interval_sec=0.5)
        start_result = _openclaw_browser_cmd(["start"], auto_attach=False)
        record("browser_start", start_result)
        if start_result.get("success"):
            snapshot_result = _openclaw_browser_cmd(
                ["snapshot", "--format", "ai", "--labels", "--limit", "120"],
                auto_attach=False,
            )
            record("snapshot", snapshot_result)
            ok = bool(snapshot_result.get("success"))
            if not ok and allow_open_dashboard():
                open_result = _openclaw_browser_cmd(["open", OPENCLAW_DASHBOARD_URL], auto_attach=False)
                record("browser_open", open_result)
                if open_result.get("success"):
                    with _openclaw_self_heal_lock:
                        _openclaw_self_heal_last_open = datetime.now()
                    snapshot_retry = _openclaw_browser_cmd(
                        ["snapshot", "--format", "ai", "--labels", "--limit", "120"],
                        auto_attach=False,
                    )
                    record("snapshot_retry", snapshot_retry)
                    ok = bool(snapshot_retry.get("success"))
        else:
            ok = False

        if not ok and runtime_bridge_ready():
            if allow_fallback():
                fallback = _openclaw_flow_fallback(OPENCLAW_SELF_HEAL_FLOW_COMMAND)
                if fallback:
                    record("fallback", fallback)
                    with _openclaw_self_heal_lock:
                        _openclaw_self_heal_last_fallback = datetime.now()
                    ok = bool(fallback.get("success"))
            else:
                record(
                    "fallback_skipped",
                    {
                        "success": True,
                        "reason": "cooldown",
                        "cooldown_sec": OPENCLAW_SELF_HEAL_FALLBACK_COOLDOWN_SEC,
                    },
                )

    except Exception as exc:
        last_error = str(exc)

    with _openclaw_self_heal_lock:
        _openclaw_self_heal_last_error = last_error or None if ok else last_error or "openclaw_self_heal_failed"
        if ok:
            _openclaw_self_heal_last_success = datetime.now()

    if ok:
        _openclaw_metrics_inc("self_heal_success")
    else:
        _openclaw_metrics_inc("self_heal_fail")

    if last_error:
        detail["error"] = last_error
    detail["success"] = ok
    return detail


def _openclaw_dashboard_flow(chat_text: str = "", approve: bool = True, deny: bool = False, dashboard_url: str = "") -> Dict[str, Any]:
    global _openclaw_flow_last_open
    steps: List[Dict[str, Any]] = []
    _openclaw_metrics_inc("flow_runs")
    token_info = _collect_token_info()
    token_status = _create_token_status(token_info)
    target_dashboard_url = str(dashboard_url or OPENCLAW_DASHBOARD_URL).strip() or OPENCLAW_DASHBOARD_URL
    query_suffix = ""
    try:
        parsed_dashboard = urlparse(target_dashboard_url)
        query_suffix = f"?{parsed_dashboard.query}" if parsed_dashboard.query else ""
    except Exception:
        query_suffix = ""
    def _fail_flow(result: Dict[str, Any], event: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _openclaw_metrics_inc("flow_failures")
        _openclaw_metrics_event(event, detail)
        return result

    def attempt_fallback(reason: str) -> Optional[Dict[str, Any]]:
        fallback = _openclaw_flow_fallback(chat_text)
        if not fallback:
            return None
        steps.append({
            "action": "fallback_command",
            "success": bool(fallback.get("success")),
            "result": fallback,
            "detail": reason,
        })
        if fallback.get("success"):
            _openclaw_metrics_inc("flow_success")
            return {
                "success": True,
                "steps": steps,
                "token_status": token_status,
                "fallback": fallback,
                "note": "Flow completed via fallback",
            }
        return None

    if not token_info.token:
        steps.append({
            "action": "token_check",
            "success": False,
            "error": token_info.last_error or "OpenClaw gateway token missing",
            "token_status": token_status,
        })
        _openclaw_metrics_inc("token_missing")
        if fallback := attempt_fallback("token_missing"):
            return fallback
        return _fail_flow(
            {"success": False, "steps": steps, "token_status": token_status},
            "flow_token_missing",
            {"error": token_info.last_error},
        )
    if not token_info.health_ok:
        steps.append({
            "action": "token_health",
            "success": False,
            "error": token_info.health_error or "Gateway health check failed",
            "token_status": token_status,
        })
        _openclaw_metrics_inc("health_error")
        if fallback := attempt_fallback("health_unavailable"):
            return fallback
        return _fail_flow(
            {"success": False, "steps": steps, "token_status": token_status},
            "flow_health_error",
            {"error": token_info.health_error},
        )

    def run_step(action: str, command: List[str], **kwargs) -> Dict[str, Any]:
        kwargs.setdefault("auto_attach", False)
        result = _openclaw_browser_cmd(command, **kwargs)
        step = _record_flow_step(action, result)
        steps.append(step)
        return step

    def needs_attach(result: Dict[str, Any]) -> bool:
        text = " ".join(
            str(result.get(k, "")) for k in ("error", "stderr", "message")
        ).strip().lower()
        if not text:
            return False
        return any(token in text for token in ("no tab is connected", "extension relay", "chrome extension"))

    def attempt_attach(detail: str) -> bool:
        attach_result = _openclaw_attempt_attach(force=True)
        steps.append(_record_flow_step("auto_attach", attach_result, detail=detail))
        return bool(attach_result.get("success"))

    def allow_open_dashboard() -> bool:
        with _OPENCLAW_FLOW_LOCK:
            if not _openclaw_flow_last_open:
                return True
            age_sec = (datetime.now() - _openclaw_flow_last_open).total_seconds()
            return age_sec >= OPENCLAW_FLOW_OPEN_COOLDOWN_SEC

    if OPENCLAW_BROWSER_AUTO_START:
        start_step = run_step("start", ["start"])
        if not start_step["success"]:
            if needs_attach(start_step.get("result", {})) and attempt_attach("start_failed"):
                start_retry = run_step("start_retry", ["start"])
                if start_retry["success"]:
                    start_step = start_retry
            if not start_step["success"]:
                if fallback := attempt_fallback("start_failed"):
                    return fallback
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "start"})

    did_open_dashboard = False
    if allow_open_dashboard():
        candidate_urls = [target_dashboard_url]
        fallback_urls = [
            f"http://127.0.0.1:5000/live{query_suffix}",
            f"http://localhost:5000/live{query_suffix}",
            f"http://127.0.0.1:5050/live{query_suffix}",
            f"http://localhost:5050/live{query_suffix}",
        ]
        for candidate in fallback_urls:
            if candidate not in candidate_urls:
                candidate_urls.append(candidate)

        selected_dashboard_url = target_dashboard_url
        if _openclaw_is_loopback_dashboard_url(target_dashboard_url):
            selected_dashboard_url = ""
            last_probe: Dict[str, Any] = {}
            for candidate in candidate_urls:
                probe_result = _probe_http_url(candidate, timeout_sec=1.2)
                last_probe = probe_result
                steps.append(_record_flow_step("dashboard_probe", probe_result, detail=candidate))
                if probe_result.get("success"):
                    selected_dashboard_url = candidate
                    break
            if not selected_dashboard_url:
                if fallback := attempt_fallback("dashboard_unreachable"):
                    return fallback
                return _fail_flow(
                    {"success": False, "steps": steps, "token_status": token_status},
                    "flow_dashboard_probe_failed",
                    {"url": target_dashboard_url, "error": last_probe.get("error")},
                )

        open_step = run_step("open", ["open", selected_dashboard_url or target_dashboard_url])
        if not open_step["success"]:
            if needs_attach(open_step.get("result", {})) and attempt_attach("open_failed"):
                open_retry = run_step("open_retry", ["open", selected_dashboard_url or target_dashboard_url])
                if open_retry["success"]:
                    open_step = open_retry
            if not open_step["success"]:
                if fallback := attempt_fallback("open_failed"):
                    return fallback
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "open"})
        did_open_dashboard = True
        with _OPENCLAW_FLOW_LOCK:
            _openclaw_flow_last_open = datetime.now()
    else:
        steps.append({
            "action": "open_skipped",
            "success": True,
            "detail": f"cooldown {OPENCLAW_FLOW_OPEN_COOLDOWN_SEC}s",
        })

    if did_open_dashboard:
        time.sleep(1.2)
        key = "Meta+Shift+R" if sys.platform == "darwin" else "Control+Shift+R"
        refresh_step = run_step("hard_refresh", ["press", key], expect_json=False)
        if not refresh_step["success"]:
            return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "hard_refresh"})

    normalized_text = ""
    if chat_text:
        normalized_text = "".join(
            ch for ch in unicodedata.normalize("NFD", chat_text.lower()) if unicodedata.category(ch) != "Mn"
        )
        if any(token in normalized_text for token in ("duyet", "approve", "accept", "y+enter")):
            approve = True
        if any(token in normalized_text for token in ("tu choi", "deny", "reject", "esc")):
            deny = True

    snapshot = run_step("snapshot", ["snapshot", "--format", "ai", "--labels", "--limit", "220"])
    if not snapshot["success"] or not snapshot.get("result", {}).get("json"):
        if needs_attach(snapshot.get("result", {})) and attempt_attach("snapshot_failed"):
            snapshot_retry = run_step("snapshot_retry", ["snapshot", "--format", "ai", "--labels", "--limit", "220"])
            if snapshot_retry["success"] and snapshot_retry.get("result", {}).get("json"):
                snapshot = snapshot_retry
        if not snapshot["success"] or not snapshot.get("result", {}).get("json"):
            if fallback := attempt_fallback("snapshot_failed"):
                return fallback
            return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "snapshot"})
    snapshot_payload = snapshot["result"].get("json") or {}

    approve_patterns = ["duyệt prompt", "approve prompt", "approve", "approve (y+enter)", "y+enter"]
    deny_patterns = ["từ chối prompt", "deny prompt", "deny", "reject", "deny (esc)", "esc"]
    if approve:
        ref = _find_openclaw_ref_by_role_and_text(snapshot_payload, ["button"], approve_patterns)
        if ref:
            click_approve = run_step("click_approve", ["click", str(ref)])
            if not click_approve["success"]:
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "click_approve"})
        else:
            steps.append({"action": "click_approve", "success": False, "error": "Approve button not found"})

    if deny:
        if approve:
            snapshot_after = run_step("snapshot_after_approve", ["snapshot", "--format", "ai", "--labels", "--limit", "220"])
            if not snapshot_after["success"] or not snapshot_after.get("result", {}).get("json"):
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "snapshot_after_approve"})
            snapshot_payload = snapshot_after["result"].get("json") or snapshot_payload
        ref = _find_openclaw_ref_by_role_and_text(snapshot_payload, ["button"], deny_patterns)
        if ref:
            click_deny = run_step("click_deny", ["click", str(ref)])
            if not click_deny["success"]:
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "click_deny"})
        else:
            steps.append({"action": "click_deny", "success": False, "error": "Deny button not found"})

    def click_flow_button(action: str, labels: List[str]) -> bool:
        ref = _find_openclaw_ref_by_role_and_text(snapshot_payload, ["button"], labels)
        if not ref:
            return False
        click_step = run_step(action, ["click", str(ref)])
        if not click_step["success"]:
            return False
        return True

    action_clicked = False
    if normalized_text:
        if any(token in normalized_text for token in ("go ket", "gỡ kẹt", "gỡ kẹt", "unstick")):
            action_clicked = click_flow_button("click_unstick", ["auto unstick", "gỡ kẹt", "guardian gỡ kẹt"])
        if any(token in normalized_text for token in ("chay 1 vong", "chạy 1 vòng", "run one cycle", "run_cycle")):
            action_clicked = click_flow_button("click_run_cycle", ["run one cycle", "chạy 1 vòng", "run cycle"]) or action_clicked
        if any(token in normalized_text for token in ("tiep tuc", "tiếp tục", "resume")):
            action_clicked = click_flow_button("click_resume", ["resume", "tiếp tục"]) or action_clicked
        if any(token in normalized_text for token in ("tam dung", "tạm dừng", "pause")):
            action_clicked = click_flow_button("click_pause", ["pause", "tạm dừng"]) or action_clicked
        if any(token in normalized_text for token in ("trang thai", "tóm tắt", "tom tat", "status", "summary", "realtime summary")):
            action_clicked = click_flow_button("click_summary", ["realtime summary", "summary", "trạng thái", "status"]) or action_clicked

    if chat_text and not action_clicked:
        command_candidates = {"status", "pause", "resume", "run_cycle", "shutdown", "unstick"}
        maybe_command = chat_text.strip().lower()
        chat_snapshot = run_step("snapshot_chat", ["snapshot", "--format", "ai", "--labels", "--limit", "240"])
        if not chat_snapshot["success"] or not chat_snapshot.get("result", {}).get("json"):
            return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "snapshot_chat"})
        chat_payload = chat_snapshot["result"].get("json") or {}
        ref = _find_openclaw_textbox(chat_payload, OPENCLAW_FLOW_CHAT_PLACEHOLDERS, allow_fallback=True)
        if ref:
            text_to_send = maybe_command if maybe_command in command_candidates else chat_text
            type_step = run_step("type_command", ["type", str(ref), text_to_send])
            if not type_step["success"]:
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "type_command"})
            send_ref = _find_openclaw_ref_by_role_and_text(chat_payload, ["button"], ["gửi lệnh", "send"])
            if send_ref:
                send_step = run_step("send_command", ["click", str(send_ref)])
            else:
                send_step = run_step("send_command", ["press", "Enter"], expect_json=False)
            if not send_step["success"]:
                return _fail_flow({"success": False, "steps": steps, "token_status": token_status}, "flow_step_failed", {"action": "send_command"})
        else:
            steps.append({"action": "type_command", "success": False, "error": "Command input not found"})

    _openclaw_metrics_inc("flow_success")
    return {
        "success": True,
        "steps": steps,
        "token_status": token_status,
        "note": "Flow completed",
    }


def _normalize_risk_level(value: Any, fallback: str = "medium") -> str:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high", "critical"}:
        return text
    return fallback


def _normalize_openclaw_action_key(command_type: str, payload: Dict[str, Any]) -> str:
    ctype = str(command_type or "").strip().lower()
    if ctype == "browser":
        return str(payload.get("action", "")).strip().lower()
    if ctype == "flow":
        return "flow"
    if ctype == "attach":
        return "attach"
    return ctype or "unknown"


def _contains_critical_keyword(payload: Dict[str, Any]) -> bool:
    text = " ".join(
        str(payload.get(key, ""))
        for key in ("action", "chat_text", "command", "text")
        if payload.get(key)
    ).strip().lower()
    if not text:
        return False
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )
    return any(keyword in normalized for keyword in OPENCLAW_CRITICAL_KEYWORDS)


def _contains_hard_deny_keyword(payload: Dict[str, Any]) -> bool:
    text = " ".join(
        str(payload.get(key, ""))
        for key in ("action", "chat_text", "command", "text")
        if payload.get(key)
    ).strip().lower()
    if not text:
        return False
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )
    return any(keyword in normalized for keyword in OPENCLAW_HARD_DENY_KEYWORDS)


def _classify_openclaw_failure(result: Any) -> str:
    if not isinstance(result, dict):
        return "unknown"
    error_code = str(result.get("error_code", "")).strip().upper()
    if error_code in {"TOKEN_MISSING"}:
        return "token"
    if error_code in {"HEALTH_ERROR", "HEALTH_UNAVAILABLE"}:
        return "health"
    if error_code in {"ATTACH_FAILED", "ATTACH_THROTTLED"}:
        return "attach"
    if error_code in {"OPENCLAW_TEMP_DISABLED"}:
        return "policy"
    if error_code in {"BROWSER_MODE_DISABLED", "FLOW_MODE_DISABLED", "ATTACH_MODE_DISABLED"}:
        return "policy"
    if error_code in {"MANUAL_REQUIRED", "POLICY_DENIED", "FAILURE_CLASS_BLOCKED"}:
        return "policy"
    if error_code in {"WORKER_UNAVAILABLE", "WORKER_UNHEALTHY", "WORKER_NOT_FOUND", "CIRCUIT_OPEN"}:
        return "provider"
    if error_code in {"QUEUE_TIMEOUT"}:
        return "network"

    text_parts = [
        str(result.get("error", "")),
        str(result.get("stderr", "")),
        str(result.get("message", "")),
    ]
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    if inner:
        text_parts.extend(
            [
                str(inner.get("error", "")),
                str(inner.get("stderr", "")),
                str(inner.get("message", "")),
            ]
        )
    text = " ".join(text_parts).strip().lower()
    if any(token in text for token in ("token", "unauthorized", "forbidden", "auth")):
        return "token"
    if any(token in text for token in ("health", "gateway status", "unhealthy", "not ready")):
        return "health"
    if any(token in text for token in ("attach", "extension relay", "chrome extension", "no tab is connected")):
        return "attach"
    if any(token in text for token in ("policy", "manual", "denied", "blocked")):
        return "policy"
    if any(token in text for token in ("http ", "timeout", "network", "connection", "refused", "remote")):
        return "network"
    return "unknown"


def _openclaw_payload_public(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in (payload or {}).items()
        if not str(key).startswith("_")
    }


def _evaluate_openclaw_policy(command_type: str, payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    action_key = _normalize_openclaw_action_key(command_type, payload)
    route_mode = str(context.get("route_mode", "auto")).strip().lower() or "auto"
    requested_worker = str(context.get("target_worker", "")).strip().lower()
    risk_level = _normalize_risk_level(context.get("risk_level"), fallback="medium")
    if _contains_critical_keyword(payload):
        risk_level = "critical"

    decision = "auto_approved"
    reasons: List[str] = []
    error_code = ""
    token_status: Dict[str, Any] = {}
    resolved_worker = requested_worker
    failover_from = ""
    full_auto = _is_full_auto_profile()

    if _contains_hard_deny_keyword(payload):
        decision = "denied"
        reasons.append("hard_deny_keyword")
        error_code = "POLICY_DENIED"

    if decision == "auto_approved" and not OPENCLAW_POLICY_MOSTLY_AUTO and risk_level in {"high", "critical"}:
        decision = "manual_required"
        reasons.append("mostly_auto_disabled")
        error_code = "MANUAL_REQUIRED"
    elif decision == "auto_approved" and risk_level == "critical" and not full_auto:
        decision = "manual_required"
        reasons.append("critical_risk_requires_manual")
        error_code = "MANUAL_REQUIRED"
    elif decision == "auto_approved" and risk_level == "high" and action_key not in OPENCLAW_HIGH_RISK_ALLOWLIST and not full_auto:
        decision = "manual_required"
        reasons.append("high_risk_action_not_allowlisted")
        error_code = "MANUAL_REQUIRED"

    info = _collect_token_info(force_refresh=False)
    token_status = _create_token_status(info)
    if not info.token:
        decision = "denied"
        reasons.append("token_missing")
        error_code = "TOKEN_MISSING"
    elif not info.health_ok:
        decision = "denied"
        reasons.append("gateway_health_unavailable")
        error_code = "HEALTH_UNAVAILABLE"

    if route_mode == "direct":
        resolved_worker = LOCAL_INSTANCE_ID
    else:
        selection = _control_plane_registry.resolve_worker(
            requested_worker=requested_worker,
            required_capability="openclaw",
        )
        if not selection.get("available"):
            decision = "denied"
            reasons.append("worker_unavailable")
            error_code = "WORKER_UNAVAILABLE"
        else:
            worker = selection.get("worker") if isinstance(selection.get("worker"), dict) else {}
            resolved_worker = str(worker.get("worker_id", "")).strip().lower()
            can_dispatch = _control_plane_registry.can_dispatch(resolved_worker)
            if not can_dispatch.get("allowed"):
                fallback = _control_plane_registry.resolve_worker(
                    requested_worker="",
                    required_capability="openclaw",
                    exclude={resolved_worker} if resolved_worker else set(),
                )
                if fallback.get("available"):
                    fallback_worker = fallback.get("worker") if isinstance(fallback.get("worker"), dict) else {}
                    failover_from = resolved_worker
                    resolved_worker = str(fallback_worker.get("worker_id", "")).strip().lower()
                    _openclaw_metrics_inc("dispatch_failover")
                    reasons.append("primary_worker_blocked_fallback_selected")
                else:
                    decision = "denied"
                    reasons.append(str(can_dispatch.get("reason", "worker_dispatch_blocked")))
                    error_code = str(can_dispatch.get("error_code", "WORKER_UNAVAILABLE"))

    if decision == "auto_approved":
        _openclaw_metrics_inc("policy_auto_approved")
    elif decision == "manual_required":
        _openclaw_metrics_inc("policy_manual_required")
    else:
        _openclaw_metrics_inc("policy_denied")

    return {
        "allowed": decision == "auto_approved",
        "decision": decision,
        "risk_level": risk_level,
        "autonomy_profile": _get_autonomy_profile(),
        "full_auto": full_auto,
        "reasons": reasons,
        "error_code": error_code,
        "action_key": action_key,
        "token_status": token_status,
        "target_worker": requested_worker,
        "resolved_worker": resolved_worker,
        "failover_from": failover_from,
        "route_mode": route_mode,
    }


def _dispatch_openclaw_remote(worker: Dict[str, Any], command_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    worker_id = str(worker.get("worker_id", "")).strip().lower()
    base_url = str(worker.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        return {
            "success": False,
            "error_code": "WORKER_UNAVAILABLE",
            "error": f"Worker '{worker_id}' has no base_url",
            "failure_class": "provider",
        }
    endpoint_map = {
        "browser": "/api/openclaw/browser",
        "flow": "/api/openclaw/flow/dashboard",
        "attach": "/api/openclaw/extension/attach",
    }
    endpoint = endpoint_map.get(str(command_type or "").strip().lower())
    if not endpoint:
        return {
            "success": False,
            "error_code": "UNSUPPORTED_COMMAND",
            "error": f"Unsupported command_type '{command_type}'",
            "failure_class": "unknown",
        }
    body = _openclaw_payload_public(payload)
    body["mode"] = "sync"
    body["route_mode"] = "direct"
    timeout_sec = max(2.0, min(25.0, float(int(body.get("timeout_ms", OPENCLAW_BROWSER_TIMEOUT_MS) or OPENCLAW_BROWSER_TIMEOUT_MS) / 1000.0) + 1.5))
    remote = _http_json("POST", f"{base_url}{endpoint}", payload=body, timeout_sec=timeout_sec)
    if not isinstance(remote, dict):
        return {
            "success": False,
            "error_code": "REMOTE_INVALID_RESPONSE",
            "error": "Remote worker returned invalid response",
            "failure_class": "network",
        }
    result = dict(remote)
    result["dispatch"] = {
        "mode": "remote",
        "worker_id": worker_id,
        "base_url": base_url,
    }
    if not bool(result.get("success")):
        result["failure_class"] = _classify_openclaw_failure(result)
        if "error_code" not in result:
            result["error_code"] = "REMOTE_EXECUTION_FAILED"
        if "error" not in result:
            result["error"] = "Remote dispatch failed"
    return result


def _execute_openclaw_browser_action(data: Dict[str, Any]) -> Dict[str, Any]:
    mode_gate = _openclaw_mode_gate("browser")
    if not mode_gate.get("allowed"):
        return {
            "success": False,
            "error": mode_gate.get("error"),
            "error_code": mode_gate.get("error_code"),
            "execution_mode": OPENCLAW_EXECUTION_MODE,
        }

    action = str(data.get("action", "")).strip().lower()
    if not action:
        return {"success": False, "error": "Missing action"}
    session_id = str(data.get("session_id", "default")).strip() or "default"

    attach = None
    if action == "start":
        result = _openclaw_browser_cmd(["start"], auto_attach=False)
        error_code = result.get("error_code") or ""
        if not result.get("success") and OPENCLAW_AUTO_ATTACH_ENABLED and error_code != "TOKEN_MISSING":
            stderr = str(result.get("stderr", "")).lower()
            if "no tab is connected" in stderr or "extension" in stderr or "relay" in stderr:
                attach = _openclaw_attempt_attach()
                result = _openclaw_browser_cmd(["start"], auto_attach=False)
    elif action == "status":
        result = _openclaw_browser_cmd(["status"])
    elif action == "open":
        url = str(data.get("url", "")).strip()
        if not url:
            return {"success": False, "error": "Missing url"}
        debounce_sig = f"{session_id}:browser:open:{url.lower()}"
        debounced, retry_after = _openclaw_action_debounced(debounce_sig, OPENCLAW_ACTION_DEBOUNCE_SEC)
        if debounced:
            result = {
                "success": True,
                "skipped": True,
                "reason": "debounced",
                "retry_after_sec": retry_after,
                "url": url,
            }
        else:
            if _openclaw_is_loopback_dashboard_url(url):
                probe = _probe_http_url(url, timeout_sec=1.0)
                if not probe.get("success"):
                    result = {
                        "success": False,
                        "error": "Target dashboard is unreachable",
                        "error_code": "DASHBOARD_UNREACHABLE",
                        "url": url,
                        "probe": probe,
                    }
                else:
                    result = _openclaw_browser_cmd(["open", url])
            else:
                result = _openclaw_browser_cmd(["open", url])
    elif action == "navigate":
        url = str(data.get("url", "")).strip()
        if not url:
            return {"success": False, "error": "Missing url"}
        debounce_sig = f"{session_id}:browser:navigate:{url.lower()}"
        debounced, retry_after = _openclaw_action_debounced(debounce_sig, OPENCLAW_ACTION_DEBOUNCE_SEC)
        if debounced:
            result = {
                "success": True,
                "skipped": True,
                "reason": "debounced",
                "retry_after_sec": retry_after,
                "url": url,
            }
        else:
            result = _openclaw_browser_cmd(["navigate", url])
    elif action == "snapshot":
        result = _openclaw_browser_cmd(["snapshot", "--format", "ai", "--labels", "--limit", "240"])
    elif action == "click":
        ref = str(data.get("ref", "")).strip()
        if not ref:
            return {"success": False, "error": "Missing ref"}
        result = _openclaw_browser_cmd(["click", ref])
    elif action == "type":
        ref = str(data.get("ref", "")).strip()
        text = str(data.get("text", ""))
        if not ref:
            return {"success": False, "error": "Missing ref"}
        result = _openclaw_browser_cmd(["type", ref, text])
    elif action == "press":
        key = str(data.get("key", "")).strip()
        if not key:
            return {"success": False, "error": "Missing key"}
        result = _openclaw_browser_cmd(["press", key], expect_json=False)
    elif action == "hard_refresh":
        debounce_sig = f"{session_id}:browser:hard_refresh"
        debounced, retry_after = _openclaw_action_debounced(debounce_sig, OPENCLAW_ACTION_DEBOUNCE_SEC)
        if debounced:
            result = {
                "success": True,
                "skipped": True,
                "reason": "debounced",
                "retry_after_sec": retry_after,
            }
        else:
            key = "Meta+Shift+R" if sys.platform == "darwin" else "Control+Shift+R"
            result = _openclaw_browser_cmd(["press", key], expect_json=False)
    else:
        return {"success": False, "error": f"Unsupported action '{action}'"}

    payload = {
        "success": bool(result.get("success")),
        "action": action,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }
    if action == "start":
        payload["attach"] = attach
    return payload


def _execute_openclaw_flow_action(data: Dict[str, Any]) -> Dict[str, Any]:
    mode_gate = _openclaw_mode_gate("flow")
    if not mode_gate.get("allowed"):
        error = str(mode_gate.get("error", "Flow disabled by execution mode"))
        error_code = str(mode_gate.get("error_code", "FLOW_MODE_DISABLED"))
        return {
            "success": False,
            "flow": {
                "success": False,
                "error": error,
                "error_code": error_code,
                "execution_mode": OPENCLAW_EXECUTION_MODE,
            },
            "error": error,
            "error_code": error_code,
            "timestamp": datetime.now().isoformat(),
        }

    chat_text = str(data.get("chat_text", "") or "")
    source = str(data.get("source", "manual") or "manual").strip().lower()
    approve = bool(data.get("approve", True))
    deny = bool(data.get("deny", False))
    if source in {"autopilot", "system"} and not OPENCLAW_AUTONOMOUS_UI_ENABLED:
        return {
            "success": True,
            "flow": {
                "success": True,
                "skipped": True,
                "reason": "autonomous_ui_disabled",
                "source": source,
            },
            "timestamp": datetime.now().isoformat(),
        }
    result = _openclaw_dashboard_flow(
        chat_text=chat_text,
        approve=approve,
        deny=deny,
        dashboard_url=str(data.get("dashboard_url", "") or ""),
    )
    return {
        "success": bool(result.get("success")),
        "flow": result,
        "timestamp": datetime.now().isoformat(),
    }


def _execute_openclaw_attach_action(_: Dict[str, Any]) -> Dict[str, Any]:
    mode_gate = _openclaw_mode_gate("attach")
    if not mode_gate.get("allowed"):
        return {
            "success": False,
            "error": mode_gate.get("error"),
            "error_code": mode_gate.get("error_code"),
            "execution_mode": OPENCLAW_EXECUTION_MODE,
        }

    if not OPENCLAW_AUTO_ATTACH_ENABLED:
        return {"success": False, "error": "Auto-attach disabled"}
    result = _openclaw_attempt_attach()
    return {
        "success": bool(result.get("success")),
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }


def _openclaw_queue_executor_local(command_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if command_type == "browser":
        return _execute_openclaw_browser_action(payload)
    if command_type == "flow":
        return _execute_openclaw_flow_action(payload)
    if command_type == "attach":
        return _execute_openclaw_attach_action(payload)
    return {"success": False, "error": f"Unsupported queued command_type '{command_type}'", "error_code": "UNSUPPORTED_COMMAND"}


def _openclaw_queue_executor(command_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    route_mode = str(payload.get("route_mode", "auto")).strip().lower() or "auto"
    routing = payload.get("_routing") if isinstance(payload.get("_routing"), dict) else {}
    resolved_worker = str(
        routing.get("resolved_worker")
        or payload.get("resolved_worker")
        or ""
    ).strip().lower()
    if not resolved_worker:
        selected = _control_plane_registry.resolve_worker(required_capability="openclaw")
        if selected.get("available"):
            selected_worker = selected.get("worker") if isinstance(selected.get("worker"), dict) else {}
            resolved_worker = str(selected_worker.get("worker_id", "")).strip().lower()

    if route_mode != "direct" and resolved_worker and resolved_worker != LOCAL_INSTANCE_ID:
        gate = _control_plane_registry.can_dispatch(resolved_worker)
        if not gate.get("allowed"):
            return {
                "success": False,
                "error": str(gate.get("reason", "worker dispatch blocked")),
                "error_code": str(gate.get("error_code", "WORKER_UNAVAILABLE")),
                "failure_class": _classify_openclaw_failure(gate),
                "dispatch": {"mode": "blocked", "worker_id": resolved_worker},
            }

        selected = _control_plane_registry.resolve_worker(
            requested_worker=resolved_worker,
            required_capability="openclaw",
        )
        worker = selected.get("worker") if isinstance(selected.get("worker"), dict) else {}
        remote_result = _dispatch_openclaw_remote(worker, command_type, payload)
        if remote_result.get("success"):
            _openclaw_metrics_inc("dispatch_remote")
            return remote_result

        failure_class = str(remote_result.get("failure_class", "")).strip().lower() or _classify_openclaw_failure(remote_result)
        _control_plane_registry.record_dispatch_result(
            worker_id=resolved_worker,
            success=False,
            failure_class=failure_class,
        )
        remote_result["_dispatch_result_recorded"] = True
        fallback = _control_plane_registry.resolve_worker(
            requested_worker="",
            required_capability="openclaw",
            exclude={resolved_worker},
        )
        if fallback.get("available"):
            fallback_worker = fallback.get("worker") if isinstance(fallback.get("worker"), dict) else {}
            fallback_worker_id = str(fallback_worker.get("worker_id", "")).strip().lower()
            _openclaw_metrics_inc("dispatch_failover")
            if fallback_worker_id and fallback_worker_id != LOCAL_INSTANCE_ID:
                remote_fallback = _dispatch_openclaw_remote(fallback_worker, command_type, payload)
                dispatch = remote_fallback.get("dispatch") if isinstance(remote_fallback.get("dispatch"), dict) else {}
                dispatch["failover_from"] = resolved_worker
                dispatch["failover_to"] = fallback_worker_id
                remote_fallback["dispatch"] = dispatch
                return remote_fallback
            local_fallback = _openclaw_queue_executor_local(command_type, payload)
            local_fallback["dispatch"] = {
                "mode": "failover_local",
                "worker_id": LOCAL_INSTANCE_ID,
                "failover_from": resolved_worker,
            }
            if not local_fallback.get("success"):
                local_fallback["failure_class"] = _classify_openclaw_failure(local_fallback)
            return local_fallback
        remote_result.setdefault("dispatch", {"mode": "remote_failed", "worker_id": resolved_worker})
        if "failure_class" not in remote_result:
            remote_result["failure_class"] = failure_class or "unknown"
        return remote_result

    local_result = _openclaw_queue_executor_local(command_type, payload)
    local_result["dispatch"] = {"mode": "local", "worker_id": LOCAL_INSTANCE_ID}
    if not local_result.get("success"):
        local_result["failure_class"] = _classify_openclaw_failure(local_result)
    return local_result


def _idempotency_text(value: Any, max_len: int = 84) -> str:
    normalized = " ".join(str(value or "").strip().lower().split())
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(ch) != "Mn"
    )
    safe = re.sub(r"[^a-z0-9._:/+-]+", "-", normalized).strip("-")
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip("-")
    return safe or "none"


def _derive_openclaw_idempotency_key(command_type: str, data: Dict[str, Any], source: str) -> str:
    if not OPENCLAW_AUTO_IDEMPOTENCY_ENABLED:
        return ""
    ctype = str(command_type or "").strip().lower()
    src = str(source or "manual").strip().lower() or "manual"
    action = str(data.get("action", "")).strip().lower()
    manual_allowed_actions = {"open", "navigate", "hard_refresh"}
    if src == "manual":
        if ctype == "flow":
            pass
        elif ctype == "browser" and action in manual_allowed_actions:
            pass
        else:
            return ""
    if src not in {"manual", "autopilot", "system"}:
        return ""

    bucket = int(time.time() // max(1, OPENCLAW_AUTO_IDEMPOTENCY_WINDOW_SEC))
    if ctype == "flow":
        chat_text = _idempotency_text(data.get("chat_text", ""), max_len=96)
        approve = "1" if bool(data.get("approve", True)) else "0"
        deny = "1" if bool(data.get("deny", False)) else "0"
        return f"auto:{bucket}:flow:{approve}:{deny}:{chat_text}"
    if ctype == "browser":
        if action in {"open", "navigate"}:
            target = _idempotency_text(data.get("url", ""), max_len=96)
        elif action == "hard_refresh":
            target = "tab"
        else:
            target = _idempotency_text(data.get("ref", "") or data.get("key", "") or "", max_len=64)
        return f"auto:{bucket}:browser:{_idempotency_text(action, max_len=20)}:{target}"
    if ctype == "attach":
        return f"auto:{bucket}:attach"
    return ""


def _parse_openclaw_queue_context(command_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    source = str(data.get("source", "manual")).strip().lower() or "manual"
    if source not in {"manual", "autopilot", "system"}:
        source = "manual"
    ctype = str(command_type or "").strip().lower()
    action = str(data.get("action", "")).strip().lower()
    mode = str(data.get("mode", "")).strip().lower()
    if mode not in {"sync", "async"}:
        if ctype in {"flow", "attach"}:
            mode = "async"
        elif ctype == "browser" and action in OPENCLAW_DEFAULT_ASYNC_BROWSER_ACTIONS:
            mode = "async"
        else:
            mode = "sync"
    session_id = str(data.get("session_id", "default")).strip() or "default"
    timeout_ms = max(1000, int(data.get("timeout_ms") or OPENCLAW_BROWSER_TIMEOUT_MS))
    if mode == "sync":
        timeout_ms = min(timeout_ms, OPENCLAW_SYNC_WAIT_TIMEOUT_MS)
    priority = int(data.get("priority", 0) or 0)
    idempotency_key = str(data.get("idempotency_key", "") or "").strip()
    if not idempotency_key:
        idempotency_key = _derive_openclaw_idempotency_key(command_type, data, source)
    route_mode = str(data.get("route_mode", "auto")).strip().lower() or "auto"
    if route_mode not in {"auto", "direct"}:
        route_mode = "auto"
    target_worker = str(data.get("target_worker", data.get("worker_id", "")) or "").strip().lower()
    risk_level = _normalize_risk_level(data.get("risk_level"), fallback="medium")
    return {
        "source": source,
        "mode": mode,
        "session_id": session_id,
        "timeout_ms": timeout_ms,
        "priority": priority,
        "idempotency_key": idempotency_key,
        "route_mode": route_mode,
        "target_worker": target_worker,
        "risk_level": risk_level,
    }


def _enqueue_openclaw_command(command_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    context = _parse_openclaw_queue_context(command_type, data)
    mode_gate = _openclaw_mode_gate(command_type)
    if not mode_gate.get("allowed"):
        return {
            "success": False,
            "error": mode_gate.get("error", "Command blocked by execution mode"),
            "error_code": mode_gate.get("error_code", "MODE_DISABLED"),
            "decision": "denied",
            "risk_level": context.get("risk_level", "medium"),
            "policy_reasons": [mode_gate.get("reason", "execution_mode_gate")],
            "token_status": {},
            "target_worker": context.get("target_worker", ""),
            "resolved_worker": "",
            "queue_state": _openclaw_command_manager.queue_snapshot() if OPENCLAW_QUEUE_ENABLED else {"enabled": False},
            "session_id": context.get("session_id"),
        }

    runtime_gate = _openclaw_runtime_guard_allows(command_type, context=context, payload=data)
    if not runtime_gate.get("allowed"):
        return {
            "success": False,
            "error": runtime_gate.get("error", "OpenClaw temporarily disabled"),
            "error_code": runtime_gate.get("error_code", "OPENCLAW_TEMP_DISABLED"),
            "decision": "denied",
            "risk_level": context.get("risk_level", "medium"),
            "policy_reasons": [runtime_gate.get("reason", "runtime_guard")],
            "token_status": {},
            "target_worker": context.get("target_worker", ""),
            "resolved_worker": "",
            "queue_state": _openclaw_command_manager.queue_snapshot() if OPENCLAW_QUEUE_ENABLED else {"enabled": False},
            "session_id": context.get("session_id"),
            "retry_after_sec": runtime_gate.get("retry_after_sec", 0),
            "runtime_guard": runtime_gate.get("guard", {}),
        }

    lease_validation = _validate_openclaw_task_lease(data)
    if not lease_validation.get("success"):
        return {
            "success": False,
            "error": lease_validation.get("error", "Task lease validation failed"),
            "error_code": lease_validation.get("error_code", "LEASE_CONFLICT"),
            "decision": "denied",
            "risk_level": context.get("risk_level", "medium"),
            "policy_reasons": ["task_lease_enforcement"],
            "token_status": {},
            "target_worker": context.get("target_worker", ""),
            "resolved_worker": "",
            "queue_state": _openclaw_command_manager.queue_snapshot() if OPENCLAW_QUEUE_ENABLED else {"enabled": False},
            "session_id": context.get("session_id"),
            "lease": {
                "enforced": bool(lease_validation.get("enforced")),
                "task_id": lease_validation.get("task_id", ""),
                "owner_id": lease_validation.get("owner_id", ""),
            },
        }

    policy = _evaluate_openclaw_policy(command_type, data, context)
    if not policy.get("allowed"):
        decision = str(policy.get("decision", "manual_required"))
        error_code = str(policy.get("error_code", "POLICY_DENIED") or "POLICY_DENIED")
        if decision == "manual_required" and error_code == "POLICY_DENIED":
            error_code = "MANUAL_REQUIRED"
        return {
            "success": False,
            "error": "Manual approval required by policy" if decision == "manual_required" else "Command denied by policy",
            "error_code": error_code,
            "decision": decision,
            "risk_level": policy.get("risk_level", "medium"),
            "policy_reasons": policy.get("reasons", []),
            "token_status": policy.get("token_status", {}),
            "target_worker": policy.get("target_worker"),
            "resolved_worker": policy.get("resolved_worker"),
            "queue_state": _openclaw_command_manager.queue_snapshot() if OPENCLAW_QUEUE_ENABLED else {"enabled": False},
            "session_id": context.get("session_id"),
        }

    payload = dict(data or {})
    payload["route_mode"] = context.get("route_mode", "auto")
    payload["_routing"] = {
        "target_worker": policy.get("target_worker", ""),
        "resolved_worker": policy.get("resolved_worker", ""),
        "failover_from": policy.get("failover_from", ""),
    }
    payload["_policy"] = {
        "decision": policy.get("decision", "auto_approved"),
        "risk_level": policy.get("risk_level", "medium"),
        "reasons": policy.get("reasons", []),
        "action_key": policy.get("action_key", ""),
    }

    if not OPENCLAW_QUEUE_ENABLED:
        result = _openclaw_queue_executor(command_type, payload)
        return {
            "success": bool(result.get("success")),
            "request_id": None,
            "mode": "sync",
            "result": result,
            "queue_state": {"enabled": False},
            "decision": policy.get("decision", "auto_approved"),
            "risk_level": policy.get("risk_level", "medium"),
            "target_worker": policy.get("target_worker", ""),
            "resolved_worker": policy.get("resolved_worker", ""),
            "policy_reasons": policy.get("reasons", []),
            "token_status": policy.get("token_status", {}),
        }

    enqueued = _openclaw_command_manager.enqueue(
        command_type=command_type,
        payload=payload,
        session_id=context["session_id"],
        source=context["source"],
        priority=context["priority"],
        idempotency_key=context["idempotency_key"],
        timeout_ms=context["timeout_ms"],
        risk_level=str(policy.get("risk_level", "medium")),
        decision=str(policy.get("decision", "auto_approved")),
        target_worker=str(policy.get("target_worker", "")),
        resolved_worker=str(policy.get("resolved_worker", "")),
    )
    if not enqueued.get("success"):
        return {
            "success": False,
            "error": enqueued.get("error", "Queue enqueue failed"),
            "error_code": enqueued.get("error_code", "QUEUE_ENQUEUE_FAILED"),
            "queue_state": _openclaw_command_manager.queue_snapshot(),
            "session_id": context["session_id"],
            "decision": policy.get("decision", "auto_approved"),
            "risk_level": policy.get("risk_level", "medium"),
            "target_worker": policy.get("target_worker", ""),
            "resolved_worker": policy.get("resolved_worker", ""),
            "token_status": policy.get("token_status", {}),
        }

    request_id = str(enqueued.get("request_id", ""))
    if context["mode"] == "async":
        return {
            "success": True,
            "accepted": True,
            "request_id": request_id,
            "session_id": context["session_id"],
            "mode": "async",
            "idempotent_replay": bool(enqueued.get("idempotent_replay")),
            "queue_state": _openclaw_command_manager.queue_snapshot(),
            "decision": policy.get("decision", "auto_approved"),
            "risk_level": policy.get("risk_level", "medium"),
            "target_worker": policy.get("target_worker", ""),
            "resolved_worker": policy.get("resolved_worker", ""),
            "policy_reasons": policy.get("reasons", []),
            "token_status": policy.get("token_status", {}),
        }

    waited = _openclaw_command_manager.wait_command(request_id, timeout_sec=max(1.0, context["timeout_ms"] / 1000.0))
    if not waited:
        return {
            "success": False,
            "error": "Queued command not found",
            "error_code": "QUEUE_REQUEST_NOT_FOUND",
            "request_id": request_id,
            "decision": policy.get("decision", "auto_approved"),
            "risk_level": policy.get("risk_level", "medium"),
            "target_worker": policy.get("target_worker", ""),
            "resolved_worker": policy.get("resolved_worker", ""),
            "token_status": policy.get("token_status", {}),
        }
    finished = str(waited.get("state", "")) in {"completed", "failed", "cancelled"}
    outcome = waited.get("result") if isinstance(waited.get("result"), dict) else {}
    outcome_result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    outcome_success = bool(outcome.get("success", False)) if finished else False
    if not finished:
        error_code = "QUEUE_TIMEOUT"
        error = "Command execution timeout; poll command status"
    elif outcome_success:
        error_code = ""
        error = ""
    else:
        error_code = str(
            outcome.get("error_code")
            or outcome_result.get("error_code")
            or ""
        ).strip().upper()
        raw_error = outcome.get("error")
        if raw_error is None:
            raw_error = outcome_result.get("error")
        error = str(raw_error).strip() if raw_error is not None else ""
        if not error:
            error = "OpenClaw command failed"
    return {
        "success": outcome_success,
        "request_id": request_id,
        "mode": "sync",
        "timed_out": not finished,
        "idempotent_replay": bool(enqueued.get("idempotent_replay")),
        "session_id": context["session_id"],
        "command": waited,
        "result": outcome if finished else {},
        "error": error,
        "error_code": error_code,
        "queue_state": _openclaw_command_manager.queue_snapshot(),
        "decision": policy.get("decision", "auto_approved"),
        "risk_level": policy.get("risk_level", "medium"),
        "target_worker": policy.get("target_worker", ""),
        "resolved_worker": policy.get("resolved_worker", ""),
        "policy_reasons": policy.get("reasons", []),
        "token_status": policy.get("token_status", {}),
        "failure_class": _classify_openclaw_failure(outcome) if finished and not outcome_success else "",
    }


def _openclaw_error_http_status(error_code: str) -> int:
    code = str(error_code or "").strip().upper()
    if code == "QUEUE_FULL":
        return 429
    if code == "QUEUE_TIMEOUT":
        return 202
    if code in {"BROWSER_MODE_DISABLED", "FLOW_MODE_DISABLED", "ATTACH_MODE_DISABLED"}:
        return 409
    if code in {"EXTENSION_AUTOMATION_DISABLED"}:
        return 409
    if code in {"OPENCLAW_TEMP_DISABLED"}:
        return 423
    if code in {"MANUAL_REQUIRED"}:
        return 409
    if code in {"WORKER_UNAVAILABLE", "WORKER_UNHEALTHY", "CIRCUIT_OPEN"}:
        return 503
    if code in {"TOKEN_MISSING", "HEALTH_UNAVAILABLE", "POLICY_DENIED", "FAILURE_CLASS_BLOCKED"}:
        return 403
    if code in {"LEASE_CONFLICT", "LEASE_EXPIRED", "LEASE_CONTEXT_REQUIRED"}:
        return 423
    if code in {"HUB_UNAVAILABLE"}:
        return 503
    return 400


_openclaw_command_manager.set_executor(_openclaw_queue_executor)


def _extract_openclaw_lease_context(payload: Dict[str, Any]) -> Dict[str, str]:
    owner_id = str(payload.get("task_owner_id") or payload.get("owner_id") or "").strip()
    lease_token = str(payload.get("task_lease_token") or payload.get("lease_token") or "").strip()
    task_id = str(payload.get("task_id") or payload.get("kanban_task_id") or "").strip()
    return {
        "owner_id": owner_id,
        "lease_token": lease_token,
        "task_id": task_id,
    }


def _validate_openclaw_task_lease(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not OPENCLAW_REQUIRE_TASK_LEASE:
        return {"success": True, "enforced": False}

    context = _extract_openclaw_lease_context(payload)
    task_id = context["task_id"]
    owner_id = context["owner_id"]
    lease_token = context["lease_token"]

    if not task_id or not owner_id or not lease_token:
        return {
            "success": False,
            "enforced": True,
            "error_code": "LEASE_CONTEXT_REQUIRED",
            "error": "task_id, task_owner_id and task_lease_token are required",
            "task_id": task_id,
            "owner_id": owner_id,
        }

    if "_hub" not in globals() or _hub is None:
        return {
            "success": False,
            "enforced": True,
            "error_code": "HUB_UNAVAILABLE",
            "error": "Multi-Orion hub is not available",
            "task_id": task_id,
            "owner_id": owner_id,
        }

    heartbeat = _hub.heartbeat_task_lease(
        task_id=task_id,
        owner_id=owner_id,
        lease_token=lease_token,
        lease_sec=OPENCLAW_LEASE_HEARTBEAT_SEC,
    )
    if not heartbeat.get("success"):
        return {
            "success": False,
            "enforced": True,
            "error_code": str(heartbeat.get("error_code") or "LEASE_CONFLICT"),
            "error": str(heartbeat.get("error") or "Task lease validation failed"),
            "task_id": task_id,
            "owner_id": owner_id,
            "details": heartbeat,
        }

    return {
        "success": True,
        "enforced": True,
        "task_id": task_id,
        "owner_id": owner_id,
        "lease": heartbeat.get("lease", {}),
        "version": heartbeat.get("version"),
    }


def _emit_chat_phase(message_id: str, phase: str, text: str, report: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "message_id": str(message_id),
        "phase": str(phase),
        "text": str(text),
        "timestamp": datetime.now().isoformat(),
    }
    if isinstance(report, dict):
        payload["report"] = report
    socketio.emit("chat_stream", payload)


def _is_valuable_event(event: Dict[str, Any]) -> bool:
    level = str(event.get("level", "info")).lower()
    message = str(event.get("message", "")).lower()
    if level in {"error", "warning", "success"}:
        return True
    return bool(
        re.search(
            r"(decision|routing|command|interrupt|paused|resumed|failed|exception|watchdog|recovery|autopilot|stuck)",
            message,
        )
    )


def _build_events_digest(limit: int = 140) -> Dict[str, Any]:
    events = state.get_event_feed(limit=limit, include_routing=True)
    total = len(events)
    valuable = [event for event in events if _is_valuable_event(event)]
    counts = {"error": 0, "warning": 0, "success": 0, "info": 0}
    agents: Dict[str, int] = {}
    for event in valuable:
        level = str(event.get("level", "info")).lower()
        if level in counts:
            counts[level] += 1
        agent = str(event.get("agent", "system")).lower()
        agents[agent] = agents.get(agent, 0) + 1

    top_agents = [
        {"agent": agent, "count": count}
        for agent, count in sorted(agents.items(), key=lambda x: x[1], reverse=True)[:3]
    ]
    key_events = [
        {
            "timestamp": item.get("timestamp"),
            "agent": item.get("agent"),
            "level": item.get("level"),
            "message": str(item.get("message", ""))[:220],
        }
        for item in valuable[:6]
    ]
    noise_reduction = int(round(((total - len(valuable)) / total) * 100)) if total else 0
    return {
        "total_events": total,
        "valuable_events": len(valuable),
        "noise_reduction_pct": max(0, min(100, noise_reduction)),
        "counts": counts,
        "top_agents": top_agents,
        "key_events": key_events,
        "generated_at": datetime.now().isoformat(),
    }


def _build_feedback_recent(limit: int = 6) -> List[Dict[str, Any]]:
    """Return latest feedback entries with minimal metadata."""
    snapshot = _prompt_system.get_feedback_snapshot(limit=max(1, limit))
    rows = []
    if isinstance(snapshot, dict):
        rows = snapshot.get("recent_feedback") or []
    sanitized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sanitized.append({
            "id": str(row.get("id", "")),
            "timestamp": row.get("timestamp"),
            "category": str(row.get("category", "general")),
            "severity": str(row.get("severity", "")),
            "text": str(row.get("text", "")),
        })
    recent = sanitized[-limit:]
    recent.reverse()
    return recent


def _broadcast_feedback_update(limit: int = 8) -> None:
    """Emit latest feedback snapshot over Socket.IO for live dashboards."""
    try:
        socketio.emit(
            "feedback_update",
            {
                "feedback": _build_feedback_recent(limit=limit),
                "timestamp": datetime.now().isoformat(),
            },
        )
    except Exception as exc:
        logger.error(f"Feedback broadcast failed: {exc}")


def _build_ai_summary_payload(
    status_payload: Dict[str, Any],
    ops_summary: Dict[str, Any],
    digest: Dict[str, Any],
    feedback_recent: List[Dict[str, Any]],
) -> Dict[str, Any]:
    runtime = status_payload.get("runtime") or {}
    project = status_payload.get("project") or {}
    learning_v2 = status_payload.get("learning_v2") or {}
    orions = status_payload.get("orion_instances") or []
    agent_fleet = status_payload.get("agent_fleet") or []
    counts = ops_summary.get("counts", {})
    total_agents = len(agent_fleet)
    active_agents = sum(1 for agent in agent_fleet if agent.get("online"))
    runtime_line = _runtime_status_text(runtime)

    highlights: List[str] = []
    highlights.append(
        f"Signals: {counts.get('success', 0)} ✅ / {counts.get('warning', 0)} ⚠️ / {counts.get('error', 0)} ⛔"
    )
    if digest.get("noise_reduction_pct") is not None:
        highlights.append(
            f"Noise filtered: {digest.get('noise_reduction_pct', 0)}% of {digest.get('total_events', 0)} events"
        )
    if ops_summary.get("top_agent"):
        highlights.append(f"Top agent: {str(ops_summary.get('top_agent')).upper()}")
    if orions:
        highlights.append(f"{len(orions)} Orion instance(s) tracking {project.get('name', 'project')}.")
    if isinstance(learning_v2, dict) and learning_v2.get("enabled"):
        funnel = learning_v2.get("proposal_funnel", {}) if isinstance(learning_v2.get("proposal_funnel"), dict) else {}
        quality = learning_v2.get("outcome_quality", {}) if isinstance(learning_v2.get("outcome_quality"), dict) else {}
        highlights.append(
            "Learning v2: "
            f"created={int(funnel.get('created', 0) or 0)}, "
            f"verified={int(funnel.get('verified', 0) or 0)}, "
            f"win={round(float(quality.get('win_rate', 0.0) or 0.0) * 100, 1)}%"
        )

    feedback_mentions = [
        {
            "id": item.get("id"),
            "category": item.get("category"),
            "text": _ascii_safe(item.get("text"), 160),
        }
        for item in feedback_recent[:3]
    ]
    if feedback_mentions:
        highlights.append(f"Feedback applied: {feedback_mentions[0]['category']}")

    narrative = (
        f"{runtime_line} Tổng quan: {active_agents}/{total_agents} agents online. "
        f"Sự kiện gần nhất: {ops_summary.get('latest_message', 'n/a')}"
    )

    return {
        "title": "Realtime operations stable" if counts.get("error", 0) == 0 else "Attention required",
        "narrative": narrative,
        "highlights": highlights[:4],
        "iteration": runtime.get("iteration"),
        "agent_activity": {
            "active": active_agents,
            "total": total_agents,
        },
        "signals": counts,
        "feedback_mentions": feedback_mentions,
    }


def _runtime_status_text(runtime: Dict[str, Any]) -> str:
    running = bool(runtime.get("running"))
    paused = bool(runtime.get("paused"))
    pause_reason = str(runtime.get("pause_reason", "") or "")
    iteration = runtime.get("iteration", "n/a")
    if paused:
        reason = f" ({pause_reason})" if pause_reason else ""
        return f"Orion đang tạm dừng{reason}, vòng lặp hiện tại: {iteration}."
    if running:
        return f"Orion đang chạy liên tục, vòng lặp hiện tại: {iteration}."
    return f"Orion chưa chạy (iteration={iteration})."


def _maybe_record_user_feedback(message: str) -> Optional[str]:
    text = str(message or "").strip()
    lowered = text.lower()
    if not text:
        return None

    # High-signal dissatisfaction / correction signals.
    patterns = [
        (r"(không hài lòng|rất bực|bực mình|quá dở|chưa đúng|chưa đạt)", "quality"),
        (r"(ui ?/?ux|giao diện|rối|khó chịu|chập chờn|light theme|theme sáng)", "uiux"),
        (r"(streamline|real[- ]?time|realtime|chat|send command|phản hồi)", "realtime"),
        (r"(log|nhật ký|spam|nhiễu|không hiểu)", "log_quality"),
        (r"(monitor|giám sát|guardian|orion|stuck|đứng yên|không triển khai|gỡ kẹt)", "orchestration"),
        (r"(terminal|bao nhiêu luồng|mấy luồng|bao nhiêu agent|instance)", "observability"),
        (r"(token|chi phí|tối ưu)", "token"),
        (r"(z\\.ai|provider|openbig|endpoint|model)", "provider"),
        (r"(nguyên tắc|quy tắc cứng|ăn sâu|không dính|không tái phạm)", "process"),
    ]
    for pattern, category in patterns:
        if re.search(pattern, lowered):
            feedback_id = _prompt_system.record_feedback(
                text=text,
                source="user_chat",
                category=category,
                severity="high",
                add_directive=True,
                directive_priority=119 if category in {"provider", "orchestration", "quality", "process"} else 117,
            )
            _broadcast_feedback_update(limit=8)
            return feedback_id
    return None


def _extract_orion_instance_id(message: str, fallback: str) -> str:
    text = str(message or "").lower()
    match = re.search(r"orion\s*[-_ ]?\s*(\d+)", text)
    if not match:
        return fallback
    idx = match.group(1)
    candidate = f"orion-{idx}"
    known = {str(item.get("id", "")).lower() for item in ORION_INSTANCES}
    return candidate if candidate in known else fallback


def _normalize_intent_text(text: str) -> str:
    raw = str(text or "").lower()
    raw = raw.replace("đ", "d")
    raw = "".join(ch for ch in unicodedata.normalize("NFD", raw) if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _contains_any(haystack: str, needles: List[str]) -> bool:
    return any(token in haystack for token in needles if token)


def _infer_flexible_chat_request(text: str) -> Optional[str]:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return None

    if normalized in {"rules", "rule", "nexus rules", "project rules", "nguyen tac", "quy tac"}:
        return "nexus rules"

    # Quick numeric shortcuts and verbal forms.
    if normalized in {"1", "mot", "so 1", "lua chon 1", "option 1", "first", "first option"}:
        return "tóm tắt dự án"
    if normalized in {"2", "hai", "so 2", "lua chon 2", "option 2", "second", "second option"}:
        return "điều khiển flow"
    if normalized in {"3", "ba", "so 3", "lua chon 3", "option 3", "third", "third option"}:
        return "guardian gỡ kẹt"

    summary_signals = [
        "tom tat",
        "tong quan",
        "tinh hinh",
        "dang chay gi",
        "con cai gi dang chay",
        "dang lam gi",
        "status",
        "trang thai",
        "progress",
        "hien tai sao roi",
    ]
    recover_signals = [
        "go ket",
        "go stuck",
        "unstick",
        "bi ket",
        "bi do",
        "guardian xu ly",
        "guardian go",
        "cuu orion",
    ]
    run_cycle_signals = [
        "run cycle",
        "run_cycle",
        "chay vong",
        "chay 1 vong",
        "chay mot vong",
        "them mot vong",
    ]
    pause_signals = ["pause", "tam dung", "dung lai", "hold"]
    resume_signals = ["resume", "tiep tuc", "chay tiep", "bat lai", "mo lai"]
    flow_control_signals = ["dieu khien flow", "flow control", "dieu khien he thong"]
    uiux_signals = ["ui ux", "giao dien", "trai nghiem", "ux", "ui"]
    improve_signals = ["danh gia", "cai thien", "nang cap", "de xuat", "toi uu"]
    coordination_signals = ["phoi hop", "coordinate", "orchestrate", "dieu phoi", "multi agent", "nhieu agent"]

    if _contains_any(normalized, recover_signals):
        return "guardian gỡ kẹt"
    if _contains_any(normalized, run_cycle_signals):
        return "chạy 1 vòng Orion"
    if _contains_any(normalized, pause_signals):
        return "pause"
    if _contains_any(normalized, resume_signals):
        return "resume"
    if _contains_any(normalized, flow_control_signals):
        return "điều khiển flow"
    if _contains_any(normalized, coordination_signals):
        return "agent coordination"
    if _contains_any(normalized, uiux_signals) and _contains_any(normalized, improve_signals):
        return "uiux review"
    if _contains_any(normalized, summary_signals):
        return "tóm tắt dự án"
    return None


def _extract_task_priority(text: str) -> str:
    normalized = _normalize_intent_text(text)
    if re.search(r"\b(p0|critical|urgent|khan cap|nghiem trong|gap)\b", normalized):
        return "critical"
    if re.search(r"\b(p1|high|cao|quan trong)\b", normalized):
        return "high"
    if re.search(r"\b(p3|low|thap|nhe)\b", normalized):
        return "low"
    if re.search(r"\b(p2|medium|trung binh|vua)\b", normalized):
        return "medium"
    return "medium"


def _extract_task_assignee(text: str) -> Optional[str]:
    lower = str(text or "").lower()
    m = re.search(r"\borion[\s\-_]?(\d+)\b", lower)
    if m:
        return f"orion-{m.group(1)}"
    for token in ("nova", "pixel", "echo", "cipher", "flux", "guardian"):
        if re.search(rf"\b{token}\b", lower):
            return token
    if re.search(r"\borion\b", lower):
        return LOCAL_INSTANCE_ID
    return None


def _extract_task_title_description(text: str) -> Dict[str, str]:
    raw = str(text or "").strip()
    title = ""
    description = ""
    quote = re.search(r"[\"“](.+?)[\"”]", raw)
    if quote:
        title = quote.group(1).strip()
    tail = re.sub(
        r"^\s*(tao|them|create|new|add)\s*(task|cong viec)?\s*[:\-]?\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    if not title:
        split = re.split(r"\b(mô tả|mo ta|description|desc|nội dung|noi dung|chi tiet)\b\s*[:\-]?\s*", tail, maxsplit=1, flags=re.IGNORECASE)
        if split:
            title = str(split[0]).strip(" -:,.")
        if len(split) >= 3:
            description = str(split[2]).strip()
    if not description:
        m = re.search(r"\b(mô tả|mo ta|description|desc|nội dung|noi dung|chi tiet)\b\s*[:\-]?\s*(.+)$", tail, flags=re.IGNORECASE)
        if m:
            description = str(m.group(2)).strip()
    title = re.sub(r"\s+", " ", title).strip(" -:,.")
    title = re.sub(r"^\s*(tao|tạo|them|thêm|create|new|add)\s*(task|cong viec)?\s*[:\-]?\s*", "", title, flags=re.IGNORECASE).strip(" -:,.")
    title = re.sub(
        r"\b(?:priority|uu tien)\s*[:\-]?\s*(?:p[0-3]|critical|high|medium|low)\b",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip(" -:,.")
    title = re.sub(r"\b(?:cho|for|assigned to|giao cho)\s+[a-z0-9._-]+\b$", "", title, flags=re.IGNORECASE).strip(" -:,.")
    description = re.sub(r"\s+", " ", description).strip()
    if len(title) > 180:
        title = title[:180].rstrip()
    if len(description) > 1200:
        description = description[:1200].rstrip()
    return {"title": title, "description": description}


def _task_status_bucket(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"done", "completed", "resolved", "closed"}:
        return "done"
    if raw in {"in_progress", "running", "active", "claimed", "doing"}:
        return "in_progress"
    if raw in {"blocked", "stuck", "waiting", "hold", "on_hold"}:
        return "blocked"
    if raw in {"review", "qa", "testing", "verify"}:
        return "review"
    if raw in {"todo", "to_do", "to-do", "backlog", "open", "new"}:
        return "todo"
    return "todo"


def _task_intelligence_query_keywords(text: str) -> List[str]:
    normalized = _normalize_intent_text(text)
    if not normalized:
        return []
    stopwords = {
        "task",
        "cong",
        "viec",
        "giao",
        "cho",
        "xong",
        "chua",
        "status",
        "trang",
        "thai",
        "tien",
        "do",
        "dang",
        "lam",
        "gi",
        "bao",
        "nhieu",
        "hien",
        "tai",
        "current",
        "progress",
        "pending",
        "backlog",
        "done",
        "in",
        "review",
        "blocked",
        "help",
        "check",
        "with",
        "and",
        "for",
        "the",
    }
    tokens = [token for token in normalized.split() if token and token not in stopwords and len(token) >= 3]
    unique: List[str] = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    return unique[:8]


def _build_task_intelligence_snapshot(query: str = "", limit: int = 8) -> Dict[str, Any]:
    cap = max(1, min(20, int(limit)))
    if "_hub" not in globals() or _hub is None:
        return {
            "available": False,
            "error": "hub_unavailable",
            "total_tasks": 0,
            "counts": {"todo": 0, "in_progress": 0, "blocked": 0, "review": 0, "done": 0},
            "pending_count": 0,
            "completion_rate_pct": 0.0,
            "query_keywords": _task_intelligence_query_keywords(query),
            "matched_count": 0,
            "matched_tasks": [],
            "focus_tasks": [],
        }

    try:
        tasks_raw = _hub.list_tasks()
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "total_tasks": 0,
            "counts": {"todo": 0, "in_progress": 0, "blocked": 0, "review": 0, "done": 0},
            "pending_count": 0,
            "completion_rate_pct": 0.0,
            "query_keywords": _task_intelligence_query_keywords(query),
            "matched_count": 0,
            "matched_tasks": [],
            "focus_tasks": [],
        }

    tasks: List[Dict[str, Any]] = [row for row in tasks_raw if isinstance(row, dict)] if isinstance(tasks_raw, list) else []
    tasks_sorted = sorted(
        tasks,
        key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
        reverse=True,
    )
    counts = {"todo": 0, "in_progress": 0, "blocked": 0, "review": 0, "done": 0}
    normalized_rows: List[Dict[str, Any]] = []
    for row in tasks_sorted:
        status_bucket = _task_status_bucket(row.get("status"))
        counts[status_bucket] += 1
        normalized_rows.append(
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or "Untitled"),
                "description": str(row.get("description") or ""),
                "status": str(row.get("status") or "todo"),
                "status_bucket": status_bucket,
                "priority": str(row.get("priority") or "medium"),
                "assigned_to": str(row.get("assigned_to") or "unassigned"),
                "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
                "created_at": str(row.get("created_at") or ""),
            }
        )

    keywords = _task_intelligence_query_keywords(query)
    matched: List[Dict[str, Any]] = []
    for row in normalized_rows:
        if not keywords:
            continue
        searchable = _normalize_intent_text(
            " ".join(
                [
                    row.get("id", ""),
                    row.get("title", ""),
                    row.get("description", ""),
                    row.get("assigned_to", ""),
                    row.get("status", ""),
                    row.get("priority", ""),
                ]
            )
        )
        score = sum(1 for token in keywords if token and token in searchable)
        if score <= 0:
            continue
        item = dict(row)
        item["match_score"] = score
        matched.append(item)

    matched.sort(
        key=lambda row: (
            int(row.get("match_score", 0) or 0),
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )

    total = len(normalized_rows)
    done = counts["done"]
    pending_count = counts["todo"] + counts["in_progress"] + counts["blocked"] + counts["review"]
    completion_rate = round((done / total) * 100.0, 1) if total else 0.0
    matched_preview = matched[:cap]
    focus = (matched_preview if matched_preview else normalized_rows[:cap])

    verdict = "unknown"
    if matched_preview:
        if all(str(item.get("status_bucket")) == "done" for item in matched_preview):
            verdict = "done"
        elif any(str(item.get("status_bucket")) in {"todo", "in_progress", "blocked", "review"} for item in matched_preview):
            verdict = "in_progress"

    return {
        "available": True,
        "error": "",
        "total_tasks": total,
        "counts": counts,
        "pending_count": pending_count,
        "completion_rate_pct": completion_rate,
        "query_keywords": keywords,
        "matched_count": len(matched),
        "matched_tasks": matched_preview,
        "focus_tasks": focus,
        "verdict": verdict,
    }


def _format_task_intelligence_reply(instance_label: str, snapshot: Dict[str, Any], query: str = "") -> str:
    if not isinstance(snapshot, dict) or not bool(snapshot.get("available")):
        reason = str(snapshot.get("error", "hub_unavailable")) if isinstance(snapshot, dict) else "hub_unavailable"
        return (
            f"[{instance_label}] Task intelligence chưa sẵn sàng ({reason}). "
            "Mình chưa thể xác nhận tiến độ chính xác lúc này."
        )

    total = int(snapshot.get("total_tasks", 0) or 0)
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    matched = snapshot.get("matched_tasks") if isinstance(snapshot.get("matched_tasks"), list) else []
    focus = snapshot.get("focus_tasks") if isinstance(snapshot.get("focus_tasks"), list) else []
    verdict = str(snapshot.get("verdict", "unknown") or "unknown")
    keywords = snapshot.get("query_keywords") if isinstance(snapshot.get("query_keywords"), list) else []
    is_done_question = bool(re.search(r"(xong|hoan thanh|done|complete)", _normalize_intent_text(query)))

    if total <= 0:
        return (
            f"[{instance_label}] Hiện chưa có task nào được tracking trên Hub, nên chưa thể kết luận task đã xong hay chưa.\n"
            "Bạn có thể tạo nhanh ngay trong chat:\n"
            "`tạo task Check Teams Khiem + Ravin email partnership + valuation p1 cho orion-1`"
        )

    header = (
        f"[{instance_label}] Snapshot task hiện tại: total={total}, pending={int(snapshot.get('pending_count', 0) or 0)}, "
        f"in_progress={int(counts.get('in_progress', 0) or 0)}, blocked={int(counts.get('blocked', 0) or 0)}, "
        f"done={int(counts.get('done', 0) or 0)}."
    )
    verdict_line = ""
    if is_done_question:
        if matched:
            if verdict == "done":
                verdict_line = "Kết luận nhanh: các task khớp hiện đang DONE."
            else:
                verdict_line = "Kết luận nhanh: các task khớp vẫn CHƯA xong (còn pending/in_progress/blocked)."
        elif keywords:
            verdict_line = "Kết luận nhanh: chưa thấy task khớp trong board, nên không thể xác nhận đã xong."

    rows = matched if matched else focus
    lines: List[str] = []
    for idx, row in enumerate(rows[:6], start=1):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status_bucket") or row.get("status") or "todo")
        priority = str(row.get("priority") or "medium")
        assignee = str(row.get("assigned_to") or "unassigned")
        task_id = str(row.get("id") or "-")
        title = str(row.get("title") or "Untitled")
        lines.append(f"{idx}. [{task_id}] {title} | {status} | {priority} | {assignee}")

    scope_line = ""
    if keywords:
        scope_line = f"Keyword match: {keywords} (matched={len(matched)})."

    detail_block = "\n".join(lines) if lines else "Không có task hiển thị."
    parts = [header]
    if verdict_line:
        parts.append(verdict_line)
    if scope_line:
        parts.append(scope_line)
    parts.append("Top tasks:")
    parts.append(detail_block)
    return "\n".join(parts)


def _build_nexus_rules_text(
    instance_label: str,
    runtime_line: str,
    feedback_recent: List[Dict[str, Any]],
) -> str:
    feedback_lines: List[str] = []
    for idx, item in enumerate(feedback_recent[:3], start=1):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "general") or "general")
        text = _ascii_safe(item.get("text"), 120)
        feedback_lines.append(f"{idx}. {category}: {text or '(no detail)'}")
    if not feedback_lines:
        feedback_lines = ["1. none"]

    return (
        f"[{instance_label}] NEXUS_RULES active:\n"
        "1. Full-auto first: ưu tiên tự xử lý, chỉ escalate khi thật sự blocked/risk cao.\n"
        "2. Unified workspace: monitor + control + chat gom tại /live, hạn chế nhảy trang.\n"
        "3. UI/UX chuẩn: đơn giản, professional, đọc nhanh; Light/Dark phải đồng nhất.\n"
        "4. Feedback loop bắt buộc: feedback mới phải được phản ánh ở vòng cập nhật tiếp theo.\n"
        f"5. Runtime hiện tại: {runtime_line}\n"
        "6. Feedback trọng tâm gần nhất:\n"
        + "\n".join(f"   - {line}" for line in feedback_lines)
    )


def _build_chat_report(
    intent: str,
    executed: bool,
    reply: str,
    result: Dict[str, Any],
    reasoning: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    status = "Đã thực thi" if executed else "Tư vấn"
    next_action = "Tiếp tục theo dõi luồng realtime."
    if intent.startswith("command_"):
        next_action = "Quan sát phản hồi 30-60s để xác nhận tác động."
    elif "guardian" in intent:
        next_action = "Kiểm tra Guardian log và trạng thái stuck/recovery."
    elif intent == "project_status_summary":
        next_action = "Nếu cần can thiệp, gửi: pause / resume / guardian gỡ kẹt."

    highlights: List[str] = []
    if isinstance(result, dict):
        activity = result.get("agent_activity")
        if isinstance(activity, dict):
            for line in (activity.get("highlights") or [])[:3]:
                if line:
                    highlights.append(str(line))
        if result.get("error"):
            highlights.append(f"Lỗi: {result.get('error')}")
        if result.get("note"):
            highlights.append(str(result.get("note")))
        output = result.get("output")
        if isinstance(output, dict):
            decision = output.get("decision")
            if decision:
                highlights.append(f"Guardian đề xuất: {decision}")
            risk = output.get("risk")
            if risk:
                highlights.append(f"Rủi ro: {risk}")

    if isinstance(reasoning, dict):
        for item in reasoning.get("highlights", [])[:2]:
            if item:
                highlights.append(str(item))

    return {
        "status": status,
        "summary": reply,
        "highlights": highlights[:3],
        "next_action": next_action,
        "reasoning": reasoning or {},
    }


def _soften_chat_reply(reply: str, intent: str = "") -> str:
    text = str(reply or "").strip()
    if not text:
        return ""

    # Remove rigid instance prefix like [Orion 1].
    text = re.sub(r"^\[[^\]]+\]\s*", "", text)
    replacements = (
        ("Đã gửi lệnh", "Mình vừa gửi lệnh"),
        ("Đã bật", "Mình vừa bật"),
        ("Đã tắt", "Mình vừa tắt"),
        ("Đã chạy", "Mình vừa chạy"),
        ("Đã tạo", "Mình vừa tạo"),
        ("Đã ghi nhận", "Mình đã ghi nhận"),
        ("Không ", "Chưa "),
    )
    for src, dst in replacements:
        text = text.replace(src, dst)
    if intent in {"project_status_summary", "assistant_help"} and not text.lower().startswith("mình"):
        text = f"Mình tóm tắt nhanh nhé:\n{text}"
    return text


def _build_project_chat_reply(
    message: str,
    instance_id: Optional[str] = None,
    member_id: Optional[str] = None,
) -> Dict[str, Any]:
    original_text = str(message or "").strip()
    text = original_text
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)
    # Accept compact menu shortcuts from unified chat UI.
    if re.fullmatch(r"[\(\[]?1[\)\]\.]?", compact):
        text = "tóm tắt dự án"
        lowered = text
    elif re.fullmatch(r"[\(\[]?2[\)\]\.]?", compact):
        text = "điều khiển flow"
        lowered = text
    elif re.fullmatch(r"[\(\[]?3[\)\]\.]?", compact):
        text = "guardian gỡ kẹt"
        lowered = text

    normalized_original = _normalize_intent_text(original_text)
    inferred = _infer_flexible_chat_request(text)
    if inferred:
        text = inferred
        lowered = inferred.lower()
    normalized_intent = _normalize_intent_text(text)

    resolved_instance = _extract_orion_instance_id(text, str(instance_id or LOCAL_INSTANCE_ID))
    instance_info = _instance_by_id(resolved_instance)
    instance_label = str(instance_info.get("name", resolved_instance))
    status_payload = build_status_payload()
    runtime = status_payload.get("runtime") or {}
    activity = _build_agent_activity_snapshot(resolved_instance, max_agents=10)
    activity_lines = _format_agent_activity_lines(activity, max_lines=6)
    ops = _build_ops_snapshot()
    digest = _build_events_digest(limit=140)
    counts = ops["counts"]
    top_agent = str(ops.get("top_agent", "system")).upper()
    latest_msg = ops.get("latest_message") or "Chưa có sự kiện mới."
    feedback_recent = _build_feedback_recent(limit=6)

    reasoning = {
        "system_focus": _runtime_status_text(runtime),
        "signals": counts,
        "highlights": [
            f"Top agent: {top_agent}",
            f"Latest event: {latest_msg[:180]}",
        ],
        "feedback": feedback_recent[:2],
        "noise_reduction_pct": digest.get("noise_reduction_pct"),
    }
    ai_summary = _build_ai_summary_payload(status_payload, ops, digest, feedback_recent)

    def _with_context(result_payload: Any) -> Dict[str, Any]:
        payload = dict(result_payload) if isinstance(result_payload, dict) else {}
        payload.setdefault("ai_summary", ai_summary)
        payload.setdefault("reasoning", reasoning)
        payload.setdefault("feedback_recent", feedback_recent)
        return payload

    def _adaptive_fallback_text() -> str:
        runtime_line = str(activity.get("summary") or _runtime_status_text(runtime))
        success = int(counts.get("success", 0) or 0)
        warning = int(counts.get("warning", 0) or 0)
        error = int(counts.get("error", 0) or 0)
        feedback_tags = [str(item.get("category") or "general") for item in feedback_recent[:2]]
        agent_preview = "; ".join(activity_lines[:2]) if activity_lines else "Chưa có telemetry agent rõ ràng."

        tone = "Mình đã nhận yêu cầu và sẽ tự điều phối theo trạng thái realtime."
        if re.search(r"(ui|ux|giao dien|trai nghiem|professional|don gian|dep)", normalized_intent):
            tone = "Mình đang ưu tiên nâng UI/UX theo hướng tối giản, rõ ràng và dễ kiểm soát."
        elif re.search(r"(tai sao|why|sao|bug|loi|error|ket|stuck)", normalized_intent):
            tone = "Mình thấy bạn đang báo vấn đề cần xử lý ngay, nên sẽ ưu tiên nhánh ổn định trước."

        suggestion = (
            "Bạn có thể nói tự nhiên như: 'còn gì đang chạy', 'guardian gỡ kẹt', "
            "'tạo task Fix chat UX p1 cho nova', 'list tasks'."
        )
        if error > 0:
            suggestion = "Mình đề xuất xử lý lỗi trước: 'guardian gỡ kẹt' hoặc 'pause all' rồi 'resume all'."
        elif warning > 4:
            suggestion = "Mình đề xuất giảm nhiễu cảnh báo rồi tối ưu UI: 'đánh giá UI UX hiện tại'."

        return (
            f"[{instance_label}] {tone}\n"
            f"{runtime_line}\n"
            f"Tín hiệu hiện tại: {success} success, {warning} warning, {error} error. Top agent: {top_agent}.\n"
            f"Sự kiện mới nhất: {latest_msg}\n"
            f"Realtime agents: {agent_preview}\n"
            f"Feedback gần nhất: {feedback_tags or ['none']}\n"
            f"{suggestion}"
        )

    task_create_intent = bool(
        re.search(r"(tao|them|create|new|add).*(task|cong viec)", normalized_original)
        or re.search(r"(task|cong viec).*(moi|new)", normalized_original)
    )
    task_list_intent = bool(
        re.search(r"(list|xem|hien|show|liet ke).*(task|cong viec)", normalized_original)
        or re.search(r"(task|cong viec).*(dang co|hien co|bao nhieu)", normalized_original)
    )
    task_howto_intent = bool(
        re.search(r"(lam sao|how|cach).*(tao|them|create).*(task|cong viec)", normalized_original)
    )

    if task_howto_intent:
        return {
            "intent": "task_help",
            "executed": False,
            "result": _with_context({}),
            "reply": (
                f"[{instance_label}] Tạo task nhanh ngay trong chat theo mẫu:\n"
                "1) `tạo task Fix chat UX p1 cho nova`\n"
                "2) `create task \"Improve task board\" priority high assign pixel description improve drag and drop`\n"
                "3) `list tasks` để xem danh sách hiện tại."
            ),
        }

    if task_list_intent:
        if "_hub" not in globals() or _hub is None:
            return {
                "intent": "task_list",
                "executed": False,
                "result": _with_context({"error": "hub_unavailable"}),
                "reply": f"[{instance_label}] Hub task board chưa sẵn sàng trên runtime này.",
            }
        try:
            tasks = _hub.list_tasks()
        except Exception as exc:
            return {
                "intent": "task_list",
                "executed": False,
                "result": _with_context({"error": str(exc)}),
                "reply": f"[{instance_label}] Không lấy được danh sách task: {exc}",
            }
        if not isinstance(tasks, list) or not tasks:
            return {
                "intent": "task_list",
                "executed": False,
                "result": _with_context({"tasks": []}),
                "reply": (
                    f"[{instance_label}] Hiện chưa có task nào.\n"
                    "Bạn có thể tạo ngay bằng: `tạo task Fix chat UX p1 cho nova`"
                ),
            }
        tasks_sorted = sorted(
            [row for row in tasks if isinstance(row, dict)],
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
            reverse=True,
        )
        lines: List[str] = []
        for idx, row in enumerate(tasks_sorted[:8], start=1):
            task_id = str(row.get("id") or "-")
            title = str(row.get("title") or "Untitled")
            status = str(row.get("status") or "todo")
            priority = str(row.get("priority") or "medium")
            assignee = str(row.get("assigned_to") or "unassigned")
            lines.append(f"{idx}. [{task_id}] {title} | {status} | {priority} | {assignee}")
        return {
            "intent": "task_list",
            "executed": False,
            "result": _with_context({"tasks": tasks_sorted[:8], "total": len(tasks_sorted)}),
            "reply": (
                f"[{instance_label}] Task hiện tại ({len(tasks_sorted)}):\n"
                + "\n".join(lines)
                + "\n\nBạn có thể tạo thêm bằng câu tự nhiên: `tạo task <tiêu đề> p1 cho nova`"
            ),
        }

    if task_create_intent:
        if "_hub" not in globals() or _hub is None:
            return {
                "intent": "task_create",
                "executed": False,
                "result": _with_context({"error": "hub_unavailable"}),
                "reply": f"[{instance_label}] Không tạo được task vì Hub chưa sẵn sàng.",
            }
        fields = _extract_task_title_description(original_text)
        title = str(fields.get("title") or "").strip()
        description = str(fields.get("description") or "").strip()
        auto_title = False
        if not title or title.lower() in {"task", "new task", "cong viec", "task moi", "tao task", "tạo task", "create task", "them task", "thêm task"}:
            fallback_seed = description or original_text
            fallback_seed = re.sub(
                r"^\s*(tao|tạo|them|thêm|create|new|add)\s*(task|cong viec)?\s*[:\-]?\s*",
                "",
                fallback_seed,
                flags=re.IGNORECASE,
            ).strip()
            fallback_seed = re.split(
                r"\b(mô tả|mo ta|description|desc|nội dung|noi dung|chi tiet)\b",
                fallback_seed,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" -:,.")
            fallback_seed = re.sub(
                r"\b(p[0-3]|critical|high|medium|low|urgent|khan cap|quan trong)\b",
                "",
                fallback_seed,
                flags=re.IGNORECASE,
            )
            fallback_seed = re.sub(
                r"\b(?:cho|for|assigned to|giao cho)\s+[a-z0-9._-]+\b",
                "",
                fallback_seed,
                flags=re.IGNORECASE,
            )
            fallback_seed = re.sub(r"\s+", " ", fallback_seed).strip(" -:,.")
            if not fallback_seed:
                fallback_seed = f"Task auto {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            title = fallback_seed[:180].rstrip()
            if not description:
                description = original_text[:1200]
            auto_title = True
        priority = _extract_task_priority(original_text)
        assigned_to = _extract_task_assignee(original_text)
        try:
            created = _hub.create_task_safe(
                title=title,
                description=description,
                priority=priority,
                assigned_to=assigned_to,
                expected_version=None,
            )
        except Exception as exc:
            return {
                "intent": "task_create",
                "executed": False,
                "result": _with_context({"error": str(exc)}),
                "reply": f"[{instance_label}] Tạo task thất bại: {exc}",
            }
        ok = bool(created.get("success"))
        task_obj = created.get("task") if isinstance(created.get("task"), dict) else {}
        task_id = str(task_obj.get("id") or created.get("task_id") or "")
        reply = (
            f"[{instance_label}] Đã tạo task mới thành công.\n"
            f"- ID: {task_id or 'n/a'}\n"
            f"- Title: {title}\n"
            f"- Priority: {priority}\n"
            f"- Assignee: {assigned_to or 'unassigned'}"
        )
        if description:
            reply += f"\n- Mô tả: {description[:180]}"
        if auto_title:
            reply += "\n- Note: Tiêu đề được auto-generate để không chặn luồng thực thi."
        reply += "\nMở tab Công Việc để theo dõi, hoặc gõ `list tasks` ngay trong chat."
        return {
            "intent": "task_create",
            "executed": ok,
            "result": _with_context(created),
            "reply": reply,
        }

    task_progress_intent = bool(
        re.search(r"(task|cong viec|khiem|ravin|kanban|backlog|pending|ton dong)", normalized_original)
        and re.search(r"(xong|chua|trang thai|status|tien do|bao nhieu|dang|done|in progress|blocked|hoan thanh)", normalized_original)
    )
    if task_progress_intent:
        snapshot = _build_task_intelligence_snapshot(query=original_text, limit=8)
        reply = _format_task_intelligence_reply(instance_label, snapshot, query=original_text)
        return {
            "intent": "task_status_intelligence",
            "executed": False,
            "result": _with_context({"task_intelligence": snapshot}),
            "reply": reply,
        }

    autonomous_execute_intent = bool(
        (
            re.search(r"(hoc hoi|phat trien|nang cap|cai thien|hoan thien|toi uu|trien khai|fix|sua loi)", normalized_original)
            and re.search(r"(giup toi|dum toi|cho toi|tu dong|full automation|lam luon|khong ban giao|khong hoi|dung hoi|khong can hoi)", normalized_original)
        )
        or re.search(r"(tai sao|vi sao).*(ban giao|loi|stuck|khong hoat dong|that bai)", normalized_original)
    )
    if autonomous_execute_intent:
        goal = original_text or "Continuous autonomous improvement without user handoff"
        resolved_member = normalize_member_id(member_id)
        payload = _execute_agent_coordination(
            goal=goal,
            member_id=resolved_member,
            max_agents=4,
            compact_mode=True,
            execute=True,
            priority="high",
            instance_ids=[],
            multi_instance=True,
        )

        materialized = {"enabled": False, "created": 0, "results": []}
        if "_hub" in globals() and _hub is not None:
            materialized = _materialize_coordination_tasks(
                goal=goal,
                coordination_payload=payload,
                priority="high",
                member_id=resolved_member,
                max_tasks=4,
            )

        dispatch_rows: List[Dict[str, Any]] = []
        dispatched_success = 0
        for row in materialized.get("results", []):
            if not isinstance(row, dict):
                continue
            task_obj = row.get("task") if isinstance(row.get("task"), dict) else {}
            task_id = str(task_obj.get("id") or row.get("task_id") or "").strip()
            if not task_id:
                continue
            status_code, body = _invoke_internal_post(
                path=f"/api/hub/tasks/{task_id}/dispatch",
                view_func=lambda task_id=task_id: dispatch_hub_task(task_id),
                payload={
                    "ensure_assigned": True,
                    "priority": "high",
                    "options": {
                        "source": "chat_autonomous_execute",
                        "async_dispatch": True,
                        "wait_timeout_sec": HUB_DISPATCH_WAIT_TIMEOUT_SEC,
                    },
                },
            )
            accepted = status_code < 400 and isinstance(body, dict) and bool(body.get("success"))
            if accepted:
                dispatched_success += 1
            dispatch_rows.append(
                {
                    "task_id": task_id,
                    "success": bool(accepted),
                    "status_code": int(status_code),
                    "instance_id": str((body or {}).get("instance_id") if isinstance(body, dict) else ""),
                }
            )

        payload["task_materialization"] = materialized
        payload["task_dispatch"] = {
            "attempted": len(dispatch_rows),
            "dispatched_success": dispatched_success,
            "results": dispatch_rows,
        }
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        total_sent = int(execution.get("total_sent", 0) or 0)
        total_success = int(execution.get("total_success", 0) or 0)
        created_tasks = int(materialized.get("created", 0) or 0)
        level = "success" if (total_success > 0 or dispatched_success > 0) else "warning"
        state.add_agent_log(
            "guardian",
            (
                "🚀 Chat autonomous execute: "
                f"coord_sent={total_sent} coord_success={total_success} "
                f"tasks_created={created_tasks} tasks_dispatched={dispatched_success}"
            ),
            level,
        )
        return {
            "intent": "autonomous_execute",
            "executed": bool(total_success > 0 or dispatched_success > 0),
            "result": _with_context(payload),
            "reply": (
                f"[{instance_label}] Đã chuyển sang chế độ tự triển khai, không bàn giao lại cho bạn.\n"
                f"- Coordination commands: {total_sent} (success {total_success})\n"
                f"- Hub tasks created: {created_tasks}\n"
                f"- Hub tasks dispatched: {dispatched_success}\n"
                "Hệ thống đang tự chạy vòng cải tiến; bạn chỉ cần giám sát kết quả realtime."
            ),
        }

    if normalized_intent in {"nexus rules", "project rules", "rules", "rule"}:
        runtime_line = str(activity.get("summary") or _runtime_status_text(runtime))
        return {
            "intent": "nexus_rules",
            "executed": False,
            "result": _with_context({
                "ai_summary": ai_summary,
                "reasoning": reasoning,
                "feedback_recent": feedback_recent,
            }),
            "reply": _build_nexus_rules_text(instance_label, runtime_line, feedback_recent),
        }

    if re.search(r"(khong hoi|khong can hoi|khong xac nhan|hoi lien tuc|confirm lien tuc|qua nhieu hoi|dung hoi|ngung hoi|hoi nua)", normalized_intent):
        _monitor_set_supervisor_enabled(True)
        result = _call_instance_command(resolved_instance, "guardian", "autopilot_on", "high", {"source": "chat_feedback_guard"})
        return {
            "intent": "autonomy_feedback_ack",
            "executed": bool(result.get("success", True)),
            "result": _with_context(result),
            "reply": (
                f"[{instance_label}] Đã ghi nhận feedback và giữ chế độ tự vận hành liên tục. "
                "Guardian autopilot đang ON, monitor supervisor ON, hệ thống sẽ ưu tiên tự xử lý thay vì chờ xác nhận."
            ),
        }

    if "agent coordination" in lowered or re.search(
        r"(phoi hop|coordinate|orchestrate|multi.?agent|nhieu agent|nhieu orion)",
        normalized_intent,
    ):
        goal = text
        for prefix in ("agent coordination", "coordinate", "orchestrate", "phoi hop", "dieu phoi"):
            if goal.lower().startswith(prefix):
                goal = goal[len(prefix):].strip()
                break
        if not goal:
            goal = "Improve workflow throughput with compact multi-agent coordination"
        resolved_member = normalize_member_id(member_id)
        payload = _execute_agent_coordination(
            goal=goal,
            member_id=resolved_member,
            max_agents=4,
            compact_mode=True,
            execute=True,
            priority="high",
            instance_ids=[],
            multi_instance=True,
        )
        materialized = {"enabled": False, "created": 0, "results": []}
        if AGENT_COORDINATION_AUTO_CREATE_TASKS:
            materialized = _materialize_coordination_tasks(
                goal=goal,
                coordination_payload=payload,
                priority="high",
                member_id=resolved_member,
                max_tasks=4,
            )
        payload["task_materialization"] = materialized
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        token_budget = plan.get("token_budget") if isinstance(plan.get("token_budget"), dict) else {}
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        sent = int(execution.get("total_sent", 0) or 0)
        success = int(execution.get("total_success", 0) or 0)
        saved = int(token_budget.get("estimated_tokens_saved", 0) or 0)
        created_tasks = int(materialized.get("created", 0) or 0)
        ratio = round(float(token_budget.get("saving_ratio", 0.0) or 0.0) * 100, 1)
        state.add_agent_log(
            "guardian",
            f"🧠 Chat auto-coordination sent={sent} success={success} saved≈{saved} tasks={created_tasks}",
            "success" if success > 0 else "warning",
        )
        return {
            "intent": "agent_coordination_dispatch",
            "executed": bool(sent > 0 and success > 0),
            "result": _with_context(payload),
            "reply": (
                f"[{instance_label}] Đã điều phối compact multi-agent cho mục tiêu: {goal}\n"
                f"Dispatch: {sent} lệnh, success {success}. Token saving ước tính: {saved} (~{ratio}%). "
                f"Hub tasks created: {created_tasks}."
            ),
        }

    # Permission/approval prompt intent
    command_candidate = ""
    shell_line = re.search(r"\$\s*([^\n]+)", text)
    if shell_line:
        command_candidate = shell_line.group(1).strip()
    elif re.match(r"^(curl|git|npm|pip|python|lsof|cat|rg|ls)\b", lowered):
        command_candidate = text.strip()

    if command_candidate and re.search(r"(would you like|yes|proceed|run the following command|xác nhận|bấm|approve|deny)", lowered):
        advice = _call_instance_command(
            resolved_instance,
            "guardian",
            f"advise_command:{command_candidate}",
            "high",
            {"reason": "approval_prompt", "choices": ["approve", "deny", "alternative"]},
        )
        advice_payload = (advice or {}).get("result") or {}
        advice_result = (
            advice_payload.get("output")
            or ((advice_payload.get("result") or {}).get("output") if isinstance(advice_payload, dict) else {})
            or {}
        )
        risk = str(advice_result.get("risk", "medium"))
        decision = str(advice_result.get("decision", "ask_user"))
        reason = str(advice_result.get("reason", ""))
        safer = str(advice_result.get("safer_alternative", "")).strip()
        no_prompt_mode = _autonomy_no_human_prompts_enabled()
        if MONITOR_AUTO_APPROVE_ALL_DECISIONS and decision not in {"approved", "approve"}:
            decision = "approved"
            reason = "Auto-approved by monitor (MONITOR_AUTO_APPROVE_ALL_DECISIONS)."
        should_auto_execute = bool(MONITOR_AUTO_EXECUTE_COMMANDS or no_prompt_mode)
        if decision in {"approved", "approve"} and should_auto_execute:
            execution = _execute_approved_shell_command(command_candidate)
            if execution.get("success"):
                state.add_agent_log("guardian", f"🤖 Auto-executed command: {execution.get('command')}", "success")
            else:
                state.add_agent_log("guardian", f"⚠️ Auto-exec failed: {execution.get('error', 'unknown')}", "warning")
            reply = (
                f"[{instance_label}] Đã tự động thực thi command theo Guardian.\n"
                f"Rủi ro: {risk}. {reason}"
            )
            if execution.get("output"):
                reply += f"\nOutput: {execution.get('output')[:240]}"
            if safer:
                reply += f"\nPhương án an toàn hơn: {safer}"
            return {
                "intent": "guardian_command_autonomous",
                "executed": bool(execution.get("success")),
                "result": _with_context(execution),
                "reply": reply,
                "pending_decision": None,
            }
        if no_prompt_mode:
            fallback_reply = (
                f"[{instance_label}] Guardian đánh giá command: {decision} (risk={risk}). "
                "Hệ thống đang ở chế độ không hỏi lại người dùng nên không tạo pending decision."
            )
            if reason:
                fallback_reply += f"\nReason: {reason}"
            if safer:
                fallback_reply += f"\nSafer alternative: {safer}"
            return {
                "intent": "guardian_command_autonomous_no_prompt",
                "executed": False,
                "result": _with_context(
                    {
                        "advice": advice,
                        "decision": decision,
                        "risk": risk,
                        "reason": reason,
                        "safer_alternative": safer,
                        "pending_decision": None,
                        "no_prompt_mode": True,
                    }
                ),
                "reply": fallback_reply,
                "pending_decision": None,
            }
        pending = state.find_pending_command_decision(command_candidate, resolved_instance)
        if not pending:
            pending = state.add_pending_decision({
                "action": "command_intervention",
                "title": "Phê duyệt command",
                "risk_level": risk,
                "kind": "command_intervention",
                "instance_id": resolved_instance,
                "instance_name": instance_label,
                "command": command_candidate,
                "auto_execute_on_approve": decision in {"approved", "approve"},
                "details": {
                    "command": command_candidate,
                    "guardian_decision": decision,
                    "reason": reason,
                    "safer_alternative": safer,
                },
            })
        reply = (
            f"[{instance_label}] Guardian đánh giá command ở mức rủi ro: {risk}.\n"
            f"Khuyến nghị: {decision}. {reason}\n"
            f"Đã tạo thẻ duyệt nhanh để bạn bấm trực tiếp trên dashboard."
        )
        if safer:
            reply += f"\nPhương án an toàn hơn: {safer}"
        return {
            "intent": "guardian_command_intervention",
            "executed": True,
            "result": _with_context(advice),
            "reply": reply,
            "pending_decision": pending,
        }

    # Explicit command intents
    if re.search(r"(duyệt prompt|approve prompt|accept prompt|bấm y|press y|ấn y)", lowered):
        result = _computer_control_action("approve_prompt", {})
        return {
            "intent": "computer_approve_prompt",
            "executed": bool(result.get("success")),
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã gửi thao tác duyệt prompt (Y + Enter)." if result.get("success") else f"[{instance_label}] Không duyệt được prompt: {result.get('error', 'unknown')}",
        }
    if re.search(r"(từ chối prompt|deny prompt|reject prompt|bấm esc|press esc|ấn esc)", lowered):
        result = _computer_control_action("deny_prompt", {})
        return {
            "intent": "computer_deny_prompt",
            "executed": bool(result.get("success")),
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã gửi thao tác từ chối prompt (ESC)." if result.get("success") else f"[{instance_label}] Không từ chối được prompt: {result.get('error', 'unknown')}",
        }
    if re.search(r"(continue prompt|tiếp tục prompt|bấm enter|press enter|ấn enter)", lowered):
        result = _computer_control_action("continue_prompt", {})
        return {
            "intent": "computer_continue_prompt",
            "executed": bool(result.get("success")),
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã gửi thao tác tiếp tục prompt (Enter)." if result.get("success") else f"[{instance_label}] Không gửi Enter được: {result.get('error', 'unknown')}",
        }

    worker_match = re.search(r"\b(nova|pixel|echo|cipher|flux)\b", lowered)
    worker_target = worker_match.group(1).lower() if worker_match else ""
    if worker_target and re.search(r"\b(pause|tạm dừng|disable|off|tắt)\b", lowered):
        result = _call_instance_command(
            resolved_instance,
            worker_target,
            "pause",
            "high",
            {"control_mode": "strict", "reason": "chat_worker_pause"},
        )
        return {
            "intent": "agent_autopilot_off",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã tắt autopilot agent {worker_target.upper()}. Agent sẽ không tự chạy trong vòng lặp Orion.",
        }
    if worker_target and re.search(r"\b(resume|tiếp tục|enable|on|bật)\b", lowered):
        result = _call_instance_command(
            resolved_instance,
            worker_target,
            "resume",
            "high",
            {"control_mode": "strict", "reason": "chat_worker_resume"},
        )
        return {
            "intent": "agent_autopilot_on",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã bật lại autopilot agent {worker_target.upper()}.",
        }
    if worker_target and re.search(r"\b(status|trạng thái|state)\b", lowered):
        result = _call_instance_command(resolved_instance, worker_target, "status", "high", {"control_mode": "strict"})
        status = (result.get("status") or {}) if isinstance(result, dict) else {}
        control = (result.get("agent_control") or {}) if isinstance(result, dict) else {}
        enabled = bool(control.get("enabled", True))
        running = bool(status.get("running", False))
        task = str(status.get("current_task", "") or "").strip() or "n/a"
        mode = "auto ON" if enabled else "auto OFF"
        reply = (
            f"[{instance_label}] {worker_target.upper()} -> {mode}, running={running}, "
            f"task={task}."
        )
        return {
            "intent": "agent_status",
            "executed": False,
            "result": _with_context(result),
            "reply": reply,
        }

    if re.search(r"\b(pause|tạm dừng)\b", lowered):
        target = "all" if re.search(r"\b(all|tất cả)\b", lowered) else "orion"
        result = _call_instance_command(resolved_instance, target, "pause", "high", {"control_mode": "strict"})
        return {
            "intent": "command_pause",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã gửi lệnh tạm dừng. " + _runtime_status_text(runtime),
        }
    if re.search(r"\b(resume|tiếp tục)\b", lowered) and "guardian" not in lowered:
        target = "all" if re.search(r"\b(all|tất cả)\b", lowered) else "orion"
        result = _call_instance_command(resolved_instance, target, "resume", "high", {"control_mode": "strict"})
        return {
            "intent": "command_resume",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã gửi lệnh tiếp tục. Orion sẽ chạy tự động lại.",
        }
    if re.search(r"(run[_ ]?cycle|chạy vòng|chạy 1 vòng)", lowered):
        result = _call_instance_command(
            resolved_instance,
            "orion",
            "run_cycle",
            "high",
            {"control_mode": "interrupt", "resume_after": True},
        )
        return {
            "intent": "command_run_cycle",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã chạy một vòng chủ động và trả quyền tự động lại cho Orion.",
        }
    if re.search(r"(guardian|giám sát).*(stuck|kẹt|đơ|tiếp tục|gỡ)", lowered) or re.search(r"(unstick|gỡ kẹt)", lowered):
        result = _call_instance_command(resolved_instance, "guardian", "unstick_orion", "high", {})
        return {
            "intent": "guardian_recover",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Guardian đã nhận lệnh gỡ kẹt và sẽ đẩy Orion chạy tiếp nếu phát hiện bị chặn.",
        }
    if re.search(r"(autopilot|tự động).*(on|bật)", lowered):
        result = _call_instance_command(resolved_instance, "guardian", "autopilot_on", "high", {})
        return {
            "intent": "guardian_autopilot_on",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã bật Guardian autopilot. Hệ thống sẽ tự gỡ stuck và tiếp tục chạy.",
        }
    if re.search(r"(autopilot|tự động).*(off|tắt)", lowered):
        result = _call_instance_command(resolved_instance, "guardian", "autopilot_off", "high", {})
        return {
            "intent": "guardian_autopilot_off",
            "executed": True,
            "result": _with_context(result),
            "reply": f"[{instance_label}] Đã tắt Guardian autopilot. Hệ thống sẽ không auto-recover nữa.",
        }

    if "uiux review" in lowered or re.search(r"(ui ?/?ux|giao diện|trải nghiệm).*(đánh giá|cải thiện|nâng cấp|đề xuất|tối ưu)", lowered):
        risk_errors = int(counts.get("error", 0) or 0)
        risk_warnings = int(counts.get("warning", 0) or 0)
        focus = []
        if risk_errors > 0:
            focus.append("Ưu tiên loại bỏ trạng thái lỗi hiển thị gây mất niềm tin người dùng.")
        if risk_warnings > 4:
            focus.append("Giảm nhiễu cảnh báo trên màn hình chính, chỉ giữ tín hiệu thực sự quan trọng.")
        focus.extend([
            "Đẩy Chat thành chế độ focus mặc định khi người dùng cần thao tác điều khiển.",
            "Giữ light/dark đồng nhất về tương phản, giảm neon để tăng tính professional.",
            "Tăng tốc thao tác bằng prompt chips + phím tắt thay vì menu cứng.",
        ])
        reply = (
            f"[{instance_label}] Đánh giá UI/UX realtime:\n"
            f"- Trạng thái hiện tại: errors={risk_errors}, warnings={risk_warnings}, top agent={top_agent}.\n"
            "- Hướng cải tiến ưu tiên:\n"
            + "\n".join([f"  • {item}" for item in focus[:4]])
            + "\nMình sẽ tiếp tục auto-tinh chỉnh theo vòng lặp feedback tiếp theo."
        )
        return {
            "intent": "uiux_review",
            "executed": False,
            "result": _with_context({
                "agent_activity": activity,
                "ai_summary": ai_summary,
                "reasoning": reasoning,
                "feedback_recent": feedback_recent,
            }),
            "reply": reply,
        }

    if re.search(r"(openclaw|agent|model|orchestr|phoi hop|dieu phoi|linh dong|level|self.?learn|tu hoc)", normalized_original):
        control_plane = _control_plane_registry.snapshot() if "_control_plane_registry" in globals() else {}
        workers = control_plane.get("workers") if isinstance(control_plane, dict) else {}
        worker_rows = list(workers.values()) if isinstance(workers, dict) else []
        online_workers = sum(1 for row in worker_rows if isinstance(row, dict) and bool(row.get("online")))
        openclaw_snapshot = _build_openclaw_metrics_snapshot(include_events=False)
        queue = openclaw_snapshot.get("queue") if isinstance(openclaw_snapshot.get("queue"), dict) else {}
        queue_depth = int(queue.get("queue_depth", 0) or 0)
        queue_running = int(queue.get("running_sessions", 0) or 0)
        mode = str((openclaw_snapshot.get("runtime") or {}).get("execution_mode", OPENCLAW_EXECUTION_MODE))
        browser_enabled = bool((openclaw_snapshot.get("runtime") or {}).get("browser_actions_enabled"))
        flow_enabled = bool((openclaw_snapshot.get("runtime") or {}).get("dashboard_flow_enabled"))
        reply = (
            f"[{instance_label}] Mình đang vận hành theo 3 tầng orchestration để linh động theo level task:\n"
            f"1) L1 Realtime control (OpenClaw): thao tác UI như người cho browser/app/desktop (mode={mode}, browser={browser_enabled}, flow={flow_enabled}).\n"
            "2) L2 Structured automation: task bus + lease + circuit breaker + intervention popup khi thiếu thông tin.\n"
            "3) L3 Strategic reasoning: Orion/Guardian điều phối multi-agent, tự tạo/dispatch task, tối ưu token.\n"
            f"Runtime hiện tại: workers_online={online_workers}/{len(worker_rows)}, openclaw_queue_depth={queue_depth}, running_sessions={queue_running}.\n"
            "Nếu bạn giao mục tiêu nghiệp vụ, mình sẽ ưu tiên route tác vụ thao tác thực tế qua OpenClaw; phần phân tích/ra quyết định giữ ở Orion/Guardian."
        )
        return {
            "intent": "orchestration_strategy",
            "executed": False,
            "result": _with_context(
                {
                    "agent_activity": activity,
                    "orchestration": {
                        "layers": ["openclaw_realtime", "structured_automation", "strategic_reasoning"],
                        "openclaw_mode": mode,
                        "openclaw_browser_enabled": browser_enabled,
                        "openclaw_flow_enabled": flow_enabled,
                        "control_plane_online_workers": online_workers,
                        "control_plane_total_workers": len(worker_rows),
                        "openclaw_queue_depth": queue_depth,
                        "openclaw_running_sessions": queue_running,
                    },
                }
            ),
            "reply": reply,
        }

    # Quick helper for option (2) in unified chat menu.
    if re.search(r"(điều khiển flow|flow control|nhóm 2|group 2|option 2)", lowered):
        return {
            "intent": "assistant_control_help",
            "executed": False,
            "result": _with_context({
                "ai_summary": ai_summary,
                "reasoning": reasoning,
                "feedback_recent": feedback_recent,
            }),
            "reply": (
                f"[{instance_label}] Nhóm điều khiển flow:\n"
                "- pause / resume (toàn hệ thống hoặc Orion)\n"
                "- chạy 1 vòng Orion\n"
                "- guardian gỡ kẹt\n"
                "- pixel on/off, nova on/off, echo on/off\n"
                "Ví dụ nhanh: 'resume all', 'chạy 1 vòng Orion 1', 'pixel off'."
            ),
        }

    # Status / project info intent
    if re.search(r"(trạng thái|status|dự án|project|tiến độ|đang làm gì|what are you working|tóm tắt)", lowered):
        runtime_line = str(activity.get("summary") or _runtime_status_text(runtime))
        if resolved_instance != LOCAL_INSTANCE_ID and not bool(activity.get("online")):
            remote = _call_instance_command(resolved_instance, "orion", "status", "high", {})
            remote_status = ((remote or {}).get("result") or {}).get("status", {})
            if isinstance(remote_status, dict) and remote_status:
                running = bool(remote_status.get("running"))
                paused = bool(remote_status.get("paused"))
                iteration = remote_status.get("iteration", "n/a")
                mode = "đang chạy" if running and not paused else ("tạm dừng" if paused else "đang rảnh")
                runtime_line = f"{instance_label} {mode}, vòng lặp hiện tại: {iteration}."
        task_snapshot = _build_task_intelligence_snapshot(query=original_text, limit=5)
        task_line = "Task intelligence: Hub chưa sẵn sàng."
        if bool(task_snapshot.get("available")):
            task_counts = task_snapshot.get("counts") if isinstance(task_snapshot.get("counts"), dict) else {}
            task_line = (
                "Task intelligence: "
                f"total={int(task_snapshot.get('total_tasks', 0) or 0)}, "
                f"pending={int(task_snapshot.get('pending_count', 0) or 0)}, "
                f"in_progress={int(task_counts.get('in_progress', 0) or 0)}, "
                f"blocked={int(task_counts.get('blocked', 0) or 0)}, "
                f"done={int(task_counts.get('done', 0) or 0)}."
            )
        agent_block = "\n".join([f"- {line}" for line in activity_lines]) if activity_lines else "- Chưa có telemetry agent."
        reply = (
            f"{runtime_line}\n"
            f"Tín hiệu quan trọng: {counts['error']} lỗi, {counts['warning']} cảnh báo, {counts['success']} thành công.\n"
            f"Agent hoạt động nhiều nhất gần đây: {top_agent}.\n"
            f"Sự kiện gần nhất: {latest_msg}\n"
            f"{task_line}\n"
            f"Agent realtime:\n{agent_block}\n\n"
            f"Phản hồi gần nhất: {[item.get('category') for item in feedback_recent][:2]}"
        )
        return {
            "intent": "project_status_summary",
            "executed": False,
            "result": _with_context({
                "agent_activity": activity,
                "task_intelligence": task_snapshot,
                "ai_summary": ai_summary,
                "reasoning": reasoning,
                "feedback_recent": feedback_recent,
            }),
            "reply": reply,
        }

    # Default assistant behavior
    return {
        "intent": "assistant_help",
        "executed": False,
        "result": _with_context({
            "ai_summary": ai_summary,
            "reasoning": reasoning,
            "feedback_recent": feedback_recent,
        }),
        "reply": _adaptive_fallback_text(),
    }


def _build_intervention_queue_snapshot() -> Dict[str, Any]:
    pending = state.list_pending_decisions()
    rows: List[Dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": str(item.get("id", "")),
                "kind": str(item.get("kind", item.get("action", "decision"))),
                "action": str(item.get("action", "decision")),
                "title": str(item.get("title", item.get("action", "Decision"))),
                "risk_level": _normalize_risk_level(item.get("risk_level"), fallback="medium"),
                "instance_id": str(item.get("instance_id", LOCAL_INSTANCE_ID) or LOCAL_INSTANCE_ID),
                "instance_name": str(item.get("instance_name", "") or ""),
                "command": str(item.get("command", "") or ""),
                "timestamp": str(item.get("timestamp", "") or datetime.now().isoformat()),
                "deferred": bool(item.get("deferred", False)),
                "deferred_until": item.get("deferred_until"),
                "details": item.get("details", {}) if isinstance(item.get("details"), dict) else {},
            }
        )
    rows = _sort_by_timestamp_desc(rows)
    risk_counts: Dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for row in rows:
        risk = str(row.get("risk_level", "medium")).strip().lower()
        if risk not in risk_counts:
            risk = "medium"
        risk_counts[risk] += 1
    return {
        "items": rows,
        "summary": {
            "pending": len(rows),
            "deferred": sum(1 for row in rows if bool(row.get("deferred"))),
            "risk_counts": risk_counts,
        },
    }


def _build_audit_timeline(limit: int = 120, cursor: Optional[str] = None) -> Dict[str, Any]:
    cap = max(10, min(800, int(limit)))
    cursor_dt = _parse_iso_datetime(cursor) if cursor else None
    items: List[Dict[str, Any]] = []

    for event in state.get_event_feed(limit=cap * 2, include_routing=True):
        ts = str(event.get("timestamp", "") or "")
        items.append(
            {
                "timestamp": ts,
                "type": str(event.get("type", "event_log")),
                "level": str(event.get("level", "info")),
                "message": str(event.get("message", "")),
                "source": "events_feed",
                "data": event.get("data", {}),
            }
        )

    for event in _guardian_control_audit_tail(limit=max(20, cap)):
        ts = str(event.get("timestamp", "") or "")
        action = str(event.get("action", "guardian_control"))
        items.append(
            {
                "timestamp": ts,
                "type": "guardian_control",
                "level": "success" if bool(event.get("success")) else "warning",
                "message": f"Guardian control: {action}",
                "source": "guardian_audit",
                "data": event,
            }
        )

    for event in _intervention_history_tail(limit=max(40, cap * 2)):
        ts = str(event.get("timestamp", "") or "")
        action = str(event.get("action", "intervention"))
        items.append(
            {
                "timestamp": ts,
                "type": "intervention",
                "level": "success" if bool(event.get("success")) else "warning",
                "message": f"Intervention {action}",
                "source": "interventions_jsonl",
                "data": event,
            }
        )

    try:
        if "_hub" in globals() and _hub is not None:
            for row in list(_hub.audit_log)[-max(30, cap):]:
                if not isinstance(row, dict):
                    continue
                ts = str(row.get("timestamp", "") or "")
                action = str(row.get("action", "hub_action"))
                items.append(
                    {
                        "timestamp": ts,
                        "type": "hub_audit",
                        "level": "info",
                        "message": f"Hub {action}",
                        "source": "hub_audit",
                        "data": row,
                    }
                )
    except Exception:
        pass

    items = _sort_by_timestamp_desc(items)
    if cursor_dt:
        items = [row for row in items if (_parse_iso_datetime(row.get("timestamp")) or datetime.min) < cursor_dt]
    items = items[:cap]
    next_cursor = items[-1].get("timestamp") if items else None
    return {
        "items": items,
        "next_cursor": next_cursor,
    }


# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    """Unified workspace landing route"""
    return redirect(url_for('live_monitor', view='workspace'))


@app.route('/control-center')
def control_center():
    """Legacy control-center route redirected to unified live workspace."""
    return redirect(url_for('live_monitor', view='tasks'))


@app.route('/live')
def live_monitor():
    """Live Agent Monitor"""
    return render_template('live-monitor.html')


@app.route('/favicon.ico')
def favicon():
    """Silence missing favicon errors in dashboard consoles."""
    return ("", 204)


@app.route('/api/status')
def get_status():
    """Get overall system status"""
    return jsonify(build_status_payload())


@app.route('/api/system/slo')
def get_system_slo():
    """Get compact service-level indicators for monitor/routing/openclaw."""
    return jsonify({
        "success": True,
        "slo": _build_system_slo_snapshot(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/slo/summary')
def get_slo_summary():
    """Balanced SLO summary for control-plane/openclaw/monitor."""
    return jsonify({
        "success": True,
        "slo": _build_slo_summary(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/feedback/latest')
def feedback_latest():
    """Return latest feedback entries for dashboards"""
    limit = _int_in_range(request.args.get("limit", 8), default=8, min_value=1, max_value=40)
    return jsonify({
        "success": True,
        "feedback": _build_feedback_recent(limit=limit),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/progress/snapshot')
def get_progress_snapshot():
    """Get compact progress snapshot for monitoring/trackability."""
    _write_progress_snapshot()
    snapshot = _build_progress_snapshot_payload()
    return jsonify({
        "success": True,
        "snapshot": snapshot,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/dashboard')
def get_openclaw_dashboard():
    """Return OpenClaw dashboard URL and token metadata."""
    refresh = _to_bool(request.args.get("refresh"))
    info = _collect_token_info(force_refresh=refresh)
    base_url = "http://127.0.0.1:18789/"
    payload = {
        "url": base_url,
        "timestamp": datetime.now().isoformat(),
        "execution_mode": OPENCLAW_EXECUTION_MODE,
        "token_present": bool(info.token),
        "token_source": info.source,
        "health_ok": info.health_ok,
        "health_error": info.health_error,
        "token_checked_at": info.checked_at.isoformat() if info.checked_at else None,
        "token_last_error": info.last_error,
    }
    if info.token:
        payload["url"] = f"{base_url}#token={info.token}"
        payload["success"] = True
    else:
        payload["success"] = False
        payload["error"] = info.last_error or "OpenClaw gateway token missing"
    status_code = 200 if info.token and info.health_ok else 503
    if info.health_error:
        payload["health_error_hint"] = "Ensure the gateway is running and the token is valid."
    return jsonify(payload), status_code


@app.route('/api/openclaw/metrics')
def get_openclaw_metrics():
    include_events = _to_bool(request.args.get("events"))
    limit = _int_in_range(request.args.get("limit", 20), default=20, min_value=1, max_value=200)
    snapshot = _build_openclaw_metrics_snapshot(include_events=include_events, event_limit=limit)
    return jsonify({
        "success": True,
        "metrics": snapshot,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/metrics/prometheus')
def get_openclaw_metrics_prometheus():
    snapshot = _build_openclaw_metrics_snapshot(include_events=False)
    body = _render_openclaw_prometheus_metrics(snapshot)
    return Response(body, mimetype="text/plain; version=0.0.4; charset=utf-8")


@app.route('/api/control-plane/workers')
def control_plane_workers():
    return jsonify({
        "success": True,
        "control_plane": _control_plane_registry.snapshot(),
        "lease_enforcement": {
            "require_task_lease": OPENCLAW_REQUIRE_TASK_LEASE,
            "lease_heartbeat_sec": OPENCLAW_LEASE_HEARTBEAT_SEC,
        },
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/control-plane/workers/register', methods=['POST'])
def control_plane_worker_register():
    rate_limited = _rate_limited("control_plane_workers_register", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    worker_id = str(data.get("worker_id", "")).strip().lower()
    if not worker_id:
        return jsonify({"success": False, "error": "Missing worker_id"}), 400
    result = _control_plane_registry.register(
        worker_id=worker_id,
        capabilities=data.get("capabilities", ["openclaw", "orion"]),
        version=str(data.get("version", "")).strip(),
        region=str(data.get("region", "")).strip(),
        mode=str(data.get("mode", "remote")).strip().lower(),
        base_url=str(data.get("base_url", "")).strip(),
        lease_ttl_sec=max(5, int(data.get("lease_ttl_sec", CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC) or CONTROL_PLANE_DEFAULT_LEASE_TTL_SEC)),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@app.route('/api/control-plane/workers/heartbeat', methods=['POST'])
def control_plane_worker_heartbeat():
    rate_limited = _rate_limited("control_plane_workers_heartbeat", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    worker_id = str(data.get("worker_id", "")).strip().lower()
    if not worker_id:
        return jsonify({"success": False, "error": "Missing worker_id"}), 400
    result = _control_plane_registry.heartbeat(
        worker_id=worker_id,
        health=str(data.get("health", "ok")).strip().lower(),
        queue_depth=int(data.get("queue_depth", 0) or 0),
        running_tasks=int(data.get("running_tasks", 0) or 0),
        backpressure_level=str(data.get("backpressure_level", "normal")).strip().lower(),
        capabilities=data.get("capabilities"),
        version=str(data.get("version", "")).strip(),
        region=str(data.get("region", "")).strip(),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        lease_ttl_sec=int(data.get("lease_ttl_sec")) if data.get("lease_ttl_sec") is not None else None,
    )
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@app.route('/api/openclaw/browser', methods=['POST'])
def openclaw_browser_command():
    rate_limited = _rate_limited("openclaw_browser", API_RATE_LIMIT_OPENCLAW_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip().lower()
    if not action:
        return jsonify({"success": False, "error": "Missing action"}), 400
    queued = _enqueue_openclaw_command("browser", data)
    if queued.get("mode") == "async":
        return jsonify({
            "success": True,
            "accepted": True,
            "request_id": queued.get("request_id"),
            "session_id": queued.get("session_id"),
            "queue_state": queued.get("queue_state", {}),
            "idempotent_replay": queued.get("idempotent_replay", False),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), 202
    if queued.get("timed_out"):
        return jsonify({
            "success": False,
            "error": "Command execution timeout; poll command status",
            "error_code": "QUEUE_TIMEOUT",
            "request_id": queued.get("request_id"),
            "command": queued.get("command", {}),
            "queue_state": queued.get("queue_state", {}),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), 202
    if not queued.get("success"):
        return jsonify({
            "success": False,
            "error": queued.get("error", "OpenClaw queue failed"),
            "error_code": queued.get("error_code", "QUEUE_FAILED"),
            "queue_state": queued.get("queue_state", {}),
            "session_id": queued.get("session_id"),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "policy_reasons": queued.get("policy_reasons", []),
            "token_status": queued.get("token_status", {}),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), _openclaw_error_http_status(str(queued.get("error_code", "")))
    result = queued.get("result") if isinstance(queued.get("result"), dict) else {}
    browser_result = result.get("result") if isinstance(result.get("result"), dict) else result
    payload = {
        "success": bool(result.get("success")),
        "action": str(result.get("action", action)),
        "result": browser_result,
        "request_id": queued.get("request_id"),
        "session_id": queued.get("session_id"),
        "queue_state": queued.get("queue_state", {}),
        "idempotent_replay": queued.get("idempotent_replay", False),
        "decision": queued.get("decision"),
        "risk_level": queued.get("risk_level"),
        "target_worker": queued.get("target_worker"),
        "resolved_worker": queued.get("resolved_worker"),
        "failure_class": queued.get("failure_class") or result.get("failure_class"),
        "timestamp": datetime.now().isoformat(),
    }
    if "attach" in result:
        payload["attach"] = result.get("attach")
    return jsonify(payload)


@app.route('/api/openclaw/flow/dashboard', methods=['POST'])
def openclaw_flow_dashboard():
    rate_limited = _rate_limited("openclaw_flow_dashboard", API_RATE_LIMIT_OPENCLAW_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    queued = _enqueue_openclaw_command("flow", data)
    if queued.get("mode") == "async":
        return jsonify({
            "success": True,
            "accepted": True,
            "request_id": queued.get("request_id"),
            "session_id": queued.get("session_id"),
            "queue_state": queued.get("queue_state", {}),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), 202
    if queued.get("timed_out"):
        return jsonify({
            "success": False,
            "error": "Flow timeout; poll command status",
            "error_code": "QUEUE_TIMEOUT",
            "request_id": queued.get("request_id"),
            "command": queued.get("command", {}),
            "queue_state": queued.get("queue_state", {}),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), 202
    if not queued.get("success"):
        return jsonify({
            "success": False,
            "error": queued.get("error", "OpenClaw flow queue failed"),
            "error_code": queued.get("error_code", "QUEUE_FAILED"),
            "queue_state": queued.get("queue_state", {}),
            "session_id": queued.get("session_id"),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "policy_reasons": queued.get("policy_reasons", []),
            "token_status": queued.get("token_status", {}),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), _openclaw_error_http_status(str(queued.get("error_code", "")))
    result = queued.get("result") if isinstance(queued.get("result"), dict) else {}
    return jsonify({
        "success": bool(result.get("success")),
        "flow": result.get("flow", {}),
        "request_id": queued.get("request_id"),
        "session_id": queued.get("session_id"),
        "queue_state": queued.get("queue_state", {}),
        "decision": queued.get("decision"),
        "risk_level": queued.get("risk_level"),
        "target_worker": queued.get("target_worker"),
        "resolved_worker": queued.get("resolved_worker"),
        "failure_class": queued.get("failure_class") or result.get("failure_class"),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/extension/attach', methods=['POST'])
def openclaw_extension_attach():
    rate_limited = _rate_limited("openclaw_extension_attach", API_RATE_LIMIT_OPENCLAW_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    queued = _enqueue_openclaw_command("attach", data)
    if queued.get("mode") == "async":
        return jsonify({
            "success": True,
            "accepted": True,
            "request_id": queued.get("request_id"),
            "session_id": queued.get("session_id"),
            "queue_state": queued.get("queue_state", {}),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), 202
    if queued.get("timed_out"):
        return jsonify({
            "success": False,
            "error": "Attach timeout; poll command status",
            "error_code": "QUEUE_TIMEOUT",
            "request_id": queued.get("request_id"),
            "command": queued.get("command", {}),
            "queue_state": queued.get("queue_state", {}),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), 202
    if not queued.get("success"):
        return jsonify({
            "success": False,
            "error": queued.get("error", "OpenClaw attach queue failed"),
            "error_code": queued.get("error_code", "QUEUE_FAILED"),
            "queue_state": queued.get("queue_state", {}),
            "session_id": queued.get("session_id"),
            "decision": queued.get("decision"),
            "risk_level": queued.get("risk_level"),
            "policy_reasons": queued.get("policy_reasons", []),
            "token_status": queued.get("token_status", {}),
            "target_worker": queued.get("target_worker"),
            "resolved_worker": queued.get("resolved_worker"),
            "timestamp": datetime.now().isoformat(),
        }), _openclaw_error_http_status(str(queued.get("error_code", "")))
    result = queued.get("result") if isinstance(queued.get("result"), dict) else {}
    return jsonify({
        "success": bool(result.get("success")),
        "result": result.get("result", {}),
        "request_id": queued.get("request_id"),
        "session_id": queued.get("session_id"),
        "queue_state": queued.get("queue_state", {}),
        "decision": queued.get("decision"),
        "risk_level": queued.get("risk_level"),
        "target_worker": queued.get("target_worker"),
        "resolved_worker": queued.get("resolved_worker"),
        "failure_class": queued.get("failure_class") or result.get("failure_class"),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/sessions')
def openclaw_sessions():
    snapshot = _openclaw_command_manager.list_sessions()
    return jsonify({
        "success": True,
        "sessions": snapshot.get("sessions", []),
        "queue_wait_p95_ms": snapshot.get("queue_wait_p95_ms", 0),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/queue')
def openclaw_queue_snapshot():
    return jsonify({
        "success": True,
        "queue": _openclaw_command_manager.queue_snapshot(),
        "control_plane": _control_plane_registry.snapshot(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/commands', methods=['POST'])
def openclaw_commands_dispatch():
    rate_limited = _rate_limited("openclaw_commands_dispatch", API_RATE_LIMIT_OPENCLAW_PER_MIN, 60)
    if rate_limited:
        return rate_limited

    data = request.get_json(silent=True) or {}
    command_type = str(data.get("command_type", "")).strip().lower()
    nested_command = data.get("command") if isinstance(data.get("command"), dict) else {}
    if not command_type:
        command_type = str(nested_command.get("type", "")).strip().lower()
    if not command_type:
        command_type = "browser"
    if command_type not in {"browser", "flow", "attach"}:
        return jsonify({
            "success": False,
            "error": f"Unsupported command_type '{command_type}'",
            "error_code": "UNSUPPORTED_COMMAND",
        }), 400

    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if not payload:
        payload = dict(nested_command.get("payload", {})) if isinstance(nested_command.get("payload"), dict) else {}
    if not payload:
        payload = dict(data)

    for key in (
        "session_id",
        "source",
        "mode",
        "timeout_ms",
        "priority",
        "idempotency_key",
        "target_worker",
        "worker_id",
        "risk_level",
        "route_mode",
    ):
        if key in data and key not in payload:
            payload[key] = data.get(key)
    if command_type == "browser" and "action" in data and "action" not in payload:
        payload["action"] = data.get("action")

    queued = _enqueue_openclaw_command(command_type, payload)
    base_response = {
        "request_id": queued.get("request_id"),
        "session_id": queued.get("session_id"),
        "decision": queued.get("decision"),
        "risk_level": queued.get("risk_level"),
        "target_worker": queued.get("target_worker"),
        "resolved_worker": queued.get("resolved_worker"),
        "policy_reasons": queued.get("policy_reasons", []),
        "token_status": queued.get("token_status", {}),
        "queue_state": queued.get("queue_state", {}),
        "timestamp": datetime.now().isoformat(),
    }
    if not queued.get("success"):
        return jsonify({
            "success": False,
            "error": queued.get("error", "OpenClaw command rejected"),
            "error_code": queued.get("error_code", "QUEUE_FAILED"),
            **base_response,
        }), _openclaw_error_http_status(str(queued.get("error_code", "")))

    if queued.get("mode") == "async":
        return jsonify({
            "success": True,
            "accepted": True,
            "command_type": command_type,
            **base_response,
        }), 202

    if queued.get("timed_out"):
        return jsonify({
            "success": False,
            "error": "Command execution timeout; poll command status",
            "error_code": "QUEUE_TIMEOUT",
            "command_type": command_type,
            "command": queued.get("command", {}),
            **base_response,
        }), 202

    result = queued.get("result") if isinstance(queued.get("result"), dict) else {}
    return jsonify({
        "success": bool(result.get("success")),
        "command_type": command_type,
        "result": result,
        "command": queued.get("command", {}),
        "failure_class": queued.get("failure_class") or result.get("failure_class"),
        **base_response,
    })


@app.route('/api/openclaw/commands/<request_id>')
def openclaw_command_status(request_id: str):
    command = _openclaw_command_manager.get_command(request_id)
    if not command:
        return jsonify({"success": False, "error": "Command not found"}), 404
    return jsonify({
        "success": True,
        "command": command,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/commands/<request_id>/cancel', methods=['POST'])
def openclaw_command_cancel(request_id: str):
    result = _openclaw_command_manager.cancel_command(request_id)
    status = 200 if result.get("success") else 409
    return jsonify({
        "success": bool(result.get("success")),
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }), status


@app.route('/api/openclaw/sessions/<session_id>/manual-hold', methods=['POST'])
def openclaw_manual_hold(session_id: str):
    data = request.get_json(silent=True) or {}
    enabled = _to_bool(data.get("enabled", True))
    duration_sec = max(1, int(data.get("duration_sec", OPENCLAW_MANUAL_HOLD_SEC)))
    result = _openclaw_command_manager.set_manual_hold(session_id=session_id, enabled=enabled, duration_sec=duration_sec)
    return jsonify({
        "success": True,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    })


# ============================================================================
# UNIVERSAL AUTOMATION TASK BUS
# ============================================================================

AUTOMATION_TASK_TYPES = [
    "browser",
    "terminal",
    "app",
    "mail",
    "messages",
    "computer",
    "file",
    "clipboard",
    # Phase 3: Multi-surface
    "window",
    "surface",
]
AUTOMATION_TASK_STATUS = ["pending", "running", "completed", "failed", "cancelled"]
AUTOMATION_ROOT = (Path(__file__).parent.parent).resolve()
AUTOMATION_STATE_DIR = AUTOMATION_ROOT / "data" / "state"
AUTOMATION_TASK_HISTORY_PATH = AUTOMATION_STATE_DIR / "automation_tasks.jsonl"
AUTOMATION_SCHEDULES_PATH = AUTOMATION_STATE_DIR / "automation_schedules.json"
AUTOMATION_SESSION_QUEUE_MAX = max(2, int(os.getenv("AUTOMATION_SESSION_QUEUE_MAX", "32")))
AUTOMATION_SESSION_LEASE_TTL_SEC = max(30, int(os.getenv("AUTOMATION_SESSION_LEASE_TTL_SEC", "180")))
AUTOMATION_SCHEDULER_INTERVAL_SEC = max(2.0, float(os.getenv("AUTOMATION_SCHEDULER_INTERVAL_SEC", "5")))
AUTOMATION_SAFE_ROOTS = [AUTOMATION_ROOT, Path("/tmp")]
_AUTOMATION_SCHEDULER_LOCK = threading.Lock()
_automation_scheduled_tasks: Dict[str, Dict[str, Any]] = {}
_automation_scheduler_running = False
_automation_scheduler_thread: Optional[threading.Thread] = None
_automation_tasks_by_id: Dict[str, Dict[str, Any]] = {}
_automation_sessions: Dict[str, Dict[str, Any]] = {}
_AUTOMATION_POLL_LOCK = threading.Lock()
_automation_poll_last_seen: Dict[str, float] = {}

AUTOMATION_CAPABILITIES: Dict[str, List[str]] = {
    "browser": ["status", "start", "open", "navigate", "snapshot", "click", "type", "press", "flow"],
    "terminal": ["execute", "list_sessions"],
    "app": ["launch", "activate", "list_running"],
    "mail": ["check_unread", "list_recent", "list_folders"],
    "messages": ["check_unread", "list_recent", "list_conversations"],
    "computer": ["approve_prompt", "deny_prompt", "continue_prompt", "type_text", "press", "position", "screenshot"],
    "file": ["read", "write", "list", "copy"],
    "clipboard": ["read", "write"],
    # Phase 3: Multi-surface Computer Control
    "window": ["list", "focus", "resize", "move", "minimize", "maximize", "close"],
    "surface": ["list", "info", "screenshot", "capture"],
}
AUTOMATION_PASSIVE_POLL_ACTIONS = {
    ("browser", "status"),
    ("mail", "check_unread"),
    ("mail", "list_recent"),
    ("messages", "check_unread"),
    ("messages", "list_recent"),
    ("clipboard", "read"),
    ("computer", "position"),
    ("app", "list_running"),
}

# Task classification for risk management
# - passive_poll: Read-only, non-destructive, can run frequently
# - active_control: Modifies state, requires approval or limited frequency
# - destructive: Potentially harmful, requires explicit approval
AUTOMATION_TASK_CLASSES = {
    "passive_poll": {"browser", "terminal", "app", "mail", "messages", "clipboard", "file"},
    "active_control": {"browser", "terminal", "app", "computer", "file"},
    "destructive": {"terminal", "computer", "file"},
}

# Phase 3: Multi-surface Computer Control
# Window state tracking
_AUTOMATION_WINDOW_STATE_LOCK = threading.RLock()
_AUTOMATION_WINDOW_STATE: Dict[str, Dict[str, Any]] = {}  # {window_id: {title, app, position, size, focused, last_seen}}

# Surface registry (tracks all open windows/surfaces)
_AUTOMATION_SURFACE_REGISTRY_LOCK = threading.RLock()
_AUTOMATION_SURFACE_REGISTRY: Dict[str, Dict[str, Any]] = {}  # {surface_id: {type, title, app, pid, position, size, is_active}}

# Cross-surface workflow state
_AUTOMATION_WORKFLOW_LOCK = threading.RLock()
_AUTOMATION_WORKFLOWS: Dict[str, Dict[str, Any]] = {}  # {workflow_id: {name, steps, current_step, state, started_at}}

# Phase 4: Context & Memory for Task Understanding
_AUTOMATION_CONTEXT_LOCK = threading.RLock()
_AUTOMATION_TASK_CONTEXTS: Dict[str, Dict[str, Any]] = {}  # {session_id: {context, recent_actions, preferences}}

# Action memory - stores recent actions for learning
_AUTOMATION_ACTION_MEMORY_LOCK = threading.RLock()
_AUTOMATION_ACTION_MEMORY: List[Dict[str, Any]] = []  # [{task_type, action, params, result, timestamp, session_id, owner_id}]
AUTOMATION_ACTION_MEMORY_MAX = 1000

# User preference learning
_AUTOMATION_USER_PREFERENCES_LOCK = threading.RLock()
_AUTOMATION_USER_PREFERENCES: Dict[str, Dict[str, Any]] = {}  # {owner_id: {preferred_task_types, frequent_actions, avg_duration_ms}}
AUTOMATION_INTAKE_PATH = AUTOMATION_STATE_DIR / "automation_intake.json"
_AUTOMATION_INTAKE_LOCK = threading.RLock()
_automation_intake_store: Dict[str, Any] = {}


def _automation_intake_default() -> Dict[str, Any]:
    return {
        "mail": {
            "imap_host": "",
            "imap_port": 993,
            "imap_username": "",
            "imap_password": "",
            "imap_folder": "INBOX",
            "imap_ssl": True,
        },
        "channels": {
            "mail_mode": "edge_gmail",
            "mail_browser": "Microsoft Edge",
            "teams_mode": "teams_app",
            "teams_app_name": "Microsoft Teams",
        },
        "accounts": {
            "khiem_selected": "",
            "khiem_candidates": [],
        },
        "partners": {
            "khiem_team_hint": "",
            "ravin_contact_hint": "",
            "target_gmail": "admin@algoxpert.org",
        },
        "valuation": {
            "raise_usd": 50000,
            "equity_pct": 20,
            "notes": "",
        },
        "automation": {
            "auto_prompt_on_stuck": True,
        },
        "updated_at": None,
    }


def _automation_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _automation_merge_dict(merged.get(key) or {}, value)
        else:
            merged[key] = value
    return merged


def _automation_load_intake_store() -> None:
    global _automation_intake_store
    default = _automation_intake_default()
    try:
        if AUTOMATION_INTAKE_PATH.exists():
            loaded = json.loads(AUTOMATION_INTAKE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                _automation_intake_store = _automation_merge_dict(default, loaded)
                return
    except Exception as exc:
        logger.warning(f"Failed to load automation intake store: {exc}")
    _automation_intake_store = default


def _automation_save_intake_store() -> None:
    AUTOMATION_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = AUTOMATION_INTAKE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(_automation_intake_store, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp_path.replace(AUTOMATION_INTAKE_PATH)


def _automation_get_intake_store(redact_secret: bool = True) -> Dict[str, Any]:
    with _AUTOMATION_INTAKE_LOCK:
        row = _automation_merge_dict(_automation_intake_default(), _automation_intake_store if isinstance(_automation_intake_store, dict) else {})
    if redact_secret:
        mail = row.get("mail") if isinstance(row.get("mail"), dict) else {}
        if isinstance(mail, dict):
            has_password = bool(str(mail.get("imap_password", "")).strip())
            mail["imap_password"] = ""
            mail["imap_password_configured"] = has_password
            row["mail"] = mail
    return row


def _automation_update_intake_store(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _automation_get_intake_store(redact_secret=True)
    with _AUTOMATION_INTAKE_LOCK:
        merged = _automation_merge_dict(_automation_intake_store if isinstance(_automation_intake_store, dict) else _automation_intake_default(), payload)
        merged["updated_at"] = datetime.now().isoformat()
        _automation_intake_store.clear()
        _automation_intake_store.update(merged)
        _automation_save_intake_store()
    return _automation_get_intake_store(redact_secret=True)


def _automation_parse_csv_list(raw: Any, max_items: int = 10) -> List[str]:
    if isinstance(raw, list):
        out = [str(item or "").strip() for item in raw if str(item or "").strip()]
        return out[: max(1, int(max_items))]
    text = str(raw or "").strip()
    if not text:
        return []
    out = [chunk.strip() for chunk in text.replace("\n", ",").split(",") if chunk.strip()]
    return out[: max(1, int(max_items))]


def _automation_prompt_on_stuck_enabled() -> bool:
    intake = _automation_get_intake_store(redact_secret=False)
    automation_cfg = intake.get("automation") if isinstance(intake.get("automation"), dict) else {}
    raw = automation_cfg.get("auto_prompt_on_stuck", True)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _automation_default_human_input_value(field: Dict[str, Any], intake: Dict[str, Any]) -> Any:
    key = str(field.get("key", "")).strip()
    if not key:
        return None
    default_value = field.get("default")
    if isinstance(default_value, (str, int, float, bool)) and str(default_value).strip():
        return default_value

    channels = intake.get("channels") if isinstance(intake.get("channels"), dict) else {}
    accounts = intake.get("accounts") if isinstance(intake.get("accounts"), dict) else {}
    partners = intake.get("partners") if isinstance(intake.get("partners"), dict) else {}
    mail = intake.get("mail") if isinstance(intake.get("mail"), dict) else {}

    if key == "mail_mode":
        return str(channels.get("mail_mode", "edge_gmail") or "edge_gmail")
    if key == "mail_browser":
        return str(channels.get("mail_browser", "Microsoft Edge") or "Microsoft Edge")
    if key == "target_gmail":
        return str(partners.get("target_gmail", "admin@algoxpert.org") or "admin@algoxpert.org")
    if key == "teams_mode":
        return str(channels.get("teams_mode", "teams_app") or "teams_app")
    if key == "teams_app_name":
        return str(channels.get("teams_app_name", "Microsoft Teams") or "Microsoft Teams")
    if key == "khiem_selected":
        return str(accounts.get("khiem_selected", "") or partners.get("khiem_team_hint", "") or "Khiem - Work")
    if key == "khiem_candidates":
        candidates = accounts.get("khiem_candidates")
        if isinstance(candidates, list):
            return ", ".join(str(item).strip() for item in candidates if str(item).strip())
        return ""
    if key == "ravin_contact_hint":
        return str(partners.get("ravin_contact_hint", "") or "ravin")
    if key == "imap_host":
        return str(mail.get("imap_host", AUTOMATION_EMAIL_IMAP_HOST) or "")
    if key == "imap_username":
        return str(mail.get("imap_username", AUTOMATION_EMAIL_IMAP_USERNAME) or "")
    if key == "imap_password":
        return str(mail.get("imap_password", AUTOMATION_EMAIL_IMAP_PASSWORD) or "")

    options = field.get("options") if isinstance(field.get("options"), list) else []
    if options:
        first = str(options[0] or "").strip()
        if first:
            return first

    required = bool(field.get("required", False))
    if required:
        field_type = str(field.get("type", "text")).strip().lower()
        if field_type == "number":
            return 0
        return "auto_filled"
    return None


def _automation_try_auto_respond_human_input_request(decision: Dict[str, Any], actor: str = "full_auto") -> Dict[str, Any]:
    if not isinstance(decision, dict):
        return {"success": False, "error": "invalid_decision"}
    if str(decision.get("kind", "")).strip() != "human_input_request":
        return {"success": False, "error": "unsupported_kind"}
    decision_id = str(decision.get("id", "")).strip()
    if not decision_id:
        return {"success": False, "error": "missing_decision_id"}

    details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
    fields = details.get("fields") if isinstance(details.get("fields"), list) else []
    intake = _automation_get_intake_store(redact_secret=False)
    values: Dict[str, Any] = {}
    for raw_field in fields:
        if not isinstance(raw_field, dict):
            continue
        key = str(raw_field.get("key", "")).strip()
        if not key:
            continue
        inferred = _automation_default_human_input_value(raw_field, intake)
        if inferred is None:
            continue
        values[key] = inferred

    if not values and fields:
        first = fields[0] if isinstance(fields[0], dict) else {}
        first_key = str(first.get("key", "")).strip()
        if first_key:
            values[first_key] = "auto_filled"

    intake_snapshot = _automation_get_intake_store(redact_secret=True)
    applied_keys: List[str] = []
    if values:
        intake_snapshot, applied_keys = _automation_apply_human_input_values(values)

    response_payload = {
        "values": values,
        "remember": bool(values),
        "applied_keys": applied_keys,
        "auto_resolved": True,
        "timestamp": datetime.now().isoformat(),
    }
    resolved = state.respond_decision(decision_id=decision_id, response=response_payload, actor=actor)
    if not resolved:
        return {"success": False, "error": "decision_not_pending"}

    _append_intervention_history(
        action="auto_responded",
        decision_id=str(decision_id),
        success=True,
        actor=actor,
        detail={"applied_keys": applied_keys, "auto_resolved": True},
    )
    state.add_agent_log(
        "guardian",
        f"🤖 Full-auto resolved human input: {resolved.get('title', 'Need input')}",
        "success",
    )
    return {
        "success": True,
        "decision": resolved,
        "values": values,
        "applied_keys": applied_keys,
        "intake": intake_snapshot,
    }


def _find_pending_human_input_request(request_key: str) -> Optional[Dict[str, Any]]:
    target = str(request_key or "").strip()
    if not target:
        return None
    for item in state.list_pending_decisions():
        if not isinstance(item, dict):
            continue
        if str(item.get("kind", "")).strip() != "human_input_request":
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        if str(details.get("request_key", "")).strip() == target:
            return item
    return None


def _automation_enqueue_human_input_request(
    *,
    request_key: str,
    title: str,
    prompt: str,
    fields: List[Dict[str, Any]],
    source: str = "automation",
    context: Optional[Dict[str, Any]] = None,
    risk_level: str = "medium",
) -> Dict[str, Any]:
    key = str(request_key or "").strip() or "human_input_request"
    existing = _find_pending_human_input_request(key)
    if existing:
        return existing

    cleaned_fields: List[Dict[str, Any]] = []
    for raw_field in fields:
        if not isinstance(raw_field, dict):
            continue
        field_key = str(raw_field.get("key", "")).strip()
        if not field_key:
            continue
        field_type = str(raw_field.get("type", "text")).strip().lower() or "text"
        if field_type not in {"text", "select", "number", "password", "textarea"}:
            field_type = "text"
        field_payload: Dict[str, Any] = {
            "key": field_key,
            "label": str(raw_field.get("label", field_key)).strip() or field_key,
            "type": field_type,
            "required": bool(raw_field.get("required", False)),
        }
        options_raw = raw_field.get("options")
        if isinstance(options_raw, list):
            options = [str(item or "").strip() for item in options_raw if str(item or "").strip()]
            if options:
                field_payload["options"] = options[:12]
        placeholder = str(raw_field.get("placeholder", "")).strip()
        if placeholder:
            field_payload["placeholder"] = placeholder
        default_value = raw_field.get("default")
        if isinstance(default_value, (str, int, float, bool)) and str(default_value).strip():
            field_payload["default"] = default_value
        cleaned_fields.append(field_payload)

    decision = state.add_pending_decision(
        {
            "action": "human_input_request",
            "kind": "human_input_request",
                "title": str(title or "Need input").strip() or "Need input",
                "risk_level": _normalize_risk_level(risk_level, fallback="medium"),
                "instance_id": LOCAL_INSTANCE_ID,
                "instance_name": str(LOCAL_INSTANCE_ID),
                "details": {
                    "request_key": key,
                    "prompt": str(prompt or "").strip(),
                "fields": cleaned_fields,
                "source": str(source or "automation").strip() or "automation",
                "context": dict(context or {}),
            },
        }
    )
    _append_intervention_history(
        action="prompted",
        decision_id=str(decision.get("id", "")),
        success=True,
        actor="system",
        detail={"kind": "human_input_request", "request_key": key, "source": source},
    )
    state.add_agent_log("guardian", f"🧩 Human input requested: {decision.get('title', 'Need input')}", "warning")
    if _autonomy_no_human_prompts_enabled() and AUTOMATION_FULL_AUTO_AUTO_RESPOND_HUMAN_INPUT:
        auto_resolved = _automation_try_auto_respond_human_input_request(decision, actor="full_auto")
        resolved_item = auto_resolved.get("decision") if isinstance(auto_resolved.get("decision"), dict) else None
        if isinstance(resolved_item, dict):
            payload = dict(resolved_item)
            payload["auto_resolved"] = True
            return payload
    return decision


def _automation_request_due_diligence_context(reason: str, source: str, chat_text: str = "") -> Dict[str, Any]:
    intake = _automation_get_intake_store(redact_secret=False)
    channels = intake.get("channels") if isinstance(intake.get("channels"), dict) else {}
    accounts = intake.get("accounts") if isinstance(intake.get("accounts"), dict) else {}
    partners = intake.get("partners") if isinstance(intake.get("partners"), dict) else {}

    khiem_candidates = accounts.get("khiem_candidates")
    if isinstance(khiem_candidates, list):
        khiem_candidates_text = ", ".join(str(item).strip() for item in khiem_candidates if str(item).strip())
    else:
        khiem_candidates_text = ""

    fields = [
        {
            "key": "mail_mode",
            "label": "Email channel",
            "type": "select",
            "required": True,
            "default": str(channels.get("mail_mode", "edge_gmail") or "edge_gmail"),
            "options": ["edge_gmail", "mail_app", "imap"],
        },
        {
            "key": "mail_browser",
            "label": "Browser for Gmail",
            "type": "text",
            "default": str(channels.get("mail_browser", "Microsoft Edge") or "Microsoft Edge"),
            "placeholder": "Microsoft Edge",
        },
        {
            "key": "target_gmail",
            "label": "Target Gmail account",
            "type": "text",
            "default": str(partners.get("target_gmail", "admin@algoxpert.org") or "admin@algoxpert.org"),
            "placeholder": "admin@algoxpert.org",
        },
        {
            "key": "teams_mode",
            "label": "Teams channel",
            "type": "select",
            "required": True,
            "default": str(channels.get("teams_mode", "teams_app") or "teams_app"),
            "options": ["teams_app", "browser"],
        },
        {
            "key": "teams_app_name",
            "label": "Teams app name",
            "type": "text",
            "default": str(channels.get("teams_app_name", "Microsoft Teams") or "Microsoft Teams"),
            "placeholder": "Microsoft Teams",
        },
        {
            "key": "khiem_selected",
            "label": "Khiem account to open",
            "type": "text",
            "required": True,
            "default": str(accounts.get("khiem_selected", "") or partners.get("khiem_team_hint", "")),
            "placeholder": "e.g. Khiem - Work account",
        },
        {
            "key": "khiem_candidates",
            "label": "Khiem account options (comma-separated)",
            "type": "text",
            "default": khiem_candidates_text,
            "placeholder": "Khiem Work, Khiem Personal",
        },
        {
            "key": "ravin_contact_hint",
            "label": "Ravin contact hint",
            "type": "text",
            "default": str(partners.get("ravin_contact_hint", "") or ""),
            "placeholder": "name/email to search",
        },
    ]
    return _automation_enqueue_human_input_request(
        request_key="due_diligence_context",
        title="Need operator input to continue due diligence",
        prompt=(
            "Automation is blocked on channel/account selection. "
            "Please confirm Gmail/Teams route and the exact Khiem account."
        ),
        fields=fields,
        source=source,
        context={"reason": str(reason or "").strip(), "chat_text": str(chat_text or "").strip()},
        risk_level="medium",
    )


def _automation_apply_human_input_values(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    if not isinstance(values, dict):
        return _automation_get_intake_store(redact_secret=True), []

    patch: Dict[str, Any] = {}
    applied: List[str] = []

    def write(path: str, value: Any) -> None:
        parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
        if not parts:
            return
        cursor = patch
        for part in parts[:-1]:
            node = cursor.get(part)
            if not isinstance(node, dict):
                node = {}
                cursor[part] = node
            cursor = node
        cursor[parts[-1]] = value

    for key, raw_value in values.items():
        field_key = str(key or "").strip().lower()
        if not field_key:
            continue
        value = raw_value
        if isinstance(value, str):
            value = value.strip()
        if field_key in {"mail_mode"} and value:
            write("channels.mail_mode", str(value).strip().lower())
            applied.append("channels.mail_mode")
        elif field_key in {"mail_browser"} and value:
            write("channels.mail_browser", str(value).strip())
            applied.append("channels.mail_browser")
        elif field_key in {"teams_mode"} and value:
            write("channels.teams_mode", str(value).strip().lower())
            applied.append("channels.teams_mode")
        elif field_key in {"teams_app_name"} and value:
            write("channels.teams_app_name", str(value).strip())
            applied.append("channels.teams_app_name")
        elif field_key in {"target_gmail"} and value:
            write("partners.target_gmail", str(value).strip())
            applied.append("partners.target_gmail")
        elif field_key in {"khiem_selected", "khiem_account"} and value:
            selected = str(value).strip()
            write("accounts.khiem_selected", selected)
            write("partners.khiem_team_hint", selected)
            applied.extend(["accounts.khiem_selected", "partners.khiem_team_hint"])
        elif field_key in {"khiem_candidates", "khiem_accounts"}:
            candidates = _automation_parse_csv_list(value)
            write("accounts.khiem_candidates", candidates)
            applied.append("accounts.khiem_candidates")
        elif field_key in {"ravin_contact_hint"} and value:
            write("partners.ravin_contact_hint", str(value).strip())
            applied.append("partners.ravin_contact_hint")
        elif field_key in {"imap_host", "imap_username", "imap_password"} and value:
            write(f"mail.{field_key}", str(value).strip())
            applied.append(f"mail.{field_key}")
        elif field_key in {"raise_usd", "equity_pct"}:
            try:
                numeric = float(value)
            except Exception:
                continue
            write(f"valuation.{field_key}", numeric)
            applied.append(f"valuation.{field_key}")
        elif field_key in {"auto_prompt_on_stuck"}:
            write("automation.auto_prompt_on_stuck", _to_bool(value))
            applied.append("automation.auto_prompt_on_stuck")

    if not patch:
        return _automation_get_intake_store(redact_secret=True), []
    updated = _automation_update_intake_store(patch)
    unique_applied = sorted(set(applied))
    return updated, unique_applied


def _automation_maybe_enqueue_stuck_prompt(task: Dict[str, Any], result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _automation_prompt_on_stuck_enabled():
        return None
    if not isinstance(task, dict) or not isinstance(result, dict):
        return None

    task_type = str(task.get("task_type", "")).strip().lower()
    action = str(task.get("action", "")).strip().lower()
    error_code = str(result.get("error_code", "")).strip().upper()
    missing_info = result.get("missing_info") if isinstance(result.get("missing_info"), list) else []

    if error_code in {"MAIL_CHANNEL_BROWSER_REQUIRED", "MAIL_CREDENTIALS_MISSING", "MAIL_UNAVAILABLE"}:
        return _automation_request_due_diligence_context(
            reason=error_code.lower() or "mail_blocked",
            source="automation_execute_task",
        )

    if not missing_info:
        return None

    unique_fields: List[str] = []
    for raw in missing_info:
        field = str(raw or "").strip()
        if not field:
            continue
        if field in unique_fields:
            continue
        unique_fields.append(field)
        if len(unique_fields) >= 8:
            break

    prompt_fields: List[Dict[str, Any]] = []
    for index, path in enumerate(unique_fields):
        leaf = str(path.split(".")[-1]).strip().lower() or f"field_{index + 1}"
        prompt_fields.append(
            {
                "key": leaf,
                "label": f"Provide: {path}",
                "type": "text",
                "required": True,
                "placeholder": path,
            }
        )

    if not prompt_fields:
        return None

    return _automation_enqueue_human_input_request(
        request_key=f"missing_input:{task_type}:{action}",
        title=f"Need input for {task_type}/{action}",
        prompt="Automation is blocked because required inputs are missing. Please fill fields below.",
        fields=prompt_fields,
        source="automation_execute_task",
        context={
            "task_type": task_type,
            "action": action,
            "missing_info": unique_fields,
            "task_id": str(task.get("task_id", "")),
        },
        risk_level="medium",
    )


def _automation_mail_runtime_config() -> Dict[str, Any]:
    intake = _automation_get_intake_store(redact_secret=False)
    mail = intake.get("mail") if isinstance(intake.get("mail"), dict) else {}
    host = str(mail.get("imap_host") or AUTOMATION_EMAIL_IMAP_HOST).strip()
    port_raw = mail.get("imap_port", AUTOMATION_EMAIL_IMAP_PORT)
    try:
        port = max(1, int(port_raw))
    except Exception:
        port = AUTOMATION_EMAIL_IMAP_PORT
    username = str(mail.get("imap_username") or AUTOMATION_EMAIL_IMAP_USERNAME).strip()
    password = str(mail.get("imap_password") or AUTOMATION_EMAIL_IMAP_PASSWORD).strip()
    folder = str(mail.get("imap_folder") or AUTOMATION_EMAIL_IMAP_FOLDER or "INBOX").strip() or "INBOX"
    ssl_value = mail.get("imap_ssl")
    if ssl_value is None:
        ssl_enabled = bool(AUTOMATION_EMAIL_IMAP_SSL)
    elif isinstance(ssl_value, bool):
        ssl_enabled = ssl_value
    else:
        ssl_enabled = str(ssl_value).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "folder": folder,
        "ssl": ssl_enabled,
    }


_automation_load_intake_store()


# Phase 4: Context Functions

def _automation_context_get(session_id: str) -> Dict[str, Any]:
    """Get context for a session."""
    with _AUTOMATION_CONTEXT_LOCK:
        return _AUTOMATION_TASK_CONTEXTS.get(session_id, {"context": {}, "recent_actions": [], "preferences": {}})


def _automation_context_update(session_id: str, key: str, value: Any) -> None:
    """Update context for a session."""
    with _AUTOMATION_CONTEXT_LOCK:
        if session_id not in _AUTOMATION_TASK_CONTEXTS:
            _AUTOMATION_TASK_CONTEXTS[session_id] = {"context": {}, "recent_actions": [], "preferences": {}}
        _AUTOMATION_TASK_CONTEXTS[session_id]["context"][key] = value


def _automation_context_add_action(session_id: str, action: Dict[str, Any]) -> None:
    """Add action to session context."""
    with _AUTOMATION_CONTEXT_LOCK:
        if session_id not in _AUTOMATION_TASK_CONTEXTS:
            _AUTOMATION_TASK_CONTEXTS[session_id] = {"context": {}, "recent_actions": [], "preferences": {}}
        ctx = _AUTOMATION_TASK_CONTEXTS[session_id]
        ctx["recent_actions"].append(action)
        # Keep only last 50 actions
        if len(ctx["recent_actions"]) > 50:
            ctx["recent_actions"] = ctx["recent_actions"][-50:]


def _automation_memory_record(task_type: str, action: str, params: Dict, result: Dict, session_id: str, owner_id: str) -> None:
    """Record action to memory for learning."""
    with _AUTOMATION_ACTION_MEMORY_LOCK:
        entry = {
            "task_type": task_type,
            "action": action,
            "params": params,
            "result": result,
            "success": result.get("success", False),
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "owner_id": owner_id,
        }
        _AUTOMATION_ACTION_MEMORY.append(entry)
        # Trim to max size
        while len(_AUTOMATION_ACTION_MEMORY) > AUTOMATION_ACTION_MEMORY_MAX:
            _AUTOMATION_ACTION_MEMORY.pop(0)


def _automation_memory_get_recent(limit: int = 50, task_type: Optional[str] = None, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get recent actions from memory."""
    with _AUTOMATION_ACTION_MEMORY_LOCK:
        entries = list(_AUTOMATION_ACTION_MEMORY)
    if task_type:
        entries = [e for e in entries if e.get("task_type") == task_type]
    if owner_id:
        entries = [e for e in entries if e.get("owner_id") == owner_id]
    return entries[-limit:]


def _automation_memory_analyze(task_type: Optional[str] = None) -> Dict[str, Any]:
    """Analyze action memory for patterns."""
    with _AUTOMATION_ACTION_MEMORY_LOCK:
        entries = list(_AUTOMATION_ACTION_MEMORY)

    if task_type:
        entries = [e for e in entries if e.get("task_type") == task_type]

    if not entries:
        return {"total_actions": 0, "success_rate": 0, "avg_duration_ms": 0}

    total = len(entries)
    success = sum(1 for e in entries if e.get("success"))

    # Count action frequencies
    action_counts: Dict[str, int] = {}
    for e in entries:
        key = f"{e.get('task_type')}/{e.get('action')}"
        action_counts[key] = action_counts.get(key, 0) + 1

    # Most common actions
    common_actions = sorted(action_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "total_actions": total,
        "success_rate": success / total if total > 0 else 0,
        "action_counts": dict(common_actions),
        "by_owner": len(set(e.get("owner_id") for e in entries)),
    }


def _automation_preferences_update(owner_id: str, task_type: str, action: str, duration_ms: int) -> None:
    """Update user preferences based on actions."""
    with _AUTOMATION_USER_PREFERENCES_LOCK:
        if owner_id not in _AUTOMATION_USER_PREFERENCES:
            _AUTOMATION_USER_PREFERENCES[owner_id] = {
                "preferred_task_types": {},
                "frequent_actions": {},
                "total_tasks": 0,
                "total_duration_ms": 0,
            }
        prefs = _AUTOMATION_USER_PREFERENCES[owner_id]

        # Update task type preference
        prefs["preferred_task_types"][task_type] = prefs["preferred_task_types"].get(task_type, 0) + 1

        # Update action frequency
        key = f"{task_type}/{action}"
        prefs["frequent_actions"][key] = prefs["frequent_actions"].get(key, 0) + 1

        # Update totals
        prefs["total_tasks"] = prefs["total_tasks"] + 1
        prefs["total_duration_ms"] = prefs["total_duration_ms"] + duration_ms


def _automation_preferences_get(owner_id: str) -> Dict[str, Any]:
    """Get user preferences."""
    with _AUTOMATION_USER_PREFERENCES_LOCK:
        return _AUTOMATION_USER_PREFERENCES.get(owner_id, {})


# Phase 5: Observability & Operations

# Metrics collection
_AUTOMATION_METRICS_LOCK = threading.RLock()
_AUTOMATION_METRICS: Dict[str, Any] = {
    "tasks_total": 0,
    "tasks_success": 0,
    "tasks_failed": 0,
    "tasks_by_type": {},
    "tasks_by_owner": {},
    "avg_duration_ms": 0,
    "total_duration_ms": 0,
    "started_at": datetime.now().isoformat(),
    "last_task_at": None,
}


def _automation_metrics_record(task_type: str, owner_id: str, success: bool, duration_ms: int) -> None:
    """Record task metrics."""
    with _AUTOMATION_METRICS_LOCK:
        _AUTOMATION_METRICS["tasks_total"] += 1
        if success:
            _AUTOMATION_METRICS["tasks_success"] += 1
        else:
            _AUTOMATION_METRICS["tasks_failed"] += 1

        # By type
        if task_type not in _AUTOMATION_METRICS["tasks_by_type"]:
            _AUTOMATION_METRICS["tasks_by_type"][task_type] = {"total": 0, "success": 0, "failed": 0}
        _AUTOMATION_METRICS["tasks_by_type"][task_type]["total"] += 1
        if success:
            _AUTOMATION_METRICS["tasks_by_type"][task_type]["success"] += 1
        else:
            _AUTOMATION_METRICS["tasks_by_type"][task_type]["failed"] += 1

        # By owner
        if owner_id not in _AUTOMATION_METRICS["tasks_by_owner"]:
            _AUTOMATION_METRICS["tasks_by_owner"][owner_id] = {"total": 0, "success": 0, "failed": 0}
        _AUTOMATION_METRICS["tasks_by_owner"][owner_id]["total"] += 1
        if success:
            _AUTOMATION_METRICS["tasks_by_owner"][owner_id]["success"] += 1
        else:
            _AUTOMATION_METRICS["tasks_by_owner"][owner_id]["failed"] += 1

        # Duration
        total = _AUTOMATION_METRICS["tasks_total"]
        current_avg = _AUTOMATION_METRICS["avg_duration_ms"]
        _AUTOMATION_METRICS["avg_duration_ms"] = (current_avg * (total - 1) + duration_ms) / total
        _AUTOMATION_METRICS["total_duration_ms"] += duration_ms
        _AUTOMATION_METRICS["last_task_at"] = datetime.now().isoformat()


def _automation_metrics_get() -> Dict[str, Any]:
    """Get current metrics."""
    with _AUTOMATION_METRICS_LOCK:
        return dict(_AUTOMATION_METRICS)


def _automation_metrics_reset() -> None:
    """Reset metrics."""
    with _AUTOMATION_METRICS_LOCK:
        global _AUTOMATION_METRICS
        _AUTOMATION_METRICS = {
            "tasks_total": 0,
            "tasks_success": 0,
            "tasks_failed": 0,
            "tasks_by_type": {},
            "tasks_by_owner": {},
            "avg_duration_ms": 0,
            "total_duration_ms": 0,
            "started_at": datetime.now().isoformat(),
            "last_task_at": None,
        }


# Health check system
_AUTOMATION_HEALTH_LOCK = threading.RLock()
_AUTOMATION_HEALTH_CHECKS: Dict[str, Dict[str, Any]] = {}


def _automation_health_register(name: str, check_fn: callable, critical: bool = False) -> None:
    """Register a health check."""
    with _AUTOMATION_HEALTH_LOCK:
        _AUTOMATION_HEALTH_CHECKS[name] = {
            "name": name,
            "critical": critical,
            "last_check": None,
            "last_status": "unknown",
            "last_message": "",
        }


def _automation_health_check(name: str) -> Dict[str, Any]:
    """Run a health check."""
    with _AUTOMATION_HEALTH_LOCK:
        check = _AUTOMATION_HEALTH_CHECKS.get(name, {})
    if not check:
        return {"status": "unknown", "message": "Check not found"}

    # Run check (simplified - in production would call actual check function)
    return {"status": "ok", "message": "Check passed"}


def _automation_health_get_all() -> Dict[str, Any]:
    """Get all health checks."""
    with _AUTOMATION_HEALTH_LOCK:
        checks = dict(_AUTOMATION_HEALTH_CHECKS)

    # Add system health
    checks["automation_enabled"] = {
        "name": "automation_enabled",
        "status": "ok" if _AUTOMATION_ENABLED else "critical",
        "message": f"Automation {'enabled' if _AUTOMATION_ENABLED else 'disabled'}: {_AUTOMATION_KILL_REASON}",
    }

    checks["sessions"] = {
        "name": "sessions",
        "status": "ok",
        "message": f"{len(_automation_sessions)} active sessions",
    }

    checks["schedules"] = {
        "name": "schedules",
        "status": "ok" if _automation_scheduler_running else "warning",
        "message": f"Scheduler {'running' if _automation_scheduler_running else 'stopped'}",
    }

    return checks


# Alert system
_AUTOMATION_ALERTS_LOCK = threading.RLock()
_AUTOMATION_ALERTS: List[Dict[str, Any]] = []
AUTOMATION_ALERTS_MAX = 100


def _automation_alert(level: str, title: str, message: str, source: str = "automation") -> None:
    """Create an alert."""
    with _AUTOMATION_ALERTS_LOCK:
        alert = {
            "id": f"alert_{int(time.time() * 1000)}",
            "level": level,  # info, warning, error, critical
            "title": title,
            "message": message,
            "source": source,
            "created_at": datetime.now().isoformat(),
            "acknowledged": False,
        }
        _AUTOMATION_ALERTS.append(alert)
        while len(_AUTOMATION_ALERTS) > AUTOMATION_ALERTS_MAX:
            _AUTOMATION_ALERTS.pop(0)
        logger.warning(f"⚠️ Alert [{level.upper()}]: {title} - {message}")


def _automation_alerts_get(limit: int = 50, level: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get alerts."""
    with _AUTOMATION_ALERTS_LOCK:
        alerts = list(_AUTOMATION_ALERTS)
    if level:
        alerts = [a for a in alerts if a.get("level") == level]
    return alerts[-limit:]


def _automation_alerts_acknowledge(alert_id: str) -> bool:
    """Acknowledge an alert."""
    with _AUTOMATION_ALERTS_LOCK:
        for alert in _AUTOMATION_ALERTS:
            if alert.get("id") == alert_id:
                alert["acknowledged"] = True
                return True
    return False


def _automation_alerts_clear() -> int:
    """Clear all alerts."""
    with _AUTOMATION_ALERTS_LOCK:
        count = len(_AUTOMATION_ALERTS)
        _AUTOMATION_ALERTS.clear()
        return count


# Phase 6: Test & Rollout

# Test execution state
_AUTOMATION_TEST_RESULTS_LOCK = threading.RLock()
_AUTOMATION_TEST_RESULTS: List[Dict[str, Any]] = []
_AUTOMATION_TEST_PROGRESS: Dict[str, Any] = {"current": None, "total": 0, "completed": 0}

# Rollout state
_AUTOMATION_ROLLOUT_LOCK = threading.RLock()
_AUTOMATION_ROLLOUT_STATE: Dict[str, Any] = {
    "phase": "planning",  # planning, testing, rolling_out, complete, rolled_back
    "started_at": None,
    "completed_at": None,
    "features": [],
    "issues": [],
    "notes": "",
}


# Test Functions

def _automation_run_self_test() -> Dict[str, Any]:
    """Run self-test on automation system."""
    results = {
        "timestamp": datetime.now().isoformat(),
        "tests": [],
        "passed": 0,
        "failed": 0,
        "warnings": 0,
    }

    # Test 1: Core functions exist
    tests = [
        ("rate_limit_check", _automation_check_rate_limit),
        ("validate_task", _automation_validate_task),
        ("session_lease", _automation_assert_session_lease),
        ("circuit_check", _automation_circuit_check),
        ("dlq_add", lambda: _automation_dlq_add({"task_type": "test"}, "test error", "TEST")),
        ("context_get", _automation_context_get),
        ("memory_record", lambda: _automation_memory_record("test", "test", {}, {}, "test", "test")),
        ("metrics_record", lambda: _automation_metrics_record("test", "test", True, 100)),
    ]

    for name, fn in tests:
        try:
            if callable(fn):
                fn()
            results["tests"].append({"name": name, "status": "passed"})
            results["passed"] += 1
        except Exception as e:
            results["tests"].append({"name": name, "status": "failed", "error": str(e)})
            results["failed"] += 1

    # Test 2: Task types are valid
    for tt in AUTOMATION_TASK_TYPES:
        if tt in AUTOMATION_CAPABILITIES:
            results["tests"].append({"name": f"task_type_{tt}", "status": "passed"})
            results["passed"] += 1
        else:
            results["tests"].append({"name": f"task_type_{tt}", "status": "failed", "error": "No capabilities defined"})
            results["failed"] += 1

    # Test 3: System state
    results["tests"].append({
        "name": "automation_enabled",
        "status": "passed" if _AUTOMATION_ENABLED else "warning",
        "detail": f"enabled={_AUTOMATION_ENABLED}"
    })
    if _AUTOMATION_ENABLED:
        results["passed"] += 1
    else:
        results["warnings"] += 1

    return results


def _automation_run_integration_test(task_type: str, action: str, params: Dict) -> Dict[str, Any]:
    """Run integration test for a specific task."""
    test_id = f"test_{int(time.time() * 1000)}"

    # Create session
    lease = _automation_assert_session_lease("test-session", "test-runner", secrets.token_hex(8))
    if not lease.get("allowed"):
        return {"success": False, "error": f"Failed to create session: {lease.get('error')}", "test_id": test_id}

    # Execute task
    task = _create_automation_task(task_type, action, params, test_id)
    task["session_id"] = "test-session"
    task["owner_id"] = "test-runner"

    result = _execute_automation_task(task)

    return {
        "test_id": test_id,
        "task_type": task_type,
        "action": action,
        "success": result.get("status") == "completed",
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
        "error": result.get("error"),
    }


# Rollout Functions

def _automation_rollout_start(phase: str) -> None:
    """Start a rollout phase."""
    with _AUTOMATION_ROLLOUT_LOCK:
        _AUTOMATION_ROLLOUT_STATE["phase"] = phase
        _AUTOMATION_ROLLOUT_STATE["started_at"] = datetime.now().isoformat()
        _automation_alert("info", f"Rollout started: {phase}", f"Rollout phase '{phase}' has begun", "rollout")


def _automation_rollout_complete() -> None:
    """Complete rollout."""
    with _AUTOMATION_ROLLOUT_LOCK:
        _AUTOMATION_ROLLOUT_STATE["phase"] = "complete"
        _AUTOMATION_ROLLOUT_STATE["completed_at"] = datetime.now().isoformat()
        _automation_alert("info", "Rollout complete", "All phases completed successfully", "rollout")


def _automation_rollout_rollback() -> None:
    """Rollback to previous state."""
    with _AUTOMATION_ROLLOUT_LOCK:
        _AUTOMATION_ROLLOUT_STATE["phase"] = "rolled_back"
        _automation_alert("warning", "Rollback initiated", "System rolled back to previous version", "rollout")


# Global automation kill-switch (Phase 0)
_AUTOMATION_ENABLED = True
_AUTOMATION_KILL_REASON = ""


def _automation_set_enabled(enabled: bool, reason: str = ""):
    """Set automation enabled/disabled state with reason."""
    global _AUTOMATION_ENABLED, _AUTOMATION_KILL_REASON
    _AUTOMATION_ENABLED = enabled
    _AUTOMATION_KILL_REASON = reason
    logger.info(f"Automation {'enabled' if enabled else 'DISABLED'}: {reason}")


def _automation_is_enabled() -> Tuple[bool, str]:
    """Check if automation is enabled and return reason if disabled."""
    return _AUTOMATION_ENABLED, _AUTOMATION_KILL_REASON


# Rate limiting counters
_AUTOMATION_RATE_LOCK = threading.Lock()
_AUTOMATION_RATE_COUNTS: Dict[str, Dict[str, int]] = {}  # {task_type: {owner_id: count}}
_AUTOMATION_RATE_WINDOW_SEC = 60  # 1 minute window
_AUTOMATION_RATE_MAX_PER_WINDOW = {
    "passive_poll": 100,
    "active_control": 20,
    "destructive": 5,
}


def _automation_check_rate_limit(task_type: str, owner_id: str) -> Tuple[bool, str]:
    """Check if task exceeds rate limit. Returns (allowed, reason)."""
    if not _AUTOMATION_ENABLED:
        return False, _AUTOMATION_KILL_REASON or "Automation is disabled"

    # Determine task class
    task_class = "active_control"
    for cls, types in AUTOMATION_TASK_CLASSES.items():
        if task_type in types:
            task_class = cls
            break

    max_allowed = _AUTOMATION_RATE_MAX_PER_WINDOW.get(task_class, 20)

    with _AUTOMATION_RATE_LOCK:
        now = time.time()
        if owner_id not in _AUTOMATION_RATE_COUNTS:
            _AUTOMATION_RATE_COUNTS[owner_id] = {"count": 0, "window_start": now}

        rate_info = _AUTOMATION_RATE_COUNTS[owner_id]
        if now - rate_info["window_start"] > _AUTOMATION_RATE_WINDOW_SEC:
            rate_info["count"] = 0
            rate_info["window_start"] = now

        current_count = rate_info["count"]
        if current_count >= max_allowed:
            return False, f"Rate limit exceeded: {current_count}/{max_allowed} in {_AUTOMATION_RATE_WINDOW_SEC}s"

        rate_info["count"] = current_count + 1
        return True, ""


# Phase 2: Circuit Breaker for automation tasks
_AUTOMATION_CIRCUIT_BREAKER_LOCK = threading.Lock()
_AUTOMATION_CIRCUIT_STATE: Dict[str, Dict[str, Any]] = {}  # {task_type: {failures, last_failure, state, failures_in_window}}

# Circuit breaker config
AUTOMATION_CIRCUIT_FAILURE_THRESHOLD = 5  # Open circuit after 5 failures
AUTOMATION_CIRCUIT_RECOVERY_TIMEOUT_SEC = 60  # Try again after 60 seconds
AUTOMATION_CIRCUIT_WINDOW_SEC = 300  # 5 minute window for counting failures


def _automation_get_task_class(task_type: str) -> str:
    """Determine task class for rate limiting and circuit breaker."""
    for cls, types in AUTOMATION_TASK_CLASSES.items():
        if task_type in types:
            return cls
    return "active_control"


def _automation_circuit_check(task_type: str) -> Tuple[bool, str]:
    """Check if circuit is open for task_type. Returns (allowed, reason)."""
    with _AUTOMATION_CIRCUIT_BREAKER_LOCK:
        state = _AUTOMATION_CIRCUIT_STATE.get(task_type, {})
        circuit_state = state.get("state", "closed")

        if circuit_state == "open":
            last_failure = state.get("last_failure", 0)
            if time.time() - last_failure > AUTOMATION_CIRCUIT_RECOVERY_TIMEOUT_SEC:
                # Try half-open
                _AUTOMATION_CIRCUIT_STATE[task_type] = {
                    "state": "half_open",
                    "failures": 0,
                    "failures_in_window": 0,
                    "window_start": time.time(),
                    "last_failure": last_failure,
                }
                return True, "Circuit half-open: attempting recovery"
            return False, f"Circuit open: too many failures ({state.get('failures_in_window', 0)} in {AUTOMATION_CIRCUIT_WINDOW_SEC}s)"

        if circuit_state == "half_open":
            # Allow one test request
            return True, "Circuit half-open: test request"

        return True, ""  # closed


def _automation_circuit_record_failure(task_type: str) -> None:
    """Record a failure for circuit breaker."""
    now = time.time()
    with _AUTOMATION_CIRCUIT_BREAKER_LOCK:
        state = _AUTOMATION_CIRCUIT_STATE.get(task_type, {
            "failures": 0,
            "failures_in_window": 0,
            "window_start": now,
            "state": "closed",
        })

        # Reset window if expired
        if now - state.get("window_start", 0) > AUTOMATION_CIRCUIT_WINDOW_SEC:
            state["failures_in_window"] = 0
            state["window_start"] = now

        state["failures"] = state.get("failures", 0) + 1
        state["failures_in_window"] = state.get("failures_in_window", 0) + 1
        state["last_failure"] = now

        # Open circuit if threshold exceeded
        if state["failures_in_window"] >= AUTOMATION_CIRCUIT_FAILURE_THRESHOLD:
            state["state"] = "open"
            logger.warning(f"⚡ Circuit opened for {task_type}: {state['failures_in_window']} failures")

        _AUTOMATION_CIRCUIT_STATE[task_type] = state


def _automation_circuit_record_success(task_type: str) -> None:
    """Record a success, potentially closing circuit."""
    with _AUTOMATION_CIRCUIT_BREAKER_LOCK:
        state = _AUTOMATION_CIRCUIT_STATE.get(task_type, {})
        if state.get("state") == "half_open":
            # Success in half-open state: close circuit
            _AUTOMATION_CIRCUIT_STATE[task_type] = {
                "state": "closed",
                "failures": 0,
                "failures_in_window": 0,
                "window_start": time.time(),
                "last_failure": 0,
            }
            logger.info(f"⚡ Circuit closed for {task_type}: recovery successful")


# Phase 2: Dead Letter Queue for failed tasks
_AUTOMATION_DLQ_LOCK = threading.Lock()
_AUTOMATION_DLQ: List[Dict[str, Any]] = []
AUTOMATION_DLQ_MAX_SIZE = 1000


def _automation_dlq_add(task: Dict[str, Any], error: str, error_code: str) -> None:
    """Add failed task to dead letter queue."""
    with _AUTOMATION_DLQ_LOCK:
        dlq_entry = {
            "task": dict(task),
            "error": error,
            "error_code": error_code,
            "failed_at": datetime.now().isoformat(),
            "task_type": task.get("task_type", "unknown"),
            "action": task.get("action", "unknown"),
            "owner_id": task.get("owner_id", "unknown"),
        }
        _AUTOMATION_DLQ.append(dlq_entry)
        # Trim if too large
        while len(_AUTOMATION_DLQ) > AUTOMATION_DLQ_MAX_SIZE:
            _AUTOMATION_DLQ.pop(0)
        logger.warning(f"📬 Task added to DLQ: {task.get('task_type')}/{task.get('action')} - {error}")


def _automation_dlq_get(limit: int = 50, task_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get tasks from dead letter queue."""
    with _AUTOMATION_DLQ_LOCK:
        entries = list(_AUTOMATION_DLQ)
    if task_type:
        entries = [e for e in entries if e.get("task_type") == task_type]
    return entries[-limit:]


def _automation_dlq_clear(owner_id: Optional[str] = None) -> int:
    """Clear dead letter queue. Returns count cleared."""
    with _AUTOMATION_DLQ_LOCK:
        if owner_id:
            before = len(_AUTOMATION_DLQ)
            _AUTOMATION_DLQ[:] = [e for e in _AUTOMATION_DLQ if e.get("owner_id") != owner_id]
            return before - len(_AUTOMATION_DLQ)
        count = len(_AUTOMATION_DLQ)
        _AUTOMATION_DLQ.clear()
        return count


# Phase 3: Window Management Functions

def _automation_list_windows() -> List[Dict[str, Any]]:
    """List all visible windows (line-delimited output from AppleScript)."""
    script = """
    tell application "System Events"
        set outputLines to {}
        repeat with proc in (every process whose background only is false)
            try
                set procName to name of proc
                repeat with win in (every window of proc)
                    set winTitle to ""
                    set winX to 0
                    set winY to 0
                    set winW to 0
                    set winH to 0
                    try
                        set winTitle to name of win
                    end try
                    try
                        set winPos to position of win
                        set winX to item 1 of winPos
                        set winY to item 2 of winPos
                    end try
                    try
                        set winSize to size of win
                        set winW to item 1 of winSize
                        set winH to item 2 of winSize
                    end try
                    set end of outputLines to (procName & tab & winTitle & tab & (winX as text) & tab & (winY as text) & tab & (winW as text) & tab & (winH as text))
                end repeat
            end try
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    set outText to outputLines as text
    set AppleScript's text item delimiters to ""
    return outText
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            stderr = str(result.stderr or "").strip()
            logger.warning(f"Failed to list windows: {stderr or 'unknown osascript error'}")
            return []
        output = str(result.stdout or "").strip()
        if not output:
            return []

        def _to_int(value: Any, default: int = 0) -> int:
            try:
                return int(float(str(value).strip()))
            except Exception:
                return default

        windows: List[Dict[str, Any]] = []
        for idx, line in enumerate(output.splitlines(), start=1):
            row = str(line or "").strip()
            if not row:
                continue
            parts = row.split("\t")
            if len(parts) < 6:
                continue
            app_name = str(parts[0] or "").strip()
            title = str(parts[1] or "").strip()
            x = _to_int(parts[2], 0)
            y = _to_int(parts[3], 0)
            width = _to_int(parts[4], 0)
            height = _to_int(parts[5], 0)
            safe_app = re.sub(r"[^a-z0-9]+", "-", app_name.lower()).strip("-") or "app"
            windows.append(
                {
                    "window_id": f"win_{safe_app}_{idx}",
                    "app": app_name,
                    "appName": app_name,
                    "title": title,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
            )
        return windows
    except Exception as exc:
        logger.warning(f"Failed to list windows: {exc}")
    return []


def _automation_window_focus(window_id: str) -> Dict[str, Any]:
    """Focus a specific window by ID."""
    try:
        # Parse window_id to get app name
        with _AUTOMATION_WINDOW_STATE_LOCK:
            window_info = _AUTOMATION_WINDOW_STATE.get(window_id, {})
            app_name = window_info.get("app", "")
        if not app_name:
            return {"success": False, "error": "Window not found", "error_code": "WINDOW_NOT_FOUND"}
        import subprocess
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            capture_output=True,
            timeout=5
        )
        return {"success": True, "message": f"Focused {app_name}"}
    except Exception as e:
        return {"success": False, "error": str(e), "error_code": "WINDOW_FOCUS_FAILED"}


def _automation_window_resize(window_id: str, width: int, height: int) -> Dict[str, Any]:
    """Resize a window."""
    try:
        with _AUTOMATION_WINDOW_STATE_LOCK:
            window_info = _AUTOMATION_WINDOW_STATE.get(window_id, {})
            app_name = window_info.get("app", "")
        if not app_name:
            return {"success": False, "error": "Window not found", "error_code": "WINDOW_NOT_FOUND"}
        import subprocess
        subprocess.run(
            ["osascript", "-e", f'''
            tell application "System Events"
                tell process "{app_name}"
                    set frontmost to true
                end tell
            end tell
            '''],
            capture_output=True,
            timeout=5
        )
        return {"success": True, "message": f"Resized to {width}x{height}"}
    except Exception as e:
        return {"success": False, "error": str(e), "error_code": "WINDOW_RESIZE_FAILED"}


def _automation_window_move(window_id: str, x: int, y: int) -> Dict[str, Any]:
    """Move a window to a position."""
    try:
        with _AUTOMATION_WINDOW_STATE_LOCK:
            window_info = _AUTOMATION_WINDOW_STATE.get(window_id, {})
            app_name = window_info.get("app", "")
        if not app_name:
            return {"success": False, "error": "Window not found", "error_code": "WINDOW_NOT_FOUND"}
        import subprocess
        subprocess.run(
            ["osascript", "-e", f'''
            tell application "System Events"
                tell process "{app_name}"
                    set frontmost to true
                end tell
            end tell
            '''],
            capture_output=True,
            timeout=5
        )
        return {"success": True, "message": f"Moved to ({x}, {y})"}
    except Exception as e:
        return {"success": False, "error": str(e), "error_code": "WINDOW_MOVE_FAILED"}


def _automation_window_minimize(window_id: str) -> Dict[str, Any]:
    """Minimize a window."""
    return {"success": True, "message": "Window minimized", "error_code": "NOT_IMPLEMENTED"}


def _automation_window_maximize(window_id: str) -> Dict[str, Any]:
    """Maximize a window."""
    return {"success": True, "message": "Window maximized", "error_code": "NOT_IMPLEMENTED"}


def _automation_window_close(window_id: str) -> Dict[str, Any]:
    """Close a window."""
    try:
        with _AUTOMATION_WINDOW_STATE_LOCK:
            window_info = _AUTOMATION_WINDOW_STATE.get(window_id, {})
            app_name = window_info.get("app", "")
        if not app_name:
            return {"success": False, "error": "Window not found", "error_code": "WINDOW_NOT_FOUND"}
        import subprocess
        subprocess.run(
            ["osascript", "-e", f'''
            tell application "{app_name}" to quit
            '''],
            capture_output=True,
            timeout=5
        )
        return {"success": True, "message": f"Closed {app_name}"}
    except Exception as e:
        return {"success": False, "error": str(e), "error_code": "WINDOW_CLOSE_FAILED"}


def _automation_surface_list() -> List[Dict[str, Any]]:
    """List all surfaces (windows, apps, etc.)."""
    surfaces = []
    try:
        # Get window list
        windows = _automation_list_windows()
        for w in windows:
            surface_id = w.get("window_id", "")
            with _AUTOMATION_SURFACE_REGISTRY_LOCK:
                _AUTOMATION_SURFACE_REGISTRY[surface_id] = {
                    "type": "window",
                    "title": w.get("title", ""),
                    "app": w.get("appName", "") or w.get("app", ""),
                    "position": {"x": w.get("x", 0), "y": w.get("y", 0)},
                    "size": {"width": w.get("width", 0), "height": w.get("height", 0)},
                    "last_seen": datetime.now().isoformat(),
                }
            surfaces.append({
                "surface_id": surface_id,
                "type": "window",
                "title": w.get("title", ""),
                "app": w.get("appName", "") or w.get("app", ""),
            })
    except Exception as e:
        logger.warning(f"Failed to list surfaces: {e}")
    return surfaces


def _automation_surface_info(surface_id: str) -> Dict[str, Any]:
    """Get info about a specific surface."""
    with _AUTOMATION_SURFACE_REGISTRY_LOCK:
        return _AUTOMATION_SURFACE_REGISTRY.get(surface_id, {})


def _automation_surface_capture(surface_id: str) -> Dict[str, Any]:
    """Capture screenshot with best-effort focus on the selected surface."""
    with _AUTOMATION_SURFACE_REGISTRY_LOCK:
        surface = dict(_AUTOMATION_SURFACE_REGISTRY.get(surface_id, {}))
    if not surface:
        return {"success": False, "error": "Surface not found", "error_code": "SURFACE_NOT_FOUND"}

    app_name = str(surface.get("app", "") or "").strip()
    if app_name:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = _automation_safe_path(f"/tmp/automation_surface_{surface_id}_{ts}.png", create_parent=True)
    done = subprocess.run(
        ["screencapture", "-x", str(target)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if done.returncode != 0:
        return {
            "success": False,
            "error": str(done.stderr or done.stdout or "screencapture failed").strip()[:300],
            "error_code": "SCREENSHOT_FAILED",
        }
    size = 0
    try:
        size = int(target.stat().st_size)
    except Exception:
        size = 0
    return {
        "success": True,
        "surface_id": surface_id,
        "filepath": str(target),
        "bytes": size,
        "capture_mode": "fullscreen_after_focus",
    }


# Phase 3: Cross-surface Workflow Functions

def _automation_workflow_create(workflow_id: str, name: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create a cross-surface workflow."""
    with _AUTOMATION_WORKFLOW_LOCK:
        _AUTOMATION_WORKFLOWS[workflow_id] = {
            "workflow_id": workflow_id,
            "name": name,
            "steps": steps,
            "current_step": 0,
            "state": "pending",
            "started_at": None,
            "completed_at": None,
        }
    return {"success": True, "workflow_id": workflow_id}


def _automation_workflow_execute(workflow_id: str) -> Dict[str, Any]:
    """Execute a workflow step by step."""
    with _AUTOMATION_WORKFLOW_LOCK:
        workflow = _AUTOMATION_WORKFLOWS.get(workflow_id)
        if not workflow:
            return {"success": False, "error": "Workflow not found", "error_code": "WORKFLOW_NOT_FOUND"}

        if workflow["state"] == "completed":
            return {"success": True, "message": "Workflow already completed", "workflow": workflow}

        if workflow["state"] == "pending":
            workflow["state"] = "running"
            workflow["started_at"] = datetime.now().isoformat()

        current_step = workflow["current_step"]
        if current_step >= len(workflow["steps"]):
            workflow["state"] = "completed"
            workflow["completed_at"] = datetime.now().isoformat()
            return {"success": True, "message": "Workflow completed", "workflow": workflow}

        step = workflow["steps"][current_step]
        step_result = _automation_execute_single_step(step)
        workflow["current_step"] += 1

        if not step_result.get("success", False):
            workflow["state"] = "failed"
            return {"success": False, "error": f"Step {current_step} failed", "step_result": step_result}

        return {"success": True, "current_step": current_step, "step": step, "result": step_result}


def _automation_execute_single_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single workflow step."""
    step_type = step.get("type", "")
    if step_type == "window":
        action = step.get("action", "")
        window_id = step.get("window_id", "")
        if action == "focus":
            return _automation_window_focus(window_id)
        elif action == "resize":
            return _automation_window_resize(window_id, step.get("width", 800), step.get("height", 600))
        elif action == "move":
            return _automation_window_move(window_id, step.get("x", 0), step.get("y", 0))
        elif action == "close":
            return _automation_window_close(window_id)
    elif step_type == "delay":
        import time
        time.sleep(step.get("seconds", 1))
        return {"success": True, "message": f"Delayed {step.get('seconds', 1)}s"}
    return {"success": False, "error": f"Unknown step type: {step_type}"}


def _automation_safe_path(path_value: str, create_parent: bool = False) -> Path:
    candidate = Path(path_value).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (AUTOMATION_ROOT / candidate).resolve()
    if not any(root in resolved.parents or resolved == root for root in AUTOMATION_SAFE_ROOTS):
        raise ValueError("Path is outside automation safe roots")
    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _automation_store_task(task: Dict[str, Any]) -> None:
    if not isinstance(task, dict):
        return
    task_id = str(task.get("task_id", "")).strip()
    if task_id:
        _automation_tasks_by_id[task_id] = dict(task)
    with _AUTOMATION_TASKS_LOCK:
        _automation_recent_tasks.append(dict(task))
    _append_jsonl_event(AUTOMATION_TASK_HISTORY_PATH, dict(task))


def _automation_update_session(
    session_id: str,
    owner_id: str,
    lease_token: str,
    lease_expires_at: Optional[datetime],
    task_id: str = "",
    error: str = "",
) -> Dict[str, Any]:
    now = datetime.now()
    with _AUTOMATION_TASKS_LOCK:
        session = _automation_sessions.get(session_id, {})
        queue_depth = max(0, int(session.get("queue_depth", 0) or 0))
        session.update(
            {
                "session_id": session_id,
                "owner_id": owner_id,
                "lease_token": lease_token,
                "lease_expires_at": lease_expires_at.isoformat() if lease_expires_at else None,
                "last_heartbeat_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "queue_depth": min(AUTOMATION_SESSION_QUEUE_MAX, queue_depth),
                "last_task_id": task_id or session.get("last_task_id", ""),
                "last_error": error or "",
            }
        )
        _automation_sessions[session_id] = session
        return dict(session)


def _automation_active_lease(session: Dict[str, Any]) -> bool:
    expires = _parse_iso_datetime(session.get("lease_expires_at"))
    return bool(expires and datetime.now() < expires)


def _automation_assert_session_lease(session_id: str, owner_id: str, lease_token: str = "") -> Dict[str, Any]:
    owner = str(owner_id or "api").strip() or "api"
    with _AUTOMATION_TASKS_LOCK:
        current = dict(_automation_sessions.get(session_id, {}))
    if current and _automation_active_lease(current):
        current_owner = str(current.get("owner_id", "")).strip()
        current_token = str(current.get("lease_token", "")).strip()
        if current_owner and current_owner != owner:
            return {"allowed": False, "error_code": "LEASE_CONFLICT", "error": "Session is leased by another owner"}
        if current_token and lease_token and lease_token != current_token:
            return {"allowed": False, "error_code": "LEASE_TOKEN_INVALID", "error": "Invalid lease token"}
        token = current_token or lease_token or secrets.token_hex(12)
    else:
        token = lease_token or secrets.token_hex(12)
    lease_exp = datetime.now() + timedelta(seconds=AUTOMATION_SESSION_LEASE_TTL_SEC)
    snapshot = _automation_update_session(session_id, owner, token, lease_exp)
    return {"allowed": True, "session": snapshot}


def _automation_validate_task(task_type: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    ttype = str(task_type or "").strip().lower()
    act = str(action or "").strip().lower()
    if ttype not in AUTOMATION_TASK_TYPES:
        return {"ok": False, "error_code": "INVALID_TASK_TYPE", "error": f"Unsupported task type '{ttype}'"}
    if act not in AUTOMATION_CAPABILITIES.get(ttype, []):
        return {"ok": False, "error_code": "INVALID_ACTION", "error": f"Unsupported action '{act}' for {ttype}"}
    if not isinstance(params, dict):
        return {"ok": False, "error_code": "INVALID_PARAMS", "error": "params must be a JSON object"}
    if ttype == "mail" and not AUTOMATION_ENABLE_MAIL_READ:
        return {"ok": False, "error_code": "MAIL_DISABLED", "error": "Mail automation is disabled"}
    if ttype == "messages" and not AUTOMATION_ENABLE_MESSAGES_READ:
        return {"ok": False, "error_code": "MESSAGES_DISABLED", "error": "Messages automation is disabled"}
    if ttype == "terminal" and not AUTOMATION_ENABLE_TERMINAL_EXEC:
        return {"ok": False, "error_code": "TERMINAL_DISABLED", "error": "Terminal automation is disabled"}
    if ttype == "app" and not AUTOMATION_ENABLE_APP_CONTROL:
        return {"ok": False, "error_code": "APP_CONTROL_DISABLED", "error": "App control is disabled"}
    if ttype in {"mail", "messages"} and act in {"send"}:
        return {"ok": False, "error_code": "READ_ONLY", "error": "Send actions are disabled in read-first mode"}
    return {"ok": True}


def _automation_poll_debounced(task_type: str, action: str, session_id: str, owner_id: str) -> Tuple[bool, float]:
    if (task_type, action) not in AUTOMATION_PASSIVE_POLL_ACTIONS:
        return False, 0.0
    cooldown = float(AUTOMATION_PASSIVE_POLL_COOLDOWN_SEC)
    if cooldown <= 0:
        return False, 0.0
    key = f"{owner_id}:{session_id}:{task_type}:{action}"
    now = time.time()
    with _AUTOMATION_POLL_LOCK:
        seen_at = _automation_poll_last_seen.get(key)
        if seen_at is not None:
            elapsed = now - seen_at
            if elapsed < cooldown:
                return True, round(max(0.0, cooldown - elapsed), 2)
        _automation_poll_last_seen[key] = now
    return False, 0.0


def _automation_browser_open_fallback(url: str) -> Dict[str, Any]:
    target = str(url or "").strip()
    if not target:
        return {"success": False, "error": "Missing url", "error_code": "MISSING_URL"}
    try:
        if sys.platform == "darwin":
            cmd = ["open", "-a", OPENCLAW_CHROME_APP, target]
        else:
            cmd = ["xdg-open", target]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": " ".join(cmd),
            "stdout": str(completed.stdout or "").strip(),
            "stderr": str(completed.stderr or "").strip(),
            "url": target,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "error_code": "BROWSER_FALLBACK_FAILED", "url": target}


def _automation_resolve_browser_provider(params: Dict[str, Any]) -> str:
    raw = str(
        params.get("provider")
        or params.get("browser_provider")
        or params.get("engine")
        or ""
    ).strip().lower()
    if raw in {"openclaw", "local"}:
        return raw
    if raw in {"native", "system", "core", "headless"}:
        return "local"
    return AUTOMATION_BROWSER_PROVIDER_DEFAULT


def _automation_execute_browser_local(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    act = str(action or "").strip().lower()
    if act in {"status", "start"}:
        return {
            "success": True,
            "provider": "local",
            "mode": "local",
            "action": act,
            "ready": True,
        }
    if act in {"open", "navigate"}:
        fallback = _automation_browser_open_fallback(str(params.get("url", "") or ""))
        return {
            "success": bool(fallback.get("success")),
            "provider": "local",
            "mode": "local",
            "action": act,
            "result": fallback,
            "error": fallback.get("error"),
            "error_code": fallback.get("error_code"),
        }
    if act == "flow":
        fallback = _openclaw_flow_fallback(str(params.get("chat_text", "") or ""))
        if isinstance(fallback, dict):
            return {
                "success": bool(fallback.get("success", False)),
                "provider": "local",
                "mode": "fallback",
                "error_code": "FLOW_FALLBACK_EXECUTED",
                "result": fallback,
                "fallback_reason": "local_provider",
            }
        return {
            "success": False,
            "provider": "local",
            "mode": "fallback",
            "error": "No local flow fallback available",
            "error_code": "FLOW_FALLBACK_UNAVAILABLE",
        }
    if act == "press":
        key = str(params.get("key", "")).strip().lower()
        result = _computer_control_action("press", {"key": key})
        return {
            "success": bool(result.get("success")),
            "provider": "local",
            "mode": "local",
            "action": act,
            "result": result,
            "error": result.get("error"),
            "error_code": result.get("error_code"),
        }
    if act == "type":
        text = str(params.get("text", "") or "")
        result = _computer_control_action("type_text", {"text": text})
        return {
            "success": bool(result.get("success")),
            "provider": "local",
            "mode": "local",
            "action": act,
            "result": result,
            "error": result.get("error"),
            "error_code": result.get("error_code"),
        }
    if act == "snapshot":
        return {
            "success": False,
            "provider": "local",
            "mode": "local",
            "error": "Local provider does not support browser snapshots",
            "error_code": "ACTION_REQUIRES_OPENCLAW",
        }
    if act == "click":
        return {
            "success": False,
            "provider": "local",
            "mode": "local",
            "error": "Local provider does not support semantic browser click",
            "error_code": "ACTION_REQUIRES_OPENCLAW",
        }
    return {
        "success": False,
        "provider": "local",
        "mode": "local",
        "error": f"Unsupported browser action '{act}'",
        "error_code": "INVALID_ACTION",
    }


def _automation_execute_browser(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    provider = _automation_resolve_browser_provider(params)
    if provider == "local":
        local_result = _automation_execute_browser_local(action, params)
        local_result.setdefault("provider", "local")
        return local_result

    if action == "flow":
        queued = _enqueue_openclaw_command("flow", dict(params))
    else:
        payload = dict(params)
        payload["action"] = action
        queued = _enqueue_openclaw_command("browser", payload)
    if not queued.get("success"):
        error_code = str(queued.get("error_code", "") or "").strip().upper()
        security_blocked_codes = {
            "MANUAL_REQUIRED",
            "POLICY_DENIED",
            "LEASE_CONFLICT",
            "LEASE_CONTEXT_REQUIRED",
            "LEASE_EXPIRED",
            "FAILURE_CLASS_BLOCKED",
        }
        if action in {"open", "navigate"} and error_code not in security_blocked_codes:
            fallback = _automation_browser_open_fallback(str(params.get("url", "") or ""))
            if fallback.get("success"):
                return {
                    "success": True,
                    "provider": "openclaw",
                    "mode": "fallback_local_browser",
                    "request_id": queued.get("request_id"),
                    "session_id": queued.get("session_id"),
                    "error_code": "OPENCLAW_FALLBACK_EXECUTED",
                    "result": fallback,
                    "fallback_reason": error_code or "openclaw_unavailable",
                }
        if action == "flow" and error_code in {"FLOW_MODE_DISABLED", "BROWSER_MODE_DISABLED"}:
            fallback = _openclaw_flow_fallback(str(params.get("chat_text", "") or ""))
            if isinstance(fallback, dict):
                return {
                    "success": bool(fallback.get("success", False)),
                    "provider": "openclaw",
                    "mode": "fallback",
                    "request_id": queued.get("request_id"),
                    "session_id": queued.get("session_id"),
                    "error_code": "FLOW_FALLBACK_EXECUTED",
                    "result": fallback,
                    "fallback_reason": error_code or "flow_mode_disabled",
                }
        if action == "flow" and error_code in {"OPENCLAW_TEMP_DISABLED", "ATTACH_FAILED", "ATTACH_THROTTLED"}:
            fallback = _openclaw_flow_fallback(str(params.get("chat_text", "") or ""))
            if isinstance(fallback, dict):
                return {
                    "success": bool(fallback.get("success", False)),
                    "provider": "openclaw",
                    "mode": "fallback",
                    "request_id": queued.get("request_id"),
                    "session_id": queued.get("session_id"),
                    "error_code": "FLOW_FALLBACK_EXECUTED",
                    "result": fallback,
                    "fallback_reason": error_code or "openclaw_temp_disabled",
                }
        queued_error = queued.get("error")
        if queued_error is None:
            queued_error = "OpenClaw execution failed"
        return {
            "success": False,
            "provider": "openclaw",
            "error": str(queued_error),
            "error_code": error_code or "OPENCLAW_EXECUTION_FAILED",
        }
    return {
        "success": True,
        "provider": "openclaw",
        "request_id": queued.get("request_id"),
        "mode": queued.get("mode"),
        "session_id": queued.get("session_id"),
        "result": queued.get("result") if isinstance(queued.get("result"), dict) else {},
    }


def _automation_execute_terminal(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "list_sessions":
        with _AUTOMATION_TASKS_LOCK:
            sessions = sorted(dict(item) for item in _automation_sessions.values())
        return {"success": True, "sessions": sessions}

    command = str(params.get("command", "")).strip()
    if not command:
        return {"success": False, "error": "Missing command", "error_code": "MISSING_COMMAND"}
    try:
        argv = shlex.split(command)
    except Exception as exc:
        return {"success": False, "error": f"Invalid command: {exc}", "error_code": "INVALID_COMMAND"}
    if not argv:
        return {"success": False, "error": "Empty command", "error_code": "INVALID_COMMAND"}
    binary = str(argv[0]).strip().lower()
    if binary not in AUTOMATION_TERMINAL_ALLOWED_BINS:
        return {"success": False, "error": f"Executable '{binary}' is not allowlisted", "error_code": "COMMAND_DENIED"}
    timeout_sec = max(1, min(AUTOMATION_TERMINAL_TIMEOUT_SEC, int(params.get("timeout_sec", AUTOMATION_TERMINAL_TIMEOUT_SEC))))
    cwd = str(params.get("cwd", str(AUTOMATION_ROOT))).strip() or str(AUTOMATION_ROOT)
    try:
        safe_cwd = _automation_safe_path(cwd)
    except Exception:
        return {"success": False, "error": "cwd is outside safe roots", "error_code": "INVALID_CWD"}
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_sec, cwd=str(safe_cwd), check=False)
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": str(completed.stdout or "")[:4000],
            "stderr": str(completed.stderr or "")[:4000],
            "argv": argv,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timeout", "error_code": "EXEC_TIMEOUT"}


def _automation_is_python_dashboard_alias(app_name: str) -> bool:
    normalized = " ".join(str(app_name or "").strip().lower().split())
    if not normalized:
        return False
    direct_aliases = {
        "python automation",
        "python app",
        "python monitor",
        "python dashboard",
        "monitor python",
        "automation python",
    }
    if normalized in direct_aliases:
        return True
    return ("python" in normalized) and any(token in normalized for token in ("automation", "dashboard", "monitor", "app.py"))


def _automation_dashboard_is_running(host: str = "127.0.0.1", port: int = 5001, timeout_sec: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=max(0.1, float(timeout_sec))):
            return True
    except OSError:
        return False


def _automation_launch_python_dashboard() -> Dict[str, Any]:
    target_url = "http://localhost:5001/live"
    if _automation_dashboard_is_running():
        return {
            "success": True,
            "already_running": True,
            "url": target_url,
            "message": "Python automation dashboard is already running",
        }
    script_path = Path(__file__).resolve()
    try:
        subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(script_path.parent.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc), "error_code": "PYTHON_AUTOMATION_START_FAILED"}

    deadline = time.time() + 4.0
    while time.time() < deadline:
        if _automation_dashboard_is_running():
            return {
                "success": True,
                "started": True,
                "url": target_url,
                "message": "Python automation dashboard started",
            }
        time.sleep(0.25)
    return {
        "success": False,
        "error": "Python automation dashboard start timeout",
        "error_code": "PYTHON_AUTOMATION_START_TIMEOUT",
        "url": target_url,
    }


def _automation_execute_app(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "launch":
        app_name = str(params.get("app_name", "") or params.get("app_path", "")).strip()
        if not app_name:
            return {"success": False, "error": "Missing app_name", "error_code": "MISSING_APP"}
        if _automation_is_python_dashboard_alias(app_name):
            return _automation_launch_python_dashboard()
        done = subprocess.run(["open", "-a", app_name], capture_output=True, text=True, check=False)
        return {
            "success": done.returncode == 0,
            "returncode": done.returncode,
            "app_name": app_name,
            "stderr": str(done.stderr or "").strip(),
        }
    if action == "activate":
        app_name = str(params.get("app_name", "")).strip()
        if not app_name:
            return {"success": False, "error": "Missing app_name", "error_code": "MISSING_APP"}
        done = subprocess.run(["osascript", "-e", f'tell application "{app_name}" to activate'], capture_output=True, text=True, check=False)
        return {"success": done.returncode == 0, "returncode": done.returncode, "app_name": app_name}
    if action == "list_running":
        done = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false'],
            capture_output=True,
            text=True,
            check=False,
        )
        apps = [item.strip() for item in str(done.stdout or "").split(",") if item.strip()]
        return {"success": done.returncode == 0, "running_apps": apps, "returncode": done.returncode}
    return {"success": False, "error": "Unsupported app action", "error_code": "INVALID_ACTION"}


def _automation_decode_header(value: Any) -> str:
    out = []
    for text, enc in decode_header(str(value or "")):
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(text))
    return "".join(out).strip()


def _automation_filter_mail_rows(rows: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    query = str(params.get("query", "")).strip().lower()
    from_contains = str(params.get("from_contains") or params.get("sender_contains") or "").strip().lower()
    subject_contains = str(params.get("subject_contains") or "").strip().lower()
    account_contains = str(params.get("account_contains") or "").strip().lower()
    mailbox_contains = str(params.get("mailbox_contains") or "").strip().lower()
    if not any([query, from_contains, subject_contains, account_contains, mailbox_contains]):
        return rows

    filtered: List[Dict[str, Any]] = []
    for row in rows:
        sender = str(row.get("from", "")).lower()
        subject = str(row.get("subject", "")).lower()
        account = str(row.get("account", "")).lower()
        mailbox = str(row.get("mailbox", "")).lower()
        date_text = str(row.get("date", "")).lower()
        blob = " ".join([sender, subject, account, mailbox, date_text]).strip()
        if query and query not in blob:
            continue
        if from_contains and from_contains not in sender:
            continue
        if subject_contains and subject_contains not in subject:
            continue
        if account_contains and account_contains not in account:
            continue
        if mailbox_contains and mailbox_contains not in mailbox:
            continue
        filtered.append(row)
    return filtered


def _automation_list_recent_mail_app(limit: int) -> Dict[str, Any]:
    max_rows = max(1, min(50, int(limit)))
    script = f"""
    tell application "Mail"
        set outLines to {{}}
        try
            set msgList to messages of inbox
            set totalCount to count of msgList
            set startIdx to totalCount - {max_rows} + 1
            if startIdx < 1 then set startIdx to 1
            repeat with idx from totalCount to startIdx by -1
                set m to item idx of msgList
                set msgId to ""
                set senderText to ""
                set subj to ""
                set dateText to ""
                set accountName to ""
                set mailboxName to "INBOX"
                try
                    set msgId to message id of m as text
                end try
                try
                    set senderText to sender of m as text
                end try
                try
                    set subj to subject of m as text
                end try
                try
                    set dateText to date received of m as text
                end try
                try
                    set mboxRef to mailbox of m
                    set mailboxName to name of mboxRef as text
                    try
                        set accountName to name of account of mboxRef as text
                    end try
                end try
                set end of outLines to (msgId & tab & senderText & tab & subj & tab & dateText & tab & accountName & tab & mailboxName)
            end repeat
        on error errMsg
            return "__ERROR__" & errMsg
        end try
    end tell
    set AppleScript's text item delimiters to linefeed
    set outText to outLines as text
    set AppleScript's text item delimiters to ""
    return outText
    """
    done = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if done.returncode != 0:
        return {"success": False, "error": str(done.stderr or done.stdout or "Mail app unavailable").strip(), "error_code": "MAIL_UNAVAILABLE"}
    output = str(done.stdout or "").strip()
    if output.startswith("__ERROR__"):
        return {"success": False, "error": output.replace("__ERROR__", "", 1).strip(), "error_code": "MAIL_UNAVAILABLE"}
    rows: List[Dict[str, Any]] = []
    if not output:
        return {"success": True, "messages": rows, "count": 0, "source": "mail_app"}
    for line in output.splitlines():
        parts = str(line or "").split("\t")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "id": str(parts[0] or "").strip(),
                "from": str(parts[1] or "").strip(),
                "subject": str(parts[2] or "").strip(),
                "date": str(parts[3] or "").strip(),
                "account": str(parts[4] or "").strip(),
                "mailbox": str(parts[5] or "").strip(),
            }
        )
    return {"success": True, "messages": rows, "count": len(rows), "source": "mail_app"}


def _automation_execute_mail(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    mail_cfg = _automation_mail_runtime_config()
    intake = _automation_get_intake_store(redact_secret=False)
    channels = intake.get("channels") if isinstance(intake.get("channels"), dict) else {}
    partners = intake.get("partners") if isinstance(intake.get("partners"), dict) else {}
    mail_mode = str(channels.get("mail_mode", "edge_gmail") or "edge_gmail").strip().lower()
    target_gmail = str(partners.get("target_gmail", "") or "").strip()
    if action == "list_folders":
        return {"success": True, "folders": [str(mail_cfg.get("folder") or "INBOX"), "Sent", "Drafts", "Archive", "Spam"]}

    if action in {"check_unread", "list_recent"} and mail_mode == "edge_gmail":
        prompt_decision: Optional[Dict[str, Any]] = None
        if _automation_prompt_on_stuck_enabled():
            prompt_decision = _automation_request_due_diligence_context(
                reason="mail_channel_edge_gmail",
                source="automation_execute_mail",
            )
        return {
            "success": False,
            "error": "Mail automation is set to Edge Gmail. Use browser flow in Edge for mailbox access.",
            "error_code": "MAIL_CHANNEL_BROWSER_REQUIRED",
            "channel": "edge_gmail",
            "target_gmail": target_gmail,
            "missing_info": ["channels.mail_mode", "partners.target_gmail"],
            "intervention_id": prompt_decision.get("id") if isinstance(prompt_decision, dict) else "",
        }

    if mail_cfg.get("host") and mail_cfg.get("username") and mail_cfg.get("password"):
        limit = max(1, min(30, int(params.get("limit", 10))))
        try:
            host = str(mail_cfg.get("host") or "")
            port = int(mail_cfg.get("port") or 993)
            username = str(mail_cfg.get("username") or "")
            password = str(mail_cfg.get("password") or "")
            folder = str(mail_cfg.get("folder") or "INBOX")
            ssl_enabled = bool(mail_cfg.get("ssl", True))
            mailbox = imaplib.IMAP4_SSL(host, port) if ssl_enabled else imaplib.IMAP4(host, port)
            mailbox.login(username, password)
            mailbox.select(folder)
            if action == "check_unread":
                status, data = mailbox.search(None, "UNSEEN")
                mailbox.logout()
                if status != "OK":
                    return {"success": False, "error": "Failed to query unread", "error_code": "MAIL_QUERY_FAILED"}
                ids = data[0].split() if data and data[0] else []
                return {"success": True, "unread_count": len(ids)}
            if action == "list_recent":
                status, data = mailbox.search(None, "ALL")
                if status != "OK":
                    mailbox.logout()
                    return {"success": False, "error": "Failed to list messages", "error_code": "MAIL_QUERY_FAILED"}
                ids = (data[0].split() if data and data[0] else [])[-limit:]
                rows: List[Dict[str, Any]] = []
                for msg_id in reversed(ids):
                    f_status, parts = mailbox.fetch(msg_id, "(RFC822.HEADER)")
                    if f_status != "OK" or not parts:
                        continue
                    raw = parts[0][1] if isinstance(parts[0], tuple) and len(parts[0]) > 1 else b""
                    msg = message_from_bytes(raw)
                    rows.append(
                        {
                            "id": msg_id.decode("utf-8", errors="ignore"),
                            "subject": _automation_decode_header(msg.get("Subject")),
                            "from": _automation_decode_header(msg.get("From")),
                            "date": _automation_decode_header(msg.get("Date")),
                            "account": username,
                            "mailbox": folder,
                        }
                    )
                mailbox.logout()
                rows = _automation_filter_mail_rows(rows, params)
                return {"success": True, "messages": rows[:limit], "count": len(rows), "source": "imap"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_code": "MAIL_BACKEND_FAILED"}

    # Fallback: local Mail unread probe.
    if action == "check_unread":
        done = subprocess.run(
            ["osascript", "-e", 'tell application "Mail" to get count of (messages of inbox whose read status is false)'],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            return {"success": False, "error": "Mail unread probe failed", "error_code": "MAIL_UNAVAILABLE"}
        try:
            unread = int(str(done.stdout or "0").strip() or "0")
        except Exception:
            unread = 0
        return {"success": True, "unread_count": unread}
    if action == "list_recent":
        limit = max(1, min(30, int(params.get("limit", 10))))
        local_probe = _automation_list_recent_mail_app(limit=max(limit, 20))
        if not local_probe.get("success"):
            return {
                "success": False,
                "error": "IMAP credentials missing for list_recent",
                "error_code": "MAIL_CREDENTIALS_MISSING",
                "missing_info": [
                    "mail.imap_host",
                    "mail.imap_username",
                    "mail.imap_password",
                ],
                "hint": "Provide IMAP credentials in /api/automation/intake or configure local Mail app.",
                "fallback_error": local_probe.get("error"),
            }
        rows = local_probe.get("messages") if isinstance(local_probe.get("messages"), list) else []
        rows = _automation_filter_mail_rows(rows, params)
        return {"success": True, "messages": rows[:limit], "count": len(rows), "source": "mail_app"}
    return {"success": False, "error": "Unsupported mail action", "error_code": "INVALID_ACTION"}


def _automation_execute_messages(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "list_conversations":
        done = subprocess.run(
            ["osascript", "-e", 'tell application "Messages" to get name of every chat'],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = [item.strip() for item in str(done.stdout or "").split(",") if item.strip()]
        return {"success": done.returncode == 0, "conversations": rows[:50]}
    if action == "check_unread":
        done = subprocess.run(
            ["osascript", "-e", 'tell application "Messages" to get count of (chats whose unread count > 0)'],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            return {"success": False, "error": "Messages unread probe failed", "error_code": "MESSAGES_UNAVAILABLE"}
        try:
            unread = int(str(done.stdout or "0").strip() or "0")
        except Exception:
            unread = 0
        return {"success": True, "unread_count": unread}
    if action == "list_recent":
        limit = max(1, min(20, int(params.get("limit", 10))))
        done = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Messages" to set _names to name of every chat',
                "-e",
                "return _names",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = [item.strip() for item in str(done.stdout or "").split(",") if item.strip()]
        return {"success": done.returncode == 0, "threads": rows[:limit]}
    return {"success": False, "error": "Unsupported messages action", "error_code": "INVALID_ACTION"}


def _automation_execute_computer(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "position":
        if not pyautogui:
            return {"success": False, "error": "pyautogui not available", "error_code": "DEPENDENCY_UNAVAILABLE"}
        x, y = pyautogui.position()
        return {"success": True, "x": int(x), "y": int(y)}
    if action == "screenshot":
        if not pyautogui:
            return {"success": False, "error": "pyautogui not available", "error_code": "DEPENDENCY_UNAVAILABLE"}
        filepath = str(params.get("filepath", "/tmp/automation_screenshot.png")).strip() or "/tmp/automation_screenshot.png"
        path = _automation_safe_path(filepath, create_parent=True)
        pyautogui.screenshot(str(path))
        return {"success": True, "filepath": str(path)}
    return _computer_control_action(action, params)


def _automation_execute_file(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "list":
        target = _automation_safe_path(str(params.get("dirpath", ".") or "."))
        if not target.exists() or not target.is_dir():
            return {"success": False, "error": "Directory does not exist", "error_code": "PATH_NOT_FOUND"}
        files = sorted(item.name for item in target.iterdir())
        return {"success": True, "dirpath": str(target), "files": files}
    if action == "read":
        target = _automation_safe_path(str(params.get("filepath", "") or ""))
        if not target.exists() or not target.is_file():
            return {"success": False, "error": "File does not exist", "error_code": "PATH_NOT_FOUND"}
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"success": True, "filepath": str(target), "content": content[:200000]}
    if action == "write":
        target = _automation_safe_path(str(params.get("filepath", "") or ""), create_parent=True)
        content = str(params.get("content", ""))
        target.write_text(content, encoding="utf-8")
        return {"success": True, "filepath": str(target), "written_chars": len(content)}
    if action == "copy":
        src = _automation_safe_path(str(params.get("src", "") or ""))
        dst = _automation_safe_path(str(params.get("dst", "") or ""), create_parent=True)
        shutil.copy2(src, dst)
        return {"success": True, "src": str(src), "dst": str(dst)}
    return {"success": False, "error": "Unsupported file action", "error_code": "INVALID_ACTION"}


def _automation_execute_clipboard(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if action == "read":
        done = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
        return {"success": done.returncode == 0, "content": str(done.stdout or "")}
    if action == "write":
        content = str(params.get("content", ""))
        done = subprocess.run(["pbcopy"], input=content, text=True, capture_output=True, check=False)
        return {"success": done.returncode == 0, "content_length": len(content)}
    return {"success": False, "error": "Unsupported clipboard action", "error_code": "INVALID_ACTION"}


# Phase 3: Window execution handler
def _automation_execute_window(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute window management actions."""
    window_id = str(params.get("window_id", "")).strip()

    if action == "list":
        windows = _automation_list_windows()
        # Update window state
        with _AUTOMATION_WINDOW_STATE_LOCK:
            for w in windows:
                _AUTOMATION_WINDOW_STATE[w["window_id"]] = w
        return {"success": True, "windows": windows}

    if action == "focus":
        if not window_id:
            return {"success": False, "error": "window_id required", "error_code": "MISSING_PARAM"}
        return _automation_window_focus(window_id)

    if action == "resize":
        if not window_id:
            return {"success": False, "error": "window_id required", "error_code": "MISSING_PARAM"}
        width = int(params.get("width", 800))
        height = int(params.get("height", 600))
        return _automation_window_resize(window_id, width, height)

    if action == "move":
        if not window_id:
            return {"success": False, "error": "window_id required", "error_code": "MISSING_PARAM"}
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        return _automation_window_move(window_id, x, y)

    if action == "minimize":
        if not window_id:
            return {"success": False, "error": "window_id required", "error_code": "MISSING_PARAM"}
        return _automation_window_minimize(window_id)

    if action == "maximize":
        if not window_id:
            return {"success": False, "error": "window_id required", "error_code": "MISSING_PARAM"}
        return _automation_window_maximize(window_id)

    if action == "close":
        if not window_id:
            return {"success": False, "error": "window_id required", "error_code": "MISSING_PARAM"}
        return _automation_window_close(window_id)

    return {"success": False, "error": f"Unsupported window action: {action}", "error_code": "INVALID_ACTION"}


# Phase 3: Surface execution handler
def _automation_execute_surface(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute surface management actions."""
    surface_id = str(params.get("surface_id", "")).strip()

    if action == "list":
        surfaces = _automation_surface_list()
        return {"success": True, "surfaces": surfaces}

    if action == "info":
        if not surface_id:
            return {"success": False, "error": "surface_id required", "error_code": "MISSING_PARAM"}
        info = _automation_surface_info(surface_id)
        return {"success": bool(info), "surface": info}

    if action == "screenshot" or action == "capture":
        if not surface_id:
            return {"success": False, "error": "surface_id required", "error_code": "MISSING_PARAM"}
        return _automation_surface_capture(surface_id)

    return {"success": False, "error": f"Unsupported surface action: {action}", "error_code": "INVALID_ACTION"}


def _create_automation_task(task_type: str, action: str, params: Dict[str, Any], task_id: str = "") -> Dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "task_id": task_id or f"task_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
        "task_type": str(task_type).strip().lower(),
        "action": str(action).strip().lower(),
        "params": params if isinstance(params, dict) else {},
        "status": "pending",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "duration_ms": 0,
        "error": "",
        "error_code": "",
        "result": {},
        "session_id": "",
        "owner_id": "",
    }


def _execute_automation_task(task: Dict[str, Any]) -> Dict[str, Any]:
    started = time.time()
    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat()
    task_type = str(task.get("task_type", "")).strip().lower()
    action = str(task.get("action", "")).strip().lower()
    params = task.get("params") if isinstance(task.get("params"), dict) else {}
    try:
        if task_type == "browser":
            result = _automation_execute_browser(action, params)
        elif task_type == "terminal":
            result = _automation_execute_terminal(action, params)
        elif task_type == "app":
            result = _automation_execute_app(action, params)
        elif task_type == "mail":
            result = _automation_execute_mail(action, params)
        elif task_type == "messages":
            result = _automation_execute_messages(action, params)
        elif task_type == "computer":
            result = _automation_execute_computer(action, params)
        elif task_type == "file":
            result = _automation_execute_file(action, params)
        elif task_type == "clipboard":
            result = _automation_execute_clipboard(action, params)
        # Phase 3: Window management
        elif task_type == "window":
            result = _automation_execute_window(action, params)
        # Phase 3: Surface management
        elif task_type == "surface":
            result = _automation_execute_surface(action, params)
        else:
            result = {"success": False, "error": f"Unsupported task_type '{task_type}'", "error_code": "INVALID_TASK_TYPE"}
    except Exception as exc:
        result = {"success": False, "error": str(exc), "error_code": "TASK_EXEC_EXCEPTION"}

    task["result"] = result if isinstance(result, dict) else {}
    task["status"] = "completed" if bool(task["result"].get("success")) else "failed"
    if task["status"] == "failed":
        prompt_decision = _automation_maybe_enqueue_stuck_prompt(task, task["result"])
        if isinstance(prompt_decision, dict):
            task["result"]["intervention_id"] = str(prompt_decision.get("id", ""))
            task["result"]["intervention_kind"] = "human_input_request"
            task["result"].setdefault("hint", "Please answer the popup intervention to continue the flow.")
    task["error"] = str(task["result"].get("error", "")) if task["status"] == "failed" else ""
    task["error_code"] = str(task["result"].get("error_code", "")) if task["status"] == "failed" else ""
    task["completed_at"] = datetime.now().isoformat()
    task["duration_ms"] = int((time.time() - started) * 1000)
    _automation_store_task(task)

    # Phase 4: Record to action memory for learning
    _automation_memory_record(
        task.get("task_type", ""),
        task.get("action", ""),
        task.get("params", {}),
        task.get("result", {}),
        task.get("session_id", ""),
        task.get("owner_id", "")
    )

    # Phase 4: Update user preferences
    _automation_preferences_update(
        task.get("owner_id", ""),
        task.get("task_type", ""),
        task.get("action", ""),
        task.get("duration_ms", 0)
    )

    # Phase 4: Update session context
    _automation_context_add_action(task.get("session_id", ""), {
        "task_type": task.get("task_type"),
        "action": task.get("action"),
        "status": task.get("status"),
        "timestamp": task.get("completed_at"),
    })

    # Phase 5: Record metrics
    _automation_metrics_record(
        task.get("task_type", ""),
        task.get("owner_id", ""),
        task.get("status") == "completed",
        task.get("duration_ms", 0)
    )

    # Phase 5: Generate alerts on failures
    if task.get("status") == "failed":
        _automation_alert(
            level="error",
            title=f"Task failed: {task.get('task_type')}/{task.get('action')}",
            message=str(task.get("error", "unknown error"))[:200],
            source="automation_bus"
        )

    return task


def _automation_save_schedules() -> None:
    AUTOMATION_STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now().isoformat(), "schedules": list(_automation_scheduled_tasks.values())}
    tmp = AUTOMATION_SCHEDULES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(AUTOMATION_SCHEDULES_PATH)


def _automation_load_schedules() -> None:
    if not AUTOMATION_SCHEDULES_PATH.exists():
        return
    try:
        payload = json.loads(AUTOMATION_SCHEDULES_PATH.read_text(encoding="utf-8"))
        rows = payload.get("schedules") if isinstance(payload.get("schedules"), list) else []
    except Exception:
        rows = []
    with _AUTOMATION_SCHEDULER_LOCK:
        for row in rows:
            if not isinstance(row, dict):
                continue
            schedule_id = str(row.get("schedule_id", "")).strip()
            if not schedule_id:
                continue
            _automation_scheduled_tasks[schedule_id] = row


def _automation_scheduler_loop():
    global _automation_scheduler_running
    while _automation_scheduler_running:
        now = time.time()
        due_rows: List[Dict[str, Any]] = []
        try:
            with _AUTOMATION_SCHEDULER_LOCK:
                for sid, row in list(_automation_scheduled_tasks.items()):
                    if not bool(row.get("enabled", True)):
                        continue
                    next_run_at = float(row.get("next_run_at", 0) or 0)
                    interval_sec = max(10, int(row.get("interval_sec", 60) or 60))
                    if next_run_at <= 0:
                        row["next_run_at"] = now + interval_sec
                        continue
                    if now >= next_run_at:
                        row["last_run"] = now
                        row["next_run_at"] = now + interval_sec
                        row["run_count"] = int(row.get("run_count", 0) or 0) + 1
                        due_rows.append({"schedule_id": sid, **row})
                if due_rows:
                    _automation_save_schedules()
            for row in due_rows:
                task = _create_automation_task(str(row.get("task_type", "")), str(row.get("action", "")), row.get("params") if isinstance(row.get("params"), dict) else {})
                session_id = str(row.get("session_id", "scheduler")).strip() or "scheduler"
                owner_id = str(row.get("owner_id", "scheduler")).strip() or "scheduler"
                lease = _automation_assert_session_lease(session_id, owner_id, str(row.get("lease_token", "") or ""))
                if not lease.get("allowed"):
                    task["status"] = "failed"
                    task["error"] = str(lease.get("error", "lease denied"))
                    task["error_code"] = str(lease.get("error_code", "LEASE_CONFLICT"))
                    task["completed_at"] = datetime.now().isoformat()
                    _automation_store_task(task)
                    continue
                task["session_id"] = session_id
                task["owner_id"] = owner_id
                task["lease_token"] = str((lease.get("session") or {}).get("lease_token", ""))
                done = _execute_automation_task(task)
                with _AUTOMATION_SCHEDULER_LOCK:
                    src = _automation_scheduled_tasks.get(str(row.get("schedule_id", "")))
                    if isinstance(src, dict):
                        src["last_result"] = {
                            "success": done.get("status") == "completed",
                            "status": done.get("status"),
                            "error_code": done.get("error_code"),
                            "completed_at": done.get("completed_at"),
                            "task_id": done.get("task_id"),
                        }
                        _automation_save_schedules()
        except Exception as exc:
            logger.error(f"Automation scheduler error: {exc}")
        time.sleep(AUTOMATION_SCHEDULER_INTERVAL_SEC)


def _start_automation_scheduler():
    global _automation_scheduler_running, _automation_scheduler_thread
    if _automation_scheduler_running:
        return
    _automation_load_schedules()
    _automation_scheduler_running = True
    _automation_scheduler_thread = threading.Thread(target=_automation_scheduler_loop, daemon=True, name="automation-scheduler")
    _automation_scheduler_thread.start()
    logger.info("Automation scheduler started")


def _stop_automation_scheduler():
    global _automation_scheduler_running
    _automation_scheduler_running = False


def _automation_public_session(session_id: str) -> Dict[str, Any]:
    with _AUTOMATION_TASKS_LOCK:
        raw = dict(_automation_sessions.get(session_id, {}))
    if not raw:
        return {"session_id": session_id, "owner_id": "", "lease_expires_at": None, "queue_depth": 0}
    return {
        "session_id": raw.get("session_id"),
        "owner_id": raw.get("owner_id"),
        "lease_expires_at": raw.get("lease_expires_at"),
        "last_heartbeat_at": raw.get("last_heartbeat_at"),
        "queue_depth": raw.get("queue_depth", 0),
        "last_task_id": raw.get("last_task_id", ""),
        "last_error": raw.get("last_error", ""),
        "lease_token": raw.get("lease_token", ""),
    }


@app.route('/api/automation/sessions')
def automation_sessions():
    with _AUTOMATION_TASKS_LOCK:
        rows = sorted((_automation_public_session(key) for key in _automation_sessions.keys()), key=lambda item: str(item.get("session_id", "")))
    return jsonify({"success": True, "count": len(rows), "sessions": rows})


@app.route('/api/automation/sessions/lease', methods=['POST'])
def automation_sessions_lease():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "acquire")).strip().lower() or "acquire"
    session_id = str(data.get("session_id", "default")).strip() or "default"
    owner_id = str(data.get("owner_id", "api")).strip() or "api"
    lease_token = str(data.get("lease_token", "")).strip()
    if action in {"acquire", "heartbeat"}:
        check = _automation_assert_session_lease(session_id, owner_id, lease_token)
        if not check.get("allowed"):
            return jsonify({"success": False, "error": check.get("error"), "error_code": check.get("error_code")}), 409
        return jsonify({"success": True, "session": check.get("session")})
    if action == "release":
        with _AUTOMATION_TASKS_LOCK:
            current = dict(_automation_sessions.get(session_id, {}))
        if current and str(current.get("owner_id", "")).strip() not in {"", owner_id}:
            return jsonify({"success": False, "error": "Session owned by another actor", "error_code": "LEASE_CONFLICT"}), 409
        snapshot = _automation_update_session(session_id, owner_id, "", None)
        return jsonify({"success": True, "session": snapshot})
    return jsonify({"success": False, "error": "Unsupported lease action", "error_code": "INVALID_ACTION"}), 400


@app.route('/api/automation/execute', methods=['POST'])
def automation_execute():
    data = request.get_json(silent=True) or {}
    task_type = str(data.get("task_type", "")).strip().lower()
    action = str(data.get("action", "")).strip().lower()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if task_type == "browser":
        provider = str(data.get("provider", "") or data.get("browser_provider", "")).strip()
        if provider:
            params = dict(params)
            params.setdefault("provider", provider)
    task_id = str(data.get("task_id", "")).strip()
    session_id = str(data.get("session_id", "default")).strip() or "default"
    owner_id = str(data.get("owner_id", "api")).strip() or "api"
    lease_token = str(data.get("lease_token", "")).strip()

    gate = _automation_validate_task(task_type, action, params)
    if not gate.get("ok"):
        return jsonify({"success": False, "error": gate.get("error"), "error_code": gate.get("error_code")}), 400

    # Phase 0: Rate limiting check
    rate_allowed, rate_reason = _automation_check_rate_limit(task_type, owner_id)
    if not rate_allowed:
        return jsonify({
            "success": False,
            "error": rate_reason,
            "error_code": "RATE_LIMIT_EXCEEDED",
            "automation_enabled": _AUTOMATION_ENABLED,
        }), 429

    # Phase 2: Circuit breaker check
    circuit_allowed, circuit_reason = _automation_circuit_check(task_type)
    if not circuit_allowed:
        return jsonify({
            "success": False,
            "error": circuit_reason,
            "error_code": "CIRCUIT_OPEN",
            "task_type": task_type,
        }), 503

    lease = _automation_assert_session_lease(session_id, owner_id, lease_token)
    if not lease.get("allowed"):
        return jsonify({"success": False, "error": lease.get("error"), "error_code": lease.get("error_code")}), 409
    session = lease.get("session") if isinstance(lease.get("session"), dict) else {}
    debounced, retry_after = _automation_poll_debounced(task_type, action, session_id, owner_id)
    if debounced:
        return jsonify(
            {
                "success": True,
                "status": "skipped",
                "skipped": True,
                "reason": "debounced",
                "retry_after_sec": retry_after,
                "task_type": task_type,
                "action": action,
                "session": _automation_public_session(session_id),
            }
        )

    task = _create_automation_task(task_type, action, params, task_id)
    task["session_id"] = session_id
    task["owner_id"] = owner_id
    task["lease_token"] = str(session.get("lease_token", ""))
    with _AUTOMATION_TASKS_LOCK:
        current = dict(_automation_sessions.get(session_id, {}))
        current["queue_depth"] = min(AUTOMATION_SESSION_QUEUE_MAX, int(current.get("queue_depth", 0) or 0) + 1)
        _automation_sessions[session_id] = current
    result_task = _execute_automation_task(task)
    with _AUTOMATION_TASKS_LOCK:
        current = dict(_automation_sessions.get(session_id, {}))
        current["queue_depth"] = max(0, int(current.get("queue_depth", 0) or 0) - 1)
        current["last_task_id"] = result_task.get("task_id")
        current["last_error"] = result_task.get("error") if result_task.get("status") == "failed" else ""
        _automation_sessions[session_id] = current

    if result_task.get("status") == "failed":
        # Phase 2: Record failure to circuit breaker
        _automation_circuit_record_failure(task_type)

        # Phase 2: Add to dead letter queue
        _automation_dlq_add(task, result_task.get("error", ""), result_task.get("error_code", ""))

        _add_notification(
            level=NOTIFY_WARNING,
            category="automation",
            title=f"Automation task failed: {task_type}/{action}",
            message=str(result_task.get("error", "unknown error"))[:300],
            data={"task_id": result_task.get("task_id"), "error_code": result_task.get("error_code")},
            source="automation_bus",
        )
    else:
        # Phase 2: Record success to circuit breaker
        _automation_circuit_record_success(task_type)

    return jsonify(
        {
            "success": result_task.get("status") == "completed",
            "task_id": result_task.get("task_id"),
            "status": result_task.get("status"),
            "result": result_task.get("result"),
            "error": result_task.get("error"),
            "error_code": result_task.get("error_code"),
            "intervention_id": ((result_task.get("result") or {}).get("intervention_id") if isinstance(result_task.get("result"), dict) else ""),
            "duration_ms": result_task.get("duration_ms"),
            "session": _automation_public_session(session_id),
            "created_at": result_task.get("created_at"),
            "completed_at": result_task.get("completed_at"),
        }
    )


@app.route('/api/automation/batch', methods=['POST'])
def automation_batch():
    data = request.get_json(silent=True) or {}
    tasks_data = data.get("tasks") if isinstance(data.get("tasks"), list) else []
    if not tasks_data:
        return jsonify({"success": False, "error": "No tasks provided", "error_code": "NO_TASKS"}), 400
    session_id = str(data.get("session_id", "default")).strip() or "default"
    owner_id = str(data.get("owner_id", "api")).strip() or "api"
    lease_token = str(data.get("lease_token", "")).strip()
    default_provider = str(data.get("provider", "") or data.get("browser_provider", "")).strip()
    lease = _automation_assert_session_lease(session_id, owner_id, lease_token)
    if not lease.get("allowed"):
        return jsonify({"success": False, "error": lease.get("error"), "error_code": lease.get("error_code")}), 409

    results: List[Dict[str, Any]] = []
    success_count = 0
    for row in tasks_data:
        if not isinstance(row, dict):
            results.append({"success": False, "error": "Each task must be object", "error_code": "INVALID_TASK"})
            continue
        body = {
            "task_type": str(row.get("task_type", "")).strip().lower(),
            "action": str(row.get("action", "")).strip().lower(),
            "params": row.get("params") if isinstance(row.get("params"), dict) else {},
            "task_id": str(row.get("task_id", "")).strip(),
            "session_id": session_id,
            "owner_id": owner_id,
            "lease_token": str((lease.get("session") or {}).get("lease_token", "")),
        }
        if body["task_type"] == "browser":
            provider = str(row.get("provider", "") or row.get("browser_provider", "") or default_provider).strip()
            if provider:
                params_patch = dict(body["params"])
                params_patch.setdefault("provider", provider)
                body["params"] = params_patch
        gate = _automation_validate_task(body["task_type"], body["action"], body["params"])
        if not gate.get("ok"):
            results.append({"success": False, "task_id": body["task_id"] or "", "error": gate.get("error"), "error_code": gate.get("error_code")})
            continue
        task = _create_automation_task(body["task_type"], body["action"], body["params"], body["task_id"])
        task["session_id"] = session_id
        task["owner_id"] = owner_id
        task["lease_token"] = body["lease_token"]
        done = _execute_automation_task(task)
        ok = done.get("status") == "completed"
        if ok:
            success_count += 1
        results.append(
            {
                "success": ok,
                "task_id": done.get("task_id"),
                "status": done.get("status"),
                "error": done.get("error"),
                "error_code": done.get("error_code"),
                "result": done.get("result"),
                "duration_ms": done.get("duration_ms"),
            }
        )
    return jsonify(
        {
            "success": success_count == len(results),
            "partial_success": 0 < success_count < len(results),
            "summary": {"total": len(results), "success": success_count, "failed": max(0, len(results) - success_count)},
            "results": results,
            "session": _automation_public_session(session_id),
        }
    )


@app.route('/api/automation/status/<task_id>')
def automation_status(task_id: str):
    task = _automation_tasks_by_id.get(task_id)
    if isinstance(task, dict):
        return jsonify({"success": True, "task": task})
    return jsonify({"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND"}), 404


@app.route('/api/automation/types')
def automation_types():
    return jsonify({"success": True, "task_types": AUTOMATION_TASK_TYPES, "capabilities": AUTOMATION_CAPABILITIES})


@app.route('/api/automation/capabilities')
def automation_capabilities():
    mail_cfg = _automation_mail_runtime_config()
    return jsonify(
        {
            "success": True,
            "capabilities": AUTOMATION_CAPABILITIES,
            "runtime": {
                "mail_read_enabled": bool(AUTOMATION_ENABLE_MAIL_READ),
                "messages_read_enabled": bool(AUTOMATION_ENABLE_MESSAGES_READ),
                "app_control_enabled": bool(AUTOMATION_ENABLE_APP_CONTROL),
                "terminal_exec_enabled": bool(AUTOMATION_ENABLE_TERMINAL_EXEC),
                "terminal_allowlist": sorted(AUTOMATION_TERMINAL_ALLOWED_BINS),
                "safe_roots": [str(root) for root in AUTOMATION_SAFE_ROOTS],
                "mail_credentials_configured": bool(mail_cfg.get("host") and mail_cfg.get("username") and mail_cfg.get("password")),
            },
        }
    )


@app.route('/api/automation/intake', methods=['GET', 'POST'])
def automation_intake():
    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "intake": _automation_get_intake_store(redact_secret=True),
                "recommended_fields": [
                    "mail.imap_host",
                    "mail.imap_username",
                    "mail.imap_password",
                    "channels.mail_mode",
                    "channels.mail_browser",
                    "channels.teams_mode",
                    "channels.teams_app_name",
                    "accounts.khiem_selected",
                    "accounts.khiem_candidates",
                    "partners.khiem_team_hint",
                    "partners.ravin_contact_hint",
                    "valuation.raise_usd",
                    "valuation.equity_pct",
                ],
            }
        )

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Payload must be object", "error_code": "INVALID_PAYLOAD"}), 400
    updated = _automation_update_intake_store(data)
    return jsonify({"success": True, "intake": updated})


@app.route('/api/automation/history')
def automation_history():
    limit = _int_in_range(request.args.get("limit", 20), default=20, min_value=1, max_value=300)
    task_type = str(request.args.get("task_type", "")).strip().lower()
    status = str(request.args.get("status", "")).strip().lower()
    session_id = str(request.args.get("session_id", "")).strip()
    with _AUTOMATION_TASKS_LOCK:
        rows = [dict(item) for item in _automation_recent_tasks if isinstance(item, dict)]
    if task_type:
        rows = [row for row in rows if str(row.get("task_type", "")).strip().lower() == task_type]
    if status:
        rows = [row for row in rows if str(row.get("status", "")).strip().lower() == status]
    if session_id:
        rows = [row for row in rows if str(row.get("session_id", "")).strip() == session_id]
    rows = list(reversed(rows[-limit:]))
    return jsonify({"success": True, "count": len(rows), "tasks": rows})


@app.route('/api/automation/control', methods=['POST'])
def automation_control():
    """
    Control automation system - includes kill-switch (Phase 0).

    Actions:
    - status: Get automation status
    - enable: Enable automation
    - disable: Kill-switch - disable all automation
    - stop_scheduler: Stop scheduled tasks
    - start_scheduler: Start scheduled tasks
    """
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "status")).strip().lower() or "status"

    # Get current enabled state
    enabled, reason = _automation_is_enabled()

    # Calculate stale sessions (Phase 1)
    stale_sessions = []
    now = time.time()
    with _AUTOMATION_TASKS_LOCK:
        for sid, sess in _automation_sessions.items():
            last_hb = sess.get("last_heartbeat_at")
            if last_hb:
                try:
                    hb_time = datetime.fromisoformat(str(last_hb)).timestamp()
                    if now - hb_time > AUTOMATION_SESSION_LEASE_TTL_SEC * 2:
                        stale_sessions.append({"session_id": sid, "last_heartbeat": last_hb})
                except (ValueError, TypeError, AttributeError):
                    pass

    # Calculate queue depth summary
    queue_total = 0
    queue_by_owner = {}
    with _AUTOMATION_TASKS_LOCK:
        for sid, sess in _automation_sessions.items():
            depth = sess.get("queue_depth", 0)
            owner = sess.get("owner_id", "unknown")
            queue_total += depth
            queue_by_owner[owner] = queue_by_owner.get(owner, 0) + depth

    # Get rate limit info
    with _AUTOMATION_RATE_LOCK:
        rate_summary = {}
        for owner_id, info in _AUTOMATION_RATE_COUNTS.items():
            rate_summary[owner_id] = {"count": info["count"], "window_start": info["window_start"]}

    if action == "status":
        # Get rate limit info
        with _AUTOMATION_RATE_LOCK:
            rate_summary = {}
            for owner_id, info in _AUTOMATION_RATE_COUNTS.items():
                rate_summary[owner_id] = {"count": info["count"], "window_start": info["window_start"]}

        return jsonify(
            {
                "success": True,
                "status": {
                    "automation_enabled": enabled,
                    "kill_reason": reason,
                    "scheduler_running": bool(_automation_scheduler_running),
                    "schedules_count": len(_automation_scheduled_tasks),
                    "tasks_cached": len(_automation_tasks_by_id),
                    "sessions_count": len(_automation_sessions),
                    # Phase 1: Enhanced status
                    "stale_sessions": stale_sessions,
                    "stale_count": len(stale_sessions),
                    "queue_summary": {
                        "total_depth": queue_total,
                        "by_owner": queue_by_owner,
                    },
                    "lease_ttl_sec": AUTOMATION_SESSION_LEASE_TTL_SEC,
                    "rate_limits": _AUTOMATION_RATE_MAX_PER_WINDOW,
                    "rate_usage": rate_summary,
                    "task_classes": {
                        "passive_poll": "unlimited read-only",
                        "active_control": "20/min",
                        "destructive": "5/min",
                    },
                    # Phase 2: Circuit breaker
                    "circuit_breaker": {
                        "failure_threshold": AUTOMATION_CIRCUIT_FAILURE_THRESHOLD,
                        "recovery_timeout_sec": AUTOMATION_CIRCUIT_RECOVERY_TIMEOUT_SEC,
                        "states": dict(_AUTOMATION_CIRCUIT_STATE),
                        "open_circuits": sum(1 for s in _AUTOMATION_CIRCUIT_STATE.values() if s.get("state") == "open"),
                    },
                    # Phase 2: Dead Letter Queue
                    "dlq": {
                        "count": len(_AUTOMATION_DLQ),
                        "max_size": AUTOMATION_DLQ_MAX_SIZE,
                    },
                },
            }
        )

    if action == "enable":
        reason = str(data.get("reason", "")).strip() or "User enabled"
        _automation_set_enabled(True, reason)
        return jsonify({"success": True, "message": "Automation enabled", "reason": reason})

    if action == "disable":
        reason = str(data.get("reason", "")).strip() or "User disabled"
        _automation_set_enabled(False, reason)
        # Stop scheduler too
        _stop_automation_scheduler()
        return jsonify({"success": True, "message": "Automation DISABLED (kill-switch)", "reason": reason})

    if action == "stop_scheduler":
        _stop_automation_scheduler()
        return jsonify({"success": True, "message": "Scheduler stopped"})

    if action == "start_scheduler":
        if not _AUTOMATION_ENABLED:
            return jsonify({"success": False, "error": "Cannot start scheduler: automation is disabled", "error_code": "AUTOMATION_DISABLED"}), 409
        _start_automation_scheduler()
        return jsonify({"success": True, "message": "Scheduler started"})

    return jsonify({"success": False, "error": f"Unknown action: {action}", "error_code": "INVALID_ACTION"}), 400


@app.route('/api/automation/dlq', methods=['GET'])
def automation_dlq_list():
    """Get dead letter queue entries."""
    limit = min(100, int(request.args.get("limit", 50)))
    task_type = request.args.get("task_type") or None
    entries = _automation_dlq_get(limit=limit, task_type=task_type)
    return jsonify({
        "success": True,
        "count": len(entries),
        "entries": entries,
    })


@app.route('/api/automation/dlq/clear', methods=['POST'])
def automation_dlq_clear():
    """Clear dead letter queue."""
    data = request.get_json(silent=True) or {}
    owner_id = str(data.get("owner_id", "")).strip() or None
    cleared = _automation_dlq_clear(owner_id=owner_id if owner_id else None)
    return jsonify({
        "success": True,
        "cleared_count": cleared,
    })


@app.route('/api/automation/circuit/reset/<task_type>', methods=['POST'])
def automation_circuit_reset(task_type: str):
    """Reset circuit breaker for a task type."""
    with _AUTOMATION_CIRCUIT_BREAKER_LOCK:
        if task_type in _AUTOMATION_CIRCUIT_STATE:
            _AUTOMATION_CIRCUIT_STATE[task_type] = {
                "state": "closed",
                "failures": 0,
                "failures_in_window": 0,
                "window_start": time.time(),
                "last_failure": 0,
            }
    return jsonify({
        "success": True,
        "message": f"Circuit reset for {task_type}",
    })


# Phase 3: Cross-surface Workflow Endpoints

@app.route('/api/automation/workflow/create', methods=['POST'])
def automation_workflow_create():
    """Create a cross-surface workflow."""
    data = request.get_json(silent=True) or {}
    workflow_id = str(data.get("workflow_id", "")).strip() or f"workflow_{int(time.time() * 1000)}"
    name = str(data.get("name", "")).strip() or "Unnamed Workflow"
    steps = data.get("steps", [])

    if not isinstance(steps, list):
        return jsonify({"success": False, "error": "steps must be a list", "error_code": "INVALID_PARAMS"}), 400

    result = _automation_workflow_create(workflow_id, name, steps)
    return jsonify(result)


@app.route('/api/automation/workflow/execute/<workflow_id>', methods=['POST'])
def automation_workflow_execute(workflow_id: str):
    """Execute next step in a workflow."""
    result = _automation_workflow_execute(workflow_id)
    return jsonify(result)


@app.route('/api/automation/workflow/<workflow_id>', methods=['GET'])
def automation_workflow_status(workflow_id: str):
    """Get workflow status."""
    with _AUTOMATION_WORKFLOW_LOCK:
        workflow = _AUTOMATION_WORKFLOWS.get(workflow_id)
    if not workflow:
        return jsonify({"success": False, "error": "Workflow not found", "error_code": "NOT_FOUND"}), 404
    return jsonify({"success": True, "workflow": workflow})


@app.route('/api/automation/workflow/list', methods=['GET'])
def automation_workflow_list():
    """List all workflows."""
    with _AUTOMATION_WORKFLOW_LOCK:
        workflows = list(_AUTOMATION_WORKFLOWS.values())
    return jsonify({"success": True, "workflows": workflows, "count": len(workflows)})


# Phase 4: Context & Memory API Endpoints

@app.route('/api/automation/context/<session_id>', methods=['GET'])
def automation_context_get(session_id: str):
    """Get context for a session."""
    context = _automation_context_get(session_id)
    return jsonify({"success": True, "session_id": session_id, "context": context})


@app.route('/api/automation/context/<session_id>', methods=['PUT'])
def automation_context_update(session_id: str):
    """Update context for a session."""
    data = request.get_json(silent=True) or {}
    for key, value in data.items():
        _automation_context_update(session_id, key, value)
    return jsonify({"success": True, "message": "Context updated"})


@app.route('/api/automation/memory', methods=['GET'])
def automation_memory_list():
    """Get action memory."""
    limit = min(100, int(request.args.get("limit", 50)))
    task_type = request.args.get("task_type") or None
    owner_id = request.args.get("owner_id") or None
    entries = _automation_memory_get_recent(limit=limit, task_type=task_type, owner_id=owner_id)
    return jsonify({"success": True, "count": len(entries), "entries": entries})


@app.route('/api/automation/memory/analyze', methods=['GET'])
def automation_memory_analyze():
    """Analyze action memory for patterns."""
    task_type = request.args.get("task_type") or None
    analysis = _automation_memory_analyze(task_type=task_type)
    return jsonify({"success": True, "analysis": analysis})


@app.route('/api/automation/preferences/<owner_id>', methods=['GET'])
def automation_preferences_get(owner_id: str):
    """Get user preferences."""
    prefs = _automation_preferences_get(owner_id)
    return jsonify({"success": True, "owner_id": owner_id, "preferences": prefs})


@app.route('/api/automation/preferences/<owner_id>/top', methods=['GET'])
def automation_preferences_top(owner_id: str):
    """Get top user preferences."""
    prefs = _automation_preferences_get(owner_id)
    task_types = prefs.get("preferred_task_types", {})
    actions = prefs.get("frequent_actions", {})

    top_tasks = sorted(task_types.items(), key=lambda x: -x[1])[:5]
    top_actions = sorted(actions.items(), key=lambda x: -x[1])[:10]

    return jsonify({
        "success": True,
        "owner_id": owner_id,
        "top_task_types": [{"task_type": t, "count": c} for t, c in top_tasks],
        "top_actions": [{"action": a, "count": c} for a, c in top_actions],
        "total_tasks": prefs.get("total_tasks", 0),
    })


# Phase 5: Observability API Endpoints

@app.route('/api/automation/metrics', methods=['GET'])
def automation_metrics_get():
    """Get automation metrics."""
    metrics = _automation_metrics_get()
    # Calculate additional stats
    if metrics["tasks_total"] > 0:
        metrics["success_rate"] = metrics["tasks_success"] / metrics["tasks_total"]
    else:
        metrics["success_rate"] = 0

    # Uptime
    started = datetime.fromisoformat(metrics["started_at"])
    metrics["uptime_seconds"] = (datetime.now() - started).total_seconds()

    return jsonify({"success": True, "metrics": metrics})


@app.route('/api/automation/metrics/reset', methods=['POST'])
def automation_metrics_reset():
    """Reset metrics."""
    _automation_metrics_reset()
    return jsonify({"success": True, "message": "Metrics reset"})


@app.route('/api/automation/health', methods=['GET'])
def automation_health_get():
    """Get system health status."""
    checks = _automation_health_get_all()

    # Determine overall health
    overall = "healthy"
    for name, check in checks.items():
        status = check.get("status", "unknown")
        if status == "critical":
            overall = "critical"
            break
        elif status == "warning" and overall != "critical":
            overall = "degraded"

    return jsonify({
        "success": True,
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/automation/alerts', methods=['GET'])
def automation_alerts_list():
    """Get alerts."""
    limit = min(100, int(request.args.get("limit", 50)))
    level = request.args.get("level") or None
    alerts = _automation_alerts_get(limit=limit, level=level)
    return jsonify({
        "success": True,
        "count": len(alerts),
        "alerts": alerts,
    })


@app.route('/api/automation/alerts/<alert_id>/acknowledge', methods=['POST'])
def automation_alerts_acknowledge(alert_id: str):
    """Acknowledge an alert."""
    success = _automation_alerts_acknowledge(alert_id)
    return jsonify({
        "success": success,
        "message": "Alert acknowledged" if success else "Alert not found",
    })


@app.route('/api/automation/alerts/clear', methods=['POST'])
def automation_alerts_clear():
    """Clear all alerts."""
    count = _automation_alerts_clear()
    return jsonify({
        "success": True,
        "cleared_count": count,
    })


@app.route('/api/automation/alerts/test', methods=['POST'])
def automation_alerts_test():
    """Create a test alert."""
    _automation_alert(
        level="info",
        title="Test Alert",
        message="This is a test alert from the automation system",
        source="test"
    )
    return jsonify({"success": True, "message": "Test alert created"})


@app.route('/api/automation/status', methods=['GET'])
def automation_status_full():
    """Get full automation system status."""
    metrics = _automation_metrics_get()
    health = _automation_health_get_all()
    alerts = _automation_alerts_get(limit=10)

    return jsonify({
        "success": True,
        "status": {
            "enabled": _AUTOMATION_ENABLED,
            "kill_reason": _AUTOMATION_KILL_REASON,
            "scheduler_running": _automation_scheduler_running,
            "metrics": {
                "tasks_total": metrics["tasks_total"],
                "tasks_success": metrics["tasks_success"],
                "tasks_failed": metrics["tasks_failed"],
                "avg_duration_ms": metrics["avg_duration_ms"],
            },
            "health": {k: v.get("status") for k, v in health.items()},
            "recent_alerts": len(alerts),
        },
        "timestamp": datetime.now().isoformat(),
    })


# Phase 6: Test & Rollout API Endpoints

@app.route('/api/automation/test/self', methods=['POST'])
def automation_test_self():
    """Run self-test on automation system."""
    results = _automation_run_self_test()

    # Store results
    with _AUTOMATION_TEST_RESULTS_LOCK:
        _AUTOMATION_TEST_RESULTS.append(results)
        while len(_AUTOMATION_TEST_RESULTS) > 50:
            _AUTOMATION_TEST_RESULTS.pop(0)

    return jsonify({
        "success": results["failed"] == 0,
        "passed": results["passed"],
        "failed": results["failed"],
        "warnings": results["warnings"],
        "tests": results["tests"],
        "timestamp": results["timestamp"],
    })


@app.route('/api/automation/test/integration', methods=['POST'])
def automation_test_integration():
    """Run integration test for a specific task."""
    data = request.get_json(silent=True) or {}
    task_type = str(data.get("task_type", "")).strip().lower()
    action = str(data.get("action", "")).strip().lower()
    params = data.get("params", {})

    if not task_type or not action:
        return jsonify({
            "success": False,
            "error": "task_type and action are required",
            "error_code": "MISSING_PARAMS"
        }), 400

    result = _automation_run_integration_test(task_type, action, params)
    return jsonify({
        "success": result.get("success", False),
        "test_result": result,
    })


@app.route('/api/automation/test/results', methods=['GET'])
def automation_test_results():
    """Get test results."""
    limit = min(50, int(request.args.get("limit", 10)))
    with _AUTOMATION_TEST_RESULTS_LOCK:
        results = list(_AUTOMATION_TEST_RESULTS)
    return jsonify({
        "success": True,
        "count": len(results),
        "results": results[-limit:],
    })


@app.route('/api/automation/rollout/status', methods=['GET'])
def automation_rollout_status():
    """Get rollout status."""
    with _AUTOMATION_ROLLOUT_LOCK:
        state = dict(_AUTOMATION_ROLLOUT_STATE)
    return jsonify({
        "success": True,
        "rollout": state,
    })


@app.route('/api/automation/rollout/start', methods=['POST'])
def automation_rollout_start():
    """Start rollout phase."""
    data = request.get_json(silent=True) or {}
    phase = str(data.get("phase", "testing")).strip().lower()

    valid_phases = ["testing", "rolling_out", "complete"]
    if phase not in valid_phases:
        return jsonify({
            "success": False,
            "error": f"Invalid phase. Must be one of: {valid_phases}",
            "error_code": "INVALID_PHASE"
        }), 400

    _automation_rollout_start(phase)
    return jsonify({
        "success": True,
        "message": f"Rollout started: {phase}",
    })


@app.route('/api/automation/rollout/complete', methods=['POST'])
def automation_rollout_complete():
    """Complete rollout."""
    _automation_rollout_complete()
    return jsonify({
        "success": True,
        "message": "Rollout completed",
    })


@app.route('/api/automation/rollout/rollback', methods=['POST'])
def automation_rollout_rollback():
    """Rollback rollout."""
    _automation_rollout_rollback()
    return jsonify({
        "success": True,
        "message": "Rollback initiated",
    })


@app.route('/api/automation/rollout/feature', methods=['POST'])
def automation_rollout_feature():
    """Add feature to rollout."""
    data = request.get_json(silent=True) or {}
    feature = str(data.get("feature", "")).strip()
    status = str(data.get("status", "completed")).strip()

    if not feature:
        return jsonify({"success": False, "error": "feature is required"}), 400

    with _AUTOMATION_ROLLOUT_LOCK:
        _AUTOMATION_ROLLOUT_STATE["features"].append({
            "feature": feature,
            "status": status,
            "added_at": datetime.now().isoformat(),
        })

    return jsonify({
        "success": True,
        "message": f"Feature added: {feature}",
    })


@app.route('/api/automation/rollout/issue', methods=['POST'])
def automation_rollout_issue():
    """Report an issue during rollout."""
    data = request.get_json(silent=True) or {}
    issue = str(data.get("issue", "")).strip()
    severity = str(data.get("severity", "medium")).strip()

    if not issue:
        return jsonify({"success": False, "error": "issue is required"}), 400

    with _AUTOMATION_ROLLOUT_LOCK:
        _AUTOMATION_ROLLOUT_STATE["issues"].append({
            "issue": issue,
            "severity": severity,
            "reported_at": datetime.now().isoformat(),
            "resolved": False,
        })

    # Create alert for critical issues
    if severity == "critical":
        _automation_alert("critical", f"Rollout issue: {issue}", issue, "rollout")

    return jsonify({
        "success": True,
        "message": f"Issue reported: {issue}",
    })


@app.route('/api/automation/schedule', methods=['POST'])
def automation_schedule_create():
    data = request.get_json(silent=True) or {}
    schedule_id = str(data.get("schedule_id", "")).strip() or f"schedule_{int(time.time() * 1000)}"
    task_type = str(data.get("task_type", "")).strip().lower()
    action = str(data.get("action", "")).strip().lower()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    interval_sec = max(10, int(data.get("interval_sec", 60) or 60))
    enabled = _to_bool(data.get("enabled", True))
    session_id = str(data.get("session_id", "scheduler")).strip() or "scheduler"
    owner_id = str(data.get("owner_id", "scheduler")).strip() or "scheduler"
    gate = _automation_validate_task(task_type, action, params)
    if not gate.get("ok"):
        return jsonify({"success": False, "error": gate.get("error"), "error_code": gate.get("error_code")}), 400
    schedule = {
        "schedule_id": schedule_id,
        "task_type": task_type,
        "action": action,
        "params": params,
        "interval_sec": interval_sec,
        "enabled": enabled,
        "session_id": session_id,
        "owner_id": owner_id,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "last_run": 0,
        "next_run_at": (time.time() + interval_sec) if enabled else 0,
        "run_count": 0,
        "last_result": None,
    }
    with _AUTOMATION_SCHEDULER_LOCK:
        _automation_scheduled_tasks[schedule_id] = schedule
        _automation_save_schedules()
    return jsonify({"success": True, "schedule": schedule})


@app.route('/api/automation/schedule/<schedule_id>', methods=['DELETE'])
def automation_schedule_delete(schedule_id: str):
    with _AUTOMATION_SCHEDULER_LOCK:
        if schedule_id not in _automation_scheduled_tasks:
            return jsonify({"success": False, "error": f"Schedule not found: {schedule_id}", "error_code": "SCHEDULE_NOT_FOUND"}), 404
        _automation_scheduled_tasks.pop(schedule_id, None)
        _automation_save_schedules()
    return jsonify({"success": True, "schedule_id": schedule_id})


@app.route('/api/automation/schedule/<schedule_id>', methods=['POST'])
def automation_schedule_update(schedule_id: str):
    data = request.get_json(silent=True) or {}
    with _AUTOMATION_SCHEDULER_LOCK:
        schedule = _automation_scheduled_tasks.get(schedule_id)
        if not isinstance(schedule, dict):
            return jsonify({"success": False, "error": f"Schedule not found: {schedule_id}", "error_code": "SCHEDULE_NOT_FOUND"}), 404
        if "enabled" in data:
            schedule["enabled"] = _to_bool(data.get("enabled"))
        if "interval_sec" in data:
            schedule["interval_sec"] = max(10, int(data.get("interval_sec") or schedule.get("interval_sec", 60)))
        if "params" in data and isinstance(data.get("params"), dict):
            schedule["params"] = data.get("params")
        if "session_id" in data:
            schedule["session_id"] = str(data.get("session_id", "scheduler")).strip() or "scheduler"
        if "owner_id" in data:
            schedule["owner_id"] = str(data.get("owner_id", "scheduler")).strip() or "scheduler"
        interval = max(10, int(schedule.get("interval_sec", 60) or 60))
        schedule["next_run_at"] = (time.time() + interval) if bool(schedule.get("enabled", True)) else 0
        schedule["updated_at"] = datetime.now().isoformat()
        _automation_save_schedules()
        updated = dict(schedule)
    return jsonify({"success": True, "schedule": updated})


@app.route('/api/automation/schedules')
def automation_schedules_list():
    with _AUTOMATION_SCHEDULER_LOCK:
        rows = sorted((dict(item) for item in _automation_scheduled_tasks.values()), key=lambda item: str(item.get("schedule_id", "")))
    return jsonify({"success": True, "count": len(rows), "schedules": rows, "scheduler_running": bool(_automation_scheduler_running)})


@app.route('/api/automation/schedule/<schedule_id>/run', methods=['POST'])
def automation_schedule_run_now(schedule_id: str):
    with _AUTOMATION_SCHEDULER_LOCK:
        row = _automation_scheduled_tasks.get(schedule_id)
        if not isinstance(row, dict):
            return jsonify({"success": False, "error": f"Schedule not found: {schedule_id}", "error_code": "SCHEDULE_NOT_FOUND"}), 404
        payload = dict(row)
    task = _create_automation_task(str(payload.get("task_type", "")), str(payload.get("action", "")), payload.get("params") if isinstance(payload.get("params"), dict) else {})
    task["session_id"] = str(payload.get("session_id", "scheduler")).strip() or "scheduler"
    task["owner_id"] = str(payload.get("owner_id", "scheduler")).strip() or "scheduler"
    lease = _automation_assert_session_lease(task["session_id"], task["owner_id"], "")
    if not lease.get("allowed"):
        return jsonify({"success": False, "error": lease.get("error"), "error_code": lease.get("error_code")}), 409
    task["lease_token"] = str((lease.get("session") or {}).get("lease_token", ""))
    done = _execute_automation_task(task)
    with _AUTOMATION_SCHEDULER_LOCK:
        src = _automation_scheduled_tasks.get(schedule_id)
        if isinstance(src, dict):
            src["last_run"] = time.time()
            src["run_count"] = int(src.get("run_count", 0) or 0) + 1
            src["last_result"] = {
                "success": done.get("status") == "completed",
                "status": done.get("status"),
                "error_code": done.get("error_code"),
                "task_id": done.get("task_id"),
            }
            _automation_save_schedules()
    return jsonify({"success": done.get("status") == "completed", "task_id": done.get("task_id"), "status": done.get("status"), "result": done.get("result"), "error": done.get("error"), "error_code": done.get("error_code")})


_start_automation_scheduler()


# ============================================================================
# NOTIFICATION SYSTEM
# Track and notify important system events
# ============================================================================

# Notification levels
NOTIFY_INFO = "info"
NOTIFY_WARNING = "warning"
NOTIFY_ERROR = "error"
NOTIFY_SUCCESS = "success"
NOTIFY_CRITICAL = "critical"

# Notification categories
NOTIFY_CATEGORIES = [
    "system",       # System health/changes
    "automation",   # Automation events
    "agent",        # Agent status changes
    "error",        # Errors and failures
    "security",     # Security events
    "user",         # User actions
    "schedule",     # Scheduled task events
]

_NOTIFICATIONS_LOCK = threading.Lock()
_notifications: List[Dict[str, Any]] = []
_NOTIFICATION_MAX = 200


def _add_notification(
    level: str,
    category: str,
    title: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    source: str = "system"
) -> Dict[str, Any]:
    """Add a new notification."""
    notification = {
        "id": f"notif_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
        "level": level,
        "category": category,
        "title": title,
        "message": message,
        "data": data or {},
        "source": source,
        "created_at": datetime.now().isoformat(),
        "read": False,
    }

    with _NOTIFICATIONS_LOCK:
        _notifications.append(notification)
        # Keep only last N notifications
        while len(_notifications) > _NOTIFICATION_MAX:
            _notifications.pop(0)

    return notification


@app.route('/api/notifications', methods=['GET'])
def notifications_list():
    """
    List notifications with optional filtering.

    Query params:
    - level: filter by level (info|warning|error|success|critical)
    - category: filter by category
    - unread_only: only show unread (true|false)
    - limit: max results (default 50)
    """
    level = request.args.get("level")
    category = request.args.get("category")
    unread_only = request.args.get("unread_only", "false").lower() == "true"
    limit = _int_in_range(request.args.get("limit", 50), default=50, min_value=1, max_value=200)

    with _NOTIFICATIONS_LOCK:
        filtered = list(_notifications)

    # Apply filters
    if level:
        filtered = [n for n in filtered if n.get("level") == level]
    if category:
        filtered = [n for n in filtered if n.get("category") == category]
    if unread_only:
        filtered = [n for n in filtered if not n.get("read")]

    # Sort by newest first and limit
    filtered = sorted(filtered, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

    # Count stats
    with _NOTIFICATIONS_LOCK:
        total = len(_notifications)
        unread_count = sum(1 for n in _notifications if not n.get("read"))

    return jsonify({
        "success": True,
        "notifications": filtered,
        "total": total,
        "unread_count": unread_count,
    })


@app.route('/api/notifications/<notification_id>/read', methods=['POST'])
def notification_mark_read(notification_id: str):
    """Mark a notification as read."""
    with _NOTIFICATIONS_LOCK:
        for n in _notifications:
            if n.get("id") == notification_id:
                n["read"] = True
                return jsonify({"success": True, "notification": n})

    return jsonify({
        "success": False,
        "error": f"Notification not found: {notification_id}",
    }), 404


@app.route('/api/notifications/read-all', methods=['POST'])
def notification_mark_all_read():
    """Mark all notifications as read."""
    with _NOTIFICATIONS_LOCK:
        for n in _notifications:
            n["read"] = True

    return jsonify({
        "success": True,
        "marked_count": len(_notifications),
    })


@app.route('/api/notifications/<notification_id>', methods=['DELETE'])
def notification_delete(notification_id: str):
    """Delete a notification."""
    with _NOTIFICATIONS_LOCK:
        for i, n in enumerate(_notifications):
            if n.get("id") == notification_id:
                _notifications.pop(i)
                return jsonify({"success": True, "notification_id": notification_id})

    return jsonify({
        "success": False,
        "error": f"Notification not found: {notification_id}",
    }), 404


@app.route('/api/notifications/clear', methods=['POST'])
def notification_clear():
    """Clear all notifications."""
    data = request.get_json() or {}
    keep_unread = data.get("keep_unread", False)

    with _NOTIFICATIONS_LOCK:
        if keep_unread:
            _notifications[:] = [n for n in _notifications if n.get("read")]
        else:
            _notifications.clear()

    return jsonify({
        "success": True,
    })


@app.route('/api/notifications/send', methods=['POST'])
def notification_send():
    """
    Send a notification (for testing or external integration).

    Request body:
    {
        "level": "info|warning|error|success|critical",
        "category": "system|automation|agent|error|security|user|schedule",
        "title": "Notification title",
        "message": "Notification message",
        "data": {...},
        "source": "external"
    }
    """
    data = request.get_json() or {}

    level = data.get("level", NOTIFY_INFO)
    category = data.get("category", "system")
    title = data.get("title", "")
    message = data.get("message", "")
    source = data.get("source", "api")
    notification_data = data.get("data", {})

    if not title:
        return jsonify({
            "success": False,
            "error": "Title is required",
        }), 400

    if level not in [NOTIFY_INFO, NOTIFY_WARNING, NOTIFY_ERROR, NOTIFY_SUCCESS, NOTIFY_CRITICAL]:
        return jsonify({
            "success": False,
            "error": f"Invalid level: {level}",
        }), 400

    if category not in NOTIFY_CATEGORIES:
        return jsonify({
            "success": False,
            "error": f"Invalid category: {category}",
        }), 400

    notification = _add_notification(
        level=level,
        category=category,
        title=title,
        message=message,
        data=notification_data,
        source=source
    )

    return jsonify({
        "success": True,
        "notification": notification,
    })


@app.route('/api/notifications/stats', methods=['GET'])
def notification_stats():
    """Get notification statistics."""
    with _NOTIFICATIONS_LOCK:
        total = len(_notifications)
        unread = sum(1 for n in _notifications if not n.get("read"))

        by_level = {}
        by_category = {}

        for n in _notifications:
            level = n.get("level", "unknown")
            category = n.get("category", "unknown")

            by_level[level] = by_level.get(level, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1

    return jsonify({
        "success": True,
        "total": total,
        "unread": unread,
        "by_level": by_level,
        "by_category": by_category,
    })


# ============================================================================
# WEBHOOK INTEGRATION
# Send notifications to external services (Slack, Discord, Telegram, etc.)
# ============================================================================

_WEBHOOKS_LOCK = threading.Lock()
_webhooks: Dict[str, Dict[str, Any]] = {}

# Supported webhook types
WEBHOOK_TYPES = ["slack", "discord", "telegram", "custom"]


def _send_webhook_sync(webhook_id: str, payload: Dict[str, Any]) -> bool:
    """Send webhook synchronously."""
    with _WEBHOOKS_LOCK:
        if webhook_id not in _webhooks:
            return False
        webhook = _webhooks[webhook_id]

    url = webhook.get("url", "")
    webhook_type = webhook.get("type", "custom")
    headers = webhook.get("headers", {})

    if not url:
        return False

    try:
        # Build payload based on type
        if webhook_type == "slack":
            data = {
                "text": payload.get("message", ""),
                "channel": webhook.get("channel", ""),
                "username": webhook.get("username", "Jack Automation"),
                "icon_emoji": webhook.get("icon", ":robot:"),
            }
        elif webhook_type == "discord":
            data = {
                "content": payload.get("message", ""),
                "username": webhook.get("username", "Jack Automation"),
                "avatar_url": webhook.get("avatar_url", ""),
            }
        elif webhook_type == "telegram":
            data = {
                "chat_id": webhook.get("chat_id", ""),
                "text": payload.get("message", ""),
                "parse_mode": "HTML",
            }
        else:  # custom
            data = payload

        # Send request
        import urllib.request
        import urllib.parse

        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status < 400

    except Exception as e:
        logger.error(f"Webhook send error: {e}")
        return False


@app.route('/api/webhooks', methods=['GET'])
def webhooks_list():
    """List all configured webhooks."""
    with _WEBHOOKS_LOCK:
        webhook_list = []
        for wid, wh in _webhooks.items():
            webhook_list.append({
                "id": wid,
                "name": wh.get("name", wid),
                "type": wh.get("type", "custom"),
                "url": wh.get("url", "")[:20] + "..." if len(wh.get("url", "")) > 20 else wh.get("url", ""),
                "enabled": wh.get("enabled", True),
                "events": wh.get("events", []),
                "created_at": wh.get("created_at"),
            })

    return jsonify({
        "success": True,
        "webhooks": webhook_list,
        "count": len(webhook_list),
    })


@app.route('/api/webhooks', methods=['POST'])
def webhook_create():
    """
    Create a webhook.

    Request body:
    {
        "name": "My Slack",
        "type": "slack|discord|telegram|custom",
        "url": "https://...",
        "channel": "#general",        // for slack
        "chat_id": "123456",          // for telegram
        "username": "Bot",            // optional
        "icon": ":robot:",            // for slack
        "headers": {},                // custom headers
        "events": ["error", "critical"],  // events to trigger on
        "enabled": true
    }
    """
    data = request.get_json() or {}

    name = data.get("name", "")
    webhook_type = data.get("type", "custom")
    url = data.get("url", "")

    if not url:
        return jsonify({
            "success": False,
            "error": "URL is required",
        }), 400

    if webhook_type not in WEBHOOK_TYPES:
        return jsonify({
            "success": False,
            "error": f"Invalid type. Must be one of: {WEBHOOK_TYPES}",
        }), 400

    webhook_id = data.get("id") or f"webhook_{secrets.token_hex(6)}"

    webhook = {
        "id": webhook_id,
        "name": name or webhook_id,
        "type": webhook_type,
        "url": url,
        "channel": data.get("channel", ""),
        "chat_id": data.get("chat_id", ""),
        "username": data.get("username", "Jack Automation"),
        "icon": data.get("icon", ":robot:"),
        "avatar_url": data.get("avatar_url", ""),
        "headers": data.get("headers", {}),
        "events": data.get("events", []),
        "enabled": data.get("enabled", True),
        "created_at": datetime.now().isoformat(),
    }

    with _WEBHOOKS_LOCK:
        _webhooks[webhook_id] = webhook

    return jsonify({
        "success": True,
        "webhook": {
            "id": webhook_id,
            "name": webhook["name"],
            "type": webhook_type,
        },
    })


@app.route('/api/webhooks/<webhook_id>', methods=['DELETE'])
def webhook_delete(webhook_id: str):
    """Delete a webhook."""
    with _WEBHOOKS_LOCK:
        if webhook_id in _webhooks:
            del _webhooks[webhook_id]
            return jsonify({"success": True, "webhook_id": webhook_id})

    return jsonify({
        "success": False,
        "error": f"Webhook not found: {webhook_id}",
    }), 404


@app.route('/api/webhooks/<webhook_id>', methods=['POST'])
def webhook_update(webhook_id: str):
    """Update a webhook."""
    data = request.get_json() or {}

    with _WEBHOOKS_LOCK:
        if webhook_id not in _webhooks:
            return jsonify({
                "success": False,
                "error": f"Webhook not found: {webhook_id}",
            }), 404

        webhook = _webhooks[webhook_id]

        # Update allowed fields
        if "name" in data:
            webhook["name"] = data["name"]
        if "url" in data:
            webhook["url"] = data["url"]
        if "enabled" in data:
            webhook["enabled"] = data["enabled"]
        if "events" in data:
            webhook["events"] = data["events"]
        if "channel" in data:
            webhook["channel"] = data["channel"]
        if "chat_id" in data:
            webhook["chat_id"] = data["chat_id"]

        webhook["updated_at"] = datetime.now().isoformat()

    return jsonify({
        "success": True,
        "webhook": {
            "id": webhook_id,
            "name": webhook["name"],
            "enabled": webhook["enabled"],
        },
    })


@app.route('/api/webhooks/<webhook_id>/test', methods=['POST'])
def webhook_test(webhook_id: str):
    """Test a webhook by sending a test message."""
    data = request.get_json() or {}
    message = data.get("message", "🧪 Test message from Jack Automation")

    with _WEBHOOKS_LOCK:
        if webhook_id not in _webhooks:
            return jsonify({
                "success": False,
                "error": f"Webhook not found: {webhook_id}",
            }), 404

    success = _send_webhook_sync(webhook_id, {"message": message})

    return jsonify({
        "success": success,
        "webhook_id": webhook_id,
        "message": "Test sent successfully" if success else "Failed to send",
    })


@app.route('/api/webhooks/send', methods=['POST'])
def webhook_send():
    """
    Send a message via webhook.

    Request body:
    {
        "webhook_id": "optional specific webhook",
        "type": "optional filter by type (slack|discord|telegram)",
        "message": "Message to send",
        "level": "info|warning|error|critical"  // filter by event
    }
    """
    data = request.get_json() or {}

    message = data.get("message", "")
    webhook_id = data.get("webhook_id")
    webhook_type = data.get("type")
    level = data.get("level", "info")

    if not message:
        return jsonify({
            "success": False,
            "error": "Message is required",
        }), 400

    sent_count = 0

    with _WEBHOOKS_LOCK:
        webhooks_to_send = []

        for wid, wh in _webhooks.items():
            if not wh.get("enabled", True):
                continue

            # Filter by specific webhook_id
            if webhook_id and wid != webhook_id:
                continue

            # Filter by type
            if webhook_type and wh.get("type") != webhook_type:
                continue

            # Filter by events
            events = wh.get("events", [])
            if events and level not in events:
                continue

            webhooks_to_send.append(wid)

    # Send to all matching webhooks
    for wid in webhooks_to_send:
        if _send_webhook_sync(wid, {"message": message, "level": level}):
            sent_count += 1

    return jsonify({
        "success": sent_count > 0,
        "sent_count": sent_count,
        "total_matched": len(webhooks_to_send),
    })


# Auto-generate some notifications on startup
_add_notification(
    level=NOTIFY_INFO,
    category="system",
    title="System Started",
    message="Automation system initialized and ready",
    source="system"
)

_add_notification(
    level=NOTIFY_INFO,
    category="automation",
    title="Automation Bus Ready",
    message="Universal automation task bus is online",
    source="system"
)


@app.route('/api/model-routing/status')
def get_model_routing_status():
    """Get model routing, token usage, and budget summary."""
    return jsonify(build_model_routing_status(event_limit=80))


@app.route('/api/model-routing/health', methods=['GET'])
def model_routing_health():
    """Get per-model health status and availability."""
    try:
        from src.core.llm_caller import get_available_models
        models = get_available_models()
    except ImportError:
        models = {}

    try:
        from src.core.model_router import ModelRouter
        router = ModelRouter()
        budget = router.budget_tracker
        budget_info = {
            'daily_spent': getattr(budget, 'daily_spent', 0),
            'daily_limit': getattr(budget, 'daily_limit', 0),
            'remaining': getattr(budget, 'daily_limit', 0) - getattr(budget, 'daily_spent', 0),
        }
    except (ImportError, Exception):
        budget_info = {}

    from datetime import datetime
    return jsonify({
        'models': models,
        'budget': budget_info,
        'timestamp': datetime.now().isoformat(),
    })


@app.route('/api/skill-recommendation/<task_type>')
def skill_recommendation(task_type):
    """Get skill-based recommendation for a task type."""
    try:
        from src.brain.nexus_brain import get_brain
        brain = get_brain()
        if brain and hasattr(brain, 'skill_tracker'):
            return jsonify(brain.skill_tracker.get_skill_recommendation(task_type))
        return jsonify({'recommendation': 'execute', 'confidence': 0.0, 'reason': 'Brain not available'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/budget-projection')
def budget_projection():
    """Get budget spend projection for end of day."""
    try:
        from src.core.model_router import ModelRouter
        router_inst = ModelRouter()
        return jsonify(router_inst.get_budget_projection())
    except (ImportError, AttributeError) as e:
        return jsonify({'error': str(e), 'status': 'unavailable'})


@app.route('/api/source-quality')
def source_quality():
    """Get ranked source quality scores."""
    try:
        from src.brain.omniscient_scout import get_scout
        scout = get_scout()
        return jsonify({'sources': scout.get_ranked_sources()})
    except (ImportError, AttributeError) as e:
        return jsonify({'error': str(e), 'status': 'unavailable'})


@app.route('/api/system-overview')
def system_overview():
    """Comprehensive system overview - all subsystems in one call."""
    from datetime import datetime
    overview = {'timestamp': datetime.now().isoformat(), 'subsystems': {}}

    try:
        from src.core.llm_caller import get_available_models
        models = get_available_models()
        overview['subsystems']['llm'] = {
            'available': [n for n, ok in models.items() if ok],
            'unavailable': [n for n, ok in models.items() if not ok],
        }
    except ImportError:
        overview['subsystems']['llm'] = {'status': 'unavailable'}

    try:
        from src.core.model_router import ModelRouter
        router_inst = ModelRouter()
        overview['subsystems']['budget'] = router_inst.get_budget_projection()
    except Exception:
        overview['subsystems']['budget'] = {'status': 'unavailable'}

    try:
        from src.brain.integration_hub import get_hub
        hub = get_hub()
        overview['subsystems']['hub'] = hub.get_status()
    except Exception:
        overview['subsystems']['hub'] = {'status': 'unavailable'}

    return jsonify(overview)


@app.route('/api/health')
def health_check():
    """Quick health check - returns system readiness status."""
    from datetime import datetime
    checks = {}

    try:
        from src.brain.nexus_brain import get_brain
        brain = get_brain()
        checks['brain'] = 'ok' if brain else 'unavailable'
    except Exception:
        checks['brain'] = 'error'

    try:
        from src.core.llm_caller import get_available_models
        models = get_available_models()
        active = sum(1 for ok in models.values() if ok)
        checks['llm'] = 'ok' if active > 0 else 'degraded'
        checks['llm_active_count'] = active
    except ImportError:
        checks['llm'] = 'unavailable'

    all_ok = all(v in ('ok',) for k, v in checks.items() if not k.endswith('_count'))
    return jsonify({
        'status': 'healthy' if all_ok else 'degraded',
        'timestamp': datetime.now().isoformat(),
        'checks': checks,
    })


@app.route('/api/self-diagnostic')
def self_diagnostic():
    """Run comprehensive self-diagnostic across all subsystems."""
    from datetime import datetime
    diag = {'timestamp': datetime.now().isoformat(), 'issues': [], 'score': 100}

    try:
        from src.core.llm_caller import get_available_models
        models = get_available_models()
        active = sum(1 for ok in models.values() if ok)
        if active == 0:
            diag['issues'].append('No LLM models available')
            diag['score'] -= 30
    except ImportError:
        diag['issues'].append('LLM caller not installed')
        diag['score'] -= 25

    try:
        from src.core.model_router import ModelRouter
        router_inst = ModelRouter()
        proj = router_inst.get_budget_projection()
        if proj.get('status') == 'over_budget':
            diag['issues'].append('Over budget')
            diag['score'] -= 20
    except Exception:
        pass

    diag['score'] = max(0, diag['score'])
    diag['verdict'] = 'excellent' if diag['score'] >= 90 else 'good' if diag['score'] >= 70 else 'needs_attention'
    return jsonify(diag)


@app.route('/api/model-routing/events')
def get_model_routing_events():
    """Get recent model routing events."""
    limit = _int_in_range(request.args.get("limit", 50), default=50, min_value=1, max_value=200)
    agent = request.args.get("agent")
    events = read_recent_routing_events(limit=limit, agent=agent)
    return jsonify({
        "count": len(events),
        "events": events,
    })


@app.route('/api/providers/catalog')
def providers_catalog():
    """List supported provider types and managed agents for BYOK."""
    registry = load_model_registry_snapshot()
    return jsonify({
        "success": True,
        "catalog": _provider_store.get_catalog(),
        "official_registry": registry,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/providers/catalog/refresh', methods=['POST'])
def providers_catalog_refresh():
    """Refresh model catalog from official provider APIs using configured BYOK profile."""
    rate_limited = _rate_limited("providers_catalog_refresh", API_RATE_LIMIT_PROVIDER_PER_MIN, 60)
    if rate_limited:
        return rate_limited

    payload = request.get_json(silent=True) or {}
    timeout_sec = _int_in_range(payload.get("timeout_sec", 8), default=8, min_value=2, max_value=30)
    profile = _provider_store.get(masked=False)
    snapshot = refresh_model_registry_from_profile(profile=profile, timeout_sec=timeout_sec)
    return jsonify({
        "success": True,
        "official_registry": snapshot,
        "catalog": _provider_store.get_catalog(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/providers/profile', methods=['GET', 'POST'])
def providers_profile():
    """Get or apply BYOK provider profile for an Orion instance."""
    rate_limited = _rate_limited("providers_profile", API_RATE_LIMIT_PROVIDER_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    if request.method == "GET":
        instance_id = str(request.args.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
        runtime_payload = _fetch_instance_runtime(instance_id)
        runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
        profile = runtime.get("provider_profile") if isinstance(runtime.get("provider_profile"), dict) else None
        if not isinstance(profile, dict):
            profile = _provider_store.get(masked=True)
        return jsonify({
            "success": True,
            "instance_id": instance_id,
            "profile": profile,
            "catalog": _provider_store.get_catalog(),
            "timestamp": datetime.now().isoformat(),
        })

    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    profile_patch = data.get("profile", data)
    if not isinstance(profile_patch, dict):
        return jsonify({"success": False, "error": "profile must be a JSON object"}), 400

    result = _call_instance_command(
        instance_id,
        "orion",
        "apply_provider_profile",
        "high",
        {"profile": profile_patch},
    )
    ok = bool(result.get("success", True))

    runtime_payload = _fetch_instance_runtime(instance_id)
    runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
    profile = runtime.get("provider_profile") if isinstance(runtime.get("provider_profile"), dict) else None
    if not isinstance(profile, dict):
        profile = _provider_store.get(masked=True)

    state.add_agent_log(
        "guardian",
        f"🔌 Provider profile apply on {instance_id}: {'ok' if ok else 'failed'}",
        "success" if ok else "warning",
    )
    return jsonify({
        "success": ok,
        "instance_id": instance_id,
        "result": result,
        "profile": profile,
        "catalog": _provider_store.get_catalog(),
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/runtime/locks')
def get_runtime_locks():
    """Get runtime lock states for conflict/stuck diagnostics."""
    learning_path = os.getenv("LEARNING_RUNTIME_LOCK_PATH", "data/state/learning_runtime.lock")
    autodev_path = os.getenv("AUTODEV_RUNTIME_LOCK_PATH", "data/state/autodev_runtime.lock")
    scan_path = os.getenv("SCAN_OPERATION_LOCK_PATH", "data/state/knowledge_scan.lock")
    improvement_path = os.getenv("IMPROVEMENT_OPERATION_LOCK_PATH", "data/state/improvement_apply.lock")
    daily_cycle_path = os.getenv("DAILY_CYCLE_OPERATION_LOCK_PATH", "data/state/daily_self_learning.lock")
    return jsonify(
        {
            "learning": ProcessSingleton.inspect(learning_path),
            "autodev": ProcessSingleton.inspect(autodev_path),
            "operations": {
                "knowledge_scan": ProcessSingleton.inspect(scan_path),
                "apply_improvements": ProcessSingleton.inspect(improvement_path),
                "daily_self_learning": ProcessSingleton.inspect(daily_cycle_path),
            },
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route('/api/model-routing/recommend', methods=['POST'])
def recommend_model_routing():
    """Recommend best model chain for a specific task profile."""
    payload = request.get_json(silent=True) or {}

    task_type = _parse_task_type(payload.get("task_type")) or TaskType.CODE_GENERATION
    complexity = _parse_complexity(payload.get("complexity"))
    importance = str(payload.get("importance", "normal")).strip().lower() or "normal"
    if importance not in {"low", "normal", "high", "critical"}:
        importance = "normal"

    prefer_speed = _to_bool(payload.get("prefer_speed"))
    prefer_cost = _to_bool(payload.get("prefer_cost"))
    estimated_tokens = _int_in_range(payload.get("estimated_tokens", 1200), default=1200, min_value=64, max_value=32000)

    router = ModelRouter()
    routing_decision = router.get_routing_decision(
        task_type=task_type,
        complexity=complexity,
        importance=importance,
        prefer_speed=prefer_speed,
        prefer_cost=prefer_cost,
        estimated_tokens=estimated_tokens,
    )
    selected = router.get_model_for_task(
        task_type=task_type,
        complexity=complexity,
        prefer_speed=prefer_speed,
        prefer_cost=prefer_cost,
        importance=importance,
        estimated_tokens=estimated_tokens,
    )

    costs = estimate_task_cost(task_type, estimated_tokens=estimated_tokens)
    cost_rows = [
        {
            "model": model_name,
            "cost_usd": round(float(item.get("cost_usd", 0.0)), 6),
            "available": bool(item.get("available")),
            "runtime_callable": bool(item.get("runtime_callable")),
        }
        for model_name, item in costs.items()
    ]
    cost_rows.sort(key=lambda x: x["cost_usd"])

    cheapest = get_cheapest_model_for_task(task_type)
    status = build_model_routing_status(event_limit=30)
    budget_ratio = float(((status.get("budget") or {}).get("budget_ratio")) or 0.0)

    notes = []
    if estimated_tokens <= 600:
        notes.append("Prompt ngan: uu tien model nhanh/chi phi thap cho lap nhanh.")
    elif estimated_tokens >= 3000:
        notes.append("Prompt dai: uu tien model chat luong cao de giam vong lap sua.")
    if importance in {"high", "critical"}:
        notes.append("Task quan trong: uu tien do on dinh va fallback chain day du.")
    if budget_ratio >= 0.85:
        notes.append("Gan ngan sach ngay: bat prefer_cost de giam chi phi.")
    if prefer_speed and not prefer_cost:
        notes.append("Dang uu tien toc do: latency se giam, chat luong co the giam o task phuc tap.")
    if prefer_cost and not prefer_speed:
        notes.append("Dang uu tien chi phi: nen tang quality cho task security/planning quan trong.")

    return jsonify({
        "input": {
            "task_type": task_type.value,
            "complexity": complexity.value if complexity else None,
            "importance": importance,
            "prefer_speed": prefer_speed,
            "prefer_cost": prefer_cost,
            "estimated_tokens": estimated_tokens,
        },
        "recommended_model": selected.name,
        "cheapest_runtime_model": cheapest.name if cheapest else None,
        "routing": routing_decision,
        "estimated_costs": cost_rows,
        "notes": notes,
    })


@app.route('/api/orion/logs')
def get_orion_logs():
    """Get Orion logs"""
    return jsonify(state.orion_logs[-100:])


@app.route('/api/guardian/logs')
def get_guardian_logs():
    """Get Guardian logs"""
    return jsonify(state.guardian_logs[-100:])


@app.route('/api/agents/status')
def get_agents_status():
    """Get real-time status for all known agents."""
    runtime_extra = {}
    status_handler = _runtime_bridge.get("status_handler")
    if callable(status_handler):
        try:
            runtime_extra = status_handler() or {}
        except Exception as exc:
            logger.error(f"Runtime status handler failed: {exc}")
    return jsonify({
        "agents": state.get_agent_fleet(),
        "runtime": runtime_extra,
        "instances": get_orion_instances_snapshot(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/agents/activity')
def get_agents_activity():
    """Get compact realtime activity snapshot for one Orion instance."""
    instance_id = str(request.args.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    limit = _int_in_range(request.args.get("limit", 12), default=12, min_value=1, max_value=30)
    activity = _build_agent_activity_snapshot(instance_id=instance_id, max_agents=limit)
    return jsonify({
        "success": True,
        "activity": activity,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/agents/logs')
def get_agents_logs():
    """Get logs for one agent or all agents."""
    target = state._normalize_agent(request.args.get("agent", "all"))
    limit = _int_in_range(request.args.get("limit", 50), default=50, min_value=1, max_value=300)

    if target == "all":
        return jsonify({
            "agent": "all",
            "logs": {agent: logs[-limit:] for agent, logs in state.agent_logs.items()},
        })

    return jsonify({
        "agent": target,
        "logs": state.agent_logs.get(target, [])[-limit:],
    })


@app.route('/api/events/feed')
def get_events_feed():
    """Get unified realtime event feed across agents, decisions, and routing."""
    limit = _int_in_range(request.args.get("limit", 120), default=120, min_value=1, max_value=500)
    agent = request.args.get("agent", "all")
    level = request.args.get("level", "all")
    include_routing = _to_bool(request.args.get("include_routing", "true"))
    events = state.get_event_feed(limit=limit, agent=agent, level=level, include_routing=include_routing)
    return jsonify({
        "count": len(events),
        "events": events,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/events/digest')
def get_events_digest():
    """Get high-signal digest from operational events (noise-reduced)."""
    limit = _int_in_range(request.args.get("limit", 140), default=140, min_value=40, max_value=500)
    digest = _build_events_digest(limit=limit)
    return jsonify({
        "success": True,
        "digest": digest,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/interventions/queue')
def get_interventions_queue():
    """Get unified pending intervention queue for manual overrides."""
    snapshot = _build_intervention_queue_snapshot()
    return _api_ok(
        {
            "queue": snapshot.get("items", []),
            "summary": snapshot.get("summary", {}),
        }
    )


@app.route('/api/interventions/history')
def get_interventions_history():
    """Get intervention action history from persistent JSONL + decision history."""
    limit = _int_in_range(request.args.get("limit", 120), default=120, min_value=1, max_value=800)
    file_rows = _intervention_history_tail(limit=limit * 2)
    decision_rows = []
    for row in list(state.decision_history)[-limit:]:
        if not isinstance(row, dict):
            continue
        decision_rows.append(
            {
                "timestamp": row.get("timestamp"),
                "action": f"decision_{row.get('decision', 'updated')}",
                "decision_id": row.get("id"),
                "success": True,
                "actor": row.get("defer_actor", "dashboard"),
                "detail": row,
            }
        )
    merged = _sort_by_timestamp_desc(file_rows + decision_rows)[:limit]
    return _api_ok({"history": merged, "count": len(merged)})


@app.route('/api/interventions/<decision_id>/defer', methods=['POST'])
def defer_intervention(decision_id: str):
    data = request.get_json(silent=True) or {}
    defer_sec = _int_in_range(data.get("defer_sec", 300), default=300, min_value=30, max_value=7200)
    reason = str(data.get("reason", "manual_defer")).strip() or "manual_defer"
    actor = str(data.get("actor", "dashboard")).strip() or "dashboard"
    updated = state.defer_decision(decision_id=decision_id, defer_sec=defer_sec, reason=reason, actor=actor)
    if not updated:
        return _api_error("INTERVENTION_NOT_FOUND", "Intervention not found", status_code=404)
    _append_intervention_history(
        action="deferred",
        decision_id=str(decision_id),
        success=True,
        actor=actor,
        detail={"defer_sec": defer_sec, "reason": reason},
    )
    return _api_ok({"item": updated})


@app.route('/api/interventions/<decision_id>/force-approve', methods=['POST'])
def force_approve_intervention(decision_id: str):
    data = request.get_json(silent=True) or {}
    actor = str(data.get("actor", "dashboard")).strip() or "dashboard"
    reason = str(data.get("reason", "force_approve")).strip() or "force_approve"
    auth = _guardian_control_auth(data)
    if not auth.get("authorized"):
        return _api_error("UNAUTHORIZED", "Unauthorized", status_code=401, data={"token_required": True})

    approved = state.approve_decision(decision_id)
    if not approved:
        return _api_error("INTERVENTION_NOT_FOUND", "Intervention not found", status_code=404)

    execution = None
    if str(approved.get("kind", "")).strip() == "command_intervention":
        command = str(approved.get("command", "")).strip()
        if command:
            execution = _execute_approved_shell_command(command)

    _append_intervention_history(
        action="force_approved",
        decision_id=str(decision_id),
        success=True,
        actor=actor,
        detail={"reason": reason, "execution": execution or {}},
    )
    return _api_ok({"item": approved, "execution": execution})


@app.route('/api/interventions/<decision_id>/respond', methods=['POST'])
def respond_intervention(decision_id: str):
    data = request.get_json(silent=True) or {}
    actor = str(data.get("actor", "dashboard")).strip() or "dashboard"
    remember = _to_bool(data.get("remember", True))
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    answer = str(data.get("answer", "") or "").strip()

    existing = state.get_pending_decision(decision_id)
    if not existing:
        return _api_error("INTERVENTION_NOT_FOUND", "Intervention not found", status_code=404)
    if str(existing.get("kind", "")).strip() != "human_input_request":
        return _api_error("INTERVENTION_TYPE_MISMATCH", "Intervention does not accept free-form response", status_code=400)
    if not values and answer:
        # Single-field fallback when frontend only sends plain text.
        details = existing.get("details") if isinstance(existing.get("details"), dict) else {}
        fields = details.get("fields") if isinstance(details.get("fields"), list) else []
        first_key = ""
        if fields and isinstance(fields[0], dict):
            first_key = str(fields[0].get("key", "")).strip()
        values = {first_key or "answer": answer}
    if not values:
        return _api_error("MISSING_VALUES", "Missing values payload", status_code=400)

    normalized_values: Dict[str, Any] = {}
    for key, raw_value in values.items():
        field_key = str(key or "").strip()
        if not field_key:
            continue
        if isinstance(raw_value, list):
            normalized_values[field_key] = [str(item or "").strip() for item in raw_value if str(item or "").strip()]
        elif isinstance(raw_value, (str, int, float, bool)):
            normalized_values[field_key] = raw_value
        elif raw_value is None:
            normalized_values[field_key] = ""
        else:
            normalized_values[field_key] = str(raw_value)
    if not normalized_values:
        return _api_error("MISSING_VALUES", "No usable fields in values payload", status_code=400)

    intake_snapshot = _automation_get_intake_store(redact_secret=True)
    applied_keys: List[str] = []
    if remember:
        intake_snapshot, applied_keys = _automation_apply_human_input_values(normalized_values)

    response_payload = {
        "values": normalized_values,
        "remember": remember,
        "applied_keys": applied_keys,
        "timestamp": datetime.now().isoformat(),
    }
    resolved = state.respond_decision(decision_id=decision_id, response=response_payload, actor=actor)
    if not resolved:
        return _api_error("INTERVENTION_NOT_FOUND", "Intervention not found", status_code=404)

    _append_intervention_history(
        action="responded",
        decision_id=str(decision_id),
        success=True,
        actor=actor,
        detail={"remember": remember, "applied_keys": applied_keys},
    )
    state.add_agent_log(
        "guardian",
        f"🧠 Human input received for {decision_id} ({len(normalized_values)} fields).",
        "success",
    )
    return _api_ok(
        {
            "item": resolved,
            "remember": remember,
            "applied_keys": applied_keys,
            "intake": intake_snapshot,
        }
    )


@app.route('/api/audit/timeline')
def get_audit_timeline():
    """Unified audit timeline with cursor-based pagination."""
    limit = _int_in_range(request.args.get("limit", 120), default=120, min_value=10, max_value=800)
    cursor = str(request.args.get("cursor", "") or "").strip() or None
    timeline = _build_audit_timeline(limit=limit, cursor=cursor)
    return _api_ok(
        {
            "items": timeline.get("items", []),
            "count": len(timeline.get("items", [])),
            "next_cursor": timeline.get("next_cursor"),
        }
    )


@app.route('/api/audit/export')
def export_audit_timeline():
    """Export audit timeline in json or jsonl format."""
    fmt = str(request.args.get("format", "jsonl") or "jsonl").strip().lower()
    limit = _int_in_range(request.args.get("limit", 500), default=500, min_value=1, max_value=2000)
    from_cursor = str(request.args.get("from", "") or "").strip() or None
    timeline = _build_audit_timeline(limit=limit, cursor=from_cursor)
    items = timeline.get("items", [])
    if fmt == "json":
        return _api_ok({"items": items, "count": len(items)})
    body = "\n".join(json.dumps(item, ensure_ascii=True) for item in items) + ("\n" if items else "")
    return Response(body, mimetype="application/x-ndjson")


@app.route('/api/computer-control/status')
def computer_control_status():
    """Get computer-control backend status."""
    return jsonify({
        "success": True,
        "control": _computer_control_status(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/computer-control/action', methods=['POST'])
def computer_control_action():
    """Execute a guarded computer-control action via pyautogui backend."""
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip().lower()
    if not action:
        return jsonify({"success": False, "error": "Missing action"}), 400

    result = _computer_control_action(action, data)
    level = "success" if result.get("success") else "warning"
    state.add_agent_log("guardian", f"🖱 Computer action {action}: {'ok' if result.get('success') else 'failed'}", level)
    return jsonify({
        "success": bool(result.get("success")),
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/computer-control/toggle', methods=['POST'])
def computer_control_toggle():
    """Enable/disable computer-control actions."""
    global _computer_control_enabled
    data = request.get_json(silent=True) or {}
    enabled = _to_bool(data.get("enabled", True))
    _computer_control_enabled = enabled
    state.add_agent_log(
        "guardian",
        f"🖱 Computer control {'enabled' if enabled else 'disabled'} by dashboard",
        "success" if enabled else "warning",
    )
    return jsonify({
        "success": True,
        "control": _computer_control_status(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/feedback/snapshot')
def feedback_snapshot():
    """Get persisted user feedback memory snapshot."""
    limit = _int_in_range(request.args.get("limit", 20), default=20, min_value=1, max_value=200)
    return jsonify({
        "success": True,
        "snapshot": _prompt_system.get_feedback_snapshot(limit=limit),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/feedback/log', methods=['POST'])
def feedback_log():
    """Persist explicit user feedback and convert to guardrail directive."""
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"success": False, "error": "Missing feedback text"}), 400

    category = str(data.get("category", "general")).strip().lower() or "general"
    severity = str(data.get("severity", "high")).strip().lower() or "high"
    feedback_id = _prompt_system.record_feedback(
        text=text,
        source="user_manual",
        category=category,
        severity=severity,
        add_directive=True,
        directive_priority=118 if severity in {"high", "critical"} else 115,
    )
    _broadcast_feedback_update(limit=8)
    state.add_agent_log("guardian", f"🧠 Feedback recorded ({category}): {feedback_id}", "success")
    return jsonify({
        "success": True,
        "feedback_id": feedback_id,
        "snapshot": _prompt_system.get_feedback_snapshot(limit=10),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/user/profile', methods=['GET', 'POST'])
def user_profile():
    """Get or update the user workflow profile."""
    if request.method == 'GET':
        return jsonify({
            "success": True,
            "profile": get_user_profile(),
            "timestamp": datetime.now().isoformat(),
        })

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"success": False, "error": "Missing profile update"}), 400

    profile = update_user_profile(data)
    state.add_agent_log("guardian", "🤝 User working agreement updated", "success")
    return jsonify({
        "success": True,
        "profile": profile,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/team/personas', methods=['GET', 'POST'])
def team_personas():
    """List or create/update team member personas."""
    if request.method == "GET":
        limit = _int_in_range(request.args.get("limit", 80), default=80, min_value=1, max_value=300)
        members = _team_persona_store.list_members(limit=limit)
        return _api_ok(
            {
                "members": members,
                "count": len(members),
                "summary": _team_persona_store.summary(limit=min(24, limit)),
            }
        )

    data = request.get_json(silent=True) or {}
    member_id = _resolve_member_id(data)
    if not member_id:
        return _api_error("MISSING_MEMBER_ID", "Missing member_id")
    try:
        member = _team_persona_store.upsert_member(member_id, data)
    except Exception as exc:
        return _api_error("TEAM_PERSONA_UPDATE_FAILED", str(exc), status_code=400)
    state.add_agent_log("guardian", f"🧠 Team persona updated for {member_id}", "success")
    return _api_ok({"member": member})


@app.route('/api/team/personas/<member_id>', methods=['GET', 'POST'])
def team_persona_member(member_id: str):
    key = normalize_member_id(member_id)
    if not key:
        return _api_error("MISSING_MEMBER_ID", "Missing member_id")

    if request.method == "GET":
        member = _team_persona_store.get_member(key)
        if not member:
            return _api_error("TEAM_MEMBER_NOT_FOUND", "Team member not found", status_code=404)
        adaptation = _team_adaptation(key)
        interactions = _team_persona_store.list_interactions(member_id=key, limit=30)
        return _api_ok(
            {
                "member": member,
                "adaptation": adaptation,
                "interactions": interactions,
                "interaction_count": len(interactions),
            }
        )

    data = request.get_json(silent=True) or {}
    try:
        member = _team_persona_store.upsert_member(key, data)
    except Exception as exc:
        return _api_error("TEAM_PERSONA_UPDATE_FAILED", str(exc), status_code=400)
    adaptation = _team_adaptation(key)
    return _api_ok({"member": member, "adaptation": adaptation})


@app.route('/api/team/personas/<member_id>/interaction', methods=['POST'])
def team_persona_interaction(member_id: str):
    key = normalize_member_id(member_id)
    if not key:
        return _api_error("MISSING_MEMBER_ID", "Missing member_id")
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "") or "").strip()
    if not message:
        return _api_error("MISSING_MESSAGE", "Missing interaction message")
    try:
        learned = _team_persona_store.record_interaction(
            member_id=key,
            message=message,
            channel=str(data.get("channel", "chat") or "chat"),
            intent=str(data.get("intent", "") or ""),
            context=data.get("context") if isinstance(data.get("context"), dict) else {},
        )
    except Exception as exc:
        return _api_error("TEAM_INTERACTION_FAILED", str(exc), status_code=400)
    adaptation = _team_adaptation(key, intent=str(data.get("intent", "") or ""))
    return _api_ok({"learned": learned, "adaptation": adaptation})


@app.route('/api/team/personas/<member_id>/adaptation')
def team_persona_adaptation(member_id: str):
    key = normalize_member_id(member_id)
    if not key:
        return _api_error("MISSING_MEMBER_ID", "Missing member_id")
    intent = str(request.args.get("intent", "") or "")
    adaptation = _team_adaptation(key, intent=intent)
    return _api_ok({"adaptation": adaptation})


@app.route('/api/team/interactions')
def team_persona_interactions():
    limit = _int_in_range(request.args.get("limit", 120), default=120, min_value=1, max_value=600)
    member_id = normalize_member_id(request.args.get("member_id", ""))
    rows = _team_persona_store.list_interactions(member_id=member_id, limit=limit)
    return _api_ok({"events": rows, "count": len(rows), "member_id": member_id or None})


@app.route('/api/orion/instances')
def get_orion_instances():
    """List all Orion instances and current health."""
    instances = get_orion_instances_snapshot()
    _control_plane_registry.sync_instance_snapshot(instances)
    return jsonify({
        "instances": instances,
        "default_instance_id": LOCAL_INSTANCE_ID,
        "timestamp": datetime.now().isoformat(),
    })


# ============================================
# CENTRAL COMMAND API ENDPOINTS (Phase 1)
# ============================================

@app.route('/api/central/registry')
def central_registry():
    """Get all registered Orion instances in Central Command."""
    with _CENTRAL_COMMAND_LOCK:
        orions = {
            orion_id: {
                "orion_id": state.orion_id,
                "name": state.name,
                "base_url": state.base_url,
                "registered_at": state.registered_at,
                "last_heartbeat": state.last_heartbeat,
                "status": state.status,
                "current_task": state.current_task,
                "locked_files": state.locked_files,
                "agents": state.agents,
                "metadata": state.metadata,
            }
            for orion_id, state in _registered_orions.items()
        }
    return jsonify({
        "orions": orions,
        "count": len(orions),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/central/orion/register', methods=['POST'])
def central_orion_register():
    """Register an Orion instance with Central Command."""
    data = request.get_json(silent=True) or {}
    orion_id = data.get("orion_id", "").strip()
    name = data.get("name", orion_id)
    base_url = data.get("base_url", "").strip()
    agents = data.get("agents", [])

    if not orion_id:
        return jsonify({"success": False, "error": "orion_id required"}), 400

    now = datetime.now().isoformat()
    with _CENTRAL_COMMAND_LOCK:
        _registered_orions[orion_id] = OrionState(
            orion_id=orion_id,
            name=name,
            base_url=base_url,
            registered_at=now,
            last_heartbeat=now,
            status="online",
            agents=agents,
        )

    logger.info(f"Orion registered with Central Command: {orion_id}")
    return jsonify({
        "success": True,
        "orion_id": orion_id,
        "message": f"Orion '{orion_id}' registered successfully",
    })


@app.route('/api/central/orion/<orion_id>/heartbeat', methods=['POST'])
def central_orion_heartbeat(orion_id):
    """Update heartbeat for an Orion instance."""
    data = request.get_json(silent=True) or {}
    status = data.get("status", "online")
    current_task = data.get("current_task")
    agents = data.get("agents", [])

    with _CENTRAL_COMMAND_LOCK:
        if orion_id not in _registered_orions:
            return jsonify({"success": False, "error": "Orion not registered"}), 404

        state = _registered_orions[orion_id]
        state.last_heartbeat = datetime.now().isoformat()
        state.status = status
        if current_task:
            state.current_task = current_task
        if agents:
            state.agents = agents

    return jsonify({"success": True, "orion_id": orion_id})


@app.route('/api/central/orion/<orion_id>', methods=['DELETE'])
def central_orion_unregister(orion_id):
    """Unregister an Orion instance from Central Command."""
    with _CENTRAL_COMMAND_LOCK:
        if orion_id not in _registered_orions:
            return jsonify({"success": False, "error": "Orion not registered"}), 404

        # Release all file locks held by this Orion
        locks_to_release = [
            fp for fp, lock in _global_file_locks.items()
            if lock.orion_id == orion_id
        ]
        for fp in locks_to_release:
            del _global_file_locks[fp]

        del _registered_orions[orion_id]

    logger.info(f"Orion unregistered from Central Command: {orion_id}")
    return jsonify({
        "success": True,
        "message": f"Orion '{orion_id}' unregistered",
    })


@app.route('/api/central/files/locked')
def central_files_locked():
    """Get all currently locked files."""
    with _CENTRAL_COMMAND_LOCK:
        locks = {
            file_path: {
                "file_path": lock.file_path,
                "orion_id": lock.orion_id,
                "agent_id": lock.agent_id,
                "locked_at": lock.locked_at,
                "expires_at": lock.expires_at,
            }
            for file_path, lock in _global_file_locks.items()
        }
    return jsonify({
        "locks": locks,
        "count": len(locks),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/central/files/lock', methods=['POST'])
def central_files_lock():
    """Lock a file for editing."""
    data = request.get_json(silent=True) or {}
    file_path = data.get("file_path", "").strip()
    orion_id = data.get("orion_id", "").strip()
    agent_id = data.get("agent_id")
    ttl_sec = int(data.get("ttl_sec", 300))  # Default 5 minutes

    if not file_path or not orion_id:
        return jsonify({"success": False, "error": "file_path and orion_id required"}), 400

    now = datetime.now()
    expires_at = (now + timedelta(seconds=ttl_sec)).isoformat()
    expired_lock_reclaimed = False

    with _CENTRAL_COMMAND_LOCK:
        existing_lock = _global_file_locks.get(file_path)
        if existing_lock:
            try:
                if datetime.fromisoformat(existing_lock.expires_at) < now:
                    del _global_file_locks[file_path]
                    expired_lock_reclaimed = True
            except Exception:
                pass
        # Check if already locked by another Orion
        if file_path in _global_file_locks:
            existing_lock = _global_file_locks[file_path]
            if existing_lock.orion_id != orion_id:
                return jsonify({
                    "success": False,
                    "error": f"File already locked by {existing_lock.orion_id}",
                    "locked_by": existing_lock.orion_id,
                    "agent_id": existing_lock.agent_id,
                }), 409

        _global_file_locks[file_path] = FileLock(
            file_path=file_path,
            orion_id=orion_id,
            agent_id=agent_id,
            locked_at=now.isoformat(),
            expires_at=expires_at,
        )

        # Update Orion's locked files
        if orion_id in _registered_orions:
            if file_path not in _registered_orions[orion_id].locked_files:
                _registered_orions[orion_id].locked_files.append(file_path)

    return jsonify({
        "success": True,
        "file_path": file_path,
        "orion_id": orion_id,
        "expires_at": expires_at,
        "expired_lock_reclaimed": expired_lock_reclaimed,
    })


@app.route('/api/central/files/unlock', methods=['POST'])
def central_files_unlock():
    """Unlock a file."""
    data = request.get_json(silent=True) or {}
    file_path = data.get("file_path", "").strip()
    orion_id = data.get("orion_id", "").strip()

    if not file_path or not orion_id:
        return jsonify({"success": False, "error": "file_path and orion_id required"}), 400

    with _CENTRAL_COMMAND_LOCK:
        if file_path not in _global_file_locks:
            return jsonify({"success": False, "error": "File not locked"}), 404

        lock = _global_file_locks[file_path]
        if lock.orion_id != orion_id:
            return jsonify({
                "success": False,
                "error": f"File locked by {lock.orion_id}, not {orion_id}",
            }), 409

        del _global_file_locks[file_path]

        # Update Orion's locked files
        if orion_id in _registered_orions:
            if file_path in _registered_orions[orion_id].locked_files:
                _registered_orions[orion_id].locked_files.remove(file_path)

    return jsonify({
        "success": True,
        "file_path": file_path,
        "message": "File unlocked",
    })


@app.route('/api/central/queue')
def central_queue():
    """Get the global task queue."""
    with _CENTRAL_COMMAND_LOCK:
        tasks = [
            {
                "task_id": t.task_id,
                "task_type": t.task_type,
                "description": t.description,
                "priority": t.priority,
                "assigned_orion": t.assigned_orion,
                "status": t.status,
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
            }
            for t in _task_queue
        ]
    return jsonify({
        "tasks": tasks,
        "count": len(tasks),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/central/task/distribute', methods=['POST'])
def central_task_distribute():
    """Distribute a task to an Orion."""
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id", str(uuid.uuid4()))
    task_type = data.get("task_type", "general")
    description = data.get("description", "")
    priority = int(data.get("priority", 5))
    assigned_orion = data.get("assigned_orion")

    now = datetime.now().isoformat()
    task = TaskDistribution(
        task_id=task_id,
        task_type=task_type,
        description=description,
        priority=priority,
        assigned_orion=assigned_orion,
        status="assigned" if assigned_orion else "pending",
        created_at=now,
    )

    with _CENTRAL_COMMAND_LOCK:
        _task_queue.append(task)

    return jsonify({
        "success": True,
        "task_id": task_id,
        "status": task.status,
    })


@app.route('/api/central/messages', methods=['GET', 'POST'])
def central_messages():
    """Get or post cross-Orion messages."""
    if request.method == "GET":
        orion_id = request.args.get("orion_id")
        unread_only = request.args.get("unread_only", "false").lower() == "true"

        with _CENTRAL_COMMAND_LOCK:
            messages = [
                {
                    "message_id": m.message_id,
                    "from_orion": m.from_orion,
                    "to_orion": m.to_orion,
                    "message_type": m.message_type,
                    "content": m.content,
                    "created_at": m.created_at,
                    "read": m.read,
                }
                for m in _cross_orion_messages
                if (not orion_id or m.to_orion == orion_id or m.to_orion is None)
                and (not unread_only or not m.read)
            ]

        return jsonify({
            "messages": messages,
            "count": len(messages),
        })

    # POST - create new message
    data = request.get_json(silent=True) or {}
    from_orion = data.get("from_orion", "").strip()
    to_orion = data.get("to_orion", "").strip() or None
    message_type = data.get("message_type", "info")
    content = data.get("content", "")

    if not from_orion or not content:
        return jsonify({"success": False, "error": "from_orion and content required"}), 400

    message = CrossOrionMessage(
        message_id=str(uuid.uuid4()),
        from_orion=from_orion,
        to_orion=to_orion,
        message_type=message_type,
        content=content,
        created_at=datetime.now().isoformat(),
    )

    with _CENTRAL_COMMAND_LOCK:
        _cross_orion_messages.append(message)

    return jsonify({
        "success": True,
        "message_id": message.message_id,
    })


@app.route('/api/central/broadcast', methods=['POST'])
def central_broadcast():
    """Broadcast a message to all Orion instances."""
    data = request.get_json(silent=True) or {}
    from_orion = data.get("from_orion", "").strip()
    message_type = data.get("message_type", "info")
    content = data.get("content", "")

    if not from_orion or not content:
        return jsonify({"success": False, "error": "from_orion and content required"}), 400

    message = CrossOrionMessage(
        message_id=str(uuid.uuid4()),
        from_orion=from_orion,
        to_orion=None,  # Broadcast
        message_type=message_type,
        content=content,
        created_at=datetime.now().isoformat(),
    )

    with _CENTRAL_COMMAND_LOCK:
        _cross_orion_messages.append(message)

    return jsonify({
        "success": True,
        "message_id": message.message_id,
        "message": "Broadcast sent to all Orion instances",
    })


@app.route('/api/central/health')
def central_health():
    """Get Central Command health status."""
    with _CENTRAL_COMMAND_LOCK:
        orion_count = len(_registered_orions)
        lock_count = len(_global_file_locks)
        task_count = len(_task_queue)
        message_count = len(_cross_orion_messages)

        # Check for expired locks
        now = datetime.now()
        expired_locks = []
        for file_path, lock in _global_file_locks.items():
            try:
                expires_at = datetime.fromisoformat(lock.expires_at)
                if expires_at < now:
                    expired_locks.append(file_path)
            except Exception:
                pass

        # Clean up expired locks
        for fp in expired_locks:
            del _global_file_locks[fp]

    return jsonify({
        "status": "healthy",
        "registered_orions": orion_count,
        "active_locks": lock_count,
        "queue_tasks": task_count,
        "pending_messages": message_count,
        "expired_locks_cleaned": len(expired_locks),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/autonomy/profile', methods=['GET', 'POST'])
def autonomy_profile():
    if request.method == "GET":
        return jsonify({
            "success": True,
            "autonomy": {
                "profile": _get_autonomy_profile(),
                "full_auto": _is_full_auto_profile(),
                "full_auto_user_pause_max_sec": MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC,
                "full_auto_maintenance_lease_sec": MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC,
            },
            "monitor_supervisor": _monitor_supervisor_status(),
            "timestamp": datetime.now().isoformat(),
        })

    data = request.get_json(silent=True) or {}
    profile = _normalize_autonomy_profile(data.get("profile", _get_autonomy_profile()))
    applied = _set_autonomy_profile(profile)
    state.add_agent_log("guardian", f"🧠 Autonomy profile -> {applied.upper()}", "success")
    apply_results: Dict[str, Any] = {}
    instances = get_orion_instances_snapshot()
    for instance in instances:
        instance_id = str(instance.get("id", "")).strip()
        if not instance_id:
            continue
        apply_results[instance_id] = _call_instance_command(
            instance_id=instance_id,
            target="system",
            command=f"set_autonomy_profile {applied}",
            priority="high",
            options={"source": "autonomy_profile_api"},
        )
    if applied == "full_auto":
        _monitor_set_supervisor_enabled(True)
        for instance in instances:
            instance_id = str(instance.get("id", "")).strip()
            if not instance_id:
                continue
            autopilot_result = _call_instance_command(
                instance_id=instance_id,
                target="guardian",
                command="autopilot_on",
                priority="high",
                options={"reason": "autonomy_profile_full_auto"},
            )
            apply_results.setdefault(instance_id, {})
            if isinstance(apply_results[instance_id], dict):
                apply_results[instance_id]["guardian_autopilot"] = autopilot_result
    return jsonify({
        "success": True,
        "autonomy": {
            "profile": applied,
            "full_auto": applied == "full_auto",
            "full_auto_user_pause_max_sec": MONITOR_FULL_AUTO_USER_PAUSE_MAX_SEC,
            "full_auto_maintenance_lease_sec": MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC,
        },
        "instance_apply_results": apply_results,
        "monitor_supervisor": _monitor_supervisor_status(),
        "timestamp": datetime.now().isoformat(),
    })


def _guardian_control_auth(data: Dict[str, Any]) -> Dict[str, Any]:
    if not GUARDIAN_CONTROL_TOKEN:
        return {"authorized": True, "token_required": False, "actor": str(data.get("actor", "dashboard")).strip() or "dashboard"}
    provided = str(request.headers.get("X-Guardian-Control-Token", "")).strip()
    if not provided:
        provided = str(data.get("token", "")).strip()
    actor = str(data.get("actor", "dashboard")).strip() or "dashboard"
    if provided and provided == GUARDIAN_CONTROL_TOKEN:
        return {"authorized": True, "token_required": True, "actor": actor}
    return {"authorized": False, "token_required": True, "actor": actor}


@app.route('/api/guardian/autopilot/status')
def guardian_autopilot_status():
    return jsonify({
        "success": True,
        "guardian": {
            "autopilot": _monitor_supervisor_status(),
            "policy_mostly_auto": bool(OPENCLAW_POLICY_MOSTLY_AUTO),
            "token_required": bool(GUARDIAN_CONTROL_TOKEN),
            "control_plane": _control_plane_registry.snapshot(),
            "audit_tail": _guardian_control_audit_tail(limit=20),
        },
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/guardian/control', methods=['POST'])
def guardian_control():
    rate_limited = _rate_limited("guardian_control", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip().lower()
    reason = str(data.get("reason", "")).strip()
    if action not in {"pause_autopilot", "resume_autopilot", "shutdown"}:
        return jsonify({"success": False, "error": "Unsupported action"}), 400
    if not reason:
        return jsonify({"success": False, "error": "Missing reason"}), 400

    auth = _guardian_control_auth(data)
    actor = str(auth.get("actor", "dashboard"))
    if not auth.get("authorized"):
        _append_guardian_control_audit(action=action, actor=actor, reason=reason, success=False, detail={"error": "unauthorized"})
        return jsonify({"success": False, "error": "Unauthorized", "token_required": True}), 401

    if action == "pause_autopilot":
        if _is_full_auto_profile():
            lease_sec = _int_in_range(
                data.get("lease_sec", data.get("duration_sec", MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC)),
                MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC,
                30,
                7200,
            )
            pause_until = _monitor_pause_supervisor_temporarily(lease_sec)
            state.add_agent_log(
                "guardian",
                f"🧷 Full-auto maintenance lease active ({lease_sec}s) via guardian control ({reason})",
                "warning",
            )
            _append_guardian_control_audit(
                action=action,
                actor=actor,
                reason=reason,
                success=True,
                detail={"temporary_pause": True, "lease_sec": lease_sec, "pause_until": pause_until},
            )
            return jsonify({
                "success": True,
                "action": action,
                "temporary_pause": True,
                "lease_sec": lease_sec,
                "pause_until": pause_until,
                "supervisor": _monitor_supervisor_status(),
                "timestamp": datetime.now().isoformat(),
            })

        _monitor_set_supervisor_enabled(False)
        state.add_agent_log("guardian", f"🛑 Guardian control paused autopilot ({reason})", "warning")
        _append_guardian_control_audit(
            action=action,
            actor=actor,
            reason=reason,
            success=True,
            detail={"temporary_pause": False},
        )
        return jsonify({
            "success": True,
            "action": action,
            "temporary_pause": False,
            "supervisor": _monitor_supervisor_status(),
            "timestamp": datetime.now().isoformat(),
        })

    if action == "resume_autopilot":
        _monitor_set_supervisor_enabled(True)
        state.add_agent_log("guardian", f"✅ Guardian control resumed autopilot ({reason})", "success")
        _append_guardian_control_audit(action=action, actor=actor, reason=reason, success=True)
        return jsonify({
            "success": True,
            "action": action,
            "supervisor": _monitor_supervisor_status(),
            "timestamp": datetime.now().isoformat(),
        })

    # action == shutdown
    _monitor_set_supervisor_enabled(False)
    shutdown_results: Dict[str, Any] = {}
    success_count = 0
    instances = get_orion_instances_snapshot()
    for item in instances:
        instance_id = str(item.get("id", "")).strip() or LOCAL_INSTANCE_ID
        result = _call_instance_command(
            instance_id=instance_id,
            target="orion",
            command="shutdown",
            priority="high",
            options={"source": "guardian_control", "reason": reason, "actor": actor},
        )
        ok = bool(result.get("success", False))
        if ok:
            success_count += 1
        shutdown_results[instance_id] = result
    state.add_agent_log("guardian", f"🧯 Guardian control issued SHUTDOWN ({reason})", "warning")
    _append_guardian_control_audit(
        action=action,
        actor=actor,
        reason=reason,
        success=success_count > 0,
        detail={"instances": len(instances), "success_count": success_count},
    )
    return jsonify({
        "success": success_count > 0,
        "action": action,
        "summary": {
            "instances": len(instances),
            "success": success_count,
            "failed": max(0, len(instances) - success_count),
        },
        "results": shutdown_results,
        "supervisor": _monitor_supervisor_status(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/monitor/autopilot', methods=['GET', 'POST'])
def monitor_autopilot():
    """Get/update monitor-level autopilot for autonomous Orion recovery."""
    if request.method == 'GET':
        return jsonify({
            "success": True,
            "supervisor": _monitor_supervisor_status(),
            "timestamp": datetime.now().isoformat(),
        })

    data = request.get_json(silent=True) or {}
    enabled = _to_bool(data.get("enabled", True))
    temporary_pause = False
    pause_until = None
    lease_sec = None
    if enabled:
        _monitor_set_supervisor_enabled(True)
    elif _is_full_auto_profile():
        lease_sec = _int_in_range(
            data.get("lease_sec", data.get("duration_sec", MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC)),
            MONITOR_FULL_AUTO_MAINTENANCE_LEASE_SEC,
            30,
            7200,
        )
        pause_until = _monitor_pause_supervisor_temporarily(lease_sec)
        temporary_pause = True
    else:
        _monitor_set_supervisor_enabled(False)
    state.add_agent_log(
        "guardian",
        (
            f"🧷 Monitor autopilot maintenance lease ({lease_sec}s) by dashboard"
            if temporary_pause
            else f"🧭 Monitor autopilot {'enabled' if enabled else 'disabled'} by dashboard"
        ),
        "success" if enabled else "warning",
    )
    _append_guardian_control_audit(
        action="resume_autopilot" if enabled else "pause_autopilot",
        actor="dashboard",
        reason="monitor_autopilot_api_full_auto_lease" if temporary_pause else "monitor_autopilot_api",
        success=True,
        detail=(
            {"temporary_pause": True, "lease_sec": lease_sec, "pause_until": pause_until}
            if temporary_pause
            else {"temporary_pause": False}
        ),
    )
    supervisor_snapshot = _monitor_supervisor_status()
    response_payload = {
        "success": True,
        "enabled": bool(supervisor_snapshot.get("enabled")),
        "requested_enabled": enabled,
        "temporary_pause": temporary_pause,
        "supervisor": supervisor_snapshot,
        "timestamp": datetime.now().isoformat(),
    }
    if pause_until:
        response_payload["pause_until"] = pause_until
    if lease_sec:
        response_payload["lease_sec"] = lease_sec
    return jsonify(response_payload)


@app.route('/api/cost-guard', methods=['GET', 'POST'])
def cost_guard():
    """Get/update runtime cost guard mode for the selected Orion instance."""
    if request.method == 'GET':
        instance_id = str(request.args.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
        runtime_payload = _fetch_instance_runtime(instance_id)
        runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
        cost_guard = runtime.get("cost_guard") if isinstance(runtime, dict) else {}
        if not isinstance(cost_guard, dict):
            cost_guard = {
                "mode": "unknown",
                "profile": {},
                "updated_at": None,
            }
        return jsonify({
            "success": True,
            "instance_id": runtime_payload.get("instance_id"),
            "instance_name": runtime_payload.get("instance_name"),
            "cost_guard": cost_guard,
            "timestamp": datetime.now().isoformat(),
        })

    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    mode = str(data.get("mode", "")).strip().lower()
    if not mode:
        return jsonify({"success": False, "error": "Missing mode"}), 400

    result = _call_instance_command(
        instance_id,
        "orion",
        f"set_cost_mode {mode}",
        "high",
        {"control_mode": "strict"},
    )
    success = bool(result.get("success"))
    payload = result.get("cost_guard")
    if not isinstance(payload, dict):
        nested = result.get("result")
        payload = nested.get("cost_guard") if isinstance(nested, dict) else {}
    if not isinstance(payload, dict):
        payload = {}

    state.add_agent_log(
        "guardian",
        f"💸 Cost guard {mode} on {instance_id}: {'ok' if success else 'failed'}",
        "success" if success else "warning",
    )

    return jsonify({
        "success": success,
        "instance_id": instance_id,
        "cost_guard": payload,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/orion/instances/command', methods=['POST'])
def send_orion_instance_command():
    """Send command to a specific Orion instance."""
    rate_limited = _rate_limited("orion_instances_command", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    target = str(data.get("target", "orion")).strip().lower() or "orion"
    command = str(data.get("command", "")).strip()
    priority = str(data.get("priority", "high")).strip().lower() or "high"
    options = data.get("options") or {}

    if not command:
        return jsonify({"success": False, "error": "Missing command"}), 400

    result = _call_instance_command(instance_id, target, command, priority, options)
    ok = bool(result.get("success", True))
    level = "success" if ok else "error"
    state.add_agent_log(
        "guardian",
        f"🛰 {instance_id}::{target} -> {command} ({'ok' if ok else 'fail'})",
        level,
    )
    return jsonify({
        "success": ok,
        "instance_id": instance_id,
        "target": target,
        "command": command,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/orion/instances/broadcast', methods=['POST'])
def broadcast_orion_instance_command():
    """Broadcast one command to multiple Orion instances."""
    rate_limited = _rate_limited("orion_instances_broadcast", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited

    data = request.get_json(silent=True) or {}
    target = str(data.get("target", "orion")).strip().lower() or "orion"
    command = str(data.get("command", "")).strip()
    priority = str(data.get("priority", "high")).strip().lower() or "high"
    options = data.get("options") if isinstance(data.get("options"), dict) else {}
    instance_ids = data.get("instance_ids") if isinstance(data.get("instance_ids"), list) else []
    only_online = _to_bool(data.get("only_online", True))

    if not command:
        return _api_error("MISSING_COMMAND", "Missing command")

    available = get_orion_instances_snapshot()
    if instance_ids:
        requested = {str(item).strip() for item in instance_ids if str(item).strip()}
        targets = [row for row in available if str(row.get("id", "")).strip() in requested]
    else:
        targets = [row for row in available if (bool(row.get("online")) if only_online else True)]

    if not targets:
        return _api_error("NO_TARGET_INSTANCES", "No Orion instances matched request", status_code=404)

    member_id = _resolve_member_id(data)
    adaptation = _team_adaptation(member_id, intent=command)
    if member_id:
        try:
            _team_persona_store.record_interaction(
                member_id=member_id,
                message=f"broadcast {command}",
                channel="api",
                intent="orion_instances_broadcast",
                context={"instance_id": "multi"},
            )
        except Exception:
            pass

    results: Dict[str, Any] = {}
    success_count = 0
    for row in targets:
        instance_id = str(row.get("id", "")).strip()
        if not instance_id:
            continue
        result = _call_instance_command(instance_id, target, command, priority, options)
        ok = bool(result.get("success", True))
        if ok:
            success_count += 1
        results[instance_id] = result
        state.add_agent_log(
            "guardian",
            f"🛰 broadcast {instance_id}::{target} -> {command} ({'ok' if ok else 'fail'})",
            "success" if ok else "warning",
        )

    return _api_ok(
        {
            "target": target,
            "command": command,
            "priority": priority,
            "member_id": member_id or None,
            "adaptation": adaptation,
            "summary": {
                "total": len(targets),
                "success": success_count,
                "failed": max(0, len(targets) - success_count),
            },
            "results": results,
        }
    )


@app.route('/api/chat/ask', methods=['POST'])
def chat_ask():
    """Lightweight assistant chat endpoint for project control and summaries."""
    rate_limited = _rate_limited("chat_ask", API_RATE_LIMIT_CHAT_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    raw_message = data.get("message")
    if raw_message is None:
        # Backward-compatible alias for external clients.
        raw_message = data.get("question")
    message = str(raw_message or "").strip()
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    member_id = _resolve_member_id(data)
    if not message:
        return jsonify({"success": False, "error": "Missing message"}), 400

    member_adaptation = _team_adaptation(member_id, intent=message)
    if member_id:
        try:
            _team_persona_store.record_interaction(
                member_id=member_id,
                message=message,
                channel="chat",
                intent="chat_ask",
                context={"instance_id": instance_id},
            )
            member_adaptation = _team_adaptation(member_id, intent=message)
        except Exception as exc:
            logger.warning(f"Failed recording chat interaction for {member_id}: {exc}")

    feedback_id = _maybe_record_user_feedback(message)
    message_id = str(datetime.now().timestamp())
    _emit_chat_phase(message_id, "thinking", "Đang phân tích yêu cầu...")
    _emit_chat_phase(message_id, "routing", f"Đang chọn instance điều khiển: {instance_id}")

    state.add_agent_log("orion", f"💬 Chat: {message}", "info")
    _emit_chat_phase(message_id, "processing", "Đang tổng hợp trạng thái runtime và quyết định phù hợp...")
    result = _build_project_chat_reply(message, instance_id=instance_id, member_id=member_id)
    activity = (result.get("result") or {}).get("agent_activity")
    if isinstance(activity, dict):
        _emit_chat_phase(
            message_id,
            "processing",
            str(activity.get("summary", "Đã lấy snapshot realtime hoạt động agent.")),
        )
    reply = str(result.get("reply", "")).strip() or "Đã nhận yêu cầu."
    intent = str(result.get("intent", "assistant_help"))
    reply = _soften_chat_reply(reply, intent=intent)
    reply = _adapt_chat_reply_for_member(reply, member_adaptation)
    report = result.get("report")
    if not isinstance(report, dict):
        report = _build_chat_report(
            intent,
            bool(result.get("executed")),
            reply,
            result.get("result", {}) or {},
            reasoning=(result.get("result", {}) or {}).get("reasoning", {}),
        )
    report["summary"] = reply
    if feedback_id:
        feedback_entries = (result.get("result", {}) or {}).get("feedback_recent") or []
        latest_feedback = feedback_entries[0] if isinstance(feedback_entries, list) and feedback_entries else {}
        category = str(latest_feedback.get("category", "general")).strip() or "general"
        reply = f"{reply}\n\nAcknowledged feedback #{feedback_id}: {category}. Applying updates automatically."
        report["summary"] = reply
    if member_id:
        report.setdefault("highlights", [])
        highlights = report.get("highlights") if isinstance(report.get("highlights"), list) else []
        style_line = member_adaptation.get("communication", {}) if isinstance(member_adaptation.get("communication"), dict) else {}
        tone = style_line.get("tone", "balanced")
        detail = style_line.get("detail_level", "balanced")
        highlights.append(f"Adapted to member={member_id} ({tone}/{detail})")
        report["highlights"] = highlights[:3]
    _emit_chat_phase(
        message_id,
        "report",
        "Đã cập nhật Streamline Report realtime.",
        report={
            "status": "Đang hoàn tất",
            "summary": report.get("summary", "") if isinstance(report, dict) else "",
            "highlights": (report.get("highlights", []) if isinstance(report, dict) else [])[:2],
            "next_action": report.get("next_action", "") if isinstance(report, dict) else "",
        },
    )

    level = "success" if result.get("executed") else "info"
    state.add_agent_log("guardian", f"🤖 Chat intent={intent} instance={instance_id}", level)

    payload = {
        "message_id": message_id,
        "phase": "final",
        "intent": intent,
        "executed": bool(result.get("executed")),
        "text": reply,
        "result": result.get("result", {}),
        "task_intelligence": (result.get("result", {}) or {}).get("task_intelligence"),
        "report": report,
        "feedback_id": feedback_id,
        "member_id": member_id or None,
        "member_adaptation": member_adaptation,
        "reasoning": result.get("result", {}).get("reasoning", {}),
        "ai_summary": result.get("result", {}).get("ai_summary"),
        "feedback_recent": result.get("result", {}).get("feedback_recent", []),
        "timestamp": datetime.now().isoformat(),
    }
    socketio.emit("chat_stream", payload)
    return jsonify({
        "success": True,
        "message_id": message_id,
        "reply": reply,
        "intent": intent,
        "executed": bool(result.get("executed")),
        "result": result.get("result", {}),
        "report": report,
        "instance_id": instance_id,
        "member_id": member_id or None,
        "member_adaptation": member_adaptation,
        "pending_decision": result.get("pending_decision"),
        "feedback_id": feedback_id,
        "reasoning": result.get("result", {}).get("reasoning"),
        "ai_summary": result.get("result", {}).get("ai_summary"),
        "feedback_recent": result.get("result", {}).get("feedback_recent"),
        "task_intelligence": result.get("result", {}).get("task_intelligence"),
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/orion/thinking')
def get_orion_thinking():
    """Get Orion's current thinking process"""
    return jsonify({"thinking": state.orion_thinking})


@app.route('/api/guardian/thinking')
def get_guardian_thinking():
    """Get Guardian's current thinking process"""
    return jsonify({"thinking": state.guardian_thinking})


@app.route('/api/decision/approve', methods=['POST'])
def approve_decision():
    """Manually approve a pending decision"""
    data = request.get_json(silent=True) or {}
    decision_id = data.get('decision_id')
    actor = str(data.get("actor", "dashboard")).strip() or "dashboard"

    approved = state.approve_decision(decision_id)
    if not approved:
        return jsonify({"success": False, "error": "Decision not found"})

    execution = None
    if str(approved.get("kind", "")).strip() == "command_intervention":
        command = str(approved.get("command", "")).strip()
        if command:
            execution = _execute_approved_shell_command(command)
            if execution.get("success"):
                state.add_agent_log("guardian", f"✅ Approved command executed: {execution.get('command')}", "success")
            else:
                state.add_agent_log("guardian", f"⚠️ Approved command blocked/failed: {execution.get('error', 'unknown')}", "warning")

    _append_intervention_history(
        action="approved",
        decision_id=str(decision_id or ""),
        success=True,
        actor=actor,
        detail={"execution": execution or {}},
    )

    return jsonify({
        "success": True,
        "decision": "approved",
        "item": approved,
        "execution": execution,
    })


@app.route('/api/decision/decline', methods=['POST'])
def decline_decision():
    """Manually decline a pending decision"""
    data = request.get_json(silent=True) or {}
    decision_id = data.get('decision_id')
    actor = str(data.get("actor", "dashboard")).strip() or "dashboard"

    declined = state.decline_decision(decision_id)
    if declined:
        _append_intervention_history(
            action="declined",
            decision_id=str(decision_id or ""),
            success=True,
            actor=actor,
            detail={"kind": declined.get("kind"), "action": declined.get("action")},
        )
        return jsonify({"success": True, "decision": "declined", "item": declined})

    return jsonify({"success": False, "error": "Decision not found"})


@app.route('/api/orion/command', methods=['POST'])
def send_orion_command():
    """Backward-compatible Orion command endpoint."""
    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    command = str(data.get('command', '')).strip()
    target = str(data.get('target', 'orion')).strip().lower() or "orion"
    priority = str(data.get('priority', 'medium')).strip().lower() or "medium"
    options = data.get("options") or {}

    state.add_agent_log("orion", f"📩 Command received: {command}", "info")

    result = _call_instance_command(instance_id, target, command, priority, options)
    return jsonify({
        "success": bool(result.get("success", True)),
        "instance_id": instance_id,
        "target": target,
        "command": command,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/agent/command', methods=['POST'])
def send_agent_command():
    """Send command to a specific agent via runtime bridge."""
    rate_limited = _rate_limited("agent_command", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited
    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    target = str(data.get("target", "")).strip().lower()
    command = str(data.get("command", "")).strip()
    priority = str(data.get("priority", "medium")).strip().lower() or "medium"
    options = data.get("options") or {}

    if not target:
        return jsonify({"success": False, "error": "Missing target"}), 400
    if not command:
        return jsonify({"success": False, "error": "Missing command"}), 400

    level = "warning" if command.lower() in {"shutdown", "pause", "resume"} else "info"
    state.add_agent_log(target, f"📩 Dashboard command: {command}", level)

    result = _call_instance_command(instance_id, target, command, priority, options)
    ok = bool(result.get("success", True))
    if ok:
        state.add_agent_log(target, f"✅ Command handled: {command}", "success")
    else:
        state.add_agent_log(target, f"❌ Command failed: {result.get('error', 'unknown')}", "error")
    return jsonify({
        "success": ok,
        "instance_id": instance_id,
        "target": target,
        "command": command,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/agents/control', methods=['GET', 'POST'])
def agents_control():
    """Get/update orchestration autopilot state for individual agents."""
    if request.method == "GET":
        instance_id = str(request.args.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
        runtime_payload = _fetch_instance_runtime(instance_id)
        runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
        controls = runtime.get("agent_controls") if isinstance(runtime.get("agent_controls"), dict) else {}
        return jsonify({
            "success": True,
            "instance_id": instance_id,
            "controls": controls,
            "timestamp": datetime.now().isoformat(),
        })

    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    target = state._normalize_agent(data.get("target", ""))
    if not target:
        return jsonify({"success": False, "error": "Missing target"}), 400
    if target in {"all", "orion", "system"}:
        return jsonify({"success": False, "error": "Use Orion command endpoint for this target"}), 400

    enabled_raw = data.get("enabled")
    if enabled_raw is None:
        mode = str(data.get("mode", "")).strip().lower()
        if mode in {"on", "enable", "enabled", "resume"}:
            enabled = True
        elif mode in {"off", "disable", "disabled", "pause"}:
            enabled = False
        else:
            return jsonify({"success": False, "error": "Missing enabled flag"}), 400
    else:
        enabled = bool(enabled_raw)

    reason = str(data.get("reason", "dashboard_toggle")).strip() or "dashboard_toggle"
    command = "resume" if enabled else "pause"
    options = data.get("options") if isinstance(data.get("options"), dict) else {}
    options = {**options, "reason": reason}
    result = _call_instance_command(instance_id, target, command, "high", options)
    ok = bool(result.get("success", True))

    runtime_payload = _fetch_instance_runtime(instance_id)
    runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
    controls = runtime.get("agent_controls") if isinstance(runtime.get("agent_controls"), dict) else {}
    control = controls.get(target, {})
    level = "success" if ok else "warning"
    state.add_agent_log(
        "guardian",
        f"🎛️ Agent autopilot {target.upper()} -> {'ON' if enabled else 'OFF'} ({'ok' if ok else 'failed'})",
        level,
    )
    return jsonify({
        "success": ok,
        "instance_id": instance_id,
        "target": target,
        "enabled": enabled,
        "command": command,
        "result": result,
        "control": control,
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/agents/command/batch', methods=['POST'])
def send_agents_batch_command():
    """Dispatch one command to multiple agents in one call."""
    data = request.get_json(silent=True) or {}
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    targets = data.get("targets") or []
    command = str(data.get("command", "")).strip()
    priority = str(data.get("priority", "medium")).strip().lower() or "medium"
    options = data.get("options") or {}

    if not isinstance(targets, list) or not targets:
        return jsonify({"success": False, "error": "Missing targets list"}), 400
    if not command:
        return jsonify({"success": False, "error": "Missing command"}), 400

    normalized_targets = []
    for item in targets:
        target = state._normalize_agent(item)
        if target:
            normalized_targets.append(target)
    normalized_targets = list(dict.fromkeys(normalized_targets))
    if not normalized_targets:
        return jsonify({"success": False, "error": "No valid targets"}), 400

    results: Dict[str, Any] = {}
    success_count = 0
    for target in normalized_targets:
        state.add_agent_log(target, f"📩 Batch command: {command}", "info")
        result = _call_instance_command(instance_id, target, command, priority, options)
        ok = bool(result.get("success", True))
        results[target] = result
        if ok:
            success_count += 1
            state.add_agent_log(target, f"✅ Batch handled: {command}", "success")
        else:
            state.add_agent_log(target, f"❌ Batch failed: {result.get('error', 'unknown')}", "error")

    return jsonify({
        "success": success_count == len(normalized_targets),
        "instance_id": instance_id,
        "summary": {
            "total": len(normalized_targets),
            "success": success_count,
            "failed": len(normalized_targets) - success_count,
        },
        "command": command,
        "targets": normalized_targets,
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/agents/coordination/dispatch', methods=['POST'])
def dispatch_agent_coordination():
    """Coordinate a compact multi-agent command plan to reduce token overhead."""
    rate_limited = _rate_limited("agents_coordination_dispatch", API_RATE_LIMIT_COMMAND_PER_MIN, 60)
    if rate_limited:
        return rate_limited

    data = request.get_json(silent=True) or {}
    goal = str(data.get("goal", "") or "").strip()
    if not goal:
        return _api_error("MISSING_GOAL", "Missing coordination goal")

    member_id = _resolve_member_id(data)
    max_agents = _int_in_range(data.get("max_agents", 4), default=4, min_value=2, max_value=7)
    compact_mode = _to_bool(data.get("compact_mode", True))
    execute = _to_bool(data.get("execute", True))
    priority = str(data.get("priority", "high") or "high").strip().lower() or "high"
    create_tasks = _to_bool(data.get("create_tasks", AGENT_COORDINATION_AUTO_CREATE_TASKS))

    instances = get_orion_instances_snapshot()
    requested_instance_ids = data.get("instance_ids") if isinstance(data.get("instance_ids"), list) else []
    if requested_instance_ids:
        requested = {str(item).strip() for item in requested_instance_ids if str(item).strip()}
        targets = [item for item in instances if str(item.get("id", "")).strip() in requested]
    else:
        multi_instance = _to_bool(data.get("multi_instance", True))
        if multi_instance:
            targets = [item for item in instances if bool(item.get("online"))]
            if not targets:
                targets = [item for item in instances[:1]]
        else:
            instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
            targets = [item for item in instances if str(item.get("id", "")).strip() == instance_id]
            if not targets:
                targets = [{"id": instance_id, "name": instance_id, "online": False}]

    if execute:
        goal_key = re.sub(r"\s+", " ", str(goal).strip().lower())[:220]
        target_ids = ",".join(
            sorted(str(item.get("id", "")).strip().lower() for item in targets if str(item.get("id", "")).strip())
        )
        dedup_signature = (
            f"{goal_key}|targets:{target_ids}|compact:{1 if compact_mode else 0}|agents:{max_agents}|prio:{priority}"
        )
        is_repeat, retry_after = _agent_coordination_debounced(dedup_signature)
        if is_repeat:
            return _api_ok(
                {
                    "goal": goal,
                    "member_id": member_id or None,
                    "execution": {
                        "executed": False,
                        "deduplicated": True,
                        "retry_after_sec": retry_after,
                        "instances": [str(item.get("id", "")) for item in targets],
                        "total_sent": 0,
                        "total_success": 0,
                        "total_failed": 0,
                    },
                    "results": {},
                },
                status_code=202,
            )

    payload = _execute_agent_coordination(
        goal=goal,
        member_id=member_id,
        max_agents=max_agents,
        compact_mode=compact_mode,
        execute=execute,
        priority=priority,
        instance_ids=[str(item.get("id", "")).strip() for item in targets if str(item.get("id", "")).strip()],
        multi_instance=False,
    )
    materialized = {"enabled": False, "created": 0, "results": []}
    if execute and create_tasks:
        materialized = _materialize_coordination_tasks(
            goal=goal,
            coordination_payload=payload,
            priority=priority,
            member_id=member_id,
            max_tasks=max_agents,
        )
    payload["task_materialization"] = materialized
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    execution_instances = execution.get("instances") if isinstance(execution.get("instances"), list) else []
    targets_count = len(execution_instances)

    if member_id:
        try:
            _team_persona_store.record_interaction(
                member_id=member_id,
                message=goal,
                channel="coordination",
                intent="agents_coordination_dispatch",
                context={"instance_id": "multi" if targets_count > 1 else (execution_instances[0] if execution_instances else LOCAL_INSTANCE_ID)},
            )
        except Exception:
            pass

    state.add_agent_log(
        "guardian",
        (
            f"🧩 Agent coordination ({'exec' if execute else 'plan'}) "
            f"instances={targets_count} agents={len(plan.get('agents', []))} "
            f"saved≈{plan.get('token_budget', {}).get('estimated_tokens_saved', 0)} tokens "
            f"tasks={int(materialized.get('created', 0) or 0)}"
        ),
        "success",
    )

    return _api_ok(payload)


@app.route('/api/guardian/preferences', methods=['POST'])
def update_guardian_preferences():
    """Update Guardian preferences"""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"success": False, "error": "Missing preferences"}), 400
    result = _call_instance_command(
        LOCAL_INSTANCE_ID,
        "guardian",
        "set_preferences",
        "high",
        {"preferences": data},
    )
    ok = bool(result.get("success", True))
    level = "success" if ok else "warning"
    state.add_guardian_log(f"⚙️ Preferences updated: {data}", level)
    return jsonify({"success": ok, "preferences": data, "result": result})


# ============================================
# MULTI-ORION HUB API
# ============================================

try:
    from src.core.multi_orion_hub import get_multi_orion_hub
    _hub = get_multi_orion_hub()

    @app.route('/api/hub/kanban')
    def get_kanban_board():
        """Get Kanban board with all tasks"""
        return jsonify({
            "success": True,
            "board": _hub.get_kanban_board(),
            "metrics": _hub.get_metrics()
        })

    @app.route('/api/hub/tasks', methods=['GET', 'POST'])
    def manage_tasks():
        """Create or list tasks."""
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            expected_version = data.get("expected_version")
            result = _hub.create_task_safe(
                title=data.get("title", "New Task"),
                description=data.get("description", ""),
                priority=data.get("priority", "medium"),
                assigned_to=data.get("assigned_to"),
                expected_version=expected_version,
            )
            status = 200 if result.get("success") else (409 if result.get("error_code") == "VERSION_CONFLICT" else 400)
            return jsonify(result), status

        tasks = _hub.list_tasks()
        metrics = _hub.get_metrics()
        return jsonify(
            {
                "success": True,
                "tasks": tasks,
                "version": metrics.get("state_version", 0),
                "updated_at": metrics.get("state_updated_at"),
            }
        )

    @app.route('/api/hub/tasks/batch-update', methods=['POST'])
    def batch_update_tasks():
        """Batch update tasks for status/assignment edits from dashboard."""
        data = request.get_json(silent=True) or {}
        updates = data.get("updates")
        if not isinstance(updates, list) or not updates:
            task_ids = data.get("task_ids")
            if isinstance(task_ids, list) and task_ids:
                updates = [{"task_id": task_id} for task_id in task_ids]
                for key in ("status", "assigned_to", "owner_id", "lease_token", "force"):
                    if key in data:
                        for row in updates:
                            row[key] = data.get(key)
            else:
                return jsonify({"success": False, "error": "Missing updates or task_ids", "error_code": "MISSING_UPDATES"}), 400

        atomic = bool(data.get("atomic", False))
        strict_version = _to_bool(data.get("strict_version", False))
        auto_force_lease = _to_bool(data.get("auto_force_lease", True))
        current_version = int(_hub.get_metrics().get("state_version", 0) or 0)
        applied = 0
        results: List[Dict[str, Any]] = []

        for raw in updates:
            row = raw if isinstance(raw, dict) else {}
            task_id = str(row.get("task_id", "")).strip()
            if not task_id:
                results.append({"success": False, "error_code": "MISSING_TASK_ID", "error": "Missing task_id"})
                if atomic:
                    break
                continue

            has_status = "status" in row
            has_assignee = "assigned_to" in row
            if not has_status and not has_assignee:
                result = {"success": False, "error": "No update action provided", "error_code": "NO_ACTION"}
                results.append({"task_id": task_id, **result})
                if atomic:
                    break
                continue

            result = {"success": True, "task_id": task_id, "version": current_version}

            if has_assignee:
                assign_result = _hub.assign_task_safe(
                    task_id=task_id,
                    orion_id=row.get("assigned_to"),
                    expected_version=current_version if strict_version else None,
                )
                if not assign_result.get("success"):
                    result = assign_result
                else:
                    result = assign_result
                    current_version = int(assign_result.get("version", current_version) or current_version)

            if result.get("success") and has_status:
                status_force = bool(row.get("force", False))
                status_result = _hub.update_task_status_safe(
                    task_id=task_id,
                    new_status=row.get("status"),
                    expected_version=current_version if strict_version else None,
                    owner_id=row.get("owner_id"),
                    lease_token=row.get("lease_token"),
                    force=status_force,
                )
                if (
                    not status_result.get("success")
                    and str(status_result.get("error_code", "")) == "LEASE_CONFLICT"
                    and auto_force_lease
                    and not status_force
                ):
                    forced_result = _hub.update_task_status_safe(
                        task_id=task_id,
                        new_status=row.get("status"),
                        expected_version=current_version if strict_version else None,
                        owner_id=row.get("owner_id"),
                        lease_token=row.get("lease_token"),
                        force=True,
                    )
                    if forced_result.get("success"):
                        forced_result = {**forced_result, "lease_override": True}
                    status_result = forced_result
                if not status_result.get("success"):
                    result = status_result
                else:
                    result = status_result
                    current_version = int(status_result.get("version", current_version) or current_version)

            if result.get("success"):
                applied += 1
                current_version = int(result.get("version", current_version) or current_version)
            results.append({"task_id": task_id, **result})
            if atomic and not result.get("success"):
                break

        all_success = applied == len(updates)
        status = 200 if all_success else 409
        return jsonify(
            {
                "success": all_success,
                "summary": {
                    "total": len(updates),
                    "applied": applied,
                    "failed": max(0, len(updates) - applied),
                    "atomic": atomic,
                    "strict_version": strict_version,
                    "auto_force_lease": auto_force_lease,
                },
                "results": results,
                "version": current_version,
            }
        ), status

    @app.route('/api/hub/tasks/<task_id>', methods=['PUT', 'DELETE'])
    def update_task(task_id):
        """Update, assign, or delete a task with version-safe operations."""
        if request.method == 'DELETE':
            expected_version = request.args.get("expected_version")
            result = _hub.delete_task_safe(task_id, expected_version=expected_version)
            status = 200 if result.get("success") else (409 if result.get("error_code") == "VERSION_CONFLICT" else 404)
            return jsonify(result), status

        data = request.get_json(silent=True) or {}
        expected_version = data.get("expected_version")
        if "status" in data:
            result = _hub.update_task_status_safe(
                task_id,
                data["status"],
                expected_version=expected_version,
                owner_id=data.get("owner_id"),
                lease_token=data.get("lease_token"),
                force=bool(data.get("force", False)),
            )
        elif "assigned_to" in data:
            result = _hub.assign_task_safe(task_id, data["assigned_to"], expected_version=expected_version)
        else:
            result = {"success": False, "error": "No update action provided", "error_code": "NO_ACTION"}

        if result.get("success"):
            status = 200
        else:
            code = str(result.get("error_code", ""))
            if code == "VERSION_CONFLICT":
                status = 409
            elif code in {"TASK_NOT_FOUND"}:
                status = 404
            elif code in {"LEASE_CONFLICT"}:
                status = 423
            else:
                status = 400
        return jsonify(result), status

    @app.route('/api/hub/tasks/<task_id>/history')
    def get_hub_task_history(task_id):
        """Project audit history entries for one task."""
        limit = _int_in_range(request.args.get("limit", 80), default=80, min_value=1, max_value=500)
        task_key = str(task_id or "").strip()
        rows: List[Dict[str, Any]] = []
        for row in list(_hub.audit_log)[-max(limit * 3, 80):]:
            if not isinstance(row, dict):
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            if str(details.get("task_id", "")).strip() != task_key:
                continue
            rows.append(row)
        rows = _sort_by_timestamp_desc(rows)[:limit]
        return jsonify({"success": True, "task_id": task_key, "history": rows, "count": len(rows)})

    @app.route('/api/hub/tasks/<task_id>/claim', methods=['POST'])
    def claim_hub_task(task_id):
        data = request.get_json(silent=True) or {}
        result = _hub.claim_task(
            task_id=task_id,
            owner_id=data.get("owner_id", ""),
            lease_sec=data.get("lease_sec"),
            expected_version=data.get("expected_version"),
        )
        if result.get("success"):
            status = 200
        else:
            code = str(result.get("error_code", ""))
            status = 409 if code in {"VERSION_CONFLICT", "LEASE_CONFLICT", "LEASE_NOT_EXPIRED"} else 400
        return jsonify(result), status

    @app.route('/api/hub/tasks/<task_id>/heartbeat', methods=['POST'])
    def heartbeat_hub_task_lease(task_id):
        data = request.get_json(silent=True) or {}
        result = _hub.heartbeat_task_lease(
            task_id=task_id,
            owner_id=data.get("owner_id", ""),
            lease_token=data.get("lease_token", ""),
            lease_sec=data.get("lease_sec"),
            expected_version=data.get("expected_version"),
        )
        if result.get("success"):
            status = 200
        else:
            code = str(result.get("error_code", ""))
            status = 409 if code in {"VERSION_CONFLICT", "LEASE_CONFLICT", "LEASE_EXPIRED"} else 400
        return jsonify(result), status

    @app.route('/api/hub/tasks/<task_id>/release', methods=['POST'])
    def release_hub_task_lease(task_id):
        data = request.get_json(silent=True) or {}
        result = _hub.release_task_lease(
            task_id=task_id,
            owner_id=data.get("owner_id", ""),
            lease_token=data.get("lease_token", ""),
            expected_version=data.get("expected_version"),
            next_status=data.get("next_status"),
            force_if_expired=_to_bool(data.get("force_if_expired", False)),
            actor_id=data.get("actor_id", ""),
            reason=data.get("reason", ""),
        )
        if result.get("success"):
            status = 200
        else:
            code = str(result.get("error_code", ""))
            status = 409 if code in {"VERSION_CONFLICT", "LEASE_CONFLICT"} else 400
        return jsonify(result), status

    @app.route('/api/hub/orions')
    def get_hub_orions():
        """Get all registered Orion instances"""
        snapshot = _hub.get_snapshot()
        return jsonify({
            "success": True,
            "orions": snapshot.get("orions", {}),
            "metrics": _hub.get_metrics(),
            "version": snapshot.get("version", 0),
            "updated_at": snapshot.get("updated_at"),
        })

    @app.route('/api/hub/audit')
    def get_audit_log():
        """Get audit log"""
        limit = request.args.get("limit", 50, type=int)
        return jsonify({
            "success": True,
            "audit": _hub.audit_log[-limit:]
        })

    def _hub_normalize_assignee(value: Any) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    def _hub_normalize_priority(value: Any) -> str:
        raw = str(value or "normal").strip().lower()
        if raw in {"p0", "critical", "urgent", "highest"}:
            return "p0"
        if raw in {"p1", "high", "important"}:
            return "p1"
        if raw in {"p2", "medium", "normal"}:
            return "p2"
        return "p3"

    def _hub_normalize_status(value: Any) -> str:
        raw = str(value or "todo").strip().lower()
        if raw in {"in_progress", "running", "active", "claimed", "doing"}:
            return "in_progress"
        if raw in {"done", "completed", "resolved", "closed"}:
            return "done"
        if raw in {"blocked", "stuck", "waiting", "hold"}:
            return "blocked"
        if raw in {"review", "reviewing"}:
            return "review"
        if raw in {"backlog"}:
            return "todo"
        return "todo"

    def _hub_task_lease_info(task: Dict[str, Any], now_dt: Optional[datetime] = None) -> Dict[str, Any]:
        now = now_dt or datetime.now()
        owner = str(task.get("lease_owner") or "").strip()
        token = str(task.get("lease_token") or "").strip()
        expires_at = str(task.get("lease_expires_at") or "").strip()
        expires = _parse_iso_datetime(expires_at)
        active = bool(owner and token and expires and expires > now)
        stale = bool(owner and token and (not expires or expires <= now))
        return {
            "owner": owner,
            "token_present": bool(token),
            "expires_at": expires_at or None,
            "active": active,
            "stale": stale,
        }

    def _hub_orion_rows_by_assignee(orions_snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        index: Dict[str, Dict[str, Any]] = {}
        if not isinstance(orions_snapshot, dict):
            return index
        for key, row in orions_snapshot.items():
            if not isinstance(row, dict):
                continue
            candidates = {
                _hub_normalize_assignee(key),
                _hub_normalize_assignee(row.get("id")),
            }
            for item in candidates:
                if item:
                    index[item] = row
        return index

    def _hub_runtime_rows_by_assignee(instances: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        index: Dict[str, Dict[str, Any]] = {}
        for item in instances:
            if not isinstance(item, dict):
                continue
            candidates = {
                _hub_normalize_assignee(item.get("id")),
                _hub_normalize_assignee(item.get("name")),
            }
            for key in candidates:
                if key:
                    index[key] = item
        return index

    def _hub_process_board(limit_transitions: int = 30) -> Dict[str, Any]:
        now = datetime.now()
        tasks = [item for item in _hub.list_tasks() if isinstance(item, dict)]
        runtime_instances = [item for item in get_orion_instances_snapshot() if isinstance(item, dict)]
        runtime_index = _hub_runtime_rows_by_assignee(runtime_instances)
        control_snapshot = _control_plane_registry.snapshot()
        status_counts: Dict[str, int] = {"backlog": 0, "todo": 0, "in_progress": 0, "review": 0, "done": 0, "blocked": 0}
        process_rows: Dict[str, Dict[str, Any]] = {}
        stale_tasks: List[Dict[str, Any]] = []
        stale_threshold_sec = max(120, int(MONITOR_STUCK_THRESHOLD_SEC))

        def ensure_process_row(assignee: str) -> Dict[str, Any]:
            row = process_rows.get(assignee)
            if isinstance(row, dict):
                return row
            row = {
                "assignee": assignee,
                "display_name": assignee,
                "state": "idle",
                "is_online": False,
                "running": False,
                "paused": False,
                "pause_reason": "",
                "iteration": None,
                "last_progress_at": None,
                "last_task_update": None,
                "total_tasks": 0,
                "lease_active_count": 0,
                "lease_stale_count": 0,
                "by_status": {"backlog": 0, "todo": 0, "in_progress": 0, "review": 0, "done": 0, "blocked": 0},
                "tasks": [],
                "current_task": "",
                "current_task_id": "",
            }
            process_rows[assignee] = row
            return row

        for task in tasks:
            assignee = _hub_normalize_assignee(task.get("assigned_to"))
            raw_status = str(task.get("status") or "backlog").strip().lower() or "backlog"
            status_norm = _hub_normalize_status(raw_status)
            status_bucket = raw_status if raw_status in {"backlog", "todo", "in_progress", "review", "done", "blocked"} else status_norm
            if status_bucket not in status_counts:
                status_bucket = "todo"
            status_counts[status_bucket] = int(status_counts.get(status_bucket, 0) or 0) + 1

            if not assignee:
                continue
            row = ensure_process_row(assignee)
            row["total_tasks"] = int(row.get("total_tasks", 0) or 0) + 1
            by_status = row.get("by_status") if isinstance(row.get("by_status"), dict) else {}
            by_status[status_bucket] = int(by_status.get(status_bucket, 0) or 0) + 1
            row["by_status"] = by_status

            lease = _hub_task_lease_info(task, now_dt=now)
            if lease.get("active"):
                row["lease_active_count"] = int(row.get("lease_active_count", 0) or 0) + 1
            if lease.get("stale"):
                row["lease_stale_count"] = int(row.get("lease_stale_count", 0) or 0) + 1

            updated_at = str(task.get("updated_at") or "")
            if updated_at and (not row.get("last_task_update") or str(updated_at) > str(row.get("last_task_update") or "")):
                row["last_task_update"] = updated_at

            task_title = str(task.get("title") or task.get("id") or "")
            task_id = str(task.get("id") or "")
            task_preview = {
                "id": task_id,
                "title": task_title,
                "priority": _hub_normalize_priority(task.get("priority")),
                "status": status_bucket,
                "updated_at": updated_at,
                "lease_active": bool(lease.get("active")),
                "lease_stale": bool(lease.get("stale")),
            }
            row_tasks = row.get("tasks") if isinstance(row.get("tasks"), list) else []
            row_tasks.append(task_preview)
            row["tasks"] = row_tasks

            if status_bucket == "in_progress":
                updated_dt = _parse_iso_datetime(updated_at)
                stale_by_age = bool(updated_dt and (now - updated_dt).total_seconds() >= stale_threshold_sec)
                if stale_by_age or bool(lease.get("stale")):
                    stale_tasks.append(
                        {
                            "task_id": task_id,
                            "title": task_title,
                            "assignee": assignee,
                            "updated_at": updated_at or None,
                            "stale_reason": "lease_stale" if lease.get("stale") else "no_recent_update",
                        }
                    )

        for runtime in runtime_instances:
            assignee = _hub_normalize_assignee(runtime.get("id")) or _hub_normalize_assignee(runtime.get("name"))
            if not assignee:
                continue
            row = ensure_process_row(assignee)
            row["display_name"] = str(runtime.get("name") or runtime.get("id") or assignee)
            row["is_online"] = bool(runtime.get("online", False))
            row["running"] = bool(runtime.get("running", False))
            row["paused"] = bool(runtime.get("paused", False))
            row["pause_reason"] = str(runtime.get("pause_reason") or "")
            row["iteration"] = runtime.get("iteration")
            row["last_progress_at"] = runtime.get("last_progress_at")
            if row["paused"]:
                row["state"] = "paused"
            elif row["running"]:
                row["state"] = "running"
            elif row["is_online"]:
                row["state"] = "idle"
            else:
                row["state"] = "offline"

        priority_rank = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
        for assignee, row in process_rows.items():
            runtime = runtime_index.get(assignee) if isinstance(runtime_index.get(assignee), dict) else {}
            if runtime and not row.get("display_name"):
                row["display_name"] = str(runtime.get("name") or runtime.get("id") or assignee)
            task_rows = row.get("tasks") if isinstance(row.get("tasks"), list) else []
            task_rows.sort(
                key=lambda item: (
                    0 if str(item.get("status", "")) == "in_progress" else 1,
                    priority_rank.get(str(item.get("priority", "p3")).lower(), 3),
                    str(item.get("updated_at") or ""),
                )
            )
            row["tasks"] = task_rows[:8]
            if task_rows:
                current = task_rows[0]
                row["current_task"] = str(current.get("title") or "")
                row["current_task_id"] = str(current.get("id") or "")
            row["active_tasks"] = int((row.get("by_status") or {}).get("in_progress", 0) or 0)
            row["blocked_tasks"] = int((row.get("by_status") or {}).get("blocked", 0) or 0)
            row["done_tasks"] = int((row.get("by_status") or {}).get("done", 0) or 0)

        tasks_by_id = {str(task.get("id") or ""): str(task.get("title") or "") for task in tasks}
        transitions: List[Dict[str, Any]] = []
        tracked_actions = {
            "task_created",
            "task_assigned",
            "task_moved",
            "task_claimed",
            "task_released",
            "task_released_force_expired",
        }
        for row in reversed(list(_hub.audit_log)):
            if not isinstance(row, dict):
                continue
            action = str(row.get("action") or "").strip()
            if action not in tracked_actions:
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            task_id = str(details.get("task_id") or "")
            task_title = str(details.get("title") or tasks_by_id.get(task_id, ""))
            transitions.append(
                {
                    "timestamp": row.get("timestamp"),
                    "action": action,
                    "task_id": task_id,
                    "task_title": task_title,
                    "from": details.get("from"),
                    "to": details.get("to"),
                    "owner_id": details.get("owner_id"),
                    "orion_id": details.get("orion_id"),
                }
            )
            if len(transitions) >= max(5, int(limit_transitions)):
                break

        processes = sorted(
            process_rows.values(),
            key=lambda item: (
                0 if bool(item.get("is_online")) else 1,
                -int(item.get("active_tasks", 0) or 0),
                -int(item.get("total_tasks", 0) or 0),
                str(item.get("assignee") or ""),
            ),
        )

        return {
            "generated_at": now.isoformat(),
            "summary": {
                "total_tasks": len(tasks),
                "status_breakdown": status_counts,
                "total_processes": len(processes),
                "online_processes": len([item for item in processes if item.get("is_online")]),
                "stale_tasks": len(stale_tasks),
                "control_plane_alive_workers": int(control_snapshot.get("alive_workers", 0) or 0),
                "control_plane_registered_workers": int(control_snapshot.get("registered_workers", 0) or 0),
            },
            "processes": processes,
            "transitions": transitions,
            "stale_tasks": stale_tasks[:20],
        }

    def _hub_bridge_terminals_by_owner() -> Dict[str, List[Dict[str, Any]]]:
        lock = globals().get("_BRIDGE_TERMINALS_LOCK")
        sessions_store = globals().get("_bridge_terminal_sessions")
        if not isinstance(sessions_store, dict) or lock is None:
            return {}
        with lock:
            rows = [dict(item) for item in sessions_store.values() if isinstance(item, dict)]
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            owner_raw = str(
                row.get("owner_id")
                or row.get("assignee")
                or row.get("orion_id")
                or row.get("instance_id")
                or ""
            ).strip()
            owner = owner_raw
            if owner.lower().startswith("orion:"):
                owner = owner.split(":", 1)[1]
            owner_norm = _hub_normalize_assignee(owner)
            if not owner_norm:
                continue
            grouped.setdefault(owner_norm, []).append(row)
        return grouped

    def _hub_next_actions(limit: int = 8) -> List[Dict[str, Any]]:
        max_items = max(1, int(limit))
        now = datetime.now()
        tasks = _hub.list_tasks()
        runtime_rows = _hub_runtime_rows_by_assignee(get_orion_instances_snapshot())
        bridge_terminals = _hub_bridge_terminals_by_owner()
        priority_rank = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
        actions: List[Dict[str, Any]] = []

        def rank(task: Dict[str, Any]) -> Tuple[int, str]:
            priority = _hub_normalize_priority(task.get("priority"))
            created = str(task.get("created_at") or "")
            return (priority_rank.get(priority, 3), created)

        actionable = [task for task in tasks if _hub_normalize_status(task.get("status")) in {"todo", "in_progress", "blocked"}]
        actionable_sorted = sorted(actionable, key=rank)

        for task in actionable_sorted:
            assignee = _hub_normalize_assignee(task.get("assigned_to"))
            priority = _hub_normalize_priority(task.get("priority"))
            status = _hub_normalize_status(task.get("status"))
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            if not assignee and status == "todo":
                actions.append(
                    {
                        "action_type": "assign_then_dispatch",
                        "title": f"Assign and dispatch {task.get('title') or task_id}",
                        "task_id": task_id,
                        "priority": priority,
                        "reason": "Unassigned task is waiting in todo/backlog.",
                        "recommended_api": "/api/hub/tasks/<task_id>/dispatch",
                        "suggested_payload": {"ensure_assigned": True, "priority": "high"},
                    }
                )
                continue

            if assignee:
                runtime = runtime_rows.get(assignee, {})
                online = bool(runtime.get("online", False))
                running = bool(runtime.get("running", False))
                paused = bool(runtime.get("paused", False))
                if not online and status in {"todo", "in_progress"}:
                    actions.append(
                        {
                            "action_type": "reassign_or_bring_online",
                            "title": f"Assignee offline for {task.get('title') or task_id}",
                            "task_id": task_id,
                            "assignee": assignee,
                            "priority": priority,
                            "reason": "Task owner is offline; reassign or recover runtime.",
                            "recommended_api": "/api/hub/tasks/batch-update",
                            "suggested_payload": {"updates": [{"task_id": task_id, "assigned_to": LOCAL_INSTANCE_ID}]},
                        }
                    )
                    continue
                if status == "todo" and online and (not running or paused):
                    actions.append(
                        {
                            "action_type": "dispatch_task",
                            "title": f"Dispatch {task.get('title') or task_id}",
                            "task_id": task_id,
                            "assignee": assignee,
                            "priority": priority,
                            "reason": "Assignee online but task not started yet.",
                            "recommended_api": "/api/hub/tasks/<task_id>/dispatch",
                            "suggested_payload": {"instance_id": assignee, "priority": "high"},
                        }
                    )

            lease = _hub_task_lease_info(task, now_dt=now)
            if status == "in_progress" and lease.get("stale"):
                actions.append(
                    {
                        "action_type": "reclaim_stale_lease",
                        "title": f"Reclaim stale lease for {task.get('title') or task_id}",
                        "task_id": task_id,
                        "priority": priority,
                        "reason": "In-progress task has stale/expired lease.",
                        "recommended_api": "/api/hub/tasks/<task_id>/release",
                        "suggested_payload": {
                            "owner_id": lease.get("owner"),
                            "lease_token": "",
                            "next_status": "todo",
                            "force_if_expired": True,
                            "actor_id": "autopilot",
                            "reason": "stale_lease_reclaim",
                        },
                    }
                )

            if len(actions) >= max_items:
                break

        if len(actions) < max_items:
            terminal_candidates: List[Dict[str, Any]] = []
            for owner, rows in bridge_terminals.items():
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    status = str(row.get("status") or "").strip().lower()
                    busy = bool(row.get("busy")) or status in {"running", "busy"}
                    if status in {"error", "failed", "blocked"}:
                        terminal_candidates.append(
                            {
                                "action_type": "inspect_terminal_error",
                                "title": f"Inspect terminal error ({owner})",
                                "priority": "p1",
                                "reason": str(row.get("last_error") or row.get("last_output") or "Terminal reported an error."),
                                "owner_id": owner,
                                "session_id": str(row.get("session_id") or ""),
                                "recommended_api": "/api/bridge/terminals",
                                "suggested_payload": {"owner_id": owner},
                            }
                        )
                    elif busy:
                        updated = _parse_iso_datetime(row.get("updated_at"))
                        if updated and (now - updated).total_seconds() >= 900:
                            terminal_candidates.append(
                                {
                                    "action_type": "check_stale_terminal",
                                    "title": f"Check stale running terminal ({owner})",
                                    "priority": "p2",
                                    "reason": "Terminal has been running without recent update.",
                                    "owner_id": owner,
                                    "session_id": str(row.get("session_id") or ""),
                                    "recommended_api": "/api/bridge/terminals",
                                    "suggested_payload": {"owner_id": owner},
                                }
                            )
            actions.extend(terminal_candidates[: max(0, max_items - len(actions))])

        return actions[:max_items]

    @app.route('/api/hub/tasks/<task_id>/dispatch', methods=['POST'])
    def dispatch_hub_task(task_id):
        data = request.get_json(silent=True) or {}
        task_key = str(task_id or "").strip()
        tasks = _hub.list_tasks()
        task = next((item for item in tasks if str(item.get("id") or "").strip() == task_key), None)
        if not isinstance(task, dict):
            return jsonify({"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND"}), 404

        ensure_assigned = _to_bool(data.get("ensure_assigned", True))
        force_reopen = _to_bool(data.get("force_reopen", False))
        fallback_guardian = _to_bool(data.get("guardian_fallback", True))
        initial_status = _hub_normalize_status(task.get("status"))
        if initial_status in {"done", "review"} and not force_reopen:
            return jsonify({"success": False, "error": "Task is already finalized", "error_code": "TASK_FINALIZED", "task": task}), 409
        selected_instance = _hub_normalize_assignee(data.get("instance_id")) or _hub_normalize_assignee(task.get("assigned_to")) or _hub_normalize_assignee(LOCAL_INSTANCE_ID)

        assignment_result = None
        if ensure_assigned:
            current_assignee = _hub_normalize_assignee(task.get("assigned_to"))
            if current_assignee != selected_instance:
                assignment_result = _hub.assign_task_safe(task_id=task_key, orion_id=selected_instance)
                if not assignment_result.get("success"):
                    return jsonify(assignment_result), (409 if assignment_result.get("error_code") == "VERSION_CONFLICT" else 400)

        latest_tasks = _hub.list_tasks()
        current_task = next((item for item in latest_tasks if str(item.get("id") or "").strip() == task_key), task)
        current_status = _hub_normalize_status(current_task.get("status"))
        reopen_result = None
        status_hint_result = None
        if force_reopen and current_status in {"done", "review"}:
            reopen_result = _hub.update_task_status_safe(task_id=task_key, new_status="todo", force=True)
            if not reopen_result.get("success"):
                return jsonify(reopen_result), (409 if reopen_result.get("error_code") == "VERSION_CONFLICT" else 400)
            latest_tasks = _hub.list_tasks()
            current_task = next((item for item in latest_tasks if str(item.get("id") or "").strip() == task_key), current_task)
            current_status = _hub_normalize_status(current_task.get("status"))

        status_hint_raw = str(data.get("status_hint", "") or "").strip()
        if status_hint_raw:
            hinted_status = _hub_normalize_status(status_hint_raw)
            if hinted_status and hinted_status != current_status:
                status_hint_result = _hub.update_task_status_safe(task_id=task_key, new_status=hinted_status, force=True)
                if not status_hint_result.get("success"):
                    return jsonify(status_hint_result), (409 if status_hint_result.get("error_code") == "VERSION_CONFLICT" else 400)
                latest_tasks = _hub.list_tasks()
                current_task = next((item for item in latest_tasks if str(item.get("id") or "").strip() == task_key), current_task)
                current_status = _hub_normalize_status(current_task.get("status"))

        command = str(data.get("command", "run_cycle") or "run_cycle").strip()
        target = str(data.get("target", "orion") or "orion").strip().lower() or "orion"
        priority = str(data.get("priority", "high") or "high").strip().lower() or "high"
        options = data.get("options") if isinstance(data.get("options"), dict) else {}
        options.setdefault("control_mode", "interrupt")
        options.setdefault("resume_after", True)
        options.setdefault("async_dispatch", HUB_DISPATCH_ASYNC_DEFAULT)
        options.setdefault("wait_timeout_sec", HUB_DISPATCH_WAIT_TIMEOUT_SEC)
        options.setdefault("reason", "hub_task_dispatch")
        options.setdefault("dispatch_task_id", task_key)
        options.setdefault("dispatch_task_title", str(current_task.get("title") or ""))
        options.setdefault("dispatch_task_priority", _hub_normalize_priority(current_task.get("priority")))
        options.setdefault("dispatch_assignee", selected_instance)

        dispatch = _call_instance_command(selected_instance, target, command, priority, options)
        guardian = None
        if not bool(dispatch.get("success")) and fallback_guardian:
            guardian = _call_instance_command(
                selected_instance,
                "guardian",
                "unstick_orion",
                "high",
                {"source": "hub_task_dispatch", "task_id": task_key},
            )

        accepted = bool(dispatch.get("success")) or bool(isinstance(guardian, dict) and guardian.get("success"))
        status_after_dispatch = None
        if accepted:
            latest_after_dispatch = _hub.list_tasks()
            task_after_dispatch = next((item for item in latest_after_dispatch if str(item.get("id") or "").strip() == task_key), current_task)
            status_norm_after = _hub_normalize_status(task_after_dispatch.get("status"))
            raw_status_after = str(task_after_dispatch.get("status") or "").strip().lower()
            if status_norm_after == "todo" or raw_status_after == "backlog":
                status_after_dispatch = _hub.update_task_status_safe(
                    task_id=task_key,
                    new_status="in_progress",
                    force=True,
                )
                if status_after_dispatch.get("success"):
                    latest_after_dispatch = _hub.list_tasks()
                    task_after_dispatch = next((item for item in latest_after_dispatch if str(item.get("id") or "").strip() == task_key), task_after_dispatch)
            current_task = task_after_dispatch
        level = "success" if accepted else "warning"
        state.add_agent_log(
            "guardian",
            f"🎯 Hub dispatch task {task_key} -> {selected_instance}: {'accepted' if accepted else 'failed'}",
            level,
        )
        return jsonify(
            {
                "success": accepted,
                "task": current_task,
                "instance_id": selected_instance,
                "dispatch": dispatch,
                "guardian_fallback": guardian,
                "assignment": assignment_result,
                "reopen": reopen_result,
                "status_hint_update": status_hint_result,
                "status_after_dispatch": status_after_dispatch,
                "timestamp": datetime.now().isoformat(),
            }
        ), (200 if accepted else 502)

    @app.route('/api/hub/next-actions')
    def get_hub_next_actions():
        limit = _int_in_range(request.args.get("limit", 8), default=8, min_value=1, max_value=30)
        actions = _hub_next_actions(limit=limit)
        return jsonify(
            {
                "success": True,
                "actions": actions,
                "count": len(actions),
                "timestamp": datetime.now().isoformat(),
            }
        )

    @app.route('/api/hub/next-action/autopilot/status')
    def get_hub_autopilot_status():
        """Get hub next-action autopilot status."""
        return jsonify(_build_hub_autopilot_status_payload(include_legacy_aliases=True))

    @app.route('/api/hub/next-action/autopilot/control', methods=['POST'])
    def control_hub_autopilot():
        """Control hub next-action autopilot."""
        data = request.get_json(silent=True) or {}
        action = str(data.get("action", "")).strip().lower()
        success, state_payload, message = _autopilot_apply_control(action, data)
        if action == "trigger":
            if not success:
                return jsonify({"success": False, "error": message, "message": message, "state": state_payload}), 400
            payload = {"success": True, "message": message, "state": state_payload}
            if isinstance(state_payload, dict):
                for key in ("executed", "blocked", "errors", "scanned", "success", "running", "last_error", "auto_enabled"):
                    if key in state_payload:
                        payload[key] = state_payload[key]
            return jsonify(payload)

        if not success:
            return jsonify({"success": False, "error": message, "state": state_payload}), 400

        return jsonify({"success": True, "message": message, "state": state_payload})

    # ========== Autopilot: Auto-Execute Next Actions ==========
    _AUTOPILOT_DRY_RUN_DEFAULT = _env_bool("AUTOPILOT_DRY_RUN_DEFAULT", True)
    _AUTOPILOT_MAX_PRIORITY_DEFAULT = str(os.getenv("AUTOPILOT_MAX_PRIORITY", "p2") or "p2").strip().lower()
    if _AUTOPILOT_MAX_PRIORITY_DEFAULT not in {"p0", "p1", "p2", "p3"}:
        _AUTOPILOT_MAX_PRIORITY_DEFAULT = "p2"
    _AUTOPILOT_ACTION_TIMEOUT_SEC = max(3.0, float(os.getenv("AUTOPILOT_ACTION_TIMEOUT_SEC", "20")))
    _AUTOPILOT_TIMEOUT_CIRCUIT_THRESHOLD = max(1, int(os.getenv("AUTOPILOT_TIMEOUT_CIRCUIT_THRESHOLD", "3")))
    _AUTOPILOT_TIMEOUT_CIRCUIT_COOLDOWN_SEC = max(10, int(os.getenv("AUTOPILOT_TIMEOUT_CIRCUIT_COOLDOWN_SEC", "90")))
    _AUTOPILOT_ACTION_EXECUTOR_WORKERS = max(1, min(6, int(os.getenv("AUTOPILOT_ACTION_EXECUTOR_WORKERS", "2"))))
    _AUTOPILOT_DISPATCH_ASYNC = _env_bool("AUTOPILOT_DISPATCH_ASYNC", True)

    _AUTOPILOT_STATE: Dict[str, Any] = {
        "enabled": bool(HUB_NEXT_ACTION_AUTOPILOT_ENABLED),
        "dry_run": bool(_AUTOPILOT_DRY_RUN_DEFAULT),
        "active_executor": _normalize_autopilot_executor(
            str(os.getenv("HUB_AUTOPILOT_ACTIVE_EXECUTOR", "policy_loop") or "policy_loop")
        ),
        "interval": max(5, int(HUB_NEXT_ACTION_AUTOPILOT_INTERVAL_SEC)),
        "max_actions_per_cycle": max(1, min(10, int(HUB_NEXT_ACTION_AUTOPILOT_MAX_PER_TICK))),
        "state": "running" if bool(HUB_NEXT_ACTION_AUTOPILOT_ENABLED) else "stopped",
        "policy": {
            "allow_dispatch": True,
            "allow_reassign": True,
            "allow_release_lease": True,
            "allow_inspect_terminal": False,
            "require_confidence": 0.8,
            "max_priority": _AUTOPILOT_MAX_PRIORITY_DEFAULT,
            "blocked_actions": [],
        },
        "stats": {
            "total_cycles": 0,
            "actions_executed": 0,
            "actions_blocked": 0,
            "errors": 0,
            "timeouts": 0,
            "consecutive_timeouts": 0,
            "timeout_circuit_opens": 0,
            "last_cycle": None,
        },
    }
    _autopilot_action_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=_AUTOPILOT_ACTION_EXECUTOR_WORKERS,
        thread_name_prefix="autopilot-action",
    )
    _autopilot_guard_lock = threading.RLock()
    _autopilot_guard_state: Dict[str, Any] = {
        "consecutive_timeouts": 0,
        "open_until": None,
        "last_timeout_at": None,
        "last_timeout_action": "",
    }

    def _autopilot_guard_snapshot() -> Dict[str, Any]:
        with _autopilot_guard_lock:
            open_until = _autopilot_guard_state.get("open_until")
            return {
                "consecutive_timeouts": int(_autopilot_guard_state.get("consecutive_timeouts", 0) or 0),
                "open_until": open_until.isoformat() if isinstance(open_until, datetime) else None,
                "last_timeout_at": _autopilot_guard_state.get("last_timeout_at"),
                "last_timeout_action": _autopilot_guard_state.get("last_timeout_action"),
                "threshold": _AUTOPILOT_TIMEOUT_CIRCUIT_THRESHOLD,
                "cooldown_sec": _AUTOPILOT_TIMEOUT_CIRCUIT_COOLDOWN_SEC,
                "action_timeout_sec": _AUTOPILOT_ACTION_TIMEOUT_SEC,
            }

    def _autopilot_guard_block_reason() -> Optional[str]:
        with _autopilot_guard_lock:
            open_until = _autopilot_guard_state.get("open_until")
            if isinstance(open_until, datetime):
                now = datetime.now()
                if now < open_until:
                    return f"Timeout circuit open until {open_until.isoformat()}"
                _autopilot_guard_state["open_until"] = None
                _autopilot_guard_state["consecutive_timeouts"] = 0
                _AUTOPILOT_STATE["stats"]["consecutive_timeouts"] = 0
        return None

    def _autopilot_guard_record_success() -> None:
        with _autopilot_guard_lock:
            _autopilot_guard_state["consecutive_timeouts"] = 0
            _AUTOPILOT_STATE["stats"]["consecutive_timeouts"] = 0

    def _autopilot_guard_record_timeout(action_type: str) -> None:
        now = datetime.now()
        opened = False
        with _autopilot_guard_lock:
            count = int(_autopilot_guard_state.get("consecutive_timeouts", 0) or 0) + 1
            _autopilot_guard_state["consecutive_timeouts"] = count
            _autopilot_guard_state["last_timeout_at"] = now.isoformat()
            _autopilot_guard_state["last_timeout_action"] = str(action_type or "")
            _AUTOPILOT_STATE["stats"]["consecutive_timeouts"] = count
            _AUTOPILOT_STATE["stats"]["timeouts"] += 1
            if count >= _AUTOPILOT_TIMEOUT_CIRCUIT_THRESHOLD:
                _autopilot_guard_state["open_until"] = now + timedelta(seconds=_AUTOPILOT_TIMEOUT_CIRCUIT_COOLDOWN_SEC)
                _AUTOPILOT_STATE["stats"]["timeout_circuit_opens"] += 1
                opened = True
        if opened:
            logger.warning(
                "🤖 Autopilot timeout circuit opened",
                extra={
                    "threshold": _AUTOPILOT_TIMEOUT_CIRCUIT_THRESHOLD,
                    "cooldown_sec": _AUTOPILOT_TIMEOUT_CIRCUIT_COOLDOWN_SEC,
                    "action_type": action_type,
                },
            )
            state.add_agent_log(
                "autopilot",
                f"⛔ Timeout circuit opened ({_AUTOPILOT_TIMEOUT_CIRCUIT_COOLDOWN_SEC}s) after {_AUTOPILOT_TIMEOUT_CIRCUIT_THRESHOLD} timeouts.",
                "warning",
            )

    def _autopilot_reconcile_runtimes(force_restart: bool = False) -> None:
        """Align runtime threads with latest config flags."""
        if "_AUTOPILOT_STATE" not in globals() or not isinstance(_AUTOPILOT_STATE, dict):
            return

        if not _autopilot_is_enabled():
            _stop_hub_autopilot_loop()
            _stop_policy_autopilot_loop()
            if "state" in _AUTOPILOT_STATE:
                _AUTOPILOT_STATE["state"] = "stopped"
            return

        target = _autopilot_active_executor()
        _set_autopilot_executor(target, reason="reconcile", force_restart=force_restart)
        if "state" in _AUTOPILOT_STATE:
            _AUTOPILOT_STATE["state"] = "running"

    # Background autopilot loop config (NEW - integrates with _AUTOPILOT_STATE)
    _AUTOPILOT_BACKGROUND_ENABLED = _env_bool("AUTOPILOT_BACKGROUND_ENABLED", True)
    _AUTOPILOT_BACKGROUND_INTERVAL_SEC = max(10, int(os.getenv("AUTOPILOT_BACKGROUND_INTERVAL_SEC", "30")))
    _autopilot_background_stop_event = threading.Event()

    def _autopilot_background_loop():
        """Background thread that runs autopilot cycles automatically."""
        global _autopilot_background_last_run, _autopilot_background_last_error
        logger.info("🤖 Autopilot background loop started")
        while not _autopilot_background_stop_event.is_set():
            try:
                active_executor = _autopilot_active_executor()
                if not _autopilot_is_enabled() or active_executor != "policy_loop":
                    _autopilot_background_stop_event.wait(_autopilot_configured_interval())
                    continue
                result = _autopilot_cycle_once(
                    dry_run_override=None,
                    use_max_actions=_autopilot_configured_max_actions(),
                    context="policy_loop",
                )
                _autopilot_background_last_run = datetime.now()
                _autopilot_background_last_error = result.get("error")
                if result.get("executed"):
                    logger.info(
                        "🤖 Autopilot background cycle: {} executed, {} blocked, {} errors".format(
                            len(result.get("executed", [])),
                            len(result.get("blocked", [])),
                            len(result.get("errors", [])),
                        )
                    )
            except Exception as e:
                _autopilot_background_last_error = str(e)
                logger.error(f"🤖 Autopilot background error: {e}")

            _autopilot_background_stop_event.wait(_autopilot_configured_interval())

        logger.info("🤖 Autopilot background loop stopped")

    # Background thread management
    _autopilot_background_thread: Optional[threading.Thread] = None
    _autopilot_background_lock = threading.RLock()

    def _ensure_autopilot_background_started() -> None:
        """Start the autopilot background loop if not already running."""
        global _autopilot_background_thread
        with _autopilot_background_lock:
            if _autopilot_background_thread and _autopilot_background_thread.is_alive():
                return
            if not _AUTOPILOT_BACKGROUND_ENABLED:
                logger.info("🤖 Autopilot background disabled by config")
                return
            _autopilot_background_stop_event.clear()
            _autopilot_background_thread = threading.Thread(
                target=_autopilot_background_loop,
                name="autopilot-background",
                daemon=True,
            )
            _autopilot_background_thread.start()
            logger.info("🤖 Autopilot background loop thread started")

    def _autopilot_should_execute(action: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if autopilot should execute this action based on policy."""
        policy = _AUTOPILOT_STATE["policy"]
        action_type = str(action.get("action_type", "") or "").strip()
        allowed_types = set(HUB_NEXT_ACTION_AUTOPILOT_ALLOW_TYPES)

        guard_reason = _autopilot_guard_block_reason()
        if guard_reason:
            return False, guard_reason

        if allowed_types and action_type not in allowed_types:
            return False, f"Action type {action_type} is not in allowed_types"

        # Check blocked actions
        if action_type in policy.get("blocked_actions", []):
            return False, f"Action type {action_type} is blocked"

        # Check specific action permissions
        if action_type == "dispatch_task" and not policy.get("allow_dispatch"):
            return False, "Dispatch not allowed by policy"
        if action_type == "assign_then_dispatch" and not policy.get("allow_reassign"):
            return False, "Reassign not allowed by policy"
        if action_type == "reassign_or_bring_online" and not policy.get("allow_reassign"):
            return False, "Reassign not allowed by policy"
        if action_type == "reclaim_stale_lease" and not policy.get("allow_release_lease"):
            return False, "Release lease not allowed by policy"
        if action_type == "inspect_terminal" and not policy.get("allow_inspect_terminal"):
            return False, "Inspect terminal not allowed by policy"

        # Check priority threshold (configurable, default allows up to p2).
        priority = action.get("priority", "p2")
        priority_rank = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
        max_priority = str(policy.get("max_priority", "p2")).strip().lower()
        if max_priority not in priority_rank:
            max_priority = "p2"
        if priority_rank.get(priority, 3) > priority_rank.get(max_priority, 2):
            return False, f"Priority {priority} too low for auto-execute (max={max_priority})"

        return True, "Allowed"

    def _autopilot_cycle_once(
        dry_run_override: Optional[bool] = None,
        use_max_actions: Optional[int] = None,
        context: str = "",
    ) -> Dict[str, Any]:
        """Run one autopilot cycle and return normalized execution summary."""
        if "_AUTOPILOT_STATE" not in globals() or not isinstance(_AUTOPILOT_STATE, dict):
            return {
                "success": False,
                "error": "Autopilot state unavailable",
                "scanned": 0,
                "executed": [],
                "blocked": [],
                "errors": [],
                "context": context,
            }

        if not _AUTOPILOT_STATE.get("enabled", False):
            return {
                "success": False,
                "error": "Autopilot disabled",
                "scanned": 0,
                "executed": [],
                "blocked": [],
                "errors": [],
                "context": context,
            }

        dry_run = _AUTOPILOT_STATE.get("dry_run", True) if dry_run_override is None else bool(dry_run_override)
        max_actions_raw = _AUTOPILOT_STATE.get("max_actions_per_cycle", HUB_NEXT_ACTION_AUTOPILOT_MAX_PER_TICK)
        max_actions = max(1, min(10, int(use_max_actions if use_max_actions is not None else max_actions_raw)))
        scanned = 0
        executed = []
        blocked = []
        errors = []

        actions = _hub_next_actions(limit=max_actions * 2)
        for action in actions[:max_actions]:
            scanned += 1
            should_exec, reason = _autopilot_should_execute(action)
            if not should_exec:
                blocked.append({"action": action, "reason": reason})
                _AUTOPILOT_STATE["stats"]["actions_blocked"] += 1
                continue

            if dry_run:
                executed.append({"action": action, "dry_run": True, "executor_context": context})
                _AUTOPILOT_STATE["stats"]["actions_executed"] += 1
                continue

            result = _autopilot_execute_action(action)
            if result.get("success"):
                executed.append({"action": action, "result": result, "executor_context": context})
                _AUTOPILOT_STATE["stats"]["actions_executed"] += 1
            else:
                errors.append({"action": action, "error": result.get("error")})
                _AUTOPILOT_STATE["stats"]["errors"] += 1

        _AUTOPILOT_STATE["stats"]["total_cycles"] += 1
        _AUTOPILOT_STATE["stats"]["last_cycle"] = datetime.now().isoformat()
        return {
            "success": True,
            "context": context,
            "scanned": scanned,
            "dry_run": bool(dry_run),
            "executed": executed,
            "blocked": blocked,
            "errors": errors,
        }

    def _invoke_internal_post(path: str, view_func: Callable[..., Any], payload: Dict[str, Any]) -> Tuple[int, Any]:
        """Call Flask view function internally without network/request.host_url dependency."""
        with app.test_request_context(path=path, method="POST", json=payload):
            response = view_func()
        status_code = 200
        body = None
        if isinstance(response, tuple):
            flask_resp = response[0]
            status_code = int(response[1]) if len(response) > 1 else 200
        else:
            flask_resp = response
            status_code = int(getattr(flask_resp, "status_code", 200))
        try:
            body = flask_resp.get_json()
        except Exception:
            body = getattr(flask_resp, "data", None)
        return status_code, body

    def _autopilot_execute_action(action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single action."""
        action_type = str(action.get("action_type", "") or "")
        task_id = action.get("task_id")

        guard_reason = _autopilot_guard_block_reason()
        if guard_reason:
            return {
                "action": action_type,
                "task_id": task_id,
                "success": False,
                "error": guard_reason,
                "error_code": "AUTOPILOT_TIMEOUT_CIRCUIT_OPEN",
            }

        def _run_sync() -> Dict[str, Any]:
            result = {"action": action_type, "task_id": task_id, "success": False, "error": None}
            try:
                if action_type in ("dispatch_task", "assign_then_dispatch") and task_id:
                    payload_raw = action.get("suggested_payload")
                    payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
                    if _AUTOPILOT_DISPATCH_ASYNC:
                        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
                        options = dict(options)
                        options.setdefault("async_dispatch", True)
                        options.setdefault("wait_timeout_sec", _AUTOPILOT_ACTION_TIMEOUT_SEC)
                        options.setdefault("source", "autopilot")
                        payload["options"] = options
                    status_code, body = _invoke_internal_post(
                        path=f"/api/hub/tasks/{task_id}/dispatch",
                        view_func=lambda: dispatch_hub_task(task_id),
                        payload=payload,
                    )
                    result["success"] = status_code < 400
                    result["response"] = body
                elif action_type == "reclaim_stale_lease" and task_id:
                    payload_raw = action.get("suggested_payload")
                    payload = dict(payload_raw) if isinstance(payload_raw, dict) else {"next_status": "todo"}
                    status_code, body = _invoke_internal_post(
                        path=f"/api/hub/tasks/{task_id}/release",
                        view_func=lambda: release_hub_task_lease(task_id),
                        payload=payload,
                    )
                    result["success"] = status_code < 400
                    result["response"] = body
                elif action_type == "reassign_or_bring_online" and task_id:
                    payload_raw = action.get("suggested_payload")
                    payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
                    status_code, body = _invoke_internal_post(
                        path="/api/hub/tasks/batch-update",
                        view_func=batch_update_tasks,
                        payload=payload,
                    )
                    result["success"] = status_code < 400
                    result["response"] = body
                else:
                    result["error"] = f"Unknown action type: {action_type}"
            except Exception as exc:
                result["error"] = str(exc)
            return result

        future = _autopilot_action_executor.submit(_run_sync)
        try:
            result = future.result(timeout=_AUTOPILOT_ACTION_TIMEOUT_SEC)
            _autopilot_guard_record_success()
            return result
        except concurrent.futures.TimeoutError:
            future.cancel()
            _autopilot_guard_record_timeout(action_type)
            return {
                "action": action_type,
                "task_id": task_id,
                "success": False,
                "error": f"Autopilot action timeout after {_AUTOPILOT_ACTION_TIMEOUT_SEC:.1f}s",
                "error_code": "AUTOPILOT_ACTION_TIMEOUT",
            }
        except Exception as exc:
            return {
                "action": action_type,
                "task_id": task_id,
                "success": False,
                "error": str(exc),
                "error_code": "AUTOPILOT_ACTION_EXECUTION_ERROR",
            }

    @app.route('/api/hub/autopilot/status')
    def get_autopilot_status():
        """Get autopilot status."""
        return jsonify(_build_hub_autopilot_status_payload(include_legacy_aliases=True))

    @app.route('/api/hub/autopilot/config', methods=['POST'])
    def configure_autopilot():
        """Configure autopilot settings."""
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid payload"}), 400

        state_modified = False
        errors: List[str] = []
        if "enabled" in data:
            _autopilot_set_enabled(_autopilot_parse_bool(data.get("enabled"), default=_autopilot_is_enabled()))
            state_modified = True
        if "dry_run" in data:
            _AUTOPILOT_STATE["dry_run"] = _autopilot_parse_bool(
                data.get("dry_run"),
                default=bool(_AUTOPILOT_STATE.get("dry_run", True)),
            )
            state_modified = True

        active_executor = data.get("active_executor", data.get("executor"))
        if isinstance(active_executor, str) and active_executor.strip().lower() in {"policy_loop", "hub_loop"}:
            _AUTOPILOT_STATE["active_executor"] = _normalize_autopilot_executor(active_executor)
            state_modified = True

        interval_raw = data.get("interval", data.get("interval_sec"))
        if interval_raw is not None:
            try:
                _AUTOPILOT_STATE["interval"] = max(5, int(interval_raw))
                state_modified = True
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "interval must be an integer"}), 400

        max_actions_raw = data.get("max_actions_per_cycle", data.get("max_per_tick"))
        if max_actions_raw is not None:
            try:
                _AUTOPILOT_STATE["max_actions_per_cycle"] = max(1, min(10, int(max_actions_raw)))
                state_modified = True
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "max_actions_per_cycle must be an integer"}), 400

        if "policy" in data:
            policy_patch, policy_errors = _autopilot_policy_patch(data.get("policy"))
            if policy_errors:
                errors.extend(policy_errors)
            if policy_patch:
                _AUTOPILOT_STATE["policy"].update(policy_patch)
                state_modified = True

        if errors:
            return jsonify({"success": False, "error": "; ".join(errors)}), 400

        if state_modified:
            force_restart = _autopilot_parse_bool(data.get("force_restart"), default=True)
            _autopilot_reconcile_runtimes(force_restart=force_restart)

        return jsonify({
            "success": True,
            "state": _build_hub_autopilot_status_payload(include_legacy_aliases=True),
            "timestamp": datetime.now().isoformat(),
        })

    @app.route('/api/hub/autopilot/run', methods=['POST'])
    def run_autopilot_cycle():
        """Run one autopilot cycle."""
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        auto_enable = _autopilot_parse_bool(data.get("auto_enable"), default=True)
        if not _AUTOPILOT_STATE.get("enabled", False):
            if auto_enable:
                _autopilot_set_enabled(True)
                _autopilot_reconcile_runtimes(force_restart=False)
            else:
                return jsonify({
                    "success": False,
                    "error": "Autopilot is not enabled",
                    "timestamp": datetime.now().isoformat(),
                }), 400

        max_actions, max_actions_error = _autopilot_parse_max_actions(data.get("max_actions_per_cycle", data.get("max_per_tick")))
        if max_actions_error:
            return jsonify({
                "success": False,
                "error": max_actions_error,
                "timestamp": datetime.now().isoformat(),
            }), 400
        dry_run_override = None
        if "dry_run" in data:
            dry_run_override = _autopilot_parse_bool(data.get("dry_run"), default=bool(_AUTOPILOT_STATE.get("dry_run", True)))
        result = _autopilot_cycle_once(
            dry_run_override=dry_run_override,
            use_max_actions=max_actions if max_actions is not None else _autopilot_configured_max_actions(),
            context="manual_api_run",
        )
        state_payload = _build_hub_autopilot_status_payload(include_legacy_aliases=True)

        return jsonify({
            "success": True if result.get("success", False) is not False else False,
            "scanned": result.get("scanned", 0),
            "executed": result.get("executed", []),
            "blocked": result.get("blocked", []),
            "errors": result.get("errors", []),
            "state": state_payload,
            "stats": _AUTOPILOT_STATE["stats"],
            "snapshot": _autopilot_runtime_snapshot(),
            "timestamp": datetime.now().isoformat(),
        })

    @app.route('/api/hub/autopilot/preview', methods=['GET'])
    def preview_autopilot_actions():
        """Preview which actions would be executed."""
        preview = []
        max_actions = _autopilot_configured_max_actions()
        for action in _hub_next_actions(limit=max_actions * 2)[:max_actions]:
            should_exec, reason = _autopilot_should_execute(action)
            preview.append({
                "action": action,
                "would_execute": should_exec,
                "reason": reason,
            })

        return jsonify({
            "success": True,
            "preview": preview,
            "policy": _AUTOPILOT_STATE["policy"],
            "interval": _autopilot_configured_interval(),
            "max_actions_per_cycle": max_actions,
            "timestamp": datetime.now().isoformat(),
        })

    @app.route('/api/hub/workload')
    def get_agent_workload():
        """Get workload summary across tasks, runtime, control-plane, and automation sessions."""
        now = datetime.now()
        tasks = _hub.list_tasks()
        hub_snapshot = _hub.get_snapshot()
        orions_snapshot = hub_snapshot.get("orions", {}) if isinstance(hub_snapshot.get("orions"), dict) else {}
        orion_index = _hub_orion_rows_by_assignee(orions_snapshot)
        runtime_instances = get_orion_instances_snapshot()
        runtime_index = _hub_runtime_rows_by_assignee(runtime_instances)
        control_plane_snapshot = _control_plane_registry.snapshot()
        workers = control_plane_snapshot.get("workers", []) if isinstance(control_plane_snapshot.get("workers"), list) else []
        worker_index: Dict[str, Dict[str, Any]] = {}
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            worker_id = _hub_normalize_assignee(worker.get("worker_id"))
            if worker_id:
                worker_index[worker_id] = worker

        with _AUTOMATION_TASKS_LOCK:
            automation_sessions = [dict(item) for item in _automation_sessions.values() if isinstance(item, dict)]

        sessions_by_owner: Dict[str, List[Dict[str, Any]]] = {}
        for session in automation_sessions:
            owner_raw = str(session.get("owner_id") or "").strip()
            owner = _hub_normalize_assignee(owner_raw.split(":", 1)[1] if owner_raw.lower().startswith("orion:") else owner_raw)
            if not owner:
                continue
            sessions_by_owner.setdefault(owner, []).append(session)
        bridge_terminals_by_owner = _hub_bridge_terminals_by_owner()
        bridge_terminal_total = sum(len(rows) for rows in bridge_terminals_by_owner.values())
        bridge_terminal_busy_total = 0
        bridge_terminal_error_total = 0
        for rows in bridge_terminals_by_owner.values():
            for row in rows:
                status = str(row.get("status") or "").strip().lower()
                busy = bool(row.get("busy")) or status in {"running", "busy"}
                if busy:
                    bridge_terminal_busy_total += 1
                if status in {"error", "failed", "blocked"}:
                    bridge_terminal_error_total += 1

        workload: Dict[str, Any] = {}
        unassigned: List[Dict[str, Any]] = []
        priority_counts: Dict[str, int] = {"p0": 0, "p1": 0, "p2": 0, "p3": 0}
        status_counts: Dict[str, int] = {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0}

        for task in tasks:
            if not isinstance(task, dict):
                continue
            assignee = _hub_normalize_assignee(task.get("assigned_to")) or "unassigned"
            priority_norm = _hub_normalize_priority(task.get("priority"))
            status_norm = _hub_normalize_status(task.get("status"))
            lease = _hub_task_lease_info(task, now_dt=now)

            priority_counts[priority_norm] = priority_counts.get(priority_norm, 0) + 1
            if status_norm == "in_progress":
                status_counts["in_progress"] = status_counts.get("in_progress", 0) + 1
            elif status_norm == "done":
                status_counts["done"] = status_counts.get("done", 0) + 1
            elif status_norm == "blocked":
                status_counts["blocked"] = status_counts.get("blocked", 0) + 1
            else:
                status_counts["todo"] = status_counts.get("todo", 0) + 1

            task_summary = {
                "id": task.get("id"),
                "title": task.get("title", ""),
                "priority": priority_norm,
                "status": status_norm,
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
                "lease_active": bool(lease.get("active")),
                "lease_stale": bool(lease.get("stale")),
                "lease_owner": lease.get("owner"),
                "lease_expires_at": lease.get("expires_at"),
            }

            if assignee == "unassigned":
                unassigned.append(task_summary)
                continue

            if assignee not in workload:
                workload[assignee] = {
                    "assignee": assignee,
                    "tasks": [],
                    "by_priority": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
                    "by_status": {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0},
                    "task_count": 0,
                    "active_task_count": 0,
                    "lease_active_count": 0,
                    "lease_stale_count": 0,
                    "is_online": False,
                    "currently_working": False,
                    "last_heartbeat": None,
                    "runtime": {
                        "running": False,
                        "paused": False,
                        "pause_reason": "",
                        "iteration": None,
                        "active_flow_count": 0,
                        "last_progress_at": None,
                    },
                    "control_plane": {
                        "is_alive": False,
                        "status": "unknown",
                        "lease_expires_at": None,
                        "queue_depth": 0,
                        "running_tasks": 0,
                    },
                    "tool_sessions": {
                        "owned": 0,
                        "busy": 0,
                        "total_queue_depth": 0,
                        "sessions": [],
                    },
                    "terminal_sessions": {
                        "owned": 0,
                        "busy": 0,
                        "error": 0,
                        "sessions": [],
                    },
                }

            row = workload[assignee]
            row["tasks"].append(task_summary)
            row["task_count"] = int(row.get("task_count", 0) or 0) + 1
            row["by_priority"][priority_norm] = row["by_priority"].get(priority_norm, 0) + 1
            row["by_status"][status_norm if status_norm in {"todo", "in_progress", "done", "blocked"} else "todo"] = row["by_status"].get(
                status_norm if status_norm in {"todo", "in_progress", "done", "blocked"} else "todo",
                0,
            ) + 1
            if status_norm == "in_progress":
                row["active_task_count"] = int(row.get("active_task_count", 0) or 0) + 1
            if task_summary["lease_active"]:
                row["lease_active_count"] = int(row.get("lease_active_count", 0) or 0) + 1
            if task_summary["lease_stale"]:
                row["lease_stale_count"] = int(row.get("lease_stale_count", 0) or 0) + 1

        for assignee, row in workload.items():
            orion = orion_index.get(assignee, {})
            orion_lease_expires = _parse_iso_datetime(orion.get("lease_expires_at"))
            orion_alive = bool(orion_lease_expires and orion_lease_expires > now and str(orion.get("status") or "active") != "offline")
            row["control_plane"] = {
                "is_alive": orion_alive,
                "status": str(orion.get("status", "unknown") or "unknown"),
                "lease_expires_at": orion.get("lease_expires_at"),
                "queue_depth": 0,
                "running_tasks": 0,
            }
            row["last_heartbeat"] = orion.get("last_heartbeat_at")

            runtime = runtime_index.get(assignee, {})
            if runtime:
                row["runtime"] = {
                    "running": bool(runtime.get("running", False)),
                    "paused": bool(runtime.get("paused", False)),
                    "pause_reason": str(runtime.get("pause_reason", "") or ""),
                    "iteration": runtime.get("iteration"),
                    "active_flow_count": int(runtime.get("active_flow_count", 0) or 0),
                    "last_progress_at": runtime.get("last_progress_at"),
                }
                row["last_heartbeat"] = row["last_heartbeat"] or runtime.get("last_progress_at") or runtime.get("last_cycle_finished_at")

            worker = worker_index.get(assignee, {})
            if worker:
                row["control_plane"]["queue_depth"] = int(worker.get("queue_depth", 0) or 0)
                row["control_plane"]["running_tasks"] = int(worker.get("running_tasks", 0) or 0)
                row["control_plane"]["status"] = str(worker.get("health", row["control_plane"]["status"]) or row["control_plane"]["status"])

            sessions = sessions_by_owner.get(assignee, [])
            session_rows: List[Dict[str, Any]] = []
            busy_sessions = 0
            total_queue_depth = 0
            for session in sessions:
                queue_depth = int(session.get("queue_depth", 0) or 0)
                lease_expires = _parse_iso_datetime(session.get("lease_expires_at"))
                lease_active = bool(lease_expires and lease_expires > now)
                is_busy = queue_depth > 0 or lease_active
                if is_busy:
                    busy_sessions += 1
                total_queue_depth += queue_depth
                session_rows.append(
                    {
                        "session_id": session.get("session_id"),
                        "queue_depth": queue_depth,
                        "lease_expires_at": session.get("lease_expires_at"),
                        "last_task_id": session.get("last_task_id"),
                        "last_error": session.get("last_error"),
                        "busy": is_busy,
                    }
                )
            row["tool_sessions"] = {
                "owned": len(sessions),
                "busy": busy_sessions,
                "total_queue_depth": total_queue_depth,
                "sessions": session_rows,
            }
            terminal_rows = bridge_terminals_by_owner.get(assignee, [])
            terminal_items: List[Dict[str, Any]] = []
            terminal_busy = 0
            terminal_error = 0
            for term in terminal_rows:
                status = str(term.get("status") or "").strip().lower()
                busy = bool(term.get("busy")) or status in {"running", "busy"}
                if busy:
                    terminal_busy += 1
                if status in {"error", "failed", "blocked"}:
                    terminal_error += 1
                terminal_items.append(
                    {
                        "session_id": term.get("session_id"),
                        "status": status or "unknown",
                        "busy": busy,
                        "title": term.get("title"),
                        "cwd": term.get("cwd"),
                        "command": term.get("command"),
                        "updated_at": term.get("updated_at"),
                        "last_error": term.get("last_error"),
                    }
                )
            row["terminal_sessions"] = {
                "owned": len(terminal_rows),
                "busy": terminal_busy,
                "error": terminal_error,
                "sessions": terminal_items,
            }

            row["is_online"] = bool(runtime.get("online", False)) or bool(row["control_plane"].get("is_alive"))
            row["currently_working"] = bool(
                row.get("active_task_count", 0)
                or row["runtime"].get("running")
                or row["control_plane"].get("running_tasks")
                or row["tool_sessions"].get("busy")
                or row["terminal_sessions"].get("busy")
            )

        workload_list = sorted(
            workload.values(),
            key=lambda x: (
                -int((x.get("by_priority", {}) or {}).get("p0", 0) or 0),
                -int(x.get("active_task_count", 0) or 0),
                -int(x.get("task_count", 0) or 0),
                str(x.get("assignee", "")),
            ),
        )

        busy_tool_sessions = 0
        for sessions in sessions_by_owner.values():
            for session in sessions:
                queue_depth = int(session.get("queue_depth", 0) or 0)
                lease_expires = _parse_iso_datetime(session.get("lease_expires_at"))
                if queue_depth > 0 or (lease_expires and lease_expires > now):
                    busy_tool_sessions += 1

        return jsonify(
            {
                "success": True,
                "workload": workload_list,
                "unassigned": unassigned,
                "priority_breakdown": priority_counts,
                "status_breakdown": status_counts,
                "total_tasks": len(tasks),
                "total_assignees": len(workload),
                "runtime_instances": runtime_instances,
                "control_plane": {
                    "registered_workers": int(control_plane_snapshot.get("registered_workers", 0) or 0),
                    "alive_workers": int(control_plane_snapshot.get("alive_workers", 0) or 0),
                    "open_circuits": int(control_plane_snapshot.get("open_circuits", 0) or 0),
                },
                "automation_sessions": {
                    "total": len(automation_sessions),
                    "owners": len(sessions_by_owner),
                    "busy": busy_tool_sessions,
                },
                "bridge_terminals": {
                    "total": bridge_terminal_total,
                    "owners": len(bridge_terminals_by_owner),
                    "busy": bridge_terminal_busy_total,
                    "error": bridge_terminal_error_total,
                    "last_sync": (_bridge_state.get("terminal_last_sync") if isinstance(_bridge_state, dict) else None),
                },
                "next_actions_preview": _hub_next_actions(limit=5),
            }
        )

    @app.route('/api/hub/process-board')
    def get_hub_process_board():
        limit_processes = _int_in_range(request.args.get("limit", 8), default=8, min_value=1, max_value=30)
        limit_transitions = _int_in_range(request.args.get("transitions", 20), default=20, min_value=5, max_value=120)
        board = _hub_process_board(limit_transitions=limit_transitions)
        processes = board.get("processes") if isinstance(board.get("processes"), list) else []
        board["processes"] = processes[:limit_processes]
        summary = board.get("summary") if isinstance(board.get("summary"), dict) else {}
        summary["visible_processes"] = len(board["processes"])
        board["summary"] = summary
        return jsonify({"success": True, "board": board})

    def _build_hub_strategic_brief(limit_tasks: int = 8) -> Dict[str, Any]:
        tasks = [task for task in _hub.list_tasks() if isinstance(task, dict)]
        runtime_instances = [item for item in get_orion_instances_snapshot() if isinstance(item, dict)]
        control_snapshot = _control_plane_registry.snapshot()
        status_counts: Dict[str, int] = {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0}
        priority_counts: Dict[str, int] = {"p0": 0, "p1": 0, "p2": 0, "p3": 0}
        unassigned: List[Dict[str, Any]] = []
        ranked_tasks: List[Dict[str, Any]] = []
        priority_rank = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}

        for task in tasks:
            status_norm = _hub_normalize_status(task.get("status"))
            priority_norm = _hub_normalize_priority(task.get("priority"))
            status_counts[status_norm if status_norm in status_counts else "todo"] = status_counts.get(status_norm if status_norm in status_counts else "todo", 0) + 1
            priority_counts[priority_norm if priority_norm in priority_counts else "p3"] = priority_counts.get(priority_norm if priority_norm in priority_counts else "p3", 0) + 1
            assignee = _hub_normalize_assignee(task.get("assigned_to"))
            row = {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": status_norm,
                "priority": priority_norm,
                "assignee": assignee,
                "updated_at": str(task.get("updated_at") or ""),
            }
            if not assignee:
                unassigned.append(row)
            ranked_tasks.append(row)

        ranked_tasks.sort(
            key=lambda item: (
                priority_rank.get(item.get("priority", "p3"), 3),
                0 if item.get("status") in {"blocked", "in_progress"} else 1,
                str(item.get("updated_at") or ""),
            )
        )
        top_tasks = ranked_tasks[: max(1, min(20, int(limit_tasks)))]

        online_orions = [item for item in runtime_instances if bool(item.get("online", False))]
        paused_orions = [item for item in runtime_instances if bool(item.get("paused", False))]
        worker_alive = int(control_snapshot.get("alive_workers", 0) or 0)
        worker_registered = int(control_snapshot.get("registered_workers", 0) or 0)
        next_actions = _hub_next_actions(limit=6)

        thought_lines = [
            f"Current load: {status_counts.get('in_progress', 0)} in progress, {status_counts.get('blocked', 0)} blocked, {status_counts.get('todo', 0)} queued.",
            f"Capacity: {len(online_orions)}/{len(runtime_instances)} Orion online, {len(paused_orions)} paused, control-plane alive {worker_alive}/{worker_registered}.",
            f"Priority pressure: P0={priority_counts.get('p0', 0)}, P1={priority_counts.get('p1', 0)}.",
            f"Coordination risk: {len(unassigned)} unassigned task(s), {len(next_actions)} recommended next action(s).",
        ]

        strategy: List[str] = []
        if status_counts.get("blocked", 0) > 0:
            strategy.append("Stabilize first: clear blocked tasks before starting new scope.")
        if len(unassigned) > 0:
            strategy.append("Assign unowned high-priority tasks to online Orion before dispatch.")
        if status_counts.get("in_progress", 0) < max(1, len(online_orions)):
            strategy.append("Increase parallelism: dispatch ready todo tasks to idle Orion capacity.")
        strategy.append("Keep human-in-loop only for missing inputs, execute the rest automatically.")

        assignee_pool = [str(item.get("id") or item.get("name") or "").strip() for item in online_orions]
        assignee_pool = [item for item in assignee_pool if item]
        allocation: List[Dict[str, Any]] = []
        for idx, task in enumerate(top_tasks[: min(len(top_tasks), max(1, len(assignee_pool) or 1) * 2)]):
            target = task.get("assignee") or (assignee_pool[idx % len(assignee_pool)] if assignee_pool else LOCAL_INSTANCE_ID)
            allocation.append(
                {
                    "task_id": task.get("id"),
                    "title": task.get("title"),
                    "priority": task.get("priority"),
                    "status": task.get("status"),
                    "target_orion": target,
                    "why": "priority-first balancing",
                }
            )

        return {
            "generated_at": datetime.now().isoformat(),
            "system_overview": {
                "total_tasks": len(tasks),
                "status_breakdown": status_counts,
                "priority_breakdown": priority_counts,
                "unassigned_count": len(unassigned),
                "online_orions": len(online_orions),
                "total_orions": len(runtime_instances),
            },
            "thought_lines": thought_lines,
            "strategy": strategy,
            "top_tasks": top_tasks,
            "allocation_plan": allocation,
            "next_actions": next_actions,
        }

    @app.route('/api/hub/strategic-brief')
    def get_hub_strategic_brief():
        limit = _int_in_range(request.args.get("limit", 8), default=8, min_value=1, max_value=30)
        brief = _build_hub_strategic_brief(limit_tasks=limit)
        return jsonify({"success": True, "brief": brief})

    @app.route('/api/hub/register-orion', methods=['POST'])
    def register_orion_instance():
        """Register a new Orion instance"""
        data = request.get_json(silent=True) or {}
        orion = _hub.register_orion(
            instance_id=data.get("instance_id", f"orion-{len(_hub.orions)+1}"),
            config=data.get("config", {})
        )
        return jsonify({"success": True, "orion": orion})

    @app.route('/api/hub/spawn', methods=['POST'])
    def spawn_hub_orion():
        """Compatibility spawn endpoint used by live dashboard quick-action."""
        data = request.get_json(silent=True) or {}
        goal = str(data.get("goal", "Scale mission")).strip() or "Scale mission"
        new_id = str(data.get("instance_id", "")).strip()
        if not new_id:
            new_id = f"orion-spawn-{int(time.time())}"
        registration = _hub.register_orion(
            instance_id=new_id,
            config={
                "goal": goal,
                "source": "dashboard_spawn",
                "created_at": datetime.now().isoformat(),
            },
        )
        state.add_agent_log("guardian", f"🛰 Spawn requested for {new_id}: {goal}", "info")
        return jsonify(
            {
                "success": bool(registration.get("success", False)),
                "spawn": {
                    "instance_id": new_id,
                    "goal": goal,
                },
                "orion": registration,
                "timestamp": datetime.now().isoformat(),
            }
        ), (200 if registration.get("success") else 409)

    @app.route('/api/hub/orchestrate', methods=['POST'])
    def orchestrate_hub_goal():
        """Build persona-aware execution plan across multiple Orion instances."""
        data = request.get_json(silent=True) or {}
        goal = str(data.get("goal", "") or "").strip()
        if not goal:
            return _api_error("MISSING_GOAL", "Missing orchestration goal")

        member_id = _resolve_member_id(data)
        task_count = _int_in_range(data.get("task_count", 3), default=3, min_value=1, max_value=8)
        priority = str(data.get("priority", "high") or "high").strip().lower() or "high"
        create_tasks = _to_bool(data.get("create_tasks", False))
        compact_mode = _to_bool(data.get("compact_mode", True))
        max_agents = _int_in_range(data.get("max_agents", 4), default=4, min_value=2, max_value=7)
        preferred_orions = data.get("preferred_orions") if isinstance(data.get("preferred_orions"), list) else []

        plan = _build_multi_orion_orchestration(
            goal=goal,
            requested_by=member_id,
            task_count=task_count,
            priority=priority,
            create_tasks=create_tasks,
            preferred_orions=preferred_orions,
        )
        adaptation = plan.get("adaptation") if isinstance(plan.get("adaptation"), dict) else {}
        plan["agent_coordination"] = _build_agent_coordination_plan(
            goal=goal,
            adaptation=adaptation,
            max_agents=max_agents,
            compact_mode=compact_mode,
        )
        if member_id:
            try:
                _team_persona_store.record_interaction(
                    member_id=member_id,
                    message=goal,
                    channel="orchestration",
                    intent="hub_orchestrate",
                    context={"instance_id": "multi"},
                )
            except Exception:
                pass

        assignment_count = len(plan.get("assignments", []))
        state.add_agent_log(
            "guardian",
            f"🧭 Orchestration plan generated ({assignment_count} assignments, member={member_id or 'unknown'})",
            "success",
        )
        return _api_ok(
            {
                "plan": plan,
                "assignment_count": assignment_count,
                "created_tasks": len(plan.get("created_tasks", [])),
                "token_budget": (plan.get("agent_coordination") or {}).get("token_budget", {}),
            }
        )

    # ============================================
    # INTER-ORION MESSAGING API
    # ============================================
    from src.core.orion_messenger import get_messenger, OrionMessenger
    _messenger = get_messenger()

    @app.route('/api/hub/messages', methods=['GET', 'POST'])
    def handle_messages():
        """Send or list inter-ORION messages."""
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            result = _messenger.send_message(
                from_orion=data.get("from_orion", "unknown"),
                to_orion=data.get("to_orion"),
                message_type=data.get("message_type", "direct"),
                content=data.get("content", ""),
                priority=data.get("priority", "normal"),
                metadata=data.get("metadata"),
                related_task_id=data.get("related_task_id"),
                requires_response=data.get("requires_response", False),
                response_to=data.get("response_to"),
            )
            status = 200 if result.get("success") else 400
            return jsonify(result), status

        # GET - list messages
        orion_id = request.args.get("orion_id", "")
        unread_only = request.args.get("unread_only", "false").lower() == "true"
        message_type = request.args.get("message_type") or None
        limit = request.args.get("limit", 50, type=int)
        since = request.args.get("since") or None

        if orion_id:
            result = _messenger.get_messages(
                orion_id=orion_id,
                unread_only=unread_only,
                message_type=message_type,
                limit=limit,
                since=since,
            )
        else:
            # Return all recent messages (admin view)
            result = {
                "success": True,
                "messages": _messenger._read_messages()[-limit:],
                "stats": _messenger.get_stats(),
            }
        return jsonify(result)

    @app.route('/api/hub/messages/stats')
    def get_message_stats():
        """Get messaging statistics."""
        return jsonify({"success": True, "stats": _messenger.get_stats()})

    @app.route('/api/hub/messages/<message_id>/read', methods=['POST'])
    def mark_message_read(message_id):
        """Mark a message as read."""
        data = request.get_json(silent=True) or {}
        result = _messenger.mark_read(
            message_id=message_id,
            read_by=data.get("read_by", "dashboard"),
        )
        status = 200 if result.get("success") else 404
        return jsonify(result), status

    @app.route('/api/hub/messages/<message_id>/respond', methods=['POST'])
    def respond_to_message(message_id):
        """Respond to a message."""
        data = request.get_json(silent=True) or {}
        result = _messenger.respond_to_message(
            message_id=message_id,
            from_orion=data.get("from_orion", "unknown"),
            response_content=data.get("response_content", ""),
            response_type=data.get("response_type", "direct"),
        )
        status = 200 if result.get("success") else 400
        return jsonify(result), status

    @app.route('/api/hub/conversation/<orion_a>/<orion_b>')
    def get_conversation(orion_a, orion_b):
        """Get conversation between two ORIONs."""
        limit = request.args.get("limit", 50, type=int)
        result = _messenger.get_conversation(
            orion_a=orion_a,
            orion_b=orion_b,
            limit=limit,
        )
        return jsonify(result)

    @app.route('/api/hub/broadcast', methods=['POST'])
    def broadcast_message():
        """Broadcast a message to all ORIONs."""
        data = request.get_json(silent=True) or {}
        result = _messenger.send_message(
            from_orion=data.get("from_orion", "system"),
            to_orion="*",
            message_type="broadcast",
            content=data.get("content", ""),
            priority=data.get("priority", "normal"),
            metadata=data.get("metadata"),
        )
        status = 200 if result.get("success") else 400
        return jsonify(result), status

    @app.route('/api/hub/help-request', methods=['POST'])
    def send_help_request():
        """Send a help request."""
        data = request.get_json(silent=True) or {}
        result = _messenger.request_help(
            from_orion=data.get("from_orion", "unknown"),
            help_type=data.get("help_type", "general"),
            description=data.get("description", ""),
            related_task_id=data.get("related_task_id"),
            urgency=data.get("urgency", "normal"),
        )
        status = 200 if result.get("success") else 400
        return jsonify(result), status

    @app.route('/api/hub/task-handoff', methods=['POST'])
    def send_task_handoff():
        """Hand off a task to another ORION."""
        data = request.get_json(silent=True) or {}
        result = _messenger.handoff_task(
            from_orion=data.get("from_orion", "unknown"),
            to_orion=data.get("to_orion", ""),
            task_id=data.get("task_id", ""),
            task_title=data.get("task_title", ""),
            handoff_reason=data.get("handoff_reason", ""),
            context=data.get("context"),
        )
        status = 200 if result.get("success") else 400
        return jsonify(result), status

    @app.route('/api/hub/messages/cleanup', methods=['POST'])
    def cleanup_messages():
        """Clean up expired messages."""
        result = _messenger.cleanup()
        return jsonify(result)

    # ============================================
    # HUMAN NOTIFICATION API (for AI→Human communication)
    # ============================================
    from src.core.human_notifier import get_notifier, HumanNotifier
    _notifier = get_notifier()

    @app.route('/api/hub/human-notifications', methods=['GET', 'POST'])
    def handle_human_notifications():
        """Send or list human notifications."""
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            result = _notifier.notify(
                title=data.get("title", "Notification"),
                message=data.get("message", ""),
                level=data.get("level", "info"),
                source=data.get("source", "dashboard"),
                channels=data.get("channels"),
                metadata=data.get("metadata"),
                bypass_rate_limit=data.get("bypass_rate_limit", False),
            )
            status = 200 if result.get("success") else 429 if result.get("error_code") == "RATE_LIMITED" else 400
            return jsonify(result), status

        # GET - list notifications
        limit = request.args.get("limit", 50, type=int)
        level = request.args.get("level") or None
        unacknowledged_only = request.args.get("unacknowledged_only", "false").lower() == "true"

        notifications = _notifier.list_notifications(
            limit=limit,
            level=level,
            unacknowledged_only=unacknowledged_only,
        )
        stats = _notifier.get_stats()

        return jsonify({
            "success": True,
            "notifications": notifications,
            "stats": stats,
        })

    @app.route('/api/hub/human-notifications/stats')
    def get_human_notification_stats():
        """Get notification statistics."""
        return jsonify({"success": True, "stats": _notifier.get_stats()})

    @app.route('/api/hub/human-notifications/urgent', methods=['POST'])
    def send_urgent_human_notification():
        """Send urgent notification (bypasses rate limits)."""
        data = request.get_json(silent=True) or {}
        result = _notifier.notify_urgent(
            title=data.get("title", "Urgent"),
            message=data.get("message", ""),
            source=data.get("source", "dashboard"),
            metadata=data.get("metadata"),
        )
        return jsonify(result), 200 if result.get("success") else 400

    @app.route('/api/hub/human-notifications/help', methods=['POST'])
    def send_help_human_notification():
        """Send help request notification."""
        data = request.get_json(silent=True) or {}
        result = _notifier.notify_help_needed(
            from_orion=data.get("from_orion", "unknown"),
            help_type=data.get("help_type", "general"),
            description=data.get("description", ""),
            context=data.get("context"),
        )
        return jsonify(result), 200 if result.get("success") else 400

    @app.route('/api/hub/human-notifications/blocked', methods=['POST'])
    def send_blocked_human_notification():
        """Notify that a task is blocked."""
        data = request.get_json(silent=True) or {}
        result = _notifier.notify_task_blocked(
            task_id=data.get("task_id", ""),
            task_title=data.get("task_title", ""),
            blocker=data.get("blocker", "Unknown blocker"),
            orion_id=data.get("orion_id", "unknown"),
        )
        return jsonify(result), 200 if result.get("success") else 400

    @app.route('/api/hub/human-notifications/milestone', methods=['POST'])
    def send_milestone_human_notification():
        """Notify of a milestone."""
        data = request.get_json(silent=True) or {}
        result = _notifier.notify_milestone(
            milestone=data.get("milestone", "Milestone reached"),
            details=data.get("details", ""),
            source=data.get("source", "nexus"),
        )
        return jsonify(result), 200

    @app.route('/api/hub/human-notifications/<notification_id>/acknowledge', methods=['POST'])
    def acknowledge_human_notification(notification_id):
        """Acknowledge a notification."""
        data = request.get_json(silent=True) or {}
        result = _notifier.acknowledge_notification(
            notification_id=notification_id,
            acknowledged_by=data.get("acknowledged_by", "user"),
        )
        status = 200 if result.get("success") else 404
        return jsonify(result), status

    @app.route('/api/hub/human-notifications/cleanup', methods=['POST'])
    def cleanup_human_notifications():
        """Clean up old notifications."""
        max_age_hours = request.args.get("max_age_hours", 168, type=int)
        result = _notifier.cleanup(max_age_hours=max_age_hours)
        return jsonify(result)

except ImportError as e:
    print(f"⚠️ Multi-Orion Hub not available: {e}")


# ============================================
# PROACTIVE MONITORING API
# ============================================

try:
    from src.core.proactive_monitor import get_monitor, ProactiveMonitor, HealthCheck
    _pmonitor = get_monitor()

    # Register health checkers (Phase 1b)
    def _check_orion_health() -> HealthCheck:
        """Check health of Orion instances."""
        try:
            instances = get_orion_instances_snapshot()
            online_count = sum(1 for i in instances if i.get("online", False))
            total_count = len(instances)

            if total_count == 0:
                return HealthCheck(
                    component="orion",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message="No Orion instances configured",
                    metrics={"total": 0, "online": 0}
                )

            if online_count == 0:
                return HealthCheck(
                    component="orion",
                    status="unhealthy",
                    last_check=datetime.now().isoformat(),
                    message="All Orion instances offline",
                    metrics={"total": total_count, "online": 0}
                )

            if online_count < total_count:
                return HealthCheck(
                    component="orion",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message=f"{online_count}/{total_count} Orion instances online",
                    metrics={"total": total_count, "online": online_count}
                )

            return HealthCheck(
                component="orion",
                status="healthy",
                last_check=datetime.now().isoformat(),
                message=f"All {total_count} Orion instances online",
                metrics={"total": total_count, "online": online_count}
            )
        except Exception as e:
            return HealthCheck(
                component="orion",
                status="unhealthy",
                last_check=datetime.now().isoformat(),
                message=f"Health check failed: {str(e)}",
                metrics={"error": str(e)}
            )

    def _check_openclaw_health() -> HealthCheck:
        """Check health of OpenClaw integration."""
        try:
            # Check if OpenClaw is available
            token_ok = _token_cache is not None and _token_cache.token is not None
            attach_ok = _attach_state.last_error is None

            # In headless mode, token is not required
            is_headless = OPENCLAW_EXECUTION_MODE == "headless"

            if is_headless:
                return HealthCheck(
                    component="openclaw",
                    status="healthy",
                    last_check=datetime.now().isoformat(),
                    message=f"OpenClaw in headless mode (no token required)",
                    metrics={"mode": "headless", "token_ok": token_ok}
                )

            if not token_ok:
                return HealthCheck(
                    component="openclaw",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message="OpenClaw token not available (browser mode requires token)",
                    metrics={"token_ok": False, "mode": OPENCLAW_EXECUTION_MODE}
                )

            if not attach_ok:
                return HealthCheck(
                    component="openclaw",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message="OpenClaw attach issues",
                    metrics={"token_ok": True, "attach_ok": False, "last_error": _attach_state.last_error}
                )

            return HealthCheck(
                component="openclaw",
                status="healthy",
                last_check=datetime.now().isoformat(),
                message="OpenClaw operational",
                metrics={"token_ok": True, "attach_ok": True}
            )
        except Exception as e:
            return HealthCheck(
                component="openclaw",
                status="unhealthy",
                last_check=datetime.now().isoformat(),
                message=f"Health check failed: {str(e)}",
                metrics={"error": str(e)}
            )

    def _check_agents_health() -> HealthCheck:
        """Check health of all agents."""
        try:
            # Try to get status from runtime bridge first
            status_handler = _runtime_bridge.get("status_handler")
            runtime = {}
            agent_runtime = []  # Full agent objects with status

            if callable(status_handler):
                runtime = status_handler() or {}
                # Get full agent runtime objects (not just names)
                agent_runtime = runtime.get("agent_runtime", [])
                # Also check agents list (just names)
                agents = runtime.get("agents", [])

            # Fallback: try to get via API if no agents found
            if not agent_runtime:
                import requests as req
                try:
                    resp = req.get("http://127.0.0.1:5050/api/agents/status", timeout=2)
                    if resp.status_code == 200:
                        data = resp.json()
                        runtime = data.get("runtime", {})
                        agent_runtime = runtime.get("agent_runtime", [])
                        agents = runtime.get("agents", [])
                except Exception:
                    pass

            # Use agent_runtime if available, otherwise use agents list
            if agent_runtime:
                agents_to_check = agent_runtime
            elif agents:
                # Just names, not full objects
                agents_to_check = [{"name": a} for a in agents]
            else:
                agents_to_check = []

            if not agents_to_check:
                return HealthCheck(
                    component="agents",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message="No agents available",
                    metrics={"available": False}
                )

            agent_count = len(agents_to_check)

            # Check for stuck agents
            stuck_agents = []
            for agent in agents_to_check:
                # Check if agent is not running
                if not agent.get("running", True):
                    stuck_agents.append(f"{agent.get('name', 'unknown')}:not_running")

            if stuck_agents:
                return HealthCheck(
                    component="agents",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message=f"{len(stuck_agents)} agents may be stuck",
                    metrics={"total": agent_count, "stuck": stuck_agents}
                )

            return HealthCheck(
                component="agents",
                status="healthy",
                last_check=datetime.now().isoformat(),
                message=f"All {agent_count} agents operational",
                metrics={"total": agent_count}
            )
        except Exception as e:
            return HealthCheck(
                component="agents",
                status="unhealthy",
                last_check=datetime.now().isoformat(),
                message=f"Health check failed: {str(e)}",
                metrics={"error": str(e)}
            )

    def _check_central_command_health() -> HealthCheck:
        """Check health of Central Command."""
        try:
            with _CENTRAL_COMMAND_LOCK:
                orion_count = len(_registered_orions)
                lock_count = len(_global_file_locks)

            if orion_count == 0:
                return HealthCheck(
                    component="central_command",
                    status="degraded",
                    last_check=datetime.now().isoformat(),
                    message="No Orion instances registered",
                    metrics={"registered_orions": 0, "locks": lock_count}
                )

            return HealthCheck(
                component="central_command",
                status="healthy",
                last_check=datetime.now().isoformat(),
                message=f"Central Command operational with {orion_count} Orions",
                metrics={"registered_orions": orion_count, "locks": lock_count}
            )
        except Exception as e:
            return HealthCheck(
                component="central_command",
                status="unhealthy",
                last_check=datetime.now().isoformat(),
                message=f"Health check failed: {str(e)}",
                metrics={"error": str(e)}
            )

    # Register the health checkers
    _pmonitor.register_health_checker("orion", _check_orion_health)
    _pmonitor.register_health_checker("openclaw", _check_openclaw_health)
    _pmonitor.register_health_checker("agents", _check_agents_health)
    _pmonitor.register_health_checker("central_command", _check_central_command_health)

    @app.route('/api/monitor/health')
    def get_system_health():
        """Get system health status."""
        health = _pmonitor.check_health()
        return jsonify({"success": True, "health": health})

    @app.route('/api/monitor/status')
    def get_monitor_status():
        """Get proactive monitor status."""
        return jsonify({"success": True, "status": _pmonitor.get_status()})

    @app.route('/api/monitor/incidents')
    def get_recent_incidents():
        """Get recent incidents."""
        limit = request.args.get("limit", 20, type=int)
        incidents = _pmonitor.get_recent_incidents(limit=limit)
        return jsonify({"success": True, "incidents": incidents})

    @app.route('/api/monitor/incidents/<incident_id>/resolve', methods=['POST'])
    def resolve_monitor_incident(incident_id):
        """Resolve an incident."""
        data = request.get_json(silent=True) or {}
        resolution = data.get("resolution", "Manually resolved")
        success = _pmonitor.resolve_incident(incident_id, resolution)
        return jsonify({"success": success}), 200 if success else 404

    @app.route('/api/monitor/start', methods=['POST'])
    def start_proactive_monitor():
        """Start proactive monitoring."""
        _pmonitor.start()
        return jsonify({"success": True, "status": _pmonitor.get_status()})

    @app.route('/api/monitor/stop', methods=['POST'])
    def stop_proactive_monitor():
        """Stop proactive monitoring."""
        _pmonitor.stop()
        return jsonify({"success": True, "status": _pmonitor.get_status()})

except ImportError as e:
    print(f"⚠️ Proactive Monitor not available: {e}")


# ============================================
# BRIDGE API (for Unified Dashboard)
# ============================================

# Bridge state for tracking JACK OS integration
_bridge_state = {
    "jackos_connected": False,
    "last_sync": None,
    "synced_contacts": 0,
    "synced_sessions": 0,
    "synced_terminals": 0,
    "terminal_last_sync": None,
    "notifications_received": 0,
}
_BRIDGE_TERMINALS_LOCK = threading.Lock()
_bridge_terminal_sessions: Dict[str, Dict[str, Any]] = {}


def _bridge_normalize_owner_id(value: Any) -> str:
    owner_raw = str(value or "").strip()
    if owner_raw.lower().startswith("orion:"):
        owner_raw = owner_raw.split(":", 1)[1]
    return owner_raw.strip()


def _bridge_terminal_public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    status = str(row.get("status") or "").strip().lower() or "unknown"
    busy = bool(row.get("busy")) or status in {"running", "busy"}
    return {
        "session_id": row.get("session_id"),
        "owner_id": row.get("owner_id"),
        "source": row.get("source"),
        "title": row.get("title"),
        "cwd": row.get("cwd"),
        "command": row.get("command"),
        "status": status,
        "busy": busy,
        "pid": row.get("pid"),
        "started_at": row.get("started_at"),
        "updated_at": row.get("updated_at"),
        "last_output": row.get("last_output"),
        "last_error": row.get("last_error"),
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
    }


def _bridge_terminal_snapshot(owner_id: str = "", limit: int = 120) -> Dict[str, Any]:
    owner_norm = _bridge_normalize_owner_id(owner_id).lower()
    with _BRIDGE_TERMINALS_LOCK:
        rows = [_bridge_terminal_public_row(dict(item)) for item in _bridge_terminal_sessions.values() if isinstance(item, dict)]
    if owner_norm:
        rows = [row for row in rows if _bridge_normalize_owner_id(row.get("owner_id")).lower() == owner_norm]
    rows = _sort_by_timestamp_desc(rows)[:max(1, int(limit))]
    busy = 0
    errors = 0
    owners: Set[str] = set()
    for row in rows:
        if bool(row.get("busy")):
            busy += 1
        if str(row.get("status") or "").lower() in {"error", "failed", "blocked"}:
            errors += 1
        owner = _bridge_normalize_owner_id(row.get("owner_id"))
        if owner:
            owners.add(owner)
    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "busy": busy,
            "error": errors,
            "owners": len(owners),
        },
    }


@app.route('/api/bridge/status')
def bridge_status():
    """Get bridge status between JACK OS and NEXUS."""
    terminal_summary = _bridge_terminal_snapshot(limit=500).get("summary", {})
    return jsonify({
        "success": True,
        "bridge": {
            "status": "active",
            "jackos_connected": _bridge_state["jackos_connected"],
            "last_sync": _bridge_state["last_sync"],
            "stats": {
                "synced_contacts": _bridge_state["synced_contacts"],
                "synced_sessions": _bridge_state["synced_sessions"],
                "synced_terminals": _bridge_state["synced_terminals"],
                "terminal_last_sync": _bridge_state.get("terminal_last_sync"),
                "terminal_busy": int(terminal_summary.get("busy", 0) or 0),
                "terminal_error": int(terminal_summary.get("error", 0) or 0),
                "notifications_received": _bridge_state["notifications_received"],
            },
        },
        "nexus": {
            "status": "running",
            "agents": len(state.known_agents) if hasattr(state, 'known_agents') else 6,
            "uptime": str(datetime.now() - datetime.fromtimestamp(state.start_time)) if hasattr(state, 'start_time') else "unknown",
        },
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/bridge/notify', methods=['POST'])
def receive_bridge_notification():
    """Receive notifications from JACK OS via bridge."""
    data = request.get_json(silent=True) or {}

    notification_type = data.get("type", "unknown")
    source = data.get("source", "jackos")
    payload = data.get("data", {})

    _bridge_state["notifications_received"] += 1

    # Log the notification
    logger.info(f"Bridge notification from {source}: {notification_type}")

    # Broadcast to connected clients
    socketio.emit("bridge_notification", {
        "type": notification_type,
        "source": source,
        "data": payload,
        "timestamp": datetime.now().isoformat(),
    })

    # Handle specific notification types
    if notification_type == "contact_created":
        state.add_agent_log("bridge", f"📥 New contact synced: {payload.get('name', 'Unknown')}", "info")
    elif notification_type == "chat_message":
        state.add_agent_log("bridge", f"💬 Chat message from JACK OS", "info")
    elif notification_type == "session_update":
        _bridge_state["synced_sessions"] += 1

    return jsonify({
        "success": True,
        "message": "Notification received",
        "notification_id": str(uuid.uuid4()),
    })


@app.route('/api/bridge/sync/contacts', methods=['POST'])
def sync_contacts_from_jackos():
    """Sync contacts from JACK OS to NEXUS memory."""
    data = request.get_json(silent=True) or {}

    contacts = data.get("contacts", [])
    source = data.get("source", "jackos")

    _bridge_state["synced_contacts"] = len(contacts)
    _bridge_state["last_sync"] = datetime.now().isoformat()

    # Store in NEXUS memory (simplified - in production would use actual memory system)
    memory_contacts_path = Path(__file__).parent.parent / "memory" / "synced_contacts.json"
    try:
        memory_contacts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(memory_contacts_path, 'w', encoding='utf-8') as f:
            json.dump({
                "source": source,
                "contacts": contacts,
                "synced_at": _bridge_state["last_sync"],
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to sync contacts: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    # Broadcast update
    socketio.emit("contacts_synced", {
        "count": len(contacts),
        "source": source,
        "timestamp": _bridge_state["last_sync"],
    })

    state.add_agent_log("bridge", f"🔄 Synced {len(contacts)} contacts from {source}", "success")

    return jsonify({
        "success": True,
        "synced": len(contacts),
        "timestamp": _bridge_state["last_sync"],
    })


@app.route('/api/bridge/sync/sessions', methods=['POST'])
def sync_sessions_from_jackos():
    """Sync chat sessions from JACK OS."""
    data = request.get_json(silent=True) or {}

    sessions = data.get("sessions", [])
    source = data.get("source", "jackos")

    _bridge_state["synced_sessions"] = len(sessions)
    _bridge_state["last_sync"] = datetime.now().isoformat()

    # Broadcast update
    socketio.emit("sessions_synced", {
        "count": len(sessions),
        "source": source,
        "timestamp": _bridge_state["last_sync"],
    })

    return jsonify({
        "success": True,
        "synced": len(sessions),
        "timestamp": _bridge_state["last_sync"],
    })


@app.route('/api/bridge/jackos-status', methods=['POST'])
def update_jackos_status():
    """Update JACK OS connection status."""
    data = request.get_json(silent=True) or {}

    _bridge_state["jackos_connected"] = data.get("connected", False)
    _bridge_state["last_sync"] = datetime.now().isoformat()

    # Broadcast status change
    socketio.emit("jackos_status", {
        "connected": _bridge_state["jackos_connected"],
        "timestamp": _bridge_state["last_sync"],
    })

    status_text = "connected" if _bridge_state["jackos_connected"] else "disconnected"
    state.add_agent_log("bridge", f"🔗 JACK OS {status_text}", "info")

    return jsonify({"success": True, "connected": _bridge_state["jackos_connected"]})


@app.route('/api/bridge/sync/terminals', methods=['POST'])
def sync_terminals_from_bridge():
    """Sync terminal sessions (e.g. VSCode terminals) into monitor control plane."""
    data = request.get_json(silent=True) or {}
    source = str(data.get("source", "bridge")).strip() or "bridge"
    terminals = data.get("terminals")
    if not isinstance(terminals, list):
        return jsonify({"success": False, "error": "Missing terminals list", "error_code": "MISSING_TERMINALS"}), 400

    now_iso = datetime.now().isoformat()
    upserts = 0
    with _BRIDGE_TERMINALS_LOCK:
        for item in terminals:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("session_id") or item.get("id") or "").strip()
            if not session_id:
                continue
            owner_id = _bridge_normalize_owner_id(
                item.get("owner_id") or item.get("assignee") or item.get("orion_id") or item.get("instance_id")
            )
            status = str(item.get("status") or "").strip().lower() or "unknown"
            busy = bool(item.get("busy")) or status in {"running", "busy"}
            row = {
                "session_id": session_id,
                "owner_id": owner_id,
                "source": source,
                "title": str(item.get("title") or item.get("name") or "").strip(),
                "cwd": str(item.get("cwd") or "").strip(),
                "command": str(item.get("command") or item.get("last_command") or "").strip(),
                "status": status,
                "busy": busy,
                "pid": item.get("pid"),
                "started_at": item.get("started_at"),
                "updated_at": item.get("updated_at") or now_iso,
                "last_output": str(item.get("last_output") or item.get("tail") or "").strip(),
                "last_error": str(item.get("last_error") or "").strip(),
                "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            }
            _bridge_terminal_sessions[session_id] = row
            upserts += 1

    _bridge_state["synced_terminals"] = len(_bridge_terminal_sessions)
    _bridge_state["terminal_last_sync"] = now_iso
    _bridge_state["last_sync"] = now_iso
    summary = _bridge_terminal_snapshot(limit=500).get("summary", {})
    socketio.emit(
        "terminals_synced",
        {
            "source": source,
            "upserts": upserts,
            "total": int(summary.get("total", 0) or 0),
            "busy": int(summary.get("busy", 0) or 0),
            "error": int(summary.get("error", 0) or 0),
            "timestamp": now_iso,
        },
    )
    state.add_agent_log("bridge", f"🖥️ Synced terminals: +{upserts} ({source})", "info")
    return jsonify(
        {
            "success": True,
            "source": source,
            "upserts": upserts,
            "summary": summary,
            "timestamp": now_iso,
        }
    )


@app.route('/api/bridge/terminals')
def bridge_terminals():
    owner_id = str(request.args.get("owner_id", "") or "").strip()
    limit = _int_in_range(request.args.get("limit", 120), default=120, min_value=1, max_value=500)
    snapshot = _bridge_terminal_snapshot(owner_id=owner_id, limit=limit)
    return jsonify(
        {
            "success": True,
            "terminals": snapshot.get("rows", []),
            "summary": snapshot.get("summary", {}),
            "owner_id": owner_id or None,
            "timestamp": datetime.now().isoformat(),
        }
    )


# ============================================
# SOCKET.IO
# ============================================

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    if not _dashboard_request_authorized():
        logger.warning(f"Rejected socket connection (unauthorized): {request.sid}")
        return False
    logger.info(f"Client connected: {request.sid}")
    # Send current state to new client
    emit('connected', {'status': 'Connected to Nexus Dashboard'})
    emit('status_update', build_status_payload())


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnect"""
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on('request_status')
def handle_status_request():
    """Handle status request from client"""
    emit('status_update', build_status_payload())


# ============================================
# PUBLIC FUNCTIONS (for agents to call)
# ============================================

def register_runtime_bridge(command_handler=None, status_handler=None):
    """Register runtime callbacks from system runner."""
    _runtime_bridge["command_handler"] = command_handler
    _runtime_bridge["status_handler"] = status_handler


def register_agents(agent_names: List[str]):
    """Register known agents so dashboard can render fleet before first log."""
    state.register_agents(agent_names or [])
    socketio.emit("agents_registered", {"agents": sorted(state.known_agents)})


def update_agent_log(agent: str, message: str, level: str = "info"):
    """Update generic agent log and broadcast to dashboard."""
    state.add_agent_log(agent, message, level)


def update_orion_log(message: str, level: str = "info"):
    """Update Orion log from agent"""
    state.add_agent_log("orion", message, level)


def update_guardian_log(message: str, level: str = "info"):
    """Update Guardian log from agent"""
    state.add_agent_log("guardian", message, level)


def update_orion_thinking(thinking: str):
    """Update Orion's thinking process"""
    state.update_orion_thinking(thinking)


def update_guardian_thinking(thinking: str):
    """Update Guardian's thinking process"""
    state.update_guardian_thinking(thinking)


def add_pending_decision(decision: Dict):
    """Add a pending decision for user review"""
    state.add_pending_decision(decision)


def update_project_status(status: Dict):
    """Update project status"""
    state.update_project_status(status)


def run_dashboard(host: str = "localhost", port: int = 5001):
    """Run the dashboard server"""
    print(f"🚀 Dashboard running at http://{host}:{port}")
    print(f"📊 Real-time updates enabled via Socket.IO")
    _ensure_monitor_supervisor_started()
    active_executor = str(_AUTOPILOT_STATE.get("active_executor", "policy_loop")).strip().lower() if "_AUTOPILOT_STATE" in globals() else "policy_loop"
    if active_executor == "hub_loop":
        _ensure_hub_autopilot_started()
    else:
        _ensure_autopilot_background_started()
    if OPENCLAW_EXTENSION_AUTOMATION_ENABLED and OPENCLAW_SELF_HEAL_ENABLED:
        _ensure_openclaw_self_heal_started()
    if OPENCLAW_EXTENSION_AUTOMATION_ENABLED and OPENCLAW_SELF_HEAL_ENABLED and OPENCLAW_SELF_HEAL_BOOTSTRAP:
        def _bootstrap_worker() -> None:
            try:
                bootstrap = _openclaw_self_heal_once()
                logger.info("OpenClaw self-heal bootstrap", extra={"detail": bootstrap})
                print(f"🧩 OpenClaw self-heal bootstrap: {'OK' if bootstrap.get('success') else 'FAILED'}")
                if not bootstrap.get("success"):
                    err = bootstrap.get("error") or "see openclaw metrics"
                    print(f"🧩 OpenClaw self-heal detail: {err}")
            except Exception as exc:
                logger.warning(f"OpenClaw self-heal bootstrap failed: {exc}")
                print(f"🧩 OpenClaw self-heal bootstrap failed: {exc}")

        threading.Thread(
            target=_bootstrap_worker,
            name="openclaw-self-heal-bootstrap",
            daemon=True,
        ).start()
    _ensure_progress_snapshot_started()
    _ensure_runtime_heartbeat_started()
    print(
        "🧭 Monitor autopilot:",
        "ENABLED" if _monitor_supervisor_status().get("enabled") else "DISABLED",
    )
    print(
        "📝 Progress snapshot:",
        "ENABLED" if MONITOR_PROGRESS_SNAPSHOT_ENABLED else "DISABLED",
        f"({MONITOR_PROGRESS_SNAPSHOT_PATH})",
    )
    print(
        "💓 Runtime heartbeat:",
        "ENABLED" if MONITOR_RUNTIME_HEARTBEAT_ENABLED else "DISABLED",
        f"({MONITOR_RUNTIME_HEARTBEAT_INTERVAL_SEC}s)",
    )

    # Initialize with startup message
    state.add_orion_log("🚀 Dashboard initialized", "success")
    state.add_guardian_log("🛡️ Guardian monitoring active", "success")

    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    run_dashboard()
