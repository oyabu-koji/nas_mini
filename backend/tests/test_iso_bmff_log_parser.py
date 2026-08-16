import os
import random
from typing import get_args

import pytest
from app.services import iso_bmff_log_parser
from app.services.iso_bmff_log_parser import (
    ISO_VISUAL_SAMPLE_ENTRY_TYPES,
    MAX_BOX_HEADERS,
    MAX_FILE_SIZE,
    MAX_METADATA_BYTES,
    MAX_NESTING_DEPTH,
    MAX_RETAINED_IDENTIFIERS,
    MAX_SAMPLE_DESCRIPTIONS,
    MAX_VIDEO_TRACKS,
    PARSER_RESULT_KINDS,
    SIGNAL_KINDS,
    SOURCE_PROFILES,
    VISUAL_SAMPLE_ENTRY_CHILD_OFFSET,
    BoxHeader,
    ParseBudget,
    ParserResultKind,
    SignalKind,
    SourceProfile,
    _BoundedReader,
    _checked_add,
    _InvalidContainer,
    _parse_top_level,
    _read_box_header,
    _record_depth,
    _ResourceLimit,
    _walk_box_tree,
    parse_apple_log_signal,
)


def test_parser_result_profile_and_signal_types_are_closed():
    assert set(get_args(ParserResultKind)) == PARSER_RESULT_KINDS
    assert (
        set(get_args(SourceProfile))
        == SOURCE_PROFILES
        == {
            "apple-log-1",
            "apple-log-2",
        }
    )
    assert set(get_args(SignalKind)) == SIGNAL_KINDS


def test_box_header_is_an_immutable_half_open_range():
    header = BoxHeader(
        type=b"ftyp",
        start=0,
        header_size=8,
        payload_start=8,
        end=24,
    )

    assert header.payload_start - header.start == header.header_size
    assert header.end - header.start == 24
    with pytest.raises((AttributeError, TypeError)):
        header.end = 25


def test_parse_budget_and_hard_limits_are_fixed():
    assert ParseBudget() == ParseBudget(
        box_headers=0,
        max_depth_seen=0,
        video_tracks=0,
        sample_descriptions=0,
        bytes_read=0,
        retained_identifiers=0,
    )
    assert {
        "file_size": MAX_FILE_SIZE,
        "box_headers": MAX_BOX_HEADERS,
        "nesting_depth": MAX_NESTING_DEPTH,
        "video_tracks": MAX_VIDEO_TRACKS,
        "sample_descriptions": MAX_SAMPLE_DESCRIPTIONS,
        "metadata_bytes": MAX_METADATA_BYTES,
        "retained_identifiers": MAX_RETAINED_IDENTIFIERS,
    } == {
        "file_size": 1_099_511_627_776,
        "box_headers": 65_536,
        "nesting_depth": 12,
        "video_tracks": 8,
        "sample_descriptions": 32,
        "metadata_bytes": 1_048_576,
        "retained_identifiers": 16,
    }


def test_read_exact_is_bounded_and_does_not_change_shared_offset(tmp_path):
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"0123456789")
    descriptor = os.open(source_path, os.O_RDONLY)
    budget = ParseBudget()
    reader = _BoundedReader(fd=descriptor, file_size=10, budget=budget)
    try:
        os.lseek(descriptor, 9, os.SEEK_SET)
        assert reader.read_exact(2, 4) == b"2345"
        assert budget.bytes_read == 4
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 9
        with pytest.raises(_InvalidContainer):
            reader.read_exact(9, 2)
        budget.bytes_read = MAX_METADATA_BYTES
        with pytest.raises(_ResourceLimit):
            reader.read_exact(0, 1)
    finally:
        os.close(descriptor)


def test_checked_offset_addition_stays_inside_parent_range():
    assert _checked_add(8, 16, parent_end=24) == 24
    with pytest.raises(_InvalidContainer):
        _checked_add(8, 17, parent_end=24)
    with pytest.raises(_InvalidContainer):
        _checked_add((1 << 64) - 1, 1, parent_end=1 << 64)


def test_unsigned_32_bit_box_header_is_parsed(tmp_path):
    raw = (12).to_bytes(4, "big") + b"ftyp" + b"isom"
    source_path = tmp_path / "box.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    budget = ParseBudget()
    try:
        header = _read_box_header(
            _BoundedReader(fd=descriptor, file_size=len(raw), budget=budget),
            start=0,
            parent_end=len(raw),
            current_depth=1,
            top_level=True,
        )
    finally:
        os.close(descriptor)

    assert header == BoxHeader(
        type=b"ftyp",
        start=0,
        header_size=8,
        payload_start=8,
        end=12,
    )


def test_unsigned_64_bit_extended_box_header_is_parsed(tmp_path):
    raw = (1).to_bytes(4, "big") + b"moov" + (20).to_bytes(8, "big") + b"body"
    source_path = tmp_path / "extended-box.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        header = _read_box_header(
            _BoundedReader(
                fd=descriptor,
                file_size=len(raw),
                budget=ParseBudget(),
            ),
            start=0,
            parent_end=len(raw),
            current_depth=1,
            top_level=True,
        )
    finally:
        os.close(descriptor)

    assert header.header_size == 16
    assert header.payload_start == 16
    assert header.end == 20


def test_top_level_zero_size_box_expands_to_eof(tmp_path):
    raw = (0).to_bytes(4, "big") + b"mdat" + b"payload"
    source_path = tmp_path / "zero-box.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        header = _read_box_header(
            _BoundedReader(
                fd=descriptor,
                file_size=len(raw),
                budget=ParseBudget(),
            ),
            start=0,
            parent_end=len(raw),
            current_depth=1,
            top_level=True,
        )
    finally:
        os.close(descriptor)

    assert header.end == len(raw)


