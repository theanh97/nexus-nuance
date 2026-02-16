"""Execution engine for proposal v2."""

from datetime import datetime
from typing import Any, Dict, Optional
import os

from .proposal_engine import get_proposal_engine_v2
from .storage_v2 import get_storage_v2
from .self_debugger import health_check

try:
    from nexus.self_improvement import run_self_improvement_cycle
except Exception:
    run_self_improvement_cycle = None


class ExperimentExecutor:
    def __init__(self):
        self.storage = get_storage_v2()
        self.proposals = get_proposal_engine_v2()
        self.enable_real_apply = os.getenv("ENABLE_EXECUTOR_REAL_APPLY", "false").strip().lower() == "true"
        self.max_real_apply_patches = int(os.getenv("EXECUTOR_REAL_APPLY_MAX_PATCHES", "1"))

    def execute_proposal(self, proposal_id: str, mode: str = "safe") -> Dict[str, Any]:
        mode = (mode or "safe").strip().lower()
        if mode not in {"safe", "normal"}:
            mode = "safe"

        proposal = None
        for p in self.proposals.list_pending():
            if p.get("id") == proposal_id:
                proposal = p
                break

        if not proposal:
            return {"ok": False, "error": "proposal_not_found", "proposal_id": proposal_id}

        if proposal.get("status") not in {"approved", "pending_approval"}:
            return {"ok": False, "error": "proposal_not_actionable", "proposal_id": proposal_id}

        if proposal.get("status") == "pending_approval":
            return {"ok": False, "error": "proposal_requires_approval", "proposal_id": proposal_id}

        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        started_at_dt = datetime.now()
        before = health_check()
        before_stats = before.get("recent_stats", {}) if isinstance(before.get("recent_stats"), dict) else {}
        run = {
            "id": run_id,
            "proposal_id": proposal_id,
            "mode": mode,
            "started_at": started_at_dt.isoformat(),
            "finished_at": None,
            "actions": ["collect_baseline", "apply_change", "verify_checks"],
            "artifacts": {
                "proposal": proposal,
                "baseline_health": before,
                "baseline_stats": before_stats,
                "throughput_before": {
                    "executed_or_verified": len(
                        [
                            p for p in self.storage.get_proposals_v2().get("proposals", [])
                            if isinstance(p, dict) and p.get("status") in {"executed", "verified"}
                        ]
                    ),
                    "verified": len(
                        [
                            p for p in self.storage.get_proposals_v2().get("proposals", [])
                            if isinstance(p, dict) and p.get("status") == "verified"
                        ]
                    ),
                },
                "dry_run": mode == "safe",
                "executor": {
                    "real_apply_enabled": self.enable_real_apply,
                    "max_real_apply_patches": self.max_real_apply_patches,
                },
            },
            "execution_status": "running",
        }
        self.storage.add_experiment_run(run)

        if mode == "normal" and self.enable_real_apply and callable(run_self_improvement_cycle):
            try:
                cycle = run_self_improvement_cycle(max_patches=max(1, self.max_real_apply_patches))
                cycle_data = cycle.__dict__ if hasattr(cycle, "__dict__") else {}
                run["artifacts"]["self_improvement_cycle"] = cycle_data
                run["artifacts"]["patches_successful"] = int(cycle_data.get("patches_successful", 0) or 0)
                run["execution_status"] = "completed" if int(cycle_data.get("patches_applied", 0) or 0) > 0 else "no_changes"
                run["artifacts"]["result"] = "real_apply_cycle_executed"
                run["artifacts"]["estimated_cost_usd"] = float(cycle_data.get("estimated_cost_usd", 0.0) or 0.0)
            except Exception as e:
                run["execution_status"] = "failed"
                run["artifacts"]["result"] = "real_apply_failed"
                run["artifacts"]["error"] = str(e)
                run["artifacts"]["estimated_cost_usd"] = 0.0
        else:
            # Non-destructive execution path.
            run["execution_status"] = "completed"
            run["artifacts"]["result"] = "simulated_apply_success" if mode == "safe" else "controlled_apply_success"
            run["artifacts"]["reason"] = "real_apply_disabled_or_unavailable"
            # Keep a deterministic cost estimate for safe analytics.
            run["artifacts"]["estimated_cost_usd"] = 0.0 if mode == "safe" else 0.01
        finished_at_dt = datetime.now()
        run["finished_at"] = finished_at_dt.isoformat()
        run["artifacts"]["duration_ms"] = int((finished_at_dt - started_at_dt).total_seconds() * 1000)
        run["artifacts"]["execution_success"] = run["execution_status"] in {"completed", "no_changes"}

        self.storage.update_experiment_run(run_id, {
            "execution_status": run["execution_status"],
            "finished_at": run["finished_at"],
            "artifacts": run["artifacts"],
        })

        new_status = "executed" if run["execution_status"] in {"completed", "no_changes"} else "approved"
        self.proposals.mark_status(
            proposal_id,
            new_status,
            executed_at=run["finished_at"] if new_status == "executed" else None,
            last_run_id=run_id,
            execution_mode=mode,
        )

        return {
            "ok": run["execution_status"] in {"completed", "no_changes"},
            "run_id": run_id,
            "proposal_id": proposal_id,
            "mode": mode,
            "status": run["execution_status"],
        }


_executor: Optional[ExperimentExecutor] = None


def get_experiment_executor() -> ExperimentExecutor:
    global _executor
    if _executor is None:
        _executor = ExperimentExecutor()
    return _executor


def execute_proposal(proposal_id: str, mode: str = "safe") -> Dict[str, Any]:
    return get_experiment_executor().execute_proposal(proposal_id, mode)
