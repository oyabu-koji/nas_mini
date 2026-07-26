import json
import sqlite3

import pytest

from app.db.connection import connect
from app.db.migrations import run_migrations
from app.db.phase2b import (
    PHASE2B_MIGRATION_VERSION,
    PHASE2B_SQL_PATH,
    schema_sql_sha256,
)


def _column_names(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply_phase2b_schema(conn):
    conn.executescript(PHASE2B_SQL_PATH.read_text(encoding="utf-8"))
    conn.execute(
        """
        INSERT INTO phase2b_schema_metadata (version, schema_sql_sha256)
        VALUES (?, ?)
        """,
        (PHASE2B_MIGRATION_VERSION, schema_sql_sha256()),
    )


def test_phase2b_schema_is_excluded_from_automatic_migrations(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)

        assert "preview_generation" not in _column_names(conn, "assets")
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (PHASE2B_MIGRATION_VERSION,),
        ).fetchone() is None


def test_phase2b_schema_adds_versioned_identity_and_formal_tables(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        apply_phase2b_schema(conn)

        assert {
            "preview_generation",
            "formal_preview_id",
            "log_detection_status",
            "source_profile",
            "detector_rule_version",
            "detector_manifest_sha256",
            "detector_evidence_sha256",
        }.issubset(_column_names(conn, "assets"))
        assert "preview_generation" in _column_names(conn, "jobs")
        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "formal_preview_attempts",
            "preview_provenance",
            "phase2b_schema_metadata",
        }.issubset(table_names)
        assert {
            "attempt_id",
            "result_id",
            "derived_file_id",
            "preview_generation",
            "detector_rule_version",
            "detector_manifest_sha256",
            "detector_evidence_sha256",
            "requested_preset_id",
            "applied_preset_id",
            "transform_kind",
            "color_transform_status",
        }.issubset(_column_names(conn, "preview_provenance"))
        identity = conn.execute(
            "SELECT * FROM phase2b_schema_metadata"
        ).fetchone()
        assert identity["version"] == PHASE2B_MIGRATION_VERSION
        assert identity["schema_sql_sha256"] == schema_sql_sha256()


def _insert_session_asset(conn, *, asset_id, session_id):
    conn.execute(
        """
        INSERT INTO assets (
            id, type, filename, original_path, size_bytes, server_sha256,
            transfer_status, verification_status, preview_status
        ) VALUES (?, 'video', 'video.mov', ?, 5, ?, 'transferred',
                  'file_verified', 'preview_generating')
        """,
        (asset_id, f"originals/{asset_id}.mov", "a" * 64),
    )
    conn.execute(
        """
        INSERT INTO upload_sessions (
            id, client_upload_id, type, filename, size_bytes,
            expected_file_sha256, chunk_size_bytes, original_relative_path,
            status, retryable, attempt_count, last_activity_at, expires_at,
            asset_id
        ) VALUES (?, ?, 'video', 'video.mov', 5, ?, 5, ?, 'completed',
                  0, 0, CURRENT_TIMESTAMP, datetime('now', '+1 day'), ?)
        """,
        (
            session_id,
            f"client-{session_id}",
            "a" * 64,
            f"originals/{asset_id}.mov",
            asset_id,
        ),
    )


