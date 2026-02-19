"""
NEXUS Data Backup & Restore
============================

Creates timestamped compressed backups of the brain data directory.
Supports restore from any backup archive.

Usage:
    from core.backup import create_backup, restore_backup, list_backups

    path = create_backup()          # Returns path to backup .tar.gz
    list_backups()                   # Returns list of available backups
    restore_backup("2026-02-19_143000.tar.gz")  # Restore from backup

Environment variables:
    NEXUS_BACKUP_DIR  – directory for backups (default: data/backups)
    NEXUS_MAX_BACKUPS – max backups to keep (default: 10, 0 = unlimited)
"""

import os
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.nexus_logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "brain"
BACKUP_DIR = Path(os.getenv("NEXUS_BACKUP_DIR", str(PROJECT_ROOT / "data" / "backups")))
MAX_BACKUPS = int(os.getenv("NEXUS_MAX_BACKUPS", "10"))


def create_backup(tag: str = "") -> Dict:
    """Create a compressed backup of the brain data directory.

    Returns:
        Dict with keys: path, size_bytes, files_count, timestamp
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = f"nexus_backup_{timestamp}"
    if tag:
        name += f"_{tag}"
    archive_path = BACKUP_DIR / f"{name}.tar.gz"

    if not DATA_DIR.exists():
        logger.warning("Data directory %s does not exist, nothing to backup", DATA_DIR)
        return {"error": "no_data_dir", "path": str(DATA_DIR)}

    files_count = 0
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in DATA_DIR.rglob("*"):
            if item.is_file() and item.suffix in (".json", ".jsonl", ".log", ".txt"):
                tar.add(item, arcname=item.relative_to(DATA_DIR))
                files_count += 1

    size_bytes = archive_path.stat().st_size
    logger.info("Backup created: %s (%d files, %d bytes)", archive_path.name, files_count, size_bytes)

    # Prune old backups
    if MAX_BACKUPS > 0:
        _prune_old_backups()

    return {
        "path": str(archive_path),
        "size_bytes": size_bytes,
        "files_count": files_count,
        "timestamp": timestamp,
    }


def restore_backup(backup_name: str) -> Dict:
    """Restore brain data from a backup archive.

    Args:
        backup_name: Name of the backup file (e.g. 'nexus_backup_2026-02-19_143000.tar.gz')

    Returns:
        Dict with keys: restored_files, timestamp
    """
    archive_path = BACKUP_DIR / backup_name
    if not archive_path.exists():
        return {"error": f"Backup not found: {backup_name}"}

    # Safety: create a pre-restore backup first
    pre_restore = create_backup(tag="pre_restore")
    logger.info("Pre-restore backup: %s", pre_restore.get("path", "none"))

    restored = 0
    with tarfile.open(archive_path, "r:gz") as tar:
        # Security: prevent path traversal
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                logger.warning("Skipping suspicious path in archive: %s", member.name)
                continue
            tar.extract(member, path=DATA_DIR)
            restored += 1

    logger.info("Restored %d files from %s", restored, backup_name)
    return {
        "restored_files": restored,
        "timestamp": datetime.now().isoformat(),
        "source": backup_name,
    }


def list_backups() -> List[Dict]:
    """List available backups sorted by date (newest first)."""
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for f in sorted(BACKUP_DIR.glob("nexus_backup_*.tar.gz"), reverse=True):
        backups.append({
            "name": f.name,
            "size_bytes": f.stat().st_size,
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return backups


def _prune_old_backups() -> None:
    """Keep only the N most recent backups."""
    backups = sorted(BACKUP_DIR.glob("nexus_backup_*.tar.gz"), reverse=True)
    for old in backups[MAX_BACKUPS:]:
        old.unlink()
        logger.info("Pruned old backup: %s", old.name)
