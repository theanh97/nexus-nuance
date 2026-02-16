#!/usr/bin/env python3
"""
NEXUS Auto-Sync Script
Runs the full learning cycle automatically.

Usage:
    python scripts/auto_sync.py              # Run once
    python scripts/auto_sync.py --daemon     # Run continuously
    python scripts/auto_sync.py --interval 300  # Custom interval

Environment Variables:
    AUTO_SYNC_INTERVAL      # Seconds between cycles (default: 3600)
    AUTO_SYNC_MAX_ITEMS     # Max items per cycle (default: 10)
    ENABLE_WEB_DISCOVERY    # Enable web search (default: true)
    ENABLE_DAILY_REPORT     # Enable daily report (default: true)
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.memory import (
    get_hls,
    get_learning_loop,
    get_feedback_manager,
    get_judgment_engine,
    get_audit_logger,
    get_web_learner,
    get_prioritizer,
    get_daily_intelligence,
    get_control_center,
    get_report_generator,
    run_daily_cycle,
    full_report,
)


class AutoSync:
    """Automatic learning sync system."""

    def __init__(self):
        self.interval = int(os.getenv("AUTO_SYNC_INTERVAL", "3600"))
        self.max_items = int(os.getenv("AUTO_SYNC_MAX_ITEMS", "10"))
        self.enable_web = os.getenv("ENABLE_WEB_DISCOVERY", "true").lower() == "true"
        self.enable_report = os.getenv("ENABLE_DAILY_REPORT", "true").lower() == "true"

        self.hls = get_hls()
        self.running = False
        self.cycle_count = 0

        print(f"🌀 AutoSync initialized")
        print(f"   Interval: {self.interval}s")
        print(f"   Max items: {self.max_items}")
        print(f"   Web discovery: {self.enable_web}")
        print(f"   Daily report: {self.enable_report}")

    def run_cycle(self) -> dict:
        """Run a single sync cycle."""
        self.cycle_count += 1
        start_time = datetime.now()

        print(f"\n{'='*60}")
        print(f"🔄 Cycle {self.cycle_count} - {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        results = {
            "cycle": self.cycle_count,
            "start_time": start_time.isoformat(),
            "steps": {},
            "success": True,
            "errors": [],
        }

        try:
            # Step 1: User Feedback Learning
            print("\n📝 Step 1: Processing user feedback...")
            fm = get_feedback_manager()
            fb_summary = fm.get_feedback_summary()
            results["steps"]["feedback"] = fb_summary
            print(f"   ✅ Feedback items: {fb_summary.get('total_feedback', 0)}")

            # Step 2: Web Discovery
            if self.enable_web:
                print("\n🌐 Step 2: Running web discovery...")
                web = get_web_learner()
                discovery = web.run_auto_search()
                results["steps"]["discovery"] = discovery
                print(f"   ✅ Discoveries: {discovery.get('discoveries', 0)}")
            else:
                print("\n🌐 Step 2: Web discovery disabled (skipping)")
                results["steps"]["discovery"] = {"skipped": True}

            # Step 3: Learning Prioritization
            print("\n🎯 Step 3: Updating priorities...")
            p = get_prioritizer()
            priorities = p.prioritize(max_topics=self.max_items)
            results["steps"]["priorities"] = {
                "count": len(priorities),
                "top": [t.get("topic") for t in priorities[:3]]
            }
            print(f"   ✅ Topics: {len(priorities)} pending")

            # Step 4: Daily Intelligence
            print("\n☀️ Step 4: Daily intelligence...")
            di = get_daily_intelligence()
            di_stats = di.get_stats()
            results["steps"]["daily"] = di_stats

            # Run morning or evening based on time
            if di.should_run_morning():
                morning = di.morning_query()
                results["steps"]["morning"] = morning
                print(f"   ✅ Morning query: {len(morning.get('suggested_queries', []))} queries")
            elif di.should_run_evening():
                # Get today's learnings
                learnings = self._get_today_learnings()
                evening = di.evening_reflection(learnings=learnings)
                results["steps"]["evening"] = evening
                print(f"   ✅ Evening reflection: {len(evening.get('insights', []))} insights")
            else:
                print(f"   ⏭️  Skipping (not morning/evening)")

            # Step 5: Control Center
            print("\n🎛️ Step 5: Checking pending approvals...")
            cc = get_control_center()
            pending = cc.get_pending()
            results["steps"]["control"] = {
                "pending": len(pending),
                "items": [p.get("action") for p in pending[:5]]
            }
            print(f"   ✅ Pending: {len(pending)} approvals")

            # Step 6: Generate Report
            if self.enable_report:
                print("\n📊 Step 6: Generating daily report...")
                report_path = get_report_generator().save_report()
                results["steps"]["report"] = str(report_path)
                print(f"   ✅ Saved: {report_path}")
            else:
                print("\n📊 Step 6: Daily report disabled (skipping)")
                results["steps"]["report"] = {"skipped": True}

            # Step 7: System Status
            print("\n📈 Step 7: System status...")
            status = self.hls.get_status()
            results["steps"]["status"] = {
                "feedback": status.get("feedback", {}).get("total_feedback", 0),
                "audit": status.get("audit", {}).get("total_logs", 0),
                "health": "OK"
            }
            print(f"   ✅ Feedback: {status.get('feedback', {}).get('total_feedback', 0)}")
            print(f"   ✅ Audit: {status.get('audit', {}).get('total_logs', 0)}")

        except Exception as e:
            results["success"] = False
            results["errors"].append(str(e))
            print(f"\n❌ Error in cycle: {e}")

        # Summary
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        results["end_time"] = end_time.isoformat()
        results["duration_seconds"] = duration

        print(f"\n{'='*60}")
        print(f"✅ Cycle {self.cycle_count} complete in {duration:.1f}s")
        print(f"{'='*60}")

        return results

    def _get_today_learnings(self) -> list:
        """Get today's learning items."""
        learnings = []

        # From web discoveries
        try:
            web = get_web_learner()
            discoveries = web.get_discoveries(limit=20)
            learnings.extend([{"source": "web", "item": d} for d in discoveries])
        except:
            pass

        # From user feedback
        try:
            fm = get_feedback_manager()
            feedback = fm.get_recent_feedback(limit=20)
            learnings.extend([{"source": "feedback", "item": f} for f in feedback])
        except:
            pass

        return learnings

    def run(self, max_cycles: int = None):
        """Run continuous sync."""
        self.running = True

        print(f"\n🚀 Starting AutoSync")
        print(f"   Press Ctrl+C to stop\n")

        try:
            while self.running:
                # Run cycle
                self.run_cycle()

                # Check if should stop
                if max_cycles and self.cycle_count >= max_cycles:
                    print(f"\n✅ Reached max cycles ({max_cycles})")
                    break

                if self.running:
                    print(f"\n💤 Sleeping for {self.interval}s...")
                    time.sleep(self.interval)

        except KeyboardInterrupt:
            print("\n\n⏹️  Stopped by user")

        print(f"\n📊 Total cycles: {self.cycle_count}")

    def stop(self):
        """Stop the sync."""
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="NEXUS Auto-Sync")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run continuously")
    parser.add_argument("--interval", "-i", type=int, help="Interval in seconds")
    parser.add_argument("--max-cycles", "-m", type=int, help="Max cycles to run")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--status", action="store_true", help="Show status only")

    args = parser.parse_args()

    if args.interval:
        os.environ["AUTO_SYNC_INTERVAL"] = str(args.interval)

    if args.status:
        # Show status
        print("📊 NEXUS Status")
        print("=" * 40)

        hls = get_hls()
        status = hls.get_status()

        print(f"\n🧠 Learning:")
        print(f"   Feedback: {status.get('feedback', {}).get('total_feedback', 0)}")
        print(f"   Patterns: {status.get('feedback', {}).get('patterns_learned', 0)}")

        print(f"\n🎯 Priorities:")
        print(f"   Topics: {status.get('priorities', {}).get('pending', 0)}")

        print(f"\n📋 Audit:")
        print(f"   Logs: {status.get('audit', {}).get('total_logs', 0)}")

        print(f"\n🎛️ Control:")
        print(f"   Level: {status.get('control', {}).get('control_level', 'NOTIFY')}")
        print(f"   Pending: {status.get('control', {}).get('pending', 0)}")

        print(f"\n☀️ Daily:")
        print(f"   Queries: {status.get('daily', {}).get('total_morning_queries', 0)}")
        print(f"   Reflections: {status.get('daily', {}).get('total_evening_reflections', 0)}")

        # Show recent report
        print(f"\n📰 Recent Report:")
        print(full_report()[:500] + "...")

        return

    if args.once or not args.daemon:
        # Run once
        sync = AutoSync()
        result = sync.run_cycle()

        if result["success"]:
            print(f"\n✅ Cycle completed in {result['duration_seconds']:.1f}s")
        else:
            print(f"\n❌ Cycle failed: {result['errors']}")
            sys.exit(1)
    else:
        # Run as daemon
        sync = AutoSync()
        sync.run(max_cycles=args.max_cycles)


if __name__ == "__main__":
    main()
