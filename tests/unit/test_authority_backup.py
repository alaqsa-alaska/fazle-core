"""Focused temporary-file tests for the writable authority backup."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules import backup


def _valid(path: Path, value: str = "approved") -> None:
    path.write_text(json.dumps({
        "version": 1,
        "status_vocabulary": ["current_approved", "historical", "unverified", "conflicting", "superseded"],
        "facts": [{"key": "x.fact", "subject": "x", "status": "current_approved", "value": value}],
    }), encoding="utf-8")


def test_valid_authority_backup_matches_and_restores(tmp_path):
    source = tmp_path / "source.json"
    backup_dir = tmp_path / "backups"
    target = tmp_path / "restored.json"
    _valid(source)

    result = backup.backup_authority_register(source, backup_dir)
    assert result["status"] == "ok"
    backup_path = Path(result["backup"])
    assert backup_path.parent == backup_dir
    assert backup_path.read_bytes() == source.read_bytes()
    assert oct(backup_path.stat().st_mode & 0o777) == "0o600"
    assert backup.validate_authority_backup(backup_path, source)["status"] == "ok"

    target.write_text("old", encoding="utf-8")
    restored = backup.restore_authority_register(backup_path, target)
    assert restored["status"] == "ok"
    assert target.read_bytes() == source.read_bytes()
    assert source.read_bytes() == json.dumps({
        "version": 1,
        "status_vocabulary": ["current_approved", "historical", "unverified", "conflicting", "superseded"],
        "facts": [{"key": "x.fact", "subject": "x", "status": "current_approved", "value": "approved"}],
    }).encode()


def test_malformed_authority_blocks_backup(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        backup.backup_authority_register(source, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()


def test_mismatched_or_invalid_backup_cannot_validate_or_restore(tmp_path):
    source = tmp_path / "source.json"
    backup_path = tmp_path / "backup.json"
    _valid(source)
    _valid(backup_path, "different")
    with pytest.raises(ValueError):
        backup.validate_authority_backup(backup_path, source)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        backup.restore_authority_register(invalid, tmp_path / "target.json")
    assert not (tmp_path / "target.json").exists()


def test_backup_is_outside_live_knowledge_tree(tmp_path):
    source = tmp_path / "knowledge_base" / "00_governance" / "recruitment_fact_authority.json"
    source.parent.mkdir(parents=True)
    _valid(source)
    result = backup.backup_authority_register(source, tmp_path / "backups" / "authority")
    assert Path(result["backup"]).parent != source.parent
