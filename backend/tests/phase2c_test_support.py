from hashlib import sha256

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.services.phase2b_migration import apply_phase2b_migration


def initialize_phase2b(settings) -> None:
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
    apply_phase2b_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        certification_check=lambda _settings: None,
    )


def insert_eligible_confirmed_asset(
    conn,
    *,
    asset_id: int = 1,
    review_status: str = "preview_confirmed",
    result_bytes: bytes | None = None,
) -> None:
    digest = "a" * 64
    result_id = f"{asset_id:032x}"
    attempt_id = f"{asset_id + 100:032x}"
    provenance_id = f"{asset_id + 200:032x}"
    session_id = f"session-{asset_id:08d}"
    result_size = len(result_bytes) if result_bytes is not None else 16
    result_sha256 = sha256(result_bytes).hexdigest() if result_bytes is not None else "c" * 64
    conn.execute(
        """
        INSERT INTO assets (
            id, type, filename, original_path, size_bytes, server_sha256,
            transfer_status, verification_status, preview_status
        ) VALUES (?, 'video', 'fixture.mov', ?, 8, ?, 'transferred',
                  'file_verified', 'preview_generating')
        """,
        (asset_id, f"originals/{asset_id}.mov", digest),
    )
    conn.execute(
        """
        INSERT INTO upload_sessions (
            id, client_upload_id, type, filename, size_bytes,
            expected_file_sha256, chunk_size_bytes, original_relative_path,
            status, retryable, attempt_count, last_activity_at, expires_at,
            asset_id
        ) VALUES (?, ?, 'video', 'fixture.mov', 8, ?, 8, ?, 'completed',
                  0, 0, CURRENT_TIMESTAMP, datetime('now', '+1 day'), ?)
        """,
        (
            session_id,
            f"client-{asset_id:08d}",
            digest,
            f"originals/{asset_id}.mov",
            asset_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO upload_chunks (
            session_id, chunk_index, start_offset, end_offset,
            size_bytes, sha256, status
        ) VALUES (?, 0, 0, 7, 8, ?, 'verified')
        """,
        (session_id, "b" * 64),
    )
    conn.execute(
        "UPDATE assets SET preview_generation = 1 WHERE id = ?",
        (asset_id,),
    )
    job_id = conn.execute(
        """
        INSERT INTO jobs (
            job_type, status, asset_id, payload_json, dedup_key,
            preview_generation
        ) VALUES ('preview', 'done', ?, '{}', ?, 1)
        """,
        (asset_id, f"phase2c-fixture:{asset_id}"),
    ).lastrowid
    derived_id = conn.execute(
        """
        INSERT INTO derived_files (
            asset_id, kind, path, mime_type, size_bytes
        ) VALUES (?, 'preview', ?, 'video/mp4', ?)
        """,
        (asset_id, f"previews/{asset_id}.mp4", result_size),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO processed_results (
            id, asset_id, derived_file_id, status, mime_type, size_bytes,
            sha256, preview_generation
        ) VALUES (?, ?, ?, 'ready', 'video/mp4', ?, ?, 1)
        """,
        (result_id, asset_id, derived_id, result_size, result_sha256),
    )
    conn.execute(
        """
        INSERT INTO formal_preview_attempts (
            id, asset_id, job_id, preview_generation, state,
            detection_status, detector_rule_version,
            detector_manifest_sha256, detector_evidence_sha256,
            detector_evidence_json, requested_preset_id,
            registry_classification, applied_preset_id,
            preset_display_name, preset_kind, preset_version,
            transform_kind, color_transform_status,
            color_transform_error_code, result_id, terminal_at
        ) VALUES (?, ?, ?, 1, 'ready', 'not_log', 'rule-v1', ?, ?, '{}',
                  'compress-only', 'valid', 'compress-only',
                  'Compress only', 'compress-only', '1', 'none',
                  'not_requested', NULL, ?, CURRENT_TIMESTAMP)
        """,
        (
            attempt_id,
            asset_id,
            job_id,
            "d" * 64,
            "e" * 64,
            result_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO preview_provenance (
            id, attempt_id, asset_id, preview_generation, result_id,
            derived_file_id, detection_status, detector_rule_version,
            detector_manifest_sha256, detector_evidence_sha256,
            requested_preset_id, applied_preset_id, preset_display_name,
            preset_kind, preset_version, transform_kind,
            color_transform_status
        ) VALUES (?, ?, ?, 1, ?, ?, 'not_log', 'rule-v1', ?, ?,
                  'compress-only', 'compress-only', 'Compress only',
                  'compress-only', NULL, 'none', 'not_requested')
        """,
        (
            provenance_id,
            attempt_id,
            asset_id,
            result_id,
            derived_id,
            "d" * 64,
            "e" * 64,
        ),
    )
    conn.execute(
        """
        UPDATE assets
        SET log_detection_status = 'not_log',
            detector_rule_version = 'rule-v1',
            detector_manifest_sha256 = ?,
            detector_evidence_sha256 = ?
        WHERE id = ?
        """,
        ("d" * 64, "e" * 64, asset_id),
    )
    conn.execute(
        """
        UPDATE assets
        SET formal_preview_id = ?, preview_status = 'preview_ready',
            review_status = ?
        WHERE id = ?
        """,
        (result_id, review_status, asset_id),
    )
