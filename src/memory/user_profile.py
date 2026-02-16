"""
User Profile Memory
Stores how the system should work with the user (workflow, autonomy, preferences).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_LOCK = threading.RLock()

MAX_AGREEMENT_ITEMS = 12
MAX_ITEM_LEN = 260


def _project_root() -> Path:
    try:
        return Path(__file__).parent.parent.parent
    except Exception:
        return Path.cwd()


def _profile_path() -> Path:
    base = _project_root() / "data" / "memory"
    base.mkdir(parents=True, exist_ok=True)
    return base / "user_profile.json"


def _default_profile() -> Dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "version": 1,
        "updated_at": now,
        "project_name": "Nexus",
        "communication": {
            "language": "vi",
            "tone": "ngắn gọn, thực dụng",
        },
        "autonomy": {
            "level": "full",
            "ask_only_critical": True,
        },
        "working_agreement": [
            "Ưu tiên tự động hóa end-to-end; tránh hỏi lại, chỉ hỏi khi rủi ro rất cao.",
            "Hệ thống phải tự vận hành liên tục; Monitor/Guardian luôn active.",
            "Dashboard là nơi theo dõi chính; log mới nhất ở trên, phản hồi phải rõ ràng.",
            "Luôn kiểm chứng bằng test/verify trước khi báo hoàn thành.",
            "Ghi lại tiến độ và phản hồi người dùng để phiên sau tiếp tục được ngay.",
        ],
    }


def _normalize_agreement(items: List[str]) -> List[str]:
    clean: List[str] = []
    for raw in items:
        text = str(raw or "").strip()
        if not text:
            continue
        # Strip common bullet prefixes
        for prefix in ("- ", "• ", "* ", "– ", "— ", "•", "-", "*"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        if not text:
            continue
        clean.append(text[:MAX_ITEM_LEN])
    if len(clean) > MAX_AGREEMENT_ITEMS:
        clean = clean[:MAX_AGREEMENT_ITEMS]
    return clean


def _load_profile() -> Dict[str, Any]:
    path = _profile_path()
    if not path.exists():
        profile = _default_profile()
        _save_profile(profile)
        return profile
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid profile payload")
        if "working_agreement" in data:
            data["working_agreement"] = _normalize_agreement(data.get("working_agreement") or [])
        return data
    except Exception:
        profile = _default_profile()
        _save_profile(profile)
        return profile


def _save_profile(profile: Dict[str, Any]) -> None:
    path = _profile_path()
    tmp_path = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def get_user_profile() -> Dict[str, Any]:
    with _LOCK:
        return _load_profile()


def update_user_profile(update: Dict[str, Any]) -> Dict[str, Any]:
    update = update or {}
    with _LOCK:
        profile = _load_profile()

        # Parse working agreement from text input if provided
        agreement_text = update.pop("working_agreement_text", None)
        if agreement_text is not None:
            lines = [line.strip() for line in str(agreement_text).splitlines()]
            update["working_agreement"] = _normalize_agreement(lines)

        if "working_agreement" in update:
            update["working_agreement"] = _normalize_agreement(update.get("working_agreement") or [])

        def _merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
            for key, value in src.items():
                if isinstance(value, dict) and isinstance(dst.get(key), dict):
                    _merge(dst[key], value)
                else:
                    dst[key] = value

        _merge(profile, update)
        profile["updated_at"] = datetime.now().isoformat()
        _save_profile(profile)
        return profile


def render_user_profile_block(max_items: int = 6) -> List[str]:
    profile = get_user_profile()
    lines: List[str] = []

    project_name = str(profile.get("project_name", "")).strip()
    if project_name:
        lines.append(f"Project name: {project_name}")

    autonomy = profile.get("autonomy") or {}
    level = str(autonomy.get("level", "")).strip()
    if level:
        ask_only_critical = autonomy.get("ask_only_critical")
        if ask_only_critical is True:
            lines.append(f"Autonomy: {level} (ask only on critical risk)")
        else:
            lines.append(f"Autonomy: {level}")

    agreement = profile.get("working_agreement") or []
    agreement = _normalize_agreement(agreement)
    if agreement:
        lines.append("User working agreement:")
        for item in agreement[:max_items]:
            lines.append(f"- {item}")
        if len(agreement) > max_items:
            lines.append(f"- (+{len(agreement) - max_items} more)")

    return lines

