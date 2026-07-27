import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ffmpeg import build_image_preview_command, run_ffmpeg


REQUIRED_FORMATS = {"heic", "jpeg", "png"}
EXPECTED_INPUT_CODECS = {
    "heic": {"hevc"},
    "jpeg": {"mjpeg"},
    "png": {"png"},
}
SHA256_LENGTH = 64
MAX_MANIFEST_BYTES = 64 * 1024
MAX_GENERATOR_LENGTH = 512
MAX_LICENSE_LENGTH = 256
MAX_LONG_EDGE = 2048
PROBE_TIMEOUT_SECONDS = 30
FIXTURE_KEYS = {
    "filename",
    "format",
    "sha256",
    "width",
    "height",
    "generator",
    "source",
    "license",
    "ownership",
}


class CodecValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fixture:
    path: Path
    format: str
    sha256: str
    width: int
    height: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path: Path) -> list[Fixture]:
    if manifest_path.is_symlink():
        raise CodecValidationError("manifest must be a regular file")
    manifest_path = manifest_path.resolve(strict=True)
    if not manifest_path.is_file():
        raise CodecValidationError("manifest must be a regular file")
    raw = manifest_path.read_bytes()
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise CodecValidationError("manifest size is invalid")
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_json_object)
    except CodecValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodecValidationError("manifest JSON is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "fixtures"}
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or not isinstance(payload.get("fixtures"), list)
    ):
        raise CodecValidationError("manifest schema is invalid")

    root = manifest_path.parent
    fixtures = [_parse_fixture(item, root) for item in payload["fixtures"]]
    formats = [fixture.format for fixture in fixtures]
    if len(formats) != len(set(formats)):
        raise CodecValidationError("fixture formats must be unique")
    if set(formats) != REQUIRED_FORMATS:
        raise CodecValidationError("manifest must contain HEIC, JPEG, and PNG")
    return fixtures


def _parse_fixture(value: object, root: Path) -> Fixture:
    if not isinstance(value, dict) or not set(value).issubset(FIXTURE_KEYS):
        raise CodecValidationError("fixture schema is invalid")
    required = {"filename", "format", "sha256", "width", "height"}
    if not required.issubset(value):
        raise CodecValidationError("fixture fields are missing")
    _validate_provenance(value, "generator", "source", MAX_GENERATOR_LENGTH)
    _validate_provenance(value, "license", "ownership", MAX_LICENSE_LENGTH)

    filename = value["filename"]
    format_name = value["format"]
    expected_sha256 = value["sha256"]
    width = value["width"]
    height = value["height"]
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise CodecValidationError("fixture filename is invalid")
    if format_name not in REQUIRED_FORMATS:
        raise CodecValidationError("fixture format is invalid")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise CodecValidationError("fixture SHA-256 is invalid")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise CodecValidationError("fixture dimensions are invalid")

    fixture_candidate = root / filename
    if fixture_candidate.is_symlink():
        raise CodecValidationError("fixture path is invalid")
    fixture_path = fixture_candidate.resolve(strict=True)
    if (
        fixture_path.parent != root
        or not fixture_path.is_file()
    ):
        raise CodecValidationError("fixture path is invalid")
    if sha256_file(fixture_path) != expected_sha256:
        raise CodecValidationError("fixture SHA-256 does not match")
    return Fixture(
        path=fixture_path,
        format=format_name,
        sha256=expected_sha256,
        width=width,
        height=height,
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CodecValidationError("manifest JSON contains a duplicate key")
        result[key] = value
    return result


def _validate_provenance(
    value: dict,
    primary_key: str,
    alternate_key: str,
    maximum_length: int,
) -> None:
    present = [
        value[key]
        for key in (primary_key, alternate_key)
        if key in value
    ]
    if not present or any(not isinstance(item, str) for item in present):
        raise CodecValidationError("fixture provenance is invalid")
    if any(not 1 <= len(item.strip()) <= maximum_length for item in present):
        raise CodecValidationError("fixture provenance is invalid")


def probe_image(path: Path) -> tuple[str, int, int]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        payload = json.loads(completed.stdout)
        streams = payload.get("streams")
        stream = streams[0] if isinstance(streams, list) and len(streams) == 1 else None
        codec = stream.get("codec_name") if isinstance(stream, dict) else None
        width = stream.get("width") if isinstance(stream, dict) else None
        height = stream.get("height") if isinstance(stream, dict) else None
        if (
            not isinstance(codec, str)
            or isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
        ):
            raise CodecValidationError("ffprobe output is invalid")
        return codec, width, height
    except CodecValidationError:
        raise
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        raise CodecValidationError("ffprobe failed") from exc


def aspect_ratio_matches(
    input_width: int,
    input_height: int,
    output_width: int,
    output_height: int,
) -> bool:
    cross_difference = abs(
        output_width * input_height - input_width * output_height
    )
    return cross_difference <= max(input_width, input_height)


def validate_fixture(fixture: Fixture, output_root: Path) -> str:
    before_sha256 = sha256_file(fixture.path)
    input_codec, input_width, input_height = probe_image(fixture.path)
    if input_codec not in EXPECTED_INPUT_CODECS[fixture.format]:
        raise CodecValidationError("input codec does not match manifest format")
    if (input_width, input_height) != (fixture.width, fixture.height):
        raise CodecValidationError("input dimensions do not match manifest")

    output_path = output_root / f"{fixture.format}.jpg"
    command = build_image_preview_command(
        input_path=fixture.path,
        output_path=output_path,
    )
    run_ffmpeg(command)
    if (
        not output_path.is_file()
        or output_path.is_symlink()
        or output_path.stat().st_size <= 0
    ):
        raise CodecValidationError("JPEG preview output is empty")

    output_codec, output_width, output_height = probe_image(output_path)
    if output_codec != "mjpeg":
        raise CodecValidationError("preview output is not JPEG")
    if max(output_width, output_height) > MAX_LONG_EDGE:
        raise CodecValidationError("preview output exceeds the long-edge limit")
    if not aspect_ratio_matches(
        input_width,
        input_height,
        output_width,
        output_height,
    ):
        raise CodecValidationError("preview output aspect ratio changed")
    if sha256_file(fixture.path) != before_sha256 or before_sha256 != fixture.sha256:
        raise CodecValidationError("input fixture changed during conversion")
    return (
        f"{fixture.format}: {input_width}x{input_height} -> "
        f"{output_width}x{output_height} mjpeg"
    )


def validate_manifest(manifest_path: Path) -> list[str]:
    fixtures = load_manifest(manifest_path)
    with tempfile.TemporaryDirectory(prefix="mediavault-codecs-") as temporary:
        output_root = Path(temporary)
        return [validate_fixture(fixture, output_root) for fixture in fixtures]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        results = validate_manifest(arguments.manifest)
    except Exception as exc:
        message = str(exc) if isinstance(exc, CodecValidationError) else "codec validation failed"
        print(f"image codec validation failed: {message}", file=sys.stderr)
        return 1
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
