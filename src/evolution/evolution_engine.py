"""
SELF-EVOLVING BACKGROUND ENGINE
================================
Chạy ngầm 24/7, tự học từ MỌI interaction, tự diagnose, tự cải tiến.

MÔ PHỎNG CON NGƯỜI HỌC VIỆC:
┌────────────────────────────────────────────────────────────────┐
│  Lần 1: 1 giờ    → Học pattern                                │
│  Lần 2: 30 phút  → Optimize                                   │
│  Lần 3: 5 phút   → Master                                     │
│  Lần 4: Bàn giao → Delegate                                   │
└────────────────────────────────────────────────────────────────┘

CAPTURE TỪ MỌI NGUỒN:
- User input/conversation
- Task execution results
- Errors/failures
- Performance metrics
- Feedback (explicit & implicit)
"""

import json
import os
import sys
import time
import threading
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import hashlib
import re

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "evolution"
MEMORY_DIR = PROJECT_ROOT / "data" / "memory"


@dataclass
class LearningEvent:
    """Single learning event"""
    id: str
    type: str  # input, output, error, feedback, diagnose, improvement
    source: str  # conversation, task, system, user
    content: str
    context: Dict
    timestamp: str
    session_id: str
    processed: bool = False


@dataclass
class Pattern:
    """Learned pattern"""
    id: str
    name: str
    pattern_type: str  # task, error, optimization, behavior
    occurrences: int
    first_seen: str
    last_seen: str
    avg_duration_ms: float
    success_rate: float
    solution: str  # How to handle this pattern
    auto_fix: bool  # Can auto-fix?
    delegate_ready: bool  # Ready to delegate?


@dataclass
class SkillLevel:
    """Skill progression tracking"""
    skill_name: str
    level: int  # 1-10
    total_executions: int
    total_failures: int
    avg_time_ms: float
    best_time_ms: float
    last_improvement: str
    mastered: bool
    can_delegate: bool


