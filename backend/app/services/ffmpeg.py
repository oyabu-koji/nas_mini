import subprocess
from pathlib import Path


DEFAULT_FFMPEG_TIMEOUT_SECONDS = 300
MAX_ERROR_MESSAGE_LENGTH = 200


class PreviewGenerationError(RuntimeError):
    pass


def build_video_preview_command(
    *,
    input_path: Path,
    output_path: Path,
    lut_path: Path | None,
) -> list[str]:
    filters = []
    if lut_path is not None:
        filters.append(f"lut3d={_escape_filter_path(lut_path)}")
    filters.extend(
        [
            "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "format=yuv420p",
        ]
    )

    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "23",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_image_preview_command(
    *,
    input_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        "scale='min(2048,iw)':'min(2048,ih)':force_original_aspect_ratio=decrease",
        "-q:v",
        "3",
        "-frames:v",
        "1",
        str(output_path),
    ]


def run_ffmpeg(
    command: list[str],
    timeout_seconds: int = DEFAULT_FFMPEG_TIMEOUT_SECONDS,
) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreviewGenerationError(_short_error("ffmpeg timed out")) from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise PreviewGenerationError(_short_error("ffmpeg failed")) from exc


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def _short_error(message: str) -> str:
    return message[:MAX_ERROR_MESSAGE_LENGTH]
