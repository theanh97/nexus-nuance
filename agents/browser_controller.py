"""
Browser Controller
Controls browser using Playwright for screenshots and interactions
"""

import os
import asyncio
from typing import Optional, List, Dict
from datetime import datetime
from pathlib import Path


class BrowserController:
    """
    Browser control using Playwright
    For screenshots, navigation, and UI interactions
    """

    def __init__(self, headless: bool = False, screenshot_dir: str = "../screenshots"):
        self.headless = headless
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.browser = None
        self.page = None
        self.playwright = None

    async def start(self):
        """Start browser"""
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080}
        )
        self.page = await self.context.new_page()
        print("Browser started")

    async def stop(self):
        """Stop browser"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("Browser stopped")

    async def navigate(self, url: str) -> bool:
        """Navigate to URL"""
        try:
            await self.page.goto(url, wait_until="networkidle")
            return True
        except Exception as e:
            print(f"Navigation error: {e}")
            return False

    async def screenshot(self, name: Optional[str] = None, full_page: bool = True) -> str:
        """Take screenshot"""
        if not name:
            name = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

        path = self.screenshot_dir / name

        await self.page.screenshot(
            path=str(path),
            full_page=full_page
        )

        print(f"Screenshot saved: {path}")
        return str(path)

    async def click(self, selector: str) -> bool:
        """Click element"""
        try:
            await self.page.click(selector)
            return True
        except Exception as e:
            print(f"Click error: {e}")
            return False

    async def type_text(self, selector: str, text: str) -> bool:
        """Type text into element"""
        try:
            await self.page.fill(selector, text)
            return True
        except Exception as e:
            print(f"Type error: {e}")
            return False

    async def get_content(self) -> str:
        """Get page content"""
        return await self.page.content()

    async def evaluate(self, script: str):
        """Execute JavaScript"""
        return await self.page.evaluate(script)

    async def wait_for_selector(self, selector: str, timeout: int = 10000) -> bool:
        """Wait for selector to appear"""
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            return True
        except:
            return False

    async def open_local_app(self, app_path: str) -> bool:
        """Open local HTML file"""
        full_path = Path(app_path).absolute()
        return await self.navigate(f"file://{full_path}")


class LocalAppServer:
    """
    Simple HTTP server for running local apps
    """

    def __init__(self, port: int = 3000, app_dir: str = "../app-output"):
        self.port = port
        self.app_dir = Path(app_dir)
        self.server = None

    async def start(self):
        """Start local server"""
        import http.server
        import socketserver
        import threading

        os.chdir(self.app_dir)

        handler = http.server.SimpleHTTPRequestHandler

        class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
            allow_reuse_address = True

        self.server = ThreadedServer(("", self.port), handler)

        thread = threading.Thread(target=self.server.serve_forever)
        thread.daemon = True
        thread.start()

        print(f"Local server started at http://localhost:{self.port}")

    def stop(self):
        """Stop local server"""
        if self.server:
            self.server.shutdown()
            print("Local server stopped")


class AppRunner:
    """
    Runs and manages the app being developed
    """

    def __init__(self, browser: BrowserController, server: LocalAppServer):
        self.browser = browser
        self.server = server

    async def start_app(self, entry_file: str = "index.html") -> str:
        """Start app and return URL"""
        await self.server.start()
        url = f"http://localhost:{self.server.port}/{entry_file}"
        await self.browser.navigate(url)
        return url

    async def capture_state(self) -> Dict:
        """Capture current app state"""
        screenshot_path = await self.browser.screenshot()
        content = await self.browser.get_content()

        return {
            "screenshot": screenshot_path,
            "html": content,
            "timestamp": datetime.now().isoformat()
        }

    async def interact_and_capture(self, actions: List[Dict]) -> Dict:
        """Perform actions and capture result"""
        for action in actions:
            if action["type"] == "click":
                await self.browser.click(action["selector"])
            elif action["type"] == "type":
                await self.browser.type_text(action["selector"], action["text"])
            elif action["type"] == "wait":
                await asyncio.sleep(action["duration"])

        return await self.capture_state()


async def main():
    """Test browser controller"""
    browser = BrowserController(headless=False)

    try:
        await browser.start()

        # Navigate to example
        await browser.navigate("https://example.com")

        # Take screenshot
        path = await browser.screenshot("test.png")
        print(f"Screenshot: {path}")

        # Wait for user
        await asyncio.sleep(5)

    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
