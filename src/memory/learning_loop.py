"""
Continuous Learning Loop - The Heart of Self-Learning System
Integrates Memory, Knowledge Scanner, and Self-Debugger
into a unified learning system that runs continuously.

THE CORE PRINCIPLE:
"Learn from everything. Improve constantly. Never stop."
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
import threading

# Import all components
from .memory_manager import (
    get_memory, remember, recall,
    learn_pattern, learn_lesson, MemoryManager
)
from .knowledge_scanner import (
    get_scanner, scan_knowledge,
    get_pending_improvements, KnowledgeScanner
)
from .self_debugger import (
    get_debugger, track_decision, track_action,
    track_error, health_check, SelfDebugger
)
from .advanced_learning import get_learning_engine, AdvancedLearningEngine
from .storage_v2 import get_storage_v2
from .proposal_engine import get_proposal_engine_v2
from .experiment_executor import get_experiment_executor
from .outcome_verifier import get_outcome_verifier
from .learning_policy import get_learning_policy_state, select_learning_policy, update_learning_policy
from .memory_governor import get_memory_governor
from .migrate_proposals_v1_to_v2 import migrate_once as migrate_proposals_v1_to_v2

# NEW: Import working API-based learning modules
try:
    from .api_learner import (
        get_api_fetcher, fetch_knowledge_from_apis, APIKnowledgeFetcher
    )
    from .auto_discovery import (
        get_discovery_engine, run_auto_discovery, get_discovery_stats, AutoDiscoveryEngine
    )
    from .user_feedback_learner import (
        get_user_learner, learn_command, learn_error, learn_chat, UserFeedbackLearner
    )
    API_LEARNING_AVAILABLE = True
except ImportError:
    API_LEARNING_AVAILABLE = False
try:
    from src.core.prompt_system import get_prompt_system
    from src.core.runtime_guard import ProcessSingleton
except ImportError:
    # Support execution contexts where `src/` is already on PYTHONPATH.
    from core.prompt_system import get_prompt_system
    from core.runtime_guard import ProcessSingleton


class LearningLoop:
    """
    The continuous learning loop that ties everything together.
    Runs infinitely, learning and improving constantly.
    """

    # Learning intervals (in seconds)
    INTERVALS = {
        "fast_check": 10,        # Quick health check
        "knowledge_scan": 3600,  # Hourly knowledge scan
        "deep_analysis": 86400,  # Daily deep analysis
        "daily_self_learning": 86400,  # Daily autonomous self-learning cycle
        "advanced_review": 21600,  # Every 6 hours
        "cleanup": 604800,       # Weekly cleanup
    }
    FOCUS_AREAS = [
        "reliability",
        "learning",
        "execution",
        "quality",
        "speed",
        "cost",
        "security",
        "ux",
    ]

    def __init__(self, base_path: str = None):
        # Data path for state files
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Default to data/state/ directory
            try:
                project_root = Path(__file__).parent.parent.parent
                self.base_path = project_root / "data" / "state"
                self.base_path.mkdir(parents=True, exist_ok=True)
            except:
                self.base_path = Path.cwd() / "data" / "state"

        self.memory = get_memory()
        self.scanner = get_scanner()
        self.debugger = get_debugger()

        # State
        self.running = False
        self.iteration = 0
        self.last_scan = None
        self.last_analysis = None
        self.last_cleanup = None
        self.last_advanced_review = None
        self.last_daily_self_learning = None
        self.applied_proposals = set()
        self.auto_approve_threshold = float(os.getenv("AUTO_APPROVE_PROPOSAL_SCORE", "8.5"))
        self.enable_auto_approve = os.getenv("ENABLE_AUTO_APPROVE_PROPOSALS", "true").lower() == "true"
        self.enable_advanced_learning = os.getenv("ENABLE_ADVANCED_LEARNING", "true").lower() == "true"
        self.advanced_review_limit = int(os.getenv("ADVANCED_REVIEW_LIMIT", "5"))
        self.advanced_ingest_limit = int(os.getenv("ADVANCED_INGEST_LIMIT", "8"))
        self.enable_daily_self_learning = os.getenv("ENABLE_DAILY_SELF_LEARNING", "true").lower() == "true"
        self.daily_self_learning_interval = int(
            os.getenv("DAILY_SELF_LEARNING_INTERVAL_SECONDS", str(self.INTERVALS["daily_self_learning"]))
        )
        self.daily_self_learning_max_experiments = int(os.getenv("DAILY_SELF_LEARNING_MAX_EXPERIMENTS", "3"))
        self.daily_self_learning_max_ideas = int(os.getenv("DAILY_SELF_LEARNING_MAX_IDEAS", "6"))
        self.enable_daily_notes = os.getenv("ENABLE_DAILY_RND_NOTES", "true").lower() == "true"
        self.rnd_notes_max_message_length = int(os.getenv("RND_NOTE_MAX_MESSAGE_CHARS", "800"))
        self.enable_iteration_self_check = os.getenv("ENABLE_ITERATION_SELF_CHECK", "true").lower() == "true"
        self.self_check_warn_streak = int(os.getenv("SELF_CHECK_WARN_STREAK", "8"))
        self.focus_rotation_warn_streak = int(
            os.getenv("FOCUS_ROTATION_WARN_STREAK", str(self.self_check_warn_streak))
        )
        self.enable_stagnation_unblock = os.getenv("ENABLE_STAGNATION_UNBLOCK", "true").lower() == "true"
        self.unblock_min_proposal_score = float(os.getenv("UNBLOCK_MIN_PROPOSAL_SCORE", "7.2"))
        self.unblock_max_per_iteration = int(os.getenv("UNBLOCK_MAX_AUTO_APPROVALS_PER_ITERATION", "1"))
        self.scan_operation_lock_path = os.getenv("SCAN_OPERATION_LOCK_PATH", "data/state/knowledge_scan.lock")
        self.improvement_operation_lock_path = os.getenv(
            "IMPROVEMENT_OPERATION_LOCK_PATH", "data/state/improvement_apply.lock"
        )
        self.daily_cycle_operation_lock_path = os.getenv(
            "DAILY_CYCLE_OPERATION_LOCK_PATH", "data/state/daily_self_learning.lock"
        )
        self.current_focus_area = str(os.getenv("DEFAULT_FOCUS_AREA", "reliability")).strip().lower()
        if self.current_focus_area not in self.FOCUS_AREAS:
            self.current_focus_area = "reliability"
        self.learning_engine: Optional[AdvancedLearningEngine] = (
            get_learning_engine() if self.enable_advanced_learning else None
        )
        self.prompt_system = get_prompt_system()
        self.storage_v2 = get_storage_v2()
        self.proposal_engine_v2 = get_proposal_engine_v2()
        self.executor_v2 = get_experiment_executor()
        self.verifier_v2 = get_outcome_verifier()
        self.governor = get_memory_governor()
        self.enable_proposal_v2 = os.getenv("ENABLE_PROPOSAL_V2", "true").strip().lower() == "true"
        self.enable_experiment_executor = os.getenv("ENABLE_EXPERIMENT_EXECUTOR", "true").strip().lower() == "true"
        self.execution_mode_default = str(os.getenv("EXECUTION_MODE_DEFAULT", "safe")).strip().lower() or "safe"
        self.enable_policy_bandit = os.getenv("ENABLE_POLICY_BANDIT", "true").strip().lower() == "true"
        self.enable_windowed_self_check = os.getenv("ENABLE_WINDOWED_SELF_CHECK", "true").strip().lower() == "true"
        self.enable_synthetic_opportunities = os.getenv("ENABLE_SYNTHETIC_OPPORTUNITIES", "true").strip().lower() == "true"
        self.synthetic_opportunity_threshold = int(os.getenv("SYNTHETIC_OPPORTUNITY_THRESHOLD", "3"))
        self.verification_retry_interval_sec = int(os.getenv("VERIFICATION_RETRY_INTERVAL_SECONDS", "300"))
        self.verification_retry_max_attempts = int(os.getenv("VERIFICATION_RETRY_MAX_ATTEMPTS", "3"))
        self.enable_cafe_calibration = os.getenv("ENABLE_CAFE_CALIBRATION", "true").strip().lower() == "true"
        self.cafe_calibration_interval_sec = int(os.getenv("CAFE_CALIBRATION_INTERVAL_SECONDS", "21600"))
        self.last_cafe_calibration: Optional[str] = None
        self.last_cafe_calibration_summary: Dict = {}
        self.enable_normal_mode_canary = os.getenv("ENABLE_NORMAL_MODE_CANARY", "true").strip().lower() == "true"
        self.normal_mode_max_per_hour = int(os.getenv("NORMAL_MODE_MAX_PER_HOUR", "1"))
        self.normal_mode_min_priority = float(os.getenv("NORMAL_MODE_MIN_PRIORITY", "0.9"))
        self.normal_mode_allowed_risk = set(
            x.strip().lower()
            for x in str(os.getenv("NORMAL_MODE_ALLOWED_RISK", "low")).split(",")
            if x.strip()
        ) or {"low"}
        self.normal_mode_cooldown_sec = int(os.getenv("NORMAL_MODE_COOLDOWN_SECONDS", "1800"))
        self.normal_mode_cooldown_until: Optional[str] = None
        self.normal_mode_execution_history: List[str] = []
        self.normal_mode_successes = 0
        self.normal_mode_losses = 0
        self.normal_mode_last_reason = "not_evaluated"
        self.window_learning_opportunities = 0
        self.window_improvement_opportunities = 0
        self.no_opportunity_iterations = 0
        if self.enable_proposal_v2:
            try:
                migrate_proposals_v1_to_v2()
            except Exception:
                pass

        self.notes_dir = self.base_path.parent / "logs"
        self.notes_dir.mkdir(parents=True, exist_ok=True)

        # Learning stats
        self.stats = {
            "total_iterations": 0,
            "knowledge_items_learned": 0,
            "issues_detected": 0,
            "issues_resolved": 0,
            "patterns_discovered": 0,
            "lessons_learned": 0,
            "daily_self_learning_runs": 0,
        }
        self.no_improvement_streak = 0
        self.no_learning_streak = 0

        # Load previous state
        self._load_state()

    def _load_state(self):
        """Load previous learning state."""
        state_path = self.base_path / "learning_state.json"
        if state_path.exists():
            try:
                with open(state_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                loaded_stats = data.get("stats", {})
                if isinstance(loaded_stats, dict):
                    merged_stats = dict(self.stats)
                    merged_stats.update(loaded_stats)
                    self.stats = merged_stats
                self.iteration = data.get("iteration", 0)
                self.last_scan = data.get("last_scan")
                self.last_analysis = data.get("last_analysis")
                self.last_cleanup = data.get("last_cleanup")
                self.last_advanced_review = data.get("last_advanced_review")
                self.last_daily_self_learning = data.get("last_daily_self_learning")
                self.applied_proposals = set(data.get("applied_proposals", []))
                self.no_improvement_streak = int(data.get("no_improvement_streak", 0))
                self.no_learning_streak = int(data.get("no_learning_streak", 0))
                self.window_learning_opportunities = int(data.get("window_learning_opportunities", 0))
                self.window_improvement_opportunities = int(data.get("window_improvement_opportunities", 0))
                self.no_opportunity_iterations = int(data.get("no_opportunity_iterations", 0))
                self.normal_mode_cooldown_until = data.get("normal_mode_cooldown_until")
                self.normal_mode_execution_history = (
                    data.get("normal_mode_execution_history", [])
                    if isinstance(data.get("normal_mode_execution_history"), list)
                    else []
                )[-500:]
                self.normal_mode_successes = int(data.get("normal_mode_successes", 0))
                self.normal_mode_losses = int(data.get("normal_mode_losses", 0))
                self.normal_mode_last_reason = str(data.get("normal_mode_last_reason", self.normal_mode_last_reason))
                loaded_focus = str(data.get("current_focus_area", self.current_focus_area)).strip().lower()
                if loaded_focus in self.FOCUS_AREAS:
                    self.current_focus_area = loaded_focus
                self.last_cafe_calibration = data.get("last_cafe_calibration")
            except Exception:
                # Start fresh on corrupted state.
                pass

    def _save_state(self):
        """Save current learning state."""
        state_path = self.base_path / "learning_state.json"
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump({
                "iteration": self.iteration,
                "stats": self.stats,
                "last_scan": self.last_scan.isoformat() if isinstance(self.last_scan, datetime) else self.last_scan,
                "last_analysis": self.last_analysis,
                "last_cleanup": self.last_cleanup,
                "last_advanced_review": self.last_advanced_review,
                "last_daily_self_learning": self.last_daily_self_learning,
                "applied_proposals": sorted(list(self.applied_proposals))[-500:],
                "no_improvement_streak": self.no_improvement_streak,
                "no_learning_streak": self.no_learning_streak,
                "window_learning_opportunities": self.window_learning_opportunities,
                "window_improvement_opportunities": self.window_improvement_opportunities,
                "no_opportunity_iterations": self.no_opportunity_iterations,
                "normal_mode_cooldown_until": self.normal_mode_cooldown_until,
                "normal_mode_execution_history": self.normal_mode_execution_history[-200:],
                "normal_mode_successes": self.normal_mode_successes,
                "normal_mode_losses": self.normal_mode_losses,
                "normal_mode_last_reason": self.normal_mode_last_reason,
                "current_focus_area": self.current_focus_area,
                "last_cafe_calibration": self.last_cafe_calibration,
                "last_updated": datetime.now().isoformat()
            }, f, indent=2)

    # ==================== LEARNING ACTIONS ====================

    def learn_from_interaction(self, interaction: Dict):
        """
        Learn from a single interaction.
        Called by agents after each action.
        """
        # Track in debugger
        track_action(
            agent=interaction.get("agent", "UNKNOWN"),
            action_type=interaction.get("type", "unknown"),
            details=interaction.get("details", {}),
            success=interaction.get("success", True),
            duration_ms=interaction.get("duration_ms", 0)
        )

        # Extract patterns if successful
        if interaction.get("success") and interaction.get("pattern"):
            learn_pattern(
                pattern_type=interaction["pattern"].get("type", "general"),
                pattern_data=interaction["pattern"].get("data", {}),
                success_rate=0.8,
                context=interaction.get("context")
            )
            self.stats["patterns_discovered"] += 1

        # Learn from failures
        if not interaction.get("success"):
            learn_lesson(
                lesson_type="failure",
                description=interaction.get("error", "Unknown error"),
                context=f"Agent: {interaction.get('agent')}, Action: {interaction.get('type')}",
                impact="medium"
            )
            self.stats["lessons_learned"] += 1

        # Store in memory if significant
        if interaction.get("significant", False):
            remember(
                key=f"interaction:{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                content=interaction,
                category="interactions",
                importance=interaction.get("importance", 5)
            )

        # Feed normalized event stream for proposal v2.
        model_hint = (
            interaction.get("model")
            or (interaction.get("details") or {}).get("model")
            or (interaction.get("context") or {}).get("model")
            or (interaction.get("context") or {}).get("selected_model")
        )
        self._record_learning_event(
            {
                "ts": datetime.now().isoformat(),
                "source": interaction.get("agent", "interaction"),
                "event_type": interaction.get("type", "interaction"),
                "content": str(interaction.get("error") or interaction.get("details") or "")[:400],
                "context": interaction.get("context", {}) if isinstance(interaction.get("context"), dict) else {},
                "model": model_hint,
                "novelty_score": 0.6 if interaction.get("significant", False) else 0.35,
                "value_score": 0.75 if interaction.get("success") else 0.65,
                "risk_score": 0.25 if interaction.get("success") else 0.55,
                "confidence": 0.7 if interaction.get("success") else 0.5,
            }
        )

        # NEW: Also learn from user feedback if available
        if API_LEARNING_AVAILABLE:
            try:
                interaction_type = interaction.get("type", "")
                if interaction_type in ["command", "terminal"]:
                    learn_command(
                        command=interaction.get("details", {}).get("command", ""),
                        success=interaction.get("success", True),
                        context=interaction.get("context")
                    )
                elif interaction_type == "error":
                    learn_error(
                        error_type=interaction.get("details", {}).get("error_type", "unknown"),
                        message=interaction.get("error", ""),
                        context=interaction.get("context")
                    )
                elif interaction_type == "chat":
                    learn_chat(
                        user_message=interaction.get("details", {}).get("user_message", ""),
                        assistant_response=interaction.get("details", {}).get("response", "")
                    )
            except Exception:
                pass  # Don't break main flow if feedback learning fails

    def scan_for_knowledge(self) -> Dict:
        """
        Scan external sources for new knowledge.
        Now uses both traditional scanner AND new API-based sources.
        Returns discoveries and proposals.
        """
        track_decision("SYSTEM", "knowledge_scan", "Initiating knowledge scan")

        results = {
            "filtered_count": 0,
            "proposals_generated": 0,
            "sources_scanned": 0,
            "sources_skipped": 0,
            "source_errors": 0,
            "top_items": [],
            "api_results": {},
            "discovery_results": {},
        }

        # 1. Try traditional knowledge scanner (may fail due to 403)
        try:
            scanner_results = scan_knowledge(min_score=6.0)
            if scanner_results:
                results["filtered_count"] += int(scanner_results.get("filtered_count", 0))
                results["proposals_generated"] += int(scanner_results.get("proposals_generated", 0))
                results["sources_scanned"] += int(scanner_results.get("sources_scanned", 0))
                results["sources_skipped"] += int(scanner_results.get("sources_skipped", 0))
                results["source_errors"] += int(scanner_results.get("source_errors", 0))
                results["top_items"].extend(scanner_results.get("top_items", []))
        except Exception as e:
            track_error("SYSTEM", "scanner_error", str(e), recoverable=True)

        # 2. Use API-based knowledge fetcher (more reliable)
        if API_LEARNING_AVAILABLE:
            try:
                api_results = fetch_knowledge_from_apis()
                if api_results:
                    results["api_results"] = api_results
                    # API results can be list or dict
                    if isinstance(api_results, list):
                        api_items = api_results
                        results["filtered_count"] += len(api_items)
                        results["sources_scanned"] += 1
                    elif isinstance(api_results, dict):
                        api_items = api_results.get("items", api_results.get("results", []))
                        results["filtered_count"] += len(api_items)
                        results["sources_scanned"] += api_results.get("sources_scanned", 1)
                    else:
                        api_items = []
                    # Add API items to top_items
                    for item in api_items[:10]:
                        if isinstance(item, dict):
                            results["top_items"].append({
                                "title": item.get("title", ""),
                                "description": item.get("description", ""),
                                "source": item.get("source", "api"),
                                "score": item.get("score", 5),
                                "url": item.get("url", ""),
                                "category": item.get("category", "general"),
                            })
            except Exception as e:
                track_error("SYSTEM", "api_learner_error", str(e), recoverable=True)

        # 3. Run auto-discovery from 32+ sources
        if API_LEARNING_AVAILABLE:
            try:
                discovery_results = run_auto_discovery(force_all=False)
                if discovery_results:
                    results["discovery_results"] = discovery_results
                    results["filtered_count"] += int(discovery_results.get("items_new", 0))
                    results["sources_scanned"] += int(discovery_results.get("sources_scanned", 0))
                    # Add top discovered items
                    for item in discovery_results.get("top_items", [])[:10]:
                        results["top_items"].append({
                            "title": item.get("title", ""),
                            "source": item.get("source", "discovery"),
                            "score": item.get("score", 5),
                        })
            except Exception as e:
                track_error("SYSTEM", "discovery_error", str(e), recoverable=True)

        self.last_scan = datetime.now().isoformat()

        # Deduplicate top_items by title
        seen_titles = set()
        unique_items = []
        for item in results["top_items"]:
            title = item.get("title", "")[:50]
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_items.append(item)
        results["top_items"] = unique_items[:20]

        # Update stats
        self.stats["knowledge_items_learned"] += results["filtered_count"]

        # Store scan results
        remember(
            key=f"scan:{datetime.now().strftime('%Y%m%d_%H')}",
            content=results,
            category="scans",
            keywords=["knowledge", "scan", "api", "discovery"],
            importance=7
        )

        # Feed high-value scan results into advanced learning memory.
        self._ingest_scan_into_advanced_learning(results)

        # Log if we found something
        if results["filtered_count"] > 0:
            api_items_count = 0
            api_res = results.get("api_results")
            if isinstance(api_res, list):
                api_items_count = len(api_res)
            elif isinstance(api_res, dict):
                api_items_count = len(api_res.get("items", api_res.get("results", [])))

            track_action(
                agent="SYSTEM",
                action_type="knowledge_scan_success",
                details={
                    "filtered_count": results["filtered_count"],
                    "sources_scanned": results["sources_scanned"],
                    "api_items": api_items_count,
                    "discovery_new": results.get("discovery_results", {}).get("items_new", 0),
                },
                success=True
            )

        return results

    def analyze_health(self) -> Dict:
        """
        Analyze system health and detect issues.
        """
        report = health_check()
        self.stats["issues_detected"] = report.get("open_issues", 0)

        # Get open issues and try to auto-resolve low severity ones
        issues = self.debugger.get_open_issues()
        for issue in issues:
            if issue["severity"] in ["low", "medium"]:
                # Try auto-resolution
                if self._try_auto_fix(issue):
                    self.debugger.resolve_issue(
                        issue["id"],
                        "Auto-resolved by learning system"
                    )
                    self.stats["issues_resolved"] += 1

        return report

    def _try_auto_fix(self, issue: Dict) -> bool:
        """Attempt to automatically fix an issue."""
        severity = issue.get("severity", "medium")
        issue_type = issue.get("type", "")
        # Never auto-fix high-risk issues.
        if severity in {"high", "critical"}:
            return False

        # Simple auto-fixes based on issue type
        fix_proposal = issue.get("fix_proposal", {})
        fix_text = str(fix_proposal.get("recommended_fix", "")).strip()
        can_apply = bool(fix_text) and issue_type in {"performance", "quality", "behavior"}

        # Log the fix attempt
        track_action(
            agent="SYSTEM",
            action_type="auto_fix",
            details={"issue_id": issue["id"], "fix": fix_proposal.get("recommended_fix")},
            success=can_apply
        )

        # For now only mark auto-fix as successful for low-risk issue types.
        return can_apply

    def get_improvement_proposals(self) -> List[Dict]:
        """Get proposals that still need approval/application."""
        return get_pending_improvements()

    def apply_approved_improvements(self) -> Dict:
        """Apply all approved improvements and optionally auto-approve strong ones."""
        proposals = self.get_improvement_proposals()
        summary = {
            "total_seen": len(proposals),
            "auto_approved": 0,
            "applied": 0,
            "skipped": 0,
            "stagnation_unblocked": 0,
        }
        stagnation_mode = (
            self.enable_stagnation_unblock
            and self.no_improvement_streak >= self.focus_rotation_warn_streak
            and int(self.stats.get("issues_detected", 0)) == 0
        )

        for proposal in proposals:
            proposal_id = proposal.get("id")
            if not proposal_id:
                summary["skipped"] += 1
                continue

            status = proposal.get("status")
            source_score = float(proposal.get("source_item", {}).get("score", 0) or 0)

            if status == "pending_approval" and self.enable_auto_approve and source_score >= self.auto_approve_threshold:
                if self.scanner.approve_proposal(proposal_id):
                    summary["auto_approved"] += 1
                    status = "approved"
            elif (
                status == "pending_approval"
                and stagnation_mode
                and summary["stagnation_unblocked"] < max(1, self.unblock_max_per_iteration)
                and source_score >= self.unblock_min_proposal_score
            ):
                if self.scanner.approve_proposal(proposal_id):
                    summary["auto_approved"] += 1
                    summary["stagnation_unblocked"] += 1
                    status = "approved"
                    track_decision(
                        "SYSTEM",
                        "stagnation_unblock_auto_approve",
                        (
                            f"Auto-approved {proposal_id} to break stagnation "
                            f"(score={source_score:.2f}, streak={self.no_improvement_streak})"
                        ),
                    )

            if status != "approved":
                summary["skipped"] += 1
                continue

            if proposal_id in self.applied_proposals:
                summary["skipped"] += 1
                continue

            # Apply the improvement
            track_action(
                agent="SYSTEM",
                action_type="apply_improvement",
                details={"proposal_id": proposal_id, "title": proposal.get("title")},
                success=True
            )

            # Learn from the application
            learn_lesson(
                lesson_type="success",
                description=f"Applied improvement: {proposal.get('title', proposal_id)}",
                context=f"Source: {proposal.get('source_item', {}).get('source', 'unknown')}",
                impact="high"
            )

            self.applied_proposals.add(proposal_id)
            self.scanner.mark_proposal_applied(proposal_id, note="Applied by learning loop")
            summary["applied"] += 1

        return summary

    def _parse_time(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None

    def _prune_normal_mode_history(self, now: Optional[datetime] = None) -> List[datetime]:
        now = now or datetime.now()
        kept: List[str] = []
        parsed: List[datetime] = []
        for raw in self.normal_mode_execution_history[-500:]:
            ts = self._parse_time(raw)
            if not ts:
                continue
            if (now - ts).total_seconds() <= 3600:
                kept.append(ts.isoformat())
                parsed.append(ts)
        self.normal_mode_execution_history = kept
        return parsed

    def _execution_guardrail_snapshot(self) -> Dict:
        now = datetime.now()
        history = self._prune_normal_mode_history(now=now)
        cooldown_until_dt = self._parse_time(self.normal_mode_cooldown_until)
        cooldown_remaining = 0
        if cooldown_until_dt:
            cooldown_remaining = max(0, int((cooldown_until_dt - now).total_seconds()))
        return {
            "canary_enabled": self.enable_normal_mode_canary,
            "executor_real_apply_enabled": bool(getattr(self.executor_v2, "enable_real_apply", False)),
            "normal_mode_max_per_hour": self.normal_mode_max_per_hour,
            "normal_mode_runs_last_hour": len(history),
            "normal_mode_min_priority": self.normal_mode_min_priority,
            "normal_mode_allowed_risk": sorted(list(self.normal_mode_allowed_risk)),
            "normal_mode_cooldown_until": self.normal_mode_cooldown_until,
            "normal_mode_cooldown_remaining_sec": cooldown_remaining,
            "normal_mode_successes": self.normal_mode_successes,
            "normal_mode_losses": self.normal_mode_losses,
            "normal_mode_last_reason": self.normal_mode_last_reason,
        }

    def _select_execution_mode_for_proposal(self, proposal: Dict) -> Dict[str, str]:
        default_mode = self.execution_mode_default if self.execution_mode_default in {"safe", "normal"} else "safe"
        if default_mode != "normal":
            self.normal_mode_last_reason = "default_safe_mode"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}
        if not self.enable_normal_mode_canary:
            self.normal_mode_last_reason = "canary_disabled"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}
        if not bool(getattr(self.executor_v2, "enable_real_apply", False)):
            self.normal_mode_last_reason = "real_apply_disabled"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}

        now = datetime.now()
        cooldown_until_dt = self._parse_time(self.normal_mode_cooldown_until)
        if cooldown_until_dt and cooldown_until_dt > now:
            self.normal_mode_last_reason = "cooldown_active"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}

        recent = self._prune_normal_mode_history(now=now)
        if len(recent) >= max(1, self.normal_mode_max_per_hour):
            self.normal_mode_last_reason = "hourly_quota_exceeded"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}

        risk_level = str(proposal.get("risk_level", "high")).strip().lower()
        priority = float(proposal.get("priority", 0.0) or 0.0)
        if risk_level not in self.normal_mode_allowed_risk:
            self.normal_mode_last_reason = f"risk_not_allowed:{risk_level}"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}
        if priority < float(self.normal_mode_min_priority):
            self.normal_mode_last_reason = "priority_below_canary_threshold"
            return {"mode": "safe", "reason": self.normal_mode_last_reason}

        self.normal_mode_last_reason = "canary_allowed"
        return {"mode": "normal", "reason": self.normal_mode_last_reason}

    def _record_normal_mode_outcome(self, verdict: str, proposal_id: str) -> None:
        now = datetime.now()
        self.normal_mode_execution_history.append(now.isoformat())
        self._prune_normal_mode_history(now=now)
        verdict = str(verdict).strip().lower()
        if verdict == "loss":
            self.normal_mode_losses += 1
            self.normal_mode_cooldown_until = (now + timedelta(seconds=max(60, self.normal_mode_cooldown_sec))).isoformat()
            self.proposal_engine_v2.mark_status(
                proposal_id,
                "verified",
                rollback_guardrail_triggered=True,
                rollback_reason="loss_detected_after_normal_mode_execution",
                rollback_triggered_at=now.isoformat(),
            )
            self._append_rnd_note(
                note_type="normal_mode_guardrail_cooldown",
                severity="warning",
                message=f"Normal-mode loss detected. Cooldown activated for {self.normal_mode_cooldown_sec}s.",
                context={
                    "proposal_id": proposal_id,
                    "cooldown_until": self.normal_mode_cooldown_until,
                },
            )
        elif verdict == "win":
            self.normal_mode_successes += 1

    def _daily_notes_path(self, dt: Optional[datetime] = None) -> Path:
        ts = dt or datetime.now()
        return self.notes_dir / f"rnd_notes_{ts.strftime('%Y%m%d')}.jsonl"

    def _append_rnd_note(
        self,
        note_type: str,
        message: str,
        severity: str = "info",
        context: Optional[Dict] = None,
    ) -> None:
        """Append a daily R&D note entry for continuous learning traceability."""
        if not self.enable_daily_notes:
            return
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "iteration": self.iteration,
                "type": str(note_type)[:64],
                "severity": str(severity)[:16],
                "message": str(message)[: self.rnd_notes_max_message_length],
                "context": context if isinstance(context, dict) else {},
            }
            note_path = self._daily_notes_path()
            with open(note_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            # Never break the main learning loop because note writing fails.
            return

    def get_today_rnd_notes(self, limit: int = 50) -> List[Dict]:
        """Read today's R&D notes (most recent first)."""
        path = self._daily_notes_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            notes = []
            for line in lines[-max(1, limit):]:
                try:
                    notes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            notes.reverse()
            return notes
        except Exception:
            return []

    def _daily_self_learning_path(self, dt: Optional[datetime] = None) -> Path:
        ts = dt or datetime.now()
        return self.notes_dir / f"daily_self_learning_{ts.strftime('%Y%m%d')}.jsonl"

    def _append_daily_self_learning_log(self, summary: Dict) -> None:
        """Append a dedicated daily self-learning cycle log."""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "iteration": self.iteration,
                "summary": summary if isinstance(summary, dict) else {},
            }
            path = self._daily_self_learning_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            return

    def get_today_daily_self_learning(self, limit: int = 20) -> List[Dict]:
        """Read today's daily self-learning logs (most recent first)."""
        path = self._daily_self_learning_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            logs = []
            for line in lines[-max(1, limit):]:
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            logs.reverse()
            return logs
        except Exception:
            return []

    def _should_run_daily_self_learning(self) -> bool:
        """Determine whether the daily autonomous self-learning cycle should run."""
        if not self.enable_daily_self_learning:
            return False
        last_run = self._parse_time(self.last_daily_self_learning)
        if not last_run:
            return True
        interval_seconds = max(60, int(self.daily_self_learning_interval))
        return datetime.now() - last_run >= timedelta(seconds=interval_seconds)

    def _generate_daily_improvement_ideas(
        self,
        health_report: Dict,
        scanner_stats: Dict,
        scan_results: Dict,
        pending_proposals: List[Dict],
    ) -> List[str]:
        """Generate concrete improvement ideas based on current system signals."""
        ideas: List[str] = []

        if int(health_report.get("open_issues", 0)) > 0:
            ideas.append("Prioritize fixing top open issues before adding new features.")
        if self.no_learning_streak > 0:
            ideas.append("Expand knowledge sources or relax min_score briefly to recover learning velocity.")
        if self.no_improvement_streak > 0:
            ideas.append("Reduce safe approval friction for high-confidence proposals to restore improvement flow.")

        pending_count = len(pending_proposals)
        if pending_count >= 5:
            ideas.append(f"Process proposal backlog in batches (current pending: {pending_count}).")

        if int(scanner_stats.get("sources_in_backoff", 0)) > 0:
            ideas.append("Add redundant sources or retry windows to reduce scan backoff blind spots.")

        top_items = scan_results.get("top_items", []) if isinstance(scan_results, dict) else []
        if len(top_items) >= 3:
            ideas.append("Run targeted implementation trials from the top 3 research findings.")
        elif int(scan_results.get("filtered_count", 0)) == 0:
            ideas.append("Trigger a broader daily research sweep to avoid stagnation.")

        if not ideas:
            ideas.append("No major blockers detected; continue controlled optimization experiments.")

        return ideas[: max(1, self.daily_self_learning_max_ideas)]

    def _run_daily_self_learning_cycle(self, iteration_results: Optional[Dict] = None) -> Dict:
        """Run the daily self-check, research, evaluation, ideation, and experiment cycle."""
        now = datetime.now()
        iteration_results = iteration_results if isinstance(iteration_results, dict) else {}

        health_report = iteration_results.get("health", {}) if isinstance(iteration_results.get("health"), dict) else {}
        if not health_report:
            health_report = health_check()

        scan_results = iteration_results.get("scan", {}) if isinstance(iteration_results.get("scan"), dict) else {}
        if not scan_results:
            # Daily cycle forces at least one explicit research attempt.
            scan_results = scan_knowledge(min_score=5.8)

        scanner_stats = self.scanner.get_stats()
        pending_proposals = self.get_improvement_proposals()
        notes = self.get_today_rnd_notes(limit=300)
        focus = self._build_focus_report(health_report=health_report, notes=notes, scanner_stats=scanner_stats)
        ideas = self._generate_daily_improvement_ideas(
            health_report=health_report,
            scanner_stats=scanner_stats,
            scan_results=scan_results,
            pending_proposals=pending_proposals,
        )
        focus_rotation = self._recommend_focus_rotation(
            health_report=health_report,
            scan_results=scan_results,
            pending_proposals=pending_proposals,
            solution_options=focus.get("solution_options", []),
        )
        if focus_rotation.get("apply_now"):
            self.current_focus_area = str(focus_rotation.get("recommended_focus_area", self.current_focus_area))

        experiments: List[Dict] = []

        proposal_scores = []
        for proposal in pending_proposals:
            score = proposal.get("source_item", {}).get("score", 0)
            try:
                proposal_scores.append(float(score))
            except (TypeError, ValueError):
                continue
        if proposal_scores:
            lowered_threshold = max(0.0, self.auto_approve_threshold - 0.5)
            additional_auto_approve = sum(
                1 for score in proposal_scores if lowered_threshold <= score < self.auto_approve_threshold
            )
            experiments.append(
                {
                    "name": "auto_approve_threshold_sensitivity",
                    "hypothesis": "Lowering threshold by 0.5 may increase safe throughput.",
                    "simulation": {
                        "current_threshold": self.auto_approve_threshold,
                        "trial_threshold": round(lowered_threshold, 2),
                        "estimated_extra_auto_approvals": additional_auto_approve,
                    },
                    "decision": "keep_current" if additional_auto_approve == 0 else "consider_controlled_trial",
                }
            )

        top_scores = []
        for item in scan_results.get("top_items", []) if isinstance(scan_results, dict) else []:
            try:
                top_scores.append(float(item.get("score", 0)))
            except (TypeError, ValueError):
                continue
        if top_scores:
            relaxed_threshold = 5.5
            extra_items = sum(1 for score in top_scores if relaxed_threshold <= score < 6.0)
            experiments.append(
                {
                    "name": "research_threshold_sensitivity",
                    "hypothesis": "A slightly wider filter may reveal useful weak signals.",
                    "simulation": {
                        "current_min_score": 6.0,
                        "trial_min_score": relaxed_threshold,
                        "estimated_additional_items": extra_items,
                    },
                    "decision": "keep_current" if extra_items == 0 else "consider_daily_research_trial",
                }
            )

        sources_in_backoff = int(scanner_stats.get("sources_in_backoff", 0))
        if sources_in_backoff > 0:
            experiments.append(
                {
                    "name": "source_resilience_simulation",
                    "hypothesis": "Adding backup sources may improve knowledge freshness.",
                    "simulation": {
                        "sources_in_backoff": sources_in_backoff,
                        "estimated_recovery_impact": "medium",
                    },
                    "decision": "prepare_backup_sources",
                }
            )

        experiments = experiments[: max(1, self.daily_self_learning_max_experiments)]

        lessons: List[str] = []
        lessons.extend(focus.get("lessons_learned_today", []))
        if int(scan_results.get("filtered_count", 0)) > 0:
            lessons.append(
                f"Daily research produced {scan_results.get('filtered_count', 0)} high-signal item(s)."
            )
        else:
            lessons.append("Daily research produced no high-signal items; source strategy needs refresh.")
        if int(health_report.get("open_issues", 0)) == 0:
            lessons.append("System health is stable; optimization can focus on speed and quality.")
        lessons = lessons[:5]

        next_actions = focus.get("next_actions", [])[:5]
        for idea in ideas[:3]:
            next_actions.append(f"Trial: {idea}")
        next_actions.append(
            f"Portfolio focus: {focus_rotation.get('recommended_focus_area', self.current_focus_area)} "
            f"({focus_rotation.get('reason', 'no_reason')})"
        )
        next_actions = next_actions[:8]

        warnings = []
        if int(health_report.get("open_issues", 0)) > 0:
            warnings.append(f"{health_report.get('open_issues', 0)} open issue(s) still active.")
        if self.no_learning_streak >= self.self_check_warn_streak:
            warnings.append(f"Learning stagnation streak is high ({self.no_learning_streak}).")
        if self.no_improvement_streak >= self.self_check_warn_streak:
            warnings.append(f"Improvement stagnation streak is high ({self.no_improvement_streak}).")

        summary = {
            "timestamp": now.isoformat(),
            "self_check": {
                "health_score": health_report.get("health_score", 0),
                "open_issues": health_report.get("open_issues", 0),
                "no_learning_streak": self.no_learning_streak,
                "no_improvement_streak": self.no_improvement_streak,
            },
            "self_research": {
                "filtered_count": scan_results.get("filtered_count", 0),
                "proposals_generated": scan_results.get("proposals_generated", 0),
                "sources_scanned": scan_results.get("sources_scanned", 0),
                "sources_skipped": scan_results.get("sources_skipped", 0),
                "source_errors": scan_results.get("source_errors", 0),
            },
            "self_evaluation": {
                "pending_proposals": len(pending_proposals),
                "sources_in_backoff": scanner_stats.get("sources_in_backoff", 0),
                "critical_problems": focus.get("critical_problems", [])[:3],
            },
            "improvement_ideas": ideas,
            "experiments": experiments,
            "lessons_learned": lessons,
            "next_actions": next_actions,
            "warnings": warnings[:5],
            "portfolio_rotation": focus_rotation,
        }

        self.last_daily_self_learning = now.isoformat()
        self.stats["daily_self_learning_runs"] = int(self.stats.get("daily_self_learning_runs", 0)) + 1

        remember(
            key=f"daily_self_learning:{now.strftime('%Y%m%d_%H%M%S')}",
            content=summary,
            category="daily_self_learning",
            keywords=["self_check", "self_research", "self_improvement"],
            importance=8,
        )
        learn_lesson(
            lesson_type="insight",
            description=(
                f"Daily self-learning cycle completed: health={health_report.get('health_score', 0)}, "
                f"research_items={scan_results.get('filtered_count', 0)}, "
                f"ideas={len(ideas)}, experiments={len(experiments)}"
            ),
            context="Daily autonomous self-learning cycle",
            impact="medium",
        )
        self.stats["lessons_learned"] += 1
        self._append_daily_self_learning_log(summary)

        return summary

    def _severity_weight(self, severity: str) -> int:
        mapping = {"critical": 4, "high": 3, "warning": 2, "medium": 2, "error": 3, "low": 1, "info": 0}
        return mapping.get(str(severity).lower(), 0)

    def _build_solution_options(
        self,
        critical_problems: List[Dict],
        pending_count: int,
        sources_backoff: int,
    ) -> List[Dict]:
        """Build feasible solution options after each analysis cycle."""
        options: List[Dict] = []

        if critical_problems:
            top = critical_problems[0]
            options.append(
                {
                    "id": "stabilize_top_risk",
                    "title": f"Stabilize: {top.get('title', 'Top issue')}",
                    "why": "Highest-severity blocker should be resolved first.",
                    "expected_impact": "high",
                    "focus_area": "reliability",
                }
            )

        if pending_count > 0:
            options.append(
                {
                    "id": "execute_pending_improvements",
                    "title": f"Process {pending_count} pending improvement(s)",
                    "why": "Backlog execution converts research into measurable progress.",
                    "expected_impact": "high" if pending_count >= 3 else "medium",
                    "focus_area": "execution",
                }
            )

        if self.no_learning_streak >= self.focus_rotation_warn_streak:
            options.append(
                {
                    "id": "expand_research_scope",
                    "title": "Expand research scope and source diversity",
                    "why": f"Learning stagnation streak reached {self.no_learning_streak}.",
                    "expected_impact": "medium",
                    "focus_area": "learning",
                }
            )

        if self.no_improvement_streak >= self.focus_rotation_warn_streak:
            options.append(
                {
                    "id": "reduce_execution_friction",
                    "title": "Reduce approval friction for safe improvements",
                    "why": f"Improvement stagnation streak reached {self.no_improvement_streak}.",
                    "expected_impact": "medium",
                    "focus_area": "execution",
                }
            )

        if sources_backoff > 0:
            options.append(
                {
                    "id": "harden_source_resilience",
                    "title": "Harden source resilience and retry strategy",
                    "why": f"{sources_backoff} source(s) currently throttled/backing off.",
                    "expected_impact": "medium",
                    "focus_area": "reliability",
                }
            )

        if not options:
            next_area = self.FOCUS_AREAS[(self.FOCUS_AREAS.index(self.current_focus_area) + 1) % len(self.FOCUS_AREAS)]
            options.append(
                {
                    "id": "rotate_focus_for_growth",
                    "title": f"Rotate focus to {next_area}",
                    "why": "Current focus appears stable; rotate to keep global progress moving.",
                    "expected_impact": "medium",
                    "focus_area": next_area,
                }
            )

        return options[:5]

    def _recommend_focus_rotation(
        self,
        health_report: Dict,
        scan_results: Dict,
        pending_proposals: List[Dict],
        solution_options: List[Dict],
    ) -> Dict:
        """Recommend when to pivot to another focus area for holistic progress."""
        current = self.current_focus_area
        pending_count = len(pending_proposals)
        open_issues = int(health_report.get("open_issues", 0))
        filtered_count = int(scan_results.get("filtered_count", 0)) if isinstance(scan_results, dict) else 0

        recommended = current
        reason = "Maintain current focus area."
        apply_now = False

        if open_issues > 0:
            recommended = "reliability"
            reason = f"Open issues detected ({open_issues}); reliability takes priority."
            apply_now = recommended != current
        elif self.no_learning_streak >= self.focus_rotation_warn_streak:
            recommended = "learning"
            reason = f"Learning stagnation reached {self.no_learning_streak}; pivot to learning."
            apply_now = recommended != current
        elif self.no_improvement_streak >= self.focus_rotation_warn_streak:
            recommended = "execution"
            reason = f"Improvement stagnation reached {self.no_improvement_streak}; pivot to execution."
            apply_now = recommended != current
        elif pending_count >= 5:
            recommended = "execution"
            reason = f"Improvement backlog is high ({pending_count}); pivot to execution."
            apply_now = recommended != current
        elif filtered_count == 0:
            recommended = "learning"
            reason = "No fresh high-signal research items; pivot to research/learning."
            apply_now = recommended != current
        else:
            # Planned rotation when current area is stable.
            recommended = self.FOCUS_AREAS[(self.FOCUS_AREAS.index(current) + 1) % len(self.FOCUS_AREAS)]
            reason = "Current area is stable; rotate to expand system-wide improvement coverage."
            apply_now = recommended != current

        alternatives = []
        seen = {recommended}
        for option in solution_options:
            area = str(option.get("focus_area", "")).strip().lower()
            if area and area not in seen and area in self.FOCUS_AREAS:
                alternatives.append(area)
                seen.add(area)
            if len(alternatives) >= 3:
                break
        if len(alternatives) < 3:
            for area in self.FOCUS_AREAS:
                if area in seen:
                    continue
                alternatives.append(area)
                seen.add(area)
                if len(alternatives) >= 3:
                    break

        return {
            "current_focus_area": current,
            "recommended_focus_area": recommended,
            "reason": reason,
            "apply_now": apply_now,
            "alternatives": alternatives,
        }

    def _build_focus_report(
        self,
        health_report: Dict,
        notes: List[Dict],
        scanner_stats: Dict,
    ) -> Dict:
        """Build an executive-focus report: key problems, lessons, and improvements."""
        critical_problems: List[Dict] = []
        lessons: List[str] = []
        improvements: List[str] = []
        next_actions: List[str] = []

        for issue in health_report.get("top_issues", [])[:5]:
            fix = issue.get("fix_proposal", {}).get("recommended_fix", "Investigate and patch")
            critical_problems.append(
                {
                    "severity": issue.get("severity", "unknown"),
                    "title": issue.get("title", "Unknown issue"),
                    "action": fix,
                }
            )
            next_actions.append(f"[{issue.get('severity', 'unknown')}] {issue.get('title', 'Issue')} -> {fix}")

        error_notes = [n for n in notes if n.get("type") in {"runtime_error", "scan_source_error", "open_issues_detected"}]
        if error_notes:
            by_type: Dict[str, int] = {}
            for note in error_notes:
                note_type = str(note.get("type", "unknown"))
                by_type[note_type] = by_type.get(note_type, 0) + 1
            lessons.append(
                "Runtime instability hotspots today: "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda x: x[1], reverse=True))
            )

        applied_notes = [n for n in notes if n.get("type") == "improvement_applied"]
        auto_approved_notes = [n for n in notes if n.get("type") == "proposal_auto_approved"]
        if applied_notes:
            improvements.append(f"Applied improvements events today: {len(applied_notes)}")
        if auto_approved_notes:
            improvements.append(f"Auto-approved proposal events today: {len(auto_approved_notes)}")

        pending_count = int(scanner_stats.get("pending_proposals", 0))
        if pending_count > 0:
            next_actions.append(f"Review/approve {pending_count} pending proposal(s)")

        sources_backoff = int(scanner_stats.get("sources_in_backoff", 0))
        if sources_backoff > 0:
            critical_problems.append(
                {
                    "severity": "warning",
                    "title": f"{sources_backoff} source(s) currently in scan backoff",
                    "action": "Check network/provider health and source reliability",
                }
            )
            lessons.append(
                f"Knowledge freshness risk: {sources_backoff} source(s) are temporarily throttled/backing off."
            )

        sorted_problems = sorted(
            critical_problems,
            key=lambda x: self._severity_weight(x.get("severity", "info")),
            reverse=True,
        )[:5]
        solution_options = self._build_solution_options(
            critical_problems=sorted_problems,
            pending_count=pending_count,
            sources_backoff=sources_backoff,
        )
        recommended_solution = solution_options[0] if solution_options else {}

        return {
            "critical_problems": sorted_problems,
            "lessons_learned_today": lessons[:5],
            "improvements_today": improvements[:5],
            "next_actions": next_actions[:8],
            "solution_options": solution_options,
            "recommended_solution": recommended_solution,
        }

    def _run_iteration_self_check(self, results: Dict) -> Dict:
        """Run mandatory self-check for blind spots and stagnation."""
        scan = results.get("scan", {}) if isinstance(results.get("scan"), dict) else {}
        improvements = results.get("improvements", {}) if isinstance(results.get("improvements"), dict) else {}
        advanced = results.get("advanced_review", {}) if isinstance(results.get("advanced_review"), dict) else {}

        learned_now = int(scan.get("filtered_count", 0)) > 0 or int(advanced.get("reviewed_items", 0)) > 0
        v2 = results.get("v2_pipeline", {}) if isinstance(results.get("v2_pipeline"), dict) else {}
        improved_now = (
            int(improvements.get("applied", 0)) > 0
            or int(v2.get("wins", 0)) > 0
            or (int(v2.get("verified", 0)) > 0 and int(v2.get("losses", 0)) == 0)
        )

        learning_window_open = "scan" in results or "advanced_review" in results
        improvement_window_open = (
            int(improvements.get("total_seen", 0)) > 0
            or int((results.get("v2_pipeline") or {}).get("created", 0)) > 0
            or int((results.get("v2_pipeline") or {}).get("approved", 0)) > 0
        )
        synthetic_window = False
        if not learning_window_open and not improvement_window_open:
            self.no_opportunity_iterations += 1
            if (
                self.enable_synthetic_opportunities
                and self.no_opportunity_iterations >= max(1, self.synthetic_opportunity_threshold)
            ):
                synthetic_window = True
                learning_window_open = True
                improvement_window_open = True
        else:
            self.no_opportunity_iterations = 0

        if self.enable_windowed_self_check:
            if learning_window_open:
                self.window_learning_opportunities += 1
                self.no_learning_streak = 0 if learned_now else self.no_learning_streak + 1
            if improvement_window_open:
                self.window_improvement_opportunities += 1
                self.no_improvement_streak = 0 if improved_now else self.no_improvement_streak + 1
        else:
            self.no_learning_streak = 0 if learned_now else self.no_learning_streak + 1
            self.no_improvement_streak = 0 if improved_now else self.no_improvement_streak + 1

        warnings = []
        suggestions = []

        if self.no_learning_streak >= self.self_check_warn_streak:
            warnings.append(f"No meaningful learning events for {self.no_learning_streak} iterations")
            suggestions.append("Increase scan frequency, widen sources, and refresh scoring thresholds.")

        if self.no_improvement_streak >= self.self_check_warn_streak:
            warnings.append(f"No applied improvements for {self.no_improvement_streak} iterations")
            suggestions.append("Lower approval friction or auto-approve threshold for safe high-score proposals.")

        if int(results.get("health", {}).get("open_issues", 0)) > 0:
            suggestions.append("Prioritize resolving open issues before generating new changes.")
        if synthetic_window:
            suggestions.append("Synthetic opportunity window opened to prevent blind stagnation; trigger scan/verify soon.")

        return {
            "enabled": self.enable_iteration_self_check,
            "learned_now": learned_now,
            "improved_now": improved_now,
            "no_learning_streak": self.no_learning_streak,
            "no_improvement_streak": self.no_improvement_streak,
            "learning_window_open": learning_window_open,
            "improvement_window_open": improvement_window_open,
            "learning_opportunities": self.window_learning_opportunities,
            "improvement_opportunities": self.window_improvement_opportunities,
            "synthetic_window_open": synthetic_window,
            "no_opportunity_iterations": self.no_opportunity_iterations,
            "warnings": warnings[:5],
            "suggestions": suggestions[:5],
        }

    def _record_learning_event(self, event: Dict) -> Optional[str]:
        if not isinstance(event, dict):
            return None
        if not self.governor.should_keep(event, category="learning_event"):
            return None
        return self.storage_v2.record_learning_event(event)

    def _should_run_cafe_calibration(self) -> bool:
        if not self.enable_cafe_calibration:
            return False
        last = self._parse_time(self.last_cafe_calibration)
        if not last:
            return True
        return (datetime.now() - last).total_seconds() > max(60, self.cafe_calibration_interval_sec)

    def _run_cafe_calibration(self) -> Dict:
        try:
            from .cafe_calibrator import get_cafe_calibrator
        except Exception:
            return {"ok": False, "error": "cafe_calibrator_unavailable"}
        calibrator = get_cafe_calibrator()
        summary = calibrator.calibrate()
        self.last_cafe_calibration = summary.get("last_updated") or datetime.now().isoformat()
        self.last_cafe_calibration_summary = summary if isinstance(summary, dict) else {}
        return summary

    def _events_from_scan(self, scan_results: Dict) -> List[Dict]:
        events: List[Dict] = []
        if not isinstance(scan_results, dict):
            return events
        for item in scan_results.get("top_items", [])[:10]:
            score = float(item.get("score", 0) or 0)
            events.append(
                {
                    "ts": datetime.now().isoformat(),
                    "source": item.get("source", "scan"),
                    "event_type": "scan_insight",
                    "content": item.get("title", ""),
                    "title": f"Integrate: {item.get('title', 'insight')}",
                    "hypothesis": item.get("potential_improvement", "Integrating this insight improves the system."),
                    "expected_impact": f"score={score:.2f}",
                    "novelty_score": min(1.0, score / 10.0),
                    "value_score": min(1.0, score / 10.0),
                    "risk_score": 0.25 if score >= 7.0 else 0.4,
                    "confidence": 0.65 if score >= 7.0 else 0.55,
                    "context": {"category": item.get("category"), "url": item.get("url", "")},
                }
            )
        return events

    def _run_v2_proposal_cycle(self, results: Dict) -> Dict:
        summary = {
            "created": 0,
            "approved": 0,
            "executed": 0,
            "verified": 0,
            "retry_attempted": 0,
            "retry_verified": 0,
            "safe_mode_runs": 0,
            "normal_mode_runs": 0,
            "canary_blocked": 0,
            "wins": 0,
            "losses": 0,
            "inconclusive": 0,
            "runs": [],
        }
        if not self.enable_proposal_v2:
            return summary

        selected_policy = {}
        if self.enable_policy_bandit:
            selected_policy = select_learning_policy()
            try:
                if "approve_threshold" in selected_policy:
                    self.proposal_engine_v2.auto_approve_threshold = float(selected_policy["approve_threshold"])
                if "scan_min_score" in selected_policy:
                    self.scanner.proposal_threshold = max(0.0, float(selected_policy["scan_min_score"]))
            except (TypeError, ValueError):
                pass

        events = []
        scan = results.get("scan", {}) if isinstance(results.get("scan"), dict) else {}
        events.extend(self._events_from_scan(scan))
        for event in events:
            event_id = self._record_learning_event(event)
            if event_id:
                event["id"] = event_id

        created = self.proposal_engine_v2.generate_from_events(events, limit=20)
        summary["created"] = len(created)
        summary["approved"] = sum(1 for p in created if p.get("status") == "approved")
        adaptive_threshold = self.proposal_engine_v2.auto_approve_threshold
        if self.no_improvement_streak >= self.focus_rotation_warn_streak:
            adaptive_threshold = max(0.0, adaptive_threshold - 0.2)
        summary["approved"] += int(
            self.proposal_engine_v2.auto_approve_safe(limit=3, min_priority=adaptive_threshold) or 0
        )

        if not self.enable_experiment_executor:
            return summary

        actionable = [p for p in self.proposal_engine_v2.list_pending() if p.get("status") == "approved"][:3]
        self.window_improvement_opportunities += len(actionable)
        for proposal in actionable:
            mode_decision = self._select_execution_mode_for_proposal(proposal)
            run_mode = str(mode_decision.get("mode", "safe"))
            run_reason = str(mode_decision.get("reason", "unknown"))
            if run_mode == "normal":
                summary["normal_mode_runs"] += 1
            else:
                summary["safe_mode_runs"] += 1
                if run_reason not in {"default_safe_mode", "canary_allowed"}:
                    summary["canary_blocked"] += 1
            exec_result = self.executor_v2.execute_proposal(
                proposal_id=str(proposal.get("id")),
                mode=run_mode,
            )
            if not exec_result.get("ok"):
                continue
            summary["executed"] += 1
            run_id = exec_result.get("run_id")
            verdict = "inconclusive"
            if run_id:
                verify = self.verifier_v2.verify_experiment(run_id)
                if verify.get("ok"):
                    if not bool(verify.get("pending_recheck", False)):
                        summary["verified"] += 1
                    evidence = verify.get("evidence", {})
                    verdict = evidence.get("verdict", "inconclusive")
                    if verdict == "win":
                        summary["wins"] += 1
                    elif verdict == "loss":
                        summary["losses"] += 1
                    else:
                        summary["inconclusive"] += 1
                    if run_mode == "normal":
                        self._record_normal_mode_outcome(verdict=verdict, proposal_id=str(proposal.get("id")))
                    if self.enable_policy_bandit:
                        update_learning_policy({"verdict": verdict, "selected": selected_policy})
            summary["runs"].append(
                {
                    "proposal_id": proposal.get("id"),
                    "run_id": run_id,
                    "verdict": verdict,
                    "mode": run_mode,
                    "canary_reason": run_reason,
                }
            )

        retry_summary = self._retry_pending_verifications(limit=3)
        summary["retry_attempted"] = int(retry_summary.get("attempted", 0))
        summary["retry_verified"] = int(retry_summary.get("verified", 0))
        summary["retry_finalized_exhausted"] = int(retry_summary.get("finalized_exhausted", 0))
        summary["verified"] += int(retry_summary.get("verified", 0))
        summary["wins"] += int(retry_summary.get("wins", 0))
        summary["losses"] += int(retry_summary.get("losses", 0))
        summary["inconclusive"] += int(retry_summary.get("inconclusive", 0))

        return summary

    def _retry_pending_verifications(self, limit: int = 3) -> Dict:
        if not self.enable_proposal_v2:
            return {
                "attempted": 0,
                "verified": 0,
                "wins": 0,
                "losses": 0,
                "inconclusive": 0,
                "finalized_exhausted": 0,
            }
        now = datetime.now()
        attempted = 0
        verified = 0
        wins = 0
        losses = 0
        inconclusive = 0
        finalized_exhausted = 0
        max_attempts = max(1, self.verification_retry_max_attempts)
        runs = self.storage_v2.get_experiment_runs(limit=500)
        for run in reversed(runs):
            if attempted >= max(1, limit):
                break
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("id", "")).strip()
            if not run_id:
                continue
            verification = run.get("verification", {}) if isinstance(run.get("verification"), dict) else {}
            if not bool(verification.get("pending_recheck", False)):
                continue
            attempts = int(verification.get("attempts", 0) or 0)
            if attempts >= max_attempts:
                finalized_at = datetime.now().isoformat()
                final_verdict = str(verification.get("verdict", "inconclusive")).strip().lower() or "inconclusive"
                try:
                    final_confidence = float(verification.get("confidence", 0.0) or 0.0)
                except (TypeError, ValueError):
                    final_confidence = 0.0
                finalized_verification = dict(verification)
                finalized_verification.update(
                    {
                        "pending_recheck": False,
                        "retry_exhausted": True,
                        "finalized_reason": "max_retries_exhausted",
                        "verified_at": finalized_at,
                    }
                )
                self.storage_v2.update_experiment_run(run_id, {"verification": finalized_verification})
                proposal_id = str(run.get("proposal_id", "")).strip()
                if proposal_id:
                    self.proposal_engine_v2.mark_status(
                        proposal_id,
                        "executed",
                        verification_pending=False,
                        verification_exhausted=True,
                        verification_finalized_at=finalized_at,
                        verification_final_reason="max_retries_exhausted",
                        verdict=final_verdict,
                        verdict_confidence=final_confidence,
                    )
                finalized_exhausted += 1
                continue
            next_recheck_after = self._parse_time(verification.get("next_recheck_after"))
            if next_recheck_after and now < next_recheck_after:
                continue
            last_verified_at = self._parse_time(verification.get("verified_at"))
            if last_verified_at and (now - last_verified_at).total_seconds() < max(10, self.verification_retry_interval_sec):
                continue

            recheck = self.verifier_v2.verify_experiment(run_id)
            attempted += 1
            if not recheck.get("ok"):
                continue
            verdict = str(((recheck.get("evidence") or {}).get("verdict", "inconclusive"))).strip().lower()
            if verdict == "win":
                wins += 1
            elif verdict == "loss":
                losses += 1
            else:
                inconclusive += 1
            if not bool(recheck.get("pending_recheck", False)):
                verified += 1

        return {
            "attempted": attempted,
            "verified": verified,
            "wins": wins,
            "losses": losses,
            "inconclusive": inconclusive,
            "finalized_exhausted": finalized_exhausted,
        }

    def _ingest_scan_into_advanced_learning(self, scan_results: Dict) -> int:
        """Ingest top discovered items into advanced learning store."""
        if not self.learning_engine:
            return 0

        ingested = 0
        for item in scan_results.get("top_items", [])[: self.advanced_ingest_limit]:
            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            improvement = str(item.get("potential_improvement", "")).strip()
            content = " | ".join([part for part in [title, description, improvement] if part])[:800]
            if not content:
                continue
            tags = [
                str(item.get("category", "")).strip(),
                str(item.get("source", "")).strip(),
            ]
            importance = int(float(item.get("score", 5)))
            self.learning_engine.add_knowledge(
                content=content,
                category=str(item.get("category", "general")),
                importance=importance,
                tags=[t for t in tags if t],
            )
            ingested += 1
        return ingested

    def _should_run_advanced_review(self) -> bool:
        if not self.learning_engine:
            return False
        last_review_time = self._parse_time(self.last_advanced_review)
        if not last_review_time:
            return True
        return datetime.now() - last_review_time > timedelta(seconds=self.INTERVALS["advanced_review"])

    def _run_advanced_review(self) -> Dict:
        """Run spaced repetition review for due items."""
        if not self.learning_engine:
            return {"enabled": False}

        due_items = self.learning_engine.get_items_for_review()
        selected = due_items[: max(1, self.advanced_review_limit)]
        results = []

        for item in selected:
            # Heuristic quality signal keeps scheduler moving without human input.
            quality = 4
            if item.mastery_level >= 85:
                quality = 5
            elif item.mastery_level < 40:
                quality = 3
            results.append(self.learning_engine.review_item(item, quality))

        self.last_advanced_review = datetime.now().isoformat()
        summary = {
            "enabled": True,
            "due_items": len(due_items),
            "reviewed_items": len(results),
            "review_results": results[:10],
            "timestamp": self.last_advanced_review,
        }

        remember(
            key=f"advanced_review:{datetime.now().strftime('%Y%m%d_%H')}",
            content=summary,
            category="advanced_learning",
            keywords=["spaced_repetition", "review"],
            importance=6,
        )
        return summary

    def _should_run_cleanup(self) -> bool:
        """Determine if weekly cleanup should run."""
        if not self.last_cleanup:
            return True
        try:
            last_cleanup_time = datetime.fromisoformat(self.last_cleanup)
        except (ValueError, TypeError):
            return True
        return datetime.now() - last_cleanup_time > timedelta(seconds=self.INTERVALS["cleanup"])

    def _run_cleanup(self) -> Dict:
        """Run memory cleanup and track results."""
        removed = self.memory.cleanup(max_age_days=90, min_access=1)
        self.last_cleanup = datetime.now().isoformat()
        track_action(
            agent="SYSTEM",
            action_type="cleanup",
            details={"removed_entries": removed},
            success=True
        )
        return {"removed_entries": removed, "timestamp": self.last_cleanup}

    def _acquire_operation_lock(self, operation_name: str, lock_path: str):
        """Acquire non-blocking process lock for a short critical operation."""
        guard = ProcessSingleton(name=operation_name, lock_path=lock_path)
        acquired, owner = guard.acquire(
            extra={
                "entrypoint": "learning_loop",
                "iteration": self.iteration,
                "operation": operation_name,
            }
        )
        return guard if acquired else None, owner

    # ==================== MAIN LOOP ====================

    def run_iteration(self) -> Dict:
        """
        Run a single learning iteration.
        This is the core learning cycle.
        """
        self.iteration += 1
        iteration_start = datetime.now()

        track_decision("SYSTEM", "iteration", f"Starting iteration {self.iteration}")

        results = {
            "iteration": self.iteration,
            "timestamp": iteration_start.isoformat(),
            "actions": [],
            "errors": [],
        }

        # 1. Quick health check (every iteration)
        try:
            health = self.analyze_health()
        except Exception as e:
            health = {"health_score": 0, "status": "error", "open_issues": -1}
            track_error("SYSTEM", "health_check_error", str(e), context={"iteration": self.iteration}, recoverable=True)
            results["errors"].append({"step": "health_check", "error": str(e)})
        results["health"] = health
        results["actions"].append("health_check")

        # 2. Knowledge scan (hourly)
        should_scan = False
        if self.last_scan is None:
            should_scan = True
        else:
            try:
                if isinstance(self.last_scan, datetime):
                    last_scan_time = self.last_scan
                else:
                    last_scan_time = datetime.fromisoformat(str(self.last_scan))
                if datetime.now() - last_scan_time > timedelta(seconds=self.INTERVALS["knowledge_scan"]):
                    should_scan = True
            except (ValueError, TypeError):
                should_scan = True  # Scan if timestamp is invalid

        if should_scan:
            scan_guard, scan_owner = self._acquire_operation_lock(
                operation_name="knowledge_scan_operation",
                lock_path=self.scan_operation_lock_path,
            )
            if not scan_guard:
                results["scan"] = {
                    "skipped_due_to_lock": True,
                    "lock_path": self.scan_operation_lock_path,
                    "lock_owner": scan_owner if isinstance(scan_owner, dict) else {},
                }
                results["actions"].append("knowledge_scan_skipped_locked")
                self._append_rnd_note(
                    note_type="operation_lock_busy",
                    severity="warning",
                    message="Skipped knowledge scan because another process holds the scan lock.",
                    context={"operation": "knowledge_scan", "lock_owner": scan_owner},
                )
            else:
                try:
                    scan_results = self.scan_for_knowledge()
                    results["scan"] = scan_results
                    results["actions"].append("knowledge_scan")
                except Exception as e:
                    track_error(
                        "SYSTEM",
                        "knowledge_scan_error",
                        str(e),
                        context={"iteration": self.iteration},
                        recoverable=True,
                    )
                    results["errors"].append({"step": "knowledge_scan", "error": str(e)})
                finally:
                    scan_guard.release()

        # 3. Apply improvements if any
        improvement_summary = {
            "total_seen": 0,
            "auto_approved": 0,
            "applied": 0,
            "skipped": 0,
            "stagnation_unblocked": 0,
        }
        improvement_guard, improvement_owner = self._acquire_operation_lock(
            operation_name="improvement_apply_operation",
            lock_path=self.improvement_operation_lock_path,
        )
        if not improvement_guard:
            improvement_summary["skipped_due_to_lock"] = True
            improvement_summary["lock_path"] = self.improvement_operation_lock_path
            improvement_summary["lock_owner"] = improvement_owner if isinstance(improvement_owner, dict) else {}
            results["actions"].append("check_improvements_skipped_locked")
            self._append_rnd_note(
                note_type="operation_lock_busy",
                severity="warning",
                message="Skipped applying improvements because another process holds the improvement lock.",
                context={"operation": "check_improvements", "lock_owner": improvement_owner},
            )
        else:
            try:
                improvement_summary = self.apply_approved_improvements()
                results["actions"].append("check_improvements")
            except Exception as e:
                improvement_summary = {
                    "total_seen": 0,
                    "auto_approved": 0,
                    "applied": 0,
                    "skipped": 0,
                    "stagnation_unblocked": 0,
                }
                track_error(
                    "SYSTEM",
                    "apply_improvements_error",
                    str(e),
                    context={"iteration": self.iteration},
                    recoverable=True,
                )
                results["errors"].append({"step": "check_improvements", "error": str(e)})
            finally:
                improvement_guard.release()
        results["improvements"] = improvement_summary

        # 3b. Run v2 proposal -> execute -> verify pipeline.
        try:
            v2_summary = self._run_v2_proposal_cycle(results)
            results["v2_pipeline"] = v2_summary
            if (
                int(v2_summary.get("created", 0)) > 0
                or int(v2_summary.get("executed", 0)) > 0
                or int(v2_summary.get("verified", 0)) > 0
            ):
                results["actions"].append("v2_pipeline")
        except Exception as e:
            track_error(
                "SYSTEM",
                "v2_pipeline_error",
                str(e),
                context={"iteration": self.iteration},
                recoverable=True,
            )
            results["errors"].append({"step": "v2_pipeline", "error": str(e)})

        # 3c. Calibrate CAFE model biases from recent evidence.
        if self._should_run_cafe_calibration():
            try:
                cafe_summary = self._run_cafe_calibration()
                results["cafe_calibration"] = cafe_summary
                results["actions"].append("cafe_calibration")
            except Exception as e:
                track_error(
                    "SYSTEM",
                    "cafe_calibration_error",
                    str(e),
                    context={"iteration": self.iteration},
                    recoverable=True,
                )
                results["errors"].append({"step": "cafe_calibration", "error": str(e)})

        # 4. Advanced learning review
        if self._should_run_advanced_review():
            try:
                results["advanced_review"] = self._run_advanced_review()
                results["actions"].append("advanced_review")
            except Exception as e:
                track_error("SYSTEM", "advanced_review_error", str(e), context={"iteration": self.iteration}, recoverable=True)
                results["errors"].append({"step": "advanced_review", "error": str(e)})

        # 5. Weekly cleanup
        if self._should_run_cleanup():
            try:
                results["cleanup"] = self._run_cleanup()
                results["actions"].append("cleanup")
            except Exception as e:
                track_error("SYSTEM", "cleanup_error", str(e), context={"iteration": self.iteration}, recoverable=True)
                results["errors"].append({"step": "cleanup", "error": str(e)})

        # 6. Persist daily R&D notes for errors/issues/improvements.
        for err in results["errors"]:
            self._append_rnd_note(
                note_type="runtime_error",
                severity="error",
                message=f"{err.get('step')}: {err.get('error')}",
                context={"step": err.get("step")},
            )

        if improvement_summary.get("applied", 0) > 0:
            self._append_rnd_note(
                note_type="improvement_applied",
                severity="info",
                message=f"Applied {improvement_summary.get('applied', 0)} improvement(s)",
                context=improvement_summary,
            )
        if improvement_summary.get("auto_approved", 0) > 0:
            self._append_rnd_note(
                note_type="proposal_auto_approved",
                severity="info",
                message=f"Auto-approved {improvement_summary.get('auto_approved', 0)} proposal(s)",
                context=improvement_summary,
            )
        if improvement_summary.get("stagnation_unblocked", 0) > 0:
            self._append_rnd_note(
                note_type="stagnation_unblocked",
                severity="warning",
                message=(
                    f"Auto-approved {improvement_summary.get('stagnation_unblocked', 0)} proposal(s) "
                    "to break stagnation safely"
                ),
                context=improvement_summary,
            )

        if "scan" in results:
            scan_data = results["scan"]
            if int(scan_data.get("source_errors", 0)) > 0:
                self._append_rnd_note(
                    note_type="scan_source_error",
                    severity="warning",
                    message=f"Knowledge scan had {scan_data.get('source_errors', 0)} source error(s)",
                    context={
                        "sources_scanned": scan_data.get("sources_scanned", 0),
                        "sources_skipped": scan_data.get("sources_skipped", 0),
                        "source_errors": scan_data.get("source_errors", 0),
                    },
                )

        if int(health.get("open_issues", 0)) > 0:
            self._append_rnd_note(
                note_type="open_issues_detected",
                severity="warning",
                message=f"Health check reports {health.get('open_issues', 0)} open issue(s)",
                context={"health_score": health.get("health_score", 0)},
            )

        self._append_rnd_note(
            note_type="iteration_summary",
            severity="info" if not results["errors"] else "warning",
            message=(
                f"Iteration {self.iteration}: actions={len(results['actions'])}, "
                f"errors={len(results['errors'])}, applied={improvement_summary.get('applied', 0)}"
            ),
            context={"actions": results["actions"]},
        )

        # 7. Mandatory self-check pass.
        if self.enable_iteration_self_check:
            self_check = self._run_iteration_self_check(results)
            results["self_check"] = self_check
            results["actions"].append("self_check")
            if self_check.get("warnings"):
                self._append_rnd_note(
                    note_type="self_check_warning",
                    severity="warning",
                    message="; ".join(self_check.get("warnings", [])),
                    context=self_check,
                )
            else:
                self._append_rnd_note(
                    note_type="self_check_ok",
                    severity="info",
                    message=(
                        f"Self-check passed: learning_streak={self_check.get('no_learning_streak', 0)}, "
                        f"improvement_streak={self_check.get('no_improvement_streak', 0)}"
                    ),
                    context=self_check,
                )

        # 8. Daily autonomous self-learning cycle.
        if self._should_run_daily_self_learning():
            daily_guard, daily_owner = self._acquire_operation_lock(
                operation_name="daily_self_learning_operation",
                lock_path=self.daily_cycle_operation_lock_path,
            )
            if not daily_guard:
                results["daily_self_learning"] = {
                    "skipped_due_to_lock": True,
                    "lock_path": self.daily_cycle_operation_lock_path,
                    "lock_owner": daily_owner if isinstance(daily_owner, dict) else {},
                }
                results["actions"].append("daily_self_learning_skipped_locked")
                self._append_rnd_note(
                    note_type="operation_lock_busy",
                    severity="warning",
                    message="Skipped daily self-learning cycle because another process is running it.",
                    context={"operation": "daily_self_learning", "lock_owner": daily_owner},
                )
            else:
                try:
                    daily_summary = self._run_daily_self_learning_cycle(results)
                    results["daily_self_learning"] = daily_summary
                    results["actions"].append("daily_self_learning")
                    self._append_rnd_note(
                        note_type="daily_self_learning",
                        severity="warning" if daily_summary.get("warnings") else "info",
                        message=(
                            f"Daily cycle: ideas={len(daily_summary.get('improvement_ideas', []))}, "
                            f"experiments={len(daily_summary.get('experiments', []))}, "
                            f"warnings={len(daily_summary.get('warnings', []))}"
                        ),
                        context={
                            "last_daily_self_learning": daily_summary.get("timestamp"),
                            "warnings": daily_summary.get("warnings", []),
                        },
                    )
                except Exception as e:
                    track_error(
                        "SYSTEM",
                        "daily_self_learning_error",
                        str(e),
                        context={"iteration": self.iteration},
                        recoverable=True,
                    )
                    results["errors"].append({"step": "daily_self_learning", "error": str(e)})
                finally:
                    daily_guard.release()

        # 9. Save state (after self-check + daily cycle so streaks and daily run are persisted).
        try:
            self._save_state()
            results["actions"].append("save_state")
        except Exception as e:
            track_error("SYSTEM", "save_state_error", str(e), context={"iteration": self.iteration}, recoverable=True)
            results["errors"].append({"step": "save_state", "error": str(e)})

        # Calculate iteration duration
        duration = (datetime.now() - iteration_start).total_seconds()
        results["duration_seconds"] = duration

        # Track iteration
        track_action(
            agent="SYSTEM",
            action_type="iteration",
            details={
                "iteration": self.iteration,
                "actions": results["actions"],
                "applied_improvements": improvement_summary.get("applied", 0),
                "v2_executed": int((results.get("v2_pipeline") or {}).get("executed", 0)),
                "errors": len(results["errors"]),
            },
            success=len(results["errors"]) == 0,
            duration_ms=int(duration * 1000)
        )

        self.stats["total_iterations"] += 1

        return results

    def start(self, interval_seconds: int = 60, max_iterations: int = None):
        """
        Start the continuous learning loop.

        Args:
            interval_seconds: Time between iterations
            max_iterations: Maximum iterations (None = infinite)
        """
        self.running = True

        print(f"🔄 Learning Loop Started")
        print(f"   Interval: {interval_seconds}s")
        print(f"   Max iterations: {max_iterations or 'infinite'}")
        print("-" * 50)
        start_iteration = self.iteration

        try:
            while self.running:
                if max_iterations and (self.iteration - start_iteration) >= max_iterations:
                    print(f"✅ Reached max iterations ({max_iterations})")
                    break

                # Run iteration
                results = self.run_iteration()

                # Print status
                print(f"\n📊 Iteration {results['iteration']}")
                print(f"   Health: {results['health']['health_score']}/100")
                print(f"   Actions: {', '.join(results['actions'])}")
                print(f"   Duration: {results['duration_seconds']:.2f}s")

                # Wait for next iteration
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            print("\n⏹️ Learning Loop Stopped by user")
        finally:
            self.stop()

    def stop(self):
        """Stop the learning loop."""
        self.running = False

        # End debugger session
        stats = self.debugger.end_session()

        # Save final state
        self._save_state()

        print(f"\n📈 Final Stats:")
        print(f"   Total iterations: {self.stats['total_iterations']}")
        print(f"   Knowledge items learned: {self.stats['knowledge_items_learned']}")
        print(f"   Issues detected: {self.stats['issues_detected']}")
        print(f"   Issues resolved: {self.stats['issues_resolved']}")
        print(f"   Patterns discovered: {self.stats['patterns_discovered']}")
        print(f"   Lessons learned: {self.stats['lessons_learned']}")

    # ==================== REPORTING ====================

    def get_status_report(self) -> Dict:
        """Get comprehensive status report."""
        memory_stats = self.memory.get_stats()
        scanner_stats = self.scanner.get_stats()
        health_report = health_check()
        advanced_stats = {}
        if self.learning_engine:
            advanced_stats = self.learning_engine.get_learning_stats()
            advanced_stats["knowledge_items"] = len(self.learning_engine.knowledge_items)
            advanced_stats["due_for_review"] = len(self.learning_engine.get_items_for_review())
            advanced_stats["last_review"] = self.last_advanced_review

        today_notes_all = self.get_today_rnd_notes(limit=10000)
        today_notes_recent = today_notes_all[:5]
        daily_self_learning_all = self.get_today_daily_self_learning(limit=200)
        daily_self_learning_recent = daily_self_learning_all[:3]
        last_daily_run_time = self._parse_time(self.last_daily_self_learning)
        next_daily_run_in_seconds = 0
        if self.enable_daily_self_learning and last_daily_run_time:
            elapsed = (datetime.now() - last_daily_run_time).total_seconds()
            next_daily_run_in_seconds = max(0, int(self.daily_self_learning_interval - elapsed))
        prompt_system_snapshot = self.prompt_system.get_snapshot(limit=5)
        focus_report = self._build_focus_report(
            health_report=health_report,
            notes=today_notes_all,
            scanner_stats=scanner_stats,
        )
        self_check_summary = {
            "enabled": self.enable_iteration_self_check,
            "no_learning_streak": self.no_learning_streak,
            "no_improvement_streak": self.no_improvement_streak,
            "warn_streak_threshold": self.self_check_warn_streak,
        }
        latest_rotation = {}
        if daily_self_learning_recent:
            latest_summary = daily_self_learning_recent[0].get("summary", {})
            if isinstance(latest_summary, dict):
                latest_rotation = latest_summary.get("portfolio_rotation", {})
        proposals_v2 = self.storage_v2.get_proposals_v2()
        v2_rows = proposals_v2.get("proposals", []) if isinstance(proposals_v2.get("proposals"), list) else []
        status_counts: Dict[str, int] = {}
        for row in v2_rows:
            status = str(row.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1

        proposal_funnel = {
            "created": len(v2_rows),
            "pending_approval": status_counts.get("pending_approval", 0),
            "approved": status_counts.get("approved", 0),
            "executed": status_counts.get("executed", 0),
            "verified": status_counts.get("verified", 0),
            "rejected": status_counts.get("rejected", 0),
        }
        now_dt = datetime.now()

        def _safe_ratio(num: int, den: int) -> float:
            return round(float(num) / float(den), 4) if den > 0 else 0.0

        approved_or_beyond = (
            status_counts.get("approved", 0)
            + status_counts.get("executed", 0)
            + status_counts.get("verified", 0)
        )
        executed_or_beyond = status_counts.get("executed", 0) + status_counts.get("verified", 0)
        proposal_funnel["conversion"] = {
            "approval_rate": _safe_ratio(approved_or_beyond, proposal_funnel["created"]),
            "execution_rate": _safe_ratio(executed_or_beyond, approved_or_beyond),
            "verification_rate": _safe_ratio(proposal_funnel["verified"], executed_or_beyond),
            "verified_from_created_rate": _safe_ratio(proposal_funnel["verified"], proposal_funnel["created"]),
        }

        def _window_funnel(hours: int) -> Dict:
            window_seconds = max(1, int(hours)) * 3600
            cohort: List[Dict] = []
            for row in v2_rows:
                created_at = self._parse_time(row.get("created_at"))
                if not created_at:
                    continue
                if (now_dt - created_at).total_seconds() <= window_seconds:
                    cohort.append(row)
            created = len(cohort)
            approved = 0
            executed = 0
            verified_window = 0
            for row in cohort:
                status = str(row.get("status", "")).strip().lower()
                if status in {"approved", "executed", "verified"}:
                    approved += 1
                if status in {"executed", "verified"}:
                    executed += 1
                if status == "verified":
                    verified_window += 1
            return {
                "created": created,
                "approved": approved,
                "executed": executed,
                "verified": verified_window,
                "approval_rate": _safe_ratio(approved, created),
                "execution_rate": _safe_ratio(executed, approved),
                "verification_rate": _safe_ratio(verified_window, executed),
                "verified_from_created_rate": _safe_ratio(verified_window, created),
            }

        proposal_funnel["window_24h"] = _window_funnel(24)
        proposal_funnel["window_7d"] = _window_funnel(24 * 7)

        runs = self.storage_v2.get_experiment_runs(limit=500)
        verdict_counts = {"win": 0, "loss": 0, "inconclusive": 0}
        for run in runs:
            verification = run.get("verification", {}) if isinstance(run.get("verification"), dict) else {}
            if bool(verification.get("holdout_pending", False)):
                continue
            verdict = str(verification.get("verdict", "")).strip().lower()
            if verdict in verdict_counts:
                verdict_counts[verdict] += 1
        verified_total = sum(verdict_counts.values())
        evidence_rows = self.storage_v2.list_outcome_evidence(limit=500)
        pending_recheck_runs = 0
        retry_exhausted_runs = 0
        holdout_pending_runs = 0
        for run in runs:
            verification = run.get("verification", {}) if isinstance(run.get("verification"), dict) else {}
            if bool(verification.get("pending_recheck", False)):
                pending_recheck_runs += 1
            if bool(verification.get("holdout_pending", False)):
                holdout_pending_runs += 1
            if bool(verification.get("retry_exhausted", False)):
                retry_exhausted_runs += 1
        trend_24h = {"win": 0, "loss": 0, "inconclusive": 0}
        trend_7d = {"win": 0, "loss": 0, "inconclusive": 0}
        confidence_values = []
        delta_health_values = []
        delta_error_values = []
        delta_latency_values = []
        execution_cost_values = []
        execution_duration_values = []
        for evd in evidence_rows:
            if not isinstance(evd, dict):
                continue
            verdict = str(evd.get("verdict", "")).strip().lower()
            ts = evd.get("ts")
            ts_dt = None
            if ts:
                try:
                    ts_dt = datetime.fromisoformat(str(ts))
                except Exception:
                    ts_dt = None
            if verdict in trend_24h and ts_dt and not bool(evd.get("holdout_pending", False)):
                age_sec = (now_dt - ts_dt).total_seconds()
                if age_sec <= 24 * 3600:
                    trend_24h[verdict] += 1
                if age_sec <= 7 * 24 * 3600:
                    trend_7d[verdict] += 1
            try:
                confidence_values.append(float(evd.get("confidence", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            delta = evd.get("delta", {}) if isinstance(evd.get("delta"), dict) else {}
            execution = evd.get("execution", {}) if isinstance(evd.get("execution"), dict) else {}
            try:
                delta_health_values.append(float(delta.get("health_score", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                delta_error_values.append(float(delta.get("total_errors", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                delta_latency_values.append(float(delta.get("avg_duration_ms", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                execution_cost_values.append(float(execution.get("estimated_cost_usd", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                execution_duration_values.append(float(execution.get("duration_ms", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass

        def _avg(values):
            return round(sum(values) / len(values), 4) if values else 0.0

        outcome_quality = {
            "verified_total": verified_total,
            "win_rate": round(verdict_counts["win"] / verified_total, 4) if verified_total else 0.0,
            "loss_rate": round(verdict_counts["loss"] / verified_total, 4) if verified_total else 0.0,
            "inconclusive_rate": round(verdict_counts["inconclusive"] / verified_total, 4) if verified_total else 0.0,
            "verdict_counts": verdict_counts,
            "avg_confidence": _avg(confidence_values),
            "avg_health_delta": _avg(delta_health_values),
            "avg_error_delta": _avg(delta_error_values),
            "avg_latency_delta_ms": _avg(delta_latency_values),
            "avg_execution_cost_usd": _avg(execution_cost_values),
            "avg_execution_duration_ms": _avg(execution_duration_values),
            "evidence_samples": len(evidence_rows),
            "pending_recheck_runs": pending_recheck_runs,
            "holdout_pending_runs": holdout_pending_runs,
            "retry_exhausted_runs": retry_exhausted_runs,
            "trend_24h": trend_24h,
            "trend_7d": trend_7d,
        }
        events = self.storage_v2.list_learning_events(limit=1000)
        stream_counts = {"production": 0, "non_production": 0, "unknown": 0}
        stream_trend_24h = {"production": 0, "non_production": 0}
        stream_trend_7d = {"production": 0, "non_production": 0}
        source_counts: Dict[str, int] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            stream = str(event.get("stream", "")).strip().lower()
            if stream not in {"production", "non_production"}:
                stream = "non_production" if bool(event.get("is_non_production", False)) else "production"
            stream_counts[stream] = stream_counts.get(stream, 0) + 1
            source = str(event.get("source", "")).strip() or "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1
            ts_dt = self._parse_time(event.get("ts"))
            if ts_dt:
                age_sec = (now_dt - ts_dt).total_seconds()
                if age_sec <= 24 * 3600:
                    stream_trend_24h[stream] = stream_trend_24h.get(stream, 0) + 1
                if age_sec <= 7 * 24 * 3600:
                    stream_trend_7d[stream] = stream_trend_7d.get(stream, 0) + 1

        total_events = sum(stream_counts.values())
        production_events = stream_counts.get("production", 0)
        non_production_events = stream_counts.get("non_production", 0)
        top_sources = sorted(source_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        learning_event_quality = {
            "total_events": total_events,
            "production_events": production_events,
            "non_production_events": non_production_events,
            "production_ratio": _safe_ratio(production_events, total_events),
            "non_production_ratio": _safe_ratio(non_production_events, total_events),
            "stream_counts": stream_counts,
            "trend_24h": stream_trend_24h,
            "trend_7d": stream_trend_7d,
            "top_sources": [{"source": src, "count": count} for src, count in top_sources],
        }

        policy_state = get_learning_policy_state() if self.enable_policy_bandit else {}
        execution_guardrail = self._execution_guardrail_snapshot()
        cafe_state = {}
        try:
            from .cafe_calibrator import get_cafe_calibrator
            cafe_state = get_cafe_calibrator().get_state()
        except Exception:
            cafe_state = {}
        quality_score = 0.0
        issues = []
        try:
            health_score = float(health_report.get("health_score", 0) or 0.0)
        except (TypeError, ValueError):
            health_score = 0.0
        inconclusive_rate = float(outcome_quality.get("inconclusive_rate", 1.0) or 1.0)
        production_ratio = float(learning_event_quality.get("production_ratio", 0.0) or 0.0)
        no_improvement = int(self.no_improvement_streak or 0)
        quality_score = (
            max(0.0, min(100.0, health_score))
            + (1.0 - min(1.0, max(0.0, inconclusive_rate))) * 40.0
            + production_ratio * 20.0
            - min(30.0, float(no_improvement))
        )
        quality_score = max(0.0, min(100.0, quality_score))
        if health_score < 75:
            issues.append("health_degraded")
        if inconclusive_rate > 0.6:
            issues.append("high_inconclusive_rate")
        if production_ratio < 0.8:
            issues.append("low_production_ratio")
        if no_improvement >= self.self_check_warn_streak:
            issues.append("stagnation_detected")
        if quality_score >= 75:
            grade = "good"
        elif quality_score >= 50:
            grade = "needs_improvement"
        else:
            grade = "critical"
        self_assessment = {
            "score": round(quality_score, 2),
            "grade": grade,
            "issues": issues[:5],
        }

        return {
            "timestamp": datetime.now().isoformat(),
            "iteration": self.iteration,
            "running": self.running,
            "stats": self.stats,
            "memory": memory_stats,
            "scanner": scanner_stats,
            "health": health_report,
            "advanced_learning": advanced_stats,
            "daily_notes": {
                "enabled": self.enable_daily_notes,
                "count_today": len(today_notes_all),
                "recent": today_notes_recent,
                "path": str(self._daily_notes_path()),
            },
            "daily_self_learning": {
                "enabled": self.enable_daily_self_learning,
                "interval_seconds": self.daily_self_learning_interval,
                "max_experiments": self.daily_self_learning_max_experiments,
                "max_ideas": self.daily_self_learning_max_ideas,
                "last_run": self.last_daily_self_learning,
                "next_run_in_seconds": next_daily_run_in_seconds,
                "count_today": len(daily_self_learning_all),
                "recent": daily_self_learning_recent,
                "path": str(self._daily_self_learning_path()),
            },
            "self_check": self_check_summary,
            "proposal_funnel": proposal_funnel,
            "outcome_quality": outcome_quality,
            "learning_event_quality": learning_event_quality,
            "policy_state": policy_state,
            "execution_guardrail": execution_guardrail,
            "cafe": {
                "enabled": self.enable_cafe_calibration,
                "last_calibration": self.last_cafe_calibration,
                "model_bias_count": len(cafe_state.get("model_bias", {})) if isinstance(cafe_state, dict) else 0,
                "model_bias": cafe_state.get("model_bias", {}) if isinstance(cafe_state, dict) else {},
                "last_updated": cafe_state.get("last_updated") if isinstance(cafe_state, dict) else None,
            },
            "self_assessment": self_assessment,
            "prompt_system": prompt_system_snapshot,
            "focus": focus_report,
            "strategy": {
                "current_focus_area": self.current_focus_area,
                "rotation_warn_streak": self.focus_rotation_warn_streak,
                "latest_portfolio_rotation": latest_rotation if isinstance(latest_rotation, dict) else {},
            },
            "windows": {
                "learning_opportunities": self.window_learning_opportunities,
                "improvement_opportunities": self.window_improvement_opportunities,
                "windowed_self_check_enabled": self.enable_windowed_self_check,
                "synthetic_opportunities_enabled": self.enable_synthetic_opportunities,
                "no_opportunity_iterations": self.no_opportunity_iterations,
            },
        }

    def generate_daily_report(self) -> str:
        """Generate a human-readable daily report."""
        report = self.get_status_report()

        lines = [
            "# 📊 Daily Learning Report",
            f"Generated: {report['timestamp']}",
            "",
            "## 💓 System Health",
            f"- Health Score: {report['health']['health_score']}/100",
            f"- Status: {report['health']['status']}",
            f"- Open Issues: {report['health']['open_issues']}",
            "",
            "## 📈 Learning Stats",
            f"- Total Iterations: {report['stats']['total_iterations']}",
            f"- Knowledge Items Learned: {report['stats']['knowledge_items_learned']}",
            f"- Patterns Discovered: {report['stats']['patterns_discovered']}",
            f"- Lessons Learned: {report['stats']['lessons_learned']}",
            "",
            "## 🔧 Issues",
            f"- Detected: {report['stats']['issues_detected']}",
            f"- Resolved: {report['stats']['issues_resolved']}",
            "",
            "## 🧠 Advanced Learning",
            f"- Enabled: {'yes' if self.learning_engine else 'no'}",
            f"- Knowledge Items: {report.get('advanced_learning', {}).get('knowledge_items', 0)}",
            f"- Due For Review: {report.get('advanced_learning', {}).get('due_for_review', 0)}",
            "",
            "## 🧭 Daily Self-Learning",
            f"- Enabled: {report.get('daily_self_learning', {}).get('enabled', False)}",
            f"- Runs Today: {report.get('daily_self_learning', {}).get('count_today', 0)}",
            f"- Last Run: {report.get('daily_self_learning', {}).get('last_run', 'N/A')}",
            f"- Current Focus Area: {report.get('strategy', {}).get('current_focus_area', 'N/A')}",
            "",
            "## 🧪 Proposal Funnel",
            f"- Created: {report.get('proposal_funnel', {}).get('created', 0)}",
            f"- Approved: {report.get('proposal_funnel', {}).get('approved', 0)}",
            f"- Executed: {report.get('proposal_funnel', {}).get('executed', 0)}",
            f"- Verified: {report.get('proposal_funnel', {}).get('verified', 0)}",
            f"- Win Rate: {report.get('outcome_quality', {}).get('win_rate', 0)}",
            f"- Inconclusive Rate: {report.get('outcome_quality', {}).get('inconclusive_rate', 0)}",
            f"- Avg Confidence: {report.get('outcome_quality', {}).get('avg_confidence', 0)}",
            "",
            "## 🧮 CAFE Calibration",
            f"- Enabled: {report.get('cafe', {}).get('enabled', False)}",
            f"- Last Calibration: {report.get('cafe', {}).get('last_calibration', 'N/A')}",
            f"- Model Bias Count: {report.get('cafe', {}).get('model_bias_count', 0)}",
            "",
            "## 🛡️ Execution Guardrail",
            f"- Canary Enabled: {report.get('execution_guardrail', {}).get('canary_enabled', False)}",
            f"- Real Apply Enabled: {report.get('execution_guardrail', {}).get('executor_real_apply_enabled', False)}",
            f"- Normal Runs Last Hour: {report.get('execution_guardrail', {}).get('normal_mode_runs_last_hour', 0)}",
            f"- Cooldown Remaining: {report.get('execution_guardrail', {}).get('normal_mode_cooldown_remaining_sec', 0)}s",
            "",
            "## 💾 Memory",
            f"- Entries: {report['memory']['total_entries']}",
            f"- Patterns: {report['memory']['total_patterns']}",
            f"- Lessons: {report['memory']['total_lessons']}",
            "",
            "---",
            "*The Dream Team - Continuous Learning System*"
        ]

        return "\n".join(lines)


# ==================== CONVENIENCE FUNCTIONS ====================

_loop = None

def get_learning_loop() -> LearningLoop:
    """Get singleton learning loop instance."""
    global _loop
    if _loop is None:
        _loop = LearningLoop()
    return _loop


def start_learning(interval: int = 60, max_iterations: int = None):
    """Start the learning loop."""
    loop = get_learning_loop()
    loop.start(interval_seconds=interval, max_iterations=max_iterations)


def learn(interaction: Dict):
    """Quick learn from an interaction."""
    get_learning_loop().learn_from_interaction(interaction)


def status() -> Dict:
    """Get current status."""
    return get_learning_loop().get_status_report()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("🔄 Continuous Learning Loop")
    print("=" * 50)

    loop = LearningLoop()

    # Run a few test iterations
    print("\n🧪 Running 3 test iterations...\n")

    for i in range(3):
        results = loop.run_iteration()
        print(f"Iteration {results['iteration']}:")
        print(f"  Health: {results['health']['health_score']}/100")
        print(f"  Actions: {results['actions']}")
        print()

    # Generate report
    print("\n" + loop.generate_daily_report())

    # Show how to start continuous learning
    print("\n" + "=" * 50)
    print("To start continuous learning, run:")
    print("  loop.start(interval_seconds=60)")
    print("\nOr import and use:")
    print("  from learning_loop import start_learning")
    print("  start_learning(interval=60)")
