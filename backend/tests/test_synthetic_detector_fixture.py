import os

from app.services.iso_bmff_log_parser import parse_apple_log_signal
from app.services.synthetic_detector_fixture import (
    build_apple_log_1_synthetic_container,
)


def test_generated_apple_log_1_synthetic_container_is_deterministic_and_parseable(
    tmp_path,
):
    first = build_apple_log_1_synthetic_container(track_id=1)
    second = build_apple_log_1_synthetic_container(track_id=1)
    fixture = tmp_path / "apple-log-1.mov"
    fixture.write_bytes(first)
    descriptor = os.open(fixture, os.O_RDONLY)
    try:
        signal = parse_apple_log_signal(descriptor, len(first), selected_track_id=1)
    finally:
        os.close(descriptor)

    assert first == second
    assert signal.kind == "recognized_logs"
    assert signal.source_profile == "apple-log-1"
    assert signal.track_id == 1
    assert signal.track_resolution == "matched"
