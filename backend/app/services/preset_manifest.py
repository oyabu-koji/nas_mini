from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal

import rfc8785


MANIFEST_MAX_BYTES = 65_536
LUT_MAX_BYTES = 16 * 1024 * 1024
CUBE_LINE_MAX_BYTES = 4096
SUPPORTED_GRID_SIZES = frozenset({17, 33, 65})
PRESET_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "preset_id",
        "display_name",
        "enabled",
        "preset_kind",
        "version",
        "source_reference",
        "terms_reference",
        "target_color_space",
        "lut_relative_path",
        "lut_sha256",
        "file_format",
        "grid_size",
        "manifest_sha256",
    }
)
PresetKind = Literal["generated-identity", "generated-test", "custom"]
RegistryClassification = Literal["absent", "disabled", "registered_invalid", "valid"]


class PresetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PresetManifest:
    schema_version: int
    preset_id: str
    display_name: str
    enabled: bool
    preset_kind: PresetKind
    version: str
    source_reference: str
    terms_reference: str
    target_color_space: str | None
    lut_relative_path: str
    lut_sha256: str
    file_format: Literal["cube"]
    grid_size: int
    manifest_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True)
class CubeMetadata:
    grid_size: int
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PresetSnapshot:
    requested_preset_id: str
    registry_classification: RegistryClassification
    applied_preset_id: str | None
    display_name: str | None
    preset_kind: str | None
    version: str | None
    source_reference: str | None
    terms_reference: str | None
    target_color_space: str | None
    manifest_canonical_bytes: bytes | None
    manifest_sha256: str | None
    expected_lut_sha256: str | None
    file_format: str | None
    grid_size: int | None
    source_root_kind: str | None
    source_relative_lut_path: str | None


@dataclass(frozen=True)
class VirtualPresetSnapshot(PresetSnapshot):
    def __post_init__(self) -> None:
        if self.requested_preset_id != "compress-only":
            raise ValueError("virtual preset must be compress-only")
        if self.registry_classification != "valid" or self.applied_preset_id != "compress-only":
            raise ValueError("virtual preset must be available")
        nullable_fields = (
            self.manifest_canonical_bytes,
            self.manifest_sha256,
            self.expected_lut_sha256,
            self.file_format,
            self.grid_size,
            self.source_root_kind,
            self.source_relative_lut_path,
            self.target_color_space,
        )
        if any(value is not None for value in nullable_fields):
            raise ValueError("virtual preset cannot contain external LUT identity")


def compress_only_snapshot() -> VirtualPresetSnapshot:
    return VirtualPresetSnapshot(
        requested_preset_id="compress-only",
        registry_classification="valid",
        applied_preset_id="compress-only",
        display_name="Compress only",
        preset_kind="compress-only",
        version="1",
        source_reference="MediaVault built-in",
        terms_reference="Project source",
        target_color_space=None,
        manifest_canonical_bytes=None,
        manifest_sha256=None,
        expected_lut_sha256=None,
        file_format=None,
        grid_size=None,
        source_root_kind=None,
        source_relative_lut_path=None,
    )


def load_manifest(path: Path, *, max_bytes: int = MANIFEST_MAX_BYTES) -> PresetManifest:
    try:
        with path.open("rb") as source:
            raw = source.read(max_bytes + 1)
    except OSError as exc:
        raise PresetValidationError("manifest cannot be read") from exc
    return load_manifest_bytes(raw, max_bytes=max_bytes)


