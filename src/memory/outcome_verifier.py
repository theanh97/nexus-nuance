"""Outcome verification for executed experiments."""

from datetime import datetime, timedelta
from time import sleep
from typing import Any, Dict, List, Optional
import os

from .proposal_engine import get_proposal_engine_v2
from .self_debugger import health_check
from .storage_v2 import get_storage_v2


DEFAULT_VERIFICATION_THRESHOLDS: Dict[str, float] = {
    "health_improve": 1.0,
    "health_decline": -1.0,
    "latency_improve_ms": -100.0,
    "latency_regress_ms": 200.0,
    "success_improve": 0.02,
    "success_regress": -0.02,
    "weak_signal_abs_health": 0.5,
    "weak_signal_abs_latency_ms": 50.0,
    "weak_signal_abs_success_rate": 0.01,
    "pending_recheck_confidence_max": 0.58,
}

VERIFICATION_THRESHOLD_BOUNDS: Dict[str, tuple[float, float]] = {
    "health_improve": (0.6, 1.2),
    "health_decline": (-1.2, -0.6),
    "latency_improve_ms": (-180.0, -60.0),
    "latency_regress_ms": (120.0, 280.0),
    "success_improve": (0.01, 0.03),
    "success_regress": (-0.03, -0.01),
    "weak_signal_abs_health": (0.35, 0.8),
    "weak_signal_abs_latency_ms": (30.0, 120.0),
    "weak_signal_abs_success_rate": (0.006, 0.03),
    "pending_recheck_confidence_max": (0.52, 0.68),
}


