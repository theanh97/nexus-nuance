"""
Screen Monitor - Continuous Screen Observation Engine
=====================================================

Monitors the computer screen 24/7:
- Periodic screenshot capture with change detection
- Terminal content reading (macOS via AppleScript)
- Image hashing for efficient change detection
- Observable event stream for supervisor consumption

Usage:
    from src.core.screen_monitor import ScreenMonitor

    monitor = ScreenMonitor(interval_sec=3)
    monitor.start()
    # ... monitor runs in background thread
    monitor.stop()
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.nexus_logger import get_logger

logger = get_logger(__name__)

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class ScreenState:
    """Snapshot of the current screen state."""
    timestamp: str
    screenshot_path: Optional[str] = None
    image_hash: Optional[str] = None
    terminal_content: str = ""
    terminal_hash: str = ""
    active_app: str = ""
    changed: bool = False
    change_type: str = ""  # "screen", "terminal", "app_switch", "none"


@dataclass
class ScreenEvent:
    """Event emitted when screen state changes."""
    event_type: str   # screen_changed, terminal_changed, app_switched, error
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)
    screen_state: Optional[ScreenState] = None


# ── Screen Monitor ────────────────────────────────────────────────

class ScreenMonitor:
    """
    Continuous screen observation engine.

    Runs in a background thread, periodically captures screen state,
    detects changes, and emits events to registered callbacks.
    """

    def __init__(
        self,
        interval_sec: float = 3.0,
        screenshot_dir: str = "screenshots/monitor",
        max_screenshots: int = 100,
        capture_screenshots: bool = True,
        read_terminal: bool = True,
        detect_active_app: bool = True,
    ):
        self.interval_sec = max(0.5, interval_sec)
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.max_screenshots = max_screenshots
        self.capture_screenshots = capture_screenshots
        self.read_terminal = read_terminal
        self.detect_active_app = detect_active_app

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_state: Optional[ScreenState] = None
        self._last_image_hash: str = ""
        self._last_terminal_hash: str = ""
        self._last_active_app: str = ""

        # Event callbacks
        self._callbacks: List[Callable[[ScreenEvent], None]] = []

        # Metrics
        self.total_captures = 0
        self.total_changes = 0
        self.screen_changes = 0
        self.terminal_changes = 0
        self.app_switches = 0
        self.errors = 0
        self._started_at: Optional[str] = None

        # History (ring buffer of recent states)
        self._history: List[ScreenState] = []
        self._history_max = 50

    # ── Callback Registration ─────────────────────────────────────

    def on_change(self, callback: Callable[[ScreenEvent], None]) -> None:
        """Register a callback for screen change events."""
        self._callbacks.append(callback)

    def _emit(self, event: ScreenEvent) -> None:
        """Emit an event to all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"ScreenMonitor callback error: {e}")

    # ── Screen Capture ────────────────────────────────────────────

    def _take_screenshot(self) -> Optional[str]:
        """Capture screen and return file path."""
        if not self.capture_screenshots:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.screenshot_dir / f"screen_{timestamp}.png"

        try:
            if PYAUTOGUI_AVAILABLE:
                screenshot = pyautogui.screenshot()
                screenshot.save(str(path))
                return str(path)
            elif os.uname().sysname == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["screencapture", "-x", str(path)],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    return str(path)
        except Exception as e:
            logger.debug(f"Screenshot failed: {e}")

        return None

    def _hash_image(self, path: str) -> str:
        """Generate perceptual hash for image change detection."""
        try:
            if PIL_AVAILABLE:
                img = Image.open(path)
                # Resize to small size for fast hashing
                img = img.resize((32, 32), Image.LANCZOS).convert("L")
                pixels = list(img.getdata())
                avg = sum(pixels) / len(pixels)
                bits = "".join("1" if p > avg else "0" for p in pixels)
                return hashlib.md5(bits.encode()).hexdigest()
            else:
                # Fallback: hash file bytes
                with open(path, "rb") as f:
                    return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    # ── Terminal Content ──────────────────────────────────────────

    def _read_terminal_content(self) -> str:
        """Read current Terminal.app content via AppleScript (macOS)."""
        if not self.read_terminal:
            return ""

        try:
            import subprocess
            script = '''
            tell application "Terminal"
                if (count of windows) > 0 then
                    tell front window
                        set tabContents to ""
                        repeat with t in tabs
                            set tabContents to tabContents & (contents of t) & "\\n---TAB---\\n"
                        end repeat
                        return tabContents
                    end tell
                end if
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()[-5000:]  # Last 5000 chars
        except Exception as e:
            logger.debug(f"Terminal read failed: {e}")

        return ""

    # ── Active Application ────────────────────────────────────────

    def _get_active_app(self) -> str:
        """Get the currently active application (macOS)."""
        if not self.detect_active_app:
            return ""

        try:
            import subprocess
            script = '''
            tell application "System Events"
                set frontApp to name of first application process whose frontmost is true
                return frontApp
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        return ""

    # ── Cleanup old screenshots ───────────────────────────────────

    def _cleanup_screenshots(self) -> None:
        """Remove old screenshots to prevent disk bloat."""
        try:
            files = sorted(self.screenshot_dir.glob("screen_*.png"))
            if len(files) > self.max_screenshots:
                for f in files[: len(files) - self.max_screenshots]:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Main Observation Cycle ────────────────────────────────────

    def _observe_once(self) -> ScreenState:
        """Perform one observation cycle."""
        now = datetime.now().isoformat()
        state = ScreenState(timestamp=now)

        # 1. Capture screenshot
        screenshot_path = self._take_screenshot()
        if screenshot_path:
            state.screenshot_path = screenshot_path
            state.image_hash = self._hash_image(screenshot_path)

        # 2. Read terminal
        terminal_content = self._read_terminal_content()
        state.terminal_content = terminal_content
        state.terminal_hash = hashlib.md5(terminal_content.encode()).hexdigest() if terminal_content else ""

        # 3. Get active app
        state.active_app = self._get_active_app()

        # 4. Detect changes
        changes = []

        if state.image_hash and state.image_hash != self._last_image_hash:
            changes.append("screen")
            self.screen_changes += 1

        if state.terminal_hash and state.terminal_hash != self._last_terminal_hash:
            changes.append("terminal")
            self.terminal_changes += 1

        if state.active_app and state.active_app != self._last_active_app:
            changes.append("app_switch")
            self.app_switches += 1

        state.changed = bool(changes)
        state.change_type = "+".join(changes) if changes else "none"

        # Update last state
        self._last_image_hash = state.image_hash or self._last_image_hash
        self._last_terminal_hash = state.terminal_hash or self._last_terminal_hash
        self._last_active_app = state.active_app or self._last_active_app

        self.total_captures += 1
        if state.changed:
            self.total_changes += 1

        return state

    # ── Background Loop ───────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """Main monitoring loop (runs in background thread)."""
        logger.info(f"ScreenMonitor started (interval={self.interval_sec}s)")

        while self._running:
            try:
                state = self._observe_once()

                with self._lock:
                    self._last_state = state
                    self._history.append(state)
                    if len(self._history) > self._history_max:
                        self._history = self._history[-self._history_max:]

                # Emit events for changes
                if state.changed:
                    for change in state.change_type.split("+"):
                        event_type = {
                            "screen": "screen_changed",
                            "terminal": "terminal_changed",
                            "app_switch": "app_switched",
                        }.get(change, "unknown")

                        self._emit(ScreenEvent(
                            event_type=event_type,
                            timestamp=state.timestamp,
                            details={
                                "change_type": change,
                                "active_app": state.active_app,
                                "has_terminal": bool(state.terminal_content),
                            },
                            screen_state=state,
                        ))

                # Cleanup old screenshots periodically
                if self.total_captures % 50 == 0:
                    self._cleanup_screenshots()

            except Exception as e:
                self.errors += 1
                logger.error(f"ScreenMonitor error: {e}")
                self._emit(ScreenEvent(
                    event_type="error",
                    timestamp=datetime.now().isoformat(),
                    details={"error": str(e)},
                ))

            time.sleep(self.interval_sec)

        logger.info("ScreenMonitor stopped")

    # ── Start / Stop ──────────────────────────────────────────────

    def start(self) -> None:
        """Start the screen monitor."""
        if self._running:
            return
        self._running = True
        self._started_at = datetime.now().isoformat()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="screen-monitor")
        self._thread.start()

    def stop(self) -> None:
        """Stop the screen monitor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._running

    # ── Queries ───────────────────────────────────────────────────

    def get_current_state(self) -> Optional[ScreenState]:
        """Get the most recent screen state."""
        with self._lock:
            return self._last_state

    def get_terminal_content(self) -> str:
        """Get the latest terminal content."""
        with self._lock:
            return self._last_state.terminal_content if self._last_state else ""

    def get_active_app(self) -> str:
        """Get the current active application."""
        with self._lock:
            return self._last_state.active_app if self._last_state else ""

    def get_recent_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent screen state history."""
        with self._lock:
            return [
                {
                    "timestamp": s.timestamp,
                    "changed": s.changed,
                    "change_type": s.change_type,
                    "active_app": s.active_app,
                    "has_terminal": bool(s.terminal_content),
                    "screenshot": s.screenshot_path,
                }
                for s in self._history[-limit:]
            ]

    def get_status(self) -> Dict[str, Any]:
        """Get monitor status and metrics."""
        return {
            "running": self._running,
            "started_at": self._started_at,
            "interval_sec": self.interval_sec,
            "total_captures": self.total_captures,
            "total_changes": self.total_changes,
            "screen_changes": self.screen_changes,
            "terminal_changes": self.terminal_changes,
            "app_switches": self.app_switches,
            "errors": self.errors,
            "capture_screenshots": self.capture_screenshots,
            "read_terminal": self.read_terminal,
            "detect_active_app": self.detect_active_app,
            "current_app": self.get_active_app(),
        }


# ── Singleton ─────────────────────────────────────────────────────

_instance: Optional[ScreenMonitor] = None
_instance_lock = threading.Lock()


def get_screen_monitor(**kwargs) -> ScreenMonitor:
    """Get singleton ScreenMonitor instance."""
    global _instance
    with _instance_lock:
        if _instance is None:
            if "interval_sec" not in kwargs:
                kwargs["interval_sec"] = float(os.getenv("SCREEN_MONITOR_INTERVAL_SEC", "3"))
            if "capture_screenshots" not in kwargs:
                kwargs["capture_screenshots"] = os.getenv("SCREEN_MONITOR_CAPTURE", "true").lower() == "true"
            _instance = ScreenMonitor(**kwargs)
        return _instance


def reset_instance() -> None:
    """Reset singleton (for testing)."""
    global _instance
    with _instance_lock:
        if _instance and _instance.running:
            _instance.stop()
        _instance = None