def test_nested_zero_size_box_is_invalid(tmp_path):
    raw = (0).to_bytes(4, "big") + b"free"
    source_path = tmp_path / "nested-zero-box.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        with pytest.raises(_InvalidContainer):
            _read_box_header(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=ParseBudget(),
                ),
                start=0,
                parent_end=len(raw),
                current_depth=2,
                top_level=False,
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("length", [0, 1, 7])
def test_short_eight_byte_header_is_invalid(tmp_path, length):
    raw = b"\x00" * length
    source_path = tmp_path / f"short-{length}.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        with pytest.raises(_InvalidContainer):
            _read_box_header(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=ParseBudget(),
                ),
                start=0,
                parent_end=len(raw),
                current_depth=1,
                top_level=True,
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("length", [8, 9, 15])
def test_short_sixteen_byte_extended_header_is_invalid(tmp_path, length):
    raw = ((1).to_bytes(4, "big") + b"moov" + b"\x00" * 8)[:length]
    source_path = tmp_path / f"short-extended-{length}.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        with pytest.raises(_InvalidContainer):
            _read_box_header(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=ParseBudget(),
                ),
                start=0,
                parent_end=len(raw),
                current_depth=1,
                top_level=True,
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "raw",
    [
        (7).to_bytes(4, "big") + b"free",
        (1).to_bytes(4, "big") + b"free" + (15).to_bytes(8, "big"),
    ],
)
def test_declared_box_size_smaller_than_header_is_invalid(tmp_path, raw):
    source_path = tmp_path / "undersized-header.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        with pytest.raises(_InvalidContainer):
            _read_box_header(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=ParseBudget(),
                ),
                start=0,
                parent_end=len(raw),
                current_depth=1,
                top_level=True,
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("parent_end", [12, 15])
def test_box_end_beyond_parent_or_file_is_invalid(tmp_path, parent_end):
    raw = (16).to_bytes(4, "big") + b"free" + b"payload!"
    source_path = tmp_path / "out-of-parent.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        with pytest.raises(_InvalidContainer):
            _read_box_header(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=ParseBudget(),
                ),
                start=0,
                parent_end=parent_end,
                current_depth=1,
                top_level=True,
            )
    finally:
        os.close(descriptor)


def test_box_header_count_limit_is_maximum_inclusive(tmp_path):
    raw = (8).to_bytes(4, "big") + b"free"
    source_path = tmp_path / "header-limit.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    budget = ParseBudget(box_headers=MAX_BOX_HEADERS - 1)
    reader = _BoundedReader(fd=descriptor, file_size=len(raw), budget=budget)
    try:
        _read_box_header(
            reader,
            start=0,
            parent_end=len(raw),
            current_depth=1,
            top_level=True,
        )
        assert budget.box_headers == MAX_BOX_HEADERS
        with pytest.raises(_ResourceLimit):
            _read_box_header(
                reader,
                start=0,
                parent_end=len(raw),
                current_depth=1,
                top_level=True,
            )
    finally:
        os.close(descriptor)


def _box(box_type, payload=b""):
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _tkhd(track_id, *, version=0):
    payload_size = 84 if version == 0 else 96
    payload = bytearray(payload_size)
    payload[0] = version
    track_id_offset = 12 if version == 0 else 20
    payload[track_id_offset : track_id_offset + 4] = track_id.to_bytes(4, "big")
    return _box(b"tkhd", bytes(payload))


def _hdlr(handler_type=b"vide", *, version=0, flags=0):
    payload = bytearray(24)
    payload[0] = version
    payload[1:4] = flags.to_bytes(3, "big")
    payload[8:12] = handler_type
    return _box(b"hdlr", bytes(payload))


def _visual_entry(entry_type=b"avc1", children=b""):
    return _box(entry_type, b"\x00" * 78 + children)


def _stsd(entries, *, version=0, flags=0, declared_count=None):
    count = len(entries) if declared_count is None else declared_count
    fullbox = bytes([version]) + flags.to_bytes(3, "big")
    return _box(b"stsd", fullbox + count.to_bytes(4, "big") + b"".join(entries))


def _track(track_id, entries, *, handler_type=b"vide", version=0):
    sample_table = _box(b"stbl", _stsd(entries))
    media = _box(
        b"mdia",
        _hdlr(handler_type) + _box(b"minf", sample_table),
    )
    return _box(b"trak", _tkhd(track_id, version=version) + media)


def _movie(*tracks, extra_moov_payload=b"", extra_top_level=b""):
    return (
        _box(b"ftyp", b"isom\x00\x00\x00\x00")
        + extra_top_level
        + _box(b"moov", b"".join(tracks) + extra_moov_payload)
    )


def _parse_movie(tmp_path, raw, *, selected_track_id):
    source_path = tmp_path / "movie.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        return parse_apple_log_signal(
            descriptor,
            len(raw),
            selected_track_id=selected_track_id,
        )
    finally:
        os.close(descriptor)


def test_authoritative_traversal_reaches_only_moov_trak_stsd_visual_logs_path(
    tmp_path,
):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")
    raw = _movie(_track(7, [_visual_entry(b"avc1", logs)]))

    result = _parse_movie(tmp_path, raw, selected_track_id=7)

    assert result.kind == "recognized_logs"
    assert result.source_profile == "apple-log-1"
    assert result.track_id == 7


