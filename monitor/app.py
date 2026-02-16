"""
Auto Dev Loop - Monitoring Dashboard
Real-time visualization of Orion and Guardian agents

FIXED: Persist state, proper initialization
"""

import os
import sys
import json
import re
import shlex
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Set, Tuple
import logging
import threading
from urllib import request as urllib_request, error as urllib_error

from flask import Flask, render_template, jsonify, request
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
OPENCLAW_BROWSER_AUTO_START = _env_bool("OPENCLAW_BROWSER_AUTO_START", True)
OPENCLAW_BROWSER_TIMEOUT_MS = max(5000, int(os.getenv("OPENCLAW_BROWSER_TIMEOUT_MS", "30000")))
OPENCLAW_DASHBOARD_URL = os.getenv("OPENCLAW_DASHBOARD_URL", "http://127.0.0.1:5050/")
OPENCLAW_FLOW_CHAT_PLACEHOLDERS = [
    "nhập lệnh",
    "nhập yêu cầu",
    "send command",
    "run_cycle",
    "status / pause",
    "lệnh tùy chỉnh",
    "lệnh cho các agent",
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

_monitor_supervisor_thread: Optional[threading.Thread] = None
_monitor_supervisor_stop_event = threading.Event()
_monitor_supervisor_lock = threading.RLock()
_monitor_supervisor_enabled = MONITOR_AUTOPILOT_ENABLED_DEFAULT
_monitor_last_recovery_at: Dict[str, datetime] = {}
_monitor_last_reason: Dict[str, str] = {}
_computer_control_enabled = MONITOR_COMPUTER_CONTROL_ENABLED_DEFAULT
_monitor_progress_thread: Optional[threading.Thread] = None
_monitor_progress_stop_event = threading.Event()
_monitor_progress_lock = threading.RLock()
_monitor_process_started_at = datetime.now()
_runtime_heartbeat_thread: Optional[threading.Thread] = None
_runtime_heartbeat_stop_event = threading.Event()
_runtime_heartbeat_lock = threading.RLock()


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
    if not (MONITOR_AUTO_APPROVE_SAFE_DECISIONS or MONITOR_AUTO_APPROVE_ALL_DECISIONS):
        return
    try:
        pending = list(state.pending_decisions)
    except Exception:
        pending = []
    for item in pending:
        try:
            kind = str(item.get("kind", "")).strip()
            if kind != "command_intervention":
                if MONITOR_AUTO_APPROVE_ALL_DECISIONS:
                    decision_id = str(item.get("id", "")).strip()
                    if decision_id:
                        approved = state.approve_decision(decision_id)
                        if approved:
                            state.add_agent_log("guardian", f"🤖 Auto-approved decision: {approved.get('title', kind or 'decision')}", "success")
                continue
            risk = str(item.get("risk_level", "medium")).strip().lower()
            if not MONITOR_AUTO_APPROVE_ALL_DECISIONS and risk not in {"low"}:
                continue
            if not bool(item.get("auto_execute_on_approve", False)):
                if not MONITOR_AUTO_APPROVE_ALL_DECISIONS:
                    continue
            decision_id = str(item.get("id", "")).strip()
            if not decision_id:
                continue
            approved = state.approve_decision(decision_id)
            if not approved:
                continue
            command = str(approved.get("command", "")).strip()
            if command and MONITOR_AUTO_EXECUTE_COMMANDS:
                execution = _execute_approved_shell_command(command)
                if execution.get("success"):
                    state.add_agent_log("guardian", f"🤖 Auto-executed decision: {execution.get('command')}", "success")
                else:
                    state.add_agent_log("guardian", f"⚠️ Auto-execute failed: {execution.get('error', 'unknown')}", "warning")
            else:
                state.add_agent_log("guardian", "🤖 Auto-approved decision (no execution).", "info")
        except Exception as exc:
            logger.error(f"Safe decision auto-approve error: {exc}")


def build_status_payload() -> Dict[str, Any]:
    payload = state.get_status()
    status_handler = _runtime_bridge.get("status_handler")
    if callable(status_handler):
        try:
            payload["runtime"] = status_handler() or {}
        except Exception as exc:
            logger.error(f"Runtime status handler failed: {exc}")
    payload["orion_instances"] = get_orion_instances_snapshot()
    payload["monitor_supervisor"] = _monitor_supervisor_status()
    payload["computer_control"] = _computer_control_status()
    return payload


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
                "iteration": runtime.get("iteration"),
                "active_flow_count": int(runtime.get("active_flow_count", 0) or 0),
                "agent_count": len(runtime.get("agents") or []),
                "last_progress_at": runtime.get("last_progress_at"),
                "last_cycle_started_at": runtime.get("last_cycle_started_at"),
                "last_cycle_finished_at": runtime.get("last_cycle_finished_at"),
            })
        snapshot.append(item)
    return snapshot


