from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

ParserResultKind = Literal[
    "recognized_logs",
    "no_logs",
    "unknown_logs",
    "conflicting_logs",
    "unsupported_container",
    "invalid",
    "resource_limit",
]
PARSER_RESULT_KINDS = frozenset(
    {
        "recognized_logs",
        "no_logs",
        "unknown_logs",
        "conflicting_logs",
        "unsupported_container",
        "invalid",
        "resource_limit",
    }
)
SourceProfile = Literal["apple-log-1", "apple-log-2"]
SOURCE_PROFILES = frozenset({"apple-log-1", "apple-log-2"})
SignalKind = Literal[
    "apple-log-1-logs",
    "apple-log-2-logs",
    "no-logs",
    "unknown-logs",
    "conflicting-logs",
    "track-unresolved",
    "unsupported-container",
]
SIGNAL_KINDS = frozenset(
    {
        "apple-log-1-logs",
        "apple-log-2-logs",
        "no-logs",
        "unknown-logs",
        "conflicting-logs",
        "track-unresolved",
        "unsupported-container",
    }
)
TrackResolution = Literal["matched", "unresolved", "not_applicable"]
MAX_FILE_SIZE = 1_099_511_627_776
MAX_BOX_HEADERS = 65_536
MAX_NESTING_DEPTH = 12
MAX_VIDEO_TRACKS = 8
MAX_SAMPLE_DESCRIPTIONS = 32
MAX_METADATA_BYTES = 1_048_576
MAX_RETAINED_IDENTIFIERS = 16
MAX_UINT64 = (1 << 64) - 1


@dataclass(frozen=True)
class BoxHeader:
    type: bytes
    start: int
    header_size: int
    payload_start: int
    end: int


@dataclass
class ParseBudget:
    box_headers: int = 0
    max_depth_seen: int = 0
    video_tracks: int = 0
    sample_descriptions: int = 0
    bytes_read: int = 0
    retained_identifiers: int = 0


@dataclass(frozen=True)
class ContainerSignal:
    kind: ParserResultKind
    source_profile: SourceProfile | None
    track_id: int | None
    track_resolution: TrackResolution
    signal_kind: SignalKind | None
    box_headers: int
    max_depth_seen: int
    metadata_bytes_read: int


@dataclass(frozen=True)
class _StsdAnalysis:
    resolved: bool
    unsupported_layout: bool
    recognized_profiles: frozenset[SourceProfile]
    has_unknown_identifier: bool


@dataclass(frozen=True)
class _TrackAnalysis:
    track_id: int | None
    handler_type: bytes | None
    stsd: _StsdAnalysis | None


class _InvalidContainer(Exception):
    pass


class _ResourceLimit(Exception):
    pass


class _UnsupportedContainer(Exception):
    pass


@dataclass
class _BoundedReader:
    fd: int
    file_size: int
    budget: ParseBudget

    def __post_init__(self) -> None:
        if (
            not isinstance(self.fd, int)
            or isinstance(self.fd, bool)
            or self.fd < 0
            or not isinstance(self.file_size, int)
            or isinstance(self.file_size, bool)
            or self.file_size < 0
        ):
            raise _InvalidContainer()
        if self.file_size > MAX_FILE_SIZE:
            raise _ResourceLimit()

    def read_exact(self, offset: int, length: int) -> bytes:
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length < 0
            or offset > self.file_size
            or length > self.file_size - offset
        ):
            raise _InvalidContainer()
        if length > MAX_METADATA_BYTES - self.budget.bytes_read:
            raise _ResourceLimit()
        self.budget.bytes_read += length
        try:
            value = os.pread(self.fd, length, offset)
        except OSError as exc:
            raise _InvalidContainer() from exc
        if len(value) != length:
            raise _InvalidContainer()
        return value


def _checked_add(offset: int, length: int, *, parent_end: int) -> int:
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(length, int)
        or isinstance(length, bool)
        or length < 0
        or not isinstance(parent_end, int)
        or isinstance(parent_end, bool)
        or parent_end < 0
        or offset > parent_end
        or offset > MAX_UINT64
        or length > MAX_UINT64 - offset
        or length > parent_end - offset
    ):
        raise _InvalidContainer()
    return offset + length