def test_phase2b_job_generation_backfill_and_new_relation_constraints(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('preview', 'done', 1, '{}', 'historical-preview')
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('lut_preview', 'failed', 1, '{}', 'historical-lut')
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('rendition', 'failed', 1, '{}', 'historical-rendition')
            """
        )
        apply_phase2b_schema(conn)

        generations = {
            row["dedup_key"]: row["preview_generation"]
            for row in conn.execute(
                "SELECT dedup_key, preview_generation FROM jobs"
            )
        }
        assert generations == {
            "historical-preview": 0,
            "historical-lut": 0,
            "historical-rendition": None,
        }

        conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
        conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key,
                preview_generation
            ) VALUES ('preview', 'queued', 1, ?, 'formal-preview', 1)
            """,
            (json.dumps({"asset_id": 1, "preview_generation": 1}),),
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="formal_preview_job_relation_invalid"
        ):
            conn.execute(
                """
                INSERT INTO jobs (
                    job_type, status, asset_id, payload_json, dedup_key,
                    preview_generation
                ) VALUES ('preview', 'queued', 1, '{}', 'missing-generation', NULL)
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="formal_preview_lut_job_not_allowed"
        ):
            conn.execute(
                """
                INSERT INTO jobs (
                    job_type, status, asset_id, payload_json, dedup_key,
                    preview_generation
                ) VALUES ('lut_preview', 'queued', 1, '{}', 'new-lut', 1)
                """
            )


def test_formal_attempt_has_unique_generation_job_and_snapshot_columns(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)
        conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
        job_id = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key,
                preview_generation
            ) VALUES ('preview', 'queued', 1, '{}', 'formal-preview', 1)
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO formal_preview_attempts (
                id, asset_id, job_id, preview_generation, state
            ) VALUES (?, 1, ?, 1, 'queued')
            """,
            ("1" * 32, job_id),
        )

        columns = _column_names(conn, "formal_preview_attempts")
        assert {
            "state",
            "detection_status",
            "detector_evidence_json",
            "requested_preset_id",
            "registry_classification",
            "manifest_canonical_bytes",
            "transform_kind",
            "color_transform_status",
            "failure_code",
            "result_id",
            "terminal_at",
        }.issubset(columns)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO formal_preview_attempts (
                    id, asset_id, job_id, preview_generation, state
                ) VALUES (?, 1, ?, 1, 'queued')
                """,
                ("2" * 32, job_id),
            )


def _prepare_ready_formal_components(
    conn,
    *,
    detection_status,
    requested=None,
    applied=None,
    registry_classification=None,
    transform_kind=None,
    transform_status=None,
    transform_error=None,
    manifest_sha256=None,
    expected_lut_sha256=None,
):
    _insert_session_asset(conn, asset_id=1, session_id="session-one")
    apply_phase2b_schema(conn)
    conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
    job_id = conn.execute(
        """
        INSERT INTO jobs (
            job_type, status, asset_id, payload_json, dedup_key,
            preview_generation
        ) VALUES ('preview', 'running', 1, '{}', 'formal-preview', 1)
        """
    ).lastrowid
    derived_id = conn.execute(
        """
        INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
        VALUES (1, 'preview', 'previews/formal.mp4', 'video/mp4', 10)
        """
    ).lastrowid
    result_id = "3" * 32
    conn.execute(
        """
        INSERT INTO processed_results (
            id, asset_id, derived_file_id, status, mime_type, size_bytes,
            sha256, preview_generation
        ) VALUES (?, 1, ?, 'ready', 'video/mp4', 10, ?, 1)
        """,
        (result_id, derived_id, "d" * 64),
    )
    if requested is None:
        requested = (
            "generated-apple-log-rec709"
            if detection_status == "apple_log"
            else "compress-only"
        )
    applied = applied or "compress-only"
    registry_classification = registry_classification or (
        "absent" if detection_status == "apple_log" else "valid"
    )
    transform_kind = transform_kind or "none"
    transform_status = transform_status or (
        "unavailable" if detection_status == "apple_log" else "not_requested"
    )
    if detection_status == "apple_log" and transform_status == "unavailable":
        transform_error = transform_error or "lut_preset_unavailable"
    conn.execute(
        """
        INSERT INTO formal_preview_attempts (
            id, asset_id, job_id, preview_generation, state,
            detection_status, detector_rule_version,
            detector_manifest_sha256, detector_evidence_sha256,
            detector_evidence_json, requested_preset_id,
            registry_classification, applied_preset_id,
            manifest_sha256, expected_lut_sha256, transform_kind,
            color_transform_status, color_transform_error_code,
            result_id, terminal_at
        ) VALUES (?, 1, ?, 1, 'ready', ?, 'rule-v1', ?, ?, '{}', ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            "1" * 32,
            job_id,
            detection_status,
            "a" * 64,
            "b" * 64,
            requested,
            registry_classification,
            applied,
            manifest_sha256,
            expected_lut_sha256,
            transform_kind,
            transform_status,
            transform_error,
            result_id,
        ),
    )
    conn.execute(
        """
        UPDATE assets
        SET log_detection_status = ?,
            detector_rule_version = 'rule-v1',
            detector_manifest_sha256 = ?,
            detector_evidence_sha256 = ?
        WHERE id = 1
        """,
        (detection_status, "a" * 64, "b" * 64),
    )
    return result_id, derived_id


