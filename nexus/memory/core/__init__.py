"""
The Dream Team - Self-Learning System
=====================================

A continuous learning system that:
- Stores and retrieves knowledge efficiently
- Scans external sources for new knowledge
- Detects and fixes issues automatically
- Learns from every interaction

Usage:
    from memory import (
        # Memory
        remember, recall, learn_pattern, learn_lesson,

        # Knowledge Scanner
        scan_knowledge, get_pending_improvements,

        # Self-Debugger
        track_decision, track_action, track_error, health_check,

        # Learning Loop
        start_learning, learn, status
    )

    # Quick start
    start_learning(interval=60)  # Start learning every 60 seconds

    # Or manual learning
    learn({
        "agent": "NOVA",
        "type": "code_generation",
        "success": True,
        "details": {"file": "app.tsx"}
    })
"""

from .memory_manager import (
    MemoryManager,
    get_memory,
    remember,
    recall,
    learn_pattern,
    learn_lesson
)

from .knowledge_scanner import (
    KnowledgeScanner,
    KnowledgeSource,
    get_scanner,
    scan_knowledge,
    get_pending_improvements
)

from .self_debugger import (
    SelfDebugger,
    Severity,
    IssueType,
    get_debugger,
    track_decision,
    track_action,
    track_error,
    health_check
)

from .learning_loop import (
    LearningLoop,
    get_learning_loop,
    start_learning,
    learn,
    status
)

from .advanced_learning import (
    AdvancedLearningEngine,
    get_learning_engine,
    run_daily_learning,
)

# NEW: Human-Like Learning System
from .user_feedback import (
    UserFeedbackManager,
    get_feedback_manager,
    record_thumbs_up,
    record_thumbs_down,
    record_correction,
    record_approval,
    record_denial,
    should_auto_approve,
    get_preference,
    feedback_stats,
)

from .user_profile import (
    get_user_profile,
    update_user_profile,
    render_user_profile_block,
)

from .judgment_engine import (
    JudgmentEngine,
    get_judgment_engine,
    analyze_situation,
    should_proceed,
    record_decision,
    requires_approval,
)

from .audit_logger import (
    AuditLogger,
    get_audit_logger,
    audit_log,
    log_learning,
    log_decision,
    log_user_feedback,
    log_approval,
    get_audit_stats,
)

from .web_search_learner import (
    WebSearchLearner,
    get_web_learner,
    search_web,
    learn_from_web,
    get_trending,
    run_auto_discovery,
)

from .learning_prioritizer import (
    LearningPrioritizer,
    get_prioritizer,
    add_learning_topic,
    get_next_to_learn,
    prioritize_learning,
    get_search_queries,
)

from .daily_intelligence import (
    DailyIntelligence,
    get_daily_intelligence,
    morning_query,
    evening_reflection,
    add_insight,
    get_today_summary,
)

from .control_center import (
    ControlCenter,
    get_control_center,
    request_approval,
    approve_request,
    deny_request,
    get_pending_approvals,
    set_control_level,
    get_control_level,
)

from .daily_report import (
    DailyReportGenerator,
    get_report_generator,
    generate_daily_report,
    save_daily_report,
    get_recent_reports,
)

from .human_like_learning import (
    HumanLikeLearningSystem,
    get_hls,
    learn_from_user,
    analyze_and_decide,
    run_daily_cycle,
    get_status,
    full_report,
)

from .autonomous_improver import (
    AutonomousImprover,
    get_autonomous_improver,
    learn_from_input,
    run_auto_improvement,
    get_autonomous_stats,
)

from .chat_learner import (
    ChatLearner,
    get_chat_learner,
    learn_from_chat,
    learn_correction,
    get_chat_summary,
)

from .nexus_brain import (
    NexusBrain,
    get_nexus_brain,
    process_user_message,
    process_user_correction,
    process_user_feedback,
    run_improvement,
    nexus_status,
    get_all_learnings,
)

from .nexus_integration import (
    NexusIntegration,
    get_nexus,
    nexus_learn,
    nexus_learn_response,
    nexus_improve,
    nexus_report,
)

# NEW: API-Based Learning (reliable knowledge fetching)
from .api_learner import (
    APIKnowledgeFetcher,
    APISource,
    get_api_fetcher,
    fetch_knowledge_from_apis,
)

