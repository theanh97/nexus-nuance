"""Tests for the Playwright browser automation module.

Tests run WITHOUT a real browser - they validate:
- Module structure and configuration
- Session management
- Error handling when browser not launched
- Capabilities reporting
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.browser_automation import (
    PlaywrightAutomation,
    BrowserBackend,
    BrowserMode,
    BrowserSession,
    AutomationResult,
    get_playwright_automation,
    PLAYWRIGHT_AVAILABLE,
)


class TestAutomationResult:
    def test_success_result(self):
        r = AutomationResult(success=True, action="click", data={"selector": "#btn"})
        assert r.success is True
        assert r.action == "click"
        assert r.error == ""

    def test_failure_result(self):
        r = AutomationResult(success=False, action="navigate", error="timeout")
        assert r.success is False
        assert r.error == "timeout"


class TestBrowserSession:
    def test_session_creation(self):
        s = BrowserSession(
            session_id="test-1",
            backend=BrowserBackend.CHROMIUM,
            mode=BrowserMode.HEADLESS,
        )
        assert s.alive is True
        assert s.page_url == ""
        assert s.session_id == "test-1"


class TestPlaywrightAutomationWithoutBrowser:
    """Tests that work without launching a real browser."""

    def test_default_config(self):
        auto = PlaywrightAutomation()
        assert auto._backend == BrowserBackend.CHROMIUM
        assert auto._mode == BrowserMode.HEADLESS
        assert auto.is_launched is False
        assert auto.current_url == ""

    def test_custom_config(self):
        auto = PlaywrightAutomation(
            backend=BrowserBackend.FIREFOX,
            mode=BrowserMode.HEADED,
            timeout_ms=5000,
        )
        assert auto._backend == BrowserBackend.FIREFOX
        assert auto._mode == BrowserMode.HEADED
        assert auto._timeout_ms == 5000

    def test_capabilities_without_launch(self):
        auto = PlaywrightAutomation()
        caps = auto.get_capabilities()
        assert caps["browser_launched"] is False
        assert caps["backend"] == "chromium"
        assert caps["mode"] == "headless"
        assert caps["session_count"] == 0
        assert "playwright_available" in caps

    def test_no_sessions_initially(self):
        auto = PlaywrightAutomation()
        assert auto.get_sessions() == []


class TestNoPageErrors:
    """All actions should return clean errors when no page is available."""

    @pytest.fixture()
    def auto(self):
        return PlaywrightAutomation()

    @pytest.mark.asyncio
    async def test_navigate_no_page(self, auto):
        r = await auto.navigate("https://example.com")
        assert r.success is False
        assert "no browser page" in r.error

    @pytest.mark.asyncio
    async def test_click_no_page(self, auto):
        r = await auto.click("#btn")
        assert r.success is False
        assert "no browser page" in r.error

    @pytest.mark.asyncio
    async def test_fill_no_page(self, auto):
        r = await auto.fill("#input", "text")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_type_text_no_page(self, auto):
        r = await auto.type_text("#input", "hello")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_press_key_no_page(self, auto):
        r = await auto.press_key("Enter")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_screenshot_no_page(self, auto):
        r = await auto.screenshot()
        assert r.success is False

    @pytest.mark.asyncio
    async def test_evaluate_no_page(self, auto):
        r = await auto.evaluate("document.title")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_wait_for_selector_no_page(self, auto):
        r = await auto.wait_for_selector(".loading")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_get_text_no_page(self, auto):
        r = await auto.get_text("h1")
        assert r.success is False

    @pytest.mark.asyncio
    async def test_get_page_content_no_page(self, auto):
        r = await auto.get_page_content()
        assert r.success is False


class TestLaunchWithoutPlaywright:
    """Test launch when playwright is not installed."""

    @pytest.mark.asyncio
    async def test_launch_without_playwright(self, monkeypatch):
        monkeypatch.setattr("src.core.browser_automation.PLAYWRIGHT_AVAILABLE", False)
        auto = PlaywrightAutomation()
        r = await auto.launch()
        assert r.success is False
        assert "playwright not installed" in r.error


class TestCloseWithoutLaunch:
    @pytest.mark.asyncio
    async def test_close_without_launch(self):
        auto = PlaywrightAutomation()
        r = await auto.close()
        assert r.success is True  # Closing when not launched is OK


class TestSessionManagement:
    def test_create_session(self):
        auto = PlaywrightAutomation()
        session = auto._create_session()
        assert session.session_id.startswith("pw-")
        assert session.alive is True
        assert len(auto.get_sessions()) == 1

    def test_multiple_sessions(self):
        auto = PlaywrightAutomation()
        s1 = auto._create_session()
        s2 = auto._create_session()
        assert s1.session_id != s2.session_id
        assert len(auto.get_sessions()) == 2

    def test_session_serialization(self):
        auto = PlaywrightAutomation()
        auto._create_session()
        sessions = auto.get_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert "session_id" in s
        assert "backend" in s
        assert "mode" in s
        assert "alive" in s
        assert s["backend"] == "chromium"


class TestBrowserBackendEnum:
    def test_all_backends(self):
        assert BrowserBackend.CHROMIUM.value == "chromium"
        assert BrowserBackend.FIREFOX.value == "firefox"
        assert BrowserBackend.WEBKIT.value == "webkit"


class TestBrowserModeEnum:
    def test_all_modes(self):
        assert BrowserMode.HEADLESS.value == "headless"
        assert BrowserMode.HEADED.value == "headed"
