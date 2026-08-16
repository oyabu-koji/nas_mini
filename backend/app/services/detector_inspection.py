from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.apple_log_detector import (
    DetectionResult,
    ProbeSignal,
    classify_detection,
    parse_probe_signal,
)
from app.services.bounded_subprocess import (
    BoundedProcessError,
    run_bounded_process,
)
from app.services.detector_manifest import (
    DETECTOR_MAX_STDERR_BYTES,
    DETECTOR_MAX_STDOUT_BYTES,
    DETECTOR_PROBE_TIMEOUT_MS,
    FFPROBE_PROBE_ARGUMENTS,
    canonical_document,
)
from app.services.detector_source import (
    ContainerDetectionError,
    DetectorSource,
    resolve_descriptor_path,
)
from app.services.iso_bmff_log_parser import (
    PARSER_RESULT_KINDS,
    SIGNAL_KINDS,
    SOURCE_PROFILES,
    ContainerSignal,
    parse_apple_log_signal,
)

INSPECTION_SCHEMA_VERSION = 1
INSPECTION_MAX_BYTES = 4_096
INSPECTION_FIELDS = frozenset({"schema_version", "container", "probe"})
CONTAINER_FIELDS = frozenset(
    {
        "kind",
        "source_profile",
        "track_id",
        "track_resolution",
        "signal_kind",
        "box_headers",
        "max_depth_seen",
        "metadata_bytes_read",
    }
)
PROBE_FIELDS = frozenset(
    {
        "index",
        "track_id_status",
        "track_id",
        "codec_type",
        "color_space",
        "color_transfer",
        "color_primaries",
    }
)
TRACK_RESOLUTIONS = frozenset({"matched", "unresolved", "not_applicable"})


@dataclass(frozen=True)
class InspectionResult:
    container: ContainerSignal
    probe: ProbeSignal


def inspect_fixture_path(
    path: Path, *, ffprobe_binary: str = "ffprobe"
) -> InspectionResult:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        return inspect_opened_fixture(descriptor, ffprobe_binary=ffprobe_binary)
    except OSError as exc:
        raise BoundedProcessError("log_probe_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def detect_path_same_fd(
    path: Path,
    *,
    ffprobe_binary: str,
    expected_size: int,
    manifest,
) -> DetectionResult:
    try:
        with DetectorSource(path, expected_size=expected_size) as source:
            inspection = inspect_opened_fixture(
                source.fd,
                ffprobe_binary=ffprobe_binary,
            )
            return classify_detection(
                container=inspection.container,
                probe=inspection.probe,
                manifest=manifest,
            )
    except ContainerDetectionError as exc:
        raise BoundedProcessError(exc.code) from exc


def inspect_opened_fixture(
    descriptor: int, *, ffprobe_binary: str = "ffprobe"
) -> InspectionResult:
    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise BoundedProcessError("log_probe_failed") from exc
    if not stat.S_ISREG(before.st_mode):
        raise BoundedProcessError("log_probe_failed")

    try:
        descriptor_path = resolve_descriptor_path(descriptor)
    except ContainerDetectionError as exc:
        raise BoundedProcessError(exc.code) from exc

    probe_result = run_bounded_process(
        [
            ffprobe_binary,
            *FFPROBE_PROBE_ARGUMENTS,
            str(descriptor_path),
        ],
        timeout_ms=DETECTOR_PROBE_TIMEOUT_MS,
        max_stdout_bytes=DETECTOR_MAX_STDOUT_BYTES,
        max_stderr_bytes=DETECTOR_MAX_STDERR_BYTES,
        pass_fds=(descriptor,),
    )
    probe = parse_probe_signal(probe_result.stdout)
    selected_track_id = probe.track_id if probe.track_id_status == "valid" else None
    container = parse_apple_log_signal(
        descriptor,
        before.st_size,
        selected_track_id,
    )
    try:
        after = os.fstat(descriptor)
    except OSError as exc:
        raise BoundedProcessError("log_container_source_changed") from exc
    if _file_identity(before) != _file_identity(after):
        raise BoundedProcessError("log_container_source_changed")
    return InspectionResult(container=container, probe=probe)


