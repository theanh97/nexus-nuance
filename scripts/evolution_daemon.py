#!/usr/bin/env python3
"""
Evolution Engine Daemon
Chạy ngầm 24/7, tự học từ mọi interaction

Usage:
    python scripts/evolution_daemon.py start     # Start daemon
    python scripts/evolution_daemon.py stop      # Stop daemon
    python scripts/evolution_daemon.py status    # Check status
    python scripts/evolution_daemon.py stats     # Show stats
"""

import sys
import os
import time
import signal
import json
from pathlib import Path
from datetime import datetime

# Add project to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evolution.evolution_engine import get_engine, start_evolution, get_evolution_stats

PID_FILE = PROJECT_ROOT / "data" / "evolution" / "daemon.pid"
LOG_FILE = PROJECT_ROOT / "data" / "evolution" / "daemon.log"


def log(message: str):
    """Log message"""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")


def write_pid():
    """Write PID file"""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid():
    """Remove PID file"""
    if PID_FILE.exists():
        PID_FILE.unlink()


def is_running() -> bool:
    """Check if daemon is running"""
    if not PID_FILE.exists():
        return False

    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())

        # Check if process exists
        os.kill(pid, 0)
        return True
    except:
        remove_pid()
        return False


def run_daemon():
    """Run daemon"""
    log("=" * 50)
    log("EVOLUTION ENGINE DAEMON STARTED")
    log("=" * 50)

    write_pid()

    engine = start_evolution()

    # Signal handler
    def shutdown(signum, frame):
        log("Shutdown signal received")
        engine.stop()
        remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Main loop
    try:
        while True:
            time.sleep(60)  # Check every minute
            stats = get_evolution_stats()
            log(f"Heartbeat - Events: {stats['total_events']}, Patterns: {stats['total_patterns']}, Skills: {stats['total_skills']}")
    except KeyboardInterrupt:
        shutdown(None, None)


def start_daemon():
    """Start daemon"""
    if is_running():
        print("Daemon already running!")
        return

    # Fork to background
    pid = os.fork()

    if pid > 0:
        # Parent
        print(f"Daemon started (PID: {pid})")
        return

    # Child - become daemon
    os.setsid()
    os.chdir('/')

    # Redirect std
    sys.stdin = open('/dev/null', 'r')
    sys.stdout = open(LOG_FILE, 'a')
    sys.stderr = sys.stdout

    run_daemon()


def stop_daemon():
    """Stop daemon"""
    if not is_running():
        print("Daemon not running!")
        return

    with open(PID_FILE, 'r') as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Daemon stopped (PID: {pid})")
        remove_pid()
    except Exception as e:
        print(f"Error stopping daemon: {e}")


def show_status():
    """Show daemon status"""
    if is_running():
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        print(f"Daemon RUNNING (PID: {pid})")
    else:
        print("Daemon STOPPED")


def show_stats():
    """Show evolution stats"""
    stats = get_evolution_stats()
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "start":
        start_daemon()
    elif command == "stop":
        stop_daemon()
    elif command == "status":
        show_status()
    elif command == "stats":
        show_stats()
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
