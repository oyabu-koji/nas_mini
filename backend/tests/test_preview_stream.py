import pytest

from app.services.preview_stream import InvalidRangeError, parse_range_header


def test_parse_range_header_allows_missing_header():
    assert parse_range_header(None, 10) is None


def test_parse_range_header_start_end():
    byte_range = parse_range_header("bytes=0-3", 10)

    assert byte_range is not None
    assert byte_range.start == 0
    assert byte_range.end == 3
    assert byte_range.total == 10
    assert byte_range.length == 4


def test_parse_range_header_start_to_end():
    byte_range = parse_range_header("bytes=3-", 10)

    assert byte_range is not None
    assert byte_range.start == 3
    assert byte_range.end == 9


def test_parse_range_header_suffix():
    byte_range = parse_range_header("bytes=-4", 10)

    assert byte_range is not None
    assert byte_range.start == 6
    assert byte_range.end == 9


def test_parse_range_header_clamps_oversized_end():
    byte_range = parse_range_header("bytes=8-99", 10)

    assert byte_range is not None
    assert byte_range.start == 8
    assert byte_range.end == 9


@pytest.mark.parametrize(
    "header",
    [
        "items=0-3",
        "bytes=",
        "bytes=0-3,5-7",
        "bytes=9-3",
        "bytes=10-",
        "bytes=-0",
    ],
)
def test_parse_range_header_rejects_invalid_ranges(header):
    with pytest.raises(InvalidRangeError) as exc_info:
        parse_range_header(header, 10)

    assert exc_info.value.total_size == 10