def _monitor_supervisor_status() -> Dict[str, Any]:
    with _monitor_supervisor_lock:
        startup_age_sec = max(0, int((datetime.now() - _monitor_process_started_at).total_seconds()))
        return {
            "enabled": bool(_monitor_supervisor_enabled),
            "thread_alive": bool(_monitor_supervisor_thread and _monitor_supervisor_thread.is_alive()),
            "interval_sec": MONITOR_AUTOPILOT_INTERVAL_SEC,
            "stuck_threshold_sec": MONITOR_STUCK_THRESHOLD_SEC,
            "cooldown_sec": MONITOR_RECOVERY_COOLDOWN_SEC,
            "startup_grace_sec": MONITOR_STARTUP_GRACE_SEC,
            "startup_age_sec": startup_age_sec,
            "last_recovery": {key: value.isoformat() for key, value in _monitor_last_recovery_at.items()},
            "last_reason": dict(_monitor_last_reason),
        }


def _monitor_set_supervisor_enabled(enabled: bool) -> None:
    global _monitor_supervisor_enabled
    with _monitor_supervisor_lock:
        _monitor_supervisor_enabled = bool(enabled)


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

    if paused and pause_reason != "user":
        reason = f"paused:{pause_reason or 'unknown'}"
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
    while not _monitor_supervisor_stop_event.is_set():
        try:
            with _monitor_supervisor_lock:
                enabled = bool(_monitor_supervisor_enabled)
            if enabled:
                for instance in get_orion_instances_snapshot():
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
            last_error = stderr or payload.get("error")
        except subprocess.TimeoutExpired:
            last_error = "openclaw command timeout"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(min(OPENCLAW_CLI_RETRY_DELAY_SEC * attempt, 1.5))
    logger.warning("OpenClaw CLI failed", extra={"cmd": args, "error": last_error})
    return {"success": False, "error": last_error or "openclaw command failed"}


def _openclaw_browser_cmd(
    args: List[str],
    expect_json: bool = True,
    timeout_ms: int = OPENCLAW_BROWSER_TIMEOUT_MS,
    require_token: bool = True,
) -> Dict[str, Any]:
    token_info = _collect_token_info()
    if require_token and not token_info.token:
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
        start = _openclaw_browser_cmd(["start"])
        steps.append({"action": "start_after_menu_click", "result": start})
        status = _openclaw_browser_cmd(["status"])
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
    steps: List[Dict[str, Any]] = []

    if not pyautogui:
        return {"success": False, "error": "pyautogui not available"}

    with _ATTACH_STATE_LOCK:
        now = datetime.now()
        if not force and _attach_state.cooldown_until and now < _attach_state.cooldown_until:
            cooldown = (_attach_state.cooldown_until - now).total_seconds()
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
            start = _openclaw_browser_cmd(["start"])
            steps.append({"action": "start_after_click", "result": start})
            status = _openclaw_browser_cmd(["status"])
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

    return {"success": attached, "method": method_used, "steps": steps}


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


def _create_token_status(info: _OpenClawTokenInfo) -> Dict[str, Any]:
    return {
        "token_present": bool(info.token),
        "health_ok": info.health_ok,
        "health_error": info.health_error,
        "source": info.source,
        "checked_at": info.checked_at.isoformat() if info.checked_at else None,
    }


