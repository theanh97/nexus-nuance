"""
NEXUS BRAIN API
Unified API for all NEXUS functionality
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional, Any
from datetime import datetime
import os
import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from brain.nexus_brain import (
    get_brain,
    start_brain,
    learn,
    feedback,
    task_executed,
    brain_stats
)
from brain.autonomous_agent import get_autonomous_agent
from brain.action_executor import get_executor

try:
    from core.event_bus import emit_event
except ImportError:
    def emit_event(*_a, **_kw): pass

try:
    from core.rate_limiter import check_rate_limit as _check_rate_limit
except ImportError:
    def _check_rate_limit(*_a, **_kw): return True, {}


def _rate_limit_or_429(request: Request) -> None:
    """Raise 429 if rate limit exceeded. Call at top of POST endpoints."""
    client = request.client.host if request.client else "unknown"
    allowed, info = _check_rate_limit(client)
    if not allowed:
        raise HTTPException(status_code=429, detail=info)


router = APIRouter(prefix="/api/nexus", tags=["NEXUS Brain"])


# ==================== REQUEST METRICS ====================

class _RequestMetrics:
    """Lightweight in-memory request metrics collector."""

    __slots__ = ("_lock", "_data")

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self._data: Dict[str, Dict] = {}

    def record(self, path: str, method: str, status: int, duration_ms: float) -> None:
        key = f"{method} {path}"
        with self._lock:
            entry = self._data.setdefault(key, {
                "count": 0, "errors": 0, "total_ms": 0.0,
                "min_ms": float("inf"), "max_ms": 0.0,
            })
            entry["count"] += 1
            entry["total_ms"] += duration_ms
            entry["min_ms"] = min(entry["min_ms"], duration_ms)
            entry["max_ms"] = max(entry["max_ms"], duration_ms)
            if status >= 400:
                entry["errors"] += 1

    def snapshot(self) -> Dict:
        with self._lock:
            result = {}
            for key, v in self._data.items():
                result[key] = {
                    **v,
                    "avg_ms": round(v["total_ms"] / v["count"], 1) if v["count"] else 0,
                    "min_ms": round(v["min_ms"], 1) if v["min_ms"] != float("inf") else 0,
                    "max_ms": round(v["max_ms"], 1),
                }
            return result


_metrics = _RequestMetrics()


# ==================== MODELS ====================

class LearnRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, max_length=50_000)
    url: Optional[str] = Field(None, max_length=2000)
    relevance: float = Field(0.7, ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        return [t[:100] for t in v]


class FeedbackRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)
    is_positive: bool
    context: Optional[Dict[str, Any]] = None


class TaskExecutionRequest(BaseModel):
    task_name: str = Field(..., min_length=1, max_length=500)
    duration_ms: float = Field(..., ge=0)
    success: bool
    details: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(10, ge=1, le=100)


class ExecuteRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=5000)
    max_cycles: int = Field(10, ge=1, le=100)
    verification_required: bool = True


# ==================== ENDPOINTS ====================

@router.get("/status")
async def get_status():
    """Get brain status and stats"""
    stats = brain_stats()
    return {
        "status": "running" if stats.get("running") else "stopped",
        "stats": stats
    }


@router.post("/start")
async def start_nexus():
    """Start Nexus Brain"""
    brain = start_brain()
    return {"status": "started"}


@router.post("/learn")
async def learn_knowledge(req: LearnRequest, request: Request):
    """Learn new knowledge"""
    _rate_limit_or_429(request)
    item = learn(
        source=req.source,
        type=req.type,
        title=req.title,
        content=req.content,
        url=req.url,
        relevance=req.relevance,
        tags=req.tags
    )
    emit_event("knowledge.learned", {"id": item.id, "source": req.source, "title": req.title})
    return {"success": True, "id": item.id}


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Submit feedback"""
    feedback(req.content, req.is_positive, req.context)
    return {"success": True}


@router.post("/task")
async def record_task(req: TaskExecutionRequest):
    """Record task execution"""
    task_executed(req.task_name, req.duration_ms, req.success, req.details)
    return {"success": True}


@router.post("/search")
async def search_knowledge(req: SearchRequest, request: Request):
    """Search knowledge base"""
    _rate_limit_or_429(request)
    brain = get_brain()
    results = brain.search(req.query)
    return {"results": results, "count": len(results)}


@router.get("/skills")
async def get_skills():
    """Get skill progression"""
    stats = brain_stats()
    return stats.get("skills", {})


@router.get("/memory")
async def get_memory_stats():
    """Get memory statistics"""
    stats = brain_stats()
    return stats.get("memory", {})


