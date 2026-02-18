"""
Autonomous Loop API Endpoints
FastAPI endpoints for loop control and monitoring
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
from datetime import datetime
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loop.autonomous_loop import (
    get_loop,
    AutonomousLoop,
    TaskPriority,
    TaskStatus
)


router = APIRouter(prefix="/api/loop", tags=["Autonomous Loop"])


# ==================== MODELS ====================

class TaskCreate(BaseModel):
    name: str
    description: str
    action: str
    params: Optional[Dict[str, Any]] = {}
    priority: str = "MEDIUM"


class VerificationTask(BaseModel):
    target: str
    task_type: str = "url"  # url or file


# ==================== HELPERS ====================

def priority_from_str(s: str) -> TaskPriority:
    mapping = {
        "CRITICAL": TaskPriority.CRITICAL,
        "HIGH": TaskPriority.HIGH,
        "MEDIUM": TaskPriority.MEDIUM,
        "LOW": TaskPriority.LOW
    }
    return mapping.get(s.upper(), TaskPriority.MEDIUM)


def task_to_dict(task) -> Dict:
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "action": task.action,
        "status": task.status.value if hasattr(task.status, 'value') else str(task.status),
        "priority": task.priority.value if hasattr(task.priority, 'value') else str(task.priority),
        "created_at": task.created_at,
        "result": task.result,
        "retry_count": task.retry_count
    }


# ==================== ENDPOINTS ====================

@router.get("/status")
async def get_status():
    """Get current loop status"""
    loop = get_loop()
    status = loop.get_status()
    stats = loop.get_stats()

    return {
        "running": status["running"],
        "pending_tasks": status["pending_tasks"],
        "completed_tasks": status["completed_tasks"],
        "current_task": task_to_dict(status["current_task"]) if status.get("current_task") else None,
        "success_rate": stats.get("loop", {}).get("success_rate", 0),
        "stats": stats,
        "last_updated": status["last_updated"]
    }


@router.post("/start")
async def start_loop():
    """Start autonomous loop"""
    loop = get_loop()
    result = loop.start()
    return result


@router.post("/stop")
async def stop_loop():
    """Stop autonomous loop"""
    loop = get_loop()
    result = loop.stop()
    return result


@router.post("/task")
async def add_task(task: TaskCreate):
    """Add task to queue"""
    loop = get_loop()
    new_task = loop.add_task(
        name=task.name,
        description=task.description,
        action=task.action,
        params=task.params,
        priority=priority_from_str(task.priority)
    )
    return {"success": True, "task": task_to_dict(new_task)}


@router.post("/verify")
async def add_verification(task: VerificationTask):
    """Add verification task"""
    loop = get_loop()
    new_task = loop.add_verification_task(task.target, task.task_type)
    return {"success": True, "task": task_to_dict(new_task)}


@router.get("/tasks")
async def get_tasks(status: Optional[str] = None, limit: int = 20):
    """Get tasks from queue"""
    loop = get_loop()

    tasks = []

    # Pending
    for task in loop.task_queue[:limit]:
        tasks.append(task_to_dict(task))

    # Completed
    for task in loop.completed_tasks[-limit:]:
        tasks.append(task_to_dict(task))

    # Filter by status
    if status:
        tasks = [t for t in tasks if t["status"] == status.upper()]

    return {"tasks": tasks, "count": len(tasks)}


@router.get("/stats")
async def get_stats():
    """Get comprehensive stats"""
    loop = get_loop()
    return loop.get_stats()


@router.get("/log")
async def get_log(lines: int = 50):
    """Get loop log"""
    log_file = Path(__file__).parent.parent.parent / "data" / "loop" / "loop.log"

    if not log_file.exists():
        return {"log": [], "message": "No log file yet"}

    with open(log_file, 'r') as f:
        all_lines = f.readlines()

    return {"log": all_lines[-lines:], "total_lines": len(all_lines)}


@router.post("/learn")
async def add_learning(input_type: str, content: str, value_score: float = 0.5):
    """Add learning directly"""
    loop = get_loop()
    result = loop.improver.learn_from_input(input_type, content, value_score)
    return {"success": True, "learning_id": result.get("id")}


# ==================== MAIN ====================

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title="NEXUS Autonomous Loop API")
    app.include_router(router)

    print("Starting NEXUS Autonomous Loop API...")
    print("Endpoints:")
    print("  GET  /api/loop/status   - Get loop status")
    print("  POST /api/loop/start    - Start loop")
    print("  POST /api/loop/stop     - Stop loop")
    print("  POST /api/loop/task     - Add task")
    print("  POST /api/loop/verify   - Add verification task")
    print("  GET  /api/loop/tasks    - Get tasks")
    print("  GET  /api/loop/stats    - Get stats")

    uvicorn.run(app, host="0.0.0.0", port=8765)