def test_logs_outside_authoritative_sample_entry_path_are_ignored(tmp_path):
    decoy = _box(b"logs", b"com.apple.rec2020.apple-log\x00")
    media = _box(
        b"mdia",
        _hdlr() + decoy + _box(b"minf", _box(b"stbl", _stsd([_visual_entry()]))),
    )
    track = _box(b"trak", _tkhd(7) + decoy + media)
    raw = _movie(track, extra_moov_payload=decoy, extra_top_level=decoy)

    result = _parse_movie(tmp_path, raw, selected_track_id=7)

    assert result.kind == "no_logs"
    assert result.source_profile is None


@pytest.mark.parametrize("version", [2, 255])
def test_tkhd_fullbox_version_is_closed_and_unsupported_is_unresolved(
    tmp_path, version
):
    media = _box(
        b"mdia",
        _hdlr() + _box(b"minf", _box(b"stbl", _stsd([_visual_entry()]))),
    )
    track = _box(b"trak", _tkhd(7, version=version) + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "unknown_logs"
    assert result.track_resolution == "unresolved"


def test_tkhd_version_zero_reads_nonzero_uint32_track_id(tmp_path):
    track_id = 0xFFFF_FFFF
    result = _parse_movie(
        tmp_path,
        _movie(_track(track_id, [_visual_entry()], version=0)),
        selected_track_id=track_id,
    )

    assert result.kind == "no_logs"
    assert result.track_id == track_id


def test_tkhd_version_one_reads_nonzero_uint32_track_id(tmp_path):
    track_id = 0x8000_0001
    result = _parse_movie(
        tmp_path,
        _movie(_track(track_id, [_visual_entry()], version=1)),
        selected_track_id=track_id,
    )

    assert result.kind == "no_logs"
    assert result.track_id == track_id


@pytest.mark.parametrize("tkhd_count", [0, 2])
def test_missing_or_duplicate_bounded_tkhd_is_unresolved(tmp_path, tkhd_count):
    media = _box(
        b"mdia",
        _hdlr() + _box(b"minf", _box(b"stbl", _stsd([_visual_entry()]))),
    )
    track = _box(b"trak", _tkhd(7) * tkhd_count + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "unknown_logs"
    assert result.track_id is None
    assert result.track_resolution == "unresolved"


@pytest.mark.parametrize("case", ["zero", "duplicate", "unmatched"])
def test_zero_duplicate_or_unmatched_track_id_is_unresolved(tmp_path, case):
    if case == "zero":
        tracks = (_track(0, [_visual_entry()]),)
        selected_track_id = 7
    elif case == "duplicate":
        tracks = (
            _track(7, [_visual_entry()]),
            _track(7, [_visual_entry()]),
        )
        selected_track_id = 7
    else:
        tracks = (_track(7, [_visual_entry()]),)
        selected_track_id = 8

    result = _parse_movie(
        tmp_path,
        _movie(*tracks),
        selected_track_id=selected_track_id,
    )

    assert result.kind == "unknown_logs"
    assert result.track_id is None
    assert result.track_resolution == "unresolved"


@pytest.mark.parametrize(
    "tkhd",
    [
        _box(b"tkhd", b""),
        _box(b"tkhd", b"\x00" * 83),
        (512).to_bytes(4, "big") + b"tkhd" + b"\x00" * 84,
    ],
)
def test_tkhd_short_required_fields_or_boundary_damage_is_invalid(tmp_path, tkhd):
    media = _box(
        b"mdia",
        _hdlr() + _box(b"minf", _box(b"stbl", _stsd([_visual_entry()]))),
    )
    track = _box(b"trak", tkhd + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "invalid"
    assert result.track_resolution == "not_applicable"


@pytest.mark.parametrize(
    ("handler_type", "expected_kind"),
    [(b"vide", "no_logs"), (b"soun", "unknown_logs")],
)
def test_hdlr_fullbox_reads_exact_handler_type(tmp_path, handler_type, expected_kind):
    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry()], handler_type=handler_type)),
        selected_track_id=7,
    )

    assert result.kind == expected_kind