@router.get("/cycles")
async def get_cycles():
    """Get cycle status"""
    stats = brain_stats()
    return stats.get("cycles", {})


@router.post("/execute")
async def execute_task(req: ExecuteRequest, request: Request):
    """Execute a task with autonomous agent."""
    _rate_limit_or_429(request)
    agent = get_autonomous_agent()
    result = agent.execute_autonomously(req.task, max_cycles=req.max_cycles)
    success = result.get("success", False)
    emit_event("task.executed", {"task": req.task[:100], "success": success})
    if req.verification_required and not success:
        raise HTTPException(status_code=409, detail={"success": False, "result": result})
    return {"success": success, "result": result}


@router.get("/safety")
async def get_safety():
    """Get execution policy and recent policy-block data."""
    executor = get_executor()
    recent = executor.history[-50:]
    policy_blocked = sum(1 for r in recent if getattr(r, "policy_blocked", False))
    return {
        "execution_mode": executor.execution_mode,
        "policy_blocked_recent": policy_blocked,
        "recent_actions": len(recent),
    }


@router.get("/trust-metrics")
async def get_trust_metrics():
    """Get trust metrics: grounded success and policy rates."""
    executor = get_executor()
    recent = executor.history[-100:]
    total = len(recent)
    objective_success = sum(1 for r in recent if getattr(r, "objective_success", False))
    policy_blocked = sum(1 for r in recent if getattr(r, "policy_blocked", False))
    failed = sum(1 for r in recent if r.status.value in {"failed", "timeout"})
    return {
        "sample_size": total,
        "objective_success_rate": (objective_success / total) if total else 0.0,
        "policy_block_rate": (policy_blocked / total) if total else 0.0,
        "failure_rate": (failed / total) if total else 0.0,
        "generated_at": datetime.now().isoformat(),
    }


@router.get("/skill-recommendation/{task_type}")
async def skill_recommendation(task_type: str):
    """Get skill-based recommendation for a task type."""
    brain = get_brain()
    if not brain or not hasattr(brain, "skill_tracker"):
        return {"recommendation": "execute", "confidence": 0.0, "reason": "Brain not available"}
    return brain.skill_tracker.get_skill_recommendation(task_type)


@router.get("/budget-projection")
async def budget_projection():
    """Get budget spend projection for end of day."""
    try:
        from core.model_router import ModelRouter
        router_inst = ModelRouter()
        return router_inst.get_budget_projection()
    except (ImportError, AttributeError) as e:
        return {"error": str(e), "status": "unavailable"}


@router.get("/source-quality")
async def source_quality():
    """Get ranked source quality scores."""
    try:
        from brain.omniscient_scout import get_scout
        scout = get_scout()
        return {"sources": scout.get_ranked_sources()}
    except (ImportError, AttributeError) as e:
        return {"error": str(e), "status": "unavailable"}


@router.get("/system-overview")
async def system_overview():
    """Comprehensive system overview - all subsystems in one call."""
    overview = {"timestamp": datetime.now().isoformat(), "subsystems": {}}

    # Brain status
    try:
        stats = brain_stats()
        overview["subsystems"]["brain"] = {
            "status": "running" if stats.get("running") else "stopped",
            "knowledge_items": stats.get("memory", {}).get("total_knowledge", 0),
            "skills": len(stats.get("skills", {})),
        }
    except Exception as e:
        overview["subsystems"]["brain"] = {"status": "error", "error": str(e)}

    # LLM models
    try:
        from core.llm_caller import get_available_models
        models = get_available_models()
        overview["subsystems"]["llm"] = {
            "available": [n for n, ok in models.items() if ok],
            "unavailable": [n for n, ok in models.items() if not ok],
        }
    except ImportError:
        overview["subsystems"]["llm"] = {"status": "unavailable"}

    # Budget projection
    try:
        from core.model_router import ModelRouter
        router_inst = ModelRouter()
        overview["subsystems"]["budget"] = router_inst.get_budget_projection()
    except Exception:
        overview["subsystems"]["budget"] = {"status": "unavailable"}

    # Integration hub
    try:
        from brain.integration_hub import get_hub
        hub = get_hub()
        overview["subsystems"]["hub"] = hub.get_status()
    except Exception:
        overview["subsystems"]["hub"] = {"status": "unavailable"}

    # Action executor
    try:
        executor = get_executor()
        recent = executor.history[-50:]
        overview["subsystems"]["executor"] = {
            "total_actions": len(executor.history),
            "recent_success_rate": sum(1 for r in recent if r.status.value == "success") / max(len(recent), 1),
            "execution_mode": executor.execution_mode,
        }
    except Exception:
        overview["subsystems"]["executor"] = {"status": "unavailable"}

    return overview


