import os
from pathlib import Path

import pytest
from app.services import detector_inspection, detector_source
from app.services.apple_log_detector import ProbeSignal
from app.services.bounded_subprocess import BoundedProcessError, BoundedProcessResult
from app.services.detector_inspection import (
    InspectionResult,
    detect_path_same_fd,
    inspect_fixture_path,
    inspect_opened_fixture,
    parse_inspection,
    serialize_inspection,
)
from app.services.iso_bmff_log_parser import ContainerSignal


def _inspection():
    return InspectionResult(
        container=ContainerSignal(
            kind="recognized_logs",
            source_profile="apple-log-2",
            track_id=7,
            track_resolution="matched",
            signal_kind="apple-log-2-logs",
            box_headers=12,
            max_depth_seen=8,
            metadata_bytes_read=128,
        ),
        probe=ProbeSignal(
            index=0,
            track_id_status="valid",
            track_id=7,
            codec_type="video",
            color_space="bt2020nc",
            color_transfer=None,
            color_primaries=None,
        ),
    )


@pytest.mark.parametrize(
    ("platform_name", "descriptor_root"),
    [("linux", "/proc/self/fd"), ("darwin", "/dev/fd")],
)
def test_inspector_passes_same_platform_descriptor_to_ffprobe_and_parser(
    tmp_path, monkeypatch, platform_name, descriptor_root
):
    fixture = tmp_path / "snapshot.mov"
    fixture.write_bytes(b"snapshot")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["pass_fds"] = kwargs["pass_fds"]
        return BoundedProcessResult(
            stdout=b'{"streams":[{"index":0,"id":"0x7"}]}',
            stderr=b"",
            returncode=0,
        )

    def fake_parse(descriptor, file_size, selected_track_id):
        captured["parser"] = (descriptor, file_size, selected_track_id)
        return _inspection().container

    monkeypatch.setattr(detector_inspection, "run_bounded_process", fake_run)
    monkeypatch.setattr(detector_inspection, "parse_apple_log_signal", fake_parse)
    monkeypatch.setattr(detector_source.sys, "platform", platform_name)
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda candidate: candidate.as_posix() == descriptor_root,
    )

    descriptor = os.open(fixture, os.O_RDONLY)
    try:
        result = inspect_opened_fixture(descriptor)
    finally:
        os.close(descriptor)

    assert result.container.source_profile == "apple-log-2"
    assert captured["pass_fds"] == (descriptor,)
    assert captured["argv"][-1] == f"{descriptor_root}/{descriptor}"
    assert captured["parser"] == (descriptor, len(b"snapshot"), 7)


def test_same_fd_detection_requires_caller_verified_size(tmp_path, monkeypatch):
    fixture = tmp_path / "snapshot.mov"
    fixture.write_bytes(b"snapshot")
    monkeypatch.setattr(
        detector_inspection,
        "inspect_opened_fixture",
        lambda *_args, **_kwargs: pytest.fail("inspection must not run"),
    )

    with pytest.raises(BoundedProcessError) as raised:
        detect_path_same_fd(
            fixture,
            ffprobe_binary="ffprobe",
            expected_size=len(b"snapshot") + 1,
            manifest=object(),
        )

    assert raised.value.code == "log_container_source_changed"


