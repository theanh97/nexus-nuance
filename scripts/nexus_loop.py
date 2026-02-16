#!/usr/bin/env python3
"""
NEXUS Auto-Loop - Self-Learning Daemon
Chạy ngầm và liên tục học hỏi, cải thiện hệ thống.

Usage:
    python scripts/nexus_loop.py              # Run once
    python scripts/nexus_loop.py --daemon       # Run continuously
    python scripts/nexus_loop.py --status       # Show status
"""

import os
import sys
import time
import json
import signal
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.memory import (
    # Chat learning
    ChatLearner, get_chat_learner,
    # Autonomous improvement
    get_autonomous_improver, run_auto_improvement,
    # Human-like learning
    get_hls, get_status as get_hls_status,
    # Audit
    get_audit_logger,
)


class NexusLoop:
    """NEXUS Auto-Learning Loop - chạy ngầm liên tục."""

    def __init__(self):
        self.running = False
        self.iteration = 0
        self.interval = 60  # 60 seconds default
        self.chat_learner = get_chat_learner()
        self.improver = get_autonomous_improver()
        self.hls = get_hls()
        self.audit = get_audit_logger()

        # Stats
        self.stats = {
            "total_iterations": 0,
            "learnings_processed": 0,
            "improvements_generated": 0,
            "errors": 0,
            "start_time": None,
        }

    def run_iteration(self):
        """Chạy một vòng learning."""
        self.iteration += 1
        self.stats["total_iterations"] += 1

        timestamp = datetime.now().strftime("%H:%M:%S")

        # Get chat learnings
        chat_summary = self.chat_learner.get_session_summary()
        new_learnings = chat_summary.get("learned_items", 0)
        improvement = {}
        improvements = 0

        # If there are new learnings, run improvement
        if new_learnings > self.stats.get("last_learnings", 0):
            # Run auto improvement
            improvement = run_auto_improvement()
            improvements = int((improvement or {}).get("improvements_generated", 0) or 0)
            self.stats["improvements_generated"] += improvements

            # Log
            self.audit.log_learning(
                learning_type="auto_improvement",
                content=f"Iteration {self.iteration}: {new_learnings} learnings, {improvements} improvements",
                source="nexus_loop"
            )

            status_line = f"[{timestamp}] 🔄 Iteration {self.iteration}: Learned {new_learnings} items, generated {improvements} improvements"
        else:
            status_line = f"[{timestamp}] 💤 Iteration {self.iteration}: No new learnings"

        self.stats["last_learnings"] = new_learnings

        return {
            "iteration": self.iteration,
            "learnings": new_learnings,
            "improvements": improvements,
            "timestamp": timestamp,
        }

    def run(self, max_iterations=None):
        """Chạy loop liên tục."""
        self.running = True
        self.stats["start_time"] = datetime.now().isoformat()

        print("🧠 NEXUS Auto-Loop Started")
        print(f"   Interval: {self.interval}s")
        print(f"   Press Ctrl+C to stop\n")

        try:
            while self.running:
                # Run iteration
                result = self.run_iteration()

                # Print status
                if result["learnings"] > 0 or result["improvements"] > 0:
                    print(f"[{result['timestamp']}] ✅ Iter {result['iteration']}: {result['learnings']} learnings, {result['improvements']} improvements")
                else:
                    print(f"[{result['timestamp']}] 💤 Iter {result['iteration']}: waiting...")

                # Check max
                if max_iterations and self.iteration >= max_iterations:
                    break

                # Sleep
                time.sleep(self.interval)

        except KeyboardInterrupt:
            print("\n\n⏹️  Stopped")

        self.running = False

    def stop(self):
        """Dừng loop."""
        self.running = False


def status():
    """Hiển thị status."""
    print("📊 NEXUS STATUS")
    print("=" * 50)

    # Chat learner
    chat = get_chat_learner()
    summary = chat.get_session_summary()
    print(f"\n🧠 Chat Learning:")
    print(f"   Messages: {summary.get('total_messages', 0)}")
    print(f"   Learned: {summary.get('learned_items', 0)}")
    print(f"   By type: {summary.get('by_type', {})}")

    # Autonomous
    improver = get_autonomous_improver()
    stats = improver.get_stats()
    print(f"\n🤖 Autonomous:")
    print(f"   Total learnings: {stats.get('total_learnings', 0)}")
    print(f"   High value: {stats.get('high_value', 0)}")
    print(f"   Improvements: {stats.get('total_improvements', 0)}")

    # Recent learnings
    print(f"\n📚 Recent Learnings:")
    for l in improver.get_all_learnings(limit=5):
        print(f"   [{l.get('type')}] {l.get('content', '')[:50]}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", "-d", action="store_true")
    parser.add_argument("--interval", "-i", type=int, default=30)
    parser.add_argument("--status", "-s", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.status:
        status()
        return

    loop = NexusLoop()
    loop.interval = args.interval

    if args.daemon or not args.once:
        loop.run()
    else:
        result = loop.run_iteration()
        print(f"✅ Iteration complete: {result}")


if __name__ == "__main__":
    main()