@pytest.mark.parametrize(
    (
        "detection_status",
        "requested",
        "applied",
        "transform_kind",
        "transform_status",
        "transform_error",
        "manifest_sha256",
        "lut_sha256",
    ),
    [
        (
            "apple_log",
            "generated-apple-log-rec709",
            "compress-only",
            "none",
            "unavailable",
            "lut_preset_unavailable",
            None,
            None,
        ),
        (
            "not_log",
            "compress-only",
            "compress-only",
            "none",
            "not_requested",
            None,
            None,
            None,
        ),
        (
            "apple_log",
            "generated-apple-log-rec709",
            "generated-apple-log-rec709",
            "lut",
            "applied",
            None,
            "c" * 64,
            "d" * 64,
        ),
    ],
)
def test_preview_provenance_accepts_only_declared_transform_invariants(
    tmp_path,
    detection_status,
    requested,
    applied,
    transform_kind,
    transform_status,
    transform_error,
    manifest_sha256,
    lut_sha256,
):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        result_id, derived_id = _prepare_ready_formal_components(
            conn,
            detection_status=detection_status,
            requested=requested,
            applied=applied,
            registry_classification=(
                "valid"
                if transform_status in {"not_requested", "applied"}
                else "absent"
            ),
            transform_kind=transform_kind,
            transform_status=transform_status,
            transform_error=transform_error,
            manifest_sha256=manifest_sha256,
            expected_lut_sha256=lut_sha256,
        )

        conn.execute(
            """
            INSERT INTO preview_provenance (
                id, attempt_id, asset_id, preview_generation, result_id,
                derived_file_id, detection_status, detector_rule_version,
                detector_manifest_sha256, detector_evidence_sha256,
                requested_preset_id, applied_preset_id, manifest_sha256,
                lut_sha256, transform_kind, color_transform_status,
                color_transform_error_code
            ) VALUES (?, ?, 1, 1, ?, ?, ?, 'rule-v1', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2" * 32,
                "1" * 32,
                result_id,
                derived_id,
                detection_status,
                "a" * 64,
                "b" * 64,
                requested,
                applied,
                manifest_sha256,
                lut_sha256,
                transform_kind,
                transform_status,
                transform_error,
            ),
        )


def test_preview_provenance_rejects_incomplete_lut_applied_claim(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        result_id, derived_id = _prepare_ready_formal_components(
            conn, detection_status="apple_log"
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO preview_provenance (
                    id, attempt_id, asset_id, preview_generation, result_id,
                    derived_file_id, detection_status, detector_rule_version,
                    detector_manifest_sha256, detector_evidence_sha256,
                    requested_preset_id, applied_preset_id,
                    transform_kind, color_transform_status
                ) VALUES (?, ?, 1, 1, ?, ?, 'apple_log', 'rule-v1', ?, ?,
                          'generated-apple-log-rec709',
                          'generated-apple-log-rec709', 'lut', 'applied')
                """,
                (
                    "2" * 32,
                    "1" * 32,
                    result_id,
                    derived_id,
                    "a" * 64,
                    "b" * 64,
                ),
            )