def load_manifest_bytes(raw: bytes, *, max_bytes: int = MANIFEST_MAX_BYTES) -> PresetManifest:
    if len(raw) > max_bytes:
        raise PresetValidationError("manifest is too large")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise PresetValidationError("manifest BOM is not allowed")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PresetValidationError("manifest must be UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                PresetValidationError("manifest numeric value is invalid")
            ),
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PresetValidationError("manifest JSON is invalid") from exc
    if not isinstance(value, dict):
        raise PresetValidationError("manifest root must be an object")

    _validate_manifest_object(value)
    canonical_bytes = canonicalize_manifest_payload(value)
    calculated_digest = hashlib.sha256(canonical_bytes).hexdigest()
    if calculated_digest != value["manifest_sha256"]:
        raise PresetValidationError("manifest digest does not match")

    return PresetManifest(
        schema_version=value["schema_version"],
        preset_id=value["preset_id"],
        display_name=value["display_name"],
        enabled=value["enabled"],
        preset_kind=value["preset_kind"],
        version=value["version"],
        source_reference=value["source_reference"],
        terms_reference=value["terms_reference"],
        target_color_space=value["target_color_space"],
        lut_relative_path=value["lut_relative_path"],
        lut_sha256=value["lut_sha256"],
        file_format=value["file_format"],
        grid_size=value["grid_size"],
        manifest_sha256=value["manifest_sha256"],
        canonical_bytes=canonical_bytes,
    )


def canonicalize_manifest_payload(value: dict[str, Any]) -> bytes:
    digest_input = {key: member for key, member in value.items() if key != "manifest_sha256"}
    try:
        return rfc8785.dumps(digest_input)
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError) as exc:
        raise PresetValidationError("manifest cannot be canonicalized") from exc