def _openclaw_dashboard_flow(chat_text: str = "", approve: bool = True, deny: bool = False) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    token_info = _collect_token_info()
    token_status = _create_token_status(token_info)
    if not token_info.token:
        steps.append({
            "action": "token_check",
            "success": False,
            "error": token_info.last_error or "OpenClaw gateway token missing",
            "token_status": token_status,
        })
        return {"success": False, "steps": steps, "token_status": token_status}
    if not token_info.health_ok:
        steps.append({
            "action": "token_health",
            "success": False,
            "error": token_info.health_error or "Gateway health check failed",
            "token_status": token_status,
        })
        return {"success": False, "steps": steps, "token_status": token_status}

    def run_step(action: str, command: List[str], **kwargs) -> Dict[str, Any]:
        result = _openclaw_browser_cmd(command, **kwargs)
        step = _record_flow_step(action, result)
        steps.append(step)
        return step

    if OPENCLAW_BROWSER_AUTO_START:
        start_step = run_step("start", ["start"])
        if not start_step["success"]:
            return {"success": False, "steps": steps, "token_status": token_status}

    open_step = run_step("open", ["open", OPENCLAW_DASHBOARD_URL])
    if not open_step["success"]:
        return {"success": False, "steps": steps, "token_status": token_status}

    time.sleep(1.2)
    key = "Meta+Shift+R" if sys.platform == "darwin" else "Control+Shift+R"
    refresh_step = run_step("hard_refresh", ["press", key], expect_json=False)
    if not refresh_step["success"]:
        return {"success": False, "steps": steps, "token_status": token_status}

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
        return {"success": False, "steps": steps, "token_status": token_status}
    snapshot_payload = snapshot["result"].get("json") or {}

    approve_patterns = ["duyệt prompt", "approve prompt", "approve", "approve (y+enter)", "y+enter"]
    deny_patterns = ["từ chối prompt", "deny prompt", "deny", "reject", "deny (esc)", "esc"]
    if approve:
        ref = _find_openclaw_ref_by_role_and_text(snapshot_payload, ["button"], approve_patterns)
        if ref:
            click_approve = run_step("click_approve", ["click", str(ref)])
            if not click_approve["success"]:
                return {"success": False, "steps": steps, "token_status": token_status}
        else:
            steps.append({"action": "click_approve", "success": False, "error": "Approve button not found"})

    if deny:
        if approve:
            snapshot_after = run_step("snapshot_after_approve", ["snapshot", "--format", "ai", "--labels", "--limit", "220"])
            if not snapshot_after["success"] or not snapshot_after.get("result", {}).get("json"):
                return {"success": False, "steps": steps, "token_status": token_status}
            snapshot_payload = snapshot_after["result"].get("json") or snapshot_payload
        ref = _find_openclaw_ref_by_role_and_text(snapshot_payload, ["button"], deny_patterns)
        if ref:
            click_deny = run_step("click_deny", ["click", str(ref)])
            if not click_deny["success"]:
                return {"success": False, "steps": steps, "token_status": token_status}
        else:
            steps.append({"action": "click_deny", "success": False, "error": "Deny button not found"})

    if normalized_text and any(token in normalized_text for token in ("trang thai", "tom tat", "status")):
        status_ref = _find_openclaw_ref_by_role_and_text(snapshot_payload, ["button"], ["status", "trạng thái"])
        if status_ref:
            click_status = run_step("click_status", ["click", str(status_ref)])
            if not click_status["success"]:
                return {"success": False, "steps": steps, "token_status": token_status}

    if chat_text:
        command_candidates = {"status", "pause", "resume", "run_cycle", "shutdown"}
        maybe_command = chat_text.strip().lower()
        if maybe_command in command_candidates:
            chat_snapshot = run_step("snapshot_chat", ["snapshot", "--format", "ai", "--labels", "--limit", "240"])
            if not chat_snapshot["success"] or not chat_snapshot.get("result", {}).get("json"):
                return {"success": False, "steps": steps, "token_status": token_status}
            chat_payload = chat_snapshot["result"].get("json") or {}
            ref = _find_openclaw_textbox(chat_payload, OPENCLAW_FLOW_CHAT_PLACEHOLDERS, allow_fallback=False)
            if ref:
                type_step = run_step("type_command", ["type", str(ref), maybe_command])
                if not type_step["success"]:
                    return {"success": False, "steps": steps, "token_status": token_status}
                send_ref = _find_openclaw_ref_by_role_and_text(chat_payload, ["button"], ["gửi lệnh", "send"])
                if send_ref:
                    send_step = run_step("send_command", ["click", str(send_ref)])
                else:
                    send_step = run_step("send_command", ["press", "Enter"], expect_json=False)
                if not send_step["success"]:
                    return {"success": False, "steps": steps, "token_status": token_status}
            else:
                steps.append({"action": "type_command", "success": False, "error": "Command input not found"})

    return {
        "success": True,
        "steps": steps,
        "token_status": token_status,
        "note": "Flow completed",
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
            return _prompt_system.record_feedback(
                text=text,
                source="user_chat",
                category=category,
                severity="high",
                add_directive=True,
                directive_priority=119 if category in {"provider", "orchestration", "quality", "process"} else 117,
            )
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


def _build_chat_report(intent: str, executed: bool, reply: str, result: Dict[str, Any]) -> Dict[str, Any]:
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
    return {
        "status": status,
        "summary": reply,
        "highlights": highlights[:3],
        "next_action": next_action,
    }


def _build_project_chat_reply(message: str, instance_id: Optional[str] = None) -> Dict[str, Any]:
    text = str(message or "").strip()
    lowered = text.lower()
    resolved_instance = _extract_orion_instance_id(text, str(instance_id or LOCAL_INSTANCE_ID))
    instance_info = _instance_by_id(resolved_instance)
    instance_label = str(instance_info.get("name", resolved_instance))
    runtime = (build_status_payload().get("runtime") or {})
    activity = _build_agent_activity_snapshot(resolved_instance, max_agents=10)
    activity_lines = _format_agent_activity_lines(activity, max_lines=6)
    ops = _build_ops_snapshot()
    counts = ops["counts"]
    top_agent = str(ops.get("top_agent", "system")).upper()
    latest_msg = ops.get("latest_message") or "Chưa có sự kiện mới."

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
                "result": execution,
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
            "result": advice,
            "reply": reply,
            "pending_decision": pending,
        }

    # Explicit command intents
    if re.search(r"(duyệt prompt|approve prompt|accept prompt|bấm y|press y|ấn y)", lowered):
        result = _computer_control_action("approve_prompt", {})
        return {
            "intent": "computer_approve_prompt",
            "executed": bool(result.get("success")),
            "result": result,
            "reply": f"[{instance_label}] Đã gửi thao tác duyệt prompt (Y + Enter)." if result.get("success") else f"[{instance_label}] Không duyệt được prompt: {result.get('error', 'unknown')}",
        }
    if re.search(r"(từ chối prompt|deny prompt|reject prompt|bấm esc|press esc|ấn esc)", lowered):
        result = _computer_control_action("deny_prompt", {})
        return {
            "intent": "computer_deny_prompt",
            "executed": bool(result.get("success")),
            "result": result,
            "reply": f"[{instance_label}] Đã gửi thao tác từ chối prompt (ESC)." if result.get("success") else f"[{instance_label}] Không từ chối được prompt: {result.get('error', 'unknown')}",
        }
    if re.search(r"(continue prompt|tiếp tục prompt|bấm enter|press enter|ấn enter)", lowered):
        result = _computer_control_action("continue_prompt", {})
        return {
            "intent": "computer_continue_prompt",
            "executed": bool(result.get("success")),
            "result": result,
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
            "result": result,
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
            "result": result,
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
            "result": result,
            "reply": reply,
        }

    if re.search(r"\b(pause|tạm dừng)\b", lowered):
        target = "all" if re.search(r"\b(all|tất cả)\b", lowered) else "orion"
        result = _call_instance_command(resolved_instance, target, "pause", "high", {"control_mode": "strict"})
        return {
            "intent": "command_pause",
            "executed": True,
            "result": result,
            "reply": f"[{instance_label}] Đã gửi lệnh tạm dừng. " + _runtime_status_text(runtime),
        }
    if re.search(r"\b(resume|tiếp tục)\b", lowered) and "guardian" not in lowered:
        target = "all" if re.search(r"\b(all|tất cả)\b", lowered) else "orion"
        result = _call_instance_command(resolved_instance, target, "resume", "high", {"control_mode": "strict"})
        return {
            "intent": "command_resume",
            "executed": True,
            "result": result,
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
            "result": result,
            "reply": f"[{instance_label}] Đã chạy một vòng chủ động và trả quyền tự động lại cho Orion.",
        }
    if re.search(r"(guardian|giám sát).*(stuck|kẹt|đơ|tiếp tục|gỡ)", lowered) or re.search(r"(unstick|gỡ kẹt)", lowered):
        result = _call_instance_command(resolved_instance, "guardian", "unstick_orion", "high", {})
        return {
            "intent": "guardian_recover",
            "executed": True,
            "result": result,
            "reply": f"[{instance_label}] Guardian đã nhận lệnh gỡ kẹt và sẽ đẩy Orion chạy tiếp nếu phát hiện bị chặn.",
        }
    if re.search(r"(autopilot|tự động).*(on|bật)", lowered):
        result = _call_instance_command(resolved_instance, "guardian", "autopilot_on", "high", {})
        return {
            "intent": "guardian_autopilot_on",
            "executed": True,
            "result": result,
            "reply": f"[{instance_label}] Đã bật Guardian autopilot. Hệ thống sẽ tự gỡ stuck và tiếp tục chạy.",
        }
    if re.search(r"(autopilot|tự động).*(off|tắt)", lowered):
        result = _call_instance_command(resolved_instance, "guardian", "autopilot_off", "high", {})
        return {
            "intent": "guardian_autopilot_off",
            "executed": True,
            "result": result,
            "reply": f"[{instance_label}] Đã tắt Guardian autopilot. Hệ thống sẽ không auto-recover nữa.",
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
            f"Agent realtime:\n{agent_block}"
        )
        return {
            "intent": "project_status_summary",
            "executed": False,
            "result": {"agent_activity": activity},
            "reply": reply,
        }

    # Default assistant behavior
    return {
        "intent": "assistant_help",
        "executed": False,
        "result": {},
        "reply": (
            "Mình có thể giúp theo 3 nhóm: (1) tóm tắt tiến độ dự án, "
            "(2) điều khiển flow Orion/Guardian, (3) gỡ stuck tự động. "
            "Bạn có thể nói: 'tóm tắt dự án', 'guardian gỡ kẹt Orion 2', hoặc 'chạy 1 vòng Orion 3'."
        ),
    }


# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/control-center')
def control_center():
    """Multi-Agent Control Center with Kanban"""
    return render_template('control-center.html')


@app.route('/live')
def live_monitor():
    """Live Agent Monitor"""
    return render_template('live-monitor.html')


@app.route('/api/status')
def get_status():
    """Get overall system status"""
    return jsonify(build_status_payload())


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


@app.route('/api/openclaw/browser', methods=['POST'])
def openclaw_browser_command():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip().lower()
    if not action:
        return jsonify({"success": False, "error": "Missing action"}), 400

    if action == "start":
        result = _openclaw_browser_cmd(["start"])
        attach = None
        error_code = result.get("error_code") or ""
        if not result.get("success") and OPENCLAW_AUTO_ATTACH_ENABLED and error_code != "TOKEN_MISSING":
            stderr = str(result.get("stderr", "")).lower()
            if "no tab is connected" in stderr or "extension" in stderr or "relay" in stderr:
                attach = _openclaw_attempt_attach()
                result = _openclaw_browser_cmd(["start"])
    elif action == "status":
        result = _openclaw_browser_cmd(["status"])
    elif action == "open":
        url = str(data.get("url", "")).strip()
        if not url:
            return jsonify({"success": False, "error": "Missing url"}), 400
        result = _openclaw_browser_cmd(["open", url])
    elif action == "navigate":
        url = str(data.get("url", "")).strip()
        if not url:
            return jsonify({"success": False, "error": "Missing url"}), 400
        result = _openclaw_browser_cmd(["navigate", url])
    elif action == "snapshot":
        result = _openclaw_browser_cmd(["snapshot", "--format", "ai", "--labels", "--limit", "240"])
    elif action == "click":
        ref = str(data.get("ref", "")).strip()
        if not ref:
            return jsonify({"success": False, "error": "Missing ref"}), 400
        result = _openclaw_browser_cmd(["click", ref])
    elif action == "type":
        ref = str(data.get("ref", "")).strip()
        text = str(data.get("text", ""))
        if not ref:
            return jsonify({"success": False, "error": "Missing ref"}), 400
        result = _openclaw_browser_cmd(["type", ref, text])
    elif action == "press":
        key = str(data.get("key", "")).strip()
        if not key:
            return jsonify({"success": False, "error": "Missing key"}), 400
        result = _openclaw_browser_cmd(["press", key], expect_json=False)
    elif action == "hard_refresh":
        key = "Meta+Shift+R" if sys.platform == "darwin" else "Control+Shift+R"
        result = _openclaw_browser_cmd(["press", key], expect_json=False)
    else:
        return jsonify({"success": False, "error": f"Unsupported action '{action}'"}), 400

    payload = {
        "success": bool(result.get("success")),
        "action": action,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }
    if action == "start":
        payload["attach"] = attach
    return jsonify(payload)


