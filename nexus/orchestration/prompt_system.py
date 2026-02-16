"""
Prompt System - Global non-negotiable directives injected into every agent call.
"""

import json
import os
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PROMPT_SENTINEL = "CORE_PROMPT_SYSTEM_DIRECTIVE_V1"


class PromptSystem:
    """Persistent global prompt policy for all agents."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, state_path: str = None):
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._lock = threading.RLock()
        def _env_bool(name: str, default: bool = False) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        self.enabled = os.getenv("ENABLE_CORE_PROMPT_SYSTEM", "true").strip().lower() == "true"
        self.max_custom_directives = int(os.getenv("PROMPT_SYSTEM_MAX_DIRECTIVES", "100"))
        self.max_injected_directives = max(1, int(os.getenv("PROMPT_SYSTEM_MAX_INJECTED_DIRECTIVES", "4")))
        self.low_importance_injected_directives = max(
            1,
            int(os.getenv("PROMPT_SYSTEM_LOW_IMPORTANCE_DIRECTIVES", "2")),
        )
        self.low_importance_compact = _env_bool("PROMPT_SYSTEM_LOW_IMPORTANCE_COMPACT", True)
        self.include_profile_for_low_importance = _env_bool(
            "PROMPT_SYSTEM_INCLUDE_PROFILE_LOW_IMPORTANCE",
            False,
        )
        self.tracklog_enabled = os.getenv("ENABLE_PROMPT_SYSTEM_TRACKLOG", "true").strip().lower() == "true"

        if state_path:
            self.state_path = Path(state_path)
        else:
            try:
                project_root = Path(__file__).parent.parent.parent
                self.state_path = project_root / "data" / "state" / "prompt_system.json"
            except Exception:
                self.state_path = Path.cwd() / "data" / "state" / "prompt_system.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.state_path.parent.parent / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.feedback_path = self.state_path.parent / "user_feedback.jsonl"
        self._state = self._load_or_init_state()
        self.ensure_core_directives()
        self._log_event(
            "prompt_system_initialized",
            {
                "enabled": self.enabled,
                "directives_count": len(self._state.get("directives", [])),
                "state_path": str(self.state_path),
            },
        )
        self._initialized = True

    def _default_state(self) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        return {
            "version": "1.0",
            "created_at": now,
            "updated_at": now,
            "directives": [],
        }

    def _load_or_init_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            state = self._default_state()
            self._save(state)
            return state

        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Invalid prompt system state")
            data.setdefault("directives", [])
            data.setdefault("version", "1.0")
            data.setdefault("created_at", datetime.now().isoformat())
            data.setdefault("updated_at", datetime.now().isoformat())
            return data
        except Exception:
            state = self._default_state()
            self._save(state)
            return state

    def _save(self, state: Dict[str, Any]) -> None:
        with self._lock:
            state["updated_at"] = datetime.now().isoformat()
            tmp_path = self.state_path.with_name(
                f"{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.state_path)

    def _stable_id(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    def _events_path(self, dt: datetime = None) -> Path:
        ts = dt or datetime.now()
        return self.logs_dir / f"prompt_system_events_{ts.strftime('%Y%m%d')}.jsonl"

    def _feedback_id(self, text: str, source: str, category: str) -> str:
        return self._stable_id(f"{source}|{category}|{text.strip().lower()}")

    def _log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.tracklog_enabled:
            return
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": str(event_type)[:80],
                "payload": payload if isinstance(payload, dict) else {},
            }
            with open(self._events_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _read_today_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        path = self._events_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            events: List[Dict[str, Any]] = []
            for line in lines[-max(1, limit):]:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return events
        except Exception:
            return []

    def _read_feedback(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.feedback_path.exists():
            return []
        try:
            with open(self.feedback_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            rows: List[Dict[str, Any]] = []
            for line in lines[-max(1, limit):]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
            return rows
        except Exception:
            return []

    def record_feedback(
        self,
        text: str,
        source: str = "user",
        category: str = "general",
        severity: str = "high",
        add_directive: bool = True,
        directive_priority: int = 116,
    ) -> str:
        """Persist user/system feedback and optionally elevate to guardrail directive."""
        feedback_text = str(text or "").strip()
        if not feedback_text:
            return ""

        source = str(source or "user").strip().lower()
        category = str(category or "general").strip().lower()
        severity = str(severity or "high").strip().lower()
        feedback_id = self._feedback_id(feedback_text, source, category)

        recent = self._read_feedback(limit=800)
        if any(str(item.get("id", "")) == feedback_id for item in recent):
            self._log_event(
                "feedback_duplicate_skipped",
                {"feedback_id": feedback_id, "source": source, "category": category},
            )
            return feedback_id

        entry = {
            "id": feedback_id,
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "category": category,
            "severity": severity,
            "text": feedback_text[:1200],
        }
        try:
            with open(self.feedback_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

        self._log_event(
            "feedback_recorded",
            {
                "feedback_id": feedback_id,
                "source": source,
                "category": category,
                "severity": severity,
            },
        )

        if add_directive:
            title = f"User Guardrail ({category})"
            directive_text = (
                f"Do not repeat this user-reported issue: {feedback_text[:420]}. "
                f"Validate fix end-to-end before reporting completion."
            )
            self.add_directive(
                title=title,
                text=directive_text,
                priority=int(directive_priority),
                immutable=False,
            )
        return feedback_id

    def get_feedback_snapshot(self, limit: int = 20) -> Dict[str, Any]:
        rows = self._read_feedback(limit=limit)
        by_category: Dict[str, int] = {}
        for row in rows:
            category = str(row.get("category", "general")).lower()
            by_category[category] = by_category.get(category, 0) + 1
        return {
            "feedback_count": len(rows),
            "by_category": by_category,
            "recent_feedback": rows[-max(1, limit):],
            "path": str(self.feedback_path),
        }

    def ensure_core_directives(self) -> None:
        """Persist core directives so the system never forgets user priorities."""
        core_directives = [
            {
                "id": "core_problem_first",
                "priority": 100,
                "title": "Problem First",
                "text": "Always identify and report the most important active problems first.",
                "immutable": True,
            },
            {
                "id": "core_daily_learning",
                "priority": 95,
                "title": "Learn Every Day",
                "text": "Continuously learn from daily R&D failures and prevent recurrence.",
                "immutable": True,
            },
            {
                "id": "core_improvement_execution",
                "priority": 90,
                "title": "Improve Continuously",
                "text": "Translate learning into concrete, measurable system improvements.",
                "immutable": True,
            },
            {
                "id": "user_focus_directive",
                "priority": 110,
                "title": "User Focus",
                "text": (
                    "Prioritize what the user cares about most: key problems, what was learned, "
                    "and how the system improved."
                ),
                "immutable": True,
            },
            {
                "id": "core_no_complacency",
                "priority": 108,
                "title": "No Complacency",
                "text": "Never settle for the current baseline; continuously challenge assumptions and raise quality.",
                "immutable": True,
            },
            {
                "id": "core_mandatory_self_check",
                "priority": 107,
                "title": "Mandatory Self-Check",
                "text": "Before finalizing important outputs, run explicit self-check for errors, blind spots, and regressions.",
                "immutable": True,
            },
            {
                "id": "core_concurrency_safety",
                "priority": 107,
                "title": "Concurrency Safety",
                "text": (
                    "When multiple loops, agents, or processes run in parallel, prioritize lock-safe operations, "
                    "avoid conflicting writes, and prevent deadlock/stuck states."
                ),
                "immutable": True,
            },
            {
                "id": "core_frontier_updates",
                "priority": 106,
                "title": "Frontier Updates",
                "text": "Continuously seek and integrate new techniques, models, and methods to break existing limits.",
                "immutable": True,
            },
            {
                "id": "core_solution_options",
                "priority": 105,
                "title": "Solution Options",
                "text": (
                    "After each completed analysis or implementation, provide multiple feasible solution options "
                    "and clearly recommend the best next move."
                ),
                "immutable": True,
            },
            {
                "id": "core_portfolio_pivot",
                "priority": 104,
                "title": "Portfolio Pivot",
                "text": (
                    "Track the global roadmap; when one area reaches diminishing returns, pivot to the next high-impact area "
                    "to maintain continuous end-to-end improvement."
                ),
                "immutable": True,
            },
        ]

        with self._lock:
            existing = {d.get("id") for d in self._state.get("directives", []) if isinstance(d, dict)}
            changed = False
            for directive in core_directives:
                if directive["id"] in existing:
                    continue
                payload = dict(directive)
                payload["created_at"] = datetime.now().isoformat()
                self._state["directives"].append(payload)
                changed = True
            if changed:
                self._state["directives"] = sorted(
                    self._state.get("directives", []),
                    key=lambda x: int(x.get("priority", 0)),
                    reverse=True,
                )
                self._save(self._state)
                self._log_event(
                    "core_directives_updated",
                    {"directives_count": len(self._state.get("directives", []))},
                )

    def add_directive(self, title: str, text: str, priority: int = 80, immutable: bool = False) -> str:
        """Add a custom directive to prompt system state."""
        with self._lock:
            directive_id = f"custom_{self._stable_id(title + '|' + text)}"
            for directive in self._state.get("directives", []):
                if directive.get("id") == directive_id:
                    return directive_id

            custom_count = len([d for d in self._state.get("directives", []) if str(d.get("id", "")).startswith("custom_")])
            if custom_count >= self.max_custom_directives:
                # Keep space by dropping oldest non-immutable custom directive.
                customs = [
                    d for d in self._state.get("directives", [])
                    if str(d.get("id", "")).startswith("custom_") and not d.get("immutable", False)
                ]
                customs.sort(key=lambda d: str(d.get("created_at", "")))
                if customs:
                    drop_id = customs[0].get("id")
                    self._state["directives"] = [d for d in self._state.get("directives", []) if d.get("id") != drop_id]

            self._state["directives"].append(
                {
                    "id": directive_id,
                    "priority": int(priority),
                    "title": title[:120],
                    "text": text[:600],
                    "immutable": bool(immutable),
                    "created_at": datetime.now().isoformat(),
                }
            )
            self._state["directives"] = sorted(
                self._state.get("directives", []),
                key=lambda x: int(x.get("priority", 0)),
                reverse=True,
            )
            self._save(self._state)
            self._log_event(
                "custom_directive_added",
                {"directive_id": directive_id, "title": title[:120], "priority": int(priority)},
            )
            return directive_id

    def _render_directive_block(self, agent_name: str, role: str, importance: str = "normal") -> str:
        importance = str(importance or "normal").strip().lower()
        compact_mode = self.low_importance_compact and importance in {"low", "normal"}
        directive_limit = self.max_injected_directives
        if compact_mode:
            directive_limit = min(directive_limit, self.low_importance_injected_directives)

        directives = sorted(
            self._state.get("directives", []),
            key=lambda x: int(x.get("priority", 0)),
            reverse=True,
        )[:max(1, directive_limit)]
        lines = [PROMPT_SENTINEL, "Global non-negotiable directives:", f"Agent: {agent_name} ({role})"]
        for idx, directive in enumerate(directives, start=1):
            raw_text = str(directive.get("text", ""))
            directive_text = raw_text[:220] if compact_mode else raw_text
            lines.append(f"{idx}. {directive.get('title', 'Directive')}: {directive_text}")
        profile_lines: List[str] = []
        include_profile = (not compact_mode) or self.include_profile_for_low_importance
        if include_profile:
            try:
                try:
                    from src.memory.user_profile import render_user_profile_block
                except Exception:
                    from memory.user_profile import render_user_profile_block  # type: ignore
                profile_lines = render_user_profile_block(max_items=6) or []
            except Exception:
                profile_lines = []
        if profile_lines:
            lines.append("User working agreement (live profile):")
            lines.extend(profile_lines)
        if not compact_mode or importance in {"high", "critical"}:
            lines.append(
                "Reporting requirement: for important outputs, include (a) key problems, "
                "(b) lessons learned, (c) improvements made and next actions."
            )
        return "\n".join(lines)

    def inject_messages(
        self,
        messages: List[Dict[str, Any]],
        agent_name: str,
        role: str,
        importance: str = "normal",
    ) -> List[Dict[str, Any]]:
        """Inject global directives into message list via system prompt."""
        if not self.enabled:
            return list(messages or [])

        original = list(messages or [])
        directive_block = self._render_directive_block(
            agent_name=agent_name,
            role=role,
            importance=importance,
        )

        if not original:
            return [{"role": "system", "content": directive_block}]

        # Avoid duplicate injection.
        for msg in original:
            if msg.get("role") != "system":
                continue
            content = str(msg.get("content", ""))
            if PROMPT_SENTINEL in content:
                self._log_event(
                    "inject_skipped_already_present",
                    {"agent": agent_name, "role": role},
                )
                return original

        first = dict(original[0])
        if first.get("role") == "system":
            first_content = str(first.get("content", "")).strip()
            first["content"] = f"{directive_block}\n\n{first_content}".strip()
            self._log_event(
                "inject_prefixed_system",
                {"agent": agent_name, "role": role, "message_count": len(original)},
            )
            return [first] + original[1:]

        self._log_event(
            "inject_added_system",
            {"agent": agent_name, "role": role, "message_count": len(original)},
        )
        return [{"role": "system", "content": directive_block}] + original

    def inject_text_prompt(self, prompt: str, agent_name: str, role: str, importance: str = "normal") -> str:
        """Inject directive block into plain text prompt (for vision/single-text tasks)."""
        if not self.enabled:
            return prompt
        prompt = str(prompt or "")
        if PROMPT_SENTINEL in prompt:
            return prompt
        directive_block = self._render_directive_block(
            agent_name=agent_name,
            role=role,
            importance=importance,
        )
        self._log_event(
            "inject_text_prompt",
            {"agent": agent_name, "role": role, "prompt_chars": len(prompt)},
        )
        return f"{directive_block}\n\nTask prompt:\n{prompt}"

    def get_snapshot(self, limit: int = 8) -> Dict[str, Any]:
        directives = sorted(
            self._state.get("directives", []),
            key=lambda x: int(x.get("priority", 0)),
            reverse=True,
        )[:limit]
        feedback_stats = self.get_feedback_snapshot(limit=10)
        return {
            "enabled": self.enabled,
            "path": str(self.state_path),
            "tracklog_enabled": self.tracklog_enabled,
            "events_path": str(self._events_path()),
            "events_today": len(self._read_today_events(limit=5000)),
            "directives_count": len(self._state.get("directives", [])),
            "feedback_count": feedback_stats.get("feedback_count", 0),
            "feedback_path": feedback_stats.get("path"),
            "top_directives": directives,
        }


_prompt_system = None


def get_prompt_system() -> PromptSystem:
    global _prompt_system
    if _prompt_system is None:
        _prompt_system = PromptSystem()
    return _prompt_system
