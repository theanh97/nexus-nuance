"""
Project Supervisor - Autonomous Project Understanding & Monitoring
==================================================================

Reads, understands, analyzes, and monitors projects autonomously:
- Scans project structure and key files
- Tracks file changes over time
- Analyzes code quality and progress
- Identifies issues and suggests actions
- Provides project state snapshots for the supervisor daemon

Usage:
    from src.brain.project_supervisor import ProjectSupervisor

    supervisor = ProjectSupervisor("/path/to/project")
    snapshot = supervisor.scan()
    issues = supervisor.analyze()
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.nexus_logger import get_logger

logger = get_logger(__name__)

try:
    from core.llm_caller import call_llm
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class FileSnapshot:
    """Snapshot of a single file."""
    path: str
    size: int
    lines: int
    last_modified: str
    content_hash: str
    language: str = ""


@dataclass
class ProjectSnapshot:
    """Complete snapshot of a project's state."""
    project_path: str
    timestamp: str
    total_files: int = 0
    total_lines: int = 0
    total_size: int = 0
    languages: Dict[str, int] = field(default_factory=dict)  # lang -> file count
    file_tree: List[str] = field(default_factory=list)
    key_files: List[FileSnapshot] = field(default_factory=list)
    git_status: str = ""
    git_branch: str = ""
    recent_commits: List[str] = field(default_factory=list)
    readme_content: str = ""
    has_tests: bool = False
    has_ci: bool = False
    has_docker: bool = False
    config_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class ProjectIssue:
    """An identified issue in the project."""
    severity: str  # critical, high, medium, low, info
    category: str  # code, config, test, security, performance, structure
    title: str
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    suggestion: str = ""


@dataclass
class ProjectChange:
    """A detected change in the project."""
    timestamp: str
    change_type: str  # file_added, file_modified, file_deleted, git_commit
    path: str
    details: str = ""


# ── Language Detection ────────────────────────────────────────────

LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "react-js", ".tsx": "react-ts", ".java": "java",
    ".go": "go", ".rs": "rust", ".cpp": "c++", ".c": "c",
    ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".kt": "kotlin", ".cs": "c#", ".html": "html",
    ".css": "css", ".scss": "scss", ".sql": "sql",
    ".sh": "shell", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".toml": "toml", ".md": "markdown",
}

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", "coverage", ".idea", ".vscode",
    "env", ".env", "eggs", "*.egg-info",
}

IGNORE_FILES = {
    ".DS_Store", "Thumbs.db", "*.pyc", "*.pyo", "*.so",
    "*.dylib", "*.class", "*.o", "*.obj",
}

KEY_FILE_PATTERNS = [
    "README*", "LICENSE*", "Makefile", "Dockerfile*",
    "docker-compose*", "package.json", "requirements*.txt",
    "setup.py", "pyproject.toml", "Cargo.toml", "go.mod",
    ".env.template", ".env.example", "*.config.js", "*.config.ts",
    "tsconfig.json", "jest.config*", "pytest.ini", "setup.cfg",
    "tox.ini", ".github/workflows/*", ".gitlab-ci.yml",
    "Jenkinsfile", ".circleci/config.yml",
]


