import hashlib
import os
import stat
from dataclasses import replace

import pytest

from app.core.settings import Settings
from app.services.lut_snapshot import (
    LutSnapshotError,
    cleanup_lut_snapshot,
    copy_opened_lut_to_snapshot,
    create_lut_snapshot,
    open_lut_source,
    verify_lut_snapshot,
)
from app.services.storage import initialize_storage


def settings_for(tmp_path, root):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="secret",
        database_path=tmp_path / "db.sqlite3",
    )
    initialize_storage(settings.media_root)
    return replace(settings, user_lut_root=root, built_in_preset_root=root)


def source_file(root, content=b"LUT bytes"):
    path = root / "preset" / "look.cube"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return path


def test_snapshot_is_private_exclusive_and_reverified(tmp_path):
    root = tmp_path / "luts"
    content = b"expected LUT bytes"
    source_file(root, content)
    settings = settings_for(tmp_path, root)
    digest = hashlib.sha256(content).hexdigest()

    snapshot = create_lut_snapshot(
        settings=settings,
        rendition_id="a" * 32,
        source_root_kind="custom",
        source_relative_path="preset/look.cube",
        expected_sha256=digest,
    )

    assert snapshot.path.read_bytes() == content
    assert stat.S_IMODE(snapshot.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(snapshot.path.stat().st_mode) == 0o600
    verify_lut_snapshot(snapshot, expected_sha256=digest)
    snapshot.path.write_bytes(b"corrupt")
    with pytest.raises(LutSnapshotError):
        verify_lut_snapshot(snapshot, expected_sha256=digest)
    cleanup_lut_snapshot(settings=settings, rendition_id="a" * 32)
    assert not snapshot.path.parent.exists()


@pytest.mark.parametrize(
    "relative_path",
    ["/preset/look.cube", "../look.cube", "preset//look.cube", "preset/./look.cube", "preset/"],
)
def test_snapshot_rejects_unsafe_components(tmp_path, relative_path):
    root = tmp_path / "luts"
    source_file(root)
    settings = settings_for(tmp_path, root)

    with pytest.raises(LutSnapshotError):
        create_lut_snapshot(
            settings=settings,
            rendition_id="a" * 32,
            source_root_kind="custom",
            source_relative_path=relative_path,
            expected_sha256="a" * 64,
        )


def test_snapshot_rejects_symlink_nonregular_unknown_root_and_digest_mismatch(tmp_path):
    root = tmp_path / "luts"
    real = source_file(root)
    (root / "preset" / "linked.cube").symlink_to(real)
    (root / "preset" / "directory.cube").mkdir()
    settings = settings_for(tmp_path, root)
    cases = (
        ("custom", "preset/linked.cube"),
        ("custom", "preset/directory.cube"),
        ("request-selected-root", "preset/look.cube"),
    )

    for kind, path in cases:
        with pytest.raises(LutSnapshotError):
            create_lut_snapshot(
                settings=settings,
                rendition_id="a" * 32,
                source_root_kind=kind,
                source_relative_path=path,
                expected_sha256=hashlib.sha256(real.read_bytes()).hexdigest(),
            )
    with pytest.raises(LutSnapshotError):
        create_lut_snapshot(
            settings=settings,
            rendition_id="b" * 32,
            source_root_kind="custom",
            source_relative_path="preset/look.cube",
            expected_sha256="0" * 64,
        )
    assert not (settings.media_root / "jobs" / ("b" * 32)).exists()


def test_open_descriptor_uses_expected_inode_after_directory_entry_replacement(tmp_path):
    root = tmp_path / "luts"
    original = b"original expected bytes"
    path = source_file(root, original)
    settings = settings_for(tmp_path, root)
    opened = open_lut_source(
        settings=settings,
        source_root_kind="custom",
        source_relative_path="preset/look.cube",
    )
    moved = path.with_suffix(".old")
    path.rename(moved)
    path.write_bytes(b"replacement bytes")

    with opened:
        snapshot = copy_opened_lut_to_snapshot(
            settings=settings,
            rendition_id="a" * 32,
            source=opened,
            expected_sha256=hashlib.sha256(original).hexdigest(),
        )

    assert snapshot.path.read_bytes() == original


def test_replacement_before_open_and_root_symlink_fail_closed(tmp_path):
    root = tmp_path / "luts"
    path = source_file(root)
    real = path.with_suffix(".real")
    path.rename(real)
    path.symlink_to(real)
    settings = settings_for(tmp_path, root)

    with pytest.raises(LutSnapshotError):
        open_lut_source(
            settings=settings,
            source_root_kind="custom",
            source_relative_path="preset/look.cube",
        )

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root, target_is_directory=True)
    linked_settings = settings_for(tmp_path / "other", linked_root)
    with pytest.raises(LutSnapshotError):
        open_lut_source(
            settings=linked_settings,
            source_root_kind="custom",
            source_relative_path="preset/look.cube",
        )


def test_snapshot_cleanup_unlinks_job_symlink_without_deleting_target(tmp_path):
    root = tmp_path / "luts"
    root.mkdir()
    settings = settings_for(tmp_path, root)
    victim = settings.media_root / "originals" / "must-survive"
    victim.mkdir()
    original = victim / "clip.mov"
    original.write_bytes(b"original")
    rendition_id = "a" * 32
    job_link = settings.media_root / "jobs" / rendition_id
    job_link.symlink_to(victim, target_is_directory=True)

    cleanup_lut_snapshot(settings=settings, rendition_id=rendition_id)

    assert original.read_bytes() == b"original"
    assert victim.is_dir()
    assert not job_link.exists()
    assert not job_link.is_symlink()
