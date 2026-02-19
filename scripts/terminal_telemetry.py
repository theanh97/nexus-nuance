#!/usr/bin/env python3
"""
Terminal Telemetry Bridge - Sync Terminal Sessions to NEXUS
Chạy ngầm và liên tục sync terminal sessions lên Nexus.

Usage:
    python scripts/terminal_telemetry.py --daemon       # Run continuously
    python scripts/terminal_telemetry.py --once       # Run once
    python scripts/terminal_telemetry.py --status    # Show status
    python scripts/terminal_telemetry.py --source vscode  # Specify source
"""

import os
import sys
import time
import json
import signal
import socket
import subprocess
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from threading import Thread

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Default NEXUS endpoint
DEFAULT_NEXUS_URL = "http://127.0.0.1:5001"


class TerminalTelemetryBridge:
    """Bridge terminal telemetry to NEXUS."""

    def __init__(self, nexus_url: str = DEFAULT_NEXUS_URL, source: str = "local", interval: int = 3):
        self.nexus_url = nexus_url.rstrip("/")
        self.source = source
        self.interval = interval
        self.running = False
        self.session_id_prefix = f"{source}-"
        self.stats = {
            "total_syncs": 0,
            "successful_syncs": 0,
            "failed_syncs": 0,
            "start_time": None,
            "last_sync": None,
            "terminals_tracked": 0,
        }

    def get_local_terminals(self) -> List[Dict[str, Any]]:
        """
        Get terminals from local system.
        Override this method for different sources (VSCode, iTerm, etc.)
        """
        terminals = []

        # Try to get terminals from running processes
        try:
            # macOS: Get Terminal.app sessions
            if sys.platform == "darwin":
                terminals.extend(self._get_macos_terminals())
        except Exception:
            pass

        return terminals

    def _get_macos_terminals(self) -> List[Dict[str, Any]]:
        """Get terminals from macOS Terminal.app or iTerm2."""
        terminals = []

        try:
            # Try Terminal.app
            result = subprocess.run(
                ["osascript", "-e", '''
                    tell application "System Events"
                        set termList to {}
                        if exists (processes where name is "Terminal") then
                            tell application "Terminal"
                                set winList to windows
                                repeat with win in winList
                                    set tabCount to 0
                                    try
                                        set tabCount to number of tabs in win
                                    end try
                                    if tabCount > 0 then
                                        repeat with i from 1 to tabCount
                                            set tabName to ""
                                            try
                                                set tabName to name of tab i of win
                                            end try
                                            set end of termList to {title:tabName, running:true}
                                        end repeat
                                    end if
                                end repeat
                            end tell
                        end if
                        return termList
                    end tell
                '''],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0 and result.stdout.strip():
                try:
                    tabs = json.loads(result.stdout.strip())
                    for i, tab in enumerate(tabs):
                        terminals.append({
                            "session_id": f"{self.session_id_prefix}term-{i+1}",
                            "owner_id": f"{self.source}:local",
                            "title": tab.get("title", f"Terminal {i+1}"),
                            "cwd": os.getcwd(),
                            "command": "",
                            "status": "running" if tab.get("running") else "idle",
                            "busy": tab.get("running", False),
                        })
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

        # If no Terminal.app, check for basic shell sessions
        if not terminals:
            terminals.append({
                "session_id": f"{self.session_id_prefix}shell-1",
                "owner_id": f"{self.source}:local",
                "title": "Shell",
                "cwd": os.getcwd(),
                "command": "",
                "status": "running",
                "busy": True,
            })

        return terminals

    def get_vscode_terminals(self) -> List[Dict[str, Any]]:
        """Get terminals from VSCode (requires VSCode extension or manual config)."""
        # Check for VSCode terminal socket/file
        vscode_terminal_file = Path.home() / ".vscode" / "extensions" / "terminal-telemetry" / "sockets.json"

        if vscode_terminal_file.exists():
            try:
                with open(vscode_terminal_file, encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("terminals", [])
            except Exception:
                pass

        return []

    def sync_terminals(self, terminals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sync terminals to NEXUS."""
        import requests

        url = f"{self.nexus_url}/api/bridge/sync/terminals"
        payload = {
            "source": self.source,
            "terminals": terminals,
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()

            self.stats["successful_syncs"] += 1
            self.stats["terminals_tracked"] = len(terminals)
            self.stats["last_sync"] = datetime.now().isoformat()

            return {
                "success": True,
                "synced": len(terminals),
                "data": data,
            }
        except Exception as e:
            self.stats["failed_syncs"] += 1
            return {
                "success": False,
                "error": str(e),
            }

    def run_iteration(self):
        """Run one sync iteration."""
        terminals = self.get_local_terminals()

        if terminals:
            result = self.sync_terminals(terminals)
            self.stats["total_syncs"] += 1

            if result.get("success"):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Synced {len(terminals)} terminals")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Sync failed: {result.get('error')}")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 💤 No terminals found")

    def run(self):
        """Run the telemetry bridge loop."""
        self.running = True
        self.stats["start_time"] = datetime.now().isoformat()

        print("🖥️  Terminal Telemetry Bridge Started")
        print(f"   Source: {self.source}")
        print(f"   NEXUS: {self.nexus_url}")
        print(f"   Interval: {self.interval}s")
        print(f"   Press Ctrl+C to stop\n")

        try:
            while self.running:
                self.run_iteration()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n\n⏹️  Stopped")

        self.running = False

    def stop(self):
        """Stop the bridge."""
        self.running = False

    def get_status(self) -> Dict[str, Any]:
        """Get bridge status."""
        return {
            "source": self.source,
            "nexus_url": self.nexus_url,
            "interval": self.interval,
            "running": self.running,
            "stats": self.stats,
        }


def main():
    parser = argparse.ArgumentParser(description="Terminal Telemetry Bridge")
    parser.add_argument("--url", "-u", type=str, default=DEFAULT_NEXUS_URL, help="NEXUS URL")
    parser.add_argument("--source", "-s", type=str, default="local", help="Source name (e.g., vscode, iterm, local)")
    parser.add_argument("--interval", "-i", type=int, default=3, help="Sync interval in seconds")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run as daemon")
    parser.add_argument("--once", action="store_true", help="Run once")
    parser.add_argument("--status", action="store_true", help="Show status")
    args = parser.parse_args()

    bridge = TerminalTelemetryBridge(
        nexus_url=args.url,
        source=args.source,
        interval=args.interval,
    )

    if args.status:
        status = bridge.get_status()
        print(json.dumps(status, indent=2))
        return

    if args.daemon or not args.once:
        # Handle signals for graceful shutdown
        def signal_handler(sig, frame):
            print("\n\n⏹️  Shutting down...")
            bridge.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        bridge.run()
    else:
        bridge.run_iteration()


if __name__ == "__main__":
    main()