@pytest.mark.parametrize("case", ["missing", "duplicate", "version", "flags"])
def test_bounded_missing_duplicate_or_unsupported_hdlr_is_unresolved(tmp_path, case):
    if case == "missing":
        handlers = b""
    elif case == "duplicate":
        handlers = _hdlr() + _hdlr()
    elif case == "version":
        handlers = _hdlr(version=1)
    else:
        handlers = _hdlr(flags=1)
    media = _box(
        b"mdia",
        handlers + _box(b"minf", _box(b"stbl", _stsd([_visual_entry()]))),
    )
    track = _box(b"trak", _tkhd(7) + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "unknown_logs"
    assert result.track_resolution == "unresolved"


@pytest.mark.parametrize(
    "handler",
    [
        _box(b"hdlr", b""),
        _box(b"hdlr", b"\x00" * 23),
        (512).to_bytes(4, "big") + b"hdlr" + b"\x00" * 24,
    ],
)
def test_hdlr_short_required_fields_or_boundary_damage_is_invalid(tmp_path, handler):
    media = _box(
        b"mdia",
        handler + _box(b"minf", _box(b"stbl", _stsd([_visual_entry()]))),
    )
    track = _box(b"trak", _tkhd(7) + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "invalid"
    assert result.track_resolution == "not_applicable"


def test_only_vide_tracks_are_collected_as_video_tracks(tmp_path):
    audio_tracks = tuple(
        _track(track_id, [_visual_entry()], handler_type=b"soun")
        for track_id in range(1, 10)
    )
    video_track = _track(100, [_visual_entry()], handler_type=b"vide")

    result = _parse_movie(
        tmp_path,
        _movie(*audio_tracks, video_track),
        selected_track_id=100,
    )

    assert result.kind == "no_logs"


def test_logs_inside_audio_track_are_ignored(tmp_path):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")
    audio_track = _track(
        1,
        [_visual_entry(b"avc1", logs)],
        handler_type=b"soun",
    )
    video_track = _track(2, [_visual_entry()])

    result = _parse_movie(
        tmp_path,
        _movie(audio_track, video_track),
        selected_track_id=2,
    )

    assert result.kind == "no_logs"
    assert result.source_profile is None


def test_logs_inside_unselected_video_track_are_ignored(tmp_path):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log\x00")
    decoy_track = _track(1, [_visual_entry(b"avc1", logs)])
    selected_track = _track(2, [_visual_entry()])

    result = _parse_movie(
        tmp_path,
        _movie(decoy_track, selected_track),
        selected_track_id=2,
    )

    assert result.kind == "no_logs"
    assert result.source_profile is None
    assert result.track_id == 2


@pytest.mark.parametrize("decoy_type", [b"mdat", b"hoov"])
def test_mdat_and_hoov_identifier_decoys_are_ignored(tmp_path, decoy_type):
    decoy = _box(decoy_type, b"com.apple.rec2020.apple-log\x00")

    result = _parse_movie(
        tmp_path,
        _movie(
            _track(7, [_visual_entry()]),
            extra_top_level=decoy,
        ),
        selected_track_id=7,
    )

    assert result.kind == "no_logs"
    assert result.source_profile is None
    assert result.track_id == 7


@pytest.mark.parametrize(
    ("track_count", "expected_kind"),
    [(MAX_VIDEO_TRACKS, "no_logs"), (MAX_VIDEO_TRACKS + 1, "resource_limit")],
)
def test_video_track_count_limit_is_maximum_inclusive(
    tmp_path, track_count, expected_kind
):
    tracks = tuple(
        _track(track_id, [_visual_entry()]) for track_id in range(1, track_count + 1)
    )

    result = _parse_movie(tmp_path, _movie(*tracks), selected_track_id=1)

    assert result.kind == expected_kind


@pytest.mark.parametrize("version,flags", [(1, 0), (0, 1), (255, 0xFF_FFFF)])
def test_stsd_fullbox_version_and_flags_are_closed_and_unsupported_is_unresolved(
    tmp_path, version, flags
):
    sample_table = _box(
        b"stbl",
        _stsd([_visual_entry()], version=version, flags=flags),
    )
    media = _box(b"mdia", _hdlr() + _box(b"minf", sample_table))
    track = _box(b"trak", _tkhd(7) + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "unknown_logs"
    assert result.track_resolution == "unresolved"


def test_stsd_entry_count_and_each_entry_boundary_are_parsed(tmp_path):
    entries = [_visual_entry(b"avc1"), _visual_entry(b"apcs")]

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, entries)),
        selected_track_id=7,
    )

    assert result.kind == "no_logs"


@pytest.mark.parametrize("stsd_count", [0, 2])
def test_bounded_missing_or_duplicate_stsd_is_unresolved(tmp_path, stsd_count):
    sample_table = _box(b"stbl", _stsd([_visual_entry()]) * stsd_count)
    media = _box(b"mdia", _hdlr() + _box(b"minf", sample_table))
    track = _box(b"trak", _tkhd(7) + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "unknown_logs"
    assert result.track_resolution == "unresolved"


@pytest.mark.parametrize(
    "stsd",
    [
        _box(b"stsd", b""),
        _box(b"stsd", b"\x00" * 7),
        _stsd([_visual_entry()], declared_count=2),
        _stsd([_visual_entry()], declared_count=0),
        _box(
            b"stsd",
            b"\x00" * 4
            + (1).to_bytes(4, "big")
            + (512).to_bytes(4, "big")
            + b"avc1"
            + b"\x00" * 78,
        ),
    ],
)
def test_stsd_short_count_or_entry_boundary_damage_is_invalid(tmp_path, stsd):
    sample_table = _box(b"stbl", stsd)
    media = _box(b"mdia", _hdlr() + _box(b"minf", sample_table))
    track = _box(b"trak", _tkhd(7) + media)

    result = _parse_movie(tmp_path, _movie(track), selected_track_id=7)

    assert result.kind == "invalid"


@pytest.mark.parametrize(
    ("entry_count", "expected_kind"),
    [
        (MAX_SAMPLE_DESCRIPTIONS, "no_logs"),
        (MAX_SAMPLE_DESCRIPTIONS + 1, "resource_limit"),
    ],
)
def test_sample_description_count_limit_is_maximum_inclusive(
    tmp_path, entry_count, expected_kind
):
    entries = [_visual_entry() for _index in range(entry_count)]

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, entries)),
        selected_track_id=7,
    )

    assert result.kind == expected_kind


def test_supported_iso_visual_sample_entry_fourcc_allowlist_is_closed():
    assert ISO_VISUAL_SAMPLE_ENTRY_TYPES == {
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


def test_apcs_sample_entry_uses_iso_fixed_visual_layout(tmp_path):
    logs = _box(b"logs", b"com.apple.apple-wide-gamut.apple-log")

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"apcs", logs)])),
        selected_track_id=7,
    )

    assert result.kind == "recognized_logs"
    assert result.source_profile == "apple-log-2"


