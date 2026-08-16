import os
import sys

import pytest
from app.services.bounded_subprocess import run_bounded_process
from app.services.detector_source import (
    ContainerDetectionError,
    DetectorSource,
    FileIdentity,
    linux_descriptor_path,
    macos_descriptor_path,
    resolve_descriptor_path,
)


def test_file_identity_is_an_immutable_exact_snapshot():
    identity = FileIdentity(device=1, inode=2, size=3, mtime_ns=4)

    assert identity == FileIdentity(device=1, inode=2, size=3, mtime_ns=4)
    with pytest.raises((AttributeError, TypeError)):
        identity.size = 5


def test_detector_source_context_manager_owns_descriptor_lifetime(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")
    source = DetectorSource(path=source_path, expected_size=len(b"source"))

    with source as opened:
        descriptor = opened.fd
        assert opened is source
        assert os.fstat(descriptor).st_size == len(b"source")

    with pytest.raises(OSError):
        os.fstat(descriptor)
    with pytest.raises(RuntimeError):
        _ = source.fd


def test_detector_source_opens_source_read_only(tmp_path, monkeypatch):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")
    original_open = os.open
    captured_flags = []

    def capture_open(path, flags, *args, **kwargs):
        captured_flags.append(flags)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", capture_open)

    with DetectorSource(path=source_path, expected_size=len(b"source")):
        pass

    assert captured_flags == [os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)]


def test_detector_source_rejects_symlink_when_platform_supports_no_follow(tmp_path):
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW is unavailable")
    real_source = tmp_path / "real.mov"
    real_source.write_bytes(b"source")
    source_link = tmp_path / "source.mov"
    source_link.symlink_to(real_source)

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_link, expected_size=len(b"source")):
            pass

    assert getattr(raised.value, "code", None) == "log_container_source_changed"


def test_detector_source_requires_opened_regular_file(tmp_path):
    source_directory = tmp_path / "source.mov"
    source_directory.mkdir()

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_directory, expected_size=0):
            pass

    assert raised.value.code == "log_container_source_changed"


def test_detector_source_requires_verified_original_size(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source") + 1):
            pass

    assert raised.value.code == "log_container_source_changed"


def test_detector_source_matches_lstat_before_and_after_open_to_fstat(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")
    replacement = tmp_path / "replacement.mov"
    replacement.write_bytes(b"source")
    original_open = os.open

    def replace_after_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        os.replace(replacement, source_path)
        return descriptor

    monkeypatch.setattr(os, "open", replace_after_open)

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source")):
            pass

    assert raised.value.code == "log_container_source_changed"


def test_detector_source_revalidates_descriptor_identity_after_detection(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source")):
            source_path.write_bytes(b"changed-source")

    assert raised.value.code == "log_container_source_changed"


def test_detector_source_revalidates_path_identity_after_detection(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")
    replacement = tmp_path / "replacement.mov"
    replacement.write_bytes(b"source")

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source")):
            source_path.rename(tmp_path / "opened-source.mov")
            replacement.rename(source_path)

    assert raised.value.code == "log_container_source_changed"


def test_linux_descriptor_path_uses_proc_self_fd():
    assert linux_descriptor_path(17).as_posix() == "/proc/self/fd/17"
    with pytest.raises(ContainerDetectionError) as raised:
        linux_descriptor_path(-1)
    assert raised.value.code == "log_probe_failed"


def test_macos_descriptor_path_uses_dev_fd():
    assert macos_descriptor_path(23).as_posix() == "/dev/fd/23"
    with pytest.raises(ContainerDetectionError) as raised:
        macos_descriptor_path(False)
    assert raised.value.code == "log_probe_failed"


def test_unsupported_descriptor_path_is_a_safe_failure():
    with pytest.raises(ContainerDetectionError) as raised:
        resolve_descriptor_path(17, platform_name="unsupported")

    assert raised.value.code == "log_probe_failed"
    assert str(raised.value) == "log_probe_failed"


def test_parser_random_access_does_not_share_or_change_descriptor_offset(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"0123456789")

    with DetectorSource(path=source_path, expected_size=10) as source:
        os.lseek(source.fd, 7, os.SEEK_SET)
        assert source.read_at(2, 4) == b"2345"
        assert os.lseek(source.fd, 0, os.SEEK_CUR) == 7


def test_child_input_remains_opened_inode_after_path_replacement(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"opened-inode")
    replacement = tmp_path / "replacement.mov"
    replacement.write_bytes(b"replacement")
    child_result = None

    with pytest.raises(ContainerDetectionError):
        with DetectorSource(
            path=source_path,
            expected_size=len(b"opened-inode"),
        ) as source:
            source_path.rename(tmp_path / "original.mov")
            replacement.rename(source_path)
            child_result = run_bounded_process(
                [
                    sys.executable,
                    "-c",
                    "import os,sys; print(os.read(int(sys.argv[1]), 64).decode())",
                    str(source.fd),
                ],
                timeout_ms=1000,
                max_stdout_bytes=128,
                max_stderr_bytes=256,
                pass_fds=(source.fd,),
            )

    assert child_result is not None
    assert child_result.stdout == b"opened-inode\n"


def test_source_rename_maps_to_source_changed(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source")):
            source_path.rename(tmp_path / "renamed.mov")

    assert raised.value.code == "log_container_source_changed"


def test_source_symlink_replacement_maps_to_source_changed(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source")):
            opened_path = tmp_path / "opened.mov"
            source_path.rename(opened_path)
            source_path.symlink_to(opened_path)

    assert raised.value.code == "log_container_source_changed"


@pytest.mark.parametrize("mutation", ["size", "mtime"])
def test_source_size_or_mtime_change_maps_to_source_changed(tmp_path, mutation):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=source_path, expected_size=len(b"source")):
            if mutation == "size":
                source_path.write_bytes(b"source-changed")
            else:
                metadata = source_path.stat()
                os.utime(
                    source_path,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
                )

    assert raised.value.code == "log_container_source_changed"


def test_detector_source_public_error_contains_no_path_detail(tmp_path):
    private_path = tmp_path / "private-user-recording.mov"

    with pytest.raises(ContainerDetectionError) as raised:
        with DetectorSource(path=private_path, expected_size=123):
            pass

    public_error = str(raised.value)
    assert public_error == "log_container_source_changed"
    assert str(tmp_path) not in public_error
    assert private_path.name not in public_error