@app.route('/api/openclaw/flow/dashboard', methods=['POST'])
def openclaw_flow_dashboard():
    data = request.get_json(silent=True) or {}
    chat_text = str(data.get("chat_text", "") or "")
    approve = bool(data.get("approve", True))
    deny = bool(data.get("deny", False))
    result = _openclaw_dashboard_flow(chat_text=chat_text, approve=approve, deny=deny)
    return jsonify({
        "success": bool(result.get("success")),
        "flow": result,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/openclaw/extension/attach', methods=['POST'])
def openclaw_extension_attach():
    if not OPENCLAW_AUTO_ATTACH_ENABLED:
        return jsonify({"success": False, "error": "Auto-attach disabled"}), 400
    result = _openclaw_attempt_attach()
    return jsonify({
        "success": bool(result.get("success")),
        "result": result,
        "timestamp": datetime.now().isoformat(),
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
    enabled = bool(data.get("enabled", True))
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


@app.route('/api/orion/instances')
def get_orion_instances():
    """List all Orion instances and current health."""
    return jsonify({
        "instances": get_orion_instances_snapshot(),
        "default_instance_id": LOCAL_INSTANCE_ID,
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
    enabled = bool(data.get("enabled", True))
    _monitor_set_supervisor_enabled(enabled)
    state.add_agent_log(
        "guardian",
        f"🧭 Monitor autopilot {'enabled' if enabled else 'disabled'} by dashboard",
        "success" if enabled else "warning",
    )
    return jsonify({
        "success": True,
        "enabled": enabled,
        "supervisor": _monitor_supervisor_status(),
        "timestamp": datetime.now().isoformat(),
    })


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


@app.route('/api/chat/ask', methods=['POST'])
def chat_ask():
    """Lightweight assistant chat endpoint for project control and summaries."""
    data = request.get_json(silent=True) or {}
    raw_message = data.get("message")
    if raw_message is None:
        # Backward-compatible alias for external clients.
        raw_message = data.get("question")
    message = str(raw_message or "").strip()
    instance_id = str(data.get("instance_id", LOCAL_INSTANCE_ID)).strip() or LOCAL_INSTANCE_ID
    if not message:
        return jsonify({"success": False, "error": "Missing message"}), 400

    feedback_id = _maybe_record_user_feedback(message)
    message_id = str(datetime.now().timestamp())
    _emit_chat_phase(message_id, "thinking", "Đang phân tích yêu cầu...")
    _emit_chat_phase(message_id, "routing", f"Đang chọn instance điều khiển: {instance_id}")

    state.add_agent_log("orion", f"💬 Chat: {message}", "info")
    _emit_chat_phase(message_id, "processing", "Đang tổng hợp trạng thái runtime và quyết định phù hợp...")
    result = _build_project_chat_reply(message, instance_id=instance_id)
    activity = (result.get("result") or {}).get("agent_activity")
    if isinstance(activity, dict):
        _emit_chat_phase(
            message_id,
            "processing",
            str(activity.get("summary", "Đã lấy snapshot realtime hoạt động agent.")),
        )
    reply = str(result.get("reply", "")).strip() or "Đã nhận yêu cầu."
    intent = str(result.get("intent", "assistant_help"))
    report = result.get("report")
    if not isinstance(report, dict):
        report = _build_chat_report(intent, bool(result.get("executed")), reply, result.get("result", {}) or {})
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
        "pending_decision": result.get("pending_decision"),
        "feedback_id": feedback_id,
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

    declined = state.decline_decision(decision_id)
    if declined:
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
        """Create or list tasks"""
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            task = _hub.create_task(
                title=data.get("title", "New Task"),
                description=data.get("description", ""),
                priority=data.get("priority", "medium"),
                assigned_to=data.get("assigned_to")
            )
            return jsonify({"success": True, "task": task})
        return jsonify({"success": True, "tasks": _hub.tasks})

    @app.route('/api/hub/tasks/<task_id>', methods=['PUT', 'DELETE'])
    def update_task(task_id):
        """Update task status or delete"""
        if request.method == 'DELETE':
            _hub.tasks = [t for t in _hub.tasks if t.get("id") != task_id]
            _hub.save_state()
            return jsonify({"success": True})
        data = request.get_json(silent=True) or {}
        if "status" in data:
            task = _hub.update_task_status(task_id, data["status"])
        elif "assigned_to" in data:
            task = _hub.assign_task(task_id, data["assigned_to"])
        else:
            task = {}
        return jsonify({"success": True, "task": task})

    @app.route('/api/hub/orions')
    def get_hub_orions():
        """Get all registered Orion instances"""
        return jsonify({
            "success": True,
            "orions": _hub.orions,
            "metrics": _hub.get_metrics()
        })

    @app.route('/api/hub/audit')
    def get_audit_log():
        """Get audit log"""
        limit = request.args.get("limit", 50, type=int)
        return jsonify({
            "success": True,
            "audit": _hub.audit_log[-limit:]
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
