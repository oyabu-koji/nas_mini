import subprocess
from pathlib import Path

import pytest

from app.services import ffmpeg


def test_video_command_includes_preview_options(tmp_path):
    input_path = tmp_path / "input.mov"
    output_path = tmp_path / "preview.mp4"

    command = ffmpeg.build_video_preview_command(
        input_path=input_path,
        output_path=output_path,
        lut_path=None,
    )

    assert command[0] == "ffmpeg"
    assert "-i" in command
    assert str(input_path) in command
    assert str(output_path) == command[-1]
    assert "libx264" in command
    assert "23" in command
    assert "veryfast" in command
    assert "0:a?" in command
    assert "+faststart" in command
    assert "yuv420p" in command
    assert "trunc(iw/2)*2" in ",".join(command)


def test_video_command_includes_lut_path(tmp_path):
    lut_path = tmp_path / "rec709.cube"

    command = ffmpeg.build_video_preview_command(
        input_path=tmp_path / "input.mov",
        output_path=tmp_path / "preview.mp4",
        lut_path=lut_path,
    )

    filter_arg = command[command.index("-vf") + 1]
    assert "lut3d=" in filter_arg
    assert str(lut_path) in filter_arg


def test_image_command_includes_preview_options(tmp_path):
    command = ffmpeg.build_image_preview_command(
        input_path=tmp_path / "input.jpg",
        output_path=tmp_path / "preview.jpg",
    )

    assert command[0] == "ffmpeg"
    assert "-noautorotate" not in command
    assert "2048" in ",".join(command)
    assert "-q:v" in command
    assert "3" in command
    assert "-frames:v" in command


def test_run_ffmpeg_timeout_raises_sanitized_error(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    with pytest.raises(ffmpeg.PreviewGenerationError) as exc_info:
        ffmpeg.run_ffmpeg(["ffmpeg"], timeout_seconds=1)

    assert str(exc_info.value) == "ffmpeg timed out"
    assert len(str(exc_info.value)) <= 200


def test_run_ffmpeg_failure_raises_sanitized_error_without_path(monkeypatch, tmp_path):
    host_path = tmp_path / "secret" / "input.mov"

    def raise_failure(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["ffmpeg", str(host_path)],
            stderr=f"failed reading {host_path}",
        )

    monkeypatch.setattr(subprocess, "run", raise_failure)

    with pytest.raises(ffmpeg.PreviewGenerationError) as exc_info:
        ffmpeg.run_ffmpeg(["ffmpeg", str(host_path)])

    assert str(exc_info.value) == "ffmpeg failed"
    assert str(host_path) not in str(exc_info.value)
    assert len(str(exc_info.value)) <= 200
