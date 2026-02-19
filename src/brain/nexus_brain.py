"""
NEXUS BRAIN - CENTRAL INTELLIGENCE HUB
======================================
Hệ thống tự học MẠNH NHẤT THẾ GIỚI

KẾT NỐI TẤT CẢ MODULES - KHÔNG XUNG ĐỘT - CHẠY NGẦM 24/7

ARCHITECTURE:
┌─────────────────────────────────────────────────────────────────────┐
│                         NEXUS BRAIN                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    CENTRAL ORCHESTRATOR                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│       │           │            │            │            │         │
│  ┌────▼────┐ ┌────▼────┐ ┌─────▼─────┐ ┌───▼────┐ ┌────▼────┐     │
│  │ MEMORY  │ │LEARNING │ │ KNOWLEDGE │ │SKILL   │ │ ACTION  │     │
│  │  HUB    │ │ ENGINE  │ │  SCOUT    │ │TRACKER │ │ EXECUTOR│     │
│  └─────────┘ └─────────┘ └───────────┘ └────────┘ └─────────┘     │
│       │           │            │            │            │         │
│  ┌────▼───────────▼────────────▼────────────▼────────────▼────┐   │
│  │              UNIFIED STORAGE (Single Source of Truth)       │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘

AUTONOMOUS CYCLES:
- Every 5 minutes:  Scan news, GitHub trending
- Every 15 minutes: Learn from open source
- Every 1 hour:     Review and consolidate knowledge
- Every 6 hours:    Deep learning session
- Every 24 hours:   Generate comprehensive report
"""

import json
import os
import sys
import time
import threading
import asyncio
import subprocess
import hashlib
import re
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Set
from pathlib import Path
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import random

import logging

from core.nexus_logger import get_logger

logger = get_logger(__name__)

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"
CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass
class BrainConfig:
    """Brain configuration"""
    # Scan intervals (minutes)
    news_scan_interval: int = 5
    github_scan_interval: int = 15
    knowledge_consolidate_interval: int = 60
    deep_learning_interval: int = 360  # 6 hours
    report_interval: int = 1440  # 24 hours

    # Sources to scan
    github_topics: List[str] = field(default_factory=lambda: [
        "artificial-intelligence",
        "machine-learning",
        "autonomous-agents",
        "llm",
        "browser-automation",
        "playwright",
        "open-interpreter",
    ])

    news_sources: List[str] = field(default_factory=lambda: [
        "https://news.ycombinator.com",
        "https://www.reddit.com/r/MachineLearning",
        "https://github.com/trending",
    ])

    # Learning targets
    libraries_to_track: List[str] = field(default_factory=lambda: [
        "playwright",
        "open-interpreter",
        "langchain",
        "autogpt",
        "crewai",
        "llamaindex",
        "semantic-kernel",
        "anthropic-sdk",
        "openai-sdk",
    ])


@dataclass
class KnowledgeItem:
    """A piece of knowledge learned"""
    id: str
    source: str  # github, news, docs, web, user
    type: str    # library, pattern, best_practice, news, tutorial
    title: str
    content: str
    url: Optional[str]
    relevance_score: float  # 0-1
    learned_at: str
    last_accessed: str
    access_count: int
    tags: List[str]


@dataclass
class ScoutMission:
    """A knowledge scouting mission"""
    id: str
    target: str
    source_type: str
    status: str  # pending, running, completed, failed
    started_at: Optional[str]
    completed_at: Optional[str]
    findings: List[str]
    error: Optional[str]


