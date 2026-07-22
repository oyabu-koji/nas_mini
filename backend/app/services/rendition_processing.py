from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.assets import get_asset
from app.repositories.jobs import (
    set_job_done_in_transaction,
    set_job_failed_in_transaction,
)
from app.repositories.renditions import (
    NONTERMINAL_STATES,
    fail_rendition_in_transaction,
    get_rendition_by_job,
    restart_rendition_validation_in_transaction,
    transition_rendition,
)
from app.services.ffmpeg import PreviewGenerationError, build_video_preview_command, run_ffmpeg
from app.services.lut_snapshot import (
    LutSnapshot,
    LutSnapshotError,
    cleanup_lut_snapshot,
    create_lut_snapshot,
    verify_lut_snapshot,
)
from app.services.rendition_finalizer import (
    RenditionFinalizationError,
    TransformEvidence,
    finalize_rendition_output,
)
from app.services.storage import (
    StorageError,
    cleanup_uncommitted_rendition_output,
    generate_rendition_candidate_path,
    resolve_media_path,
)


class RenditionProcessingError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def process_rendition_job(*, settings: Settings, job: dict[str, Any]) -> bool:
    rendition = _prepare_relation(settings=settings, job=job)
    if rendition is None:
        return True
    if rendition["state"] in {"ready", "failed", "superseded"}:
        cleanup_lut_snapshot(settings=settings, rendition_id=rendition["id"])
        return True

    candidate_path: Path | None = None
    lut_snapshot: LutSnapshot | None = None
    has_lut = False
    try:
        if rendition.pop("recovered_from_state", None) == "finalizing":
            cleanup_uncommitted_rendition_output(settings.media_root, rendition["id"])
        evidence, lut_snapshot = _prepare_transform(settings=settings, rendition=rendition)
        has_lut = lut_snapshot is not None
        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            with conn:
                transition_rendition(
                    conn, rendition_id=rendition["id"], new_state="rendering"
                )

        asset = _load_asset(settings=settings, asset_id=int(rendition["asset_id"]))
        input_path = _original_path(settings=settings, asset=asset)
        candidate_path = generate_rendition_candidate_path(settings.media_root, rendition["id"])
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        if lut_snapshot is not None:
            verify_lut_snapshot(
                lut_snapshot, expected_sha256=str(rendition["expected_lut_sha256"])
            )
        command = build_video_preview_command(
            input_path=input_path,
            output_path=candidate_path,
            lut_path=lut_snapshot.path if lut_snapshot else None,
        )
        try:
            run_ffmpeg(command)
        except PreviewGenerationError as exc:
            raise RenditionProcessingError(
                "lut_application_failed" if has_lut else "rendition_storage_failed"
            ) from exc
        if not candidate_path.is_file() or candidate_path.stat().st_size <= 0:
            raise RenditionProcessingError("rendition_storage_failed")

        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            with conn:
                transition_rendition(
                    conn, rendition_id=rendition["id"], new_state="finalizing"
                )
        finalize_rendition_output(
            settings=settings,
            job_id=int(job["id"]),
            rendition_id=rendition["id"],
            candidate_path=candidate_path,
            evidence=evidence,
        )
        candidate_path = None
    except LutSnapshotError:
        _fail_processing(
            settings=settings,
            job_id=int(job["id"]),
            rendition_id=rendition["id"],
            code="lut_preset_source_changed",
        )
    except RenditionFinalizationError as exc:
        _fail_processing(
            settings=settings,
            job_id=int(job["id"]),
            rendition_id=rendition["id"],
            code=exc.code,
        )
    except RenditionProcessingError as exc:
        _fail_processing(
            settings=settings,
            job_id=int(job["id"]),
            rendition_id=rendition["id"],
            code=exc.code,
        )
    except (OSError, StorageError):
        _fail_processing(
            settings=settings,
            job_id=int(job["id"]),
            rendition_id=rendition["id"],
            code="rendition_storage_failed",
        )
    except Exception:
        _fail_processing(
            settings=settings,
            job_id=int(job["id"]),
            rendition_id=rendition["id"],
            code="rendition_database_failed",
        )
    finally:
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)
        cleanup_lut_snapshot(settings=settings, rendition_id=rendition["id"])
    return True


