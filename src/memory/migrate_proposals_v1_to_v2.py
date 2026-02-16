"""One-shot migration helper from v1 scanner proposals to v2 proposal store."""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from .storage_v2 import get_storage_v2


def migrate_once() -> Dict[str, Any]:
    storage = get_storage_v2()

    try:
        project_root = Path(__file__).parent.parent.parent
    except Exception:
        project_root = Path.cwd()

    v1_path = project_root / "data" / "memory" / "improvement_proposals.json"
    if not v1_path.exists():
        return {"ok": True, "migrated": 0, "reason": "v1_not_found"}

    with open(v1_path, "r", encoding="utf-8") as f:
        v1 = json.load(f)

    proposals_v1 = v1.get("proposals", []) if isinstance(v1.get("proposals"), list) else []
    v2_data = storage.get_proposals_v2()
    v2_rows = v2_data.get("proposals", []) if isinstance(v2_data.get("proposals"), list) else []
    existing_ids = {str(x.get("id")) for x in v2_rows}
    migrated = 0

    for p in proposals_v1:
        old_id = str(p.get("id") or "")
        if not old_id:
            continue
        new_id = f"pv2_mig_{old_id}"
        if new_id in existing_ids:
            continue
        source_item = p.get("source_item", {}) if isinstance(p.get("source_item"), dict) else {}
        score = float(source_item.get("score", 5.0) or 5.0)
        old_status = str(p.get("status", "pending_approval")).strip().lower()
        mapped_status = old_status if old_status in {
            "pending_approval", "approved", "executed", "verified", "rejected", "applied"
        } else "pending_approval"
        if mapped_status == "applied":
            mapped_status = "verified"
        priority = max(0.0, min(1.0, score / 8.0))
        v2_rows.append({
            "id": new_id,
            "created_at": p.get("created", datetime.now().isoformat()),
            "origin_event_ids": [f"mig_{old_id}"],
            "title": p.get("title", "Migrated proposal"),
            "hypothesis": p.get("description", "Migrated from v1 proposals"),
            "plan_steps": [
                "Collect baseline metrics",
                "Apply isolated safe change",
                "Run verification checks",
                "Keep or rollback based on evidence",
            ],
            "expected_impact": p.get("expected_impact", f"score={score:.2f}"),
            "risk_level": "medium" if score >= 6.0 else "low",
            "status": mapped_status,
            "confidence": 0.85,
            "priority": priority,
            "signature": f"mig_{old_id}",
            "metadata": {
                "source": source_item.get("source", "migration"),
                "event_type": "migrated_proposal",
                "migrated_from_v1_id": old_id,
            },
        })
        existing_ids.add(new_id)
        migrated += 1

    v2_data["proposals"] = v2_rows[-5000:]
    v2_data["pending"] = [
        row.get("id") for row in v2_rows
        if row.get("status") == "pending_approval" and row.get("id")
    ]
    storage.save_proposals_v2(v2_data)
    storage.save_policy_state({
        **storage.get_policy_state(),
        "migration": {
            "v1_to_v2_last_run": datetime.now().isoformat(),
            "migrated_count": migrated,
        },
    })
    return {"ok": True, "migrated": migrated}


if __name__ == "__main__":
    print(migrate_once())
