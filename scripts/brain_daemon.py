#!/usr/bin/env python3
"""
NEXUS BRAIN Daemon
Chạy ngầm 24/7 - Tự học từ mọi nguồn

Usage:
    python3 scripts/brain_daemon.py start    # Start daemon
    python3 scripts/brain_daemon.py stop     # Stop daemon
    python3 scripts/brain_daemon.py status   # Check status
    python3 scripts/brain_daemon.py stats    # Show stats
    python3 scripts/brain_daemon.py learn    # Add learning
    python3 scripts/brain_daemon.py search   # Search knowledge
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

PID_FILE = PROJECT_ROOT / "data" / "brain" / "daemon.pid"
LOG_FILE = PROJECT_ROOT / "data" / "brain" / "daemon.log"


def log(message: str):
    """Log message"""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}"
    print(line)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + "\n")


def write_pid():
    """Write PID file atomically (write to .tmp then os.replace)"""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PID_FILE.with_suffix('.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()))
    os.replace(str(tmp_path), str(PID_FILE))


def remove_pid():
    """Remove PID file"""
    if PID_FILE.exists():
        PID_FILE.unlink()


def is_running() -> bool:
    """Check if daemon is running"""
    if not PID_FILE.exists():
        return False

    try:
        with open(PID_FILE, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())

        # Check if process exists
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, ProcessLookupError):
        remove_pid()
        return False


def run_daemon():
    """Run daemon in foreground"""
    from brain.nexus_brain import start_brain, brain_stats
    from brain.auto_evolution import get_discovery, get_discovery_stats

    log("=" * 60)
    log("NEXUS BRAIN DAEMON STARTED")
    log("=" * 60)
    log("")
    log("AUTONOMOUS CYCLES:")
    log("  - News scan: Every 5 minutes")
    log("  - GitHub scan: Every 15 minutes")
    log("  - Consolidate: Every 1 hour")
    log("  - Deep learning: Every 6 hours")
    log("  - Daily report: Every 24 hours")
    log("  - AUTO-EVOLUTION: Every 1 hour (discovers new sources)")
    log("")

    # Get discovery stats
    discovery_stats = get_discovery_stats()
    log(f"Categories: {discovery_stats['categories']}")
    log(f"Discovered Sources: {discovery_stats['total_discovered']}")

    write_pid()

    brain = start_brain()

    # Signal handler
    def shutdown(signum, frame):
        log("Shutdown signal received")
        brain.shutdown()
        remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Initialize self-reminder engine
    self_reminder = None
    try:
        from brain.self_reminder import get_self_reminder
        self_reminder = get_self_reminder()
        log(f"Self-Reminder Engine: {'ENABLED' if self_reminder.enabled else 'DISABLED'} "
            f"({len(self_reminder._sources)} sources)")
    except Exception as exc:
        log(f"Self-Reminder init skipped: {exc}")

    # Main loop - heartbeat
    heartbeat_sec = int(os.getenv("NEXUS_HEARTBEAT_INTERVAL", "60"))
    try:
        counter = 0
        while True:
            time.sleep(heartbeat_sec)
            counter += 1

            # Self-Reminder check every heartbeat cycle
            if self_reminder and self_reminder.enabled:
                try:
                    reminders = self_reminder.check_and_remind()
                    if reminders:
                        changed = [r for r in reminders if r.get("changed")]
                        log(f"Self-Reminder: {len(reminders)} source(s) refreshed"
                            + (f", {len(changed)} CHANGED" if changed else ""))
                except Exception as exc:
                    try:
                        log(f"Self-Reminder error (non-fatal): {exc}")
                    except Exception:
                        pass

            # Heartbeat every 5 cycles (wrapped so one failure can't crash daemon)
            if counter % 5 == 0:
                try:
                    stats = brain_stats()
                    knowledge = stats.get("memory", {}).get("total_knowledge", 0)
                    skills = len(stats.get("skills", {}))
                    msg = f"Heartbeat - Knowledge: {knowledge}, Skills: {skills}"

                    # Add budget info if available
                    try:
                        from core.model_router import ModelRouter
                        proj = ModelRouter().get_budget_projection()
                        msg += f", Budget: ${proj.get('total_spent', 0):.4f}/{proj.get('daily_budget', 0)} ({proj.get('status', '?')})"
                    except Exception:
                        pass

                    # Add self-reminder stats
                    if self_reminder:
                        sr_stats = self_reminder.get_stats()
                        msg += f", Reminders: {sr_stats.get('total_reminders', 0)}"

                    log(msg)
                except Exception as exc:
                    try:
                        log(f"Heartbeat error (non-fatal): {exc}")
                    except Exception:
                        pass

    except KeyboardInterrupt:
        shutdown(None, None)


def start_daemon():
    """Start daemon in background"""
    if is_running():
        print("Daemon already running!")
        return

    # Fork to background
    try:
        pid = os.fork()
    except OSError:
        # Windows - run in foreground
        print("Running in foreground (Windows)...")
        run_daemon()
        return

    if pid > 0:
        # Parent
        print(f"Daemon started (PID: {pid})")
        return

    # Child - become daemon
    os.setsid()
    os.chdir('/')

    # Redirect std
    sys.stdin = open('/dev/null', 'r', encoding='utf-8')
    sys.stdout = open(LOG_FILE, 'a', encoding='utf-8')
    sys.stderr = sys.stdout

    run_daemon()


def stop_daemon():
    """Stop daemon"""
    if not is_running():
        print("Daemon not running!")
        return

    with open(PID_FILE, 'r', encoding='utf-8') as f:
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
        with open(PID_FILE, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())
        print(f"Daemon RUNNING (PID: {pid})")
        print()
        show_stats()
    else:
        print("Daemon STOPPED")


def show_stats():
    """Show brain stats"""
    from brain.nexus_brain import brain_stats

    try:
        stats = brain_stats()
        print()
        print("=" * 50)
        print("NEXUS BRAIN STATISTICS")
        print("=" * 50)
        print()
        print(f"Running: {stats.get('running', False)}")
        print()
        print("MEMORY:")
        memory = stats.get('memory', {})
        print(f"  Total Knowledge: {memory.get('total_knowledge', 0)}")
        print(f"  Total Patterns: {memory.get('total_patterns', 0)}")
        print(f"  Total Events: {memory.get('total_events', 0)}")
        print()
        print("SKILLS:")
        skills = stats.get('skills', {})
        for name, data in skills.items():
            print(f"  {name}: Level {data.get('level', 1)}/10")
            print(f"    Executions: {data.get('executions', 0)}")
            print(f"    Success Rate: {data.get('success_rate', 0)*100:.1f}%")
            print(f"    Mastered: {data.get('mastered', False)}")
            print(f"    Can Delegate: {data.get('can_delegate', False)}")
        print()
        print("CYCLES:")
        cycles = stats.get('cycles', {})
        for name, last in cycles.items():
            print(f"  {name}: Last run at {last}")
        print()

    except Exception as e:
        print(f"Error getting stats: {e}")


def add_learning(args):
    """Add learning manually"""
    from brain.nexus_brain import learn

    if len(args) < 3:
        print("Usage: brain_daemon.py learn <source> <type> <title> [content]")
        return

    source = args[0]
    type_ = args[1]
    title = args[2]
    content = args[3] if len(args) > 3 else title

    learn(source, type_, title, content)
    print(f"Learned: {title}")


def search_knowledge(args):
    """Search knowledge base"""
    from brain.nexus_brain import get_brain

    if len(args) < 1:
        print("Usage: brain_daemon.py search <query>")
        return

    query = " ".join(args)
    results = get_brain().search(query)

    print(f"Found {len(results)} results for '{query}':")
    print()
    for r in results[:10]:
        print(f"  [{r['source']}] {r['title']}")
        print(f"    {r['content'][:100]}...")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()
    args = sys.argv[2:]

    if command == "start":
        start_daemon()
    elif command == "stop":
        stop_daemon()
    elif command == "status":
        show_status()
    elif command == "stats":
        show_stats()
    elif command == "learn":
        add_learning(args)
    elif command == "search":
        search_knowledge(args)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
