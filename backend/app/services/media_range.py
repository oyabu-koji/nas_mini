from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


CHUNK_SIZE_BYTES = 1024 * 1024


class InvalidRangeError(RuntimeError):
    def __init__(self, total_size: int):
        super().__init__("invalid range")
        self.total_size = total_size


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range_header(range_header: str | None, total_size: int) -> ByteRange | None:
    if range_header is None or range_header.strip() == "":
        return None
    if total_size <= 0:
        raise InvalidRangeError(total_size)

    normalized = range_header.strip()
    if not normalized.startswith("bytes="):
        raise InvalidRangeError(total_size)

    range_spec = normalized.removeprefix("bytes=")
    if "," in range_spec or "-" not in range_spec:
        raise InvalidRangeError(total_size)

    start_text, end_text = range_spec.split("-", 1)
    if start_text == "" and end_text == "":
        raise InvalidRangeError(total_size)

    if start_text == "":
        return _suffix_range(end_text, total_size)

    try:
        start = int(start_text)
    except ValueError as exc:
        raise InvalidRangeError(total_size) from exc
    if start < 0 or start >= total_size:
        raise InvalidRangeError(total_size)

    if end_text == "":
        return ByteRange(start=start, end=total_size - 1, total=total_size)

    try:
        end = int(end_text)
    except ValueError as exc:
        raise InvalidRangeError(total_size) from exc
    if end < start:
        raise InvalidRangeError(total_size)
    return ByteRange(start=start, end=min(end, total_size - 1), total=total_size)


def iter_file(path: Path, *, start: int, end: int) -> Iterator[bytes]:
    file = path.open("rb")
    yield from iter_open_file(file, start=start, end=end)


def iter_open_file(file: BinaryIO, *, start: int, end: int) -> Iterator[bytes]:
    try:
        file.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = file.read(min(CHUNK_SIZE_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file.close()


def _suffix_range(end_text: str, total_size: int) -> ByteRange:
    try:
        suffix_length = int(end_text)
    except ValueError as exc:
        raise InvalidRangeError(total_size) from exc
    if suffix_length <= 0:
        raise InvalidRangeError(total_size)

    length = min(suffix_length, total_size)
    start = total_size - length
    return ByteRange(start=start, end=total_size - 1, total=total_size)
