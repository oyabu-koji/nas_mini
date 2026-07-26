from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable

from app.core.settings import Settings
from app.db.connection import connect
from app.repositories.formal_previews import (
    get_formal_preview_attempt_by_job,
    insert_or_get_formal_preview_attempt,
    save_detection_snapshot,
    save_preset_snapshot,
    transition_formal_preview_attempt,
)
from app.services.apple_log_detector import (
    DetectionResult,
    probe_and_classify,
    read_ffprobe_version,
)
from app.services.bounded_subprocess import BoundedProcessError
from app.services.detector_manifest import (
    DetectorManifest,
    DetectorValidationError,
    load_certificate_summary,
    load_detector_manifest,
    load_rule_input,
)
from app.services.ffmpeg import (
    PreviewGenerationError,
    build_video_preview_command,
    run_ffmpeg,
)
from app.services.formal_preview_finalizer import (
    FormalPreviewFinalizationError,
    finalize_formal_preview_output,
    inspect_formal_preview_candidate,
    resolve_verified_original,
)
from app.services.initial_release_guard import (
    GENERATED_APPLE_LOG_PRESET_ID,
)
from app.services.preset_manifest import PresetSnapshot, compress_only_snapshot
from app.services.preset_registry import classify_preset
from app.services.storage import (
    StorageError,
    cleanup_formal_preview_candidate,
    generate_formal_preview_candidate_path,
)


@dataclass(frozen=True)
class FormalPreviewPayload:
    asset_id: int
    preview_generation: int
    detection_required: bool


class FormalPreviewProcessingError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


ProbeRunner = Callable[[Path, DetectorManifest], DetectionResult]
RenderRunner = Callable[[list[str]], None]