def _read_box_header(
    reader: _BoundedReader,
    *,
    start: int,
    parent_end: int,
    current_depth: int,
    top_level: bool,
) -> BoxHeader:
    _record_depth(reader.budget, current_depth)
    _consume_box_header(reader.budget)
    if start > parent_end or parent_end - start < 8:
        raise _InvalidContainer()
    basic = reader.read_exact(start, 8)
    size32 = int.from_bytes(basic[:4], byteorder="big", signed=False)
    box_type = basic[4:8]
    header_size = 8
    if size32 == 1:
        if parent_end - start < 16:
            raise _InvalidContainer()
        extended = reader.read_exact(start + 8, 8)
        declared_size = int.from_bytes(extended, byteorder="big", signed=False)
        header_size = 16
    elif size32 == 0:
        if not top_level:
            raise _InvalidContainer()
        declared_size = parent_end - start
    else:
        declared_size = size32
    if declared_size < header_size:
        raise _InvalidContainer()
    end = _checked_add(start, declared_size, parent_end=parent_end)
    return BoxHeader(
        type=box_type,
        start=start,
        header_size=header_size,
        payload_start=start + header_size,
        end=end,
    )


def _record_depth(budget: ParseBudget, current_depth: int) -> None:
    if (
        not isinstance(current_depth, int)
        or isinstance(current_depth, bool)
        or current_depth < 1
    ):
        raise _InvalidContainer()
    if current_depth > MAX_NESTING_DEPTH:
        raise _ResourceLimit()
    budget.max_depth_seen = max(budget.max_depth_seen, current_depth)


def _consume_box_header(budget: ParseBudget) -> None:
    if budget.box_headers >= MAX_BOX_HEADERS:
        raise _ResourceLimit()
    budget.box_headers += 1


def _iter_box_headers(
    reader: _BoundedReader,
    *,
    start: int,
    end: int,
    current_depth: int,
    top_level: bool,
):
    offset = start
    while offset < end:
        header = _read_box_header(
            reader,
            start=offset,
            parent_end=end,
            current_depth=current_depth,
            top_level=top_level,
        )
        if header.end <= offset:
            raise _InvalidContainer()
        yield header
        offset = header.end
    if offset != end:
        raise _InvalidContainer()


def _walk_box_tree(
    reader: _BoundedReader,
    *,
    start: int,
    end: int,
    current_depth: int,
    container_types: frozenset[bytes],
):
    for header in _iter_box_headers(
        reader,
        start=start,
        end=end,
        current_depth=current_depth,
        top_level=current_depth == 1,
    ):
        yield header
        if header.type in container_types:
            yield from _walk_box_tree(
                reader,
                start=header.payload_start,
                end=header.end,
                current_depth=current_depth + 1,
                container_types=container_types,
            )


def parse_apple_log_signal(
    fd: int,
    file_size: int,
    selected_track_id: int | None,
) -> ContainerSignal:
    budget = ParseBudget()
    try:
        reader = _BoundedReader(fd=fd, file_size=file_size, budget=budget)
        top_level = _parse_top_level(reader)
        moov = next(header for header in top_level if header.type == b"moov")
        return _parse_moov(
            reader,
            moov,
            selected_track_id=selected_track_id,
        )
    except _ResourceLimit:
        return _signal(
            budget,
            kind="resource_limit",
            track_resolution="not_applicable",
            signal_kind=None,
        )
    except _InvalidContainer:
        return _signal(
            budget,
            kind="invalid",
            track_resolution="not_applicable",
            signal_kind=None,
        )
    except _UnsupportedContainer:
        return _signal(
            budget,
            kind="unsupported_container",
            track_resolution="not_applicable",
            signal_kind="unsupported-container",
        )


def _parse_top_level(reader: _BoundedReader) -> tuple[BoxHeader, ...]:
    if reader.file_size < 8:
        raise _InvalidContainer()
    first_bytes = reader.read_exact(0, 8)
    if first_bytes[4:8] != b"ftyp":
        raise _UnsupportedContainer()
    first = _read_box_header(
        reader,
        start=0,
        parent_end=reader.file_size,
        current_depth=1,
        top_level=True,
    )
    _validate_ftyp(reader, first)
    headers = [first]
    headers.extend(
        _iter_box_headers(
            reader,
            start=first.end,
            end=reader.file_size,
            current_depth=1,
            top_level=True,
        )
    )
    if sum(header.type == b"moov" for header in headers) != 1:
        raise _InvalidContainer()
    return tuple(headers)