@pytest.mark.parametrize(
    "entry_type",
    sorted(ISO_VISUAL_SAMPLE_ENTRY_TYPES - {b"apcs", b"avc1"}),
)
def test_remaining_design_fourcc_use_same_closed_iso_layout(tmp_path, entry_type):
    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(entry_type)])),
        selected_track_id=7,
    )

    assert result.kind == "no_logs"


def test_visual_sample_entry_child_offset_is_exactly_86_bytes():
    assert VISUAL_SAMPLE_ENTRY_CHILD_OFFSET == 86


@pytest.mark.parametrize("entry_size", [8, 85])
def test_visual_sample_entry_shorter_than_86_bytes_is_invalid(tmp_path, entry_size):
    entry = _box(b"avc1", b"\x00" * (entry_size - 8))

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [entry])),
        selected_track_id=7,
    )

    assert result.kind == "invalid"


def test_unknown_legacy_visual_layout_is_unsupported_without_offset_guessing(
    tmp_path,
):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")
    unknown_entry = _visual_entry(b"raw ", logs)

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [unknown_entry])),
        selected_track_id=7,
    )

    assert result.kind == "unsupported_container"
    assert result.source_profile is None
    assert result.track_id is None


def test_visual_entry_child_box_is_bounded_by_sample_entry_end(tmp_path):
    oversized_child = (100).to_bytes(4, "big") + b"logs"
    entry = _visual_entry(b"avc1", oversized_child)

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [entry])),
        selected_track_id=7,
    )

    assert result.kind == "invalid"


def test_only_direct_logs_child_is_collected_from_visual_entry(tmp_path):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log\x00")
    nested_decoy = _box(b"wave", logs)

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", nested_decoy)])),
        selected_track_id=7,
    )

    assert result.kind == "no_logs"


@pytest.mark.parametrize("padding_length", range(8))
def test_visual_sample_entry_allows_observed_short_zero_padding(
    tmp_path, padding_length
):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")
    result = _parse_movie(
        tmp_path,
        _movie(
            _track(
                7,
                [_visual_entry(b"apcs", logs + b"\x00" * padding_length)],
            )
        ),
        selected_track_id=7,
    )

    assert result.kind == "recognized_logs"


def test_visual_sample_entry_rejects_nonzero_trailing_padding(tmp_path):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")
    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"apcs", logs + b"\x01")])),
        selected_track_id=7,
    )

    assert result.kind == "invalid"


@pytest.mark.parametrize(
    ("payload", "expected_kind"),
    [
        (b"x", "unknown_logs"),
        (b"x" * 128, "unknown_logs"),
        (b"", "invalid"),
        (b"x" * 129, "invalid"),
    ],
)
def test_logs_payload_length_is_bounded_from_1_through_128_bytes(
    tmp_path, payload, expected_kind
):
    logs = _box(b"logs", payload)

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", logs)])),
        selected_track_id=7,
    )

    assert result.kind == expected_kind


@pytest.mark.parametrize(
    ("payload", "expected_kind"),
    [(b"unknown", "unknown_logs"), (b"unknown\x00", "invalid")],
)
def test_logs_payload_requires_no_nul_terminator(tmp_path, payload, expected_kind):
    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", _box(b"logs", payload))])),
        selected_track_id=7,
    )

    assert result.kind == expected_kind


def test_logs_payload_embedded_nul_is_invalid(tmp_path):
    payload = b"com.apple\x00.hidden"

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", _box(b"logs", payload))])),
        selected_track_id=7,
    )

    assert result.kind == "invalid"


def test_logs_payload_non_ascii_is_invalid(tmp_path):
    payload = b"com.apple.\xff"

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", _box(b"logs", payload))])),
        selected_track_id=7,
    )

    assert result.kind == "invalid"


def test_logs_payload_trailing_terminator_is_invalid(tmp_path):
    payload = b"com.apple.rec2020.apple-log\x00"

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", _box(b"logs", payload))])),
        selected_track_id=7,
    )

    assert result.kind == "invalid"


def test_unknown_logs_identifier_text_is_not_retained(tmp_path):
    private_identifier = b"private.camera.identifier"
    result = _parse_movie(
        tmp_path,
        _movie(
            _track(
                7,
                [
                    _visual_entry(
                        b"avc1",
                        _box(b"logs", private_identifier),
                    )
                ],
            )
        ),
        selected_track_id=7,
    )

    assert result.kind == "unknown_logs"
    assert result.signal_kind == "unknown-logs"
    assert private_identifier.decode("ascii") not in repr(result)


def test_consistent_duplicate_logs_identifiers_are_normalized(tmp_path):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")
    entries = [_visual_entry(b"avc1", logs), _visual_entry(b"apcs", logs + logs)]

    result = _parse_movie(
        tmp_path,
        _movie(_track(7, entries)),
        selected_track_id=7,
    )

    assert result.kind == "recognized_logs"
    assert result.source_profile == "apple-log-1"


def test_different_recognized_logs_identifiers_are_conflicting(tmp_path):
    apple_log_1 = _box(b"logs", b"com.apple.rec2020.apple-log")
    apple_log_2 = _box(
        b"logs",
        b"com.apple.apple-wide-gamut.apple-log",
    )

    result = _parse_movie(
        tmp_path,
        _movie(
            _track(
                7,
                [
                    _visual_entry(b"avc1", apple_log_1),
                    _visual_entry(b"apcs", apple_log_2),
                ],
            )
        ),
        selected_track_id=7,
    )

    assert result.kind == "conflicting_logs"
    assert result.source_profile is None


