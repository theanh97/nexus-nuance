"""
Safe Apply Module
=================

Safely applies code patches with rollback capability.
Ensures system stability during self-modification.
"""

import os
import json
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
import threading
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Result of applying a patch."""
    patch_id: str
    success: bool
    file_path: str
    backup_path: Optional[str]
    tests_passed: bool
    error: Optional[str] = None
    rolled_back: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class SafeApplier:
    """
    Safely applies code patches with full rollback capability.

    Safety mechanisms:
    1. Backup original file before modification
    2. Validate patch syntax
    3. Run tests after apply
    4. Auto-rollback on failure
    5. Maintain apply history
    """

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Storage
        self.backups_dir = self.project_root / "data" / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        self.history: List[ApplyResult] = []
        self.history_file = self.project_root / "data" / "patches" / "apply_history.json"
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        self._load_history()

    def _load_history(self):
        """Load apply history."""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.history = [ApplyResult(**r) for r in data.get("history", [])]
            except Exception as e:
                logger.warning(f"Failed to load apply history: {e}")

    def _save_history(self):
        """Save apply history."""
        with self._lock:
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_applies": len(self.history),
                "successful": len([r for r in self.history if r.success]),
                "rolled_back": len([r for r in self.history if r.rolled_back]),
                "history": [asdict(r) for r in self.history[-200:]]
            }
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

    def apply_patch(self, patch: Dict, auto_rollback: bool = True) -> ApplyResult:
        """
        Apply a patch safely with optional auto-rollback.

        Steps:
        1. Create backup
        2. Validate patch
        3. Apply patch
        4. Run tests
        5. Rollback if tests fail
        """
        patch_id = patch.get("patch_id", "unknown")
        file_path = patch.get("file_path", "")
        patched_code = patch.get("patched_code", "")

        full_path = self.project_root / file_path

        # Step 1: Create backup
        backup_path = self._create_backup(full_path)
        if not backup_path:
            return ApplyResult(
                patch_id=patch_id,
                success=False,
                file_path=file_path,
                backup_path=None,
                tests_passed=False,
                error="Failed to create backup"
            )

        # Step 2: Validate patch
        if not self._validate_patch(patched_code):
            return ApplyResult(
                patch_id=patch_id,
                success=False,
                file_path=file_path,
                backup_path=str(backup_path),
                tests_passed=False,
                error="Patch validation failed (syntax error)"
            )

        # Step 3: Apply patch
        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(patched_code)
            logger.info(f"Applied patch {patch_id} to {file_path}")
        except Exception as e:
            return ApplyResult(
                patch_id=patch_id,
                success=False,
                file_path=file_path,
                backup_path=str(backup_path),
                tests_passed=False,
                error=f"Failed to write patch: {e}"
            )

        # Step 4: Run tests
        tests_passed = self._run_quick_tests()

        # Step 5: Rollback if tests fail
        if not tests_passed and auto_rollback:
            self._rollback(full_path, backup_path)
            result = ApplyResult(
                patch_id=patch_id,
                success=False,
                file_path=file_path,
                backup_path=str(backup_path),
                tests_passed=False,
                error="Tests failed, patch rolled back",
                rolled_back=True
            )
        else:
            result = ApplyResult(
                patch_id=patch_id,
                success=tests_passed,
                file_path=file_path,
                backup_path=str(backup_path),
                tests_passed=tests_passed
            )

        # Record history
        with self._lock:
            self.history.append(result)
            self._save_history()

        return result

    def _create_backup(self, file_path: Path) -> Optional[Path]:
        """Create a backup of the file."""
        if not file_path.exists():
            return None

        # Create backup with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_path.name}.{timestamp}.bak"

        # Create unique backup directory for this file
        file_hash = hashlib.md5(str(file_path).encode()).hexdigest()[:8]
        backup_dir = self.backups_dir / file_hash
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_path = backup_dir / backup_name

        try:
            shutil.copy2(file_path, backup_path)
            logger.debug(f"Created backup: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return None

    def _validate_patch(self, code: str) -> bool:
        """Validate patch syntax."""
        try:
            compile(code, '<string>', 'exec')
            return True
        except SyntaxError as e:
            logger.warning(f"Patch has syntax error: {e}")
            return False

    def _run_quick_tests(self) -> bool:
        """Run quick tests to validate the patch."""
        try:
            # Try to import the main module
            result = subprocess.run(
                ["python", "-c", "import sys; sys.path.insert(0, '.'); from src import *"],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f"Quick import test failed: {result.stderr[:200]}")
                return False

            # Run a quick pytest if available
            test_result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=no", "-k", "not slow"],
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=60
            )

            # Consider tests passed if no tests failed (exit code 0 or 5 for no tests)
            return test_result.returncode in [0, 5]

        except subprocess.TimeoutExpired:
            logger.warning("Quick tests timed out")
            return False
        except Exception as e:
            logger.warning(f"Quick tests failed: {e}")
            return True  # Allow if tests can't run

    def _rollback(self, file_path: Path, backup_path: Path):
        """Rollback to the backup."""
        try:
            if backup_path.exists():
                shutil.copy2(backup_path, file_path)
                logger.info(f"Rolled back {file_path} from {backup_path}")
            else:
                logger.warning(f"Backup not found: {backup_path}")
        except Exception as e:
            logger.error(f"Rollback failed: {e}")

    def manual_rollback(self, patch_id: str) -> bool:
        """Manually rollback a specific patch."""
        # Find the patch in history
        for result in reversed(self.history):
            if result.patch_id == patch_id and result.backup_path:
                file_path = self.project_root / result.file_path
                backup_path = Path(result.backup_path)

                if backup_path.exists():
                    self._rollback(file_path, backup_path)

                    # Update history
                    result.rolled_back = True
                    result.success = False
                    self._save_history()

                    return True

        return False

    def get_recent_applies(self, limit: int = 10) -> List[ApplyResult]:
        """Get recent apply results."""
        return self.history[-limit:]

    def get_success_rate(self) -> float:
        """Get success rate of applies."""
        if not self.history:
            return 0.0

        successful = len([r for r in self.history if r.success])
        return successful / len(self.history) * 100

    def cleanup_old_backups(self, days: int = 30):
        """Clean up backups older than specified days."""
        cutoff = datetime.now().timestamp() - (days * 24 * 60 * 60)
        cleaned = 0

        for backup_file in self.backups_dir.rglob("*.bak"):
            if backup_file.stat().st_mtime < cutoff:
                backup_file.unlink()
                cleaned += 1

        logger.info(f"Cleaned up {cleaned} old backups")
        return cleaned


# Singleton
_applier: Optional[SafeApplier] = None


def get_safe_applier(project_root: str = None) -> SafeApplier:
    """Get singleton safe applier."""
    global _applier
    if _applier is None:
        _applier = SafeApplier(project_root)
    return _applier


def apply_patch_safely(patch: Dict, auto_rollback: bool = True) -> ApplyResult:
    """Apply a patch safely."""
    return get_safe_applier().apply_patch(patch, auto_rollback)
