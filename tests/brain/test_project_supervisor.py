"""Tests for ProjectSupervisor - Autonomous Project Understanding."""

import json
import os
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from brain.project_supervisor import (
    ProjectSupervisor,
    ProjectSnapshot,
    ProjectIssue,
    ProjectChange,
    FileSnapshot,
    LANG_MAP,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project structure."""
    # README
    (tmp_path / "README.md").write_text("# Test Project\nA test project.", encoding="utf-8")

    # Python source
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def hello():\n    print('hello')\n", encoding="utf-8")
    (src / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    # Tests
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_hello():\n    assert True\n", encoding="utf-8")

    # Config
    (tmp_path / "requirements.txt").write_text("flask>=2.0\npytest>=7.0\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__\n.env\n", encoding="utf-8")

    # Git init
    os.system(f"cd {tmp_path} && git init -q && git add -A && git commit -q -m 'init'")

    return tmp_path


@pytest.fixture
def supervisor(tmp_project, tmp_path):
    return ProjectSupervisor(
        str(tmp_project),
        state_dir=str(tmp_path / "state"),
    )


class TestProjectSnapshot:
    def test_default_snapshot(self):
        snap = ProjectSnapshot(project_path="/test", timestamp="2026-01-01")
        assert snap.total_files == 0
        assert snap.total_lines == 0
        assert snap.languages == {}
        assert snap.has_tests is False

    def test_snapshot_with_data(self):
        snap = ProjectSnapshot(
            project_path="/test",
            timestamp="2026-01-01",
            total_files=10,
            total_lines=500,
            languages={"python": 5, "json": 2},
            has_tests=True,
        )
        assert snap.total_files == 10
        assert snap.languages["python"] == 5


class TestFileSnapshot:
    def test_file_snapshot(self):
        fs = FileSnapshot(
            path="src/main.py",
            size=100,
            lines=10,
            last_modified="2026-01-01",
            content_hash="abc123",
            language="python",
        )
        assert fs.path == "src/main.py"
        assert fs.language == "python"


class TestProjectSupervisorScan:
    def test_scan_project(self, supervisor, tmp_project):
        snapshot = supervisor.scan()
        assert snapshot.project_path == str(tmp_project.resolve())
        assert snapshot.total_files > 0
        assert snapshot.total_lines > 0
        assert "python" in snapshot.languages

    def test_scan_detects_readme(self, supervisor):
        snapshot = supervisor.scan()
        assert "Test Project" in snapshot.readme_content

    def test_scan_detects_tests(self, supervisor):
        snapshot = supervisor.scan()
        assert snapshot.has_tests is True

    def test_scan_has_git_info(self, supervisor):
        snapshot = supervisor.scan()
        # Git branch should be set after git init
        assert snapshot.git_branch  # usually "main" or "master"

    def test_scan_key_files(self, supervisor):
        snapshot = supervisor.scan()
        key_paths = [kf.path for kf in snapshot.key_files]
        assert "README.md" in key_paths
        assert "requirements.txt" in key_paths

    def test_scan_nonexistent_project(self, tmp_path):
        sup = ProjectSupervisor(str(tmp_path / "nonexistent"), state_dir=str(tmp_path / "s"))
        snap = sup.scan()
        assert len(snap.errors) > 0

    def test_scan_file_tree(self, supervisor):
        snapshot = supervisor.scan()
        assert len(snapshot.file_tree) > 0


class TestProjectSupervisorAnalyze:
    def test_analyze_returns_issues(self, supervisor):
        supervisor.scan()
        issues = supervisor.analyze()
        assert isinstance(issues, list)

    def test_analyze_detects_no_ci(self, supervisor):
        supervisor.scan()
        issues = supervisor.analyze()
        ci_issues = [i for i in issues if i.category == "structure" and "CI" in i.title]
        assert len(ci_issues) > 0  # No CI in temp project

    def test_analyze_no_missing_readme(self, supervisor):
        supervisor.scan()
        issues = supervisor.analyze()
        readme_issues = [i for i in issues if "README" in i.title]
        assert len(readme_issues) == 0  # README exists


class TestProjectSupervisorChangeDetection:
    def test_detect_new_file(self, supervisor, tmp_project):
        supervisor.scan()

        # Add a new file
        (tmp_project / "new_file.py").write_text("x = 1\n", encoding="utf-8")
        supervisor.scan()

        changes = supervisor.get_recent_changes()
        added = [c for c in changes if c["type"] == "file_added"]
        assert len(added) > 0

    def test_detect_modified_file(self, supervisor, tmp_project):
        supervisor.scan()

        # Modify a file
        import time
        time.sleep(0.1)
        (tmp_project / "src" / "main.py").write_text("def hello():\n    print('modified')\n", encoding="utf-8")
        supervisor.scan()

        changes = supervisor.get_recent_changes()
        modified = [c for c in changes if c["type"] == "file_modified"]
        assert len(modified) > 0

    def test_detect_deleted_file(self, supervisor, tmp_project):
        supervisor.scan()

        # Delete a file
        (tmp_project / "src" / "utils.py").unlink()
        supervisor.scan()

        changes = supervisor.get_recent_changes()
        deleted = [c for c in changes if c["type"] == "file_deleted"]
        assert len(deleted) > 0


class TestProjectSupervisorTerminalAnalysis:
    def test_analyze_clean_terminal(self, supervisor):
        result = supervisor.analyze_terminal_output("$ python main.py\nHello world\n$")
        assert result["has_errors"] is False
        assert result["has_warnings"] is False

    def test_analyze_terminal_with_errors(self, supervisor):
        output = """
$ python main.py
Traceback (most recent call last):
  File "main.py", line 5
    print(x)
NameError: name 'x' is not defined
"""
        result = supervisor.analyze_terminal_output(output)
        assert result["has_errors"] is True
        assert len(result["error_lines"]) > 0

    def test_analyze_terminal_with_warnings(self, supervisor):
        output = "DeprecationWarning: this function is deprecated"
        result = supervisor.analyze_terminal_output(output)
        assert result["has_warnings"] is True

    def test_analyze_terminal_with_test_results(self, supervisor):
        output = "======= 50 passed, 2 failed in 3.21s ======="
        result = supervisor.analyze_terminal_output(output)
        assert result["test_results"] is not None


class TestProjectSupervisorReadFile:
    def test_read_existing_file(self, supervisor):
        content = supervisor.read_file("README.md")
        assert "Test Project" in content

    def test_read_nonexistent_file(self, supervisor):
        content = supervisor.read_file("nonexistent.py")
        assert "not found" in content.lower()


class TestProjectSupervisorSummary:
    def test_get_summary_before_scan(self, supervisor):
        summary = supervisor.get_summary()
        assert summary["total_files"] == 0

    def test_get_summary_after_scan(self, supervisor):
        supervisor.scan()
        summary = supervisor.get_summary()
        assert summary["total_files"] > 0
        assert "python" in summary["languages"]


class TestProjectSupervisorStatePersistence:
    def test_state_saved_and_loaded(self, tmp_project, tmp_path):
        state_dir = str(tmp_path / "state")

        # First supervisor scans and saves state
        sup1 = ProjectSupervisor(str(tmp_project), state_dir=state_dir)
        sup1.scan()
        assert len(sup1._file_hashes) > 0

        # Second supervisor loads state
        sup2 = ProjectSupervisor(str(tmp_project), state_dir=state_dir)
        assert len(sup2._file_hashes) > 0


class TestLangMap:
    def test_python_extension(self):
        assert LANG_MAP[".py"] == "python"

    def test_javascript_extension(self):
        assert LANG_MAP[".js"] == "javascript"

    def test_typescript_extension(self):
        assert LANG_MAP[".ts"] == "typescript"
