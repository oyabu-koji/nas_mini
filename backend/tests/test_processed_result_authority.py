from app.db.connection import connect
from app.db.migrations import run_migrations
from app.services.processed_result_authority import classify_active_processed_result
from tests.test_phase2b_schema import (
    _insert_ready_managed,
    _insert_session_asset,
    apply_phase2b_schema,
)


def test_active_result_classifier_covers_none_legacy_managed_and_ambiguous(tmp_path):
    database = tmp_path / "db.sqlite3"
    with connect(database, 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        assert classify_active_processed_result(conn, asset_id=1).kind == "none"

        legacy_derived = conn.execute(
            """
            INSERT INTO derived_files (asset_id, kind, path, mime_type, size_bytes)
            VALUES (1, 'preview', 'previews/legacy.mp4', 'video/mp4', 10)
            """
        ).lastrowid
        legacy_result = "1" * 32
        conn.execute(
            """
            INSERT INTO processed_results (
                id, asset_id, derived_file_id, status, mime_type, size_bytes,
                sha256, preview_generation
            ) VALUES (?, 1, ?, 'ready', 'video/mp4', 10, ?, NULL)
            """,
            (legacy_result, legacy_derived, "a" * 64),
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (legacy_result,),
        )
        apply_phase2b_schema(conn)
        assert (
            classify_active_processed_result(conn, asset_id=1).kind
            == "legacy_phase2a"
        )

        conn.execute("UPDATE assets SET active_processed_result_id = NULL WHERE id = 1")
        managed_result = _insert_ready_managed(
            conn,
            generation=1,
            result_id="2" * 32,
            rendition_id="3" * 32,
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (managed_result,),
        )
        assert (
            classify_active_processed_result(conn, asset_id=1).kind
            == "current_managed"
        )

        conn.execute("DROP TRIGGER validate_active_processed_result")
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (legacy_result,),
        )
        assert classify_active_processed_result(conn, asset_id=1).kind == "ambiguous"


def test_current_managed_allows_newer_failed_but_not_nonterminal_selection(tmp_path):
    with connect(tmp_path / "db.sqlite3", 5000) as conn:
        run_migrations(conn)
        _insert_session_asset(conn, asset_id=1, session_id="session-one")
        apply_phase2b_schema(conn)
        managed_result = _insert_ready_managed(
            conn,
            generation=1,
            result_id="2" * 32,
            rendition_id="3" * 32,
        )
        conn.execute(
            "UPDATE assets SET active_processed_result_id = ? WHERE id = 1",
            (managed_result,),
        )
        job_id = conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('rendition', 'failed', 1, '{}', 'rendition-2')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO renditions (
                id, asset_id, client_request_id, job_id, selection_generation,
                requested_preset_id, registry_classification, state,
                color_transform_status, error_code, terminal_at
            ) VALUES (?, 1, ?, ?, 2, 'compress-only', 'valid', 'failed',
                      'failed', 'render-failed', CURRENT_TIMESTAMP)
            """,
            ("4" * 32, "2" * 32, job_id),
        )
        conn.execute(
            "UPDATE assets SET rendition_selection_generation = 2 WHERE id = 1"
        )
        assert (
            classify_active_processed_result(conn, asset_id=1).kind
            == "current_managed"
        )

        next_job_id = conn.execute(
            """
            INSERT INTO jobs (job_type, status, asset_id, payload_json, dedup_key)
            VALUES ('rendition', 'queued', 1, '{}', 'rendition-3')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO renditions (
                id, asset_id, client_request_id, job_id, selection_generation,
                requested_preset_id, registry_classification, state
            ) VALUES (?, 1, ?, ?, 3, 'compress-only', 'valid', 'queued')
            """,
            ("5" * 32, "6" * 32, next_job_id),
        )
        conn.execute(
            "UPDATE assets SET rendition_selection_generation = 3 WHERE id = 1"
        )
        assert classify_active_processed_result(conn, asset_id=1).kind == "ambiguous"
