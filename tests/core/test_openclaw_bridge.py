"""Tests for OpenClawBridge - Direct interface to OpenClaw."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from core.openclaw_bridge import (
    OpenClawBridge,
    get_openclaw_bridge,
    reset_bridge,
    _run_cli,
    _call_monitor_api,
    _has_openclaw_cli,
)


class TestHasOpenClawCLI:
    def test_cli_check(self):
        result = _has_openclaw_cli()
        assert isinstance(result, bool)


class TestRunCLI:
    @patch("core.openclaw_bridge.subprocess.run")
    def test_successful_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"status": "ok"}',
            stderr="",
        )
        result = _run_cli(["status"])
        assert result["success"] is True
        assert result["json"]["status"] == "ok"

    @patch("core.openclaw_bridge.subprocess.run")
    def test_failed_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error occurred",
        )
        result = _run_cli(["bad_cmd"], retries=1)
        assert result["success"] is False

    @patch("core.openclaw_bridge.subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("openclaw", 30)
        result = _run_cli(["slow_cmd"], retries=1)
        assert result["success"] is False
        assert "Timeout" in result["error"]

    @patch("core.openclaw_bridge.subprocess.run")
    def test_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = _run_cli(["missing"])
        assert result["success"] is False
        assert "not found" in result["error"]


class TestCallMonitorAPI:
    @patch("urllib.request.urlopen")
    def test_successful_get(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"success": true}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = _call_monitor_api("/api/test")
        assert result["success"] is True

    @patch("urllib.request.urlopen")
    def test_api_unavailable(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = _call_monitor_api("/api/test")
        assert result["success"] is False
        assert "unavailable" in result["error"].lower()


class TestOpenClawBridgeInit:
    def test_default_init(self):
        bridge = OpenClawBridge()
        assert bridge.monitor_port == 5111
        assert bridge.total_commands == 0
        assert bridge.successful_commands == 0
        assert bridge.failed_commands == 0

    def test_custom_port(self):
        bridge = OpenClawBridge(monitor_port=9999)
        assert bridge.monitor_port == 9999


class TestOpenClawBridgeBrowser:
    @patch("core.openclaw_bridge._call_monitor_api")
    def test_browser_navigate_via_api(self, mock_api):
        mock_api.return_value = {"success": True}
        bridge = OpenClawBridge()
        result = bridge.browser_navigate("https://example.com")
        assert result["success"] is True
        assert bridge.total_commands == 1
        assert bridge.successful_commands == 1

    @patch("core.openclaw_bridge._call_monitor_api")
    @patch("core.openclaw_bridge._run_cli")
    def test_browser_fallback_to_cli(self, mock_cli, mock_api):
        mock_api.return_value = {"success": False}
        mock_cli.return_value = {"success": True, "stdout": "navigated"}
        bridge = OpenClawBridge()
        bridge.cli_available = True
        result = bridge.browser_navigate("https://example.com")
        assert result["success"] is True

    @patch("core.openclaw_bridge._call_monitor_api")
    def test_browser_screenshot(self, mock_api):
        mock_api.return_value = {"success": True, "path": "/tmp/screenshot.png"}
        bridge = OpenClawBridge()
        result = bridge.browser_screenshot()
        assert result["success"] is True

    @patch("core.openclaw_bridge._call_monitor_api")
    def test_browser_click(self, mock_api):
        mock_api.return_value = {"success": True}
        bridge = OpenClawBridge()
        result = bridge.browser_click("#button")
        assert result["success"] is True

    @patch("core.openclaw_bridge._call_monitor_api")
    def test_browser_type(self, mock_api):
        mock_api.return_value = {"success": True}
        bridge = OpenClawBridge()
        result = bridge.browser_type("#input", "hello")
        assert result["success"] is True


class TestOpenClawBridgeDashboard:
    @patch("core.openclaw_bridge._call_monitor_api")
    def test_send_dashboard_command(self, mock_api):
        mock_api.return_value = {"success": True}
        bridge = OpenClawBridge()
        result = bridge.send_dashboard_command("run_cycle")
        assert result["success"] is True
        assert bridge.total_commands == 1

    @patch("core.openclaw_bridge._call_monitor_api")
    def test_send_dashboard_failure(self, mock_api):
        mock_api.return_value = {"success": False, "error": "timeout"}
        bridge = OpenClawBridge()
        result = bridge.send_dashboard_command("run_cycle")
        assert result["success"] is False
        assert bridge.failed_commands == 1


class TestOpenClawBridgeQueue:
    @patch("core.openclaw_bridge._call_monitor_api")
    def test_enqueue_command(self, mock_api):
        mock_api.return_value = {"success": True, "request_id": "abc123"}
        bridge = OpenClawBridge()
        result = bridge.enqueue_command("browser", {"url": "https://example.com"})
        assert result["success"] is True


class TestOpenClawBridgeTerminal:
    @patch("core.openclaw_bridge.subprocess.run")
    def test_read_terminal(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="$ python main.py\nHello World\n",
        )
        bridge = OpenClawBridge()
        content = bridge.read_terminal()
        assert "Hello World" in content

    @patch("core.openclaw_bridge.subprocess.run")
    def test_read_terminal_failure(self, mock_run):
        mock_run.side_effect = Exception("no access")
        bridge = OpenClawBridge()
        content = bridge.read_terminal()
        assert content == ""

    @patch("core.openclaw_bridge.subprocess.run")
    def test_send_to_terminal(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        bridge = OpenClawBridge()
        result = bridge.send_to_terminal("echo hello")
        assert result["success"] is True


class TestOpenClawBridgeHealth:
    @patch("core.openclaw_bridge._call_monitor_api")
    def test_check_health_api(self, mock_api):
        mock_api.return_value = {"success": True, "status": "healthy"}
        bridge = OpenClawBridge()
        result = bridge.check_health()
        assert result["success"] is True

    @patch("core.openclaw_bridge._call_monitor_api")
    @patch("core.openclaw_bridge._run_cli")
    def test_check_health_cli_fallback(self, mock_cli, mock_api):
        mock_api.return_value = {"success": False}
        mock_cli.return_value = {"success": True, "stdout": "OK"}
        bridge = OpenClawBridge()
        bridge.cli_available = True
        result = bridge.check_health()
        assert result["success"] is True


class TestOpenClawBridgeStatus:
    def test_get_status(self):
        bridge = OpenClawBridge()
        status = bridge.get_status()
        assert "cli_available" in status
        assert "total_commands" in status
        assert "success_rate" in status
        assert status["success_rate"] == 0.0

    def test_success_rate_calculation(self):
        bridge = OpenClawBridge()
        bridge.total_commands = 10
        bridge.successful_commands = 7
        status = bridge.get_status()
        assert status["success_rate"] == 0.7


class TestOpenClawBridgeSingleton:
    def test_get_bridge(self):
        reset_bridge()
        b1 = get_openclaw_bridge()
        b2 = get_openclaw_bridge()
        assert b1 is b2
        reset_bridge()

    def test_reset_bridge(self):
        reset_bridge()
        b1 = get_openclaw_bridge()
        reset_bridge()
        b2 = get_openclaw_bridge()
        assert b1 is not b2
        reset_bridge()
