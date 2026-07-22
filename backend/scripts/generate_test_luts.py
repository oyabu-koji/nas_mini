from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from app.services.preset_manifest import manifest_document_with_digest


GRID_SIZE = 17
PRESET_ROOT = Path(__file__).parents[1] / "assets/lut/presets"
PRESETS = (
    {
        "preset_id": "identity-v1",
        "display_name": "Identity test",
        "preset_kind": "generated-identity",
        "transform": "identity",
    },
    {
        "preset_id": "test-red-blue-swap-v1",
        "display_name": "Red/blue swap test",
        "preset_kind": "generated-test",
        "transform": "red-blue-swap",
    },
)


def generate_cube_bytes(*, preset_id: str, transform: str) -> bytes:
    lines = [
        f'TITLE "MediaVault {preset_id}"',
        f"LUT_3D_SIZE {GRID_SIZE}",
        "DOMAIN_MIN 0.000000 0.000000 0.000000",
        "DOMAIN_MAX 1.000000 1.000000 1.000000",
    ]
    maximum = GRID_SIZE - 1
    for blue_index in range(GRID_SIZE):
        for green_index in range(GRID_SIZE):
            for red_index in range(GRID_SIZE):
                red = red_index / maximum
                green = green_index / maximum
                blue = blue_index / maximum
                if transform == "identity":
                    output = (red, green, blue)
                elif transform == "red-blue-swap":
                    output = (blue, green, red)
                else:
                    raise ValueError("unsupported generated transform")
                lines.append(" ".join(f"{channel:.6f}" for channel in output))
    return ("\n".join(lines) + "\n").encode("ascii")


def generated_files() -> dict[Path, bytes]:
    files: dict[Path, bytes] = {}
    for preset in PRESETS:
        preset_id = preset["preset_id"]
        cube_name = f"{preset_id}.cube"
        cube_bytes = generate_cube_bytes(preset_id=preset_id, transform=preset["transform"])
        manifest = {
            "schema_version": 1,
            "preset_id": preset_id,
            "display_name": preset["display_name"],
            "enabled": True,
            "preset_kind": preset["preset_kind"],
            "version": "1",
            "source_reference": "MediaVault deterministic test generator",
            "terms_reference": "Project source",
            "target_color_space": None,
            "lut_relative_path": cube_name,
            "lut_sha256": hashlib.sha256(cube_bytes).hexdigest(),
            "file_format": "cube",
            "grid_size": GRID_SIZE,
        }
        root = PRESET_ROOT / preset_id
        files[root / cube_name] = cube_bytes
        files[root / "manifest.json"] = manifest_document_with_digest(manifest)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    mismatches: list[Path] = []
    for path, expected in generated_files().items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
        elif not path.is_file() or path.read_bytes() != expected:
            mismatches.append(path)
    if mismatches:
        for path in mismatches:
            print(path.relative_to(PRESET_ROOT))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
