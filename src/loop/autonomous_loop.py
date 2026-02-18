"""
AUTONOMOUS LEARNING LOOP SYSTEM - Level 3
==========================================
NEXUS tự nhận task → Tự làm → Tự verify bằng browser → Tự học → Tự cải tiến

CORE LOOP:
┌─────────────────────────────────────────────────────────────┐
│  TASK QUEUE → EXECUTE → VERIFY → ANALYZE → LEARN → IMPROVE │
│       ↑                                              │      │
│       └──────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum
import subprocess
import sys

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.user_feedback import UserFeedbackManager
from memory.autonomous_improver import AutonomousImprover, learn_from_input


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    LEARNING = "learning"


class TaskPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


@dataclass
class LoopTask:
    """Task trong autonomous loop"""
    id: str
    name: str
    description: str
    action: str  # python function to call
    params: Dict = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    result: Dict = field(default_factory=dict)
    verification: Dict = field(default_factory=dict)
    learnings: List[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class BrowserVerifier:
    """Verify task results bằng browser"""

    def __init__(self, screenshot_dir: str = None):
        if screenshot_dir:
            self.screenshot_dir = Path(screenshot_dir)
        else:
            self.screenshot_dir = Path(__file__).parent.parent.parent / "data" / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.browser = None

    async def verify_url(self, url: str, task_id: str) -> Dict:
        """Verify a URL bằng cách mở browser và chụp screenshot"""
        result = {
            "url": url,
            "success": False,
            "screenshot": None,
            "error": None,
            "timestamp": datetime.now().isoformat()
        }

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1920, "height": 1080})

                # Navigate
                await page.goto(url, wait_until="networkidle", timeout=30000)

                # Screenshot
                screenshot_path = self.screenshot_dir / f"{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)

                result["success"] = True
                result["screenshot"] = str(screenshot_path)

                await browser.close()

        except Exception as e:
            result["error"] = str(e)

        return result

    def verify_file(self, file_path: str, task_id: str) -> Dict:
        """Verify local file bằng cách mở trong browser"""
        full_path = Path(file_path).absolute()
        if not full_path.exists():
            return {
                "success": False,
                "error": f"File not found: {full_path}",
                "timestamp": datetime.now().isoformat()
            }

        # Run async verification
        return asyncio.run(self.verify_url(f"file://{full_path}", task_id))


class LearningAnalyzer:
    """Phân tích kết quả để học"""

    def __init__(self):
        self.feedback_manager = UserFeedbackManager()
        self.improver = AutonomousImprover()

    def analyze_result(self, task: LoopTask, verification: Dict) -> Dict:
        """Phân tích kết quả task để rút ra learnings"""
        analysis = {
            "task_id": task.id,
            "success": verification.get("success", False),
            "learnings": [],
            "improvements": [],
            "timestamp": datetime.now().isoformat()
        }

        # Nếu task fail
        if not verification.get("success"):
            learning = {
                "type": "failure_pattern",
                "content": f"Task '{task.name}' failed: {verification.get('error', 'Unknown error')}",
                "value_score": 0.8,  # High value - learn from failures
                "suggestion": "Cần investigate root cause và fix"
            }
            analysis["learnings"].append(learning)

            # Learn from input
            self.improver.learn_from_input(
                input_type="task_failure",
                content=learning["content"],
                value_score=0.8,
                context={"task": task.name, "error": verification.get("error")}
            )

        # Nếu task success
        else:
            learning = {
                "type": "success_pattern",
                "content": f"Task '{task.name}' completed successfully",
                "value_score": 0.6,
                "approach": task.action
            }
            analysis["learnings"].append(learning)

        # Phân tích retry pattern
        if task.retry_count > 0:
            learning = {
                "type": "retry_pattern",
                "content": f"Task '{task.name}' needed {task.retry_count} retries",
                "value_score": 0.7,
                "suggestion": "Cần improve task reliability"
            }
            analysis["learnings"].append(learning)

        return analysis

    def generate_improvement_suggestions(self, analysis: Dict) -> List[Dict]:
        """Tạo improvement suggestions từ analysis"""
        suggestions = []

        for learning in analysis.get("learnings", []):
            if learning["type"] == "failure_pattern":
                suggestions.append({
                    "priority": "high",
                    "type": "bug_fix",
                    "description": learning["content"],
                    "suggested_action": "Investigate và fix root cause"
                })
            elif learning["type"] == "retry_pattern":
                suggestions.append({
                    "priority": "medium",
                    "type": "reliability",
                    "description": learning["content"],
                    "suggested_action": "Improve task stability"
                })

        return suggestions


class AutonomousLoop:
    """
    Main Autonomous Learning Loop
    Chạy 24/7: Task → Execute → Verify → Analyze → Learn → Improve
    """

    def __init__(self, data_dir: str = None):
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = Path(__file__).parent.parent.parent / "data" / "loop"

        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Components
        self.verifier = BrowserVerifier()
        self.analyzer = LearningAnalyzer()
        self.feedback_manager = UserFeedbackManager()
        self.improver = AutonomousImprover()

        # State
        self.task_queue: List[LoopTask] = []
        self.completed_tasks: List[LoopTask] = []
        self.running = False
        self.current_task: Optional[LoopTask] = None

        # Callbacks
        self.on_task_complete: Optional[Callable] = None
        self.on_learning: Optional[Callable] = None

        # Load state
        self._load_state()

        # Thread
        self._loop_thread: Optional[threading.Thread] = None

    def _load_state(self):
        """Load state từ disk"""
        state_file = self.data_dir / "loop_state.json"
        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    data = json.load(f)
                    # Restore task queue
                    for t in data.get("pending_tasks", []):
                        task = LoopTask(**t)
                        task.priority = TaskPriority(task.priority.value if isinstance(task.priority, TaskPriority) else task.priority)
                        task.status = TaskStatus.PENDING
                        self.task_queue.append(task)
            except:
                pass

    def _save_state(self):
        """Save state to disk"""
        state_file = self.data_dir / "loop_state.json"
        with open(state_file, 'w') as f:
            json.dump({
                "pending_tasks": [asdict(t) for t in self.task_queue],
                "last_updated": datetime.now().isoformat()
            }, f, indent=2, default=str)

    # ==================== TASK MANAGEMENT ====================

    def add_task(
        self,
        name: str,
        description: str,
        action: str,
        params: Dict = None,
        priority: TaskPriority = TaskPriority.MEDIUM
    ) -> LoopTask:
        """Add task to queue"""
        task = LoopTask(
            id=f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.task_queue)}",
            name=name,
            description=description,
            action=action,
            params=params or {},
            priority=priority
        )

        # Insert theo priority
        inserted = False
        for i, t in enumerate(self.task_queue):
            if task.priority.value < t.priority.value:
                self.task_queue.insert(i, task)
                inserted = True
                break

        if not inserted:
            self.task_queue.append(task)

        self._save_state()
        return task

    def add_verification_task(self, target: str, task_type: str = "url") -> LoopTask:
        """Add verification task"""
        if task_type == "url":
            return self.add_task(
                name=f"Verify URL: {target[:50]}",
                description=f"Verify and screenshot {target}",
                action="verify_url",
                params={"url": target},
                priority=TaskPriority.HIGH
            )
        else:
            return self.add_task(
                name=f"Verify File: {target}",
                description=f"Verify local file {target}",
                action="verify_file",
                params={"file_path": target},
                priority=TaskPriority.HIGH
            )

    # ==================== EXECUTION ====================

    async def execute_task(self, task: LoopTask) -> Dict:
        """Execute a single task"""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        self.current_task = task

        result = {
            "success": False,
            "output": None,
            "error": None
        }

        try:
            action = task.action
            params = task.params

            # Execute action
            if action == "verify_url":
                verification = await self.verifier.verify_url(params["url"], task.id)
                result["success"] = verification.get("success", False)
                result["output"] = verification
                task.verification = verification

            elif action == "verify_file":
                verification = self.verifier.verify_file(params["file_path"], task.id)
                result["success"] = verification.get("success", False)
                result["output"] = verification
                task.verification = verification

            elif action == "run_command":
                proc = subprocess.run(
                    params.get("command", ""),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=params.get("timeout", 60)
                )
                result["success"] = proc.returncode == 0
                result["output"] = {"stdout": proc.stdout, "stderr": proc.stderr}

            elif action == "run_python":
                # Execute Python code
                local_vars = {}
                exec(params.get("code", "pass"), {}, local_vars)
                result["success"] = True
                result["output"] = local_vars.get("result", "Executed")

            elif action == "learn_from_input":
                # Direct learning action
                learning = self.improver.learn_from_input(
                    input_type=params.get("input_type", "general"),
                    content=params.get("content", ""),
                    value_score=params.get("value_score", 0.5)
                )
                result["success"] = True
                result["output"] = learning

            else:
                result["error"] = f"Unknown action: {action}"

        except Exception as e:
            result["error"] = str(e)
            task.retry_count += 1

        task.result = result
        task.completed_at = datetime.now().isoformat()

        if result["success"]:
            task.status = TaskStatus.COMPLETED
        elif task.retry_count < task.max_retries:
            task.status = TaskStatus.PENDING  # Retry
        else:
            task.status = TaskStatus.FAILED

        return result

    async def verify_and_learn(self, task: LoopTask) -> Dict:
        """Verify result và learn from it"""
        task.status = TaskStatus.VERIFYING

        # Analyze
        analysis = self.analyzer.analyze_result(task, task.verification)

        # Learn
        task.status = TaskStatus.LEARNING
        for learning in analysis.get("learnings", []):
            task.learnings.append(learning.get("content", ""))

            # Trigger callback
            if self.on_learning:
                self.on_learning(task, learning)

        # Generate improvements
        suggestions = self.analyzer.generate_improvement_suggestions(analysis)

        # Record in feedback manager
        if task.status == TaskStatus.COMPLETED:
            self.feedback_manager.record_approval(
                action=task.action,
                details={"task_id": task.id, "result": task.result}
            )
        else:
            self.feedback_manager.record_denial(
                action=task.action,
                reason=task.result.get("error", "Unknown"),
                details={"task_id": task.id}
            )

        # Trigger callback
        if self.on_task_complete:
            self.on_task_complete(task, analysis)

        return analysis

    # ==================== MAIN LOOP ====================

    async def run_single_cycle(self) -> Dict:
        """Run one cycle of the loop"""
        cycle_result = {
            "timestamp": datetime.now().isoformat(),
            "tasks_processed": 0,
            "learnings": 0,
            "improvements": 0
        }

        if not self.task_queue:
            return cycle_result

        # Get next task
        task = self.task_queue.pop(0)
        self._save_state()

        # Execute
        result = await self.execute_task(task)

        # Verify & Learn
        if task.verification or result.get("output"):
            analysis = await self.verify_and_learn(task)
            cycle_result["learnings"] = len(analysis.get("learnings", []))
            cycle_result["improvements"] = len(analysis.get("improvements", []))

        # Move to completed
        self.completed_tasks.append(task)

        # Save completed
        completed_file = self.data_dir / "completed_tasks.json"
        with open(completed_file, 'w') as f:
            json.dump([asdict(t) for t in self.completed_tasks[-100:]], f, indent=2, default=str)

        cycle_result["tasks_processed"] = 1

        return cycle_result

    def _run_loop(self):
        """Main loop thread"""
        asyncio.set_event_loop(asyncio.new_event_loop())

        while self.running:
            try:
                # Run cycle
                cycle = asyncio.run(self.run_single_cycle())

                # Log
                if cycle["tasks_processed"] > 0:
                    self._log(f"Cycle completed: {cycle}")

                # Wait before next cycle
                time.sleep(1)

            except Exception as e:
                self._log(f"Loop error: {e}")
                time.sleep(5)

    def _log(self, message: str):
        """Log message"""
        log_file = self.data_dir / "loop.log"
        timestamp = datetime.now().isoformat()
        with open(log_file, 'a') as f:
            f.write(f"[{timestamp}] {message}\n")
        print(f"[LOOP] {message}")

    # ==================== CONTROL ====================

    def start(self):
        """Start autonomous loop"""
        if self.running:
            return {"status": "already_running"}

        self.running = True
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        self._log("Autonomous loop started")

        return {
            "status": "started",
            "pending_tasks": len(self.task_queue),
            "completed_tasks": len(self.completed_tasks)
        }

    def stop(self):
        """Stop autonomous loop"""
        self.running = False
        self._log("Autonomous loop stopped")
        return {"status": "stopped"}

    def get_status(self) -> Dict:
        """Get current status"""
        return {
            "running": self.running,
            "pending_tasks": len(self.task_queue),
            "completed_tasks": len(self.completed_tasks),
            "current_task": asdict(self.current_task) if self.current_task else None,
            "last_updated": datetime.now().isoformat()
        }

    def get_stats(self) -> Dict:
        """Get comprehensive stats"""
        # From improver
        improver_stats = self.improver.get_stats()

        # From feedback
        feedback_stats = self.feedback_manager.get_feedback_summary()

        return {
            "loop": {
                "pending_tasks": len(self.task_queue),
                "completed_tasks": len(self.completed_tasks),
                "success_rate": sum(1 for t in self.completed_tasks if t.status == TaskStatus.COMPLETED) / max(len(self.completed_tasks), 1)
            },
            "learning": improver_stats,
            "feedback": feedback_stats
        }


# ==================== CONVENIENCE FUNCTIONS ====================

_loop_instance: Optional[AutonomousLoop] = None


def get_loop() -> AutonomousLoop:
    """Get singleton loop instance"""
    global _loop_instance
    if _loop_instance is None:
        _loop_instance = AutonomousLoop()
    return _loop_instance


def add_task(name: str, description: str, action: str, params: Dict = None, priority: TaskPriority = TaskPriority.MEDIUM) -> LoopTask:
    """Add task to autonomous loop"""
    return get_loop().add_task(name, description, action, params, priority)


def add_verification(target: str, task_type: str = "url") -> LoopTask:
    """Add verification task"""
    return get_loop().add_verification_task(target, task_type)


def start_loop() -> Dict:
    """Start autonomous loop"""
    return get_loop().start()


def stop_loop() -> Dict:
    """Stop autonomous loop"""
    return get_loop().stop()


def loop_status() -> Dict:
    """Get loop status"""
    return get_loop().get_status()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 60)
    print("AUTONOMOUS LEARNING LOOP SYSTEM")
    print("=" * 60)

    loop = get_loop()

    # Add some test tasks
    print("\nAdding test tasks...")

    loop.add_task(
        name="Learn from user feedback",
        description="Process and learn from recent feedback",
        action="learn_from_input",
        params={
            "input_type": "feedback",
            "content": "User prefers autonomous execution without over-confirming",
            "value_score": 0.9
        },
        priority=TaskPriority.HIGH
    )

    # Print status
    print("\nCurrent status:")
    print(json.dumps(loop.get_status(), indent=2, default=str))

    # Run one cycle
    print("\nRunning single cycle...")
    result = asyncio.run(loop.run_single_cycle())
    print(json.dumps(result, indent=2, default=str))

    # Get stats
    print("\nStats:")
    print(json.dumps(loop.get_stats(), indent=2, default=str))
