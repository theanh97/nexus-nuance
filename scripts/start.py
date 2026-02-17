#!/usr/bin/env python3
"""
Auto Dev Loop - Main Entry Point
The Dream Team - Self-Learning AI Development System

Usage:
    python scripts/start.py              # Start the learning system
    python scripts/start.py --status     # Show current status
    python scripts/start.py --test       # Run test iterations
"""

import sys
import argparse
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory import (
    start_learning, status, health_check,
    scan_knowledge, get_pending_improvements
)
from src.core.runtime_sync import run_full_sync
from src.core.runtime_guard import ProcessSingleton


def main():
    parser = argparse.ArgumentParser(
        description="The Dream Team - Auto Dev Loop"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current system status"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Run test iterations (3)"
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Run knowledge scan only"
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Learning interval in seconds (default: 60)"
    )
    parser.add_argument(
        "--iterations", type=int, default=None,
        help="Maximum iterations (default: infinite)"
    )
    parser.add_argument(
        "--sync-models", action="store_true",
        help="Run provider/model routing sync and show snapshot"
    )
    parser.add_argument(
        "--write-env", action="store_true",
        help="When used with --sync-models, append missing synced values to .env"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  THE DREAM TEAM - AUTO DEV LOOP")
    print("  Self-Learning AI Development System")
    print("=" * 60)

    runtime_guard = None
    needs_runtime_guard = not args.status and not args.sync_models
    if needs_runtime_guard:
        lock_path = os.getenv("LEARNING_RUNTIME_LOCK_PATH", "data/state/learning_runtime.lock")
        runtime_guard = ProcessSingleton(name="learning_runtime", lock_path=lock_path)
        acquired, owner = runtime_guard.acquire(extra={"entrypoint": "scripts/start.py"})
        if not acquired:
            print("\n⚠️ LEARNING RUNTIME ALREADY ACTIVE")
            print("-" * 40)
            owner_pid = owner.get("pid", "unknown") if isinstance(owner, dict) else "unknown"
            owner_started = owner.get("started_at", "unknown") if isinstance(owner, dict) else "unknown"
            print(f"Lock file: {lock_path}")
            print(f"Owner PID: {owner_pid}")
            print(f"Started: {owner_started}")
            print("Refusing to start another conflicting learning runtime.")
            raise SystemExit(1)
        runtime_guard.start_heartbeat(interval_seconds=15)

    if args.sync_models:
        report = run_full_sync(write_env=args.write_env)
        providers = report.get("provider_status", {})
        print("\n🔄 RUNTIME SYNC")
        print("-" * 40)
        print(
            "Providers:",
            f"GLM={'on' if providers.get('glm_api') else 'off'}",
            f"Gemini={'on' if providers.get('gemini_api') else 'off'}",
            f"MiniMax={'on' if providers.get('minimax_api') else 'off'}",
        )
        print(
            "Subscription:",
            f"Codex={'on' if providers.get('codex_subscription') else 'off'}",
            f"Claude={'on' if providers.get('claude_subscription') else 'off'}",
            f"GeminiCLI={'on' if providers.get('gemini_subscription') else 'off'}",
        )
        print("Snapshot: data/state/runtime_sync.json")
        return

    # Keep routing/provider state synchronized for every runtime start.
    run_full_sync(write_env=False)

    if args.status:
        # Show status
        print("\n📊 SYSTEM STATUS")
        print("-" * 40)
        learning_lock = ProcessSingleton.inspect(
            os.getenv("LEARNING_RUNTIME_LOCK_PATH", "data/state/learning_runtime.lock")
        )
        autodev_lock = ProcessSingleton.inspect(
            os.getenv("AUTODEV_RUNTIME_LOCK_PATH", "data/state/autodev_runtime.lock")
        )
        scan_lock = ProcessSingleton.inspect(
            os.getenv("SCAN_OPERATION_LOCK_PATH", "data/state/knowledge_scan.lock")
        )
        improvement_lock = ProcessSingleton.inspect(
            os.getenv("IMPROVEMENT_OPERATION_LOCK_PATH", "data/state/improvement_apply.lock")
        )
        daily_cycle_lock = ProcessSingleton.inspect(
            os.getenv("DAILY_CYCLE_OPERATION_LOCK_PATH", "data/state/daily_self_learning.lock")
        )

        def _print_lock_line(label: str, snapshot: dict) -> None:
            owner = snapshot.get("owner", {}) if isinstance(snapshot.get("owner"), dict) else {}
            print(
                f"  • {label}:",
                f"locked={snapshot.get('locked')}",
                f"owner_pid={owner.get('pid', 'N/A')}",
                f"owner_alive={snapshot.get('owner_alive')}",
                f"owner_age_s={snapshot.get('owner_age_seconds')}",
                f"stale={snapshot.get('stale')}",
            )

        print("\n🔒 Runtime Locks:")
        _print_lock_line("Learning", learning_lock)
        _print_lock_line("AutoDev", autodev_lock)
        print("\n🧱 Operation Locks:")
        _print_lock_line("Knowledge Scan", scan_lock)
        _print_lock_line("Apply Improvements", improvement_lock)
        _print_lock_line("Daily Self-Learning", daily_cycle_lock)
        report = status()
        print(f"Iteration: {report.get('iteration', 'N/A')}")
        print(f"Running: {report.get('running', False)}")
        print(f"\n📈 Learning Stats:")
        stats = report.get('stats', {})
        for key, value in stats.items():
            print(f"  • {key}: {value}")
        print(f"\n💓 Health:")
        health = report.get('health', {})
        print(f"  • Score: {health.get('health_score', 'N/A')}/100")
        print(f"  • Status: {health.get('status', 'N/A')}")
        print(f"  • Open Issues: {health.get('open_issues', 0)}")

        advanced = report.get("advanced_learning", {})
        if advanced:
            print(f"\n🧠 Advanced Learning:")
            print(f"  • Knowledge Items: {advanced.get('knowledge_items', 0)}")
            print(f"  • Due For Review: {advanced.get('due_for_review', 0)}")
            print(f"  • Last Review: {advanced.get('last_review', 'N/A')}")

        notes = report.get("daily_notes", {})
        if notes.get("enabled", False):
            print(f"\n📝 R&D Notes (Today):")
            print(f"  • Total Notes: {notes.get('count_today', 0)}")
            print(f"  • File: {notes.get('path', 'N/A')}")
            recent = notes.get("recent", [])
            if recent:
                latest = recent[0]
                print(f"  • Latest: [{latest.get('severity', 'info')}] {latest.get('message', '')}")

        daily_cycle = report.get("daily_self_learning", {})
        if daily_cycle.get("enabled", False):
            print(f"\n🧭 Daily Self-Learning:")
            print(f"  • Runs today: {daily_cycle.get('count_today', 0)}")
            print(f"  • Last run: {daily_cycle.get('last_run', 'N/A')}")
            print(f"  • Next run in: {daily_cycle.get('next_run_in_seconds', 0)}s")
            print(f"  • Max ideas/experiments: {daily_cycle.get('max_ideas', 0)}/{daily_cycle.get('max_experiments', 0)}")
            print(f"  • File: {daily_cycle.get('path', 'N/A')}")
            recent_cycles = daily_cycle.get("recent", [])
            if recent_cycles:
                latest_cycle = recent_cycles[0].get("summary", {})
                print(
                    "  • Latest result: "
                    f"ideas={len(latest_cycle.get('improvement_ideas', []))}, "
                    f"experiments={len(latest_cycle.get('experiments', []))}, "
                    f"warnings={len(latest_cycle.get('warnings', []))}"
                )

        prompt_system = report.get("prompt_system", {})
        if prompt_system:
            print(f"\n🧩 Prompt System:")
            print(f"  • Enabled: {prompt_system.get('enabled', False)}")
            print(f"  • Directives: {prompt_system.get('directives_count', 0)}")
            print(f"  • File: {prompt_system.get('path', 'N/A')}")
            print(f"  • Tracklog: {prompt_system.get('tracklog_enabled', False)}")
            print(f"  • Events Today: {prompt_system.get('events_today', 0)}")
            print(f"  • Events File: {prompt_system.get('events_path', 'N/A')}")
            top_directives = prompt_system.get("top_directives", [])
            if top_directives:
                print(f"  • Top: {top_directives[0].get('title', 'N/A')}")

        self_check = report.get("self_check", {})
        if self_check.get("enabled", False):
            print(f"\n✅ Self-Check:")
            print(f"  • No-learning streak: {self_check.get('no_learning_streak', 0)}")
            print(f"  • No-improvement streak: {self_check.get('no_improvement_streak', 0)}")
            print(f"  • Warning threshold: {self_check.get('warn_streak_threshold', 'N/A')}")
            print(f"  • Learning window open: {self_check.get('learning_window_open', False)}")
            print(f"  • Improvement window open: {self_check.get('improvement_window_open', False)}")

        funnel = report.get("proposal_funnel", {})
        if funnel:
            print(f"\n🧪 Proposal Funnel:")
            print(f"  • Created: {funnel.get('created', 0)}")
            print(f"  • Pending Approval: {funnel.get('pending_approval', 0)}")
            print(f"  • Approved: {funnel.get('approved', 0)}")
            print(f"  • Executed: {funnel.get('executed', 0)}")
            print(f"  • Verified: {funnel.get('verified', 0)}")
            conversion = funnel.get("conversion", {}) if isinstance(funnel.get("conversion"), dict) else {}
            if conversion:
                print(
                    "  • Conversion (all-time) a/e/v: "
                    f"{conversion.get('approval_rate', 0)}/"
                    f"{conversion.get('execution_rate', 0)}/"
                    f"{conversion.get('verification_rate', 0)}"
                )
            w24 = funnel.get("window_24h", {}) if isinstance(funnel.get("window_24h"), dict) else {}
            if w24:
                print(
                    "  • 24h created→verified: "
                    f"{w24.get('created', 0)}→{w24.get('verified', 0)} "
                    f"({w24.get('verified_from_created_rate', 0)})"
                )

        outcome = report.get("outcome_quality", {})
        if outcome:
            print(f"\n📉 Outcome Quality:")
            print(f"  • Verified total: {outcome.get('verified_total', 0)}")
            print(f"  • Win rate: {outcome.get('win_rate', 0)}")
            print(f"  • Loss rate: {outcome.get('loss_rate', 0)}")
            print(f"  • Inconclusive rate: {outcome.get('inconclusive_rate', 0)}")
            print(f"  • Pending recheck runs: {outcome.get('pending_recheck_runs', 0)}")
            print(f"  • Holdout pending runs: {outcome.get('holdout_pending_runs', 0)}")
            print(f"  • Retry exhausted runs: {outcome.get('retry_exhausted_runs', 0)}")
            print(f"  • Avg confidence: {outcome.get('avg_confidence', 0)}")
            print(f"  • Avg latency delta (ms): {outcome.get('avg_latency_delta_ms', 0)}")
            print(f"  • Avg error delta: {outcome.get('avg_error_delta', 0)}")
            print(f"  • Avg execution cost (USD): {outcome.get('avg_execution_cost_usd', 0)}")
            t24 = outcome.get("trend_24h", {}) if isinstance(outcome.get("trend_24h"), dict) else {}
            print(
                f"  • Trend 24h (w/l/i): "
                f"{t24.get('win', 0)}/{t24.get('loss', 0)}/{t24.get('inconclusive', 0)}"
            )
        event_quality = report.get("learning_event_quality", {})
        if event_quality:
            print(f"\n🧼 Learning Event Quality:")
            print(f"  • Total events: {event_quality.get('total_events', 0)}")
            print(
                "  • Production/non-production: "
                f"{event_quality.get('production_events', 0)}/"
                f"{event_quality.get('non_production_events', 0)}"
            )
            print(f"  • Production ratio: {event_quality.get('production_ratio', 0)}")
            trend = event_quality.get("trend_24h", {}) if isinstance(event_quality.get("trend_24h"), dict) else {}
            print(
                "  • 24h stream (prod/non-prod): "
                f"{trend.get('production', 0)}/{trend.get('non_production', 0)}"
            )

        policy = report.get("policy_state", {})
        if policy:
            selected = policy.get("selected", {}) if isinstance(policy.get("selected"), dict) else {}
            print(f"\n🎛️ Policy State:")
            print(f"  • Approve threshold arm: {selected.get('approve_threshold', 'N/A')}")
            print(f"  • Scan min-score arm: {selected.get('scan_min_score', 'N/A')}")
            print(f"  • Focus policy arm: {selected.get('focus_policy', 'N/A')}")

        guardrail = report.get("execution_guardrail", {})
        if guardrail:
            print(f"\n🛡️ Execution Guardrail:")
            print(f"  • Canary enabled: {guardrail.get('canary_enabled', False)}")
            print(f"  • Real-apply enabled: {guardrail.get('executor_real_apply_enabled', False)}")
            print(
                f"  • Normal runs (1h): "
                f"{guardrail.get('normal_mode_runs_last_hour', 0)}/{guardrail.get('normal_mode_max_per_hour', 0)}"
            )
            print(f"  • Cooldown remaining (s): {guardrail.get('normal_mode_cooldown_remaining_sec', 0)}")
            print(f"  • Last reason: {guardrail.get('normal_mode_last_reason', 'N/A')}")

        cafe = report.get("cafe", {})
        if cafe:
            print(f"\n🧮 CAFE Calibration:")
            print(f"  • Enabled: {cafe.get('enabled', False)}")
            print(f"  • Last calibration: {cafe.get('last_calibration', 'N/A')}")
            print(f"  • Model bias count: {cafe.get('model_bias_count', 0)}")

        assessment = report.get("self_assessment", {})
        if assessment:
            print(f"\n🧭 Self-Assessment:")
            print(f"  • Grade: {assessment.get('grade', 'unknown')}")
            print(f"  • Score: {assessment.get('score', 0)}")
            issues = assessment.get("issues", [])
            if issues:
                print(f"  • Issues: {', '.join([str(x) for x in issues])}")

        windows = report.get("windows", {})
        if windows:
            print(f"\n🪟 Opportunity Windows:")
            print(f"  • Learning opportunities: {windows.get('learning_opportunities', 0)}")
            print(f"  • Improvement opportunities: {windows.get('improvement_opportunities', 0)}")
            print(f"  • Windowed self-check: {windows.get('windowed_self_check_enabled', False)}")
            print(f"  • Synthetic opportunities: {windows.get('synthetic_opportunities_enabled', False)}")
            print(f"  • No-opportunity iterations: {windows.get('no_opportunity_iterations', 0)}")

        focus = report.get("focus", {})
        critical = focus.get("critical_problems", [])
        lessons = focus.get("lessons_learned_today", [])
        improvements = focus.get("improvements_today", [])
        actions = focus.get("next_actions", [])
        solution_options = focus.get("solution_options", [])
        recommended_solution = focus.get("recommended_solution", {})
        strategy = report.get("strategy", {})
        rotation = strategy.get("latest_portfolio_rotation", {}) if isinstance(strategy, dict) else {}

        if critical:
            print(f"\n🎯 Top Problems:")
            for item in critical[:3]:
                print(f"  • [{item.get('severity', 'unknown')}] {item.get('title', '')}")
        if lessons:
            print(f"\n📘 Learned Today:")
            for lesson in lessons[:3]:
                print(f"  • {lesson}")
        if improvements:
            print(f"\n⚙️ Improved Today:")
            for imp in improvements[:3]:
                print(f"  • {imp}")
        if actions:
            print(f"\n➡️ Next Actions:")
            for action in actions[:3]:
                print(f"  • {action}")
        if recommended_solution:
            print(f"\n✅ Recommended Solution:")
            print(
                f"  • {recommended_solution.get('title', 'N/A')} "
                f"({recommended_solution.get('expected_impact', 'N/A')})"
            )
            print(f"  • Why: {recommended_solution.get('why', 'N/A')}")
        if solution_options:
            print(f"\n🧩 Solution Options:")
            for option in solution_options[:3]:
                print(
                    f"  • {option.get('title', 'N/A')} "
                    f"[focus={option.get('focus_area', 'N/A')}, impact={option.get('expected_impact', 'N/A')}]"
                )
        if strategy:
            print(f"\n🗺️ Strategy:")
            print(f"  • Current focus area: {strategy.get('current_focus_area', 'N/A')}")
            if rotation:
                print(f"  • Portfolio pivot: {rotation.get('recommended_focus_area', 'N/A')}")
                print(f"  • Reason: {rotation.get('reason', 'N/A')}")
        return

    if args.scan:
        # Run scan only
        print("\n🔍 Running Knowledge Scan...")
        print("-" * 40)
        results = scan_knowledge(min_score=6.0)
        print(f"Total discovered: {results['total_discovered']}")
        if "deduplicated_count" in results:
            print(f"After dedup: {results['deduplicated_count']}")
        print(f"Filtered (6+): {results['filtered_count']}")
        print(f"Proposals: {results['proposals_generated']}")
        if "sources_scanned" in results:
            print(
                f"Sources scanned/skipped/errors: "
                f"{results.get('sources_scanned', 0)}/"
                f"{results.get('sources_skipped', 0)}/"
                f"{results.get('source_errors', 0)}"
            )

        if results['top_items']:
            print(f"\n🔥 Top Items:")
            for item in results['top_items'][:5]:
                print(f"  [{item['score']:.1f}] {item['title']}")

        # Show pending improvements
        pending = get_pending_improvements()
        if pending:
            print(f"\n💡 Pending Improvements: {len(pending)}")
            for p in pending[:3]:
                print(f"  • {p['title']}")
        return

    if args.test:
        # Run test iterations
        print("\n🧪 Running 3 Test Iterations...")
        print("-" * 40)
        start_learning(interval=1, max_iterations=3)
        return

    # Start continuous learning
    print(f"\n🚀 Starting Continuous Learning")
    print(f"   Interval: {args.interval}s")
    print(f"   Max iterations: {args.iterations or 'infinite'}")
    print("-" * 40)
    print("\nPress Ctrl+C to stop\n")

    try:
        start_learning(interval=args.interval, max_iterations=args.iterations)
    except KeyboardInterrupt:
        print("\n\n⏹️ Stopped by user")
        print("=" * 60)


if __name__ == "__main__":
    main()
