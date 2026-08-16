from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


CleanupCallback = Callable[[], bool]


class BoundedProcessError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BoundedProcessResult:
    stdout: bytes
    stderr: bytes
    returncode: int


def run_bounded_process(
    argv: Sequence[str],
    *,
    timeout_ms: int,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    cwd: Path | None = None,
    cleanup: CleanupCallback | None = None,
    pass_fds: tuple[int, ...] = (),
    env: Mapping[str, str] | None = None,
) -> BoundedProcessResult:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must contain non-empty strings")
    if timeout_ms <= 0 or max_stdout_bytes < 0 or max_stderr_bytes < 0:
        raise ValueError("process limits must be positive")
    if any(
        not isinstance(descriptor, int)
        or isinstance(descriptor, bool)
        or descriptor < 0
        for descriptor in pass_fds
    ) or len(set(pass_fds)) != len(pass_fds):
        raise ValueError("pass_fds must contain unique non-negative integers")

    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        shell=False,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        pass_fds=pass_fds,
        env=env,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stdout = bytearray()
    stderr = bytearray()
    exceeded = threading.Event()
    reader_failed = threading.Event()

    def read_bounded(stream, target: bytearray, limit: int) -> None:
        try:
            while True:
                remaining = limit + 1 - len(target)
                if remaining <= 0:
                    exceeded.set()
                    return
                chunk = stream.read(min(65_536, remaining))
                if not chunk:
                    return
                target.extend(chunk)
                if len(target) > limit:
                    exceeded.set()
                    return
        except Exception:
            reader_failed.set()

    readers = (
        threading.Thread(
            target=read_bounded,
            args=(process.stdout, stdout, max_stdout_bytes),
            daemon=True,
        ),
        threading.Thread(
            target=read_bounded,
            args=(process.stderr, stderr, max_stderr_bytes),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()

    failure_code: str | None = None
    try:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while process.poll() is None:
            if exceeded.is_set():
                failure_code = "log_probe_output_invalid"
                break
            if reader_failed.is_set():
                failure_code = "log_probe_failed"
                break
            if time.monotonic() >= deadline:
                failure_code = "log_probe_timeout"
                break
            time.sleep(0.01)

        if failure_code is not None:
            _terminate_process_group(process)

        for reader in readers:
            reader.join(timeout=1)
        if process.poll() is None:
            _terminate_process_group(process)
        try:
            returncode = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            returncode = process.wait(timeout=1)
            failure_code = failure_code or "log_probe_failed"
    except BaseException as exc:
        _terminate_process_group(process)
        for reader in readers:
            reader.join(timeout=1)
        if cleanup is not None and not _run_cleanup(cleanup):
            raise BoundedProcessError("log_probe_failed") from exc
        raise

    if cleanup is not None and (failure_code is not None or returncode != 0):
        if not _run_cleanup(cleanup):
            raise BoundedProcessError("log_probe_failed")

    if failure_code is not None:
        raise BoundedProcessError(failure_code)
    if exceeded.is_set():
        raise BoundedProcessError("log_probe_output_invalid")
    if reader_failed.is_set() or returncode != 0:
        raise BoundedProcessError("log_probe_failed")

    return BoundedProcessResult(
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        returncode=returncode,
    )


def _run_cleanup(cleanup: CleanupCallback) -> bool:
    try:
        return cleanup()
    except Exception:
        return False


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.terminate()
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError:
            pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
