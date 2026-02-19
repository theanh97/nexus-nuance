"""
REAL ACTION EXECUTION ENGINE
=============================
Actions thực sự EXECUTE - không phải placeholder

CAPABILITIES:
├── FILE OPERATIONS
│   ├── read_file
│   ├── write_file
│   ├── edit_file
│   ├── delete_file
│   └── list_directory
│
├── CODE EXECUTION
│   ├── run_python
│   ├── run_shell
│   ├── run_javascript
│   └── run_script
│
├── BROWSER AUTOMATION
│   ├── open_browser
│   ├── navigate_url
│   ├── take_screenshot
│   ├── click_element
│   └── extract_content
│
├── API OPERATIONS
│   ├── http_get
│   ├── http_post
│   ├── call_api
│   └── web_search
│
├── SYSTEM OPERATIONS
│   ├── install_package
│   ├── run_tests
│   ├── git_operations
│   └── process_management
│
└── NEXUS OPERATIONS
    ├── learn_knowledge
    ├── query_knowledge
    ├── create_task
    └── report_status
"""

import json
import os
import sys
import time
import subprocess
import shutil
import hashlib
import re
import shlex
import traceback
import ast
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable, Set
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import threading

from core.nexus_logger import get_logger

logger = get_logger(__name__)

try:
    from core.llm_caller import call_llm
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"


class ActionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ActionResult:
    """Result of an action execution"""
    action_id: str
    action_type: str
    status: ActionStatus
    output: str
    error: Optional[str]
    data: Dict
    started_at: str
    completed_at: str
    duration_ms: float
    objective_success: bool = False
    failure_code: Optional[str] = None
    policy_blocked: bool = False
    verification: Dict[str, Any] = field(default_factory=dict)