def serialize_inspection(result: InspectionResult) -> bytes:
    raw = canonical_document(
        {
            "schema_version": INSPECTION_SCHEMA_VERSION,
            "container": {
                "kind": result.container.kind,
                "source_profile": result.container.source_profile,
                "track_id": result.container.track_id,
                "track_resolution": result.container.track_resolution,
                "signal_kind": result.container.signal_kind,
                "box_headers": result.container.box_headers,
                "max_depth_seen": result.container.max_depth_seen,
                "metadata_bytes_read": result.container.metadata_bytes_read,
            },
            "probe": {
                "index": result.probe.index,
                "track_id_status": result.probe.track_id_status,
                "track_id": result.probe.track_id,
                "codec_type": result.probe.codec_type,
                "color_space": result.probe.color_space,
                "color_transfer": result.probe.color_transfer,
                "color_primaries": result.probe.color_primaries,
            },
        }
    )
    if len(raw) > INSPECTION_MAX_BYTES:
        raise BoundedProcessError("log_probe_output_invalid")
    return raw


def parse_inspection(raw: bytes) -> InspectionResult:
    if not raw or len(raw) > INSPECTION_MAX_BYTES:
        raise BoundedProcessError("log_probe_output_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BoundedProcessError("log_probe_output_invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != INSPECTION_FIELDS
        or value["schema_version"] != INSPECTION_SCHEMA_VERSION
        or serialize_inspection(_parse_value(value)) != raw
    ):
        raise BoundedProcessError("log_probe_output_invalid")
    return _parse_value(value)


def _parse_value(value: dict[str, Any]) -> InspectionResult:
    container_value = value.get("container")
    probe_value = value.get("probe")
    if (
        not isinstance(container_value, dict)
        or set(container_value) != CONTAINER_FIELDS
        or not isinstance(probe_value, dict)
        or set(probe_value) != PROBE_FIELDS
    ):
        raise BoundedProcessError("log_probe_output_invalid")
    kind = container_value["kind"]
    source_profile = container_value["source_profile"]
    track_id = _optional_uint32(container_value["track_id"])
    track_resolution = container_value["track_resolution"]
    signal_kind = container_value["signal_kind"]
    if (
        kind not in PARSER_RESULT_KINDS
        or source_profile not in {*SOURCE_PROFILES, None}
        or track_resolution not in TRACK_RESOLUTIONS
        or signal_kind not in {*SIGNAL_KINDS, None}
    ):
        raise BoundedProcessError("log_probe_output_invalid")
    container = ContainerSignal(
        kind=kind,
        source_profile=source_profile,
        track_id=track_id,
        track_resolution=track_resolution,
        signal_kind=signal_kind,
        box_headers=_bounded_count(container_value["box_headers"]),
        max_depth_seen=_bounded_count(container_value["max_depth_seen"]),
        metadata_bytes_read=_bounded_count(container_value["metadata_bytes_read"]),
    )

    track_id_status = probe_value["track_id_status"]
    probe_track_id = _optional_uint32(probe_value["track_id"])
    if track_id_status not in {"valid", "unresolved"} or (
        (track_id_status == "valid") != (probe_track_id is not None)
    ):
        raise BoundedProcessError("log_probe_output_invalid")
    index = probe_value["index"]
    if type(index) is not int or index < 0:
        raise BoundedProcessError("log_probe_output_invalid")
    codec_type = _optional_text(probe_value["codec_type"])
    if codec_type not in {None, "video"}:
        raise BoundedProcessError("log_probe_output_invalid")
    probe = ProbeSignal(
        index=index,
        track_id_status=track_id_status,
        track_id=probe_track_id,
        codec_type=codec_type,
        color_space=_optional_text(probe_value["color_space"]),
        color_transfer=_optional_text(probe_value["color_transfer"]),
        color_primaries=_optional_text(probe_value["color_primaries"]),
    )
    return InspectionResult(container=container, probe=probe)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _optional_uint32(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0 or value > 0xFFFF_FFFF:
        raise BoundedProcessError("log_probe_output_invalid")
    return value


def _bounded_count(value: Any) -> int:
    if type(value) is not int or value < 0 or value > 1_099_511_627_776:
        raise BoundedProcessError("log_probe_output_invalid")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64 or "\x00" in value:
        raise BoundedProcessError("log_probe_output_invalid")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate inspection field")
        result[key] = value
    return result