def test_terminal_attempt_and_provenance_identity_are_immutable(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        result_id, derived_id = _prepare_ready_formal_components(
            conn, detection_status="not_log"
        )
        conn.execute(
            """
            INSERT INTO preview_provenance (
                id, attempt_id, asset_id, preview_generation, result_id,
                derived_file_id, detection_status, detector_rule_version,
                detector_manifest_sha256, detector_evidence_sha256,
                requested_preset_id, applied_preset_id, transform_kind,
                color_transform_status
            ) VALUES (?, ?, 1, 1, ?, ?, 'not_log', 'rule-v1', ?, ?,
                      'compress-only', 'compress-only', 'none', 'not_requested')
            """,
            (
                "2" * 32,
                "1" * 32,
                result_id,
                derived_id,
                "a" * 64,
                "b" * 64,
            ),
        )

        with pytest.raises(
            sqlite3.IntegrityError, match="formal_preview_attempt_terminal_immutable"
        ):
            conn.execute(
                "UPDATE formal_preview_attempts SET source_profile = 'changed' WHERE id = ?",
                ("1" * 32,),
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="formal_preview_attempt_terminal_delete_not_allowed"
        ):
            conn.execute(
                "DELETE FROM formal_preview_attempts WHERE id = ?", ("1" * 32,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="preview_provenance_immutable"):
            conn.execute(
                "UPDATE preview_provenance SET source_profile = 'changed' WHERE id = ?",
                ("2" * 32,),
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="preview_provenance_delete_not_allowed"
        ):
            conn.execute("DELETE FROM preview_provenance WHERE id = ?", ("2" * 32,))


def test_attempt_and_provenance_reject_cross_generation_relations(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        result_id, derived_id = _prepare_ready_formal_components(
            conn, detection_status="not_log"
        )
        job_id = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, asset_id, payload_json, dedup_key,
                preview_generation
            ) VALUES ('preview', 'queued', 1, '{}', 'second-formal-preview', 1)
            """
        ).lastrowid

        with pytest.raises(
            sqlite3.IntegrityError, match="formal_preview_attempt_relation_invalid"
        ):
            conn.execute(
                """
                INSERT INTO formal_preview_attempts (
                    id, asset_id, job_id, preview_generation, state
                ) VALUES (?, 1, ?, 2, 'queued')
                """,
                ("4" * 32, job_id),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="formal_preview_provenance_relation_invalid",
        ):
            conn.execute(
                """
                INSERT INTO preview_provenance (
                    id, attempt_id, asset_id, preview_generation, result_id,
                    derived_file_id, detection_status, detector_rule_version,
                    detector_manifest_sha256, detector_evidence_sha256,
                    requested_preset_id, applied_preset_id, transform_kind,
                    color_transform_status
                ) VALUES (?, ?, 1, 2, ?, ?, 'not_log', 'rule-v1', ?, ?,
                          'compress-only', 'compress-only', 'none', 'not_requested')
                """,
                (
                    "2" * 32,
                    "1" * 32,
                    result_id,
                    derived_id,
                    "a" * 64,
                    "b" * 64,
                ),
            )


def test_current_formal_result_cannot_be_superseded_or_dual_provenance(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        result_id, derived_id = _prepare_ready_formal_components(
            conn, detection_status="not_log"
        )
        conn.execute(
            """
            INSERT INTO preview_provenance (
                id, attempt_id, asset_id, preview_generation, result_id,
                derived_file_id, detection_status, detector_rule_version,
                detector_manifest_sha256, detector_evidence_sha256,
                requested_preset_id, applied_preset_id, transform_kind,
                color_transform_status
            ) VALUES (?, ?, 1, 1, ?, ?, 'not_log', 'rule-v1', ?, ?,
                      'compress-only', 'compress-only', 'none', 'not_requested')
            """,
            (
                "2" * 32,
                "1" * 32,
                result_id,
                derived_id,
                "a" * 64,
                "b" * 64,
            ),
        )
        conn.execute(
            "UPDATE assets SET formal_preview_id = ? WHERE id = 1", (result_id,)
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="formal_preview_current_cannot_be_superseded",
        ):
            conn.execute(
                """
                UPDATE processed_results
                SET status = 'superseded', superseded_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (result_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="processed_result_provenance_kind_conflict",
        ):
            conn.execute(
                """
                INSERT INTO rendition_provenance (
                    rendition_id, asset_id, result_id, derived_file_id,
                    requested_preset_id, applied_preset_id, transform_kind,
                    color_transform_status
                ) VALUES (?, 1, ?, ?, 'compress-only', 'compress-only',
                          'none', 'not_requested')
                """,
                ("4" * 32, result_id, derived_id),
            )


def _insert_ready_managed(
    conn,
    *,
    generation,
    result_id,
    rendition_id,
    size_bytes=10,
    sha256="e" * 64,
):
    job_id = conn.execute(
        """
        INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
        VALUES ('rendition', 'done', 1, '{}', ?)
        """,
        (f"rendition-{generation}",),
    ).lastrowid
    derived_id = conn.execute(
        """
        INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
        VALUES (1, 'rendition', ?, 'video/mp4', ?)
        """,
        (f"previews/renditions/{rendition_id}.mp4", size_bytes),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO processed_results (
            id, asset_id, derived_file_id, status, mime_type, size_bytes,
            sha256, preview_generation
        ) VALUES (?, 1, ?, 'ready', 'video/mp4', ?, ?, NULL)
        """,
        (result_id, derived_id, size_bytes, sha256),
    )
    conn.execute(
        """
        INSERT INTO renditions (
            id, asset_id, client_request_id, job_id, selection_generation,
            requested_preset_id, registry_classification, state,
            applied_preset_id, color_transform_status, result_id, terminal_at
        ) VALUES (?, 1, ?, ?, ?, 'compress-only', 'valid', 'ready',
                  'compress-only', 'not_requested', ?, CURRENT_TIMESTAMP)
        """,
        (rendition_id, str(generation) * 32, job_id, generation, result_id),
    )
    conn.execute(
        """
        INSERT INTO rendition_provenance (
            rendition_id, asset_id, result_id, derived_file_id,
            requested_preset_id, applied_preset_id, transform_kind,
            color_transform_status
        ) VALUES (?, 1, ?, ?, 'compress-only', 'compress-only',
                  'none', 'not_requested')
        """,
        (rendition_id, result_id, derived_id),
    )
    conn.execute(
        """
        UPDATE assets
        SET rendition_selection_generation = MAX(rendition_selection_generation, ?)
        WHERE id = 1
        """,
        (generation,),
    )
    return result_id


def test_kind_aware_active_switch_preserves_formal_and_supersedes_managed(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        formal_id, formal_derived_id = _prepare_ready_formal_components(
            conn, detection_status="not_log"
        )
        conn.execute(
            """
            INSERT INTO preview_provenance (
                id, attempt_id, asset_id, preview_generation, result_id,
                derived_file_id, detection_status, detector_rule_version,
                detector_manifest_sha256, detector_evidence_sha256,
                requested_preset_id, applied_preset_id, transform_kind,
                color_transform_status
            ) VALUES (?, ?, 1, 1, ?, ?, 'not_log', 'rule-v1', ?, ?,
                      'compress-only', 'compress-only', 'none', 'not_requested')
            """,
            (
                "2" * 32,
                "1" * 32,
                formal_id,
                formal_derived_id,
                "a" * 64,
                "b" * 64,
            ),
        )
        conn.execute(
            """
            UPDATE assets
            SET formal_preview_id = ?, active_processed_result_id = ?
            WHERE id = 1
            """,
            (formal_id, formal_id),
        )
        first_managed = _insert_ready_managed(
            conn,
            generation=1,
            result_id="4" * 32,
            rendition_id="5" * 32,
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (first_managed,),
        )
        second_managed = _insert_ready_managed(
            conn,
            generation=2,
            result_id="6" * 32,
            rendition_id="7" * 32,
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (second_managed,),
        )

        statuses = {
            row["id"]: row["status"]
            for row in conn.execute(
                "SELECT id, status FROM processed_results"
            ).fetchall()
        }
        assert statuses[formal_id] == "ready"
        assert statuses[first_managed] == "superseded"
        assert statuses[second_managed] == "ready"


def test_active_pointer_rejects_non_ready_managed_relation(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)
        job_id = conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('rendition', 'queued', 1, '{}', 'rendition-1')
            """
        ).lastrowid
        derived_id = conn.execute(
            """
            INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
            VALUES (1, 'rendition', 'previews/non-ready.mp4', 'video/mp4', 10)
            """
        ).lastrowid
        result_id = "4" * 32
        conn.execute(
            """
            INSERT INTO processed_results (
                id, asset_id, derived_file_id, status, mime_type, size_bytes,
                sha256, preview_generation
            ) VALUES (?, 1, ?, 'ready', 'video/mp4', 10, ?, NULL)
            """,
            (result_id, derived_id, "e" * 64),
        )
        rendition_id = "5" * 32
        conn.execute(
            """
            INSERT INTO renditions (
                id, asset_id, client_request_id, job_id, selection_generation,
                requested_preset_id, registry_classification, state,
                applied_preset_id, color_transform_status, result_id, terminal_at
            ) VALUES (?, 1, ?, ?, 1, 'compress-only', 'valid', 'ready',
                      'compress-only', 'not_requested', ?, CURRENT_TIMESTAMP)
            """,
            (rendition_id, "1" * 32, job_id, result_id),
        )
        conn.execute(
            """
            INSERT INTO rendition_provenance (
                rendition_id, asset_id, result_id, derived_file_id,
                requested_preset_id, applied_preset_id, transform_kind,
                color_transform_status
            ) VALUES (?, 1, ?, ?, 'compress-only', 'compress-only',
                      'none', 'not_requested')
            """,
            (rendition_id, result_id, derived_id),
        )
        conn.execute("DROP TRIGGER prevent_terminal_rendition_update")
        conn.execute(
            """
            UPDATE renditions
            SET state = 'queued', applied_preset_id = NULL,
                color_transform_status = NULL, result_id = NULL,
                terminal_at = NULL
            WHERE id = ?
            """,
            (rendition_id,),
        )
        conn.execute(
            "UPDATE assets SET rendition_selection_generation = 1 WHERE id = 1"
        )

        with pytest.raises(
            sqlite3.IntegrityError, match="active_processed_result_invalid"
        ):
            conn.execute(
                "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
                (result_id,),
            )


def test_preview_ready_uses_formal_relation_even_when_managed_is_active(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        formal_id, formal_derived_id = _prepare_ready_formal_components(
            conn, detection_status="not_log"
        )
        conn.execute(
            """
            INSERT INTO preview_provenance (
                id, attempt_id, asset_id, preview_generation, result_id,
                derived_file_id, detection_status, detector_rule_version,
                detector_manifest_sha256, detector_evidence_sha256,
                requested_preset_id, applied_preset_id, transform_kind,
                color_transform_status
            ) VALUES (?, ?, 1, 1, ?, ?, 'not_log', 'rule-v1', ?, ?,
                      'compress-only', 'compress-only', 'none', 'not_requested')
            """,
            (
                "2" * 32,
                "1" * 32,
                formal_id,
                formal_derived_id,
                "a" * 64,
                "b" * 64,
            ),
        )
        conn.execute(
            "UPDATE assets SET formal_preview_id = ? WHERE id = 1",
            (formal_id,),
        )
        managed_id = _insert_ready_managed(
            conn,
            generation=1,
            result_id="4" * 32,
            rendition_id="5" * 32,
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (managed_id,),
        )
        conn.execute(
            "UPDATE assets SET preview_status = 'preview_ready' WHERE id = 1"
        )

        asset = conn.execute("SELECT * FROM assets WHERE id = 1").fetchone()
        assert asset["formal_preview_id"] == formal_id
        assert asset["active_processed_result_id"] == managed_id
        assert asset["preview_status"] == "preview_ready"


def test_session_video_preview_ready_rejects_missing_formal_relation(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)

        with pytest.raises(
            sqlite3.IntegrityError, match="formal_preview_relation_invalid"
        ):
            conn.execute(
                "UPDATE assets SET preview_status = 'preview_ready' WHERE id = 1"
            )


def test_managed_provenance_rejects_non_null_preview_generation(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)
        conn.execute("UPDATE assets SET preview_generation = 1 WHERE id = 1")
        job_id = conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('rendition', 'done', 1, '{}', 'rendition-1')
            """
        ).lastrowid
        derived_id = conn.execute(
            """
            INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
            VALUES (1, 'rendition', 'previews/bad-generation.mp4', 'video/mp4', 10)
            """
        ).lastrowid
        result_id = "4" * 32
        conn.execute(
            """
            INSERT INTO processed_results (
                id, asset_id, derived_file_id, status, mime_type, size_bytes,
                sha256, preview_generation
            ) VALUES (?, 1, ?, 'ready', 'video/mp4', 10, ?, 1)
            """,
            (result_id, derived_id, "e" * 64),
        )
        rendition_id = "5" * 32
        conn.execute(
            """
            INSERT INTO renditions (
                id, asset_id, client_request_id, job_id, selection_generation,
                requested_preset_id, registry_classification, state,
                applied_preset_id, color_transform_status, result_id, terminal_at
            ) VALUES (?, 1, ?, ?, 1, 'compress-only', 'valid', 'ready',
                      'compress-only', 'not_requested', ?, CURRENT_TIMESTAMP)
            """,
            (rendition_id, "1" * 32, job_id, result_id),
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="managed_result_preview_generation_invalid",
        ):
            conn.execute(
                """
                INSERT INTO rendition_provenance (
                    rendition_id, asset_id, result_id, derived_file_id,
                    requested_preset_id, applied_preset_id, transform_kind,
                    color_transform_status
                ) VALUES (?, 1, ?, ?, 'compress-only', 'compress-only',
                          'none', 'not_requested')
                """,
                (rendition_id, result_id, derived_id),
            )
