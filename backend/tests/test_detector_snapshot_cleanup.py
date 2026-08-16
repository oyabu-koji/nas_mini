import os

import pytest

from app.services.detector_manifest import DetectorValidationError
from app.services.detector_snapshot_cleanup import (
    MAX_MATCHING_NAMESPACES,
    MAX_NAMESPACE_DEPTH,
    MAX_NAMESPACE_ENTRIES,
    MAX_TEMP_DIRECTORY_ENTRIES,
    STALE_AGE_SECONDS,
    sweep_stale_snapshot_namespaces,
)


def _namespace(root, suffix="0" * 32):
    path = root / f"mediavault-detector-fixtures-{suffix}"
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    snapshot = path / "snapshot.mov"
    snapshot.write_bytes(b"snapshot")
    snapshot.chmod(0o400)
    return path


def test_stale_sweep_removes_only_fixed_namespace_pattern(tmp_path):
    stale = _namespace(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    timestamp = 1_000_000_000
    os.utime(stale, ns=(timestamp, timestamp))

    removed = sweep_stale_snapshot_namespaces(
        temp_root=tmp_path,
        now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
    )

    assert removed == 1
    assert not stale.exists()
    assert unrelated.exists()


@pytest.mark.parametrize(
    ("entry_count", "raises"),
    [(MAX_TEMP_DIRECTORY_ENTRIES, False), (MAX_TEMP_DIRECTORY_ENTRIES + 1, True)],
)
def test_temp_directory_scan_limit_is_maximum_inclusive(
    tmp_path, entry_count, raises
):
    for index in range(entry_count):
        (tmp_path / f"unrelated-{index:04d}").write_bytes(b"")

    if raises:
        with pytest.raises(DetectorValidationError):
            sweep_stale_snapshot_namespaces(temp_root=tmp_path, now_ns=0)
    else:
        assert sweep_stale_snapshot_namespaces(
            temp_root=tmp_path,
            now_ns=max(path.stat().st_mtime_ns for path in tmp_path.iterdir()),
        ) == 0


@pytest.mark.parametrize(
    ("namespace_count", "raises"),
    [(MAX_MATCHING_NAMESPACES, False), (MAX_MATCHING_NAMESPACES + 1, True)],
)
def test_matching_namespace_limit_is_maximum_inclusive(
    tmp_path, namespace_count, raises
):
    for index in range(namespace_count):
        path = tmp_path / f"mediavault-detector-fixtures-{index:032x}"
        path.mkdir(mode=0o700)
        path.chmod(0o700)

    if raises:
        with pytest.raises(DetectorValidationError):
            sweep_stale_snapshot_namespaces(temp_root=tmp_path, now_ns=0)
    else:
        assert sweep_stale_snapshot_namespaces(
            temp_root=tmp_path,
            now_ns=max(path.stat().st_mtime_ns for path in tmp_path.iterdir()),
        ) == 0


@pytest.mark.parametrize(
    ("depth", "raises"),
    [(MAX_NAMESPACE_DEPTH, False), (MAX_NAMESPACE_DEPTH + 1, True)],
)
def test_namespace_tree_depth_limit_is_maximum_inclusive(tmp_path, depth, raises):
    namespace = _namespace(tmp_path)
    (namespace / "snapshot.mov").unlink()
    current = namespace
    for index in range(depth):
        current = current / f"d{index}"
        current.mkdir(mode=0o700)
        current.chmod(0o700)
    timestamp = 1_000_000_000
    os.utime(namespace, ns=(timestamp, timestamp))

    if raises:
        with pytest.raises(DetectorValidationError):
            sweep_stale_snapshot_namespaces(
                temp_root=tmp_path,
                now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
            )
    else:
        assert sweep_stale_snapshot_namespaces(
            temp_root=tmp_path,
            now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
        ) == 1


@pytest.mark.parametrize(
    ("entry_count", "raises"),
    [(MAX_NAMESPACE_ENTRIES, False), (MAX_NAMESPACE_ENTRIES + 1, True)],
)
def test_namespace_tree_entry_limit_is_maximum_inclusive(
    tmp_path, entry_count, raises
):
    namespace = _namespace(tmp_path)
    (namespace / "snapshot.mov").unlink()
    for index in range(entry_count):
        path = namespace / f"snapshot-{index:02d}"
        path.write_bytes(b"")
        path.chmod(0o400)
    timestamp = 1_000_000_000
    os.utime(namespace, ns=(timestamp, timestamp))

    if raises:
        with pytest.raises(DetectorValidationError):
            sweep_stale_snapshot_namespaces(
                temp_root=tmp_path,
                now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
            )
    else:
        assert sweep_stale_snapshot_namespaces(
            temp_root=tmp_path,
            now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
        ) == 1


@pytest.mark.parametrize(
    ("age_seconds", "removed"),
    [(STALE_AGE_SECONDS - 1, 0), (STALE_AGE_SECONDS, 1)],
)
def test_stale_age_threshold_is_exact(tmp_path, age_seconds, removed):
    namespace = _namespace(tmp_path)
    timestamp = 1_000_000_000
    os.utime(namespace, ns=(timestamp, timestamp))

    assert sweep_stale_snapshot_namespaces(
        temp_root=tmp_path,
        now_ns=timestamp + age_seconds * 1_000_000_000,
    ) == removed
    assert namespace.exists() is (removed == 0)


@pytest.mark.parametrize("file_mode", [0o400, 0o600])
def test_stale_tree_accepts_only_documented_directory_and_file_modes(
    tmp_path, file_mode
):
    namespace = _namespace(tmp_path)
    nested = namespace / "nested"
    nested.mkdir(mode=0o700)
    nested.chmod(0o700)
    snapshot = namespace / "snapshot.mov"
    snapshot.chmod(file_mode)
    timestamp = 1_000_000_000
    os.utime(namespace, ns=(timestamp, timestamp))

    assert sweep_stale_snapshot_namespaces(
        temp_root=tmp_path,
        now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
    ) == 1


@pytest.mark.parametrize(
    ("target", "mode"),
    [("directory", 0o755), ("file", 0o644)],
)
def test_stale_tree_rejects_wrong_modes(tmp_path, target, mode):
    namespace = _namespace(tmp_path)
    if target == "directory":
        namespace.chmod(mode)
    else:
        (namespace / "snapshot.mov").chmod(mode)

    with pytest.raises(DetectorValidationError):
        sweep_stale_snapshot_namespaces(temp_root=tmp_path)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "wrong_owner"])
