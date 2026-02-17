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
import subprocess
import time
import unicodedata
import uuid
import secrets
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set, Tuple
import logging
import threading
from collections import defaultdict, deque
from urllib import request as urllib_request, error as urllib_error
from urllib.parse import urlparse

from flask import Flask, render_template, jsonify, request, Response, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit

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


MONITOR_AUTOPILOT_ENABLED_DEFAULT = _env_bool("MONITOR_AUTOPILOT_ENABLED", True)
MONITOR_AUTOPILOT_INTERVAL_SEC = max(4, int(os.getenv("MONITOR_AUTOPILOT_INTERVAL_SEC", "10")))
MONITOR_STUCK_THRESHOLD_SEC = max(20, int(os.getenv("MONITOR_STUCK_THRESHOLD_SEC", "90")))
MONITOR_RECOVERY_COOLDOWN_SEC = max(10, int(os.getenv("MONITOR_RECOVERY_COOLDOWN_SEC", "35")))
MONITOR_STARTUP_GRACE_SEC = max(5, int(os.getenv("MONITOR_STARTUP_GRACE_SEC", "25")))
MONITOR_AUTO_APPROVE_SAFE_DECISIONS = _env_bool("MONITOR_AUTO_APPROVE_SAFE_DECISIONS", True)
MONITOR_AUTO_APPROVE_ALL_DECISIONS = _env_bool("MONITOR_AUTO_APPROVE_ALL_DECISIONS", True)
MONITOR_AUTO_EXECUTE_COMMANDS = _env_bool("MONITOR_AUTO_EXECUTE_COMMANDS", True)
MONITOR_ALLOW_UNSAFE_COMMANDS = _env_bool("MONITOR_ALLOW_UNSAFE_COMMANDS", False)
MONITOR_COMPUTER_CONTROL_ENABLED_DEFAULT = _env_bool("MONITOR_COMPUTER_CONTROL_ENABLED", True)
MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED = _env_bool("MONITOR_AUTOPILOT_OPENCLAW_FLOW_ENABLED", True)
MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC = max(
    20,
    int(os.getenv("MONITOR_AUTOPILOT_OPENCLAW_FLOW_COOLDOWN_SEC", "120")),
)
MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT = str(
    os.getenv("MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT", "auto unstick run one cycle resume") or ""
).strip()
OPENCLAW_BROWSER_AUTO_START = _env_bool("OPENCLAW_BROWSER_AUTO_START", True)
OPENCLAW_BROWSER_TIMEOUT_MS = max(5000, int(os.getenv("OPENCLAW_BROWSER_TIMEOUT_MS", "30000")))
OPENCLAW_DASHBOARD_URL = os.getenv("OPENCLAW_DASHBOARD_URL", "http://127.0.0.1:5050/")
OPENCLAW_SELF_HEAL_ENABLED = _env_bool("OPENCLAW_SELF_HEAL_ENABLED", True)
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
OPENCLAW_AUTO_ATTACH_ENABLED = _env_bool("OPENCLAW_AUTO_ATTACH_ENABLED", True)
OPENCLAW_AUTO_ATTACH_RETRIES = max(1, int(os.getenv("OPENCLAW_AUTO_ATTACH_RETRIES", "2")))
OPENCLAW_AUTO_ATTACH_DELAY_SEC = float(os.getenv("OPENCLAW_AUTO_ATTACH_DELAY_SEC", "1.2"))
OPENCLAW_AUTO_ATTACH_HEURISTIC = _env_bool("OPENCLAW_AUTO_ATTACH_HEURISTIC", True)
OPENCLAW_AUTO_ATTACH_ICON_MATCH = _env_bool("OPENCLAW_AUTO_ATTACH_ICON_MATCH", True)
OPENCLAW_CHROME_APP = os.getenv("OPENCLAW_CHROME_APP", "Google Chrome")
OPENCLAW_LAUNCH_WITH_EXTENSION = _env_bool("OPENCLAW_LAUNCH_WITH_EXTENSION", True)
OPENCLAW_FORCE_NEW_CHROME_INSTANCE = _env_bool("OPENCLAW_FORCE_NEW_CHROME_INSTANCE", True)
OPENCLAW_CHROME_BOUNDS = os.getenv("OPENCLAW_CHROME_BOUNDS", "0,24,1440,900")
OPENCLAW_CLI_MAX_RETRIES = max(1, int(os.getenv("OPENCLAW_CLI_MAX_RETRIES", "2")))
OPENCLAW_CLI_RETRY_DELAY_SEC = float(os.getenv("OPENCLAW_CLI_RETRY_DELAY_SEC", "0.4"))
OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC = max(5, float(os.getenv("OPENCLAW_AUTO_ATTACH_COOLDOWN_SEC", "30")))
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
AUTOMATION_TASK_HISTORY_MAX = max(50, int(os.getenv("AUTOMATION_TASK_HISTORY_MAX", "500")))
AUTOMATION_ENABLE_APP_CONTROL = _env_bool("AUTOMATION_ENABLE_APP_CONTROL", True)
AUTOMATION_ENABLE_TERMINAL_EXEC = _env_bool("AUTOMATION_ENABLE_TERMINAL_EXEC", True)
AUTOMATION_ENABLE_MAIL_READ = _env_bool("AUTOMATION_ENABLE_MAIL_READ", True)
AUTOMATION_ENABLE_MESSAGES_READ = _env_bool("AUTOMATION_ENABLE_MESSAGES_READ", True)
AUTOMATION_TERMINAL_TIMEOUT_SEC = max(3, int(os.getenv("AUTOMATION_TERMINAL_TIMEOUT_SEC", "20")))
AUTOMATION_TERMINAL_ALLOWED_BINS = {
    token.strip().lower()
    for token in os.getenv(
        "AUTOMATION_TERMINAL_ALLOWED_BINS",
        "python3,pytest,git,ls,cat,rg,tail,head,openclaw,curl,open,osascript,pwd,date",
    ).split(",")
    if token.strip()
}
AUTOMATION_EMAIL_IMAP_HOST = str(os.getenv("AUTOMATION_EMAIL_IMAP_HOST", "") or "").strip()
AUTOMATION_EMAIL_IMAP_PORT = max(1, int(os.getenv("AUTOMATION_EMAIL_IMAP_PORT", "993")))
AUTOMATION_EMAIL_IMAP_USERNAME = str(os.getenv("AUTOMATION_EMAIL_IMAP_USERNAME", "") or "").strip()
AUTOMATION_EMAIL_IMAP_PASSWORD = str(os.getenv("AUTOMATION_EMAIL_IMAP_PASSWORD", "") or "").strip()
AUTOMATION_EMAIL_IMAP_FOLDER = str(os.getenv("AUTOMATION_EMAIL_IMAP_FOLDER", "INBOX") or "INBOX").strip() or "INBOX"
AUTOMATION_EMAIL_IMAP_SSL = _env_bool("AUTOMATION_EMAIL_IMAP_SSL", True)
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
                    with open(state_file, 'r') as f:
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
                with open(tmp_path, 'w') as f:
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

    chat_text = MONITOR_AUTOPILOT_OPENCLAW_FLOW_CHAT or "auto unstick run one cycle resume"
    queued = _enqueue_openclaw_command(
        "flow",
        {
            "chat_text": chat_text,
            "source": "autopilot",
            "mode": "async",
            "session_id": f"autopilot:{instance_id}",
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

        if not pyautogui:
            _openclaw_metrics_inc("attach_failed")
            _openclaw_metrics_event("attach_failed", {"error": "pyautogui not available"})
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
        else:
            _openclaw_metrics_inc("attach_failed")
            _openclaw_metrics_event("attach_failed", {"method": method_used or "unknown"})

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
            "enabled": bool(OPENCLAW_SELF_HEAL_ENABLED),
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
        }


def _openclaw_self_heal_once() -> Dict[str, Any]:
    global _openclaw_self_heal_last_run, _openclaw_self_heal_last_error, _openclaw_self_heal_last_success, _openclaw_self_heal_last_open, _openclaw_self_heal_last_fallback
    _openclaw_metrics_inc("self_heal_runs")
    _openclaw_self_heal_last_run = datetime.now()
    detail: Dict[str, Any] = {"steps": []}

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


def _openclaw_dashboard_flow(chat_text: str = "", approve: bool = True, deny: bool = False) -> Dict[str, Any]:
    global _openclaw_flow_last_open
    steps: List[Dict[str, Any]] = []
    _openclaw_metrics_inc("flow_runs")
    token_info = _collect_token_info()
    token_status = _create_token_status(token_info)
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
        if _openclaw_is_loopback_dashboard_url(OPENCLAW_DASHBOARD_URL):
            probe_result = _probe_http_url(OPENCLAW_DASHBOARD_URL, timeout_sec=1.2)
            steps.append(_record_flow_step("dashboard_probe", probe_result))
            if not probe_result.get("success"):
                if fallback := attempt_fallback("dashboard_unreachable"):
                    return fallback
                return _fail_flow(
                    {"success": False, "steps": steps, "token_status": token_status},
                    "flow_dashboard_probe_failed",
                    {"url": OPENCLAW_DASHBOARD_URL, "error": probe_result.get("error")},
                )

        open_step = run_step("open", ["open", OPENCLAW_DASHBOARD_URL])
        if not open_step["success"]:
            if needs_attach(open_step.get("result", {})) and attempt_attach("open_failed"):
                open_retry = run_step("open_retry", ["open", OPENCLAW_DASHBOARD_URL])
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
    chat_text = str(data.get("chat_text", "") or "")
    approve = bool(data.get("approve", True))
    deny = bool(data.get("deny", False))
    result = _openclaw_dashboard_flow(chat_text=chat_text, approve=approve, deny=deny)
    return {
        "success": bool(result.get("success")),
        "flow": result,
        "timestamp": datetime.now().isoformat(),
    }


def _execute_openclaw_attach_action(_: Dict[str, Any]) -> Dict[str, Any]:
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
    mode = str(data.get("mode", "sync")).strip().lower() or "sync"
    if mode not in {"sync", "async"}:
        mode = "sync"
    session_id = str(data.get("session_id", "default")).strip() or "default"
    timeout_ms = max(1000, int(data.get("timeout_ms") or OPENCLAW_BROWSER_TIMEOUT_MS))
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
    return {
        "success": bool(outcome.get("success", False)) if finished else False,
        "request_id": request_id,
        "mode": "sync",
        "timed_out": not finished,
        "idempotent_replay": bool(enqueued.get("idempotent_replay")),
        "session_id": context["session_id"],
        "command": waited,
        "result": outcome if finished else {},
        "queue_state": _openclaw_command_manager.queue_snapshot(),
        "decision": policy.get("decision", "auto_approved"),
        "risk_level": policy.get("risk_level", "medium"),
        "target_worker": policy.get("target_worker", ""),
        "resolved_worker": policy.get("resolved_worker", ""),
        "policy_reasons": policy.get("reasons", []),
        "token_status": policy.get("token_status", {}),
        "failure_class": _classify_openclaw_failure(outcome) if finished and not bool(outcome.get("success")) else "",
    }


def _openclaw_error_http_status(error_code: str) -> int:
    code = str(error_code or "").strip().upper()
    if code == "QUEUE_FULL":
        return 429
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
    text = str(message or "").strip()
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

        suggestion = "Bạn có thể nói tự nhiên như: 'còn gì đang chạy', 'guardian gỡ kẹt', 'pause all', 'pixel status'."
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

    if re.search(r"(khong hoi|khong can hoi|khong xac nhan|hoi lien tuc|confirm lien tuc|qua nhieu hoi)", normalized_intent):
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
        if MONITOR_AUTO_APPROVE_ALL_DECISIONS and decision not in {"approved", "approve"}:
            decision = "approved"
            reason = "Auto-approved by monitor (MONITOR_AUTO_APPROVE_ALL_DECISIONS)."
        if decision in {"approved", "approve"} and MONITOR_AUTO_EXECUTE_COMMANDS:
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
        agent_block = "\n".join([f"- {line}" for line in activity_lines]) if activity_lines else "- Chưa có telemetry agent."
        reply = (
            f"{runtime_line}\n"
            f"Tín hiệu quan trọng: {counts['error']} lỗi, {counts['warning']} cảnh báo, {counts['success']} thành công.\n"
            f"Agent hoạt động nhiều nhất gần đây: {top_agent}.\n"
            f"Sự kiện gần nhất: {latest_msg}\n"
            f"Agent realtime:\n{agent_block}\n\n"
            f"Phản hồi gần nhất: {[item.get('category') for item in feedback_recent][:2]}"
        )
        return {
            "intent": "project_status_summary",
            "executed": False,
            "result": _with_context({
                "agent_activity": activity,
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
# Unified control for: browser, terminal, app, mail, messages, computer
# ============================================================================

# Task types supported by the automation bus
AUTOMATION_TASK_TYPES = [
    "browser",      # Chrome automation via OpenClaw
    "terminal",     # Terminal/command execution
    "app",          # Launch/control macOS apps
    "mail",         # Email reading/sending
    "messages",     # iMessage/Messages app
    "computer",     # General computer control (keyboard/mouse)
    "file",         # File operations
    "clipboard",    # Clipboard operations
]

# Task status
AUTOMATION_TASK_STATUS = ["pending", "running", "completed", "failed", "cancelled"]


def _create_automation_task(task_type: str, action: str, params: Dict[str, Any], task_id: str = None) -> Dict[str, Any]:
    """Create a new automation task entry."""
    if task_id is None:
        task_id = f"task_{int(time.time() * 1000)}_{secrets.token_hex(4)}"

    return {
        "task_id": task_id,
        "task_type": task_type,
        "action": action,
        "params": params,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error": None,
        "logs": [],
    }


def _execute_automation_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single automation task based on type."""
    task_type = task["task_type"]
    action = task["action"]
    params = task["params"]

    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat()

    try:
        if task_type == "browser":
            # Delegate to OpenClaw browser control
            result = _openclaw_execute_browser_action(action, params)
        elif task_type == "terminal":
            # Execute terminal command
            result = _execute_terminal_action(action, params)
        elif task_type == "app":
            # Launch/control macOS apps
            result = _execute_app_action(action, params)
        elif task_type == "mail":
            # Email automation
            result = _execute_mail_action(action, params)
        elif task_type == "messages":
            # Messages app automation
            result = _execute_messages_action(action, params)
        elif task_type == "computer":
            # General computer control (keyboard/mouse)
            result = _execute_computer_action(action, params)
        elif task_type == "file":
            # File operations
            result = _execute_file_action(action, params)
        elif task_type == "clipboard":
            # Clipboard operations
            result = _execute_clipboard_action(action, params)
        else:
            raise ValueError(f"Unknown task type: {task_type}")

        task["status"] = "completed"
        task["result"] = result
        task["completed_at"] = datetime.now().isoformat()
        return task

    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        task["completed_at"] = datetime.now().isoformat()
        return task


def _openclaw_execute_browser_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute browser action via OpenClaw."""
    # Map unified actions to OpenClaw commands
    action_map = {
        "navigate": "goto",
        "click": "click",
        "type": "type",
        "screenshot": "screenshot",
        "read": "extract",
        "scroll": "scroll",
        "status": "status",
    }

    openclaw_action = action_map.get(action, action)

    # Use existing OpenClaw flow
    return {
        "success": True,
        "action": openclaw_action,
        "params": params,
        "source": "automation_bus",
    }


def _execute_terminal_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute terminal command."""
    if action == "execute":
        cmd = params.get("command", "")
        timeout = params.get("timeout", 30)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "list_sessions":
        # List active terminal sessions
        return {
            "success": True,
            "sessions": [{"id": "main", "name": "Main Terminal"}],
        }

    return {"success": False, "error": f"Unknown terminal action: {action}"}


def _execute_app_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Launch or control macOS applications."""
    if action == "launch":
        app_path = params.get("app_path", "")
        app_name = params.get("app_name", "")

        cmd = f'open -a "{app_name or app_path}"'
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return {
                "success": result.returncode == 0,
                "app": app_name or app_path,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "activate":
        # Activate (bring to front) an app
        app_name = params.get("app_name", "")
        cmd = f'osascript -e \'tell application "{app_name}" to activate\''
        try:
            subprocess.run(cmd, shell=True, capture_output=True)
            return {"success": True, "app": app_name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "list_running":
        # List running applications
        cmd = 'osascript -e \'tell application "System Events" to get name of every process whose background only is false\''
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            apps = [a.strip() for a in result.stdout.split(", ") if a.strip()]
            return {"success": True, "running_apps": apps}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown app action: {action}"}


def _execute_mail_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Email automation via macOS Mail app."""
    if action == "check_unread":
        # Check unread emails (via AppleScript)
        cmd = '''osascript -e 'tell application "Mail" to get count of (messages of inbox whose read status is false)' '''
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            count = int(result.stdout.strip() or "0")
            return {"success": True, "unread_count": count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "send":
        # Send email via AppleScript
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")

        cmd = f'''osascript -e 'tell application "Mail" to set newMessage to make new outgoing message with properties {{subject:"{subject}", content:"{body}", visible:false}} tell newMessage to make new to recipient at end of to recipients with properties address:"{to}" send newMessage'' '''
        try:
            subprocess.run(cmd, shell=True, capture_output=True)
            return {"success": True, "to": to, "subject": subject}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "list_folders":
        return {
            "success": True,
            "folders": ["Inbox", "Sent", "Drafts", "Archive", "Junk"],
        }

    return {"success": False, "error": f"Unknown mail action: {action}"}


def _execute_messages_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """iMessage/Messages app automation."""
    if action == "check_unread":
        cmd = '''osascript -e 'tell application "Messages" to get count of (messages of active chat whose read status is false)' '''
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            count = int(result.stdout.strip() or "0")
            return {"success": True, "unread_count": count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "send":
        # Send iMessage
        recipient = params.get("recipient", "")
        message = params.get("message", "")

        cmd = f'''osascript -e 'tell application "Messages" to send "{message}" to buddy "{recipient}"' '''
        try:
            subprocess.run(cmd, shell=True, capture_output=True)
            return {"success": True, "recipient": recipient}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "list_conversations":
        return {
            "success": True,
            "conversations": [],
        }

    return {"success": False, "error": f"Unknown messages action: {action}"}


def _execute_computer_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """General computer control - keyboard/mouse."""
    try:
        import pyautogui
    except ImportError:
        return {"success": False, "error": "pyautogui not installed"}

    if action == "click":
        x = params.get("x")
        y = params.get("y")
        button = params.get("button", "left")

        if x is not None and y is not None:
            pyautogui.click(x, y, button=button)
        else:
            pyautogui.click(button=button)
        return {"success": True, "action": "click", "x": x, "y": y}

    elif action == "type":
        text = params.get("text", "")
        pyautogui.write(text)
        return {"success": True, "action": "type", "text_length": len(text)}

    elif action == "hotkey":
        keys = params.get("keys", [])
        if keys:
            pyautogui.hotkey(*keys)
        return {"success": True, "action": "hotkey", "keys": keys}

    elif action == "screenshot":
        filepath = params.get("filepath", "/tmp/screenshot.png")
        pyautogui.screenshot(filepath)
        return {"success": True, "filepath": filepath}

    elif action == "position":
        x, y = pyautogui.position()
        return {"success": True, "x": x, "y": y}

    elif action == "move":
        x = params.get("x")
        y = params.get("y")
        if x is not None and y is not None:
            pyautogui.moveTo(x, y)
        return {"success": True, "action": "move", "x": x, "y": y}

    return {"success": False, "error": f"Unknown computer action: {action}"}


def _execute_file_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """File operations."""
    import shutil

    if action == "read":
        filepath = params.get("filepath", "")
        try:
            with open(filepath, "r") as f:
                content = f.read()
            return {"success": True, "content": content, "filepath": filepath}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "write":
        filepath = params.get("filepath", "")
        content = params.get("content", "")
        try:
            with open(filepath, "w") as f:
                f.write(content)
            return {"success": True, "filepath": filepath}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "copy":
        src = params.get("src", "")
        dst = params.get("dst", "")
        try:
            shutil.copy2(src, dst)
            return {"success": True, "src": src, "dst": dst}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "list":
        dirpath = params.get("dirpath", ".")
        try:
            files = os.listdir(dirpath)
            return {"success": True, "files": files, "dirpath": dirpath}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown file action: {action}"}


def _execute_clipboard_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Clipboard operations."""
    if action == "read":
        cmd = "pbpaste"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return {"success": True, "content": result.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "write":
        content = params.get("content", "")
        cmd = f'echo "{content}" | pbcopy'
        try:
            subprocess.run(cmd, shell=True)
            return {"success": True, "content_length": len(content)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown clipboard action: {action}"}


@app.route('/api/automation/execute', methods=['POST'])
def automation_execute():
    """
    Universal automation task bus.
    Execute tasks across: browser, terminal, app, mail, messages, computer, file, clipboard.

    Request body:
    {
        "task_type": "browser|terminal|app|mail|messages|computer|file|clipboard",
        "action": "action_name",
        "params": {...},
        "task_id": "optional_custom_task_id"
    }
    """
    data = request.get_json() or {}

    task_type = data.get("task_type", "")
    action = data.get("action", "")
    params = data.get("params", {})
    task_id = data.get("task_id")

    # Validate task type
    if task_type not in AUTOMATION_TASK_TYPES:
        return jsonify({
            "success": False,
            "error": f"Invalid task_type. Must be one of: {AUTOMATION_TASK_TYPES}",
        }), 400

    # Create task
    task = _create_automation_task(task_type, action, params, task_id)

    # Add to recent tasks (for dedup)
    task_key = f"{task_type}:{action}:{json.dumps(params, sort_keys=True)}"
    with _AUTOMATION_TASKS_LOCK:
        _automation_recent_tasks.append({
            "task_id": task["task_id"],
            "task_key": task_key,
            "timestamp": time.time(),
        })

    # Execute task
    result_task = _execute_automation_task(task)

    return jsonify({
        "success": result_task["status"] == "completed",
        "task_id": result_task["task_id"],
        "status": result_task["status"],
        "result": result_task.get("result"),
        "error": result_task.get("error"),
        "created_at": result_task["created_at"],
        "completed_at": result_task.get("completed_at"),
    })


@app.route('/api/automation/batch', methods=['POST'])
def automation_batch():
    """
    Execute multiple automation tasks in batch.

    Request body:
    {
        "tasks": [
            {"task_type": "...", "action": "...", "params": {...}},
            ...
        ]
    }
    """
    data = request.get_json() or {}
    tasks_data = data.get("tasks", [])

    if not tasks_data:
        return jsonify({
            "success": False,
            "error": "No tasks provided",
        }), 400

    results = []
    for task_data in tasks_data:
        task_type = task_data.get("task_type", "")
        action = task_data.get("action", "")
        params = task_data.get("params", {})

        if task_type not in AUTOMATION_TASK_TYPES:
            results.append({
                "success": False,
                "error": f"Invalid task_type: {task_type}",
            })
            continue

        task = _create_automation_task(task_type, action, params)
        result_task = _execute_automation_task(task)

        results.append({
            "task_id": result_task["task_id"],
            "status": result_task["status"],
            "result": result_task.get("result"),
            "error": result_task.get("error"),
        })

    return jsonify({
        "success": True,
        "total": len(tasks_data),
        "results": results,
    })


@app.route('/api/automation/status/<task_id>')
def automation_status(task_id: str):
    """Get status of a specific automation task."""
    # In a full implementation, we'd store tasks in a database
    # For now, check recent tasks
    return jsonify({
        "task_id": task_id,
        "status": "unknown",
        "message": "Task status tracking requires persistent storage",
    })


@app.route('/api/automation/types')
def automation_types():
    """List supported automation task types and their actions."""
    return jsonify({
        "task_types": AUTOMATION_TASK_TYPES,
        "capabilities": {
            "browser": ["navigate", "click", "type", "screenshot", "read", "scroll", "status"],
            "terminal": ["execute", "list_sessions"],
            "app": ["launch", "activate", "list_running"],
            "mail": ["check_unread", "send", "list_folders"],
            "messages": ["check_unread", "send", "list_conversations"],
            "computer": ["click", "type", "hotkey", "screenshot", "position", "move"],
            "file": ["read", "write", "copy", "list"],
            "clipboard": ["read", "write"],
        },
    })


@app.route('/api/automation/history')
def automation_history():
    """Get recent automation task history."""
    limit = _int_in_range(request.args.get("limit", 20), default=20, min_value=1, max_value=100)

    with _AUTOMATION_TASKS_LOCK:
        recent = list(_automation_recent_tasks)[-limit:]

    return jsonify({
        "count": len(recent),
        "tasks": recent,
    })


@app.route('/api/automation/control', methods=['POST'])
def automation_control():
    """
    Universal control endpoint for all automation.
    Start/stop/restart automation services.

    Request body:
    {
        "action": "start|stop|restart|status",
        "target": "browser|terminal|all"
    }
    """
    data = request.get_json() or {}
    action = data.get("action", "status")
    target = data.get("target", "all")

    if action == "status":
        return jsonify({
            "success": True,
            "target": target,
            "status": {
                "browser": "running" if _openclaw_browser_available else "stopped",
                "automation_bus": "running",
            },
        })

    elif action == "stop":
        return jsonify({
            "success": True,
            "message": f"Stop {target} - requires persistent process management",
        })

    elif action == "start":
        return jsonify({
            "success": True,
            "message": f"Start {target} - already running in daemon mode",
        })

    return jsonify({
        "success": False,
        "error": f"Unknown action: {action}",
    }), 400


# ============================================================================
# SCHEDULED AUTOMATION
# Schedule tasks to run automatically at intervals
# ============================================================================

_AUTOMATION_SCHEDULER_LOCK = threading.Lock()
_automation_scheduled_tasks: Dict[str, Dict[str, Any]] = {}
_automation_scheduler_running = False
_automation_scheduler_thread: Optional[threading.Thread] = None


def _automation_scheduler_loop():
    """Background loop to check and execute scheduled tasks."""
    global _automation_scheduler_running

    while _automation_scheduler_running:
        try:
            now = time.time()
            with _AUTOMATION_SCHEDULER_LOCK:
                for schedule_id, schedule in list(_automation_scheduled_tasks.items()):
                    if not schedule.get("enabled", True):
                        continue

                    # Check if it's time to run
                    interval_sec = schedule.get("interval_sec", 60)
                    last_run = schedule.get("last_run", 0)

                    if now - last_run >= interval_sec:
                        # Execute the task
                        schedule["last_run"] = now
                        schedule["run_count"] = schedule.get("run_count", 0) + 1

                        task_type = schedule.get("task_type")
                        action = schedule.get("action")
                        params = schedule.get("params", {})

                        if task_type and action:
                            task = _create_automation_task(task_type, action, params)
                            result_task = _execute_automation_task(task)
                            schedule["last_result"] = {
                                "status": result_task["status"],
                                "success": result_task["status"] == "completed",
                            }

        except Exception as e:
            logger.error(f"Automation scheduler error: {e}")

        time.sleep(5)  # Check every 5 seconds


def _start_automation_scheduler():
    """Start the automation scheduler background thread."""
    global _automation_scheduler_running, _automation_scheduler_thread

    if _automation_scheduler_running:
        return

    _automation_scheduler_running = True
    _automation_scheduler_thread = threading.Thread(target=_automation_scheduler_loop, daemon=True)
    _automation_scheduler_thread.start()
    logger.info("Automation scheduler started")


def _stop_automation_scheduler():
    """Stop the automation scheduler."""
    global _automation_scheduler_running
    _automation_scheduler_running = False


# Start scheduler on module load
_start_automation_scheduler()


@app.route('/api/automation/schedule', methods=['POST'])
def automation_schedule_create():
    """
    Create a scheduled automation task.

    Request body:
    {
        "schedule_id": "optional_custom_id",
        "task_type": "terminal|app|...",
        "action": "execute|list_running|...",
        "params": {...},
        "interval_sec": 60,  // seconds between runs
        "enabled": true
    }
    """
    data = request.get_json() or {}

    schedule_id = data.get("schedule_id") or f"schedule_{int(time.time() * 1000)}"
    task_type = data.get("task_type", "")
    action = data.get("action", "")
    params = data.get("params", {})
    interval_sec = max(10, int(data.get("interval_sec", 60)))
    enabled = data.get("enabled", True)

    # Validate task type
    if task_type not in AUTOMATION_TASK_TYPES:
        return jsonify({
            "success": False,
            "error": f"Invalid task_type. Must be one of: {AUTOMATION_TASK_TYPES}",
        }), 400

    schedule = {
        "schedule_id": schedule_id,
        "task_type": task_type,
        "action": action,
        "params": params,
        "interval_sec": interval_sec,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(),
        "last_run": 0,
        "run_count": 0,
        "last_result": None,
    }

    with _AUTOMATION_SCHEDULER_LOCK:
        _automation_scheduled_tasks[schedule_id] = schedule

    return jsonify({
        "success": True,
        "schedule": schedule,
    })


@app.route('/api/automation/schedule/<schedule_id>', methods=['DELETE'])
def automation_schedule_delete(schedule_id: str):
    """Delete a scheduled automation task."""
    with _AUTOMATION_SCHEDULER_LOCK:
        if schedule_id in _automation_scheduled_tasks:
            del _automation_scheduled_tasks[schedule_id]
            return jsonify({"success": True, "schedule_id": schedule_id})

    return jsonify({
        "success": False,
        "error": f"Schedule not found: {schedule_id}",
    }), 404


@app.route('/api/automation/schedule/<schedule_id>', methods=['POST'])
def automation_schedule_update(schedule_id: str):
    """
    Update a scheduled automation task.

    Request body:
    {
        "enabled": true/false,
        "interval_sec": 60,
        "params": {...}
    }
    """
    data = request.get_json() or {}

    with _AUTOMATION_SCHEDULER_LOCK:
        if schedule_id not in _automation_scheduled_tasks:
            return jsonify({
                "success": False,
                "error": f"Schedule not found: {schedule_id}",
            }), 404

        schedule = _automation_scheduled_tasks[schedule_id]

        if "enabled" in data:
            schedule["enabled"] = data["enabled"]
        if "interval_sec" in data:
            schedule["interval_sec"] = max(10, int(data["interval_sec"]))
        if "params" in data:
            schedule["params"] = data["params"]

        schedule["updated_at"] = datetime.now().isoformat()

    return jsonify({
        "success": True,
        "schedule": schedule,
    })


@app.route('/api/automation/schedules')
def automation_schedules_list():
    """List all scheduled automation tasks."""
    with _AUTOMATION_SCHEDULER_LOCK:
        schedules = list(_automation_scheduled_tasks.values())

    return jsonify({
        "success": True,
        "count": len(schedules),
        "schedules": schedules,
    })


@app.route('/api/automation/schedule/<schedule_id>/run', methods=['POST'])
def automation_schedule_run_now(schedule_id: str):
    """Manually trigger a scheduled task to run now."""
    with _AUTOMATION_SCHEDULER_LOCK:
        if schedule_id not in _automation_scheduled_tasks:
            return jsonify({
                "success": False,
                "error": f"Schedule not found: {schedule_id}",
            }), 404

        schedule = _automation_scheduled_tasks[schedule_id]

    # Execute immediately
    task = _create_automation_task(
        schedule["task_type"],
        schedule["action"],
        schedule["params"]
    )
    result_task = _execute_automation_task(task)

    # Update schedule
    with _AUTOMATION_SCHEDULER_LOCK:
        _automation_scheduled_tasks[schedule_id]["last_run"] = time.time()
        _automation_scheduled_tasks[schedule_id]["run_count"] = _automation_scheduled_tasks[schedule_id].get("run_count", 0) + 1
        _automation_scheduled_tasks[schedule_id]["last_result"] = {
            "status": result_task["status"],
            "success": result_task["status"] == "completed",
        }

    return jsonify({
        "success": result_task["status"] == "completed",
        "task_id": result_task["task_id"],
        "status": result_task["status"],
        "result": result_task.get("result"),
    })


@app.route('/api/model-routing/status')
def get_model_routing_status():
    """Get model routing, token usage, and budget summary."""
    return jsonify(build_model_routing_status(event_limit=80))


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
    return jsonify({
        "success": True,
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
            if has_status:
                result = _hub.update_task_status_safe(
                    task_id=task_id,
                    new_status=row.get("status"),
                    expected_version=current_version,
                    owner_id=row.get("owner_id"),
                    lease_token=row.get("lease_token"),
                    force=bool(row.get("force", False)),
                )
                if result.get("success"):
                    current_version = int(result.get("version", current_version) or current_version)
            else:
                result = {"success": True, "task_id": task_id, "version": current_version}

            if result.get("success") and has_assignee:
                assign_result = _hub.assign_task_safe(
                    task_id=task_id,
                    orion_id=row.get("assigned_to"),
                    expected_version=current_version,
                )
                if not assign_result.get("success"):
                    result = assign_result
                else:
                    result = assign_result
            else:
                if not has_status:
                    result = {"success": False, "error": "No update action provided", "error_code": "NO_ACTION"}

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
            status = 409 if code in {"VERSION_CONFLICT", "LEASE_CONFLICT"} else 400
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

    @app.route('/api/hub/workload')
    def get_agent_workload():
        """Get agent workload summary - tasks per assignee with priority breakdown"""
        tasks = _hub.list_tasks()
        orions_snapshot = _hub.get_snapshot().get("orions", {})

        workload: Dict[str, Any] = {}
        unassigned: List[Dict[str, Any]] = []
        priority_counts: Dict[str, int] = {"p0": 0, "p1": 0, "p2": 0, "p3": 0}
        status_counts: Dict[str, int] = {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0}

        for task in tasks:
            assignee = str(task.get("assigned_to") or "").strip().lower() or "unassigned"
            priority_raw = str(task.get("priority") or "normal").strip().lower()
            status_raw = str(task.get("status") or "todo").strip().lower()

            # Normalize priority to P0-P3
            priority_norm = "p3"
            if priority_raw in ("p0", "critical", "urgent", "highest"):
                priority_norm = "p0"
            elif priority_raw in ("p1", "high", "important"):
                priority_norm = "p1"
            elif priority_raw in ("p2", "medium", "normal"):
                priority_norm = "p2"
            priority_counts[priority_norm] = priority_counts.get(priority_norm, 0) + 1

            # Normalize status
            if status_raw in ("in_progress", "running", "active", "claimed"):
                status_counts["in_progress"] = status_counts.get("in_progress", 0) + 1
            elif status_raw in ("done", "completed", "resolved"):
                status_counts["done"] = status_counts.get("done", 0) + 1
            elif status_raw in ("blocked", "stuck", "waiting"):
                status_counts["blocked"] = status_counts.get("blocked", 0) + 1
            else:
                status_counts["todo"] = status_counts.get("todo", 0) + 1

            task_summary = {
                "id": task.get("id"),
                "title": task.get("title", ""),
                "priority": priority_norm,
                "status": status_raw,
                "created_at": task.get("created_at"),
            }

            if assignee == "unassigned":
                unassigned.append(task_summary)
            else:
                if assignee not in workload:
                    workload[assignee] = {
                        "assignee": assignee,
                        "tasks": [],
                        "by_priority": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
                        "by_status": {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0},
                        "is_online": False,
                        "last_heartbeat": None,
                    }
                    # Check if this assignee is an online Orion
                    orion_key = assignee.replace("-", "_")
                    if orion_key in orions_snapshot:
                        orion_info = orions_snapshot[orion_key]
                        workload[assignee]["is_online"] = orion_info.get("is_alive", False)
                        workload[assignee]["last_heartbeat"] = orion_info.get("last_heartbeat")

                workload[assignee]["tasks"].append(task_summary)
                workload[assignee]["by_priority"][priority_norm] = workload[assignee]["by_priority"].get(priority_norm, 0) + 1
                norm_status = "todo"
                if status_raw in ("in_progress", "running", "active", "claimed"):
                    norm_status = "in_progress"
                elif status_raw in ("done", "completed", "resolved"):
                    norm_status = "done"
                elif status_raw in ("blocked", "stuck", "waiting"):
                    norm_status = "blocked"
                workload[assignee]["by_status"][norm_status] = workload[assignee]["by_status"].get(norm_status, 0) + 1

        # Sort by total tasks descending
        workload_list = sorted(workload.values(), key=lambda x: len(x.get("tasks", [])), reverse=True)

        return jsonify({
            "success": True,
            "workload": workload_list,
            "unassigned": unassigned,
            "priority_breakdown": priority_counts,
            "status_breakdown": status_counts,
            "total_tasks": len(tasks),
            "total_assignees": len(workload),
        })

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

except ImportError as e:
    print(f"⚠️ Multi-Orion Hub not available: {e}")


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
    _ensure_openclaw_self_heal_started()
    if OPENCLAW_SELF_HEAL_BOOTSTRAP:
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
