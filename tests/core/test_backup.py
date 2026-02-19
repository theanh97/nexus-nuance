"""Tests for data backup & restore module."""

import json
import tarfile

import pytest

import src.core.backup as _mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate data and backup dirs."""
    data_dir = tmp_path / "data" / "brain"
    data_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(_mod, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(_mod, "MAX_BACKUPS", 3)
    return data_dir, backup_dir


class TestCreateBackup:
    def test_creates_archive(self, tmp_path, _isolate):
        data_dir, _ = _isolate
        (data_dir / "test.json").write_text('{"a":1}', encoding="utf-8")
        result = _mod.create_backup()
        assert "path" in result
        assert result["files_count"] == 1
        assert result["size_bytes"] > 0

    def test_empty_data_dir(self, _isolate):
        result = _mod.create_backup()
        assert result["files_count"] == 0

    def test_skips_non_data_files(self, _isolate):
        data_dir, _ = _isolate
        (data_dir / "image.png").write_bytes(b"\x89PNG")
        (data_dir / "data.json").write_text("{}", encoding="utf-8")
        result = _mod.create_backup()
        assert result["files_count"] == 1  # only json

    def test_tag_in_filename(self, _isolate):
        data_dir, _ = _isolate
        (data_dir / "x.json").write_text("{}", encoding="utf-8")
        result = _mod.create_backup(tag="before_deploy")
        assert "before_deploy" in result["path"]

    def test_missing_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "DATA_DIR", tmp_path / "nonexistent")
        result = _mod.create_backup()
        assert "error" in result


class TestListBackups:
    def test_no_backups(self, _isolate):
        assert _mod.list_backups() == []

    def test_lists_after_create(self, _isolate):
        data_dir, _ = _isolate
        (data_dir / "x.json").write_text("{}", encoding="utf-8")
        _mod.create_backup()
        backups = _mod.list_backups()
        assert len(backups) == 1
        assert "name" in backups[0]
        assert "size_bytes" in backups[0]


class TestRestoreBackup:
    def test_restore_recreates_files(self, _isolate):
        data_dir, _ = _isolate
        (data_dir / "important.json").write_text('{"key":"value"}', encoding="utf-8")
        backup = _mod.create_backup()
        backup_name = backup["path"].split("/")[-1]

        # Delete original
        (data_dir / "important.json").unlink()
        assert not (data_dir / "important.json").exists()

        # Restore
        result = _mod.restore_backup(backup_name)
        assert result["restored_files"] >= 1
        assert (data_dir / "important.json").exists()
        assert json.loads((data_dir / "important.json").read_text(encoding="utf-8"))["key"] == "value"

    def test_restore_nonexistent(self, _isolate):
        result = _mod.restore_backup("nonexistent.tar.gz")
        assert "error" in result


class TestPruning:
    def test_prunes_old_backups(self, _isolate):
        data_dir, backup_dir = _isolate
        (data_dir / "x.json").write_text("{}", encoding="utf-8")
        for _ in range(5):
            _mod.create_backup()
        backups = _mod.list_backups()
        assert len(backups) <= 3  # MAX_BACKUPS = 3