def test_stale_tree_rejects_wrong_owner_symlink_and_special_file(
    tmp_path, monkeypatch, kind
):
    namespace = _namespace(tmp_path)
    snapshot = namespace / "snapshot.mov"
    if kind == "symlink":
        snapshot.unlink()
        snapshot.symlink_to(tmp_path / "outside")
    elif kind == "fifo":
        snapshot.unlink()
        os.mkfifo(snapshot, 0o600)
    else:
        actual_uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: actual_uid + 1)

    with pytest.raises(DetectorValidationError):
        sweep_stale_snapshot_namespaces(temp_root=tmp_path)


def test_stale_delete_revalidates_descriptor_relative_identity(tmp_path, monkeypatch):
    namespace = _namespace(tmp_path)
    snapshot = namespace / "snapshot.mov"
    timestamp = 1_000_000_000
    os.utime(namespace, ns=(timestamp, timestamp))
    real_stat = os.stat
    file_checks = 0

    def replacing_stat(path, *args, **kwargs):
        nonlocal file_checks
        if path == "snapshot.mov" and kwargs.get("dir_fd") is not None:
            file_checks += 1
            if file_checks == 2:
                snapshot.unlink()
                snapshot.write_bytes(b"replacement")
                snapshot.chmod(0o400)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", replacing_stat)

    with pytest.raises(DetectorValidationError):
        sweep_stale_snapshot_namespaces(
            temp_root=tmp_path,
            now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
        )

    assert namespace.exists()


def test_ambiguous_stale_entry_stops_all_certification_cleanup(tmp_path):
    unsafe = _namespace(tmp_path, "0" * 32)
    (unsafe / "snapshot.mov").chmod(0o644)
    safe = _namespace(tmp_path, "1" * 32)
    timestamp = 1_000_000_000
    os.utime(unsafe, ns=(timestamp, timestamp))
    os.utime(safe, ns=(timestamp, timestamp))

    with pytest.raises(DetectorValidationError):
        sweep_stale_snapshot_namespaces(
            temp_root=tmp_path,
            now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
        )

    assert unsafe.exists()
    assert safe.exists()


def test_next_certification_sweep_removes_forced_termination_residue(tmp_path):
    residue = _namespace(tmp_path)
    timestamp = 1_000_000_000
    os.utime(residue, ns=(timestamp, timestamp))

    assert residue.exists()
    assert sweep_stale_snapshot_namespaces(
        temp_root=tmp_path,
        now_ns=timestamp + STALE_AGE_SECONDS * 1_000_000_000,
    ) == 1
    assert not residue.exists()
