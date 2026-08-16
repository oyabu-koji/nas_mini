import os
import sys
import time

import pytest

from app.services.bounded_subprocess import BoundedProcessError, run_bounded_process


def test_bounded_process_returns_small_output():
    result = run_bounded_process(
        [sys.executable, "-c", "print('ok')"],
        timeout_ms=1000,
        max_stdout_bytes=64,
        max_stderr_bytes=64,
    )

    assert result.stdout == b"ok\n"


def test_bounded_process_accepts_empty_optional_pass_fds():
    result = run_bounded_process(
        [sys.executable, "-c", "print('ok')"],
        timeout_ms=1000,
        max_stdout_bytes=64,
        max_stderr_bytes=64,
        pass_fds=(),
    )

    assert result.stdout == b"ok\n"


def test_bounded_process_inherits_only_explicit_descriptors(tmp_path):
    inherited_path = tmp_path / "inherited"
    blocked_path = tmp_path / "blocked"
    inherited_path.write_bytes(b"inherited")
    blocked_path.write_bytes(b"blocked")
    inherited_fd = os.open(inherited_path, os.O_RDONLY)
    blocked_fd = os.open(blocked_path, os.O_RDONLY)
    script = (
        "import os,sys; "
        "result=[]; "
        "\nfor value in sys.argv[1:]:"
        "\n try: os.fstat(int(value)); result.append('open')"
        "\n except OSError: result.append('closed')"
        "\nprint(' '.join(result))"
    )
    try:
        result = run_bounded_process(
            [sys.executable, "-c", script, str(inherited_fd), str(blocked_fd)],
            timeout_ms=1000,
            max_stdout_bytes=64,
            max_stderr_bytes=256,
            pass_fds=(inherited_fd,),
        )
    finally:
        os.close(inherited_fd)
        os.close(blocked_fd)

    assert result.stdout == b"open closed\n"


def test_inherited_descriptor_points_to_expected_inode(tmp_path):
    source_path = tmp_path / "source.mov"
    source_path.write_bytes(b"source")
    descriptor = os.open(source_path, os.O_RDONLY)
    expected = os.fstat(descriptor)
    script = (
        "import os,sys; metadata=os.fstat(int(sys.argv[1])); "
        "print(f'{metadata.st_dev}:{metadata.st_ino}')"
    )
    try:
        result = run_bounded_process(
            [sys.executable, "-c", script, str(descriptor)],
            timeout_ms=1000,
            max_stdout_bytes=128,
            max_stderr_bytes=256,
            pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)

    assert result.stdout.decode("ascii").strip() == (
        f"{expected.st_dev}:{expected.st_ino}"
    )


@pytest.mark.parametrize(
    ("script", "code"),
    [
        ("print('x' * 1000)", "log_probe_output_invalid"),
        ("import sys; sys.stderr.write('x' * 1000)", "log_probe_output_invalid"),
        ("import time; time.sleep(1)", "log_probe_timeout"),
        ("raise SystemExit(2)", "log_probe_failed"),
    ],
)
def test_bounded_process_maps_resource_and_process_failures(script, code):
    with pytest.raises(BoundedProcessError) as raised:
        run_bounded_process(
            [sys.executable, "-c", script],
            timeout_ms=50,
            max_stdout_bytes=64,
            max_stderr_bytes=64,
        )

    assert raised.value.code == code


def test_cleanup_failure_is_stable_error():
    with pytest.raises(BoundedProcessError) as raised:
        run_bounded_process(
            [sys.executable, "-c", "raise SystemExit(2)"],
            timeout_ms=1000,
            max_stdout_bytes=64,
            max_stderr_bytes=64,
            cleanup=lambda: False,
        )

    assert raised.value.code == "log_probe_failed"


def test_interrupt_terminates_process_and_runs_cleanup(monkeypatch):
    cleanup_calls = []
    original_sleep = time.sleep
    calls = 0

    def interrupting_sleep(seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        original_sleep(seconds)

    monkeypatch.setattr(
        "app.services.bounded_subprocess.time.sleep", interrupting_sleep
    )

    with pytest.raises(KeyboardInterrupt):
        run_bounded_process(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_ms=1000,
            max_stdout_bytes=64,
            max_stderr_bytes=64,
            cleanup=lambda: cleanup_calls.append(True) is None,
        )

    assert cleanup_calls == [True]