class EvolutionEngine:
    """
    Self-Evolving Background Engine
    - Chạy ngầm 24/7
    - Capture MỌI interaction
    - Detect patterns
    - Self-diagnose issues
    - Auto-improve
    - Track skill progression
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Storage
        self.events_file = self.data_dir / "events.json"
        self.patterns_file = self.data_dir / "patterns.json"
        self.skills_file = self.data_dir / "skills.json"
        self.diagnostics_file = self.data_dir / "diagnostics.json"
        self.evolution_log = self.data_dir / "evolution.log"

        # In-memory state
        self.events: List[LearningEvent] = []
        self.patterns: Dict[str, Pattern] = {}
        self.skills: Dict[str, SkillLevel] = {}
        self.diagnostics: List[Dict] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Background thread
        self.running = False
        self._thread: Optional[threading.Thread] = None

        # Load existing data
        self._load()

    def _load(self):
        """Load all data"""
        if self.events_file.exists():
            try:
                with open(self.events_file, 'r') as f:
                    data = json.load(f)
                    self.events = [LearningEvent(**e) for e in data.get("events", [])[-5000:]]
            except:
                pass

        if self.patterns_file.exists():
            try:
                with open(self.patterns_file, 'r') as f:
                    data = json.load(f)
                    for k, v in data.get("patterns", {}).items():
                        self.patterns[k] = Pattern(**v)
            except:
                pass

        if self.skills_file.exists():
            try:
                with open(self.skills_file, 'r') as f:
                    data = json.load(f)
                    for k, v in data.get("skills", {}).items():
                        self.skills[k] = SkillLevel(**v)
            except:
                pass

    def _save(self):
        """Save all data"""
        with open(self.events_file, 'w') as f:
            json.dump({
                "events": [asdict(e) for e in self.events[-5000:]],
                "last_updated": datetime.now().isoformat()
            }, f, indent=2, default=str)

        with open(self.patterns_file, 'w') as f:
            json.dump({
                "patterns": {k: asdict(v) for k, v in self.patterns.items()},
                "last_updated": datetime.now().isoformat()
            }, f, indent=2, default=str)

        with open(self.skills_file, 'w') as f:
            json.dump({
                "skills": {k: asdict(v) for k, v in self.skills.items()},
                "last_updated": datetime.now().isoformat()
            }, f, indent=2, default=str)

    def _log(self, message: str, level: str = "INFO"):
        """Log to evolution log"""
        timestamp = datetime.now().isoformat()
        log_line = f"[{timestamp}] [{level}] {message}\n"
        with open(self.evolution_log, 'a') as f:
            f.write(log_line)

    # ==================== CAPTURE ====================

    def capture_input(self, content: str, source: str = "conversation", context: Dict = None):
        """Capture user input"""
        event = LearningEvent(
            id=self._generate_id("input"),
            type="input",
            source=source,
            content=content[:2000],
            context=context or {},
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id
        )
        self.events.append(event)
        self._process_event(event)
        self._log(f"Captured input: {content[:100]}...")

    def capture_output(self, content: str, source: str = "task", context: Dict = None):
        """Capture system output"""
        event = LearningEvent(
            id=self._generate_id("output"),
            type="output",
            source=source,
            content=content[:2000],
            context=context or {},
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id
        )
        self.events.append(event)
        self._process_event(event)

    def capture_error(self, error: str, context: Dict = None, auto_fix_attempted: bool = False):
        """Capture error/failure"""
        event = LearningEvent(
            id=self._generate_id("error"),
            type="error",
            source="system",
            content=error[:2000],
            context=context or {},
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id
        )
        self.events.append(event)

        # Auto-diagnose
        self._diagnose_error(error, context)

        self._log(f"Captured error: {error[:100]}...", "ERROR")

    def capture_feedback(self, feedback: str, is_positive: bool, context: Dict = None):
        """Capture user feedback"""
        event = LearningEvent(
            id=self._generate_id("feedback"),
            type="feedback",
            source="user",
            content=feedback[:2000],
            context={"is_positive": is_positive, **(context or {})},
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id
        )
        self.events.append(event)
        self._process_feedback(event)
        self._log(f"Captured feedback ({'positive' if is_positive else 'negative'}): {feedback[:100]}...")

    def capture_task(self, task_name: str, duration_ms: float, success: bool, details: Dict = None):
        """Capture task execution"""
        event = LearningEvent(
            id=self._generate_id("task"),
            type="task",
            source="execution",
            content=task_name,
            context={
                "duration_ms": duration_ms,
                "success": success,
                "details": details or {}
            },
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id
        )
        self.events.append(event)
        self._update_skill(task_name, duration_ms, success)
        self._process_event(event)

    # ==================== PROCESSING ====================

    def _process_event(self, event: LearningEvent):
        """Process event and extract patterns"""
        content = event.content.lower()
        context = event.context

        # Detect patterns
        pattern_key = self._extract_pattern_key(event)
        if pattern_key:
            if pattern_key in self.patterns:
                # Update existing pattern
                p = self.patterns[pattern_key]
                p.occurrences += 1
                p.last_seen = event.timestamp
                if context.get("duration_ms"):
                    p.avg_duration_ms = (p.avg_duration_ms + context["duration_ms"]) / 2
            else:
                # New pattern
                self.patterns[pattern_key] = Pattern(
                    id=pattern_key,
                    name=self._generate_pattern_name(event),
                    pattern_type=self._classify_pattern(event),
                    occurrences=1,
                    first_seen=event.timestamp,
                    last_seen=event.timestamp,
                    avg_duration_ms=context.get("duration_ms", 0),
                    success_rate=1.0 if context.get("success", True) else 0.0,
                    solution="",
                    auto_fix=False,
                    delegate_ready=False
                )

        event.processed = True

    def _process_feedback(self, event: LearningEvent):
        """Process feedback and learn"""
        is_positive = event.context.get("is_positive", True)

        if not is_positive:
            # Negative feedback - cần cải tiến
            self._generate_improvement(event.content)

    def _extract_pattern_key(self, event: LearningEvent) -> Optional[str]:
        """Extract pattern key from event"""
        content = event.content.lower()

        # Task patterns
        if event.type == "task":
            return f"task:{event.content}"

        # Error patterns
        if event.type == "error":
            # Extract error type
            error_patterns = [
                r"(\w+Error)",
                r"(\w+Exception)",
                r"failed to (\w+)",
                r"cannot (\w+)",
            ]
            for pattern in error_patterns:
                match = re.search(pattern, content)
                if match:
                    return f"error:{match.group(1)}"

        # Input patterns
        if event.type == "input":
            # Detect task requests
            task_keywords = ["fix", "add", "create", "implement", "update", "test", "deploy"]
            for kw in task_keywords:
                if kw in content:
                    return f"request:{kw}"

        return None

    def _generate_pattern_name(self, event: LearningEvent) -> str:
        """Generate human-readable pattern name"""
        return f"{event.type.title()}: {event.content[:50]}"

    def _classify_pattern(self, event: LearningEvent) -> str:
        """Classify pattern type"""
        if event.type == "task":
            return "task"
        elif event.type == "error":
            return "error"
        elif event.type == "feedback":
            return "behavior"
        else:
            return "optimization"

    # ==================== SKILL TRACKING ====================

    def _update_skill(self, skill_name: str, duration_ms: float, success: bool):
        """Update skill progression"""
        if skill_name in self.skills:
            skill = self.skills[skill_name]
            skill.total_executions += 1
            if not success:
                skill.total_failures += 1

            # Update times
            skill.avg_time_ms = (skill.avg_time_ms * (skill.total_executions - 1) + duration_ms) / skill.total_executions
            if duration_ms < skill.best_time_ms or skill.best_time_ms == 0:
                skill.best_time_ms = duration_ms

            # Update level (1-10)
            success_rate = (skill.total_executions - skill.total_failures) / skill.total_executions
            speed_improvement = skill.best_time_ms / max(skill.avg_time_ms, 1)

            # Level up logic
            new_level = min(10, int(
                1 +  # Base
                min(3, skill.total_executions // 5) +  # Experience
                min(3, int(success_rate * 3)) +  # Success rate
                min(3, int(speed_improvement * 3))  # Speed improvement
            ))

            if new_level > skill.level:
                skill.level = new_level
                skill.last_improvement = datetime.now().isoformat()
                self._log(f"SKILL LEVEL UP: {skill_name} -> Level {new_level}")

            # Master check
            if skill.level >= 8 and success_rate >= 0.9:
                skill.mastered = True

            # Delegate check
            if skill.level >= 9 and skill.total_executions >= 20:
                skill.can_delegate = True
                self._log(f"SKILL READY FOR DELEGATION: {skill_name}")

        else:
            # New skill
            self.skills[skill_name] = SkillLevel(
                skill_name=skill_name,
                level=1,
                total_executions=1,
                total_failures=0 if success else 1,
                avg_time_ms=duration_ms,
                best_time_ms=duration_ms,
                last_improvement=datetime.now().isoformat(),
                mastered=False,
                can_delegate=False
            )

    # ==================== DIAGNOSIS ====================

    def _diagnose_error(self, error: str, context: Dict = None):
        """Diagnose error and find solution"""
        diagnosis = {
            "timestamp": datetime.now().isoformat(),
            "error": error[:500],
            "context": context or {},
            "diagnosis": self._analyze_error(error),
            "suggested_fix": self._suggest_fix(error),
            "pattern_match": self._find_similar_error(error)
        }

        self.diagnostics.append(diagnosis)

        # Save diagnostics
        with open(self.diagnostics_file, 'w') as f:
            json.dump(self.diagnostics[-100:], f, indent=2, default=str)

    def _analyze_error(self, error: str) -> str:
        """Analyze error to find root cause"""
        error_lower = error.lower()

        analyses = {
            "importerror": "Module import issue - check dependencies or path",
            "module not found": "Missing module - install with pip/npm",
            "permission denied": "Permission issue - check file/directory permissions",
            "timeout": "Operation timed out - optimize or increase timeout",
            "connection refused": "Connection failed - check if service is running",
            "syntax error": "Code syntax issue - check code structure",
            "null pointer": "Null/None reference - add null checks",
            "file not found": "File missing - verify path or create file",
        }

        for key, analysis in analyses.items():
            if key in error_lower:
                return analysis

        return "Unknown error pattern - needs manual investigation"

    def _suggest_fix(self, error: str) -> str:
        """Suggest fix for error"""
        error_lower = error.lower()

        fixes = {
            "importerror": "try: from module import X\nexcept: pip install module",
            "permission denied": "chmod +x file OR run with sudo",
            "timeout": "Increase timeout or optimize operation",
            "connection refused": "Start the service first",
            "file not found": "Create file or check path",
        }

        for key, fix in fixes.items():
            if key in error_lower:
                return fix

        return "No auto-fix available"

    def _find_similar_error(self, error: str) -> Optional[str]:
        """Find similar past error"""
        error_words = set(error.lower().split())

        for pattern_key, pattern in self.patterns.items():
            if pattern_key.startswith("error:"):
                pattern_words = set(pattern.name.lower().split())
                similarity = len(error_words & pattern_words) / max(len(error_words), 1)
                if similarity > 0.5:
                    return pattern.solution

        return None

    # ==================== IMPROVEMENT ====================

    def _generate_improvement(self, trigger: str):
        """Generate improvement from negative feedback or error"""
        improvement = {
            "timestamp": datetime.now().isoformat(),
            "trigger": trigger,
            "analysis": self._analyze_improvement(trigger),
            "actions": self._suggest_improvement_actions(trigger)
        }

        # Save to improvements file
        improvements_file = self.data_dir / "improvements.json"
        improvements = []
        if improvements_file.exists():
            try:
                with open(improvements_file, 'r') as f:
                    improvements = json.load(f)
            except:
                pass

        improvements.append(improvement)
        with open(improvements_file, 'w') as f:
            json.dump(improvements[-100:], f, indent=2, default=str)

        self._log(f"Generated improvement: {trigger[:100]}...")

    def _analyze_improvement(self, trigger: str) -> str:
        """Analyze what needs improvement"""
        trigger_lower = trigger.lower()

        if "confirm" in trigger_lower or "hỏi" in trigger_lower:
            return "Over-confirming detected - need more autonomous execution"

        if "slow" in trigger_lower or "lâu" in trigger_lower:
            return "Performance issue - need optimization"

        if "error" in trigger_lower or "fail" in trigger_lower:
            return "Reliability issue - need better error handling"

        return "General improvement needed"

    def _suggest_improvement_actions(self, trigger: str) -> List[str]:
        """Suggest specific actions"""
        actions = []
        trigger_lower = trigger.lower()

        if "confirm" in trigger_lower:
            actions.append("Reduce confirmation prompts")
            actions.append("Auto-execute for known safe operations")

        if "slow" in trigger_lower:
            actions.append("Profile and optimize slow operations")
            actions.append("Add caching for repeated operations")

        return actions

    # ==================== BACKGROUND LOOP ====================

    def _background_loop(self):
        """Background processing loop"""
        while self.running:
            try:
                # Process unprocessed events
                for event in self.events[-100:]:
                    if not event.processed:
                        self._process_event(event)

                # Auto-save every 30 seconds
                self._save()

                # Sleep
                time.sleep(5)

            except Exception as e:
                self._log(f"Background loop error: {e}", "ERROR")
                time.sleep(10)

    def start(self):
        """Start background engine"""
        if self.running:
            return

        self.running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()

        self._log("Evolution Engine STARTED")
        print(f"[EVOLUTION] Engine started - Session: {self.session_id}")

    def stop(self):
        """Stop background engine"""
        self.running = False
        self._save()
        self._log("Evolution Engine STOPPED")

    # ==================== QUERIES ====================

    def get_skill_report(self) -> Dict:
        """Get skill progression report"""
        return {
            skill_name: {
                "level": skill.level,
                "executions": skill.total_executions,
                "success_rate": (skill.total_executions - skill.total_failures) / max(skill.total_executions, 1),
                "avg_time_ms": skill.avg_time_ms,
                "best_time_ms": skill.best_time_ms,
                "mastered": skill.mastered,
                "can_delegate": skill.can_delegate
            }
            for skill_name, skill in self.skills.items()
        }

    def get_patterns_report(self) -> Dict:
        """Get patterns report"""
        return {
            "total_patterns": len(self.patterns),
            "by_type": defaultdict(int, {p.pattern_type: p.occurrences for p in self.patterns.values()}),
            "top_patterns": sorted(
                [{"name": p.name, "occurrences": p.occurrences} for p in self.patterns.values()],
                key=lambda x: x["occurrences"],
                reverse=True
            )[:10]
        }

    def get_evolution_stats(self) -> Dict:
        """Get comprehensive evolution stats"""
        return {
            "session_id": self.session_id,
            "running": self.running,
            "total_events": len(self.events),
            "total_patterns": len(self.patterns),
            "total_skills": len(self.skills),
            "mastered_skills": sum(1 for s in self.skills.values() if s.mastered),
            "delegate_ready_skills": sum(1 for s in self.skills.values() if s.can_delegate),
            "recent_diagnostics": len(self.diagnostics[-10:])
        }

    def _generate_id(self, prefix: str) -> str:
        """Generate unique ID"""
        return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.events)}"


# ==================== GLOBAL INSTANCE ====================

_engine: Optional[EvolutionEngine] = None


def get_engine() -> EvolutionEngine:
    """Get singleton engine instance"""
    global _engine
    if _engine is None:
        _engine = EvolutionEngine()
    return _engine


def start_evolution():
    """Start evolution engine"""
    engine = get_engine()
    engine.start()
    return engine


def capture_input(content: str, source: str = "conversation", context: Dict = None):
    """Capture input"""
    get_engine().capture_input(content, source, context)


def capture_output(content: str, source: str = "task", context: Dict = None):
    """Capture output"""
    get_engine().capture_output(content, source, context)


def capture_error(error: str, context: Dict = None):
    """Capture error"""
    get_engine().capture_error(error, context)


def capture_feedback(feedback: str, is_positive: bool, context: Dict = None):
    """Capture feedback"""
    get_engine().capture_feedback(feedback, is_positive, context)


def capture_task(task_name: str, duration_ms: float, success: bool, details: Dict = None):
    """Capture task execution"""
    get_engine().capture_task(task_name, duration_ms, success, details)


def get_evolution_stats() -> Dict:
    """Get evolution stats"""
    return get_engine().get_evolution_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 60)
    print("SELF-EVOLVING BACKGROUND ENGINE")
    print("=" * 60)
    print()
    print("Starting engine...")

    engine = start_evolution()

    print("Engine running in background!")
    print()
    print("Capturing test events...")

    # Test captures
    capture_input("Fix the bug in login", "conversation")
    capture_output("Bug fixed successfully", "task", {"duration_ms": 5000})

    import time
    time.sleep(1)

    # Stats
    print()
    print("Stats:", engine.get_evolution_stats())
    print()
    print("Skills:", engine.get_skill_report())