@router.get("/health")
async def health_check():
    """Quick health check - returns system readiness status."""
    checks = {}

    # Brain
    try:
        brain = get_brain()
        checks["brain"] = "ok" if brain else "unavailable"
    except Exception:
        checks["brain"] = "error"

    # LLM
    try:
        from core.llm_caller import get_available_models
        models = get_available_models()
        active = sum(1 for ok in models.values() if ok)
        checks["llm"] = "ok" if active > 0 else "degraded"
        checks["llm_active_count"] = active
    except ImportError:
        checks["llm"] = "unavailable"

    # Executor
    try:
        executor = get_executor()
        checks["executor"] = "ok" if executor else "unavailable"
    except Exception:
        checks["executor"] = "error"

    all_ok = all(v in ("ok",) for k, v in checks.items() if not k.endswith("_count"))
    return {
        "status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.now().isoformat(),
        "checks": checks,
    }


@router.get("/self-diagnostic")
async def self_diagnostic():
    """Run a comprehensive self-diagnostic across all subsystems."""
    diag = {"timestamp": datetime.now().isoformat(), "issues": [], "score": 100}

    # Check recent action success rate
    try:
        executor = get_executor()
        recent = executor.history[-100:]
        if len(recent) >= 10:
            fail_rate = sum(1 for r in recent if r.status.value in ("failed", "timeout")) / len(recent)
            if fail_rate > 0.3:
                diag["issues"].append(f"High action failure rate: {fail_rate:.0%}")
                diag["score"] -= 20
            elif fail_rate > 0.1:
                diag["issues"].append(f"Elevated action failure rate: {fail_rate:.0%}")
                diag["score"] -= 10
    except Exception as e:
        diag["issues"].append(f"Executor check failed: {e}")
        diag["score"] -= 15

    # Check LLM availability
    try:
        from core.llm_caller import get_available_models
        models = get_available_models()
        active = sum(1 for ok in models.values() if ok)
        total = len(models)
        if active == 0:
            diag["issues"].append("No LLM models available - running in heuristic-only mode")
            diag["score"] -= 30
        elif active < total / 2:
            diag["issues"].append(f"Only {active}/{total} LLM models available")
            diag["score"] -= 10
    except ImportError:
        diag["issues"].append("LLM caller not installed")
        diag["score"] -= 25

    # Check budget
    try:
        from core.model_router import ModelRouter
        router_inst = ModelRouter()
        proj = router_inst.get_budget_projection()
        if proj.get("status") == "over_budget":
            diag["issues"].append("Over budget - actions may be throttled")
            diag["score"] -= 20
        elif proj.get("status") == "caution":
            diag["issues"].append("Budget caution - approaching daily limit")
            diag["score"] -= 5
    except Exception:
        pass

    # Check brain knowledge
    try:
        stats = brain_stats()
        knowledge = stats.get("memory", {}).get("total_knowledge", 0)
        if knowledge < 10:
            diag["issues"].append(f"Low knowledge base: {knowledge} items")
            diag["score"] -= 5
    except Exception:
        pass

    diag["score"] = max(0, diag["score"])
    diag["verdict"] = "excellent" if diag["score"] >= 90 else "good" if diag["score"] >= 70 else "needs_attention" if diag["score"] >= 50 else "critical"
    return diag


@router.post("/maintenance")
async def run_maintenance():
    """Run periodic maintenance: cache prune, history trim, old file cleanup."""
    try:
        from brain.integration_hub import get_hub
        hub = get_hub()
        return hub.run_maintenance()
    except (ImportError, AttributeError) as e:
        return {"error": str(e), "status": "unavailable"}


@router.get("/metrics")
async def get_metrics():
    """Get API request metrics: count, latency percentiles, error rates."""
    return {"timestamp": datetime.now().isoformat(), "endpoints": _metrics.snapshot()}


@router.get("/events")
async def get_events(limit: int = Query(default=50, ge=1, le=200), event_type: Optional[str] = None):
    """Get recent system events from the event bus."""
    try:
        from core.event_bus import get_recent_events
        return {"events": get_recent_events(limit=limit, event_type=event_type)}
    except ImportError:
        return {"events": [], "note": "event_bus not available"}


@router.post("/backup")
async def create_backup():
    """Create a compressed backup of brain data."""
    try:
        from core.backup import create_backup as _create
        return _create()
    except (ImportError, OSError) as e:
        return {"error": str(e), "status": "unavailable"}