def test_recognized_and_unknown_logs_identifiers_are_conflicting(tmp_path):
    recognized = _box(b"logs", b"com.apple.rec2020.apple-log")
    unknown = _box(b"logs", b"com.apple.private-log-profile")

    result = _parse_movie(
        tmp_path,
        _movie(
            _track(
                7,
                [_visual_entry(b"avc1", recognized + unknown)],
            )
        ),
        selected_track_id=7,
    )

    assert result.kind == "conflicting_logs"
    assert result.source_profile is None


@pytest.mark.parametrize(
    ("identifier_count", "expected_kind"),
    [
        (MAX_RETAINED_IDENTIFIERS, "recognized_logs"),
        (MAX_RETAINED_IDENTIFIERS + 1, "resource_limit"),
    ],
)
def test_retained_logs_identifier_limit_is_maximum_inclusive(
    tmp_path,
    identifier_count,
    expected_kind,
):
    logs = _box(b"logs", b"com.apple.rec2020.apple-log")

    result = _parse_movie(
        tmp_path,
        _movie(
            _track(
                7,
                [_visual_entry(b"avc1", logs * identifier_count)],
            )
        ),
        selected_track_id=7,
    )

    assert result.kind == expected_kind


@pytest.mark.parametrize(
    ("identifier", "expected_profile", "expected_signal_kind"),
    [
        (
            b"com.apple.rec2020.apple-log",
            "apple-log-1",
            "apple-log-1-logs",
        ),
        (
            b"com.apple.apple-wide-gamut.apple-log",
            "apple-log-2",
            "apple-log-2-logs",
        ),
    ],
)
def test_matched_recognized_logs_include_profile_track_and_status(
    tmp_path,
    identifier,
    expected_profile,
    expected_signal_kind,
):
    result = _parse_movie(
        tmp_path,
        _movie(
            _track(
                7,
                [_visual_entry(b"avc1", _box(b"logs", identifier))],
            )
        ),
        selected_track_id=7,
    )

    assert result.kind == "recognized_logs"
    assert result.source_profile == expected_profile
    assert result.track_id == 7
    assert result.track_resolution == "matched"
    assert result.signal_kind == expected_signal_kind


@pytest.mark.parametrize(
    ("children", "expected_kind", "expected_signal_kind"),
    [
        (b"", "no_logs", "no-logs"),
        (
            _box(b"logs", b"com.apple.private-log-profile"),
            "unknown_logs",
            "unknown-logs",
        ),
        (
            _box(b"logs", b"com.apple.rec2020.apple-log")
            + _box(b"logs", b"com.apple.apple-wide-gamut.apple-log"),
            "conflicting_logs",
            "conflicting-logs",
        ),
    ],
)
def test_other_matched_results_include_null_profile_track_and_status(
    tmp_path,
    children,
    expected_kind,
    expected_signal_kind,
):
    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry(b"avc1", children)])),
        selected_track_id=7,
    )

    assert result.kind == expected_kind
    assert result.source_profile is None
    assert result.track_id == 7
    assert result.track_resolution == "matched"
    assert result.signal_kind == expected_signal_kind


def test_unresolved_track_result_has_null_profile_and_track_id(tmp_path):
    result = _parse_movie(
        tmp_path,
        _movie(_track(7, [_visual_entry()])),
        selected_track_id=None,
    )

    assert result.kind == "unknown_logs"
    assert result.source_profile is None
    assert result.track_id is None
    assert result.track_resolution == "unresolved"
    assert result.signal_kind == "track-unresolved"


def test_terminal_and_unsupported_results_are_not_track_applicable(tmp_path):
    unsupported = _parse_movie(
        tmp_path,
        _box(b"free"),
        selected_track_id=7,
    )
    invalid = _parse_movie(
        tmp_path,
        _box(b"ftyp", b"isom\x00\x00\x00\x00"),
        selected_track_id=7,
    )
    source_path = tmp_path / "resource-limit.mov"
    source_path.write_bytes(b"")
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        resource_limited = parse_apple_log_signal(
            descriptor,
            MAX_FILE_SIZE + 1,
            selected_track_id=7,
        )
    finally:
        os.close(descriptor)

    for result in (unsupported, invalid, resource_limited):
        assert result.source_profile is None
        assert result.track_id is None
        assert result.track_resolution == "not_applicable"

    assert unsupported.kind == "unsupported_container"
    assert invalid.kind == "invalid"
    assert resource_limited.kind == "resource_limit"


def test_recursive_traversal_carries_current_depth_as_local_state(tmp_path):
    raw = _box(b"moov", _box(b"trak", _box(b"free")))
    source_path = tmp_path / "nested.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    budget = ParseBudget()
    try:
        headers = list(
            _walk_box_tree(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=budget,
                ),
                start=0,
                end=len(raw),
                current_depth=1,
                container_types=frozenset({b"moov", b"trak"}),
            )
        )
    finally:
        os.close(descriptor)

    assert [header.type for header in headers] == [b"moov", b"trak", b"free"]
    assert budget.max_depth_seen == 3


def test_many_shallow_sibling_boxes_do_not_consume_depth_limit(tmp_path):
    raw = b"".join(_box(b"free") for _index in range(256))
    source_path = tmp_path / "shallow-siblings.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    budget = ParseBudget()
    try:
        headers = list(
            _walk_box_tree(
                _BoundedReader(
                    fd=descriptor,
                    file_size=len(raw),
                    budget=budget,
                ),
                start=0,
                end=len(raw),
                current_depth=1,
                container_types=frozenset(),
            )
        )
    finally:
        os.close(descriptor)

    assert len(headers) == 256
    assert budget.max_depth_seen == 1


