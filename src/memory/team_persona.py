"""
Team persona memory and lightweight self-learning for collaborator-specific behavior adaptation.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_STORE_LOCK = threading.RLock()
_STORE_SINGLETON: Optional["TeamPersonaStore"] = None


def _project_root() -> Path:
    try:
        return Path(__file__).parent.parent.parent
    except Exception:
        return Path.cwd()


def _default_state_path() -> Path:
    base = _project_root() / "data" / "memory"
    base.mkdir(parents=True, exist_ok=True)
    return base / "team_personas.json"


def _default_events_path() -> Path:
    base = _project_root() / "data" / "memory"
    base.mkdir(parents=True, exist_ok=True)
    return base / "team_persona_events.jsonl"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def normalize_member_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:80]


def _safe_text(value: Any, max_len: int = 500) -> str:
    text = str(value or "").strip()
    if max_len > 0:
        text = text[:max_len]
    return text


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any, max_items: int = 24, max_len: int = 120) -> List[str]:
    if not isinstance(value, list):
        return []
    rows: List[str] = []
    for item in value:
        text = _safe_text(item, max_len=max_len)
        if not text:
            continue
        rows.append(text)
    deduped = list(dict.fromkeys(rows))
    return deduped[:max_items]


def _default_member(member_id: str) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "member_id": member_id,
        "name": member_id,
        "role": "",
        "team": "",
        "timezone": "",
        "persona": {
            "tone": "balanced",
            "detail_level": "balanced",
            "pace": "balanced",
            "risk_posture": "balanced",
        },
        "work_style": {
            "planning_depth": "balanced",
            "review_strictness": "balanced",
            "autonomy_preference": "high",
        },
        "preferences": {
            "preferred_channels": ["chat"],
            "response_structure": "bullets",
        },
        "signals": {
            "urgent": 0,
            "direct": 0,
            "collaborative": 0,
            "detailed": 0,
            "autonomy": 0,
            "safety": 0,
        },
        "learning": {
            "interaction_count": 0,
            "avg_message_length": 0.0,
            "total_message_chars": 0,
            "last_interaction_at": None,
            "channels": {},
        },
        "derived_style": {
            "tone": "balanced",
            "detail_level": "balanced",
            "pace": "balanced",
            "autonomy_preference": "high",
            "risk_posture": "balanced",
        },
        "notes": "",
        "tags": [],
        "created_at": now,
        "updated_at": now,
    }


def _merge_nested(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge_nested(dst[key], value)
        else:
            dst[key] = value
    return dst


def _signal_from_text(message: str, intent: str = "") -> Dict[str, bool]:
    text = f"{message} {intent}".strip().lower()
    compact = re.sub(r"\s+", " ", text)

    urgent = bool(re.search(r"\b(now|urgent|asap|ngay|gấp|liền|lập tức)\b", compact))
    direct = bool(re.search(r"\b(chỉ|just|only|do this|run this|thẳng)\b", compact))
    collaborative = bool(re.search(r"\b(we|team|cùng|phối hợp|trao đổi|thảo luận)\b", compact))
    detailed = len(message) >= 180 or bool(re.search(r"\b(detail|chi tiết|step by step|từng bước)\b", compact))
    autonomy = bool(re.search(r"\b(tự động|autonomous|auto|không hỏi|không cần hỏi)\b", compact))
    safety = bool(re.search(r"\b(cẩn thận|safe|an toàn|review|xác nhận|kiểm tra)\b", compact))
    return {
        "urgent": urgent,
        "direct": direct,
        "collaborative": collaborative,
        "detailed": detailed,
        "autonomy": autonomy,
        "safety": safety,
    }


def _derive_style(member: Dict[str, Any]) -> Dict[str, str]:
    learning = _safe_dict(member.get("learning"))
    signals = _safe_dict(member.get("signals"))
    interactions = max(1, int(learning.get("interaction_count") or 0))
    avg_len = float(learning.get("avg_message_length") or 0.0)

    direct_ratio = float(signals.get("direct", 0) or 0) / interactions
    collaborative_ratio = float(signals.get("collaborative", 0) or 0) / interactions
    urgent_ratio = float(signals.get("urgent", 0) or 0) / interactions
    detail_ratio = float(signals.get("detailed", 0) or 0) / interactions
    autonomy_ratio = float(signals.get("autonomy", 0) or 0) / interactions
    safety_ratio = float(signals.get("safety", 0) or 0) / interactions

    tone = "balanced"
    if direct_ratio >= 0.45 and direct_ratio >= collaborative_ratio:
        tone = "direct"
    elif collaborative_ratio >= 0.4:
        tone = "collaborative"

    detail_level = "balanced"
    if detail_ratio >= 0.45 or avg_len >= 220:
        detail_level = "high"
    elif avg_len <= 90 and detail_ratio <= 0.25:
        detail_level = "compact"

    pace = "balanced"
    if urgent_ratio >= 0.35:
        pace = "fast"
    elif urgent_ratio <= 0.1:
        pace = "steady"

    autonomy_preference = "high" if autonomy_ratio >= safety_ratio else "balanced"
    if safety_ratio - autonomy_ratio >= 0.2:
        autonomy_preference = "guarded"

    risk_posture = "balanced"
    if safety_ratio >= 0.35:
        risk_posture = "careful"
    elif autonomy_ratio >= 0.45 and safety_ratio <= 0.2:
        risk_posture = "aggressive"

    return {
        "tone": tone,
        "detail_level": detail_level,
        "pace": pace,
        "autonomy_preference": autonomy_preference,
        "risk_posture": risk_posture,
    }


class TeamPersonaStore:
    def __init__(self, state_path: Optional[Path] = None, events_path: Optional[Path] = None):
        self.state_path = Path(state_path) if state_path else _default_state_path()
        self.events_path = Path(events_path) if events_path else _default_events_path()
        self._lock = threading.RLock()
        self._state = self._load_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": _now_iso(),
            "members": {},
        }

    def _normalize_member(self, member_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        existing = _default_member(member_id)
        data = dict(raw or {})
        data["member_id"] = member_id
        data["name"] = _safe_text(data.get("name") or member_id, 120) or member_id
        data["role"] = _safe_text(data.get("role"), 120)
        data["team"] = _safe_text(data.get("team"), 120)
        data["timezone"] = _safe_text(data.get("timezone"), 120)
        data["notes"] = _safe_text(data.get("notes"), 600)
        data["tags"] = _safe_list(data.get("tags"), max_items=32, max_len=80)
        data["persona"] = _safe_dict(data.get("persona"))
        data["work_style"] = _safe_dict(data.get("work_style"))
        data["preferences"] = _safe_dict(data.get("preferences"))
        data["signals"] = _safe_dict(data.get("signals"))
        data["learning"] = _safe_dict(data.get("learning"))
        data["derived_style"] = _safe_dict(data.get("derived_style"))

        normalized = _merge_nested(existing, data)
        normalized["member_id"] = member_id
        normalized["updated_at"] = _safe_text(normalized.get("updated_at"), 80) or _now_iso()
        normalized["created_at"] = _safe_text(normalized.get("created_at"), 80) or _now_iso()
        return normalized

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            state = self._default_state()
            self._save_state(state)
            return state
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("invalid team persona state")
        except Exception:
            state = self._default_state()
            self._save_state(state)
            return state

        members = raw.get("members") if isinstance(raw.get("members"), dict) else {}
        normalized_members: Dict[str, Dict[str, Any]] = {}
        for raw_member_id, payload in members.items():
            member_id = normalize_member_id(raw_member_id)
            if not member_id or not isinstance(payload, dict):
                continue
            normalized_members[member_id] = self._normalize_member(member_id, payload)
        return {
            "version": max(1, int(raw.get("version") or 1)),
            "updated_at": _safe_text(raw.get("updated_at"), 80) or _now_iso(),
            "members": normalized_members,
        }

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f"{self.state_path.name}.{threading.get_ident()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)
        tmp.replace(self.state_path)

    def _append_event(self, row: Dict[str, Any]) -> None:
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        except Exception:
            return

    def _commit(self) -> None:
        self._state["updated_at"] = _now_iso()
        self._save_state(self._state)

    def list_members(self, limit: int = 120) -> List[Dict[str, Any]]:
        cap = max(1, min(500, int(limit)))
        with self._lock:
            rows = list(self._state.get("members", {}).values())
            rows.sort(key=lambda row: str(row.get("updated_at", "")), reverse=True)
            return _json_copy(rows[:cap])

    def get_member(self, member_id: Any) -> Optional[Dict[str, Any]]:
        key = normalize_member_id(member_id)
        if not key:
            return None
        with self._lock:
            row = self._state.get("members", {}).get(key)
            return _json_copy(row) if isinstance(row, dict) else None

    def upsert_member(self, member_id: Any, patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        key = normalize_member_id(member_id)
        if not key:
            raise ValueError("member_id is required")
        payload = patch if isinstance(patch, dict) else {}

        with self._lock:
            members = self._state.setdefault("members", {})
            current = members.get(key) if isinstance(members.get(key), dict) else _default_member(key)
            current = self._normalize_member(key, current)

            writable = {
                "name": _safe_text(payload.get("name"), 120),
                "role": _safe_text(payload.get("role"), 120),
                "team": _safe_text(payload.get("team"), 120),
                "timezone": _safe_text(payload.get("timezone"), 120),
                "notes": _safe_text(payload.get("notes"), 600),
            }
            for field, value in writable.items():
                if value:
                    current[field] = value

            for dict_field in ("persona", "work_style", "preferences"):
                value = payload.get(dict_field)
                if isinstance(value, dict):
                    base = _safe_dict(current.get(dict_field))
                    _merge_nested(base, value)
                    current[dict_field] = base

            tags = payload.get("tags")
            if isinstance(tags, list):
                current["tags"] = _safe_list(tags, max_items=32, max_len=80)

            current["updated_at"] = _now_iso()
            members[key] = self._normalize_member(key, current)
            self._commit()
            row = _json_copy(members[key])

        self._append_event(
            {
                "timestamp": _now_iso(),
                "action": "member_upserted",
                "member_id": key,
                "summary": {
                    "name": row.get("name"),
                    "role": row.get("role"),
                    "team": row.get("team"),
                },
            }
        )
        return row

    def record_interaction(
        self,
        member_id: Any,
        message: str,
        channel: str = "chat",
        intent: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        key = normalize_member_id(member_id)
        if not key:
            raise ValueError("member_id is required")
        msg = _safe_text(message, 1200)
        if not msg:
            raise ValueError("message is required")
        channel_name = _safe_text(channel, 40).lower() or "chat"
        intent_name = _safe_text(intent, 120)
        safe_context = context if isinstance(context, dict) else {}

        with self._lock:
            members = self._state.setdefault("members", {})
            current = members.get(key) if isinstance(members.get(key), dict) else _default_member(key)
            current = self._normalize_member(key, current)

            signals = _safe_dict(current.get("signals"))
            learning = _safe_dict(current.get("learning"))
            detected = _signal_from_text(msg, intent=intent_name)
            for signal_name, enabled in detected.items():
                if enabled:
                    signals[signal_name] = int(signals.get(signal_name, 0) or 0) + 1
            current["signals"] = signals

            count = int(learning.get("interaction_count", 0) or 0) + 1
            total_chars = int(learning.get("total_message_chars", 0) or 0) + len(msg)
            learning["interaction_count"] = count
            learning["total_message_chars"] = total_chars
            learning["avg_message_length"] = round(total_chars / max(1, count), 2)
            learning["last_interaction_at"] = _now_iso()
            channels = _safe_dict(learning.get("channels"))
            channels[channel_name] = int(channels.get(channel_name, 0) or 0) + 1
            learning["channels"] = channels
            current["learning"] = learning

            current["derived_style"] = _derive_style(current)
            current["updated_at"] = _now_iso()
            members[key] = self._normalize_member(key, current)
            self._commit()
            updated = _json_copy(members[key])

        event = {
            "timestamp": _now_iso(),
            "action": "interaction_recorded",
            "member_id": key,
            "channel": channel_name,
            "intent": intent_name,
            "signals": detected,
            "message_preview": _safe_text(msg, 180),
            "context": {
                "instance_id": _safe_text(safe_context.get("instance_id"), 80),
                "task_id": _safe_text(safe_context.get("task_id"), 80),
            },
        }
        self._append_event(event)
        return {
            "member": updated,
            "signals": detected,
            "derived_style": updated.get("derived_style", {}),
            "event": event,
        }

    def list_interactions(self, member_id: Any = "", limit: int = 120) -> List[Dict[str, Any]]:
        cap = max(1, min(600, int(limit)))
        target = normalize_member_id(member_id)
        rows: List[Dict[str, Any]] = []
        if not self.events_path.exists():
            return rows
        try:
            with open(self.events_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    text = str(line or "").strip()
                    if not text:
                        continue
                    try:
                        item = json.loads(text)
                    except Exception:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if target and normalize_member_id(item.get("member_id")) != target:
                        continue
                    rows.append(item)
        except Exception:
            return []
        rows.sort(key=lambda row: str(row.get("timestamp", "")), reverse=True)
        return rows[:cap]

    def recommend_adaptation(self, member_id: Any, intent: str = "") -> Dict[str, Any]:
        key = normalize_member_id(member_id)
        if not key:
            return {
                "member_id": "",
                "known_member": False,
                "communication": {
                    "tone": "balanced",
                    "detail_level": "balanced",
                    "response_structure": "bullets",
                },
                "orchestration": {
                    "autonomy_mode": "balanced",
                    "risk_policy": "balanced",
                    "escalate_when": "critical_only",
                },
                "hints": ["Missing member_id, using default adaptation profile."],
            }

        member = self.get_member(key)
        if not member:
            return {
                "member_id": key,
                "known_member": False,
                "communication": {
                    "tone": "balanced",
                    "detail_level": "balanced",
                    "response_structure": "bullets",
                },
                "orchestration": {
                    "autonomy_mode": "balanced",
                    "risk_policy": "balanced",
                    "escalate_when": "critical_only",
                },
                "hints": [f"Member {key} is not configured yet."],
            }

        style = _safe_dict(member.get("derived_style"))
        persona = _safe_dict(member.get("persona"))
        work_style = _safe_dict(member.get("work_style"))
        tone = str(persona.get("tone", style.get("tone", "balanced")))
        detail = str(persona.get("detail_level", style.get("detail_level", "balanced")))
        autonomy = str(work_style.get("autonomy_preference", style.get("autonomy_preference", "balanced")))
        risk = str(persona.get("risk_posture", style.get("risk_posture", "balanced")))
        pace = str(persona.get("pace", style.get("pace", "balanced")))
        response_structure = str(
            _safe_dict(member.get("preferences")).get("response_structure", "bullets")
        ) or "bullets"

        autonomy_mode = "balanced"
        if autonomy == "high" and risk in {"balanced", "aggressive"}:
            autonomy_mode = "full_auto"
        elif autonomy == "guarded" or risk == "careful":
            autonomy_mode = "safe"

        hints = [
            f"Tone={tone}, detail={detail}, pace={pace}.",
            f"Autonomy={autonomy_mode}, risk_policy={risk}.",
        ]
        if intent:
            hints.append(f"Intent context: {intent[:90]}")

        return {
            "member_id": key,
            "known_member": True,
            "communication": {
                "tone": tone,
                "detail_level": detail,
                "pace": pace,
                "response_structure": response_structure,
            },
            "orchestration": {
                "autonomy_mode": autonomy_mode,
                "risk_policy": risk,
                "escalate_when": "high_or_critical" if autonomy_mode == "safe" else "critical_only",
            },
            "hints": hints,
            "member": member,
        }

    def summary(self, limit: int = 20) -> Dict[str, Any]:
        members = self.list_members(limit=max(1, limit))
        total_interactions = sum(
            int(_safe_dict(row.get("learning")).get("interaction_count", 0) or 0)
            for row in members
            if isinstance(row, dict)
        )
        return {
            "members": len(members),
            "total_interactions": total_interactions,
            "updated_at": _safe_text(self._state.get("updated_at"), 80) or _now_iso(),
            "top_members": [
                {
                    "member_id": row.get("member_id"),
                    "name": row.get("name"),
                    "role": row.get("role"),
                    "interactions": int(_safe_dict(row.get("learning")).get("interaction_count", 0) or 0),
                    "derived_style": _safe_dict(row.get("derived_style")),
                }
                for row in members[:5]
            ],
        }


def get_team_persona_store() -> TeamPersonaStore:
    global _STORE_SINGLETON
    with _STORE_LOCK:
        if _STORE_SINGLETON is None:
            _STORE_SINGLETON = TeamPersonaStore()
        return _STORE_SINGLETON
