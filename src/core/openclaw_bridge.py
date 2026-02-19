"""
OpenClaw Bridge - Direct interface for Supervisor to use OpenClaw
================================================================

Provides a clean API for the SupervisorDaemon to leverage OpenClaw's
capabilities for computer control:

1. Browser control: navigate, click, type, screenshot, read DOM
2. Terminal monitoring: read terminal content, send commands
3. Dashboard flow: send commands to OpenClaw dashboard
4. Gateway health: check and restart OpenClaw gateway
5. CLI execution: run any OpenClaw CLI command

This bridge talks directly to the OpenClaw CLI and also to
the Monitor API when available, so the Supervisor can control
the computer through OpenClaw 24/7.

Usage:
    from src.core.openclaw_bridge import OpenClawBridge

    bridge = OpenClawBridge()
    result = bridge.browser_navigate("https://example.com")
    result = bridge.browser_screenshot()
    result = bridge.send_dashboard_command("run_cycle")
    content = bridge.read_terminal()
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.nexus_logger import get_logger

logger = get_logger(__name__)


def _has_openclaw_cli() -> bool:
    """Check if openclaw CLI is available."""
    return shutil.which("openclaw") is not None


def _run_cli(
    args: List[str],
    timeout_sec: int = 30,
    retries: int = 2,
) -> Dict[str, Any]:
    """Run an openclaw CLI command with retries."""
    cmd = ["openclaw"] + args
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()

            result = {
                "success": completed.returncode == 0,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "attempt": attempt,
            }

            # Try to parse JSON output
            try:
                if stdout.startswith("{") or stdout.startswith("["):
                    result["json"] = json.loads(stdout)
            except json.JSONDecodeError:
                pass

            if result["success"] or attempt >= retries:
                return result

            last_error = stderr or stdout

        except subprocess.TimeoutExpired:
            last_error = f"Timeout after {timeout_sec}s"
        except FileNotFoundError:
            return {"success": False, "error": "openclaw CLI not found"}
        except Exception as e:
            last_error = str(e)

        time.sleep(min(0.5 * attempt, 2.0))

    return {"success": False, "error": last_error or "Command failed"}


def _call_monitor_api(
    endpoint: str,
    method: str = "GET",
    data: Optional[Dict] = None,
    port: int = 5111,
    timeout_sec: int = 10,
) -> Dict[str, Any]:
    """Call the Monitor API for OpenClaw operations."""
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}{endpoint}"

    try:
        if method == "POST" and data:
            json_data = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=json_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else {"success": True}

    except urllib.error.URLError as e:
        return {"success": False, "error": f"Monitor API unavailable: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


class OpenClawBridge:
    """
    Bridge between SupervisorDaemon and OpenClaw.

    Uses OpenClaw CLI directly for browser/terminal control,
    and Monitor API for queue operations and health checks.
    """

    def __init__(self, monitor_port: int = 5111):
        self.monitor_port = int(os.getenv("MONITOR_PORT", str(monitor_port)))
        self.cli_available = _has_openclaw_cli()
        self._lock = threading.Lock()

        # Metrics
        self.total_commands = 0
        self.successful_commands = 0
        self.failed_commands = 0

        logger.info(f"OpenClawBridge initialized (CLI available: {self.cli_available})")

    # ── Browser Control ───────────────────────────────────────────

    def browser_navigate(self, url: str, timeout_sec: int = 30) -> Dict[str, Any]:
        """Navigate browser to URL."""
        return self._browser_cmd(["navigate", url], timeout_sec)

    def browser_click(self, selector: str, timeout_sec: int = 10) -> Dict[str, Any]:
        """Click an element by CSS selector."""
        return self._browser_cmd(["click", selector], timeout_sec)

    def browser_type(self, selector: str, text: str, timeout_sec: int = 10) -> Dict[str, Any]:
        """Type text into an element."""
        return self._browser_cmd(["type", selector, text], timeout_sec)

    def browser_screenshot(self, output_path: str = "", timeout_sec: int = 15) -> Dict[str, Any]:
        """Take a browser screenshot."""
        args = ["screenshot"]
        if output_path:
            args.extend(["--output", output_path])
        return self._browser_cmd(args, timeout_sec)

    def browser_get_dom(self, timeout_sec: int = 15) -> Dict[str, Any]:
        """Get the current page DOM/content."""
        return self._browser_cmd(["dom"], timeout_sec)

    def browser_evaluate(self, js_code: str, timeout_sec: int = 10) -> Dict[str, Any]:
        """Evaluate JavaScript in the browser."""
        return self._browser_cmd(["evaluate", js_code], timeout_sec)

    def _browser_cmd(self, args: List[str], timeout_sec: int = 30) -> Dict[str, Any]:
        """Execute a browser command via OpenClaw CLI or Monitor API."""
        self.total_commands += 1

        # Try via Monitor API first (more reliable when monitor is running)
        api_result = _call_monitor_api(
            "/api/openclaw/browser",
            method="POST",
            data={"action": args[0] if args else "", "args": args[1:]},
            port=self.monitor_port,
        )
        if api_result.get("success"):
            self.successful_commands += 1
            return api_result

        # Fallback to direct CLI
        if self.cli_available:
            result = _run_cli(["browser", "--json"] + args, timeout_sec=timeout_sec)
            if result.get("success"):
                self.successful_commands += 1
            else:
                self.failed_commands += 1
            return result

        self.failed_commands += 1
        return {"success": False, "error": "Neither Monitor API nor OpenClaw CLI available"}

    # ── Dashboard Flow Control ────────────────────────────────────

    def send_dashboard_command(self, command: str, approve: bool = True) -> Dict[str, Any]:
        """Send a command to the OpenClaw dashboard chat."""
        self.total_commands += 1

        result = _call_monitor_api(
            "/api/openclaw/flow/dashboard",
            method="POST",
            data={"chat_text": command, "approve": approve},
            port=self.monitor_port,
        )

        if result.get("success"):
            self.successful_commands += 1
        else:
            self.failed_commands += 1

        return result

    # ── Command Queue ─────────────────────────────────────────────

    def enqueue_command(self, command_type: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """Enqueue a command for OpenClaw execution."""
        self.total_commands += 1

        result = _call_monitor_api(
            "/api/openclaw/commands",
            method="POST",
            data={
                "command_type": command_type,
                **(payload or {}),
            },
            port=self.monitor_port,
        )

        if result.get("success"):
            self.successful_commands += 1
        else:
            self.failed_commands += 1

        return result

    # ── Terminal Operations ───────────────────────────────────────

    def read_terminal(self) -> str:
        """Read current terminal content via AppleScript (macOS)."""
        try:
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
                return result.stdout.strip()[-5000:]
        except Exception as e:
            logger.debug(f"Terminal read failed: {e}")
        return ""

    def send_to_terminal(self, text: str) -> Dict[str, Any]:
        """Type text into the active Terminal tab."""
        try:
            # Escape single quotes for AppleScript
            safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
            script = f'''
            tell application "Terminal"
                activate
                delay 0.3
                tell application "System Events"
                    keystroke "{safe_text}"
                    keystroke return
                end tell
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            return {
                "success": result.returncode == 0,
                "stderr": result.stderr.strip(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Gateway Health ────────────────────────────────────────────

    def check_health(self) -> Dict[str, Any]:
        """Check OpenClaw gateway health."""
        result = _call_monitor_api(
            "/api/openclaw/dashboard",
            port=self.monitor_port,
        )
        if result.get("success"):
            return result

        # Fallback: check CLI
        if self.cli_available:
            return _run_cli(["status"], timeout_sec=5)

        return {"success": False, "error": "Cannot check health"}

    def get_metrics(self) -> Dict[str, Any]:
        """Get OpenClaw metrics."""
        return _call_monitor_api(
            "/api/openclaw/metrics",
            port=self.monitor_port,
        )

    def restart_gateway(self) -> Dict[str, Any]:
        """Restart the OpenClaw gateway."""
        if self.cli_available:
            return _run_cli(["gateway", "restart"], timeout_sec=15)
        return {"success": False, "error": "OpenClaw CLI not available"}

    # ── Active Application ────────────────────────────────────────

    def get_active_app(self) -> str:
        """Get the currently active application (macOS)."""
        try:
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

    # ── Keyboard / Mouse Passthrough ──────────────────────────────

    def press_key(self, key: str) -> Dict[str, Any]:
        """Press a key via AppleScript."""
        try:
            script = f'''
            tell application "System Events"
                key code {key}
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            return {"success": result.returncode == 0}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """Press hotkey combination via AppleScript."""
        try:
            # Build AppleScript keystroke command
            # e.g., hotkey("command", "s") → keystroke "s" using command down
            modifiers = []
            key = keys[-1] if keys else ""
            for k in keys[:-1]:
                mod_map = {
                    "command": "command down",
                    "cmd": "command down",
                    "control": "control down",
                    "ctrl": "control down",
                    "option": "option down",
                    "alt": "option down",
                    "shift": "shift down",
                }
                m = mod_map.get(k.lower())
                if m:
                    modifiers.append(m)

            mod_str = ", ".join(modifiers)
            if mod_str:
                script = f'''
                tell application "System Events"
                    keystroke "{key}" using {{{mod_str}}}
                end tell
                '''
            else:
                script = f'''
                tell application "System Events"
                    keystroke "{key}"
                end tell
                '''

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            return {"success": result.returncode == 0}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get bridge status."""
        return {
            "cli_available": self.cli_available,
            "monitor_port": self.monitor_port,
            "total_commands": self.total_commands,
            "successful_commands": self.successful_commands,
            "failed_commands": self.failed_commands,
            "success_rate": (
                round(self.successful_commands / self.total_commands, 2)
                if self.total_commands > 0 else 0.0
            ),
        }


# ── Singleton ─────────────────────────────────────────────────────

_bridge: Optional[OpenClawBridge] = None
_bridge_lock = threading.Lock()


def get_openclaw_bridge() -> OpenClawBridge:
    """Get singleton OpenClawBridge instance."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = OpenClawBridge()
        return _bridge


def reset_bridge() -> None:
    """Reset singleton (for testing)."""
    global _bridge
    with _bridge_lock:
        _bridge = None