def _validate_ftyp(reader: _BoundedReader, header: BoxHeader) -> None:
    if header.type != b"ftyp" or header.end - header.payload_start < 8:
        raise _InvalidContainer()
    major_brand = reader.read_exact(header.payload_start, 4)
    if any(value < 0x20 or value > 0x7E for value in major_brand):
        raise _InvalidContainer()


def _parse_moov(
    reader: _BoundedReader,
    moov: BoxHeader,
    *,
    selected_track_id: int | None,
) -> ContainerSignal:
    children = tuple(
        _iter_box_headers(
            reader,
            start=moov.payload_start,
            end=moov.end,
            current_depth=2,
            top_level=False,
        )
    )
    tracks = tuple(
        _parse_track(
            reader,
            child,
            selected_track_id=selected_track_id,
        )
        for child in children
        if child.type == b"trak"
    )
    if (
        not isinstance(selected_track_id, int)
        or isinstance(selected_track_id, bool)
        or selected_track_id <= 0
        or selected_track_id > 0xFFFF_FFFF
    ):
        return _unresolved_signal(reader.budget)
    resolved_ids = [track.track_id for track in tracks if track.track_id is not None]
    if len(resolved_ids) != len(set(resolved_ids)):
        return _unresolved_signal(reader.budget)
    matches = [track for track in tracks if track.track_id == selected_track_id]
    if len(matches) != 1:
        return _unresolved_signal(reader.budget)
    target = matches[0]
    if (
        target.handler_type != b"vide"
        or target.stsd is None
        or not target.stsd.resolved
    ):
        return _unresolved_signal(reader.budget)
    if target.stsd.unsupported_layout:
        return _signal(
            reader.budget,
            kind="unsupported_container",
            track_resolution="not_applicable",
            signal_kind="unsupported-container",
        )
    profiles = target.stsd.recognized_profiles
    if len(profiles) > 1 or (profiles and target.stsd.has_unknown_identifier):
        return _signal(
            reader.budget,
            kind="conflicting_logs",
            track_resolution="matched",
            signal_kind="conflicting-logs",
            track_id=selected_track_id,
        )
    if profiles:
        profile = next(iter(profiles))
        return _signal(
            reader.budget,
            kind="recognized_logs",
            track_resolution="matched",
            signal_kind=(
                "apple-log-1-logs" if profile == "apple-log-1" else "apple-log-2-logs"
            ),
            source_profile=profile,
            track_id=selected_track_id,
        )
    if target.stsd.has_unknown_identifier:
        return _signal(
            reader.budget,
            kind="unknown_logs",
            track_resolution="matched",
            signal_kind="unknown-logs",
            track_id=selected_track_id,
        )
    return _signal(
        reader.budget,
        kind="no_logs",
        track_resolution="matched",
        signal_kind="no-logs",
        track_id=selected_track_id,
    )


def _parse_track(
    reader: _BoundedReader,
    track: BoxHeader,
    *,
    selected_track_id: int | None,
) -> _TrackAnalysis:
    children = tuple(
        _iter_box_headers(
            reader,
            start=track.payload_start,
            end=track.end,
            current_depth=3,
            top_level=False,
        )
    )
    tkhd_headers = tuple(child for child in children if child.type == b"tkhd")
    parsed_track_ids = tuple(_parse_tkhd(reader, header) for header in tkhd_headers)
    track_id = parsed_track_ids[0] if len(parsed_track_ids) == 1 else None
    mdia_headers = tuple(child for child in children if child.type == b"mdia")
    mdia_results = tuple(
        _parse_mdia(
            reader,
            header,
            collect_signals=(
                len(mdia_headers) == 1
                and track_id is not None
                and track_id == selected_track_id
            ),
        )
        for header in mdia_headers
    )
    if len(mdia_results) != 1:
        return _TrackAnalysis(track_id=track_id, handler_type=None, stsd=None)
    handler_type, stsd = mdia_results[0]
    return _TrackAnalysis(
        track_id=track_id,
        handler_type=handler_type,
        stsd=stsd,
    )


def _parse_tkhd(reader: _BoundedReader, header: BoxHeader) -> int | None:
    payload_length = header.end - header.payload_start
    if payload_length < 4:
        raise _InvalidContainer()
    fullbox = reader.read_exact(header.payload_start, 4)
    version = fullbox[0]
    if version == 0:
        minimum_payload = 84
        track_id_offset = 12
    elif version == 1:
        minimum_payload = 96
        track_id_offset = 20
    else:
        return None
    if payload_length < minimum_payload:
        raise _InvalidContainer()
    track_id = int.from_bytes(
        reader.read_exact(header.payload_start + track_id_offset, 4),
        byteorder="big",
        signed=False,
    )
    return track_id or None