def test_depth_twelve_succeeds_and_depth_thirteen_is_resource_limited(tmp_path):
    def nested_boxes(depth):
        raw = b""
        for _index in range(depth):
            raw = _box(b"nest", raw)
        return raw

    allowed_path = tmp_path / "depth-12.bin"
    allowed_path.write_bytes(nested_boxes(MAX_NESTING_DEPTH))
    allowed_descriptor = os.open(allowed_path, os.O_RDONLY)
    allowed_budget = ParseBudget()
    try:
        headers = list(
            _walk_box_tree(
                _BoundedReader(
                    fd=allowed_descriptor,
                    file_size=allowed_path.stat().st_size,
                    budget=allowed_budget,
                ),
                start=0,
                end=allowed_path.stat().st_size,
                current_depth=1,
                container_types=frozenset({b"nest"}),
            )
        )
    finally:
        os.close(allowed_descriptor)

    limited_path = tmp_path / "depth-13.bin"
    limited_path.write_bytes(nested_boxes(MAX_NESTING_DEPTH + 1))
    limited_descriptor = os.open(limited_path, os.O_RDONLY)
    try:
        with pytest.raises(_ResourceLimit):
            list(
                _walk_box_tree(
                    _BoundedReader(
                        fd=limited_descriptor,
                        file_size=limited_path.stat().st_size,
                        budget=ParseBudget(),
                    ),
                    start=0,
                    end=limited_path.stat().st_size,
                    current_depth=1,
                    container_types=frozenset({b"nest"}),
                )
            )
    finally:
        os.close(limited_descriptor)

    assert len(headers) == MAX_NESTING_DEPTH
    assert allowed_budget.max_depth_seen == MAX_NESTING_DEPTH


def test_seeded_byte_mutations_always_return_a_closed_parser_result(tmp_path):
    baseline = _movie(
        _track(
            7,
            [
                _visual_entry(
                    b"avc1",
                    _box(b"logs", b"com.apple.rec2020.apple-log"),
                )
            ],
        )
    )
    generator = random.Random(20260802)

    for _case in range(256):
        mutated = bytearray(baseline)
        offset = generator.randrange(len(mutated))
        mutated[offset] ^= generator.randrange(1, 256)

        result = _parse_movie(
            tmp_path,
            bytes(mutated),
            selected_track_id=7,
        )

        assert result.kind in PARSER_RESULT_KINDS


def test_seeded_byte_mutation_results_are_deterministic(tmp_path):
    baseline = _movie(
        _track(
            7,
            [
                _visual_entry(
                    b"apcs",
                    _box(
                        b"logs",
                        b"com.apple.apple-wide-gamut.apple-log\x00",
                    ),
                )
            ],
        )
    )
    generator = random.Random(20260802)

    for _case in range(128):
        mutated = bytearray(baseline)
        offset = generator.randrange(len(mutated))
        mutated[offset] ^= generator.randrange(1, 256)
        raw = bytes(mutated)

        first = _parse_movie(tmp_path, raw, selected_track_id=7)
        second = _parse_movie(tmp_path, raw, selected_track_id=7)

        assert second == first


def test_parser_metadata_read_and_retained_result_stay_bounded(tmp_path):
    large_media_payload = b"m" * (MAX_METADATA_BYTES + 1)
    raw = _movie(
        _track(
            7,
            [
                _visual_entry(
                    b"avc1",
                    _box(b"logs", b"com.apple.rec2020.apple-log"),
                )
            ],
        ),
        extra_top_level=_box(b"mdat", large_media_payload),
    )

    result = _parse_movie(tmp_path, raw, selected_track_id=7)

    assert result.kind == "recognized_logs"
    assert result.metadata_bytes_read <= MAX_METADATA_BYTES
    assert not hasattr(result, "identifiers")
    assert not hasattr(result, "raw_metadata")


def test_only_offset_zero_first_top_level_box_identifies_bmff(tmp_path):
    raw = _box(b"free") + _box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"moov")
    source_path = tmp_path / "late-ftyp.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "unsupported_container"
    assert result.box_headers == 0


@pytest.mark.parametrize("length", range(8))
def test_public_parser_treats_short_eight_byte_header_as_invalid(tmp_path, length):
    raw = b"\x00" * length
    source_path = tmp_path / f"short-public-header-{length}.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(
            descriptor,
            len(raw),
            selected_track_id=None,
        )
    finally:
        os.close(descriptor)

    assert result.kind == "invalid"
    assert result.source_profile is None
    assert result.track_id is None
    assert result.track_resolution == "not_applicable"
    assert result.signal_kind is None


@pytest.mark.parametrize("first_type", [b"free", b"mdat", b"moov"])
def test_non_ftyp_first_box_is_unsupported_container(tmp_path, first_type):
    raw = _box(first_type)
    source_path = tmp_path / "non-ftyp.bin"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "unsupported_container"
    assert result.source_profile is None


@pytest.mark.parametrize(
    ("ftyp_payload", "expected_kind"),
    [
        (b"isom\x00\x00\x00\x00", "unknown_logs"),
        (b"qt  \x00\x00\x00\x00", "unknown_logs"),
        (b"isom\x00\x00\x00", "invalid"),
        (b"iso\x00\x00\x00\x00\x00", "invalid"),
        (b"\xffsom\x00\x00\x00\x00", "invalid"),
    ],
)
def test_ftyp_requires_minimum_payload_and_printable_ascii_major_brand(
    tmp_path, ftyp_payload, expected_kind
):
    raw = _box(b"ftyp", ftyp_payload) + _box(b"moov")
    source_path = tmp_path / "ftyp.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == expected_kind