class ProjectSupervisor:
    """
    Autonomous project understanding and monitoring engine.

    Scans a project directory, builds a comprehensive snapshot,
    detects changes, identifies issues, and provides actionable insights.
    """

    def __init__(
        self,
        project_path: str,
        state_dir: str = "data/supervisor",
        max_file_size: int = 500_000,  # 500KB max per file for content reading
        max_scan_files: int = 5000,
    ):
        self.project_path = Path(project_path).resolve()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.max_file_size = max_file_size
        self.max_scan_files = max_scan_files

        # State
        self._last_snapshot: Optional[ProjectSnapshot] = None
        self._file_hashes: Dict[str, str] = {}  # path -> content hash
        self._changes: List[ProjectChange] = []
        self._issues: List[ProjectIssue] = []

        # Load previous state if exists
        self._load_state()

    # ── Project Scanning ──────────────────────────────────────────

    def scan(self) -> ProjectSnapshot:
        """Perform a full project scan and return snapshot."""
        now = datetime.now().isoformat()
        snapshot = ProjectSnapshot(
            project_path=str(self.project_path),
            timestamp=now,
        )

        if not self.project_path.exists():
            snapshot.errors.append(f"Project path does not exist: {self.project_path}")
            return snapshot

        # 1. Scan file tree
        files = self._scan_files()
        snapshot.total_files = len(files)
        snapshot.file_tree = [str(f.relative_to(self.project_path)) for f in files[:200]]

        # 2. Language statistics
        for f in files:
            lang = LANG_MAP.get(f.suffix.lower(), "other")
            snapshot.languages[lang] = snapshot.languages.get(lang, 0) + 1

        # 3. Count lines and size
        for f in files:
            try:
                stat = f.stat()
                snapshot.total_size += stat.st_size
                if stat.st_size < self.max_file_size and f.suffix.lower() in LANG_MAP:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                        snapshot.total_lines += sum(1 for _ in fh)
            except Exception:
                pass

        # 4. Key files
        snapshot.key_files = self._scan_key_files()
        snapshot.config_files = [kf.path for kf in snapshot.key_files if kf.language in ("yaml", "json", "toml")]

        # 5. README
        readme = self._find_readme()
        if readme:
            try:
                snapshot.readme_content = readme.read_text(encoding="utf-8", errors="ignore")[:3000]
            except Exception:
                pass

        # 6. Git info
        snapshot.git_branch = self._git_branch()
        snapshot.git_status = self._git_status()
        snapshot.recent_commits = self._git_recent_commits(10)

        # 7. Project features
        snapshot.has_tests = any("test" in str(f).lower() for f in files)
        snapshot.has_ci = any(
            p in str(f) for f in files
            for p in [".github/workflows", ".gitlab-ci", "Jenkinsfile", ".circleci"]
        )
        snapshot.has_docker = any("docker" in f.name.lower() for f in files)

        # 8. Detect changes from last snapshot
        self._detect_changes(files)

        # Update state
        self._last_snapshot = snapshot
        self._save_state()

        logger.info(
            f"Project scan: {snapshot.total_files} files, "
            f"{snapshot.total_lines} lines, "
            f"{len(snapshot.languages)} languages"
        )

        return snapshot

    def _scan_files(self) -> List[Path]:
        """Scan project directory for all relevant files."""
        files = []
        count = 0

        for root, dirs, filenames in os.walk(self.project_path):
            # Skip ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for fname in filenames:
                if count >= self.max_scan_files:
                    break
                if any(fname.endswith(ext) for ext in [".pyc", ".pyo", ".so", ".class"]):
                    continue
                if fname in (".DS_Store", "Thumbs.db"):
                    continue

                files.append(Path(root) / fname)
                count += 1

        return files

    def _scan_key_files(self) -> List[FileSnapshot]:
        """Scan for key project files (config, build, etc.)."""
        key_files = []

        for pattern in KEY_FILE_PATTERNS:
            for match in self.project_path.glob(pattern):
                if match.is_file():
                    try:
                        stat = match.stat()
                        content = ""
                        if stat.st_size < self.max_file_size:
                            content = match.read_text(encoding="utf-8", errors="ignore")

                        key_files.append(FileSnapshot(
                            path=str(match.relative_to(self.project_path)),
                            size=stat.st_size,
                            lines=content.count("\n") + 1 if content else 0,
                            last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            content_hash=hashlib.md5(content.encode()).hexdigest() if content else "",
                            language=LANG_MAP.get(match.suffix.lower(), ""),
                        ))
                    except Exception:
                        pass

        return key_files

    def _find_readme(self) -> Optional[Path]:
        """Find README file."""
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            path = self.project_path / name
            if path.exists():
                return path
        return None

    # ── Git Operations ────────────────────────────────────────────

    def _git_run(self, args: List[str]) -> str:
        """Run a git command and return output."""
        try:
            result = subprocess.run(
                ["git"] + args,
                capture_output=True, text=True,
                cwd=str(self.project_path),
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _git_branch(self) -> str:
        return self._git_run(["branch", "--show-current"])

    def _git_status(self) -> str:
        return self._git_run(["status", "--short"])

    def _git_recent_commits(self, count: int = 10) -> List[str]:
        output = self._git_run(["log", f"-{count}", "--oneline"])
        return output.split("\n") if output else []

    def _git_diff_stat(self) -> str:
        return self._git_run(["diff", "--stat"])

    # ── Change Detection ──────────────────────────────────────────

    def _detect_changes(self, files: List[Path]) -> None:
        """Detect file changes since last scan."""
        now = datetime.now().isoformat()
        new_hashes: Dict[str, str] = {}

        for f in files:
            try:
                rel = str(f.relative_to(self.project_path))
                stat = f.stat()
                h = f"{stat.st_size}:{stat.st_mtime}"
                new_hashes[rel] = h

                if rel not in self._file_hashes:
                    self._changes.append(ProjectChange(
                        timestamp=now,
                        change_type="file_added",
                        path=rel,
                    ))
                elif self._file_hashes[rel] != h:
                    self._changes.append(ProjectChange(
                        timestamp=now,
                        change_type="file_modified",
                        path=rel,
                    ))
            except Exception:
                pass

        # Detect deletions
        for rel in set(self._file_hashes) - set(new_hashes):
            self._changes.append(ProjectChange(
                timestamp=now,
                change_type="file_deleted",
                path=rel,
            ))

        self._file_hashes = new_hashes

        # Keep only recent changes
        if len(self._changes) > 500:
            self._changes = self._changes[-500:]

    # ── Issue Analysis ────────────────────────────────────────────

    def analyze(self) -> List[ProjectIssue]:
        """Analyze the project for potential issues."""
        issues: List[ProjectIssue] = []

        if not self._last_snapshot:
            self.scan()

        snapshot = self._last_snapshot
        if not snapshot:
            return issues

        # 1. Missing README
        if not snapshot.readme_content:
            issues.append(ProjectIssue(
                severity="medium",
                category="structure",
                title="Missing README",
                description="Project has no README file.",
                suggestion="Create a README.md with project description, setup, and usage instructions.",
            ))

        # 2. No tests
        if not snapshot.has_tests:
            issues.append(ProjectIssue(
                severity="high",
                category="test",
                title="No tests found",
                description="No test files detected in the project.",
                suggestion="Add unit tests using pytest/jest/etc.",
            ))

        # 3. No CI
        if not snapshot.has_ci:
            issues.append(ProjectIssue(
                severity="medium",
                category="structure",
                title="No CI/CD configuration",
                description="No CI pipeline configuration found.",
                suggestion="Add GitHub Actions, GitLab CI, or similar.",
            ))

        # 4. Uncommitted changes
        if snapshot.git_status:
            changed_count = len([l for l in snapshot.git_status.split("\n") if l.strip()])
            if changed_count > 20:
                issues.append(ProjectIssue(
                    severity="medium",
                    category="code",
                    title=f"Many uncommitted changes ({changed_count} files)",
                    description=f"There are {changed_count} uncommitted file changes.",
                    suggestion="Review and commit changes, or stash unfinished work.",
                ))

        # 5. Large files
        for kf in snapshot.key_files:
            if kf.size > 100_000 and kf.language in ("python", "javascript", "typescript"):
                issues.append(ProjectIssue(
                    severity="low",
                    category="code",
                    title=f"Large source file: {kf.path}",
                    description=f"{kf.path} is {kf.size // 1024}KB ({kf.lines} lines).",
                    file_path=kf.path,
                    suggestion="Consider splitting into smaller modules.",
                ))

        # 6. Check for common config issues
        self._check_config_issues(issues)

        self._issues = issues
        return issues

    def _check_config_issues(self, issues: List[ProjectIssue]) -> None:
        """Check for configuration issues."""
        # Check for .env files committed
        env_path = self.project_path / ".env"
        gitignore = self.project_path / ".gitignore"

        if env_path.exists() and gitignore.exists():
            gi_content = gitignore.read_text(encoding="utf-8", errors="ignore")
            if ".env" not in gi_content:
                issues.append(ProjectIssue(
                    severity="high",
                    category="security",
                    title=".env not in .gitignore",
                    description=".env file exists but is not in .gitignore. Secrets may be committed.",
                    file_path=".gitignore",
                    suggestion="Add .env to .gitignore immediately.",
                ))

    # ── LLM-powered Analysis ─────────────────────────────────────

    def analyze_with_llm(self, question: str = "") -> str:
        """Use LLM to analyze the project state."""
        if not _LLM_AVAILABLE:
            return "LLM not available for analysis."

        snapshot = self._last_snapshot or self.scan()

        context = (
            f"Project: {snapshot.project_path}\n"
            f"Files: {snapshot.total_files}, Lines: {snapshot.total_lines}\n"
            f"Languages: {json.dumps(snapshot.languages)}\n"
            f"Branch: {snapshot.git_branch}\n"
            f"Git status:\n{snapshot.git_status[:500]}\n"
            f"Recent commits:\n" + "\n".join(snapshot.recent_commits[:5]) + "\n"
            f"README excerpt:\n{snapshot.readme_content[:500]}\n"
        )

        prompt = question or "Analyze this project state. What is the project about? What's its current status? Any concerns?"

        try:
            return call_llm(
                prompt=f"{prompt}\n\nProject context:\n{context}",
                task_type="code_review",
                max_tokens=500,
                temperature=0.3,
            )
        except Exception as e:
            return f"LLM analysis failed: {e}"

    # ── Read Specific File ────────────────────────────────────────

    def read_file(self, relative_path: str) -> str:
        """Read a project file by relative path."""
        path = self.project_path / relative_path
        if not path.exists():
            return f"File not found: {relative_path}"
        if path.stat().st_size > self.max_file_size:
            return f"File too large: {path.stat().st_size} bytes"
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return f"Error reading file: {e}"

    # ── Terminal Output Analysis ──────────────────────────────────

    def analyze_terminal_output(self, terminal_content: str) -> Dict[str, Any]:
        """Analyze terminal output for errors, warnings, and progress."""
        result = {
            "has_errors": False,
            "has_warnings": False,
            "error_lines": [],
            "warning_lines": [],
            "test_results": None,
            "build_status": None,
            "recent_command": "",
        }

        lines = terminal_content.split("\n")

        for i, line in enumerate(lines):
            lower = line.lower()

            # Detect errors
            if any(kw in lower for kw in ["error", "failed", "traceback", "exception", "fatal"]):
                if not any(kw in lower for kw in ["0 error", "no error", "error: 0"]):
                    result["has_errors"] = True
                    result["error_lines"].append(line.strip()[:200])

            # Detect warnings
            if "warning" in lower and "0 warning" not in lower:
                result["has_warnings"] = True
                result["warning_lines"].append(line.strip()[:200])

            # Detect test results
            if "passed" in lower and ("failed" in lower or "error" in lower or "passed" in lower):
                result["test_results"] = line.strip()

            # Detect build status
            if any(kw in lower for kw in ["build successful", "build failed", "compiled"]):
                result["build_status"] = line.strip()

        # Limit
        result["error_lines"] = result["error_lines"][-10:]
        result["warning_lines"] = result["warning_lines"][-10:]

        return result

    # ── State Persistence ─────────────────────────────────────────

    def _state_file(self) -> Path:
        safe_name = hashlib.md5(str(self.project_path).encode()).hexdigest()[:12]
        return self.state_dir / f"project_{safe_name}.json"

    def _save_state(self) -> None:
        """Save current state to disk."""
        try:
            data = {
                "project_path": str(self.project_path),
                "file_hashes": self._file_hashes,
                "last_scan": self._last_snapshot.timestamp if self._last_snapshot else None,
            }
            self._state_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to save state: {e}")

    def _load_state(self) -> None:
        """Load previous state from disk."""
        try:
            path = self._state_file()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._file_hashes = data.get("file_hashes", {})
        except Exception:
            pass

    # ── Summary ───────────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the project's current state."""
        snapshot = self._last_snapshot
        return {
            "project_path": str(self.project_path),
            "last_scan": snapshot.timestamp if snapshot else None,
            "total_files": snapshot.total_files if snapshot else 0,
            "total_lines": snapshot.total_lines if snapshot else 0,
            "languages": snapshot.languages if snapshot else {},
            "git_branch": snapshot.git_branch if snapshot else "",
            "has_tests": snapshot.has_tests if snapshot else False,
            "has_ci": snapshot.has_ci if snapshot else False,
            "has_docker": snapshot.has_docker if snapshot else False,
            "issues_count": len(self._issues),
            "recent_changes": len(self._changes),
        }

    def get_recent_changes(self, limit: int = 20) -> List[Dict[str, str]]:
        """Get recent file changes."""
        return [
            {
                "timestamp": c.timestamp,
                "type": c.change_type,
                "path": c.path,
                "details": c.details,
            }
            for c in self._changes[-limit:]
        ]
