"""
NEXUS BRAIN API
Unified API for all NEXUS functionality
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
from datetime import datetime
import sys
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


router = APIRouter(prefix="/api/nexus", tags=["NEXUS Brain"])


# ==================== MODELS ====================

class LearnRequest(BaseModel):
    source: str
    type: str
    title: str
    content: str
    url: Optional[str] = None
    relevance: float = 0.7
    tags: List[str] = []


class FeedbackRequest(BaseModel):
    content: str
    is_positive: bool
    context: Optional[Dict[str, Any]] = None


class TaskExecutionRequest(BaseModel):
    task_name: str
    duration_ms: float
    success: bool
    details: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


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
async def learn_knowledge(req: LearnRequest):
    """Learn new knowledge"""
    item = learn(
        source=req.source,
        type=req.type,
        title=req.title,
        content=req.content,
        url=req.url,
        relevance=req.relevance,
        tags=req.tags
    )
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
async def search_knowledge(req: SearchRequest):
    """Search knowledge base"""
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


# ==================== MAIN ====================

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title="NEXUS Brain API")
    app.include_router(router)

    print("Starting NEXUS Brain API...")
    print("Endpoints:")
    print("  GET  /api/nexus/status   - Get status")
    print("  POST /api/nexus/start    - Start brain")
    print("  POST /api/nexus/learn    - Learn knowledge")
    print("  POST /api/nexus/feedback - Submit feedback")
    print("  POST /api/nexus/task     - Record task")
    print("  POST /api/nexus/search   - Search knowledge")
    print("  GET  /api/nexus/skills   - Get skills")
    print("  GET  /api/nexus/memory   - Get memory stats")
    print("  GET  /api/nexus/cycles   - Get cycles")

    uvicorn.run(app, host="0.0.0.0", port=8766)
