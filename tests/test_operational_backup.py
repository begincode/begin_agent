import json

import pytest

from scripts.audit_objects import inventory
from scripts.backup_restore import BackupError, digest, restore_name, verify_bundle


def bundle(tmp_path):
    root = tmp_path / "backup"
    root.mkdir()
    (root / "database.dump").write_bytes(b"dump")
    (root / "credential.key").write_bytes(b"key")
    (root / "manifest.json").write_text(
        json.dumps({"files": {p.name: digest(p) for p in root.iterdir()}})
    )
    return root


def test_backup_manifest_detects_corruption_and_missing_files(tmp_path):
    root = bundle(tmp_path)
    assert verify_bundle(root)
    (root / "database.dump").write_bytes(b"corrupt")
    with pytest.raises(BackupError, match="完整性"):
        verify_bundle(root)
    (root / "database.dump").unlink()
    with pytest.raises(BackupError, match="缺失"):
        verify_bundle(root)


@pytest.mark.parametrize(
    "target",
    [
        "postgres",
        "begin_agent",
        "../begin_agent_restore_x",
        "begin_agent_restore_;DROP DATABASE x",
        "begin_agent_restore_",
    ],
)
def test_restore_rejects_existing_service_or_unsafe_database_names(target):
    with pytest.raises(BackupError):
        restore_name(target)
    assert restore_name("begin_agent_restore_acceptance") == "begin_agent_restore_acceptance"


def test_manifest_cannot_escape_or_follow_links(tmp_path):
    root = bundle(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("secret")
    (root / "database.dump").unlink()
    (root / "database.dump").symlink_to(secret)
    with pytest.raises(BackupError, match="链接"):
        verify_bundle(root)
    (root / "manifest.json").write_text(json.dumps({"files": {"../secret": digest(secret)}}))
    with pytest.raises(BackupError, match="路径"):
        verify_bundle(root)


def test_inventory_reports_unknown_files_and_links_without_deleting(tmp_path):
    root = tmp_path / "objects"
    root.mkdir()
    (root / "orphan").write_text("untouched")
    (root / "link").symlink_to(root / "orphan")
    files, invalid, truncated = inventory(root)
    assert files == {"orphan"} and invalid == ["link"] and not truncated
    assert (root / "orphan").read_text() == "untouched"
