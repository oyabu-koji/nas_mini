import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import validate_image_codecs as validator


def _write_manifest(
    root: Path,
    *,
    fixture_overrides: dict | None = None,
    formats: tuple[str, ...] = ("heic", "jpeg", "png"),
) -> Path:
    root.mkdir()
    fixtures = []
    for format_name in formats:
        filename = f"fixture.{format_name}"
        content = f"fixture-{format_name}".encode()
        (root / filename).write_bytes(content)
        fixture = {
            "filename": filename,
            "format": format_name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "width": 4,
            "height": 3,
            "generator": "offline generator",
            "license": "repository-owned",
        }
        if format_name == formats[0]:
            fixture.update(fixture_overrides or {})
        fixtures.append(fixture)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "fixtures": fixtures}),
        encoding="utf-8",
    )
    return manifest


def test_manifest_accepts_provenance_boundaries(tmp_path):
    manifest = _write_manifest(
        tmp_path / "fixtures",
        fixture_overrides={
            "generator": "g" * 512,
            "license": "l" * 256,
        },
    )

    assert {fixture.format for fixture in validator.load_manifest(manifest)} == {
        "heic",
        "jpeg",
        "png",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"generator": ""},
        {"source": " ", "generator": ""},
        {"generator": "g" * 513},
        {"license": ""},
        {"ownership": " ", "license": ""},
        {"license": "l" * 257},
    ],
)
def test_manifest_rejects_invalid_provenance(tmp_path, overrides):
    manifest = _write_manifest(
        tmp_path / "fixtures",
        fixture_overrides=overrides,
    )

    with pytest.raises(validator.CodecValidationError, match="provenance"):
        validator.load_manifest(manifest)


def test_manifest_rejects_path_traversal_duplicate_missing_and_digest(tmp_path):
    outside = tmp_path / "outside.heic"
    outside.write_bytes(b"outside")
    traversal = _write_manifest(
        tmp_path / "traversal",
        fixture_overrides={
            "filename": "../outside.heic",
            "sha256": hashlib.sha256(b"outside").hexdigest(),
        },
    )
    with pytest.raises(validator.CodecValidationError, match="filename"):
        validator.load_manifest(traversal)

    duplicate = _write_manifest(
        tmp_path / "duplicate",
        formats=("heic", "heic", "png"),
    )
    with pytest.raises(validator.CodecValidationError):
        validator.load_manifest(duplicate)

    missing = _write_manifest(tmp_path / "missing", formats=("heic", "jpeg"))
    with pytest.raises(validator.CodecValidationError, match="HEIC, JPEG, and PNG"):
        validator.load_manifest(missing)

    digest = _write_manifest(
        tmp_path / "digest",
        fixture_overrides={"sha256": "0" * 64},
    )
    with pytest.raises(validator.CodecValidationError, match="does not match"):
        validator.load_manifest(digest)


def test_manifest_rejects_duplicate_json_keys_and_boolean_schema_version(tmp_path):
    root = tmp_path / "duplicate-key"
    root.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text(
        '{"schema_version":1,"schema_version":1,"fixtures":[]}',
        encoding="utf-8",
    )
    with pytest.raises(validator.CodecValidationError, match="duplicate key"):
        validator.load_manifest(manifest)

    manifest.write_text(
        '{"schema_version":true,"fixtures":[]}',
        encoding="utf-8",
    )
    with pytest.raises(validator.CodecValidationError, match="schema"):
        validator.load_manifest(manifest)


def test_validate_fixture_uses_production_adapter_and_checks_geometry(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "fixture.png"
    input_path.write_bytes(b"fixture")
    fixture = validator.Fixture(
        path=input_path,
        format="png",
        sha256=validator.sha256_file(input_path),
        width=4,
        height=3,
    )
    output_root = tmp_path / "output"
    output_root.mkdir()
    build = Mock(
        side_effect=lambda **values: ["ffmpeg", str(values["output_path"])]
    )
    monkeypatch.setattr(validator, "build_image_preview_command", build)

    def run(command):
        Path(command[-1]).write_bytes(b"jpeg-output")

    run_adapter = Mock(side_effect=run)
    monkeypatch.setattr(validator, "run_ffmpeg", run_adapter)
    probe = Mock(side_effect=[("png", 4, 3), ("mjpeg", 4, 3)])
    monkeypatch.setattr(validator, "probe_image", probe)

    assert validator.validate_fixture(fixture, output_root) == (
        "png: 4x3 -> 4x3 mjpeg"
    )
    build.assert_called_once_with(
        input_path=input_path,
        output_path=output_root / "png.jpg",
    )
    run_adapter.assert_called_once()


@pytest.mark.parametrize(
    ("output_dimensions", "expected"),
    [
        ((2048, 1536), True),
        ((2048, 1535), True),
        ((2048, 1500), False),
    ],
)
def test_aspect_ratio_integer_scaling_tolerance(output_dimensions, expected):
    assert validator.aspect_ratio_matches(
        4,
        3,
        output_dimensions[0],
        output_dimensions[1],
    ) is expected


def test_validate_fixture_fails_closed_for_non_jpeg(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "fixture.png"
    input_path.write_bytes(b"fixture")
    fixture = validator.Fixture(
        path=input_path,
        format="png",
        sha256=validator.sha256_file(input_path),
        width=4,
        height=3,
    )
    output_root = tmp_path / "output"
    output_root.mkdir()
    monkeypatch.setattr(
        validator,
        "build_image_preview_command",
        lambda **values: ["ffmpeg", str(values["output_path"])],
    )

    def render(command):
        Path(command[-1]).write_bytes(b"not-jpeg")

    monkeypatch.setattr(validator, "run_ffmpeg", render)
    monkeypatch.setattr(
        validator,
        "probe_image",
        lambda path: ("png", 4, 3),
    )

    with pytest.raises(validator.CodecValidationError, match="not JPEG"):
        validator.validate_fixture(fixture, output_root)


def test_validate_fixture_fails_closed_when_conversion_mutates_input(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "fixture.png"
    input_path.write_bytes(b"fixture")
    fixture = validator.Fixture(
        path=input_path,
        format="png",
        sha256=validator.sha256_file(input_path),
        width=4,
        height=3,
    )
    output_root = tmp_path / "output"
    output_root.mkdir()
    monkeypatch.setattr(
        validator,
        "build_image_preview_command",
        lambda **values: ["ffmpeg", str(values["output_path"])],
    )

    def mutate(command):
        Path(command[-1]).write_bytes(b"jpeg-output")
        input_path.write_bytes(b"changed")

    monkeypatch.setattr(validator, "run_ffmpeg", mutate)
    probe = Mock(side_effect=[("png", 4, 3), ("mjpeg", 4, 3)])
    monkeypatch.setattr(validator, "probe_image", probe)

    with pytest.raises(validator.CodecValidationError, match="changed"):
        validator.validate_fixture(fixture, output_root)
