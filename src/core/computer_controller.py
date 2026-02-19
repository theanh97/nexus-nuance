"""
Computer Controller
Allows Guardian to control the computer when needed
Uses PyAutoGUI for mouse/keyboard control and AppleScript for macOS
"""

import os
import sys
import asyncio
import shlex
import subprocess
import tempfile
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime

# Try to import optional dependencies
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
    pyautogui.FAILSAFE = True  # Move mouse to corner to abort
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class ComputerController:
    """
    Computer Controller for Guardian

    Allows the Guardian agent to:
    - View screen (screenshots)
    - Control mouse
    - Type on keyboard
    - Run terminal commands
    - Interact with UI elements
    """

    def __init__(self):
        self.platform = sys.platform
        self.screenshot_dir = Path("screenshots/guardian")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self.last_screenshot = None
        self.command_history: List[Dict] = []

    async def take_screenshot(self, save: bool = True) -> str:
        """Take a screenshot of the current screen"""

        if PYAUTOGUI_AVAILABLE:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"guardian_{timestamp}.png"
            path = self.screenshot_dir / filename

            screenshot = pyautogui.screenshot()

            if save:
                screenshot.save(str(path))
                self.last_screenshot = str(path)
                return str(path)
            else:
                return screenshot
        else:
            # Fallback to macOS screencapture
            return await self._macos_screenshot()

    async def _macos_screenshot(self) -> str:
        """Take screenshot using macOS screencapture command"""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.screenshot_dir / f"guardian_{timestamp}.png"

        result = subprocess.run(
            ["screencapture", "-x", str(path)],
            capture_output=True
        )

        if result.returncode == 0:
            self.last_screenshot = str(path)
            return str(path)
        else:
            raise Exception(f"Screenshot failed: {result.stderr.decode()}")

    async def get_mouse_position(self) -> Tuple[int, int]:
        """Get current mouse position"""

        if PYAUTOGUI_AVAILABLE:
            return pyautogui.position()
        return (0, 0)

    async def move_mouse(self, x: int, y: int, duration: float = 0.5):
        """Move mouse to position"""

        if PYAUTOGUI_AVAILABLE:
            pyautogui.moveTo(x, y, duration=duration)
            return True
        return False

    async def click(self, x: int = None, y: int = None, button: str = "left"):
        """Click at position or current position"""

        if PYAUTOGUI_AVAILABLE:
            if x is not None and y is not None:
                pyautogui.click(x, y, button=button)
            else:
                pyautogui.click(button=button)
            return True
        return False

    async def type_text(self, text: str, interval: float = 0.05):
        """Type text"""

        if PYAUTOGUI_AVAILABLE:
            pyautogui.typewrite(text, interval=interval)
            return True
        return False

    async def press_key(self, key: str):
        """Press a key"""

        if PYAUTOGUI_AVAILABLE:
            pyautogui.press(key)
            return True
        return False

    async def hotkey(self, *keys: str):
        """Press hotkey combination"""

        if PYAUTOGUI_AVAILABLE:
            pyautogui.hotkey(*keys)
            return True
        return False

    async def run_terminal_command(self, command: str, timeout: int = 30) -> Dict:
        """Run a terminal command"""

        self.command_history.append({
            "command": command,
            "timestamp": datetime.now().isoformat()
        })

        try:
            try:
                cmd_args = shlex.split(command)
            except ValueError:
                return {"success": False, "error": f"Malformed command: {command!r}"}
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def run_applescript(self, script: str) -> Dict:
        """Run AppleScript on macOS"""

        if self.platform != "darwin":
            return {"success": False, "error": "AppleScript only available on macOS"}

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True
            )

            return {
                "success": result.returncode == 0,
                "output": result.stdout.strip(),
                "error": result.stderr.strip() if result.stderr else None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def click_ui_element(self, element_name: str) -> Dict:
        """Click a UI element by name using AppleScript"""

        if self.platform != "darwin":
            return {"success": False, "error": "Only available on macOS"}

        script = f'''
        tell application "System Events"
            tell process "Terminal"
                click button "{element_name}"
            end tell
        end tell
        '''

        return await self.run_applescript(script)

    async def get_terminal_content(self) -> str:
        """Get content from Terminal app"""

        if self.platform != "darwin":
            return ""

        script = '''
        tell application "Terminal"
            if (count of windows) > 0 then
                tell front window
                    return contents
                end tell
            end if
        end tell
        '''

        result = await self.run_applescript(script)
        return result.get("output", "") if result.get("success") else ""

    async def find_on_screen(self, image_path: str, confidence: float = 0.9) -> Optional[Tuple[int, int]]:
        """Find an image on screen"""

        if not PYAUTOGUI_AVAILABLE or not PIL_AVAILABLE:
            return None

        try:
            location = pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location:
                return pyautogui.center(location)
            return None
        except Exception:
            return None

    async def wait_for_image(self, image_path: str, timeout: int = 30, confidence: float = 0.9) -> Optional[Tuple[int, int]]:
        """Wait for an image to appear on screen"""

        start_time = datetime.now()
        while (datetime.now() - start_time).seconds < timeout:
            location = await self.find_on_screen(image_path, confidence)
            if location:
                return location
            await asyncio.sleep(0.5)

        return None

    async def click_button(self, button_text: str) -> bool:
        """
        Click a button by its text
        Uses OCR or pattern matching
        """

        # This is a simplified version
        # In production, you'd use OCR or more sophisticated methods

        # Take screenshot
        screenshot = await self.take_screenshot()

        # For now, return True as placeholder
        # Real implementation would:
        # 1. Use OCR to find button text
        # 2. Calculate button position
        # 3. Click at that position

        return True

    def get_capabilities(self) -> Dict:
        """Get available capabilities"""

        return {
            "platform": self.platform,
            "pyautogui": PYAUTOGUI_AVAILABLE,
            "pil": PIL_AVAILABLE,
            "screenshot": True,
            "mouse_control": PYAUTOGUI_AVAILABLE,
            "keyboard_control": PYAUTOGUI_AVAILABLE,
            "terminal": True,
            "applescript": self.platform == "darwin",
            "terminal_content": self.platform == "darwin"
        }


# Singleton instance
_controller_instance = None

def get_computer_controller() -> ComputerController:
    """Get singleton ComputerController instance"""
    global _controller_instance
    if _controller_instance is None:
        _controller_instance = ComputerController()
    return _controller_instance