def _parse_mdia(
    reader: _BoundedReader,
    mdia: BoxHeader,
    *,
    collect_signals: bool,
) -> tuple[bytes | None, _StsdAnalysis | None]:
    children = tuple(
        _iter_box_headers(
            reader,
            start=mdia.payload_start,
            end=mdia.end,
            current_depth=4,
            top_level=False,
        )
    )
    hdlr_headers = tuple(child for child in children if child.type == b"hdlr")
    parsed_handlers = tuple(_parse_hdlr(reader, header) for header in hdlr_headers)
    handler_type = parsed_handlers[0] if len(parsed_handlers) == 1 else None
    if handler_type != b"vide":
        return handler_type, None
    _consume_video_track(reader.budget)
    minf_headers = tuple(child for child in children if child.type == b"minf")
    parsed_stsd = tuple(
        _parse_minf(
            reader,
            header,
            collect_signals=collect_signals and len(minf_headers) == 1,
        )
        for header in minf_headers
    )
    return handler_type, parsed_stsd[0] if len(parsed_stsd) == 1 else None


def _parse_hdlr(reader: _BoundedReader, header: BoxHeader) -> bytes | None:
    payload_length = header.end - header.payload_start
    if payload_length < 4:
        raise _InvalidContainer()
    fullbox = reader.read_exact(header.payload_start, 4)
    if fullbox != b"\x00\x00\x00\x00":
        return None
    if payload_length < 24:
        raise _InvalidContainer()
    return reader.read_exact(header.payload_start + 8, 4)


def _parse_minf(
    reader: _BoundedReader,
    minf: BoxHeader,
    *,
    collect_signals: bool,
) -> _StsdAnalysis | None:
    children = tuple(
        _iter_box_headers(
            reader,
            start=minf.payload_start,
            end=minf.end,
            current_depth=5,
            top_level=False,
        )
    )
    stbl_headers = tuple(child for child in children if child.type == b"stbl")
    parsed = tuple(
        _parse_stbl(
            reader,
            header,
            collect_signals=collect_signals and len(stbl_headers) == 1,
        )
        for header in stbl_headers
    )
    return parsed[0] if len(parsed) == 1 else None


def _parse_stbl(
    reader: _BoundedReader,
    stbl: BoxHeader,
    *,
    collect_signals: bool,
) -> _StsdAnalysis | None:
    children = tuple(
        _iter_box_headers(
            reader,
            start=stbl.payload_start,
            end=stbl.end,
            current_depth=6,
            top_level=False,
        )
    )
    stsd_headers = tuple(child for child in children if child.type == b"stsd")
    parsed = tuple(
        _parse_stsd(
            reader,
            header,
            collect_signals=collect_signals and len(stsd_headers) == 1,
        )
        for header in stsd_headers
    )
    return parsed[0] if len(parsed) == 1 else None


def _parse_stsd(
    reader: _BoundedReader,
    stsd: BoxHeader,
    *,
    collect_signals: bool,
) -> _StsdAnalysis:
    payload_length = stsd.end - stsd.payload_start
    if payload_length < 4:
        raise _InvalidContainer()
    fullbox = reader.read_exact(stsd.payload_start, 4)
    if fullbox != b"\x00\x00\x00\x00":
        return _StsdAnalysis(False, False, frozenset(), False)
    if payload_length < 8:
        raise _InvalidContainer()
    entry_count = int.from_bytes(
        reader.read_exact(stsd.payload_start + 4, 4),
        byteorder="big",
        signed=False,
    )
    offset = stsd.payload_start + 8
    profiles: set[SourceProfile] = set()
    has_unknown = False
    unsupported_layout = False
    for _index in range(entry_count):
        _consume_sample_description(reader.budget)
        if offset >= stsd.end:
            raise _InvalidContainer()
        entry = _read_box_header(
            reader,
            start=offset,
            parent_end=stsd.end,
            current_depth=7,
            top_level=False,
        )
        entry_profiles, entry_unknown, entry_unsupported = _parse_visual_entry(
            reader,
            entry,
            collect_signals=collect_signals,
        )
        profiles.update(entry_profiles)
        has_unknown = has_unknown or entry_unknown
        unsupported_layout = unsupported_layout or entry_unsupported
        offset = entry.end
    if offset != stsd.end:
        raise _InvalidContainer()
    return _StsdAnalysis(
        resolved=True,
        unsupported_layout=unsupported_layout,
        recognized_profiles=frozenset(profiles),
        has_unknown_identifier=has_unknown,
    )