def parse_formal_preview_payload(job: dict[str, Any]) -> FormalPreviewPayload:
    try:
        value = json.loads(job.get("payload_json"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise FormalPreviewProcessingError("formal_preview_relation_invalid") from exc
    if not isinstance(value, dict):
        raise FormalPreviewProcessingError("formal_preview_relation_invalid")
    asset_id = value.get("asset_id")
    generation = value.get("preview_generation")
    detection_required = value.get("detection_required")
    if (
        not isinstance(asset_id, int)
        or isinstance(asset_id, bool)
        or asset_id <= 0
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or detection_required is not True
    ):
        raise FormalPreviewProcessingError("formal_preview_relation_invalid")
    return FormalPreviewPayload(
        asset_id=asset_id,
        preview_generation=generation,
        detection_required=detection_required,
    )


def prepare_formal_preview_attempt(
    *, settings: Settings, job: dict[str, Any]
) -> dict[str, Any] | None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current_job_row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            if current_job_row is None:
                raise FormalPreviewProcessingError("formal_preview_relation_invalid")
            current_job = dict(current_job_row)
            asset_id = current_job.get("asset_id")
            job_generation = current_job.get("preview_generation")
            if (
                not isinstance(asset_id, int)
                or not isinstance(job_generation, int)
                or isinstance(job_generation, bool)
                or job_generation < 1
            ):
                _settle_job(
                    conn,
                    job_id=int(job["id"]),
                    status="failed",
                    error_code="formal_preview_relation_invalid",
                )
                conn.commit()
                return None
            asset_row = conn.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
            if asset_row is None:
                _settle_job(
                    conn,
                    job_id=int(job["id"]),
                    status="failed",
                    error_code="formal_preview_relation_invalid",
                )
                conn.commit()
                return None
            asset = dict(asset_row)
            attempt, _created = insert_or_get_formal_preview_attempt(
                conn,
                asset_id=asset_id,
                job_id=int(job["id"]),
                preview_generation=job_generation,
            )
            terminal = _converge_terminal_attempt(conn, attempt=attempt)
            if terminal:
                conn.commit()
                return None
            try:
                payload = parse_formal_preview_payload(current_job)
            except FormalPreviewProcessingError:
                transition_formal_preview_attempt(
                    conn,
                    attempt_id=attempt["id"],
                    new_state="failed",
                    failure_code="formal_preview_relation_invalid",
                )
                _settle_job(
                    conn,
                    job_id=int(job["id"]),
                    status="failed",
                    error_code="formal_preview_relation_invalid",
                )
                conn.execute(
                    """
                    UPDATE assets
                    SET preview_status = 'failed', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND preview_generation = ?
                    """,
                    (asset_id, job_generation),
                )
                conn.commit()
                return None
            if (
                payload.asset_id != asset_id
                or payload.preview_generation != job_generation
                or asset.get("preview_generation") != job_generation
            ):
                transition_formal_preview_attempt(
                    conn,
                    attempt_id=attempt["id"],
                    new_state="superseded",
                )
                _settle_job(
                    conn,
                    job_id=int(job["id"]),
                    status="failed",
                    error_code="preview_generation_superseded",
                )
                conn.commit()
                return None
            if attempt["state"] == "queued":
                attempt = transition_formal_preview_attempt(
                    conn,
                    attempt_id=attempt["id"],
                    new_state="probing",
                )
            elif attempt["state"] not in {
                "probing",
                "resolving",
                "rendering",
                "finalizing",
            }:
                raise FormalPreviewProcessingError("formal_preview_relation_invalid")
            conn.commit()
            return attempt
        except Exception:
            conn.rollback()
            raise


def process_formal_preview_job(
    *,
    settings: Settings,
    job: dict[str, Any],
    probe_runner: ProbeRunner | None = None,
    render_runner: RenderRunner = run_ffmpeg,
) -> bool:
    attempt = prepare_formal_preview_attempt(settings=settings, job=job)
    if attempt is None:
        return True
    candidate_path: Path | None = None
    try:
        source_path = _verified_original_path(
            settings=settings, asset_id=int(attempt["asset_id"])
        )
        if attempt["state"] == "probing":
            manifest = _load_runtime_manifest(settings)
            detector = probe_runner or (
                lambda path, loaded_manifest: probe_and_classify(
                    ffprobe_binary=settings.ffprobe_binary,
                    source_path=path,
                    manifest=loaded_manifest,
                )
            )
            detection = detector(source_path, manifest)
            attempt = _persist_detection(
                settings=settings,
                attempt=attempt,
                manifest=manifest,
                detection=detection,
            )
        if attempt["state"] == "resolving":
            snapshot, transform_status, transform_error = _resolve_preset(
                settings=settings,
                detection_status=str(attempt["detection_status"]),
            )
            attempt = _persist_preset_resolution(
                settings=settings,
                attempt=attempt,
                snapshot=snapshot,
                color_transform_status=transform_status,
                color_transform_error_code=transform_error,
            )
        candidate_path = generate_formal_preview_candidate_path(
            settings.media_root, attempt["id"]
        )
        if attempt["state"] == "rendering":
            cleanup_formal_preview_candidate(settings.media_root, attempt["id"])
            candidate_path = generate_formal_preview_candidate_path(
                settings.media_root, attempt["id"]
            )
            command = build_video_preview_command(
                input_path=source_path,
                output_path=candidate_path,
                lut_path=None,
            )
            render_runner(command)
            try:
                candidate_path.chmod(0o600, follow_symlinks=False)
            except OSError as exc:
                raise FormalPreviewProcessingError(
                    "formal_preview_storage_failed"
                ) from exc
            candidate_identity = inspect_formal_preview_candidate(candidate_path)
            attempt = _transition_to_finalizing(
                settings=settings, attempt=attempt
            )
        elif attempt["state"] == "finalizing":
            candidate_identity = inspect_formal_preview_candidate(candidate_path)
        else:
            raise FormalPreviewProcessingError("formal_preview_relation_invalid")

        finalize_formal_preview_output(
            settings=settings,
            job_id=int(job["id"]),
            attempt_id=attempt["id"],
            candidate_path=candidate_path,
            candidate_identity=candidate_identity,
        )
        candidate_path = None
    except FormalPreviewProcessingError as exc:
        if exc.code == "preview_generation_superseded":
            _supersede_current_attempt(settings=settings, attempt=attempt)
        else:
            _fail_current_attempt(
                settings=settings, attempt=attempt, failure_code=exc.code
            )
    except BoundedProcessError as exc:
        _fail_current_attempt(
            settings=settings, attempt=attempt, failure_code=exc.code
        )
    except DetectorValidationError:
        _fail_current_attempt(
            settings=settings,
            attempt=attempt,
            failure_code="log_detector_manifest_invalid",
        )
    except PreviewGenerationError:
        _fail_current_attempt(
            settings=settings,
            attempt=attempt,
            failure_code="formal_preview_render_failed",
        )
    except FormalPreviewFinalizationError as exc:
        _fail_current_attempt(
            settings=settings, attempt=attempt, failure_code=exc.code
        )
    except (OSError, StorageError):
        _fail_current_attempt(
            settings=settings,
            attempt=attempt,
            failure_code="formal_preview_storage_failed",
        )
    except sqlite3.DatabaseError:
        _fail_current_attempt(
            settings=settings,
            attempt=attempt,
            failure_code="formal_preview_database_failed",
        )
    except Exception:
        _fail_current_attempt(
            settings=settings,
            attempt=attempt,
            failure_code="formal_preview_database_failed",
        )
    finally:
        if candidate_path is not None:
            try:
                cleanup_formal_preview_candidate(
                    settings.media_root, attempt["id"]
                )
            except StorageError:
                pass
    return True


def _verified_original_path(*, settings: Settings, asset_id: int) -> Path:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        return resolve_verified_original(
            settings=settings, conn=conn, asset_id=asset_id
        ).path


def _load_runtime_manifest(settings: Settings) -> DetectorManifest:
    rule_input = load_rule_input(
        settings.detector_root / "detector-rule-input-v1.json"
    )
    manifest = load_detector_manifest(
        settings.detector_root / "manifest.json", rule_input=rule_input
    )
    load_certificate_summary(
        settings.detector_root / "certificate-summary.json",
        rule_input=rule_input,
        manifest=manifest,
    )
    version = read_ffprobe_version(
        ffprobe_binary=settings.ffprobe_binary,
        timeout_ms=settings.detector_probe_timeout_ms,
        max_stdout_bytes=settings.detector_probe_max_stdout_bytes,
        max_stderr_bytes=settings.detector_probe_max_stderr_bytes,
    )
    if version != manifest.ffprobe_version:
        raise BoundedProcessError("log_detector_version_mismatch")
    return manifest


def _persist_detection(
    *,
    settings: Settings,
    attempt: dict[str, Any],
    manifest: DetectorManifest,
    detection: DetectionResult,
) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _require_current_attempt(conn, attempt=attempt)
            if current["state"] != "probing":
                raise FormalPreviewProcessingError(
                    "formal_preview_relation_invalid"
                )
            persisted = save_detection_snapshot(
                conn,
                attempt_id=current["id"],
                detection_status=detection.status,
                source_profile=detection.source_profile,
                detector_rule_version=manifest.rule_version,
                detector_manifest_sha256=manifest.manifest_sha256,
                detector_evidence_sha256=detection.evidence_sha256,
                detector_evidence_json=detection.evidence_json,
            )
            conn.commit()
            return persisted
        except Exception:
            conn.rollback()
            raise


def _resolve_preset(
    *, settings: Settings, detection_status: str
) -> tuple[PresetSnapshot, str, str | None]:
    if detection_status == "apple_log":
        classified = classify_preset(settings, GENERATED_APPLE_LOG_PRESET_ID)
        if classified.registry_classification == "registered_invalid":
            raise FormalPreviewProcessingError("lut_preset_registered_invalid")
        if classified.registry_classification == "valid":
            raise FormalPreviewProcessingError(
                "lut_preset_registered_invalid"
            )
        fallback = compress_only_snapshot()
        return (
            PresetSnapshot(
                requested_preset_id=GENERATED_APPLE_LOG_PRESET_ID,
                registry_classification=classified.registry_classification,
                applied_preset_id=fallback.applied_preset_id,
                display_name=fallback.display_name,
                preset_kind=fallback.preset_kind,
                version=fallback.version,
                source_reference=fallback.source_reference,
                terms_reference=fallback.terms_reference,
                target_color_space=None,
                manifest_canonical_bytes=None,
                manifest_sha256=None,
                expected_lut_sha256=None,
                file_format=None,
                grid_size=None,
                source_root_kind=None,
                source_relative_lut_path=None,
            ),
            "unavailable",
            "lut_preset_unavailable",
        )
    if detection_status not in {"not_log", "unknown"}:
        raise FormalPreviewProcessingError("formal_preview_relation_invalid")
    snapshot = classify_preset(settings, "compress-only")
    if snapshot.registry_classification != "valid":
        raise FormalPreviewProcessingError("lut_preset_registered_invalid")
    return snapshot, "not_requested", None


def _persist_preset_resolution(
    *,
    settings: Settings,
    attempt: dict[str, Any],
    snapshot: PresetSnapshot,
    color_transform_status: str,
    color_transform_error_code: str | None,
) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _require_current_attempt(conn, attempt=attempt)
            if current["state"] != "resolving":
                raise FormalPreviewProcessingError(
                    "formal_preview_relation_invalid"
                )
            persisted = save_preset_snapshot(
                conn,
                attempt_id=current["id"],
                snapshot=snapshot,
                transform_kind="none",
                color_transform_status=color_transform_status,
                color_transform_error_code=color_transform_error_code,
            )
            conn.commit()
            return persisted
        except Exception:
            conn.rollback()
            raise


def _transition_to_finalizing(
    *, settings: Settings, attempt: dict[str, Any]
) -> dict[str, Any]:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _require_current_attempt(conn, attempt=attempt)
            if current["state"] != "rendering":
                raise FormalPreviewProcessingError(
                    "formal_preview_relation_invalid"
                )
            persisted = transition_formal_preview_attempt(
                conn, attempt_id=current["id"], new_state="finalizing"
            )
            conn.commit()
            return persisted
        except Exception:
            conn.rollback()
            raise


def _require_current_attempt(
    conn: sqlite3.Connection, *, attempt: dict[str, Any]
) -> dict[str, Any]:
    current = get_formal_preview_attempt_by_job(
        conn, job_id=int(attempt["job_id"])
    )
    asset = (
        conn.execute(
            "SELECT preview_generation FROM assets WHERE id = ?",
            (attempt["asset_id"],),
        ).fetchone()
        if current is not None
        else None
    )
    if (
        current is None
        or current["id"] != attempt["id"]
        or asset is None
    ):
        raise FormalPreviewProcessingError("formal_preview_relation_invalid")
    if asset["preview_generation"] != current["preview_generation"]:
        raise FormalPreviewProcessingError("preview_generation_superseded")
    return current


def _supersede_current_attempt(
    *, settings: Settings, attempt: dict[str, Any]
) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = get_formal_preview_attempt_by_job(
                conn, job_id=int(attempt["job_id"])
            )
            if current is None:
                raise FormalPreviewProcessingError(
                    "formal_preview_relation_invalid"
                )
            if current["state"] not in {"ready", "failed", "superseded"}:
                transition_formal_preview_attempt(
                    conn,
                    attempt_id=current["id"],
                    new_state="superseded",
                )
            _settle_job(
                conn,
                job_id=current["job_id"],
                status="failed",
                error_code="preview_generation_superseded",
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _converge_terminal_attempt(
    conn: sqlite3.Connection, *, attempt: dict[str, Any]
) -> bool:
    if attempt["state"] == "ready":
        _settle_job(conn, job_id=attempt["job_id"], status="done", error_code=None)
        return True
    if attempt["state"] == "failed":
        _settle_job(
            conn,
            job_id=attempt["job_id"],
            status="failed",
            error_code=attempt["failure_code"],
        )
        return True
    if attempt["state"] == "superseded":
        _settle_job(
            conn,
            job_id=attempt["job_id"],
            status="failed",
            error_code="preview_generation_superseded",
        )
        return True
    return False


def _fail_current_attempt(
    *,
    settings: Settings,
    attempt: dict[str, Any],
    failure_code: str,
) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = get_formal_preview_attempt_by_job(
                conn, job_id=int(attempt["job_id"])
            )
            if current is None or current["state"] in {"ready", "failed", "superseded"}:
                if current is not None:
                    _converge_terminal_attempt(conn, attempt=current)
                conn.commit()
                return
            transition_formal_preview_attempt(
                conn,
                attempt_id=current["id"],
                new_state="failed",
                failure_code=failure_code,
            )
            _settle_job(
                conn,
                job_id=current["job_id"],
                status="failed",
                error_code=failure_code,
            )
            conn.execute(
                """
                UPDATE assets
                SET preview_status = 'failed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND preview_generation = ?
                """,
                (current["asset_id"], current["preview_generation"]),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _settle_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    status: str,
    error_code: str | None,
) -> None:
    if status not in {"done", "failed"}:
        raise ValueError("formal preview job terminal status is invalid")
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, error_message = ?, claimed_at = NULL,
            lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, error_code, job_id),
    )