@pytest.mark.parametrize("failure_stage", ["ffprobe", "parser"])
def test_source_change_overrides_inner_inspection_error(
    tmp_path, monkeypatch, failure_stage
):
    fixture = tmp_path / "snapshot.mov"
    fixture.write_bytes(b"snapshot")
    monkeypatch.setattr(
        detector_inspection,
        "resolve_descriptor_path",
        lambda descriptor: Path("/dev/fd") / str(descriptor),
    )

    def fake_run(_argv, **_kwargs):
        if failure_stage == "ffprobe":
            fixture.write_bytes(b"changed-snapshot")
            raise BoundedProcessError("log_probe_failed")
        return BoundedProcessResult(
            stdout=b'{"streams":[{"index":0,"id":"0x7"}]}',
            stderr=b"",
            returncode=0,
        )

    def fake_parse(*_args):
        fixture.write_bytes(b"changed-snapshot")
        raise RuntimeError("parser failed")

    monkeypatch.setattr(detector_inspection, "run_bounded_process", fake_run)
    monkeypatch.setattr(detector_inspection, "parse_apple_log_signal", fake_parse)

    with pytest.raises(BoundedProcessError) as raised:
        detect_path_same_fd(
            fixture,
            ffprobe_binary="ffprobe",
            expected_size=len(b"snapshot"),
            manifest=object(),
        )

    assert raised.value.code == "log_container_source_changed"


def test_unchanged_source_preserves_ffprobe_error(tmp_path, monkeypatch):
    fixture = tmp_path / "snapshot.mov"
    fixture.write_bytes(b"snapshot")
    monkeypatch.setattr(
        detector_inspection,
        "resolve_descriptor_path",
        lambda descriptor: Path("/dev/fd") / str(descriptor),
    )
    monkeypatch.setattr(
        detector_inspection,
        "run_bounded_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BoundedProcessError("log_probe_failed")
        ),
    )

    with pytest.raises(BoundedProcessError) as raised:
        detect_path_same_fd(
            fixture,
            ffprobe_binary="ffprobe",
            expected_size=len(b"snapshot"),
            manifest=object(),
        )

    assert raised.value.code == "log_probe_failed"


def test_unchanged_source_preserves_parser_error(tmp_path, monkeypatch):
    fixture = tmp_path / "snapshot.mov"
    fixture.write_bytes(b"snapshot")
    parser_error = RuntimeError("parser failed")
    monkeypatch.setattr(
        detector_inspection,
        "resolve_descriptor_path",
        lambda descriptor: Path("/dev/fd") / str(descriptor),
    )
    monkeypatch.setattr(
        detector_inspection,
        "run_bounded_process",
        lambda *_args, **_kwargs: BoundedProcessResult(
            stdout=b'{"streams":[{"index":0,"id":"0x7"}]}',
            stderr=b"",
            returncode=0,
        ),
    )
    monkeypatch.setattr(
        detector_inspection,
        "parse_apple_log_signal",
        lambda *_args: (_ for _ in ()).throw(parser_error),
    )

    with pytest.raises(RuntimeError) as raised:
        detect_path_same_fd(
            fixture,
            ffprobe_binary="ffprobe",
            expected_size=len(b"snapshot"),
            manifest=object(),
        )

    assert raised.value is parser_error


def test_inspector_opens_snapshot_exactly_once_with_no_follow(tmp_path, monkeypatch):
    fixture = tmp_path / "snapshot.mov"
    fixture.write_bytes(b"snapshot")
    real_open = os.open
    opened = []

    def recording_open(path, flags):
        opened.append((path, flags))
        return real_open(path, flags)

    monkeypatch.setattr(detector_inspection.os, "open", recording_open)
    monkeypatch.setattr(
        detector_inspection,
        "inspect_opened_fixture",
        lambda _descriptor, **_kwargs: _inspection(),
    )

    assert inspect_fixture_path(fixture) == _inspection()
    assert len(opened) == 1
    assert opened[0][1] & getattr(os, "O_NOFOLLOW", 0)


def test_inspector_output_is_path_free_bounded_canonical_strict_json(tmp_path):
    raw = serialize_inspection(_inspection())

    assert len(raw) <= 4_096
    assert str(tmp_path).encode() not in raw
    assert b"snapshot.mov" not in raw
    assert parse_inspection(raw) == _inspection()

    with pytest.raises(BoundedProcessError):
        parse_inspection(raw + b"\n")
    with pytest.raises(BoundedProcessError):
        parse_inspection(raw.replace(b'{"container":', b'{"unknown":1,"container":'))