def manifest_document_with_digest(value: dict[str, Any]) -> bytes:
    """Build deterministic manifest bytes for generated fixtures and tests."""
    without_digest = {key: member for key, member in value.items() if key != "manifest_sha256"}
    digest = hashlib.sha256(canonicalize_manifest_payload(without_digest)).hexdigest()
    complete = {**without_digest, "manifest_sha256": digest}
    return (json.dumps(complete, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def validate_cube_file(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_grid_size: int | None = None,
    max_bytes: int = LUT_MAX_BYTES,
) -> CubeMetadata:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= max_bytes:
            raise PresetValidationError("LUT file is invalid")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            return _validate_cube_stream(
                source,
                max_bytes=max_bytes,
                expected_sha256=expected_sha256,
                expected_grid_size=expected_grid_size,
            )
    except OSError as exc:
        raise PresetValidationError("LUT file cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_cube_bytes(
    raw: bytes,
    *,
    expected_sha256: str | None = None,
    expected_grid_size: int | None = None,
    max_bytes: int = LUT_MAX_BYTES,
) -> CubeMetadata:
    if not raw or len(raw) > max_bytes:
        raise PresetValidationError("LUT file size is invalid")
    return _validate_cube_stream(
        io.BytesIO(raw),
        max_bytes=max_bytes,
        expected_sha256=expected_sha256,
        expected_grid_size=expected_grid_size,
    )


def _validate_cube_stream(
    source: BinaryIO,
    *,
    max_bytes: int,
    expected_sha256: str | None,
    expected_grid_size: int | None,
) -> CubeMetadata:
    declared_grid: int | None = None
    data_rows = 0
    total_bytes = 0
    digest = hashlib.sha256()
    seen_title = False
    seen_domains: set[str] = set()
    while True:
        raw_line = source.readline(CUBE_LINE_MAX_BYTES + 1)
        if not raw_line:
            break
        total_bytes += len(raw_line)
        if total_bytes > max_bytes or len(raw_line) > CUBE_LINE_MAX_BYTES:
            raise PresetValidationError("LUT file size is invalid")
        digest.update(raw_line)
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PresetValidationError("LUT file must be UTF-8") from exc
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        directive = tokens[0].upper()
        if directive == "LUT_1D_SIZE":
            raise PresetValidationError("1D LUT is not supported")
        if directive == "LUT_3D_SIZE":
            if declared_grid is not None or len(tokens) != 2:
                raise PresetValidationError("LUT_3D_SIZE is invalid")
            try:
                declared_grid = int(tokens[1])
            except ValueError as exc:
                raise PresetValidationError("LUT_3D_SIZE is invalid") from exc
            if declared_grid not in SUPPORTED_GRID_SIZES:
                raise PresetValidationError("LUT grid is unsupported")
            continue
        if directive == "TITLE":
            if data_rows:
                raise PresetValidationError("LUT directive follows data")
            title = stripped[len(tokens[0]) :].strip()
            if seen_title or re.fullmatch(r'"[^"\r\n]*"', title) is None:
                raise PresetValidationError("LUT TITLE is invalid")
            seen_title = True
            continue
        if directive in {"DOMAIN_MIN", "DOMAIN_MAX"}:
            if data_rows or directive in seen_domains or len(tokens) != 4:
                raise PresetValidationError("LUT domain is invalid")
            try:
                domain_values = tuple(float(token) for token in tokens[1:])
            except ValueError as exc:
                raise PresetValidationError("LUT domain is invalid") from exc
            if not all(math.isfinite(value) for value in domain_values):
                raise PresetValidationError("LUT domain must be finite")
            seen_domains.add(directive)
            continue
        if declared_grid is None or len(tokens) != 3:
            raise PresetValidationError("LUT data row is invalid")
        try:
            values = tuple(float(token) for token in tokens)
        except ValueError as exc:
            raise PresetValidationError("LUT data row is invalid") from exc
        if not all(math.isfinite(value) for value in values):
            raise PresetValidationError("LUT data must be finite")
        data_rows += 1

    if declared_grid is None or data_rows != declared_grid**3:
        raise PresetValidationError("LUT data row count does not match grid")
    if expected_grid_size is not None and declared_grid != expected_grid_size:
        raise PresetValidationError("LUT grid does not match manifest")
    calculated_digest = digest.hexdigest()
    if expected_sha256 is not None and calculated_digest != expected_sha256:
        raise PresetValidationError("LUT digest does not match manifest")
    return CubeMetadata(
        grid_size=declared_grid,
        sha256=calculated_digest,
        size_bytes=total_bytes,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PresetValidationError("manifest contains duplicate fields")
        result[key] = value
    return result


def _validate_manifest_object(value: dict[str, Any]) -> None:
    if set(value) != ALLOWED_MANIFEST_FIELDS:
        raise PresetValidationError("manifest fields are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PresetValidationError("schema_version is invalid")
    if type(value["enabled"]) is not bool:
        raise PresetValidationError("enabled is invalid")
    if type(value["grid_size"]) is not int or value["grid_size"] not in SUPPORTED_GRID_SIZES:
        raise PresetValidationError("grid_size is invalid")

    _bounded_string(value["preset_id"], "preset_id", 1, 64)
    if not PRESET_ID_PATTERN.fullmatch(value["preset_id"]):
        raise PresetValidationError("preset_id is invalid")
    _bounded_string(value["display_name"], "display_name", 1, 120, reject_control=True)
    if value["preset_kind"] not in {"generated-identity", "generated-test", "custom"}:
        raise PresetValidationError("preset_kind is invalid")
    _bounded_string(value["version"], "version", 1, 64)
    _bounded_string(value["source_reference"], "source_reference", 1, 256, reject_control=True)
    _bounded_string(value["terms_reference"], "terms_reference", 1, 256, reject_control=True)
    if value["target_color_space"] is not None:
        _bounded_string(value["target_color_space"], "target_color_space", 0, 64, reject_control=True)
    _validate_relative_lut_path(value["lut_relative_path"])
    _sha256(value["lut_sha256"], "lut_sha256")
    if value["file_format"] != "cube":
        raise PresetValidationError("file_format is invalid")
    _sha256(value["manifest_sha256"], "manifest_sha256")


def _bounded_string(
    value: Any,
    field: str,
    minimum: int,
    maximum: int,
    *,
    reject_control: bool = False,
) -> None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise PresetValidationError(f"{field} is invalid")
    if reject_control and any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PresetValidationError(f"{field} is invalid")


def _sha256(value: Any, field: str) -> None:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise PresetValidationError(f"{field} is invalid")


def _validate_relative_lut_path(value: Any) -> None:
    _bounded_string(value, "lut_relative_path", 1, 256)
    path = PurePosixPath(value)
    if path.is_absolute() or path.suffix.lower() != ".cube":
        raise PresetValidationError("lut_relative_path is invalid")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise PresetValidationError("lut_relative_path is invalid")