def _clamp(value: float, lo: float, hi: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = float(lo)
    if numeric < lo:
        return lo
    if numeric > hi:
        return hi
    return numeric


class OutcomeVerifier:
    def __init__(self):
        self.storage = get_storage_v2()
        self.proposals = get_proposal_engine_v2()
        self.holdout_enabled = os.getenv("VERIFICATION_HOLDOUT_ENABLED", "true").strip().lower() == "true"
        self.holdout_seconds = int(os.getenv("VERIFICATION_HOLDOUT_SECONDS", "180"))
        self.multi_sample_enabled = os.getenv("VERIFICATION_MULTI_SAMPLE_ENABLED", "true").strip().lower() == "true"
        self.multi_sample_count = max(1, min(5, int(os.getenv("VERIFICATION_MULTI_SAMPLE_COUNT", "3") or 3)))
        self.multi_sample_interval_seconds = max(
            0.0,
            min(30.0, float(os.getenv("VERIFICATION_MULTI_SAMPLE_INTERVAL_SECONDS", "0") or 0.0)),
        )

    def _resolve_thresholds(self, raw: Any) -> Dict[str, float]:
        source = raw if isinstance(raw, dict) else {}
        resolved: Dict[str, float] = {}
        for key, default in DEFAULT_VERIFICATION_THRESHOLDS.items():
            lo, hi = VERIFICATION_THRESHOLD_BOUNDS.get(key, (float(default), float(default)))
            resolved[key] = _clamp(source.get(key, default), lo, hi)
        return resolved

    def _load_verification_thresholds(self) -> Dict[str, float]:
        try:
            from .cafe_calibrator import get_cafe_calibrator

            state = get_cafe_calibrator().get_state()
            if isinstance(state, dict):
                return self._resolve_thresholds(state.get("verification_thresholds"))
        except Exception:
            pass
        return self._resolve_thresholds({})

    def _collect_after_snapshot(self) -> Dict[str, Any]:
        sample_target = self.multi_sample_count if self.multi_sample_enabled else 1
        sample_target = max(1, int(sample_target))
        interval = max(0.0, float(self.multi_sample_interval_seconds))

        samples: List[Dict[str, Any]] = []
        for idx in range(sample_target):
            sample = health_check()
            if isinstance(sample, dict):
                samples.append(sample)
            if idx < sample_target - 1 and interval > 0:
                sleep(interval)

        if not samples:
            return {
                "after": {},
                "meta": {
                    "sample_count": 0,
                    "requested_sample_count": sample_target,
                    "sample_interval_seconds": interval,
                    "aggregation": "mean",
                },
            }

        def _avg_float(path: List[str], default: float = 0.0) -> float:
            values: List[float] = []
            for row in samples:
                node: Any = row
                for key in path:
                    node = node.get(key) if isinstance(node, dict) else None
                try:
                    values.append(float(node or 0.0))
                except (TypeError, ValueError):
                    continue
            if not values:
                return float(default)
            return float(sum(values) / len(values))

        def _avg_int(path: List[str], default: int = 0) -> int:
            return int(round(_avg_float(path, float(default))))

        aggregated = {
            "health_score": round(_avg_float(["health_score"], 0.0), 4),
            "open_issues": _avg_int(["open_issues"], 0),
            "status": str(samples[-1].get("status", "")),
            "timestamp": datetime.now().isoformat(),
            "recent_stats": {
                "total_errors": _avg_int(["recent_stats", "total_errors"], 0),
                "avg_duration_ms": round(_avg_float(["recent_stats", "avg_duration_ms"], 0.0), 4),
                "success_rate": round(_avg_float(["recent_stats", "success_rate"], 0.0), 6),
            },
        }

        return {
            "after": aggregated,
            "meta": {
                "sample_count": len(samples),
                "requested_sample_count": sample_target,
                "sample_interval_seconds": interval,
                "aggregation": "mean",
            },
        }

    def _verdict(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any],
        run_artifacts: Optional[Dict[str, Any]] = None,
        thresholds: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
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

        thresholds = self._resolve_thresholds(thresholds)

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

        if delta_health >= thresholds["health_improve"]:
            positives += 1
            reasons.append("health_score_improved")
        elif delta_health <= thresholds["health_decline"]:
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

        if delta_duration_ms <= thresholds["latency_improve_ms"]:
            positives += 1
            reasons.append("latency_improved")
        elif delta_duration_ms >= thresholds["latency_regress_ms"]:
            negatives += 1
            reasons.append("latency_regressed")

        if delta_success_rate >= thresholds["success_improve"]:
            positives += 1
            reasons.append("success_rate_improved")
        elif delta_success_rate <= thresholds["success_regress"]:
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
        thresholds = self._load_verification_thresholds()
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
                        "sampling": {
                            "sample_count": 0,
                            "requested_sample_count": int(self.multi_sample_count),
                            "sample_interval_seconds": float(self.multi_sample_interval_seconds),
                            "aggregation": "holdout_pending",
                        },
                        "thresholds": thresholds,
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

        after_snapshot = self._collect_after_snapshot()
        after = after_snapshot.get("after", {}) if isinstance(after_snapshot.get("after"), dict) else {}
        sample_meta = after_snapshot.get("meta", {}) if isinstance(after_snapshot.get("meta"), dict) else {}
        if not after:
            after = health_check()
            sample_meta = {
                "sample_count": 1,
                "requested_sample_count": 1,
                "sample_interval_seconds": 0.0,
                "aggregation": "single_snapshot",
            }

        run_artifacts = target.get("artifacts", {}) if isinstance(target.get("artifacts"), dict) else {}
        verdict_data = self._verdict(before, after, run_artifacts=run_artifacts, thresholds=thresholds)
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
            abs(float(verdict_data["delta"].get("health_score", 0.0) or 0.0)) < thresholds["weak_signal_abs_health"]
            and int(verdict_data["delta"].get("open_issues", 0) or 0) == 0
            and int(verdict_data["delta"].get("total_errors", 0) or 0) == 0
            and abs(float(verdict_data["delta"].get("avg_duration_ms", 0.0) or 0.0)) < thresholds["weak_signal_abs_latency_ms"]
            and abs(float(verdict_data["delta"].get("success_rate", 0.0) or 0.0)) < thresholds["weak_signal_abs_success_rate"]
        )
        pending_recheck = (
            verdict_data.get("verdict") == "inconclusive"
            and float(verdict_data.get("confidence", 0.0) or 0.0) < thresholds["pending_recheck_confidence_max"]
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
            "verification_sampling": {
                "sample_count": int(sample_meta.get("sample_count", 0) or 0),
                "requested_sample_count": int(sample_meta.get("requested_sample_count", 0) or 0),
                "sample_interval_seconds": float(sample_meta.get("sample_interval_seconds", 0.0) or 0.0),
                "aggregation": str(sample_meta.get("aggregation", "mean") or "mean"),
            },
            "verification_thresholds": thresholds,
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
                "sampling": evidence.get("verification_sampling", {}),
                "thresholds": thresholds,
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