@pytest.mark.parametrize(
    "raw",
    [
        (64).to_bytes(4, "big") + b"ftyp" + b"isom\x00\x00\x00\x00",
        (1).to_bytes(4, "big") + b"ftyp" + (15).to_bytes(8, "big"),
        (7).to_bytes(4, "big") + b"ftyp",
    ],
)
def test_malformed_declared_ftyp_is_invalid(tmp_path, raw):
    source_path = tmp_path / "malformed-ftyp.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "invalid"


def test_top_level_box_iterator_advances_by_declared_box_end(tmp_path):
    raw = (
        _box(b"ftyp", b"isom\x00\x00\x00\x00")
        + _box(b"free", b"ignored")
        + _box(b"moov")
        + _box(b"mdat", b"payload")
    )
    source_path = tmp_path / "top-level.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        headers = _parse_top_level(
            _BoundedReader(
                fd=descriptor,
                file_size=len(raw),
                budget=ParseBudget(),
            )
        )
    finally:
        os.close(descriptor)

    assert [header.type for header in headers] == [
        b"ftyp",
        b"free",
        b"moov",
        b"mdat",
    ]
    assert headers[-1].end == len(raw)


def test_top_level_requires_exactly_one_moov(tmp_path):
    raw = _box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"moov")
    source_path = tmp_path / "one-moov.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "unknown_logs"


def test_missing_top_level_moov_is_invalid(tmp_path):
    raw = _box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"mdat")
    source_path = tmp_path / "missing-moov.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "invalid"


def test_duplicate_top_level_moov_is_invalid(tmp_path):
    raw = _box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"moov") + _box(b"moov")
    source_path = tmp_path / "duplicate-moov.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "invalid"


def test_mdat_payload_is_skipped_without_reading(tmp_path, monkeypatch):
    ftyp = _box(b"ftyp", b"isom\x00\x00\x00\x00")
    mdat = _box(b"mdat", b"private-media-payload" * 100)
    raw = ftyp + mdat + _box(b"moov")
    mdat_payload_start = len(ftyp) + 8
    mdat_end = len(ftyp) + len(mdat)
    source_path = tmp_path / "skip-mdat.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    original_pread = os.pread
    reads = []

    def capture_pread(fd, length, offset):
        reads.append((offset, length))
        return original_pread(fd, length, offset)

    monkeypatch.setattr(iso_bmff_log_parser.os, "pread", capture_pread)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "unknown_logs"
    assert all(
        offset + length <= mdat_payload_start or offset >= mdat_end
        for offset, length in reads
    )


@pytest.mark.parametrize("ignored_type", [b"hoov", b"zzzz"])
def test_hoov_and_unknown_top_level_payloads_are_skipped(
    tmp_path, monkeypatch, ignored_type
):
    ftyp = _box(b"ftyp", b"isom\x00\x00\x00\x00")
    ignored = _box(ignored_type, b"com.apple.private-decoy" * 100)
    raw = ftyp + ignored + _box(b"moov")
    payload_start = len(ftyp) + 8
    ignored_end = len(ftyp) + len(ignored)
    source_path = tmp_path / "skip-unknown.mov"
    source_path.write_bytes(raw)
    descriptor = os.open(source_path, os.O_RDONLY)
    original_pread = os.pread
    reads = []

    def capture_pread(fd, length, offset):
        reads.append((offset, length))
        return original_pread(fd, length, offset)

    monkeypatch.setattr(iso_bmff_log_parser.os, "pread", capture_pread)
    try:
        result = parse_apple_log_signal(descriptor, len(raw), selected_track_id=None)
    finally:
        os.close(descriptor)

    assert result.kind == "unknown_logs"
    assert all(
        offset + length <= payload_start or offset >= ignored_end
        for offset, length in reads
    )


def test_parse_budget_max_depth_seen_is_monotonic():
    budget = ParseBudget()

    for current_depth in (1, 4, 2, 4, 3):
        previous = budget.max_depth_seen
        _record_depth(budget, current_depth)
        assert budget.max_depth_seen >= previous

    assert budget.max_depth_seen == 4


def test_nesting_depth_limit_is_maximum_inclusive():
    budget = ParseBudget()

    _record_depth(budget, MAX_NESTING_DEPTH)
    assert budget.max_depth_seen == MAX_NESTING_DEPTH
    with pytest.raises(_ResourceLimit):
        _record_depth(budget, MAX_NESTING_DEPTH + 1)


def test_cumulative_metadata_read_limit_is_maximum_inclusive(tmp_path):
    source_path = tmp_path / "metadata.bin"
    source_path.write_bytes(b"abc")
    descriptor = os.open(source_path, os.O_RDONLY)
    budget = ParseBudget(bytes_read=MAX_METADATA_BYTES - 2)
    reader = _BoundedReader(fd=descriptor, file_size=3, budget=budget)
    try:
        assert reader.read_exact(0, 2) == b"ab"
        assert budget.bytes_read == MAX_METADATA_BYTES
        with pytest.raises(_ResourceLimit):
            reader.read_exact(2, 1)
    finally:
        os.close(descriptor)


def test_file_size_hard_limit_is_maximum_inclusive():
    _BoundedReader(fd=0, file_size=MAX_FILE_SIZE, budget=ParseBudget())
    with pytest.raises(_ResourceLimit):
        _BoundedReader(fd=0, file_size=MAX_FILE_SIZE + 1, budget=ParseBudget())