ISO_VISUAL_SAMPLE_ENTRY_TYPES = frozenset(
    {
        b"avc1",
        b"avc2",
        b"avc3",
        b"avc4",
        b"hvc1",
        b"hev1",
        b"dvhe",
        b"dvh1",
        b"av01",
        b"vp08",
        b"vp09",
        b"apch",
        b"apcn",
        b"apcs",
        b"apco",
        b"ap4h",
        b"ap4x",
        b"jpeg",
        b"mjpg",
    }
)
VISUAL_SAMPLE_ENTRY_CHILD_OFFSET = 86
APPLE_LOG_IDENTIFIERS = {
    b"com.apple.rec2020.apple-log": "apple-log-1",
    b"com.apple.apple-wide-gamut.apple-log": "apple-log-2",
}


def _parse_visual_entry(
    reader: _BoundedReader,
    entry: BoxHeader,
    *,
    collect_signals: bool,
) -> tuple[set[SourceProfile], bool, bool]:
    if entry.type not in ISO_VISUAL_SAMPLE_ENTRY_TYPES:
        return set(), False, collect_signals
    if (
        entry.header_size != 8
        or entry.end - entry.start < VISUAL_SAMPLE_ENTRY_CHILD_OFFSET
    ):
        raise _InvalidContainer()
    profiles: set[SourceProfile] = set()
    has_unknown = False
    for child in _iter_visual_entry_children(reader, entry=entry):
        if child.type != b"logs" or not collect_signals:
            continue
        profile = _parse_logs_payload(reader, child)
        if profile is None:
            has_unknown = True
        else:
            profiles.add(profile)
    return profiles, has_unknown, False


def _iter_visual_entry_children(reader: _BoundedReader, *, entry: BoxHeader):
    offset = entry.start + VISUAL_SAMPLE_ENTRY_CHILD_OFFSET
    while entry.end - offset >= 8:
        child = _read_box_header(
            reader,
            start=offset,
            parent_end=entry.end,
            current_depth=8,
            top_level=False,
        )
        yield child
        offset = child.end
    trailing_length = entry.end - offset
    if trailing_length and reader.read_exact(offset, trailing_length) != (
        b"\x00" * trailing_length
    ):
        raise _InvalidContainer()


def _parse_logs_payload(
    reader: _BoundedReader,
    logs: BoxHeader,
) -> SourceProfile | None:
    payload_length = logs.end - logs.payload_start
    if payload_length < 1 or payload_length > 128:
        raise _InvalidContainer()
    if reader.budget.retained_identifiers >= MAX_RETAINED_IDENTIFIERS:
        raise _ResourceLimit()
    reader.budget.retained_identifiers += 1
    payload = reader.read_exact(logs.payload_start, payload_length)
    if b"\x00" in payload:
        raise _InvalidContainer()
    try:
        payload.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise _InvalidContainer() from exc
    return APPLE_LOG_IDENTIFIERS.get(payload)


def _consume_video_track(budget: ParseBudget) -> None:
    if budget.video_tracks >= MAX_VIDEO_TRACKS:
        raise _ResourceLimit()
    budget.video_tracks += 1


def _consume_sample_description(budget: ParseBudget) -> None:
    if budget.sample_descriptions >= MAX_SAMPLE_DESCRIPTIONS:
        raise _ResourceLimit()
    budget.sample_descriptions += 1


def _unresolved_signal(budget: ParseBudget) -> ContainerSignal:
    return _signal(
        budget,
        kind="unknown_logs",
        track_resolution="unresolved",
        signal_kind="track-unresolved",
    )


def _signal(
    budget: ParseBudget,
    *,
    kind: ParserResultKind,
    track_resolution: TrackResolution,
    signal_kind: SignalKind | None,
    source_profile: SourceProfile | None = None,
    track_id: int | None = None,
) -> ContainerSignal:
    return ContainerSignal(
        kind=kind,
        source_profile=source_profile,
        track_id=track_id,
        track_resolution=track_resolution,
        signal_kind=signal_kind,
        box_headers=budget.box_headers,
        max_depth_seen=budget.max_depth_seen,
        metadata_bytes_read=budget.bytes_read,
    )
