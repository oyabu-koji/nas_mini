from __future__ import annotations


APPLE_LOG_1_IDENTIFIER = b"com.apple.rec2020.apple-log"


def build_apple_log_1_synthetic_container(*, track_id: int = 1) -> bytes:
    if type(track_id) is not int or track_id <= 0 or track_id > 0xFFFF_FFFF:
        raise ValueError("track_id must be a nonzero uint32")
    logs = _box(b"logs", APPLE_LOG_1_IDENTIFIER)
    visual_entry = _box(b"avc1", b"\x00" * 78 + logs)
    stsd = _box(
        b"stsd",
        b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + visual_entry,
    )
    tkhd_payload = bytearray(84)
    tkhd_payload[12:16] = track_id.to_bytes(4, "big")
    tkhd = _box(b"tkhd", bytes(tkhd_payload))
    hdlr_payload = bytearray(24)
    hdlr_payload[8:12] = b"vide"
    hdlr = _box(b"hdlr", bytes(hdlr_payload))
    track = _box(
        b"trak",
        tkhd
        + _box(
            b"mdia",
            hdlr + _box(b"minf", _box(b"stbl", stsd)),
        ),
    )
    return _box(b"ftyp", b"isom\x00\x00\x00\x00") + _box(b"moov", track)


def _box(box_type: bytes, payload: bytes) -> bytes:
    if len(box_type) != 4:
        raise ValueError("box type must be four bytes")
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload
