import hashlib
import json

import pytest

from scripts.generate_test_luts import PRESET_ROOT, generated_files

from app.services.preset_manifest import (
    CUBE_LINE_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    LUT_MAX_BYTES,
    PresetValidationError,
    canonicalize_manifest_payload,
    compress_only_snapshot,
    load_manifest_bytes,
    manifest_document_with_digest,
    validate_cube_bytes,
    validate_cube_file,
)


def manifest_values(**overrides):
    values = {
        "schema_version": 1,
        "preset_id": "identity-v1",
        "display_name": "Identity test",
        "enabled": True,
        "preset_kind": "generated-identity",
        "version": "1",
        "source_reference": "MediaVault generated fixture",
        "terms_reference": "Project source",
        "target_color_space": None,
        "lut_relative_path": "identity-v1.cube",
        "lut_sha256": "a" * 64,
        "file_format": "cube",
        "grid_size": 17,
    }
    values.update(overrides)
    return values


def test_load_manifest_accepts_strict_schema_and_returns_immutable_model():
    manifest = load_manifest_bytes(manifest_document_with_digest(manifest_values()))

    assert manifest.preset_id == "identity-v1"
    assert hashlib.sha256(manifest.canonical_bytes).hexdigest() == manifest.manifest_sha256
    with pytest.raises(AttributeError):
        manifest.enabled = False


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"[]",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b"x" * (MANIFEST_MAX_BYTES + 1),
    ],
)
def test_load_manifest_rejects_unsafe_json(raw):
    with pytest.raises(PresetValidationError):
        load_manifest_bytes(raw)


@pytest.mark.parametrize(
    "override",
    [
        {"unknown": "field"},
        {"schema_version": True},
        {"grid_size": 17.0},
        {"preset_id": "../unsafe"},
        {"display_name": "bad\nname"},
    ],
)
def test_load_manifest_rejects_schema_violations(override):
    with pytest.raises(PresetValidationError):
        load_manifest_bytes(manifest_document_with_digest(manifest_values(**override)))


def test_manifest_digest_uses_jcs_unicode_and_excludes_only_self_hash():
    values = manifest_values(display_name="色変換なし")
    document = manifest_document_with_digest(values)
    loaded = load_manifest_bytes(document)
    decoded = json.loads(document)

    assert loaded.canonical_bytes == canonicalize_manifest_payload(decoded)
    decoded["manifest_sha256"] = "f" * 64
    assert canonicalize_manifest_payload(decoded) == loaded.canonical_bytes


def test_load_manifest_rejects_digest_mismatch():
    document = json.loads(manifest_document_with_digest(manifest_values()))
    document["manifest_sha256"] = "0" * 64

    with pytest.raises(PresetValidationError):
        load_manifest_bytes(json.dumps(document).encode())


def cube_bytes(grid_size=17, rows=None, header=None):
    count = grid_size**3 if rows is None else rows
    lines = [header or f"LUT_3D_SIZE {grid_size}"]
    lines.extend("0.0 0.5 1.0" for _ in range(count))
    return ("\n".join(lines) + "\n").encode()


def test_validate_cube_accepts_exact_supported_3d_grid_and_digest():
    raw = cube_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    metadata = validate_cube_bytes(raw, expected_sha256=digest, expected_grid_size=17)

    assert metadata.grid_size == 17
    assert metadata.size_bytes == len(raw)


@pytest.mark.parametrize(
    "raw",
    [
        cube_bytes(header="LUT_3D_SIZE nope"),
        cube_bytes(header="LUT_1D_SIZE 17"),
        cube_bytes(header="LUT_3D_SIZE 16"),
        cube_bytes(rows=17**3 - 1),
        cube_bytes(rows=17**3 + 1),
        b"LUT_3D_SIZE 17\nNaN 0 0\n",
        b"LUT_3D_SIZE 17\nInf 0 0\n",
        b"LUT_3D_SIZE 17\nLUT_3D_SIZE 17\n",
        b"x" * (LUT_MAX_BYTES + 1),
    ],
)
def test_validate_cube_rejects_malformed_or_unbounded_input(raw):
    with pytest.raises(PresetValidationError):
        validate_cube_bytes(raw)


def test_validate_cube_rejects_manifest_digest_or_grid_mismatch():
    raw = cube_bytes()

    with pytest.raises(PresetValidationError):
        validate_cube_bytes(raw, expected_sha256="0" * 64)
    with pytest.raises(PresetValidationError):
        validate_cube_bytes(raw, expected_grid_size=33)


@pytest.mark.parametrize(
    "header",
    [
        "TITLE unquoted",
        'TITLE "first"\nTITLE "second"',
        "DOMAIN_MIN not-a-number 0 0",
        "DOMAIN_MIN 0 0",
        "DOMAIN_MIN 0 0 0\nDOMAIN_MIN 0 0 0",
        "DOMAIN_MAX Inf 1 1",
    ],
)
def test_validate_cube_rejects_malformed_optional_headers(header):
    raw = cube_bytes(header=f"{header}\nLUT_3D_SIZE 17")

    with pytest.raises(PresetValidationError):
        validate_cube_bytes(raw)


def test_validate_cube_file_streams_exact_bytes_and_enforces_configured_limits(tmp_path):
    raw = (
        'TITLE "Streaming test"\n'
        "DOMAIN_MIN 0 0 0\n"
        "DOMAIN_MAX 1 1 1\n"
    ).encode() + cube_bytes()
    path = tmp_path / "streaming.cube"
    path.write_bytes(raw)

    metadata = validate_cube_file(
        path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_grid_size=17,
    )

    assert metadata.size_bytes == len(raw)
    with pytest.raises(PresetValidationError):
        validate_cube_file(path, max_bytes=len(raw) - 1)
    path.write_bytes(b"#" + b"x" * CUBE_LINE_MAX_BYTES + b"\n")
    with pytest.raises(PresetValidationError):
        validate_cube_file(path)


def test_generated_preset_files_are_reproducible_and_valid():
    for path, expected in generated_files().items():
        assert path.read_bytes() == expected
        if path.suffix == ".cube":
            validate_cube_bytes(expected, expected_grid_size=17)
        else:
            manifest = load_manifest_bytes(expected)
            assert manifest.target_color_space is None
            assert "Apple Log" not in manifest.display_name
            assert "Rec.709" not in manifest.display_name


def test_compress_only_is_virtual_and_has_no_external_lut_identity():
    snapshot = compress_only_snapshot()

    assert snapshot.version == "1"
    assert snapshot.registry_classification == "valid"
    assert snapshot.manifest_canonical_bytes is None
    assert snapshot.manifest_sha256 is None
    assert snapshot.expected_lut_sha256 is None
    assert snapshot.source_root_kind is None
    assert snapshot.source_relative_lut_path is None
    assert snapshot.target_color_space is None
    combined_metadata = " ".join(
        (snapshot.display_name, snapshot.source_reference, snapshot.terms_reference)
    )
    assert "Apple Log" not in combined_metadata
    assert "Rec.709" not in combined_metadata