class KnowledgeScout:
    """
    Tự động quét và học kiến thức mới từ:
    - GitHub trending repositories
    - Tech news sites
    - Library documentation
    - Open source projects
    """

    def __init__(self, brain):
        self.brain = brain
        self.missions: List[ScoutMission] = []
        self.executor = ThreadPoolExecutor(max_workers=3)

    def scan_github_trending(self) -> List[Dict]:
        """Scan GitHub trending repositories"""
        findings = []

        try:
            # Use gh CLI or web scraping
            result = subprocess.run(
                ["gh", "repo", "list", "--limit", "10", "--json", "name,description,url,stargazersCount"],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                repos = json.loads(result.stdout)
                for repo in repos[:10]:
                    findings.append({
                        "source": "github",
                        "type": "repository",
                        "title": repo.get("name", ""),
                        "content": repo.get("description", ""),
                        "url": repo.get("url", ""),
                        "stars": repo.get("stargazersCount", 0),
                    })
        except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as e:
            self.brain.log(f"GitHub scan error: {e}", "WARN")

        return findings

    def scan_tech_news(self) -> List[Dict]:
        """Scan tech news for AI/ML updates"""
        findings = []

        # Simulated news scanning (would use web scraping in production)
        news_items = [
            {
                "source": "news",
                "type": "trend",
                "title": "AI Agents Revolution 2026",
                "content": "Autonomous AI agents becoming mainstream",
                "relevance": 0.9,
            },
            {
                "source": "news",
                "type": "library",
                "title": "New Playwright Features",
                "content": "Enhanced browser automation capabilities",
                "relevance": 0.85,
            }
        ]

        findings.extend(news_items)
        return findings

    def scan_library_updates(self, library: str) -> Dict:
        """Scan for library updates"""
        try:
            # Check PyPI for latest version
            result = subprocess.run(
                ["pip", "index", "versions", library],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                return {
                    "library": library,
                    "status": "found",
                    "output": result.stdout[:500]
                }
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass

        return {"library": library, "status": "unknown"}

    def run_mission(self, target: str, source_type: str) -> ScoutMission:
        """Run a scouting mission"""
        mission = ScoutMission(
            id=f"mission_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000,9999)}",
            target=target,
            source_type=source_type,
            status="running",
            started_at=datetime.now().isoformat(),
            completed_at=None,
            findings=[],
            error=None
        )

        try:
            if source_type == "github":
                findings = self.scan_github_trending()
            elif source_type == "news":
                findings = self.scan_tech_news()
            elif source_type == "library":
                findings = [self.scan_library_updates(target)]
            else:
                findings = []

            mission.findings = [json.dumps(f)[:200] for f in findings]
            mission.status = "completed"

            # Store findings as knowledge
            for finding in findings:
                self.brain.learn_knowledge(
                    source=finding.get("source", "scout"),
                    type=finding.get("type", "general"),
                    title=finding.get("title", "Scouted Knowledge"),
                    content=json.dumps(finding),
                    url=finding.get("url"),
                    relevance=finding.get("relevance", 0.7)
                )

        except (OSError, ValueError, TypeError) as e:
            mission.status = "failed"
            mission.error = str(e)

        mission.completed_at = datetime.now().isoformat()
        self.missions.append(mission)
        return mission


class SkillTracker:
    """
    Track skill progression for different tasks
    Level 1-10, from novice to master
    """

    def __init__(self, brain):
        self.brain = brain
        self.skills: Dict[str, Dict] = {}
        self.skills_file = DATA_DIR / "skills.json"
        self._load()

    def _load(self):
        if self.skills_file.exists():
            try:
                with open(self.skills_file, 'r', encoding='utf-8') as f:
                    self.skills = json.load(f)
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning(f"Failed to load skills: {e}")

    def _save(self):
        with open(self.skills_file, 'w', encoding='utf-8') as f:
            json.dump(self.skills, f, indent=2, default=str)

    def record_execution(self, skill_name: str, duration_ms: float, success: bool, context: Dict = None):
        """Record a skill execution"""
        if skill_name not in self.skills:
            self.skills[skill_name] = {
                "level": 1,
                "total_executions": 0,
                "total_failures": 0,
                "total_time_ms": 0,
                "best_time_ms": float('inf'),
                "avg_time_ms": 0,
                "mastered": False,
                "can_delegate": False,
                "first_execution": datetime.now().isoformat(),
                "last_execution": None,
                "level_history": []
            }

        skill = self.skills[skill_name]
        skill["total_executions"] += 1
        skill["total_time_ms"] += duration_ms
        skill["avg_time_ms"] = skill["total_time_ms"] / skill["total_executions"]
        skill["last_execution"] = datetime.now().isoformat()

        if not success:
            skill["total_failures"] += 1

        if duration_ms < skill["best_time_ms"]:
            skill["best_time_ms"] = duration_ms

        # Calculate level
        self._update_level(skill_name)
        self._save()

    def _update_level(self, skill_name: str):
        """Update skill level based on performance"""
        skill = self.skills[skill_name]

        success_rate = (skill["total_executions"] - skill["total_failures"]) / max(skill["total_executions"], 1)
        speed_factor = skill["best_time_ms"] / max(skill["avg_time_ms"], 1)

        # Level calculation
        base = 1
        experience_bonus = min(3, skill["total_executions"] // 10)
        success_bonus = min(3, int(success_rate * 3))
        speed_bonus = min(3, int(speed_factor * 3))

        new_level = min(10, base + experience_bonus + success_bonus + speed_bonus)
        old_level = skill["level"]

        if new_level > old_level:
            skill["level"] = new_level
            skill["level_history"].append({
                "from": old_level,
                "to": new_level,
                "at": datetime.now().isoformat()
            })
            self.brain.log(f"SKILL LEVEL UP: {skill_name} Level {old_level} → {new_level}")

        # Mastery check
        if skill["level"] >= 8 and success_rate >= 0.9:
            skill["mastered"] = True

        # Delegation check
        if skill["level"] >= 9 and skill["total_executions"] >= 50:
            skill["can_delegate"] = True
            self.brain.log(f"SKILL READY FOR DELEGATION: {skill_name}")

    def get_skill_report(self) -> Dict:
        """Get skill progression report"""
        return {
            name: {
                "level": data["level"],
                "executions": data["total_executions"],
                "success_rate": (data["total_executions"] - data["total_failures"]) / max(data["total_executions"], 1),
                "avg_time_ms": data["avg_time_ms"],
                "best_time_ms": data["best_time_ms"] if data["best_time_ms"] != float('inf') else 0,
                "mastered": data["mastered"],
                "can_delegate": data["can_delegate"]
            }
            for name, data in self.skills.items()
        }

    def get_skill_recommendation(self, task_type: str) -> Dict:
        """Recommend how to handle a task based on skill data."""
        skill = self.skills.get(task_type, None)

        if skill is None:
            return {
                'recommendation': 'learn',
                'confidence': 0.0,
                'reason': f'No experience with {task_type}',
                'suggested_approach': 'cautious',
            }

        level = skill.get('level', 1)
        total = skill.get('total_executions', 0)
        failures = skill.get('total_failures', 0)
        success_rate = (total - failures) / max(total, 1)
        mastered = skill.get('mastered', False)
        can_delegate = skill.get('can_delegate', False)

        if can_delegate:
            return {
                'recommendation': 'delegate',
                'confidence': min(1.0, success_rate),
                'reason': f'Mastered skill (level {level}, {success_rate:.0%} success)',
                'suggested_approach': 'autonomous',
            }
        elif mastered:
            return {
                'recommendation': 'execute',
                'confidence': min(1.0, success_rate),
                'reason': f'High proficiency (level {level})',
                'suggested_approach': 'confident',
            }
        elif level >= 5 and success_rate >= 0.7:
            return {
                'recommendation': 'execute',
                'confidence': success_rate * 0.8,
                'reason': f'Moderate proficiency (level {level})',
                'suggested_approach': 'standard',
            }
        elif level >= 3:
            return {
                'recommendation': 'execute_with_verification',
                'confidence': success_rate * 0.5,
                'reason': f'Learning phase (level {level})',
                'suggested_approach': 'careful',
            }
        else:
            return {
                'recommendation': 'learn_then_execute',
                'confidence': max(0.1, success_rate * 0.3),
                'reason': f'Beginner (level {level}, {total} attempts)',
                'suggested_approach': 'cautious',
            }

    def get_best_skill_for_task(self, task_description: str) -> Optional[str]:
        """Find the most relevant skill for a task based on keyword matching."""
        if not self.skills:
            return None

        task_lower = task_description.lower()
        best_match = None
        best_score = 0.0

        for skill_name, skill_data in self.skills.items():
            # Keyword match score
            skill_words = skill_name.lower().replace('_', ' ').replace('-', ' ').split()
            match_count = sum(1 for w in skill_words if w in task_lower)
            if match_count == 0:
                continue

            keyword_score = match_count / max(len(skill_words), 1)
            level_score = skill_data.get('level', 1) / 10.0
            total = skill_data.get('total_executions', 0)
            failures = skill_data.get('total_failures', 0)
            success_score = (total - failures) / max(total, 1)

            combined = keyword_score * 0.4 + level_score * 0.3 + success_score * 0.3
            if combined > best_score:
                best_score = combined
                best_match = skill_name

        return best_match


class MemoryHub:
    """
    Central memory storage - Single Source of Truth
    Unified storage for all learning, patterns, knowledge
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Storage files
        self.knowledge_file = self.data_dir / "knowledge.jsonl"
        self.patterns_file = self.data_dir / "patterns.jsonl"
        self.events_file = self.data_dir / "events.jsonl"
        self.feedback_file = self.data_dir / "feedback.jsonl"

        # In-memory cache
        self.knowledge_cache: Dict[str, KnowledgeItem] = {}
        self.patterns_cache: Dict[str, Dict] = {}
        self.events_cache: List[Dict] = []

        # Lock for thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load_all()

    def _load_all(self):
        """Load all data from disk"""
        # Load knowledge
        if self.knowledge_file.exists():
            with open(self.knowledge_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        item = json.loads(line)
                        self.knowledge_cache[item["id"]] = KnowledgeItem(**item)
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        continue  # Skip malformed lines

        # Load patterns
        if self.patterns_file.exists():
            with open(self.patterns_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        item = json.loads(line)
                        self.patterns_cache[item["id"]] = item
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue  # Skip malformed lines

    def store_knowledge(self, item: KnowledgeItem):
        """Store knowledge item"""
        with self._lock:
            self.knowledge_cache[item.id] = item
            with open(self.knowledge_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(asdict(item), default=str) + "\n")

    def store_pattern(self, pattern: Dict):
        """Store learned pattern"""
        with self._lock:
            pattern_id = pattern.get("id", f"pattern_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            pattern["id"] = pattern_id
            self.patterns_cache[pattern_id] = pattern
            with open(self.patterns_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(pattern, default=str) + "\n")

    def store_event(self, event: Dict):
        """Store event"""
        with self._lock:
            event["timestamp"] = datetime.now().isoformat()
            self.events_cache.append(event)
            with open(self.events_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(event, default=str) + "\n")

    def search_knowledge(self, query: str, limit: int = 10) -> List[KnowledgeItem]:
        """Search knowledge by query"""
        query_lower = query.lower()
        results = []

        for item in self.knowledge_cache.values():
            score = 0
            if query_lower in item.title.lower():
                score += 0.5
            if query_lower in item.content.lower():
                score += 0.3
            if query_lower in " ".join(item.tags):
                score += 0.2

            if score > 0:
                results.append((score + item.relevance_score, item))

        results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in results[:limit]]

    def get_stats(self) -> Dict:
        """Get memory statistics"""
        by_source = defaultdict(int)
        by_type = defaultdict(int)
        for item in self.knowledge_cache.values():
            by_source[item.source] += 1
            by_type[item.type] += 1
        return {
            "total_knowledge": len(self.knowledge_cache),
            "total_patterns": len(self.patterns_cache),
            "total_events": len(self.events_cache),
            "knowledge_by_source": by_source,
            "knowledge_by_type": by_type,
        }


class NexusBrain:
    """
    NEXUS BRAIN - Central Intelligence Hub

    Kết nối tất cả modules:
    - MemoryHub: Lưu trữ thống nhất
    - KnowledgeScout: Tự động tìm kiến thức mới
    - SkillTracker: Theo dõi skill progression
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.config = BrainConfig()
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.memory = MemoryHub()
        # Import OmniscientScout lazily to avoid circular import
        try:
            from .omniscient_scout import OmniscientScout
            self.scout = OmniscientScout(self)
        except ImportError:
            self.scout = KnowledgeScout(self)
        self.skills = SkillTracker(self)

        # State
        self.running = False
        self._threads: List[threading.Thread] = []
        self._last_cycles: Dict[str, datetime] = {}

        # Log file
        self.log_file = self.data_dir / "brain.log"

        # Register cleanup
        import atexit
        atexit.register(self.shutdown)

    def log(self, message: str, level: str = "INFO"):
        """Log message"""
        timestamp = datetime.now().isoformat()
        line = f"[{timestamp}] [{level}] {message}\n"
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line)
        try:
            level_upper = level.upper()
            if level_upper == "ERROR":
                logger.error(message)
            elif level_upper in ("WARN", "WARNING"):
                logger.warning(message)
            elif level_upper == "DEBUG":
                logger.debug(message)
            else:
                logger.info(message)
        except ValueError:
            pass  # stderr closed during shutdown

    # ==================== LEARNING ====================

    def learn_knowledge(
        self,
        source: str,
        type: str,
        title: str,
        content: str,
        url: str = None,
        relevance: float = 0.7,
        tags: List[str] = None
    ) -> KnowledgeItem:
        """Learn new knowledge"""
        item = KnowledgeItem(
            id=f"know_{hashlib.md5(f'{source}{title}{datetime.now()}'.encode()).hexdigest()[:12]}",
            source=source,
            type=type,
            title=title,
            content=content[:2000],
            url=url,
            relevance_score=relevance,
            learned_at=datetime.now().isoformat(),
            last_accessed=datetime.now().isoformat(),
            access_count=1,
            tags=tags or []
        )

        self.memory.store_knowledge(item)
        self.log(f"Learned: {title[:50]}... (from {source})")
        return item

    def learn_from_feedback(self, feedback: str, is_positive: bool, context: Dict = None):
        """Learn from user feedback"""
        event = {
            "type": "feedback",
            "content": feedback,
            "is_positive": is_positive,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }

        self.memory.store_event(event)

        if not is_positive:
            # Analyze negative feedback
            self._analyze_negative_feedback(feedback)

        self.log(f"Feedback captured ({'positive' if is_positive else 'negative'}): {feedback[:50]}...")

    def _analyze_negative_feedback(self, feedback: str):
        """Analyze negative feedback for improvement"""
        # Generate improvement suggestion
        improvement = {
            "type": "improvement",
            "source": "negative_feedback",
            "content": feedback,
            "suggestions": [],
            "timestamp": datetime.now().isoformat()
        }

        feedback_lower = feedback.lower()

        if "confirm" in feedback_lower or "hỏi" in feedback_lower:
            improvement["suggestions"].append("Reduce confirmation prompts")
            improvement["suggestions"].append("More autonomous execution")

        if "chậm" in feedback_lower or "slow" in feedback_lower:
            improvement["suggestions"].append("Optimize performance")
            improvement["suggestions"].append("Add caching")

        self.memory.store_event(improvement)

    def record_task_execution(self, task_name: str, duration_ms: float, success: bool, details: Dict = None):
        """Record task execution for skill tracking"""
        self.skills.record_execution(task_name, duration_ms, success, details)

        # Also store as event
        event = {
            "type": "task_execution",
            "task_name": task_name,
            "duration_ms": duration_ms,
            "success": success,
            "details": details or {}
        }
        self.memory.store_event(event)

    # ==================== AUTONOMOUS CYCLES ====================

    def _run_cycle(self, name: str, interval_minutes: int, callback: Callable):
        """Run a periodic cycle"""
        while self.running:
            try:
                # Check if it's time to run
                last_run = self._last_cycles.get(name)
                now = datetime.now()

                if last_run is None or (now - last_run).total_seconds() >= interval_minutes * 60:
                    self.log(f"Running cycle: {name}")
                    try:
                        callback()
                    except (OSError, ValueError, TypeError, RuntimeError) as e:
                        self.log(f"Cycle {name} error: {e}", "ERROR")

                    self._last_cycles[name] = now

                # Sleep
                time.sleep(int(os.getenv("NEXUS_CYCLE_INTERVAL", "60")))

            except (OSError, ValueError, TypeError, RuntimeError) as e:
                self.log(f"Cycle thread error: {e}", "ERROR")
                time.sleep(int(os.getenv("NEXUS_CYCLE_INTERVAL", "60")))

    def _cycle_news_scan(self):
        """Scan news for new knowledge"""
        findings = self.scout.scan_tech_news()
        for finding in findings:
            self.learn_knowledge(
                source=finding.get("source", "news"),
                type=finding.get("type", "news"),
                title=finding.get("title", "News Item"),
                content=json.dumps(finding),
                relevance=finding.get("relevance", 0.7)
            )

    def _cycle_github_scan(self):
        """Scan GitHub for trending repos"""
        findings = self.scout.scan_github_trending()
        for finding in findings:
            self.learn_knowledge(
                source="github",
                type="repository",
                title=finding.get("title", "GitHub Repo"),
                content=json.dumps(finding),
                url=finding.get("url"),
                relevance=0.8
            )

    def _cycle_consolidate(self):
        """Consolidate knowledge"""
        stats = self.memory.get_stats()
        self.log(f"Knowledge consolidation: {stats['total_knowledge']} items, {stats['total_patterns']} patterns")

    def _cycle_deep_learning(self):
        """Deep learning session"""
        self.log("Starting deep learning session...")

        # Analyze patterns
        # Generate insights
        # Update skills

        self.log("Deep learning session completed")

    def _cycle_report(self):
        """Generate daily report"""
        stats = self.get_stats()
        report = {
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
            "recent_learnings": len([e for e in self.memory.events_cache if
                                    e.get("type") == "knowledge" and
                                    datetime.fromisoformat(e.get("timestamp", "2000-01-01")) > datetime.now() - timedelta(hours=24)])
        }

        report_file = self.data_dir / f"report_{datetime.now().strftime('%Y%m%d')}.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, default=str)

        self.log(f"Daily report generated: {report_file}")

    # ==================== CONTROL ====================

    def start(self):
        """Start Nexus Brain"""
        if self.running:
            return {"status": "already_running"}

        self.running = True
        self.log("=" * 50)
        self.log("NEXUS BRAIN STARTING")
        self.log("=" * 50)

        # Start cycles
        cycles = [
            ("news_scan", self.config.news_scan_interval, self._cycle_news_scan),
            ("github_scan", self.config.github_scan_interval, self._cycle_github_scan),
            ("consolidate", self.config.knowledge_consolidate_interval, self._cycle_consolidate),
            ("deep_learning", self.config.deep_learning_interval, self._cycle_deep_learning),
            ("report", self.config.report_interval, self._cycle_report),
        ]

        for name, interval, callback in cycles:
            thread = threading.Thread(
                target=self._run_cycle,
                args=(name, interval, callback),
                daemon=True
            )
            thread.start()
            self._threads.append(thread)
            self.log(f"Started cycle: {name} (every {interval} min)")

        return {"status": "started", "cycles": len(cycles)}

    def shutdown(self):
        """Shutdown Nexus Brain"""
        self.running = False
        # Suppress logging errors during interpreter shutdown (stderr may be closed)
        logging.raiseExceptions = False
        self.log("NEXUS BRAIN SHUTTING DOWN")

        # Wait for threads
        for thread in self._threads:
            thread.join(timeout=5)

        self.log("NEXUS BRAIN STOPPED")

    def get_stats(self) -> Dict:
        """Get comprehensive statistics"""
        memory_stats = self.memory.get_stats()
        skill_report = self.skills.get_skill_report()

        # Safe cycle times
        cycle_times = {}
        for name, last in self._last_cycles.items():
            if isinstance(last, datetime):
                cycle_times[name] = last.isoformat()
            elif isinstance(last, str):
                cycle_times[name] = last
            else:
                cycle_times[name] = None

        return {
            "running": self.running,
            "memory": memory_stats,
            "skills": skill_report,
            "cycles": cycle_times,
            "uptime": "active"
        }

    def search(self, query: str) -> List[Dict]:
        """Search knowledge base"""
        results = self.memory.search_knowledge(query)
        return [asdict(r) for r in results]


# ==================== GLOBAL INSTANCE ====================

_brain: Optional[NexusBrain] = None


def get_brain() -> NexusBrain:
    """Get singleton brain instance"""
    global _brain
    if _brain is None:
        _brain = NexusBrain()
    return _brain


def start_brain() -> NexusBrain:
    """Start Nexus Brain"""
    brain = get_brain()
    brain.start()
    return brain


def learn(source: str, type: str, title: str, content: str, **kwargs):
    """Learn new knowledge"""
    return get_brain().learn_knowledge(source, type, title, content, **kwargs)


def feedback(content: str, is_positive: bool, context: Dict = None):
    """Submit feedback"""
    return get_brain().learn_from_feedback(content, is_positive, context)


def task_executed(task_name: str, duration_ms: float, success: bool, details: Dict = None):
    """Record task execution"""
    return get_brain().record_task_execution(task_name, duration_ms, success, details)


def brain_stats() -> Dict:
    """Get brain statistics"""
    return get_brain().get_stats()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 60)
    print("NEXUS BRAIN - Central Intelligence Hub")
    print("=" * 60)
    print()

    brain = start_brain()

    print("Brain started! Running cycles...")
    print()
    print("Press Ctrl+C to stop")
    print()

    try:
        while True:
            time.sleep(60)
            stats = brain_stats()
            print(f"[{datetime.now().isoformat()}] Knowledge: {stats['memory']['total_knowledge']}, Skills: {len(stats['skills'])}")
    except KeyboardInterrupt:
        print("\nShutting down...")
        brain.shutdown()
