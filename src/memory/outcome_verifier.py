"""Outcome verification for executed experiments."""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import os

from .proposal_engine import get_proposal_engine_v2
from .self_debugger import health_check
from .storage_v2 import get_storage_v2


class OutcomeVerifier:
    def __init__(self):
        self.storage = get_storage_v2()
        self.proposals = get_proposal_engine_v2()
        self.holdout_enabled = os.getenv("VERIFICATION_HOLDOUT_ENABLED", "true").strip().lower() == "true"
        self.holdout_seconds = int(os.getenv("VERIFICATION_HOLDOUT_SECONDS", "180"))

    def _verdict(self, before: Dict[str, Any], after: Dict[str, Any], run_artifacts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        b_health = float(before.get("health_score", 0) or 0)
        a_health = float(after.get("health_score", 0) or 0)
        b_issues = int(before.get("open_issues", 0) or 0)
        a_issues = int(after.get("open_issues", 0) or 0)
        b_stats = before.get("recent_stats", {}) if isinstance(before.get("recent_stats"), dict) else {}
        a_stats = after.get("recent_stats", {}) if isinstance(after.get("recent_stats"), dict) else {}
        b_errors = int(b_stats.get("total_errors", 0) or 0)
        a_errors = int(a_stats.get("total_errors", 0) or 0)
        b_duration = float(b_stats.get("avg_duration_ms", 0) or 0)
        a_duration = float(a_stats.get("avg_duration_ms", 0) or 0)
        b_success = float(b_stats.get("success_rate", 0.0) or 0.0)
        a_success = float(a_stats.get("success_rate", 0.0) or 0.0)

        delta_health = round(a_health - b_health, 4)
        delta_issues = a_issues - b_issues
        delta_errors = a_errors - b_errors
        delta_duration_ms = round(a_duration - b_duration, 2)
        delta_success_rate = round(a_success - b_success, 4)
        execution_status = str((run_artifacts or {}).get("result", "") or "")
        execution_success = bool((run_artifacts or {}).get("execution_success", True))
        run_duration_ms = int((run_artifacts or {}).get("duration_ms", 0) or 0)

        positives = 0
        negatives = 0
        reasons = []

        if delta_health >= 1.0:
            positives += 1
            reasons.append("health_score_improved")
        elif delta_health <= -1.0:
            negatives += 1
            reasons.append("health_score_declined")

        if delta_issues <= -1:
            positives += 1
            reasons.append("open_issues_reduced")
        elif delta_issues >= 1:
            negatives += 1
            reasons.append("open_issues_increased")

        if delta_errors <= -1:
            positives += 1
            reasons.append("errors_reduced")
        elif delta_errors >= 1:
            negatives += 1
            reasons.append("errors_increased")

        if delta_duration_ms <= -100:
            positives += 1
            reasons.append("latency_improved")
        elif delta_duration_ms >= 200:
            negatives += 1
            reasons.append("latency_regressed")

        if delta_success_rate >= 0.02:
            positives += 1
            reasons.append("success_rate_improved")
        elif delta_success_rate <= -0.02:
            negatives += 1
            reasons.append("success_rate_regressed")

        critical_loss = (
            delta_health <= -2.0
            or delta_issues >= 1
            or delta_errors >= 2
            or not execution_success
        )

        if critical_loss or negatives >= 2:
            verdict = "loss"
            confidence = 0.85 if critical_loss else 0.75
        elif positives >= 1 and negatives == 0:
            # Treat clear single-signal gains as win (lower confidence) to reduce inconclusive noise.
            verdict = "win"
            confidence = 0.66 if positives == 1 else 0.8
        else:
            verdict = "inconclusive"
            confidence = 0.55 if positives > 0 else 0.5

        return {
            "verdict": verdict,
            "confidence": confidence,
            "signals": {
                "positives": positives,
                "negatives": negatives,
                "reasons": reasons,
                "execution_success": execution_success,
                "execution_status": execution_status,
                "run_duration_ms": run_duration_ms,
            },
            "delta": {
                "health_score": delta_health,
                "open_issues": delta_issues,
                "total_errors": delta_errors,
                "avg_duration_ms": delta_duration_ms,
                "success_rate": delta_success_rate,
            },
        }

    def verify_experiment(self, experiment_id: str) -> Dict[str, Any]:
        runs = self.storage.get_experiment_runs(limit=1000)
        target = None
        for run in runs:
            if run.get("id") == experiment_id:
                target = run
                break

        if not target:
            return {"ok": False, "error": "run_not_found", "experiment_id": experiment_id}

        run_artifacts = target.get("artifacts", {}) if isinstance(target.get("artifacts"), dict) else {}
        finished_at = target.get("finished_at") or run_artifacts.get("finished_at")
        finished_at_dt = None
        if finished_at:
            try:
                finished_at_dt = datetime.fromisoformat(str(finished_at))
            except Exception:
                finished_at_dt = None

        before = ((target.get("artifacts") or {}).get("baseline_health") or {})
        after = health_check()
        existing_verification = target.get("verification", {}) if isinstance(target.get("verification"), dict) else {}
        previous_attempts = int(existing_verification.get("attempts", 0) or 0)
        proposals = self.storage.get_proposals_v2().get("proposals", [])
        verified_count = len([p for p in proposals if isinstance(p, dict) and p.get("status") == "verified"])
        executed_count = len([p for p in proposals if isinstance(p, dict) and p.get("status") in {"executed", "verified"}])
        before_throughput = {
            "executed_or_verified": int(((target.get("artifacts") or {}).get("throughput_before", {}) or {}).get("executed_or_verified", 0)),
            "verified": int(((target.get("artifacts") or {}).get("throughput_before", {}) or {}).get("verified", 0)),
        }
        after_throughput = {
            "executed_or_verified": executed_count,
            "verified": verified_count,
        }
        if self.holdout_enabled and finished_at_dt:
            now = datetime.now()
            holdout_until = finished_at_dt + timedelta(seconds=max(30, self.holdout_seconds))
            if now < holdout_until:
                evidence = {
                    "id": f"evd_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
                    "experiment_id": experiment_id,
                    "metrics_before": before,
                    "metrics_after": {},
                    "delta": {
                        "health_score": 0.0,
                        "open_issues": 0,
                        "total_errors": 0,
                        "avg_duration_ms": 0.0,
                        "success_rate": 0.0,
                    },
                    "verdict": "inconclusive",
                    "confidence": 0.45,
                    "signals": {
                        "positives": 0,
                        "negatives": 0,
                        "reasons": ["holdout_window"],
                        "execution_success": bool(run_artifacts.get("execution_success", True)),
                        "execution_status": str(run_artifacts.get("result", "")),
                        "run_duration_ms": int(run_artifacts.get("duration_ms", 0) or 0),
                    },
                    "notes": "Holdout window active; deferred verification",
                    "ts": datetime.now().isoformat(),
                    "throughput_before": {"executed_or_verified": 0, "verified": 0},
                    "throughput_after": {"executed_or_verified": 0, "verified": 0},
                    "execution": {
                        "status": str(target.get("execution_status", "")),
                        "duration_ms": int(run_artifacts.get("duration_ms", 0) or 0),
                        "estimated_cost_usd": float(run_artifacts.get("estimated_cost_usd", 0.0) or 0.0),
                        "result": str(run_artifacts.get("result", "") or ""),
                    },
                    "pending_recheck": True,
                    "attempt": previous_attempts + 1,
                    "holdout_pending": True,
                    "next_recheck_after": holdout_until.isoformat(),
                }
                proposal = run_artifacts.get("proposal", {}) if isinstance(run_artifacts, dict) else {}
                if isinstance(proposal, dict):
                    meta = proposal.get("metadata", {}) if isinstance(proposal.get("metadata"), dict) else {}
                    if meta.get("model"):
                        evidence["model"] = meta.get("model")
                try:
                    from .cafe_loop import get_cafe_scorer
                    evidence["cafe"] = get_cafe_scorer().score_evidence(evidence)
                except Exception:
                    pass
                evidence_id = self.storage.record_outcome_evidence(evidence)
                self.storage.update_experiment_run(experiment_id, {
                    "verification": {
                        "evidence_id": evidence_id,
                        "verdict": evidence["verdict"],
                        "confidence": evidence["confidence"],
                        "pending_recheck": True,
                        "attempts": previous_attempts + 1,
                        "verified_at": datetime.now().isoformat(),
                        "holdout_pending": True,
                        "next_recheck_after": evidence.get("next_recheck_after"),
                    },
                })
                proposal_id = target.get("proposal_id")
                if proposal_id:
                    self.proposals.mark_status(
                        proposal_id,
                        "executed",
                        verification_pending=True,
                        verification_last_attempt_at=datetime.now().isoformat(),
                        verdict=evidence["verdict"],
                        verdict_confidence=evidence["confidence"],
                        evidence_id=evidence_id,
                    )
                return {
                    "ok": True,
                    "evidence": evidence,
                    "evidence_id": evidence_id,
                    "pending_recheck": True,
                }

        run_artifacts = target.get("artifacts", {}) if isinstance(target.get("artifacts"), dict) else {}
        verdict_data = self._verdict(before, after, run_artifacts=run_artifacts)
        verdict_data["delta"]["proposal_throughput"] = after_throughput["executed_or_verified"] - before_throughput["executed_or_verified"]
        verdict_data["delta"]["verified_count"] = after_throughput["verified"] - before_throughput["verified"]
        throughput_gain = int(verdict_data["delta"].get("proposal_throughput", 0) or 0)
        execution_result = str(((run_artifacts or {}).get("result", "") or "")).strip().lower()
        allows_throughput_win = execution_result not in {"simulated_apply_success", "controlled_apply_success"}
        if (
            verdict_data.get("verdict") == "inconclusive"
            and throughput_gain > 0
            and allows_throughput_win
            and float(verdict_data["delta"].get("health_score", 0.0) or 0.0) >= 0.0
            and int(verdict_data["delta"].get("open_issues", 0) or 0) <= 0
        ):
            # Throughput gain without health regression is treated as a low-confidence operational win.
            verdict_data["verdict"] = "win"
            verdict_data["confidence"] = max(float(verdict_data.get("confidence", 0.0) or 0.0), 0.62)
            signals = verdict_data.get("signals", {}) if isinstance(verdict_data.get("signals"), dict) else {}
            reasons = signals.get("reasons", []) if isinstance(signals.get("reasons"), list) else []
            reasons.append("throughput_improved_without_regression")
            signals["reasons"] = reasons
            signals["positives"] = int(signals.get("positives", 0) or 0) + 1
            verdict_data["signals"] = signals
        weak_signal = (
            abs(float(verdict_data["delta"].get("health_score", 0.0) or 0.0)) < 0.5
            and int(verdict_data["delta"].get("open_issues", 0) or 0) == 0
            and int(verdict_data["delta"].get("total_errors", 0) or 0) == 0
            and abs(float(verdict_data["delta"].get("avg_duration_ms", 0.0) or 0.0)) < 50.0
            and abs(float(verdict_data["delta"].get("success_rate", 0.0) or 0.0)) < 0.01
        )
        pending_recheck = (
            verdict_data.get("verdict") == "inconclusive"
            and float(verdict_data.get("confidence", 0.0) or 0.0) < 0.58
            and weak_signal
        )
        evidence = {
            "id": f"evd_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            "experiment_id": experiment_id,
            "metrics_before": before,
            "metrics_after": after,
            "delta": verdict_data["delta"],
            "verdict": verdict_data["verdict"],
            "confidence": verdict_data["confidence"],
            "signals": verdict_data.get("signals", {}),
            "notes": "Automated outcome verification",
            "ts": datetime.now().isoformat(),
            "throughput_before": before_throughput,
            "throughput_after": after_throughput,
            "execution": {
                "status": str(target.get("execution_status", "")),
                "duration_ms": int(run_artifacts.get("duration_ms", 0) or 0),
                "estimated_cost_usd": float(run_artifacts.get("estimated_cost_usd", 0.0) or 0.0),
                "result": str(run_artifacts.get("result", "") or ""),
            },
            "pending_recheck": pending_recheck,
            "attempt": previous_attempts + 1,
        }
        proposal = run_artifacts.get("proposal", {}) if isinstance(run_artifacts, dict) else {}
        if isinstance(proposal, dict):
            meta = proposal.get("metadata", {}) if isinstance(proposal.get("metadata"), dict) else {}
            if meta.get("model"):
                evidence["model"] = meta.get("model")
        try:
            from .cafe_loop import get_cafe_scorer
            evidence["cafe"] = get_cafe_scorer().score_evidence(evidence)
        except Exception:
            pass
        evidence_id = self.storage.record_outcome_evidence(evidence)

        self.storage.update_experiment_run(experiment_id, {
            "verification": {
                "evidence_id": evidence_id,
                "verdict": evidence["verdict"],
                "confidence": evidence["confidence"],
                "pending_recheck": pending_recheck,
                "attempts": previous_attempts + 1,
                "verified_at": datetime.now().isoformat(),
            },
        })

        proposal_id = target.get("proposal_id")
        if proposal_id:
            if pending_recheck:
                self.proposals.mark_status(
                    proposal_id,
                    "executed",
                    verification_pending=True,
                    verification_last_attempt_at=datetime.now().isoformat(),
                    verdict=evidence["verdict"],
                    verdict_confidence=evidence["confidence"],
                    evidence_id=evidence_id,
                )
            else:
                self.proposals.mark_status(
                    proposal_id,
                    "verified",
                    verified_at=datetime.now().isoformat(),
                    verification_pending=False,
                    verdict=evidence["verdict"],
                    verdict_confidence=evidence["confidence"],
                    evidence_id=evidence_id,
                )

        return {
            "ok": True,
            "evidence": evidence,
            "evidence_id": evidence_id,
            "pending_recheck": pending_recheck,
        }


_verifier: Optional[OutcomeVerifier] = None


def get_outcome_verifier() -> OutcomeVerifier:
    global _verifier
    if _verifier is None:
        _verifier = OutcomeVerifier()
    return _verifier


def verify_experiment(experiment_id: str) -> Dict[str, Any]:
    return get_outcome_verifier().verify_experiment(experiment_id)