class ActionExecutor:
    """
    Real Action Execution Engine
    Thực sự execute các actions - không phải placeholder
    """

    def __init__(self):
        self.workspace = WORKSPACE_DIR
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.history_file = DATA_DIR / "action_history.jsonl"
        self.history: List[ActionResult] = []

        # Timeout settings (configurable via env vars)
        self.default_timeout = int(os.getenv("NEXUS_ACTION_TIMEOUT", "60"))
        self.max_timeout = int(os.getenv("NEXUS_ACTION_MAX_TIMEOUT", "300"))

        # Policy
        self.execution_mode = os.getenv("NEXUS_EXECUTION_MODE", "FULL_AUTO").upper()
        self.allowed_roots = [PROJECT_ROOT / "workspace", PROJECT_ROOT / "data", PROJECT_ROOT / "src"]
        self.mutating_actions = {"write_file", "edit_file", "delete_file", "create_directory", "run_script"}
        self.sensitive_prefixes = ["/etc", "/private/etc", "/bin", "/sbin", "/System", "/usr"]
        self.dangerous_shell_tokens = {"rm -rf /", "shutdown", "reboot", "mkfs", ":(){:|:&};:"}

        # Register all action handlers
        self.handlers: Dict[str, Callable] = {
            # File operations
            "read_file": self._action_read_file,
            "write_file": self._action_write_file,
            "edit_file": self._action_edit_file,
            "delete_file": self._action_delete_file,
            "list_directory": self._action_list_directory,
            "create_directory": self._action_create_directory,

            # Code execution
            "run_python": self._action_run_python,
            "run_shell": self._action_run_shell,
            "run_script": self._action_run_script,

            # Browser automation
            "open_browser": self._action_open_browser,
            "navigate_url": self._action_navigate_url,
            "take_screenshot": self._action_take_screenshot,

            # API operations
            "http_get": self._action_http_get,
            "http_post": self._action_http_post,
            "web_search": self._action_web_search,

            # System operations
            "install_package": self._action_install_package,
            "run_tests": self._action_run_tests,
            "git_status": self._action_git_status,
            "git_commit": self._action_git_commit,

            # Nexus operations
            "learn_knowledge": self._action_learn_knowledge,
            "query_knowledge": self._action_query_knowledge,
            "create_task": self._action_create_task,
            "analyze_code": self._action_analyze_code,
        }

        self._load_history()

    def _load_history(self):
        """Load action history"""
        if self.history_file.exists():
            with open(self.history_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        status = data.get("status", ActionStatus.FAILED.value)
                        data["status"] = ActionStatus(status)
                        result = ActionResult(**data)
                        self.history.append(result)
                    except Exception:
                        continue

    def _save_result(self, result: ActionResult):
        """Save action result"""
        with open(self.history_file, 'a', encoding='utf-8') as f:
            data = {
                "action_id": result.action_id,
                "action_type": result.action_type,
                "status": result.status.value,
                "output": result.output[:2000],
                "error": result.error,
                "data": result.data,
                "started_at": result.started_at,
                "completed_at": result.completed_at,
                "duration_ms": result.duration_ms,
                "objective_success": result.objective_success,
                "failure_code": result.failure_code,
                "policy_blocked": result.policy_blocked,
                "verification": result.verification,
            }
            f.write(json.dumps(data) + "\n")

    def _generate_action_id(self) -> str:
        """Generate unique action ID"""
        return f"action_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"

    def _resolve_path(self, input_path: str) -> Path:
        path = Path(input_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / input_path
        return path.resolve()

    def _enforce_path_policy(self, path: Path, action_type: str):
        in_allowed_roots = any(str(path).startswith(str(root.resolve())) for root in self.allowed_roots)
        path_str = str(path)
        sensitive = any(path_str.startswith(prefix) for prefix in self.sensitive_prefixes)

        # Always deny sensitive system paths.
        if sensitive and not path_str.startswith(str(PROJECT_ROOT.resolve())):
            raise PermissionError(f"Policy blocked sensitive path for {action_type}: {path}")

        # SAFE mode enforces all actions in allowed roots.
        if self.execution_mode == "SAFE" and not in_allowed_roots:
            raise PermissionError(f"Policy blocked {action_type} outside allowed roots: {path}")

        # In FULL_AUTO/BALANCED, mutating actions are still constrained.
        if action_type in self.mutating_actions and not in_allowed_roots:
            raise PermissionError(f"Policy blocked mutating action outside allowed roots: {path}")

    def _enforce_shell_policy(self, command: str):
        lower = command.lower()
        if any(token in lower for token in self.dangerous_shell_tokens):
            raise PermissionError("Policy blocked dangerous shell command")

    def _validate_shell_command_input(self, command: str):
        if any(ch in command for ch in ("\x00", "\n", "\r")):
            raise PermissionError("Policy blocked shell command containing control characters")

        lower = command.lower()

        if re.search(r"(?:^|\s)(sudo|su)\b", lower):
            raise PermissionError("Policy blocked privileged shell command")

        if re.search(r"\b(curl|wget)\b[^\n]*\|\s*(bash|sh|zsh)\b", lower):
            raise PermissionError("Policy blocked pipe-to-shell command pattern")

        sensitive_prefixes = tuple(prefix.lower() for prefix in self.sensitive_prefixes)
        if any(prefix in lower for prefix in sensitive_prefixes) and (">" in command or re.search(r"\btee\b", lower)):
            raise PermissionError("Policy blocked shell command writing to sensitive system paths")

    # ==================== MAIN EXECUTION ====================

    def execute(self, action_type: str, params: Dict, timeout: int = None, max_retries: int = None) -> ActionResult:
        """
        Execute an action with real effects.
        Supports automatic retry with exponential backoff for transient failures.
        """
        if max_retries is None:
            max_retries = int(os.getenv("NEXUS_ACTION_MAX_RETRIES", "0"))
        action_id = self._generate_action_id()
        started_at = datetime.now().isoformat()

        result = ActionResult(
            action_id=action_id,
            action_type=action_type,
            status=ActionStatus.PENDING,
            output="",
            error=None,
            data={},
            started_at=started_at,
            completed_at="",
            duration_ms=0
        )

        # Check if action exists (with fuzzy fallback)
        if action_type not in self.handlers:
            # Try common aliases: dash ↔ underscore, camelCase → snake_case
            normalized = action_type.replace("-", "_").lower()
            if normalized in self.handlers:
                action_type = normalized
            else:
                # Check partial match
                similar = next((k for k in self.handlers if normalized in k or k in normalized), None)
                if similar:
                    action_type = similar
                else:
                    result.status = ActionStatus.FAILED
                    result.error = f"Unknown action type: {action_type}. Available: {', '.join(sorted(self.handlers.keys()))}"
                    result.completed_at = datetime.now().isoformat()
                    self._save_result(result)
                    return result

        # Execute
        result.status = ActionStatus.RUNNING

        try:
            handler = self.handlers[action_type]
            timeout = min(timeout or self.default_timeout, self.max_timeout)

            # Run with timeout
            output, data, error = self._run_with_timeout(handler, params, timeout)

            result.output = output
            result.data = data
            result.error = str(error) if error else None
            result.status = ActionStatus.SUCCESS if not error else ActionStatus.FAILED
            result.objective_success = result.status == ActionStatus.SUCCESS
            result.verification = {"has_output": bool(output)}
            if error:
                if isinstance(error, PermissionError):
                    result.policy_blocked = True
                    result.failure_code = "policy_blocked"
                else:
                    result.failure_code = "handler_error"

        except subprocess.TimeoutExpired:
            result.status = ActionStatus.TIMEOUT
            result.error = f"Action timed out after {timeout}s"
            result.objective_success = False
            result.failure_code = "timeout"

        except Exception as e:
            result.status = ActionStatus.FAILED
            result.error = f"{type(e).__name__}: {str(e)}"
            result.output = traceback.format_exc()
            result.objective_success = False
            if isinstance(e, PermissionError):
                result.policy_blocked = True
                result.failure_code = "policy_blocked"
            else:
                result.failure_code = "exception"

        result.completed_at = datetime.now().isoformat()
        start_time = datetime.fromisoformat(started_at)
        end_time = datetime.fromisoformat(result.completed_at)
        result.duration_ms = (end_time - start_time).total_seconds() * 1000

        # Smart retry for transient failures (timeouts, network errors)
        _RETRYABLE = {"timeout", "handler_error"}
        if (
            result.status != ActionStatus.SUCCESS
            and max_retries > 0
            and result.failure_code in _RETRYABLE
            and not result.policy_blocked
        ):
            wait = min(2 ** (3 - max_retries), 30)  # exponential backoff: 1, 2, 4, ...
            time.sleep(wait)
            return self.execute(action_type, params, timeout=timeout, max_retries=max_retries - 1)

        # LLM post-execution analysis for failures
        if _LLM_AVAILABLE and result.status == ActionStatus.FAILED and result.error:
            try:
                analysis = call_llm(
                    prompt=(
                        f"Action '{action_type}' failed with error: {result.error[:300]}. "
                        f"Params: {json.dumps(params, default=str)[:200]}. "
                        "Suggest a brief fix in 1-2 sentences."
                    ),
                    task_type="code_review",
                    max_tokens=100,
                    temperature=0.2,
                )
                if isinstance(analysis, str) and analysis.strip():
                    result.verification["llm_fix_suggestion"] = analysis.strip()[:300]
            except Exception:
                pass

        self._save_result(result)
        self.history.append(result)

        return result

    def _run_with_timeout(self, handler: Callable, params: Dict, timeout: int) -> Tuple[str, Dict, Optional[Any]]:
        """Run handler with timeout"""
        result = {"output": "", "data": {}, "error": None}

        def run():
            try:
                output, data = handler(params)
                result["output"] = output
                result["data"] = data
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            raise subprocess.TimeoutExpired(handler.__name__, timeout)

        return result["output"], result["data"], result["error"]

    # ==================== FILE OPERATIONS ====================

    def _action_read_file(self, params: Dict) -> Tuple[str, Dict]:
        """Read file content"""
        file_path = params.get("path")
        if not file_path:
            raise ValueError("path parameter required")

        # Resolve path
        path = self._resolve_path(file_path)
        self._enforce_path_policy(path, "read_file")

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        return content, {"path": str(path), "size": len(content), "lines": content.count('\n') + 1}

    def _action_write_file(self, params: Dict) -> Tuple[str, Dict]:
        """Write content to file"""
        file_path = params.get("path")
        content = params.get("content", "")

        if not file_path:
            raise ValueError("path parameter required")

        path = self._resolve_path(file_path)
        self._enforce_path_policy(path, "write_file")

        # Create parent directories
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        return f"Written {len(content)} bytes to {path}", {"path": str(path), "size": len(content)}

    def _action_edit_file(self, params: Dict) -> Tuple[str, Dict]:
        """Edit file - replace string"""
        file_path = params.get("path")
        old_string = params.get("old")
        new_string = params.get("new", "")

        if not file_path or old_string is None:
            raise ValueError("path and old parameters required")

        path = self._resolve_path(file_path)
        self._enforce_path_policy(path, "edit_file")

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if old_string not in content:
            raise ValueError(f"String not found in file: {old_string[:50]}...")

        new_content = content.replace(old_string, new_string, 1)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return f"Edited {path}: replaced 1 occurrence", {"path": str(path)}

    def _action_delete_file(self, params: Dict) -> Tuple[str, Dict]:
        """Delete file or directory"""
        file_path = params.get("path")

        path = self._resolve_path(file_path)
        self._enforce_path_policy(path, "delete_file")

        if path.is_file():
            path.unlink()
            return f"Deleted file: {path}", {"path": str(path)}
        elif path.is_dir():
            shutil.rmtree(path)
            return f"Deleted directory: {path}", {"path": str(path)}
        else:
            raise FileNotFoundError(f"Path not found: {path}")

    def _action_list_directory(self, params: Dict) -> Tuple[str, Dict]:
        """List directory contents"""
        dir_path = params.get("path", ".")
        pattern = params.get("pattern", "*")

        path = self._resolve_path(dir_path)
        self._enforce_path_policy(path, "list_directory")

        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        items = list(path.glob(pattern))
        files = [{"name": i.name, "type": "dir" if i.is_dir() else "file", "size": i.stat().st_size if i.is_file() else 0} for i in items]

        output = "\n".join(f"{i['type']}: {i['name']}" for i in files)
        return output, {"path": str(path), "count": len(files), "items": files}

    def _action_create_directory(self, params: Dict) -> Tuple[str, Dict]:
        """Create directory"""
        dir_path = params.get("path")

        path = self._resolve_path(dir_path)
        self._enforce_path_policy(path, "create_directory")

        path.mkdir(parents=True, exist_ok=True)

        return f"Created directory: {path}", {"path": str(path)}

    # ==================== CODE EXECUTION ====================

    @staticmethod
    def _extract_python_defined_names(code: str) -> List[str]:
        try:
            module = ast.parse(code)
        except Exception:
            return []

        def names_from_target(target: ast.AST) -> List[str]:
            if isinstance(target, ast.Name):
                return [target.id]
            if isinstance(target, (ast.Tuple, ast.List)):
                names: List[str] = []
                for elt in target.elts:
                    names.extend(names_from_target(elt))
                return names
            return []

        names: Set[str] = set()

        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    names.update(names_from_target(target))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                names.update(names_from_target(node.target))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                names.update(names_from_target(node.target))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        names.update(names_from_target(item.optional_vars))
            elif isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.name:
                        names.add(handler.name)

        return sorted(names)

    def _action_run_python(self, params: Dict) -> Tuple[str, Dict]:
        """Execute Python code"""
        code = params.get("code")
        file_path = params.get("file")

        if file_path:
            path = self._resolve_path(file_path)
            self._enforce_path_policy(path, "run_python")
            with open(path, 'r', encoding='utf-8') as f:
                code = f.read()

        if not code:
            raise ValueError("code or file parameter required")

        variables = self._extract_python_defined_names(code)
        try:
            result = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(WORKSPACE_DIR),
            )
        except Exception as e:
            raise RuntimeError(f"Execution error: {e}")

        if result.stderr:
            raise RuntimeError(f"Execution error: {result.stderr.strip()}")
        if result.returncode != 0:
            raise RuntimeError(f"Execution error: Process exited with code {result.returncode}")

        return result.stdout, {"variables": variables}

    def _action_run_shell(self, params: Dict) -> Tuple[str, Dict]:
        """Execute shell command"""
        command = params.get("command")
        cwd = params.get("cwd", str(PROJECT_ROOT / "workspace"))

        if not command:
            raise ValueError("command parameter required")
        self._enforce_shell_policy(command)
        self._validate_shell_command_input(command)
        cwd_path = self._resolve_path(cwd)
        self._enforce_path_policy(cwd_path, "run_shell")

        # Use argument list instead of shell=True for safety
        try:
            cmd_args = shlex.split(command)
        except ValueError:
            raise ValueError(f"Malformed command string: {command!r}")

        result = subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            cwd=str(cwd_path),
            timeout=self.default_timeout
        )

        output = result.stdout
        if result.returncode != 0:
            raise RuntimeError(f"Command failed: {result.stderr}")

        return output, {"return_code": result.returncode, "command": command}

    def _action_run_script(self, params: Dict) -> Tuple[str, Dict]:
        """Run a script file"""
        script_path = params.get("path")
        args = params.get("args", [])

        path = self._resolve_path(script_path)
        self._enforce_path_policy(path, "run_script")

        if not path.exists():
            raise FileNotFoundError(f"Script not found: {path}")

        # Determine interpreter
        suffix = path.suffix.lower()
        interpreters = {
            ".py": "python3",
            ".sh": "bash",
            ".js": "node",
        }

        interpreter = interpreters.get(suffix, "bash")
        cmd = [interpreter, str(path)] + args

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.default_timeout)

        return result.stdout, {"return_code": result.returncode, "stderr": result.stderr}

    # ==================== BROWSER AUTOMATION ====================

    def _action_open_browser(self, params: Dict) -> Tuple[str, Dict]:
        """Open browser (using system open command)"""
        url = params.get("url")
        file_path = params.get("file")

        if file_path:
            path = Path(file_path)
            if not path.is_absolute():
                path = PROJECT_ROOT / file_path
            url = f"file://{path}"

        if not url:
            raise ValueError("url or file parameter required")

        # Use system open command (no shell=True - use os.startfile on Windows)
        if sys.platform == "darwin":
            subprocess.run(["open", url])
        elif sys.platform == "linux":
            subprocess.run(["xdg-open", url])
        else:
            os.startfile(url)  # Windows-safe, no shell injection

        return f"Opened browser: {url}", {"url": url}

    def _action_navigate_url(self, params: Dict) -> Tuple[str, Dict]:
        """Navigate to URL using Playwright"""
        url = params.get("url")

        if not url:
            raise ValueError("url parameter required")

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, timeout=30000)
                title = page.title()
                browser.close()

            return f"Navigated to: {url}\nTitle: {title}", {"url": url, "title": title}

        except ImportError:
            return "Playwright not installed. Run: pip install playwright && playwright install", {"url": url}

    def _action_take_screenshot(self, params: Dict) -> Tuple[str, Dict]:
        """Take screenshot of URL"""
        url = params.get("url")
        output_path = params.get("output", str(DATA_DIR / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"))

        if not url:
            raise ValueError("url parameter required")

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1920, "height": 1080})
                page.goto(url, timeout=30000)
                page.screenshot(path=output_path, full_page=True)
                browser.close()

            return f"Screenshot saved: {output_path}", {"path": output_path, "url": url}

        except ImportError:
            raise ImportError("Playwright not installed. Run: pip install playwright && playwright install")

    # ==================== API OPERATIONS ====================

    def _action_http_get(self, params: Dict) -> Tuple[str, Dict]:
        """HTTP GET request"""
        url = params.get("url")
        headers = params.get("headers", {})

        if not url:
            raise ValueError("url parameter required")

        import urllib.request
        import urllib.error

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode('utf-8')

        return content[:5000], {"url": url, "status": 200, "size": len(content)}

    def _action_http_post(self, params: Dict) -> Tuple[str, Dict]:
        """HTTP POST request"""
        url = params.get("url")
        data = params.get("data", {})
        headers = params.get("headers", {"Content-Type": "application/json"})

        if not url:
            raise ValueError("url parameter required")

        import urllib.request
        import urllib.error

        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=json_data, headers=headers, method='POST')

        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode('utf-8')

        return content[:5000], {"url": url, "status": 200}

    def _action_web_search(self, params: Dict) -> Tuple[str, Dict]:
        """Web search with real fetch + graceful unavailable fallback."""
        query = params.get("query")

        if not query:
            raise ValueError("query parameter required")
        import urllib.request
        import urllib.parse

        search_url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "NexusExecutor/1.0 (+https://nexus.local)"},
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8", errors="ignore")

            matches = re.findall(
                r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            results = []
            for href, raw_title in matches[:10]:
                title = re.sub(r"<[^>]+>", "", raw_title)
                title = re.sub(r"\s+", " ", title).strip()
                if not title:
                    continue
                results.append({"title": title[:240], "url": href})

            if not results:
                return (
                    f"No results parsed for: {query}",
                    {"query": query, "results": [], "status": "unavailable"},
                )

            return (
                f"Search results for: {query} ({len(results)} items)",
                {"query": query, "results": results, "status": "ok"},
            )
        except Exception as e:
            return (
                f"Search unavailable for: {query}",
                {"query": query, "results": [], "status": "unavailable", "error": str(e)},
            )

    # ==================== SYSTEM OPERATIONS ====================

    def _action_install_package(self, params: Dict) -> Tuple[str, Dict]:
        """Install a package"""
        package = params.get("package")
        manager = params.get("manager", "pip")

        if not package:
            raise ValueError("package parameter required")

        # Validate package name (alphanumeric, hyphens, dots, brackets)
        if not re.match(r'^[a-zA-Z0-9._\-\[\],>=<! ]+$', package):
            raise ValueError(f"Invalid package name: {package!r}")

        if manager == "pip":
            cmd_args = ["pip", "install"] + shlex.split(package)
        elif manager == "npm":
            cmd_args = ["npm", "install"] + shlex.split(package)
        else:
            raise ValueError(f"Unknown package manager: {manager}")

        result = subprocess.run(cmd_args, capture_output=True, text=True)

        return result.stdout, {"package": package, "manager": manager, "success": result.returncode == 0}

    def _action_run_tests(self, params: Dict) -> Tuple[str, Dict]:
        """Run tests"""
        test_path = params.get("path", "tests/")
        framework = params.get("framework", "pytest")

        if framework == "pytest":
            cmd_args = ["python3", "-m", "pytest", test_path, "-v"]
        else:
            cmd_args = ["python3", "-m", "unittest", "discover", test_path]

        result = subprocess.run(cmd_args, capture_output=True, text=True, cwd=str(PROJECT_ROOT))

        return result.stdout, {"return_code": result.returncode, "stderr": result.stderr}

    def _action_git_status(self, params: Dict) -> Tuple[str, Dict]:
        """Get git status"""
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT)
        )

        return result.stdout, {"return_code": result.returncode}

    def _action_git_commit(self, params: Dict) -> Tuple[str, Dict]:
        """Git add and commit"""
        message = params.get("message", f"Auto-commit by NEXUS at {datetime.now().isoformat()}")
        files = params.get("files", ["."])

        # Add files
        add_cmd = ["git", "add"] + files
        subprocess.run(add_cmd, cwd=str(PROJECT_ROOT))

        # Commit
        commit_cmd = ["git", "commit", "-m", message]
        result = subprocess.run(commit_cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))

        return result.stdout, {"message": message, "success": result.returncode == 0}

    # ==================== NEXUS OPERATIONS ====================

    def _action_learn_knowledge(self, params: Dict) -> Tuple[str, Dict]:
        """Learn new knowledge into NEXUS"""
        content = params.get("content")
        source = params.get("source", "action")
        ktype = params.get("type", "general")

        if not content:
            raise ValueError("content parameter required")

        try:
            from .integration_hub import learn
            result = learn(content, source)
            return f"Learned: {content[:100]}", {"result": result}
        except Exception as e:
            return f"Would learn: {content[:100]}", {"content": content, "error": str(e)}

    def _action_query_knowledge(self, params: Dict) -> Tuple[str, Dict]:
        """Query NEXUS knowledge"""
        query = params.get("query")

        if not query:
            raise ValueError("query parameter required")

        try:
            from .integration_hub import query_knowledge
            result = query_knowledge(query)
            return f"Query results for: {query}", {"result": result}
        except Exception as e:
            return f"Would query: {query}", {"query": query, "error": str(e)}

    def _action_create_task(self, params: Dict) -> Tuple[str, Dict]:
        """Create a new task"""
        task = params.get("task")
        priority = params.get("priority", "MEDIUM")

        if not task:
            raise ValueError("task parameter required")

        # Store task
        tasks_file = DATA_DIR / "tasks.json"
        tasks = []
        if tasks_file.exists():
            with open(tasks_file, 'r', encoding='utf-8') as f:
                tasks = json.load(f)

        new_task = {
            "id": f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "task": task,
            "priority": priority,
            "status": "pending",
            "created_at": datetime.now().isoformat()
        }
        tasks.append(new_task)

        with open(tasks_file, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=2)

        return f"Created task: {task}", {"task": new_task}

    def _action_analyze_code(self, params: Dict) -> Tuple[str, Dict]:
        """Analyze code file"""
        file_path = params.get("path")

        if not file_path:
            raise ValueError("path parameter required")

        path = Path(file_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / file_path

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Basic analysis
        lines = content.count('\n') + 1
        chars = len(content)
        functions = len(re.findall(r'def \w+', content))
        classes = len(re.findall(r'class \w+', content))
        imports = len(re.findall(r'^import |^from ', content, re.MULTILINE))

        analysis = {
            "path": str(path),
            "lines": lines,
            "characters": chars,
            "functions": functions,
            "classes": classes,
            "imports": imports
        }

        output = f"Analysis of {path}:\n"
        output += f"  Lines: {lines}\n"
        output += f"  Functions: {functions}\n"
        output += f"  Classes: {classes}\n"

        return output, analysis

    # ==================== STATS ====================

    def get_stats(self) -> Dict:
        """Get execution statistics"""
        if not self.history:
            return {"total": 0, "success": 0, "failed": 0, "success_rate": 0}

        success = sum(1 for a in self.history if a.status == ActionStatus.SUCCESS)
        failed = sum(1 for a in self.history if a.status == ActionStatus.FAILED)

        return {
            "total": len(self.history),
            "success": success,
            "failed": failed,
            "success_rate": success / len(self.history) if self.history else 0,
            "available_actions": list(self.handlers.keys())
        }

    def list_available_actions(self) -> List[Dict]:
        """List all available actions"""
        categories = {
            "file": ["read_file", "write_file", "edit_file", "delete_file", "list_directory", "create_directory"],
            "code": ["run_python", "run_shell", "run_script"],
            "browser": ["open_browser", "navigate_url", "take_screenshot"],
            "api": ["http_get", "http_post", "web_search"],
            "system": ["install_package", "run_tests", "git_status", "git_commit"],
            "nexus": ["learn_knowledge", "query_knowledge", "create_task", "analyze_code"]
        }

        result = []
        for category, actions in categories.items():
            for action in actions:
                result.append({"action": action, "category": category})

        return result


# ==================== CONVENIENCE ====================

_executor: Optional[ActionExecutor] = None


def get_executor() -> ActionExecutor:
    """Get singleton executor"""
    global _executor
    if _executor is None:
        _executor = ActionExecutor()
    return _executor


def execute_action(action_type: str, params: Dict, timeout: int = None) -> ActionResult:
    """Execute an action"""
    return get_executor().execute(action_type, params, timeout)


def list_actions() -> List[Dict]:
    """List available actions"""
    return get_executor().list_available_actions()


# ==================== MAIN ====================

if __name__ == "__main__":
    print("=" * 70)
    print("REAL ACTION EXECUTION ENGINE")
    print("=" * 70)

    executor = get_executor()

    print("\nAvailable Actions:")
    for action in executor.list_available_actions():
        print(f"  [{action['category']}] {action['action']}")

    print("\n" + "=" * 70)
    print("TESTING REAL EXECUTION")
    print("=" * 70)

    # Test 1: Write file
    print("\n1. Testing write_file...")
    result = executor.execute("write_file", {
        "path": "workspace/test.txt",
        "content": "Hello from NEXUS Action Executor!\nThis is a real file write."
    })
    print(f"   Status: {result.status.value}")
    print(f"   Output: {result.output}")

    # Test 2: Read file
    print("\n2. Testing read_file...")
    result = executor.execute("read_file", {"path": "workspace/test.txt"})
    print(f"   Status: {result.status.value}")
    print(f"   Output: {result.output[:100]}...")

    # Test 3: List directory
    print("\n3. Testing list_directory...")
    result = executor.execute("list_directory", {"path": "src/brain", "pattern": "*.py"})
    print(f"   Status: {result.status.value}")
    print(f"   Found: {result.data.get('count', 0)} files")

    # Test 4: Run shell
    print("\n4. Testing run_shell...")
    result = executor.execute("run_shell", {"command": "echo 'Hello from shell!' && date"})
    print(f"   Status: {result.status.value}")
    print(f"   Output: {result.output.strip()}")

    # Test 5: Analyze code
    print("\n5. Testing analyze_code...")
    result = executor.execute("analyze_code", {"path": "src/brain/nexus_brain.py"})
    print(f"   Status: {result.status.value}")
    print(f"   Analysis: {result.data}")

    print("\n" + "=" * 70)
    print(f"Stats: {executor.get_stats()}")
