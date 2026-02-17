#!/usr/bin/env python3
"""
Autopilot Loop - Auto-Execute Next Actions
Chạy ngầm và tự động execute các next-actions theo policy/guardrail.

Usage:
    python scripts/autopilot_loop.py --daemon       # Run continuously
    python scripts/autopilot_loop.py --once       # Run once
    python scripts/autopilot_loop.py --status    # Show status
    python scripts/autopilot_loop.py --dry-run   # Preview only
    python scripts/autopilot_loop.py --enable    # Enable and run
"""

import os
import sys
import time
import json
import signal
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Default NEXUS endpoint
DEFAULT_NEXUS_URL = "http://127.0.0.1:5001"


class AutopilotLoop:
    """Autopilot loop for auto-executing next-actions."""

    def __init__(self, nexus_url: str = DEFAULT_NEXUS_URL, interval: int = 30):
        self.nexus_url = nexus_url.rstrip("/")
        self.interval = interval
        self.running = False
        self.dry_run = True
        self.enabled = False
        self.stats = {
            "total_cycles": 0,
            "actions_executed": 0,
            "actions_blocked": 0,
            "errors": 0,
            "start_time": None,
            "last_cycle": None,
        }

    def get_autopilot_status(self) -> Dict[str, Any]:
        """Get current autopilot status from NEXUS."""
        import requests
        url = f"{self.nexus_url}/api/hub/autopilot/status"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def configure_autopilot(self, enabled: bool = None, dry_run: bool = None, interval: int = None) -> Dict[str, Any]:
        """Configure autopilot settings."""
        import requests
        url = f"{self.nexus_url}/api/hub/autopilot/config"
        payload = {}
        if enabled is not None:
            payload["enabled"] = enabled
        if dry_run is not None:
            payload["dry_run"] = dry_run
        if interval is not None:
            payload["interval"] = interval

        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def preview_actions(self) -> List[Dict[str, Any]]:
        """Preview which actions would be executed."""
        import requests
        url = f"{self.nexus_url}/api/hub/autopilot/preview"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("preview", [])
        except Exception as e:
            print(f"Error previewing actions: {e}")
            return []

    def run_cycle(self) -> Dict[str, Any]:
        """Run one autopilot cycle."""
        import requests
        url = f"{self.nexus_url}/api/hub/autopilot/run"
        try:
            resp = requests.post(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_next_actions(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Get next actions from NEXUS."""
        import requests
        url = f"{self.nexus_url}/api/hub/next-actions?limit={limit}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("actions", [])
        except Exception as e:
            print(f"Error getting next actions: {e}")
            return []

    def run_iteration(self):
        """Run one autopilot iteration."""
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Get status
        status = self.get_autopilot_status()
        if not status.get("success"):
            print(f"[{timestamp}] ❌ Failed to get autopilot status: {status.get('error')}")
            return

        enabled = status.get("enabled", False)
        dry_run = status.get("dry_run", True)

        if not enabled:
            print(f"[{timestamp}] 💤 Autopilot is disabled")
            return

        # Get next actions
        actions = self.get_next_actions(limit=5)
        if not actions:
            print(f"[{timestamp}] 💤 No pending actions")
            return

        # Preview first
        preview = self.preview_actions()
        executable = [p for p in preview if p.get("would_execute")]

        if not executable:
            print(f"[{timestamp}] 💤 No executable actions (policy blocked all)")
            return

        # Execute
        result = self.run_cycle()
        if result.get("success"):
            executed = result.get("executed", [])
            blocked = result.get("blocked", [])
            errors = result.get("errors", [])

            mode = "DRY-RUN" if dry_run else "LIVE"
            print(f"[{timestamp}] 🔄 [{mode}] Executed: {len(executed)}, Blocked: {len(blocked)}, Errors: {len(errors)}")

            for ex in executed[:3]:
                action = ex.get("action", {})
                print(f"   ✅ {action.get('action_type')}: {action.get('title', action.get('task_id'))}")

            for bl in blocked[:2]:
                print(f"   ⛔ {bl.get('action', {}).get('action_type')}: {bl.get('reason')}")

            self.stats["total_cycles"] += 1
            self.stats["actions_executed"] += len(executed)
            self.stats["actions_blocked"] += len(blocked)
            self.stats["errors"] += len(errors)
        else:
            print(f"[{timestamp}] ❌ Cycle failed: {result.get('error')}")
            self.stats["errors"] += 1

        self.stats["last_cycle"] = datetime.now().isoformat()

    def run(self):
        """Run the autopilot loop."""
        self.running = True
        self.stats["start_time"] = datetime.now().isoformat()

        print("🤖 Autopilot Loop Started")
        print(f"   NEXUS: {self.nexus_url}")
        print(f"   Interval: {self.interval}s")
        print(f"   Press Ctrl+C to stop\n")

        # Check initial status
        status = self.get_autopilot_status()
        if status.get("success"):
            print(f"   Autopilot enabled: {status.get('enabled')}")
            print(f"   Dry-run mode: {status.get('dry_run')}")

        try:
            while self.running:
                self.run_iteration()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n\n⏹️  Stopped")

        self.running = False

    def stop(self):
        """Stop the loop."""
        self.running = False

    def get_status(self) -> Dict[str, Any]:
        """Get loop status."""
        nexus_status = self.get_autopilot_status()
        return {
            "nexus_url": self.nexus_url,
            "interval": self.interval,
            "running": self.running,
            "nexus_autopilot": nexus_status,
            "loop_stats": self.stats,
        }


def main():
    parser = argparse.ArgumentParser(description="Autopilot Loop")
    parser.add_argument("--url", "-u", type=str, default=DEFAULT_NEXUS_URL, help="NEXUS URL")
    parser.add_argument("--interval", "-i", type=int, default=30, help="Cycle interval in seconds")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run as daemon")
    parser.add_argument("--once", action="store_true", help="Run once")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run mode")
    parser.add_argument("--live", action="store_true", help="Enable live mode (not dry-run)")
    parser.add_argument("--enable", action="store_true", help="Enable autopilot and run")
    parser.add_argument("--disable", action="store_true", help="Disable autopilot")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--preview", action="store_true", help="Preview actions without executing")
    args = parser.parse_args()

    autopilot = AutopilotLoop(nexus_url=args.url, interval=args.interval)

    if args.status:
        status = autopilot.get_status()
        print(json.dumps(status, indent=2))
        return

    if args.preview:
        print("🔍 Previewing actions...\n")
        preview = autopilot.preview_actions()
        for i, p in enumerate(preview, 1):
            action = p.get("action", {})
            will_exec = p.get("would_execute")
            reason = p.get("reason")
            status_icon = "✅" if will_exec else "⛔"
            print(f"{i}. {status_icon} {action.get('action_type')}: {action.get('title')}")
            print(f"   Reason: {reason}\n")
        return

    # Configure autopilot
    if args.enable:
        print("🚀 Enabling autopilot...")
        result = autopilot.configure_autopilot(enabled=True, dry_run=args.live is False, interval=args.interval)
        print(f"   Result: {json.dumps(result, indent=2)}")
    elif args.disable:
        print("🛑 Disabling autopilot...")
        result = autopilot.configure_autopilot(enabled=False)
        print(f"   Result: {json.dumps(result, indent=2)}")
    elif args.dry_run:
        print("🔍 Enabling dry-run mode...")
        result = autopilot.configure_autopilot(enabled=True, dry_run=True)
        print(f"   Result: {json.dumps(result, indent=2)}")

    if args.daemon or not args.once or args.enable:
        # Handle signals for graceful shutdown
        def signal_handler(sig, frame):
            print("\n\n⏹️  Shutting down...")
            autopilot.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        autopilot.run()
    else:
        autopilot.run_iteration()


if __name__ == "__main__":
    main()
