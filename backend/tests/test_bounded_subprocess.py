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
