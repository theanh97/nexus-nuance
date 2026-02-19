"""
ULTIMATE MULTI-AGENT ORCHESTRATION SYSTEM
==========================================
Vision: The most advanced AI agent management platform

Key Features:
1. Multi-Orion Hub - Control multiple Orion instances
2. Kanban Workflow - Drag-drop task management
3. Real-time Agent Fleet Monitor
4. Job Queue with Priority
5. Human-in-the-loop Controls
6. Analytics & Metrics
7. Audit Trail
8. Smart Notifications
"""

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl  # POSIX only
except Exception:  # pragma: no cover
    fcntl = None


class MultiOrionHub:
    """Central hub for managing multiple Orion instances with cross-process safety."""

    def __init__(self, state_path: Optional[str] = None):
        self.state_file = Path(state_path or "data/multi_orion_state.json")
        self.lock_file = self.state_file.with_suffix(".lock")
        self._local_lock = threading.RLock()

        self.orions: Dict[str, Dict[str, Any]] = {}
        self.tasks: List[Dict[str, Any]] = []
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.agent_works: Dict[str, Dict[str, Any]] = {}  # Track agent work on files
        self.agent_messages: List[Dict[str, Any]] = []  # Agent-to-agent messages
        self.audit_log: List[Dict[str, Any]] = []
        self.version: int = 0
        self.updated_at: Optional[str] = None

        self.max_audit_entries = max(200, int(os.getenv("MULTI_ORION_AUDIT_LIMIT", "1000")))
        self.default_orion_lease_ttl_sec = max(10, int(os.getenv("MULTI_ORION_LEASE_TTL_SEC", "35")))
        self.default_task_lease_sec = max(15, int(os.getenv("MULTI_ORION_TASK_LEASE_SEC", "120")))
        self.max_task_lease_sec = max(self.default_task_lease_sec, int(os.getenv("MULTI_ORION_TASK_LEASE_MAX_SEC", "900")))

        self.load_state()

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @classmethod
    def _now_iso(cls) -> str:
        return cls._now().isoformat()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": 0,
            "updated_at": self._now_iso(),
            "orions": {},
            "tasks": [],
            "agents": {},
            "agent_works": {},  # Track which agent is working on which file (Phase 4)
            "agent_messages": [],  # Agent-to-agent messages (Phase 4)
            "audit_log": [],
        }

    def _normalize_orion(self, instance_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row or {})
        now_iso = self._now_iso()
        ttl = int(data.get("lease_ttl_sec") or self.default_orion_lease_ttl_sec)
        ttl = max(10, ttl)
        last_hb = str(data.get("last_heartbeat_at") or data.get("registered_at") or now_iso)
        try:
            expires_at = datetime.fromisoformat(last_hb) + timedelta(seconds=ttl)
        except Exception:
            expires_at = self._now() + timedelta(seconds=ttl)
        return {
            "id": str(instance_id),
            "status": str(data.get("status", "active")),
            "config": data.get("config") if isinstance(data.get("config"), dict) else {},
            "registered_at": str(data.get("registered_at") or now_iso),
            "last_heartbeat_at": last_hb,
            "lease_ttl_sec": ttl,
            "lease_expires_at": str(data.get("lease_expires_at") or expires_at.isoformat()),
            "tasks_completed": int(data.get("tasks_completed") or 0),
            "current_task": data.get("current_task"),
            "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        }

    def _normalize_task(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row or {})
        now_iso = self._now_iso()
        return {
            "id": str(data.get("id") or f"task_{uuid.uuid4().hex[:8]}"),
            "title": str(data.get("title") or "New Task"),
            "description": str(data.get("description") or ""),
            "priority": str(data.get("priority") or "medium"),
            "status": str(data.get("status") or "backlog"),
            "assigned_to": data.get("assigned_to"),
            "created_at": str(data.get("created_at") or now_iso),
            "updated_at": str(data.get("updated_at") or now_iso),
            "due_date": data.get("due_date"),
            "tags": list(data.get("tags") or []),
            "subtasks": list(data.get("subtasks") or []),
            "comments": list(data.get("comments") or []),
            "lease_owner": data.get("lease_owner"),
            "lease_token": data.get("lease_token"),
            "lease_expires_at": data.get("lease_expires_at"),
            "claimed_at": data.get("claimed_at"),
        }

    def _normalize_state(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        base = self._default_state()
        if not isinstance(raw, dict):
            return base

        orions_raw = raw.get("orions") if isinstance(raw.get("orions"), dict) else {}
        tasks_raw = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []

        normalized_orions = {
            str(orion_id): self._normalize_orion(str(orion_id), row if isinstance(row, dict) else {})
            for orion_id, row in orions_raw.items()
        }
        normalized_tasks = [
            self._normalize_task(item) for item in tasks_raw if isinstance(item, dict)
        ]

        version = int(raw.get("version") or 0)
        updated_at = str(raw.get("updated_at") or base["updated_at"])
        agents = raw.get("agents") if isinstance(raw.get("agents"), dict) else {}
        agent_works = raw.get("agent_works") if isinstance(raw.get("agent_works"), dict) else {}
        agent_messages = raw.get("agent_messages") if isinstance(raw.get("agent_messages"), list) else []
        audit_log = raw.get("audit_log") if isinstance(raw.get("audit_log"), list) else []

        return {
            "version": max(0, version),
            "updated_at": updated_at,
            "orions": normalized_orions,
            "tasks": normalized_tasks,
            "agents": dict(agents),
            "agent_works": dict(agent_works),
            "agent_messages": list(agent_messages),
            "audit_log": list(audit_log)[-self.max_audit_entries :],
        }

    def _read_state_unlocked(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return self._default_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._normalize_state(data)
        except Exception:
            return self._default_state()

    def _write_state_unlocked(self, state: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.state_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, self.state_file)

    def _apply_state_to_memory(self, state: Dict[str, Any]) -> None:
        self.version = int(state.get("version") or 0)
        self.updated_at = str(state.get("updated_at") or self._now_iso())
        self.orions = state.get("orions") if isinstance(state.get("orions"), dict) else {}
        self.tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
        self.agents = state.get("agents") if isinstance(state.get("agents"), dict) else {}
        self.agent_works = state.get("agent_works") if isinstance(state.get("agent_works"), dict) else {}
        self.agent_messages = state.get("agent_messages") if isinstance(state.get("agent_messages"), list) else []
        self.audit_log = state.get("audit_log") if isinstance(state.get("audit_log"), list) else []

    def _append_audit(self, state: Dict[str, Any], action: str, details: Dict[str, Any]) -> None:
        rows = state.get("audit_log") if isinstance(state.get("audit_log"), list) else []
        rows.append(
            {
                "timestamp": self._now_iso(),
                "action": str(action),
                "details": dict(details or {}),
            }
        )
        state["audit_log"] = rows[-self.max_audit_entries :]

    def _commit_state(self, state: Dict[str, Any], increment_version: bool = True) -> int:
        next_version = int(state.get("version") or 0)
        if increment_version:
            next_version += 1
        state["version"] = next_version
        state["updated_at"] = self._now_iso()
        self._write_state_unlocked(state)
        self._apply_state_to_memory(state)
        return next_version

    @contextmanager
    def _exclusive_state(self):
        with self._local_lock:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock_file, "a+", encoding="utf-8") as lock_fh:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    state = self._read_state_unlocked()
                    yield state
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _parse_iso(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    def _is_task_lease_active(self, task: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        now_dt = now or self._now()
        expires = self._parse_iso(task.get("lease_expires_at"))
        return bool(expires and expires > now_dt and task.get("lease_owner") and task.get("lease_token"))

    def _find_task(self, state: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
        target = str(task_id or "").strip()
        for task in state.get("tasks", []):
            if str(task.get("id")) == target:
                return task
        return None

    def _version_conflict(self, expected_version: Optional[int], current_version: int) -> Optional[Dict[str, Any]]:
        if expected_version is None:
            return None
        try:
            expected = int(expected_version)
        except Exception:
            return {
                "success": False,
                "error": "Invalid expected_version",
                "error_code": "INVALID_EXPECTED_VERSION",
                "current_version": current_version,
            }
        if expected != current_version:
            return {
                "success": False,
                "error": "State version conflict",
                "error_code": "VERSION_CONFLICT",
                "expected_version": expected,
                "current_version": current_version,
            }
        return None

    def load_state(self):
        """Load existing state safely."""
        with self._exclusive_state() as state:
            self._apply_state_to_memory(state)

    def save_state(self):
        """Persist in-memory state safely."""
        with self._exclusive_state() as state:
            state["orions"] = self.orions
            state["tasks"] = [self._normalize_task(t) for t in self.tasks if isinstance(t, dict)]
            state["agents"] = self.agents
            state["audit_log"] = self.audit_log[-self.max_audit_entries :]
            self._commit_state(state)

    def get_snapshot(self) -> Dict[str, Any]:
        with self._exclusive_state() as state:
            self._apply_state_to_memory(state)
            return {
                "version": int(state.get("version") or 0),
                "updated_at": state.get("updated_at"),
                "orions": state.get("orions", {}),
                "tasks": state.get("tasks", []),
                "agents": state.get("agents", {}),
                "audit_log": state.get("audit_log", []),
            }

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._exclusive_state() as state:
            tasks = list(state.get("tasks", []))
            self._apply_state_to_memory(state)
            return tasks

    def register_orion(self, instance_id: str, config: Dict, lease_ttl_sec: Optional[int] = None) -> Dict[str, Any]:
        """Register (or refresh) an Orion instance."""
        orion_id = str(instance_id or "").strip()
        if not orion_id:
            return {"success": False, "error": "Missing instance_id", "error_code": "MISSING_INSTANCE_ID"}

        with self._exclusive_state() as state:
            ttl = int(lease_ttl_sec or self.default_orion_lease_ttl_sec)
            ttl = max(10, ttl)
            now = self._now()
            now_iso = now.isoformat()
            expires_at = (now + timedelta(seconds=ttl)).isoformat()
            existing = state.get("orions", {}).get(orion_id)
            row = {
                "id": orion_id,
                "status": "active",
                "config": config if isinstance(config, dict) else {},
                "registered_at": str(existing.get("registered_at")) if isinstance(existing, dict) and existing.get("registered_at") else now_iso,
                "last_heartbeat_at": now_iso,
                "lease_ttl_sec": ttl,
                "lease_expires_at": expires_at,
                "tasks_completed": int(existing.get("tasks_completed") or 0) if isinstance(existing, dict) else 0,
                "current_task": existing.get("current_task") if isinstance(existing, dict) else None,
                "metadata": existing.get("metadata") if isinstance(existing, dict) and isinstance(existing.get("metadata"), dict) else {},
            }
            state.setdefault("orions", {})[orion_id] = row
            self._append_audit(state, "orion_registered", {"instance_id": orion_id})
            version = self._commit_state(state)
            return {"success": True, "orion": row, "version": version}

    def heartbeat_orion(
        self,
        instance_id: str,
        status: str = "active",
        lease_ttl_sec: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Heartbeat an Orion instance lease."""
        orion_id = str(instance_id or "").strip()
        if not orion_id:
            return {"success": False, "error": "Missing instance_id", "error_code": "MISSING_INSTANCE_ID"}

        with self._exclusive_state() as state:
            row = state.get("orions", {}).get(orion_id)
            if not isinstance(row, dict):
                return {"success": False, "error": "Orion not found", "error_code": "ORION_NOT_FOUND", "version": int(state.get("version") or 0)}
            ttl = int(lease_ttl_sec or row.get("lease_ttl_sec") or self.default_orion_lease_ttl_sec)
            ttl = max(10, ttl)
            now = self._now()
            row["status"] = str(status or row.get("status") or "active")
            row["last_heartbeat_at"] = now.isoformat()
            row["lease_ttl_sec"] = ttl
            row["lease_expires_at"] = (now + timedelta(seconds=ttl)).isoformat()
            if isinstance(metadata, dict):
                existing_meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                existing_meta.update(metadata)
                row["metadata"] = existing_meta
            self._append_audit(state, "orion_heartbeat", {"instance_id": orion_id, "status": row["status"]})
            version = self._commit_state(state)
            return {"success": True, "orion": row, "version": version}

    def create_task_safe(
        self,
        title: str,
        description: str,
        priority: str = "medium",
        assigned_to: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            now_iso = self._now_iso()
            task = self._normalize_task(
                {
                    "id": f"task_{uuid.uuid4().hex[:8]}",
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "status": "backlog",
                    "assigned_to": assigned_to,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
            )
            state.setdefault("tasks", []).append(task)
            self._append_audit(state, "task_created", {"task_id": task["id"], "title": task["title"]})
            version = self._commit_state(state)
            return {"success": True, "task": task, "version": version}

    def create_task(self, title: str, description: str, priority: str = "medium", assigned_to: Optional[str] = None) -> Dict[str, Any]:
        result = self.create_task_safe(title=title, description=description, priority=priority, assigned_to=assigned_to)
        return result.get("task", {}) if result.get("success") else {}

    def update_task_status_safe(
        self,
        task_id: str,
        new_status: str,
        expected_version: Optional[int] = None,
        owner_id: Optional[str] = None,
        lease_token: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            task = self._find_task(state, task_id)
            if not task:
                return {"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND", "version": current_version}

            now = self._now()
            if self._is_task_lease_active(task, now) and not force:
                lease_owner = str(task.get("lease_owner") or "")
                request_owner = str(owner_id or "")
                request_token = str(lease_token or "")
                if request_owner != lease_owner or request_token != str(task.get("lease_token") or ""):
                    return {
                        "success": False,
                        "error": "Task is leased by another owner",
                        "error_code": "LEASE_CONFLICT",
                        "task": task,
                        "version": current_version,
                    }

            old_status = str(task.get("status") or "backlog")
            task["status"] = str(new_status or old_status)
            task["updated_at"] = now.isoformat()
            if task["status"] in {"done", "review"}:
                task["lease_owner"] = None
                task["lease_token"] = None
                task["lease_expires_at"] = None

            self._append_audit(
                state,
                "task_moved",
                {
                    "task_id": str(task.get("id")),
                    "from": old_status,
                    "to": task["status"],
                    "owner_id": owner_id,
                },
            )
            version = self._commit_state(state)
            return {"success": True, "task": task, "version": version}

    def update_task_status(self, task_id: str, new_status: str) -> Dict[str, Any]:
        result = self.update_task_status_safe(task_id=task_id, new_status=new_status)
        return result.get("task", {}) if result.get("success") else {}

    def assign_task_safe(
        self,
        task_id: str,
        orion_id: str,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            task = self._find_task(state, task_id)
            if not task:
                return {"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND", "version": current_version}

            task["assigned_to"] = str(orion_id or "") or None
            task["status"] = "todo"
            task["updated_at"] = self._now_iso()
            self._append_audit(state, "task_assigned", {"task_id": task_id, "orion_id": task["assigned_to"]})
            version = self._commit_state(state)
            return {"success": True, "task": task, "version": version}

    def assign_task(self, task_id: str, orion_id: str) -> Dict[str, Any]:
        result = self.assign_task_safe(task_id=task_id, orion_id=orion_id)
        return result.get("task", {}) if result.get("success") else {}

    def delete_task_safe(self, task_id: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            rows = state.get("tasks") if isinstance(state.get("tasks"), list) else []
            before = len(rows)
            rows = [task for task in rows if str(task.get("id")) != str(task_id)]
            if len(rows) == before:
                return {"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND", "version": current_version}
            state["tasks"] = rows
            self._append_audit(state, "task_deleted", {"task_id": task_id})
            version = self._commit_state(state)
            return {"success": True, "task_id": task_id, "version": version}

    def claim_task(
        self,
        task_id: str,
        owner_id: str,
        lease_sec: Optional[int] = None,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not owner:
            return {"success": False, "error": "Missing owner_id", "error_code": "MISSING_OWNER_ID"}

        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            task = self._find_task(state, task_id)
            if not task:
                return {"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND", "version": current_version}

            now = self._now()
            active = self._is_task_lease_active(task, now)
            current_owner = str(task.get("lease_owner") or "")
            if active and current_owner and current_owner != owner:
                return {
                    "success": False,
                    "error": "Task already leased",
                    "error_code": "LEASE_CONFLICT",
                    "task": task,
                    "version": current_version,
                }

            effective_lease = int(lease_sec or self.default_task_lease_sec)
            effective_lease = max(15, min(self.max_task_lease_sec, effective_lease))
            token = str(task.get("lease_token") or "") if current_owner == owner and active else uuid.uuid4().hex
            expires_at = (now + timedelta(seconds=effective_lease)).isoformat()

            task["lease_owner"] = owner
            task["lease_token"] = token
            task["lease_expires_at"] = expires_at
            task["claimed_at"] = str(task.get("claimed_at") or now.isoformat())
            if str(task.get("status") or "") in {"backlog", "todo"}:
                task["status"] = "in_progress"
            task["updated_at"] = now.isoformat()

            self._append_audit(
                state,
                "task_claimed",
                {
                    "task_id": task_id,
                    "owner_id": owner,
                    "lease_sec": effective_lease,
                },
            )
            version = self._commit_state(state)
            return {
                "success": True,
                "task": task,
                "lease": {
                    "owner_id": owner,
                    "lease_token": token,
                    "lease_expires_at": expires_at,
                    "lease_sec": effective_lease,
                },
                "version": version,
            }

    def heartbeat_task_lease(
        self,
        task_id: str,
        owner_id: str,
        lease_token: str,
        lease_sec: Optional[int] = None,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        owner = str(owner_id or "").strip()
        token = str(lease_token or "").strip()
        if not owner or not token:
            return {
                "success": False,
                "error": "Missing owner_id or lease_token",
                "error_code": "MISSING_LEASE_CONTEXT",
            }

        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            task = self._find_task(state, task_id)
            if not task:
                return {"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND", "version": current_version}

            now = self._now()
            if not self._is_task_lease_active(task, now):
                return {
                    "success": False,
                    "error": "Task lease expired",
                    "error_code": "LEASE_EXPIRED",
                    "task": task,
                    "version": current_version,
                }

            if str(task.get("lease_owner") or "") != owner or str(task.get("lease_token") or "") != token:
                return {
                    "success": False,
                    "error": "Lease ownership mismatch",
                    "error_code": "LEASE_CONFLICT",
                    "task": task,
                    "version": current_version,
                }

            effective_lease = int(lease_sec or self.default_task_lease_sec)
            effective_lease = max(15, min(self.max_task_lease_sec, effective_lease))
            expires_at = (now + timedelta(seconds=effective_lease)).isoformat()
            task["lease_expires_at"] = expires_at
            task["updated_at"] = now.isoformat()

            self._append_audit(
                state,
                "task_lease_heartbeat",
                {
                    "task_id": task_id,
                    "owner_id": owner,
                    "lease_sec": effective_lease,
                },
            )
            version = self._commit_state(state)
            return {
                "success": True,
                "task": task,
                "lease": {
                    "owner_id": owner,
                    "lease_token": token,
                    "lease_expires_at": expires_at,
                    "lease_sec": effective_lease,
                },
                "version": version,
            }

    def release_task_lease(
        self,
        task_id: str,
        owner_id: str,
        lease_token: str,
        expected_version: Optional[int] = None,
        next_status: Optional[str] = None,
        force_if_expired: bool = False,
        actor_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        owner = str(owner_id or "").strip()
        token = str(lease_token or "").strip()
        actor = str(actor_id or "").strip()
        force_release = bool(force_if_expired)
        if (not owner or not token) and not force_release:
            return {
                "success": False,
                "error": "Missing owner_id or lease_token",
                "error_code": "MISSING_LEASE_CONTEXT",
            }

        with self._exclusive_state() as state:
            current_version = int(state.get("version") or 0)
            conflict = self._version_conflict(expected_version, current_version)
            if conflict:
                return conflict

            task = self._find_task(state, task_id)
            if not task:
                return {"success": False, "error": "Task not found", "error_code": "TASK_NOT_FOUND", "version": current_version}

            lease_owner = str(task.get("lease_owner") or "").strip()
            lease_token_current = str(task.get("lease_token") or "").strip()
            lease_expires_at = self._parse_iso(task.get("lease_expires_at"))
            lease_expired = bool(lease_expires_at and lease_expires_at <= self._now())
            if force_release:
                if not actor:
                    return {
                        "success": False,
                        "error": "Missing actor_id for force release",
                        "error_code": "MISSING_ACTOR_ID",
                        "task": task,
                        "version": current_version,
                    }
                if not lease_expired:
                    return {
                        "success": False,
                        "error": "Lease is still active",
                        "error_code": "LEASE_NOT_EXPIRED",
                        "task": task,
                        "version": current_version,
                    }
                if owner and lease_owner and owner != lease_owner:
                    return {
                        "success": False,
                        "error": "Lease ownership mismatch",
                        "error_code": "LEASE_CONFLICT",
                        "task": task,
                        "version": current_version,
                    }
            elif lease_owner != owner or lease_token_current != token:
                return {
                    "success": False,
                    "error": "Lease ownership mismatch",
                    "error_code": "LEASE_CONFLICT",
                    "task": task,
                    "version": current_version,
                }

            task["lease_owner"] = None
            task["lease_token"] = None
            task["lease_expires_at"] = None
            if next_status:
                task["status"] = str(next_status)
            elif str(task.get("status") or "") == "in_progress":
                task["status"] = "todo"
            task["updated_at"] = self._now_iso()

            if force_release:
                self._append_audit(
                    state,
                    "task_released_force_expired",
                    {
                        "task_id": task_id,
                        "owner_id": lease_owner or owner,
                        "actor_id": actor,
                        "reason": str(reason or "force_if_expired"),
                        "next_status": task.get("status"),
                    },
                )
            else:
                self._append_audit(
                    state,
                    "task_released",
                    {
                        "task_id": task_id,
                        "owner_id": owner,
                        "next_status": task.get("status"),
                    },
                )
            version = self._commit_state(state)
            return {"success": True, "task": task, "version": version}

    def get_kanban_board(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get tasks organized by status (Kanban)."""
        with self._exclusive_state() as state:
            tasks = list(state.get("tasks", []))
            self._apply_state_to_memory(state)

        columns = {
            "backlog": [],
            "todo": [],
            "in_progress": [],
            "blocked": [],
            "review": [],
            "done": [],
        }
        for task in tasks:
            status = str(task.get("status") or "backlog")
            if status not in columns:
                status = "backlog"
            columns[status].append(task)
        return columns

    def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics."""
        with self._exclusive_state() as state:
            orions = state.get("orions", {}) if isinstance(state.get("orions"), dict) else {}
            tasks = state.get("tasks", []) if isinstance(state.get("tasks"), list) else []
            audit_log = state.get("audit_log", []) if isinstance(state.get("audit_log"), list) else []
            version = int(state.get("version") or 0)
            updated_at = state.get("updated_at")
            self._apply_state_to_memory(state)

        now = self._now()
        alive_orions = 0
        for row in orions.values():
            if not isinstance(row, dict):
                continue
            lease_expires = self._parse_iso(row.get("lease_expires_at"))
            if lease_expires and lease_expires > now and str(row.get("status") or "active") != "offline":
                alive_orions += 1

        total_tasks = len(tasks)
        done_tasks = len([t for t in tasks if str(t.get("status")) == "done"])
        in_progress = len([t for t in tasks if str(t.get("status")) == "in_progress"])
        blocked_tasks = len([t for t in tasks if str(t.get("status")) == "blocked"])
        leased_tasks = len([t for t in tasks if self._is_task_lease_active(t, now)])

        return {
            "total_tasks": total_tasks,
            "done_tasks": done_tasks,
            "in_progress": in_progress,
            "blocked_tasks": blocked_tasks,
            "leased_tasks": leased_tasks,
            "completion_rate": round(done_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0,
            "orion_count": len(orions),
            "active_orions": alive_orions,
            "audit_entries": len(audit_log),
            "state_version": version,
            "state_updated_at": updated_at,
        }

    # ============================================
    # Sub-Agent Awareness (Phase 4)
    # ============================================

    def register_agent_work(
        self,
        orion_id: str,
        agent_id: str,
        file_path: str,
        task_description: str = ""
    ) -> Dict[str, Any]:
        """Register that an agent is starting work on a file."""
        with self._exclusive_state() as state:
            agent_works = state.get("agent_works", {})

            # Create work entry
            work_id = str(uuid.uuid4())
            now_iso = self._now_iso()
            agent_works[work_id] = {
                "work_id": work_id,
                "orion_id": str(orion_id),
                "agent_id": str(agent_id),
                "file_path": str(file_path),
                "task_description": str(task_description),
                "started_at": now_iso,
                "status": "in_progress",
            }

            state["agent_works"] = agent_works
            self._append_audit(state, "agent_work_started", {
                "work_id": work_id,
                "orion_id": orion_id,
                "agent_id": agent_id,
                "file_path": file_path,
            })
            self._commit_state(state)

            return {"success": True, "work_id": work_id}

    def get_agent_working_on_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Query which agent is currently working on a file."""
        with self._exclusive_state() as state:
            agent_works = state.get("agent_works", {})
            for work in agent_works.values():
                if work.get("file_path") == file_path and work.get("status") == "in_progress":
                    return work
            return None

    def get_all_active_works(self) -> List[Dict[str, Any]]:
        """Get all currently active agent works."""
        with self._exclusive_state() as state:
            agent_works = state.get("agent_works", {})
            return [
                work for work in agent_works.values()
                if work.get("status") == "in_progress"
            ]

    def complete_agent_work(self, work_id: str) -> Dict[str, Any]:
        """Mark an agent work as completed."""
        with self._exclusive_state() as state:
            agent_works = state.get("agent_works", {})
            if work_id in agent_works:
                agent_works[work_id]["status"] = "completed"
                agent_works[work_id]["completed_at"] = self._now_iso()
                state["agent_works"] = agent_works
                self._append_audit(state, "agent_work_completed", {"work_id": work_id})
                self._commit_state(state)
                return {"success": True}
            return {"success": False, "error": "Work not found"}

    def send_agent_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message: str,
        orion_id: str
    ) -> Dict[str, Any]:
        """Send a message directly to an agent."""
        with self._exclusive_state() as state:
            agent_messages = state.get("agent_messages", [])
            msg_id = str(uuid.uuid4())

            agent_messages.append({
                "message_id": msg_id,
                "from_agent_id": str(from_agent_id),
                "to_agent_id": str(to_agent_id),
                "orion_id": str(orion_id),
                "message": str(message),
                "created_at": self._now_iso(),
                "read": False,
            })

            # Keep only last 100 messages
            if len(agent_messages) > 100:
                agent_messages = agent_messages[-100:]

            state["agent_messages"] = agent_messages
            self._commit_state(state)

            return {"success": True, "message_id": msg_id}

    def broadcast_to_agents(
        self,
        from_agent_id: str,
        message: str,
        orion_id: str
    ) -> Dict[str, Any]:
        """Broadcast a message to all agents."""
        with self._exclusive_state() as state:
            agent_messages = state.get("agent_messages", [])
            msg_id = str(uuid.uuid4())

            agent_messages.append({
                "message_id": msg_id,
                "from_agent_id": str(from_agent_id),
                "to_agent_id": None,  # Broadcast
                "orion_id": str(orion_id),
                "message": str(message),
                "created_at": self._now_iso(),
                "read": False,
            })

            # Keep only last 100 messages
            if len(agent_messages) > 100:
                agent_messages = agent_messages[-100:]

            state["agent_messages"] = agent_messages
            self._commit_state(state)

            return {"success": True, "message_id": msg_id, "broadcast": True}

    def get_agent_messages(self, agent_id: str, unread_only: bool = False) -> List[Dict[str, Any]]:
        """Get messages for a specific agent."""
        with self._exclusive_state() as state:
            agent_messages = state.get("agent_messages", [])
            return [
                msg for msg in agent_messages
                if msg.get("to_agent_id") == agent_id or msg.get("to_agent_id") is None
                if not unread_only or not msg.get("read")
            ]

    def mark_agent_message_read(self, message_id: str) -> Dict[str, Any]:
        """Mark an agent message as read."""
        with self._exclusive_state() as state:
            agent_messages = state.get("agent_messages", [])
            for msg in agent_messages:
                if msg.get("message_id") == message_id:
                    msg["read"] = True
                    state["agent_messages"] = agent_messages
                    self._commit_state(state)
                    return {"success": True}
            return {"success": False, "error": "Message not found"}


# Singleton
_hub = None


def get_multi_orion_hub() -> MultiOrionHub:
    global _hub
    if _hub is None:
        _hub = MultiOrionHub()
    return _hub


if __name__ == "__main__":
    hub = get_multi_orion_hub()

    if not hub.tasks:
        hub.create_task("Setup Multi-Agent Infrastructure", "Configure Orion cluster", "high")
        hub.create_task("Build Kanban Dashboard", "Visual workflow management", "urgent")
        hub.create_task("Implement Real-time Sync", "WebSocket for live updates", "high")
        hub.create_task("Add Analytics Module", "Track agent performance", "medium")
        hub.create_task("Security Review", "Audit all agent actions", "high")

    print("🎯 MULTI-ORION HUB")
    print("=" * 50)
    print("\n📊 METRICS:")
    m = hub.get_metrics()
    for k, v in m.items():
        print(f"  {k}: {v}")

    print("\n📋 KANBAN BOARD:")
    board = hub.get_kanban_board()
    for col, tasks in board.items():
        print(f"  {col}: {len(tasks)} tasks")