def _prepare_relation(*, settings: Settings, job: dict[str, Any]) -> dict[str, Any] | None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rendition = get_rendition_by_job(conn, int(job["id"]))
            if rendition is None:
                set_job_failed_in_transaction(
                    conn, job_id=int(job["id"]), error_message="rendition_relation_invalid"
                )
                conn.commit()
                return None
            payload_id = _payload_rendition_id(job.get("payload_json"))
            relation_valid = (
                job.get("job_type") == "rendition"
                and job.get("asset_id") == rendition["asset_id"]
                and payload_id == rendition["id"]
            )
            if not relation_valid:
                if rendition["state"] in NONTERMINAL_STATES:
                    fail_rendition_in_transaction(
                        conn,
                        rendition_id=rendition["id"],
                        error_code="rendition_relation_invalid",
                    )
                set_job_failed_in_transaction(
                    conn, job_id=int(job["id"]), error_message="rendition_relation_invalid"
                )
                conn.commit()
                return None
            if rendition["state"] in {"ready", "superseded"}:
                set_job_done_in_transaction(conn, job_id=int(job["id"]))
                conn.commit()
                return rendition
            if rendition["state"] == "failed":
                set_job_failed_in_transaction(
                    conn,
                    job_id=int(job["id"]),
                    error_message=str(rendition["error_code"]),
                )
                conn.commit()
                return rendition
            recovered_from_state = rendition["state"]
            restart_rendition_validation_in_transaction(
                conn, rendition_id=rendition["id"]
            )
            conn.commit()
            rendition["state"] = "validating"
            rendition["recovered_from_state"] = recovered_from_state
            return rendition
        except Exception:
            conn.rollback()
            raise


def _payload_rendition_id(payload_json: Any) -> str | None:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    value = payload.get("rendition_id") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def _prepare_transform(
    *, settings: Settings, rendition: dict[str, Any]
) -> tuple[TransformEvidence, LutSnapshot | None]:
    classification = rendition["registry_classification"]
    requested_id = rendition["requested_preset_id"]
    if classification == "registered_invalid":
        raise RenditionProcessingError("lut_preset_registered_invalid")
    if classification in {"absent", "disabled"}:
        return (
            TransformEvidence(
                applied_preset_id="compress-only",
                transform_kind="none",
                color_transform_status="unavailable",
                color_transform_error_code="lut_preset_unavailable",
            ),
            None,
        )
    if classification != "valid":
        raise RenditionProcessingError("lut_preset_registered_invalid")
    if requested_id == "compress-only":
        return (
            TransformEvidence(
                applied_preset_id="compress-only",
                transform_kind="none",
                color_transform_status="not_requested",
                color_transform_error_code=None,
            ),
            None,
        )

    canonical = rendition.get("manifest_canonical_bytes")
    manifest_sha256 = rendition.get("manifest_sha256")
    expected_lut_sha256 = rendition.get("expected_lut_sha256")
    source_kind = rendition.get("source_root_kind")
    source_path = rendition.get("source_relative_lut_path")
    if not all(
        (
            isinstance(canonical, (bytes, bytearray, memoryview)),
            isinstance(manifest_sha256, str),
            isinstance(expected_lut_sha256, str),
            isinstance(source_kind, str),
            isinstance(source_path, str),
        )
    ) or hashlib.sha256(bytes(canonical)).hexdigest() != manifest_sha256:
        raise RenditionProcessingError("lut_preset_registered_invalid")
    snapshot = create_lut_snapshot(
        settings=settings,
        rendition_id=rendition["id"],
        source_root_kind=source_kind,
        source_relative_path=source_path,
        expected_sha256=expected_lut_sha256,
    )
    return (
        TransformEvidence(
            applied_preset_id=requested_id,
            transform_kind="lut",
            color_transform_status="applied",
            color_transform_error_code=None,
        ),
        snapshot,
    )


def _load_asset(*, settings: Settings, asset_id: int) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        asset = get_asset(conn, asset_id)
    if asset is None:
        raise RenditionProcessingError("rendition_asset_not_eligible")
    return asset


def _original_path(*, settings: Settings, asset: dict[str, Any]) -> Path:
    relative = asset.get("original_path")
    if not isinstance(relative, str) or not relative.startswith("originals/"):
        raise RenditionProcessingError("rendition_asset_not_eligible")
    path = resolve_media_path(settings.media_root, relative)
    if not path.is_file():
        raise RenditionProcessingError("rendition_asset_not_eligible")
    return path


def _fail_processing(
    *, settings: Settings, job_id: int, rendition_id: str, code: str
) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        with conn:
            rendition = get_rendition_by_job(conn, job_id)
            if rendition is not None and rendition["state"] in NONTERMINAL_STATES:
                fail_rendition_in_transaction(
                    conn, rendition_id=rendition_id, error_code=code
                )
            set_job_failed_in_transaction(conn, job_id=job_id, error_message=code)
