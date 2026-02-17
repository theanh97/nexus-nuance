"""Proposal engine v2 with evidence-based scoring."""

from datetime import datetime
from typing import Any, Dict, List, Optional
import hashlib
import os

from .storage_v2 import get_storage_v2


class ProposalEngineV2:
    def __init__(self):
        self.storage = get_storage_v2()
        self.create_threshold = float(os.getenv("PROPOSAL_V2_CREATE_THRESHOLD", "0.62"))
        self.auto_approve_threshold = float(os.getenv("PROPOSAL_V2_AUTO_APPROVE_THRESHOLD", "0.82"))
        self.enable_cafe = os.getenv("ENABLE_CAFE_LOOP", "true").strip().lower() == "true"
        self.cafe_allow_blocked = os.getenv("CAFE_ALLOW_BLOCKED_PROPOSALS", "false").strip().lower() == "true"

    def _clamp(self, value: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value

    def _priority(self, value: float, novelty: float, confidence: float, risk: float) -> float:
        raw = (0.40 * value) + (0.25 * novelty) + (0.20 * confidence) - (0.15 * risk)
        return max(0.0, min(1.0, round(raw, 4)))

    def _risk_level(self, risk: float) -> str:
        if risk >= 0.75:
            return "high"
        if risk >= 0.45:
            return "medium"
        return "low"

    def _signature(self, event: Dict[str, Any]) -> str:
        base = "|".join([
            str(event.get("event_type", "")),
            str(event.get("source", "")),
            str(event.get("content", ""))[:160],
        ]).lower()
        return hashlib.md5(base.encode("utf-8")).hexdigest()[:14]

    def _event_stream(self, event: Dict[str, Any]) -> str:
        stream = str(event.get("stream", "")).strip().lower()
        if stream in {"production", "non_production"}:
            return stream
        if bool(event.get("is_non_production", False)):
            return "non_production"
        source = str(event.get("source", "")).strip().lower()
        if source.startswith(("test_", "unit_", "manual_", "debug_", "demo_")):
            return "non_production"
        if source in {"unit_test", "manual_test", "manual_check", "manual_boost", "demo", "debug", "local_debug"}:
            return "non_production"
        return "production"

    def generate_from_events(
        self,
        events: List[Dict[str, Any]],
        limit: int = 20,
        include_non_production: bool = False,
    ) -> List[Dict[str, Any]]:
        data = self.storage.get_proposals_v2()
        proposals = data.get("proposals", []) if isinstance(data.get("proposals"), list) else []
        pending = data.get("pending", []) if isinstance(data.get("pending"), list) else []

        active_signatures = {
            str(p.get("signature"))
            for p in proposals
            if p.get("status") in {"pending_approval", "approved", "executed", "verified"}
        }

        created: List[Dict[str, Any]] = []
        for event in events:
            stream = self._event_stream(event)
            if stream == "non_production" and not include_non_production:
                continue
            value = self._clamp(event.get("value_score", 0.0) or 0.0)
            novelty = self._clamp(event.get("novelty_score", 0.0) or 0.0)
            confidence = self._clamp(event.get("confidence", value) or value)
            risk = self._clamp(event.get("risk_score", 0.0) or 0.0)

            cafe = event.get("cafe") if isinstance(event.get("cafe"), dict) else None
            if self.enable_cafe and cafe:
                if bool(cafe.get("blocked", False)) and not self.cafe_allow_blocked:
                    continue
                helpful = self._clamp(cafe.get("helpful", value))
                harmless = self._clamp(cafe.get("harmless", 1.0 - risk))
                cafe_conf = self._clamp(cafe.get("confidence", confidence))
                value = self._clamp((value * 0.7) + (helpful * 0.3))
                risk = self._clamp(max(risk, 1.0 - harmless))
                confidence = self._clamp((confidence + cafe_conf) / 2.0)
            priority = self._priority(value, novelty, confidence, risk)
            if priority < self.create_threshold:
                continue

            signature = self._signature(event)
            if signature in active_signatures:
                continue

            proposal_id = f"pv2_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{signature[:6]}"
            risk_level = self._risk_level(risk)
            auto_approve = priority >= self.auto_approve_threshold and risk_level in {"low", "medium"}
            proposal = {
                "id": proposal_id,
                "created_at": datetime.now().isoformat(),
                "approved_at": datetime.now().isoformat() if auto_approve else None,
                "origin_event_ids": [event.get("id")],
                "title": str(event.get("title") or event.get("event_type") or "Learning-driven improvement")[:180],
                "hypothesis": str(event.get("hypothesis") or f"Applying insight from {event.get('source', 'system')} improves outcomes."),
                "plan_steps": [
                    "Collect baseline metrics",
                    "Apply isolated safe change",
                    "Run verification checks",
                    "Keep or rollback based on evidence",
                ],
                "expected_impact": str(event.get("expected_impact") or "Incremental reliability/learning throughput improvement"),
                "risk_level": risk_level,
                "status": "approved" if auto_approve else "pending_approval",
                "confidence": round(confidence, 4),
                "priority": priority,
                "signature": signature,
                "metadata": {
                    "source": event.get("source"),
                    "event_type": event.get("event_type"),
                    "event_stream": stream,
                    "model": event.get("model"),
                    "auto_approved": auto_approve,
                    "cafe": {
                        "score": cafe.get("score") if isinstance(cafe, dict) else None,
                        "confidence": cafe.get("confidence") if isinstance(cafe, dict) else None,
                        "helpful": cafe.get("helpful") if isinstance(cafe, dict) else None,
                        "harmless": cafe.get("harmless") if isinstance(cafe, dict) else None,
                        "reliability": cafe.get("reliability") if isinstance(cafe, dict) else None,
                        "blocked": cafe.get("blocked") if isinstance(cafe, dict) else None,
                    },
                },
            }
            proposals.append(proposal)
            if proposal["status"] == "pending_approval":
                pending.append(proposal_id)
            created.append(proposal)
            active_signatures.add(signature)
            if len(created) >= max(1, limit):
                break

        data["proposals"] = proposals[-5000:]
        # keep unique order
        dedup_pending: List[str] = []
        seen = set()
        for pid in pending:
            if pid in seen:
                continue
            seen.add(pid)
            dedup_pending.append(pid)
        data["pending"] = dedup_pending
        self.storage.save_proposals_v2(data)
        return created

    def list_pending(self) -> List[Dict[str, Any]]:
        data = self.storage.get_proposals_v2()
        proposals = data.get("proposals", []) if isinstance(data.get("proposals"), list) else []
        return [p for p in proposals if p.get("status") in {"pending_approval", "approved"}]

    def auto_approve_safe(self, limit: int = 5, min_priority: Optional[float] = None) -> int:
        data = self.storage.get_proposals_v2()
        proposals = data.get("proposals", []) if isinstance(data.get("proposals"), list) else []
        approved_now = 0
        threshold = float(self.auto_approve_threshold if min_priority is None else min_priority)
        for proposal in proposals:
            if approved_now >= max(1, limit):
                break
            if proposal.get("status") != "pending_approval":
                continue
            priority = float(proposal.get("priority", 0.0) or 0.0)
            risk_level = str(proposal.get("risk_level", "high")).lower()
            if priority >= threshold and risk_level in {"low", "medium"}:
                proposal["status"] = "approved"
                proposal["approved_at"] = datetime.now().isoformat()
                approved_now += 1
        if approved_now > 0:
            # Keep pending list aligned with statuses.
            data["pending"] = [p.get("id") for p in proposals if p.get("status") == "pending_approval" and p.get("id")]
            data["proposals"] = proposals
            self.storage.save_proposals_v2(data)
        return approved_now

    def mark_status(self, proposal_id: str, status: str, **extra: Any) -> bool:
        data = self.storage.get_proposals_v2()
        proposals = data.get("proposals", []) if isinstance(data.get("proposals"), list) else []
        changed = False
        for p in proposals:
            if p.get("id") == proposal_id:
                p["status"] = status
                p.update(extra)
                changed = True
                break
        if not changed:
            return False
        if status not in {"pending_approval", "approved"}:
            data["pending"] = [pid for pid in data.get("pending", []) if pid != proposal_id]
        self.storage.save_proposals_v2(data)
        return True


_engine_v2: Optional[ProposalEngineV2] = None


def get_proposal_engine_v2() -> ProposalEngineV2:
    global _engine_v2
    if _engine_v2 is None:
        _engine_v2 = ProposalEngineV2()
    return _engine_v2


def generate_proposals_v2(limit: int = 20, include_non_production: bool = False) -> List[Dict[str, Any]]:
    events = get_storage_v2().list_learning_events(limit=200)
    return get_proposal_engine_v2().generate_from_events(
        events,
        limit=limit,
        include_non_production=include_non_production,
    )
