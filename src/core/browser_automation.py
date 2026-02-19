"""
Browser automation module using Playwright.

Lightweight alternative to OpenClaw for browser-based automation.
Supports headless and headed modes, page navigation, element interaction,
screenshots, and JavaScript execution.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class BrowserBackend(str, Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class BrowserMode(str, Enum):
    HEADLESS = "headless"
    HEADED = "headed"


@dataclass
class BrowserSession:
    """Tracks a single browser session."""
    session_id: str
    backend: BrowserBackend
    mode: BrowserMode
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    page_url: str = ""
    alive: bool = True


@dataclass
class AutomationResult:
    """Standard result from any automation action."""
    success: bool
    action: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    elapsed_ms: int = 0


_DEFAULT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_DEFAULT_TIMEOUT_MS", "30000"))
_SCREENSHOT_DIR = Path(os.getenv("PLAYWRIGHT_SCREENSHOT_DIR", "screenshots/automation"))


class PlaywrightAutomation:
    """
    Lightweight browser automation using Playwright.

    Replaces OpenClaw browser mode with a pure-Python, async-first
    automation layer that supports headless operation out of the box.
    """

    def __init__(
        self,
        backend: BrowserBackend = BrowserBackend.CHROMIUM,
        mode: BrowserMode = BrowserMode.HEADLESS,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ):
        self._backend = backend
        self._mode = mode
        self._timeout_ms = timeout_ms
        self._playwright: Any = None
        self._browser: Optional[Any] = None
        self._context: Optional[Any] = None
        self._page: Optional[Any] = None
        self._sessions: Dict[str, BrowserSession] = {}
        self._session_counter = 0
        self._screenshot_dir = _SCREENSHOT_DIR
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_available(self) -> bool:
        return PLAYWRIGHT_AVAILABLE

    @property
    def is_launched(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    @property
    def current_url(self) -> str:
        if self._page and not self._page.is_closed():
            try:
                return self._page.url
            except Exception:
                return ""
        return ""

    async def launch(self) -> AutomationResult:
        """Launch the browser."""
        if not PLAYWRIGHT_AVAILABLE:
            return AutomationResult(
                success=False,
                action="launch",
                error="playwright not installed (pip install playwright && playwright install)",
            )
        if self.is_launched:
            return AutomationResult(success=True, action="launch", data={"status": "already_running"})

        start = time.time()
        try:
            self._playwright = await async_playwright().start()
            launcher = getattr(self._playwright, self._backend.value)
            self._browser = await launcher.launch(
                headless=(self._mode == BrowserMode.HEADLESS),
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="NexusAutomation/1.0",
            )
            self._context.set_default_timeout(self._timeout_ms)
            self._page = await self._context.new_page()
            elapsed = int((time.time() - start) * 1000)

            session = self._create_session()
            return AutomationResult(
                success=True,
                action="launch",
                data={
                    "session_id": session.session_id,
                    "backend": self._backend.value,
                    "mode": self._mode.value,
                },
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False,
                action="launch",
                error=str(exc),
                elapsed_ms=elapsed,
            )

    async def close(self) -> AutomationResult:
        """Close the browser and clean up."""
        start = time.time()
        errors: List[str] = []
        try:
            if self._context:
                await self._context.close()
        except Exception as exc:
            errors.append(f"context: {exc}")
        try:
            if self._browser:
                await self._browser.close()
        except Exception as exc:
            errors.append(f"browser: {exc}")
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            errors.append(f"playwright: {exc}")

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

        for sid in list(self._sessions):
            self._sessions[sid].alive = False

        elapsed = int((time.time() - start) * 1000)
        if errors:
            return AutomationResult(
                success=False, action="close", error="; ".join(errors), elapsed_ms=elapsed,
            )
        return AutomationResult(success=True, action="close", elapsed_ms=elapsed)

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> AutomationResult:
        """Navigate to a URL."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("navigate")
        start = time.time()
        try:
            response = await page.goto(url, wait_until=wait_until)
            status = response.status if response else 0
            elapsed = int((time.time() - start) * 1000)
            self._touch_session()
            return AutomationResult(
                success=True,
                action="navigate",
                data={"url": url, "status": status, "final_url": page.url},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="navigate", error=str(exc), elapsed_ms=elapsed,
            )

    async def click(self, selector: str, timeout_ms: Optional[int] = None) -> AutomationResult:
        """Click an element by CSS selector."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("click")
        start = time.time()
        try:
            await page.click(selector, timeout=timeout_ms or self._timeout_ms)
            elapsed = int((time.time() - start) * 1000)
            self._touch_session()
            return AutomationResult(
                success=True, action="click", data={"selector": selector}, elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="click", error=str(exc), elapsed_ms=elapsed,
            )

    async def fill(self, selector: str, text: str, timeout_ms: Optional[int] = None) -> AutomationResult:
        """Fill a text input."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("fill")
        start = time.time()
        try:
            await page.fill(selector, text, timeout=timeout_ms or self._timeout_ms)
            elapsed = int((time.time() - start) * 1000)
            self._touch_session()
            return AutomationResult(
                success=True,
                action="fill",
                data={"selector": selector, "text_length": len(text)},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="fill", error=str(exc), elapsed_ms=elapsed,
            )

    async def type_text(self, selector: str, text: str, delay_ms: int = 50) -> AutomationResult:
        """Type text character by character (simulates real typing)."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("type_text")
        start = time.time()
        try:
            await page.type(selector, text, delay=delay_ms)
            elapsed = int((time.time() - start) * 1000)
            self._touch_session()
            return AutomationResult(
                success=True,
                action="type_text",
                data={"selector": selector, "text_length": len(text)},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="type_text", error=str(exc), elapsed_ms=elapsed,
            )

    async def press_key(self, key: str, selector: Optional[str] = None) -> AutomationResult:
        """Press a keyboard key."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("press_key")
        start = time.time()
        try:
            if selector:
                await page.press(selector, key)
            else:
                await page.keyboard.press(key)
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=True, action="press_key", data={"key": key}, elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="press_key", error=str(exc), elapsed_ms=elapsed,
            )

    async def screenshot(self, full_page: bool = False, path: Optional[str] = None) -> AutomationResult:
        """Take a screenshot of the current page."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("screenshot")
        start = time.time()
        try:
            if not path:
                ts = int(time.time() * 1000)
                path = str(self._screenshot_dir / f"auto_{ts}.png")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=path, full_page=full_page)
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=True,
                action="screenshot",
                data={"path": path, "full_page": full_page, "url": page.url},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="screenshot", error=str(exc), elapsed_ms=elapsed,
            )

    async def evaluate(self, expression: str) -> AutomationResult:
        """Evaluate JavaScript on the page."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("evaluate")
        start = time.time()
        try:
            result = await page.evaluate(expression)
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=True,
                action="evaluate",
                data={"result": result},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="evaluate", error=str(exc), elapsed_ms=elapsed,
            )

    async def wait_for_selector(
        self, selector: str, state: str = "visible", timeout_ms: Optional[int] = None,
    ) -> AutomationResult:
        """Wait for an element to appear/be visible."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("wait_for_selector")
        start = time.time()
        try:
            await page.wait_for_selector(
                selector, state=state, timeout=timeout_ms or self._timeout_ms,
            )
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=True,
                action="wait_for_selector",
                data={"selector": selector, "state": state},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="wait_for_selector", error=str(exc), elapsed_ms=elapsed,
            )

    async def get_text(self, selector: str) -> AutomationResult:
        """Get text content of an element."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("get_text")
        start = time.time()
        try:
            text = await page.text_content(selector)
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=True,
                action="get_text",
                data={"selector": selector, "text": text or ""},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="get_text", error=str(exc), elapsed_ms=elapsed,
            )

    async def get_page_content(self) -> AutomationResult:
        """Get the full HTML content of the page."""
        page = self._ensure_page()
        if not page:
            return self._no_page_error("get_page_content")
        start = time.time()
        try:
            content = await page.content()
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=True,
                action="get_page_content",
                data={"length": len(content), "url": page.url},
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            return AutomationResult(
                success=False, action="get_page_content", error=str(exc), elapsed_ms=elapsed,
            )

    def get_sessions(self) -> List[Dict[str, Any]]:
        """Get all tracked sessions."""
        return [
            {
                "session_id": s.session_id,
                "backend": s.backend.value,
                "mode": s.mode.value,
                "created_at": s.created_at,
                "last_active": s.last_active,
                "page_url": s.page_url,
                "alive": s.alive,
            }
            for s in self._sessions.values()
        ]

    def get_capabilities(self) -> Dict[str, Any]:
        """Return available capabilities."""
        return {
            "playwright_available": PLAYWRIGHT_AVAILABLE,
            "browser_launched": self.is_launched,
            "backend": self._backend.value,
            "mode": self._mode.value,
            "current_url": self.current_url,
            "session_count": len(self._sessions),
            "timeout_ms": self._timeout_ms,
        }

    # ── Internal helpers ─────────────────────────────────────────

    def _ensure_page(self) -> Optional[Any]:
        """Return the current page if alive."""
        if self._page and not self._page.is_closed():
            return self._page
        return None

    def _no_page_error(self, action: str) -> AutomationResult:
        return AutomationResult(
            success=False,
            action=action,
            error="no browser page available (call launch() first)",
        )

    def _create_session(self) -> BrowserSession:
        self._session_counter += 1
        sid = f"pw-{self._session_counter}-{int(time.time())}"
        session = BrowserSession(
            session_id=sid,
            backend=self._backend,
            mode=self._mode,
        )
        self._sessions[sid] = session
        return session

    def _touch_session(self) -> None:
        """Update last_active on the most recent session."""
        if self._sessions:
            latest = max(self._sessions.values(), key=lambda s: s.created_at)
            latest.last_active = time.time()
            latest.page_url = self.current_url


# ── Module-level singleton ──────────────────────────────────────

_instance: Optional[PlaywrightAutomation] = None


def get_playwright_automation(
    backend: Optional[BrowserBackend] = None,
    mode: Optional[BrowserMode] = None,
) -> PlaywrightAutomation:
    """Get or create the singleton PlaywrightAutomation instance."""
    global _instance
    if _instance is None:
        env_backend = os.getenv("PLAYWRIGHT_BACKEND", "chromium").strip().lower()
        env_mode = os.getenv("PLAYWRIGHT_MODE", "headless").strip().lower()
        _instance = PlaywrightAutomation(
            backend=backend or BrowserBackend(env_backend) if env_backend in BrowserBackend.__members__.values() else BrowserBackend.CHROMIUM,
            mode=mode or BrowserMode(env_mode) if env_mode in BrowserMode.__members__.values() else BrowserMode.HEADLESS,
        )
    return _instance