# NEW: User Feedback Learner (learns from user behavior)
from .user_feedback_learner import (
    UserFeedbackLearner,
    get_user_learner,
    learn_command,
    learn_error,
    learn_chat,
)

# NEW: Auto-Discovery Engine (scans 32+ sources automatically)
from .auto_discovery import (
    AutoDiscoveryEngine,
    get_discovery_engine,
    run_auto_discovery,
    get_recent_discoveries,
    get_discovery_stats,
)

# NEW: Experiment Tracker (measure value of learning)
from .experiment_tracker import (
    ExperimentTracker,
    get_experiment_tracker,
    create_experiment,
    get_pending_experiments,
    complete_experiment,
    get_experiment_stats,
    generate_value_report,
)

__all__ = [
    # Memory
    "MemoryManager", "get_memory", "remember", "recall",
    "learn_pattern", "learn_lesson",

    # Knowledge Scanner
    "KnowledgeScanner", "KnowledgeSource", "get_scanner",
    "scan_knowledge", "get_pending_improvements",

    # Self-Debugger
    "SelfDebugger", "Severity", "IssueType", "get_debugger",
    "track_decision", "track_action", "track_error", "health_check",

    # Learning Loop
    "LearningLoop", "get_learning_loop", "start_learning", "learn", "status",

    # Advanced Learning
    "AdvancedLearningEngine", "get_learning_engine", "run_daily_learning",

    # User Feedback Learning
    "UserFeedbackManager", "get_feedback_manager",
    "record_thumbs_up", "record_thumbs_down", "record_correction",
    "record_approval", "record_denial", "should_auto_approve",
    "get_preference", "feedback_stats",

    # Judgment Engine
    "JudgmentEngine", "get_judgment_engine",
    "analyze_situation", "should_proceed", "record_decision", "requires_approval",

    # Audit Logger
    "AuditLogger", "get_audit_logger",
    "audit_log", "log_learning", "log_decision",
    "log_user_feedback", "log_approval", "get_audit_stats",

    # Web Search Learning
    "WebSearchLearner", "get_web_learner",
    "search_web", "learn_from_web", "get_trending", "run_auto_discovery",

    # Learning Prioritizer
    "LearningPrioritizer", "get_prioritizer",
    "add_learning_topic", "get_next_to_learn",
    "prioritize_learning", "get_search_queries",

    # Daily Intelligence
    "DailyIntelligence", "get_daily_intelligence",
    "morning_query", "evening_reflection", "add_insight", "get_today_summary",

    # Control Center
    "ControlCenter", "get_control_center",
    "request_approval", "approve_request", "deny_request",
    "get_pending_approvals", "set_control_level", "get_control_level",

    # Daily Report
    "DailyReportGenerator", "get_report_generator",
    "generate_daily_report", "save_daily_report", "get_recent_reports",

    # Human-Like Learning Integration
    "HumanLikeLearningSystem", "get_hls",
    "learn_from_user", "analyze_and_decide",
    "run_daily_cycle", "get_status", "full_report",

    # Autonomous Improver
    "AutonomousImprover", "get_autonomous_improver",
    "learn_from_input", "run_auto_improvement", "get_autonomous_stats",

    # Chat Learner
    "ChatLearner", "get_chat_learner",
    "learn_from_chat", "learn_correction", "get_chat_summary",

    # Nexus Brain (Unified)
    "NexusBrain", "get_nexus_brain",
    "process_user_message", "process_user_correction",
    "process_user_feedback", "run_improvement",
    "nexus_status", "get_all_learnings",

    # Nexus Integration
    "NexusIntegration", "get_nexus",
    "nexus_learn", "nexus_learn_response", "nexus_improve", "nexus_report",

    # API-Based Learning (NEW - reliable)
    "APIKnowledgeFetcher", "APISource", "get_api_fetcher", "fetch_knowledge_from_apis",

    # User Feedback Learner (NEW - learns from user)
    "UserFeedbackLearner", "get_user_learner",
    "learn_command", "learn_error", "learn_chat",

    # Auto-Discovery Engine (NEW - scans 32+ sources daily)
    "AutoDiscoveryEngine", "get_discovery_engine",
    "run_auto_discovery", "get_recent_discoveries", "get_discovery_stats",
]

__version__ = "2.0.2"