@router.get("/backups")
async def list_backups():
    """List available backups."""
    try:
        from core.backup import list_backups as _list
        return {"backups": _list()}
    except ImportError:
        return {"backups": [], "status": "unavailable"}


@router.post("/restore/{backup_name}")
async def restore_backup(backup_name: str):
    """Restore brain data from a backup."""
    if not backup_name.startswith("nexus_backup_") or not backup_name.endswith(".tar.gz"):
        raise HTTPException(400, detail="Invalid backup name format")
    try:
        from core.backup import restore_backup as _restore
        return _restore(backup_name)
    except (ImportError, OSError) as e:
        raise HTTPException(500, detail=str(e))


@router.get("/system-health")
async def system_health():
    """Comprehensive system health combining all subsystems in one call."""
    health = {"timestamp": datetime.now().isoformat(), "subsystems": {}, "overall": "healthy"}
    issues = []

    # Brain
    try:
        brain = get_brain()
        health["subsystems"]["brain"] = "ok" if brain else "unavailable"
    except Exception:
        health["subsystems"]["brain"] = "error"
        issues.append("brain_unavailable")

    # LLM models + circuit breakers
    try:
        from core.llm_caller import get_available_models, get_system_health_summary
        models = get_available_models()
        active = sum(1 for ok in models.values() if ok)
        llm_health = get_system_health_summary()
        health["subsystems"]["llm"] = {
            "active_models": active,
            "total_models": len(models),
            "degraded": llm_health.get("degraded", False),
        }
        if active == 0:
            issues.append("no_llm_available")
    except ImportError:
        health["subsystems"]["llm"] = "unavailable"
        issues.append("llm_module_missing")

    # Executor
    try:
        executor = get_executor()
        recent = executor.history[-50:]
        fail_rate = sum(1 for r in recent if r.status.value in ("failed", "timeout")) / max(len(recent), 1)
        health["subsystems"]["executor"] = {
            "total_actions": len(executor.history),
            "recent_fail_rate": round(fail_rate, 3),
            "mode": executor.execution_mode,
        }
        if fail_rate > 0.3:
            issues.append("high_failure_rate")
    except Exception:
        health["subsystems"]["executor"] = "unavailable"

    # Metrics
    health["subsystems"]["api_metrics"] = _metrics.snapshot()

    # Rate limiter
    try:
        from core.rate_limiter import _buckets
        health["subsystems"]["rate_limiter"] = {"active_clients": len(_buckets)}
    except ImportError:
        pass

    # Event bus
    try:
        from core.event_bus import get_recent_events
        recent_events = get_recent_events(limit=5)
        health["subsystems"]["event_bus"] = {"recent_count": len(recent_events)}
    except ImportError:
        pass

    health["issues"] = issues
    if issues:
        health["overall"] = "degraded" if len(issues) < 3 else "critical"

    return health


def record_request(path: str, method: str, status: int, duration_ms: float) -> None:
    """Public hook for middleware to record a request."""
    _metrics.record(path, method, status, duration_ms)


# ==================== MAIN ====================

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI(title="NEXUS Brain API")

    class _TimingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            start = time.monotonic()
            response = await call_next(request)
            elapsed_ms = (time.monotonic() - start) * 1000
            response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
            record_request(request.url.path, request.method, response.status_code, elapsed_ms)
            return response

    app.add_middleware(_TimingMiddleware)
    app.include_router(router)

    print("Starting NEXUS Brain API...")
    print("Endpoints:")
    print("  GET  /api/nexus/status   - Get status")
    print('  GET  /api/nexus/health    - Health check')
    print("  POST /api/nexus/start    - Start brain")
    print("  POST /api/nexus/learn    - Learn knowledge")
    print("  POST /api/nexus/feedback - Submit feedback")
    print("  POST /api/nexus/task     - Record task")
    print("  POST /api/nexus/search   - Search knowledge")
    print("  GET  /api/nexus/skills   - Get skills")
    print("  GET  /api/nexus/memory   - Get memory stats")
    print("  GET  /api/nexus/cycles   - Get cycles")
    print("  POST /api/nexus/execute  - Execute autonomous task")
    print("  GET  /api/nexus/safety   - Get safety policy status")
    print("  GET  /api/nexus/trust-metrics - Get trust metrics")
    print("  GET  /api/nexus/skill-recommendation/{type} - Skill recommendation")
    print("  GET  /api/nexus/budget-projection - Budget projection")
    print("  GET  /api/nexus/source-quality - Source quality scores")

    uvicorn.run(app, host="0.0.0.0", port=8766)
