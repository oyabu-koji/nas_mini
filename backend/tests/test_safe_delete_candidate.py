import inspect
import sqlite3
import time

import pytest

from app.core.settings import (
    MAX_UPLOAD_CHUNKS,
    MAX_UPLOAD_CHUNK_SIZE_BYTES,
    MAX_UPLOAD_SESSION_SIZE_BYTES,
    Settings,
)
from app.db.connection import connect
from app.services.phase2c_migration import apply_phase2c_migration
from app.services.safe_delete_candidate import (
    CANDIDATE_REASON_ORDER,
    NOT_CANDIDATE,
    SAFE_TO_DELETE_CANDIDATE,
    CandidateEvaluation,
    CandidateProjectionError,
    evaluate_safe_delete_candidate,
    project_candidate_status,
)
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)


def _settings(tmp_path):
    return Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )


def _apply(settings):
    return apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )


def _prepare_phase2c(settings):
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.commit()
    _apply(settings)


@pytest.mark.parametrize(
    ("case", "reason"),
    list(
        zip(
            (
                "schema",
                "asset",
                "session",
                "upload_limit",
                "chunk_limit",
                "chunks",
                "file_identity",
                "formal_ready",
                "formal_provenance",
                "review",
            ),
            CANDIDATE_REASON_ORDER,
            strict=True,
        )
    ),
)
def test_candidate_evaluator_fixed_first_failure_reason_order(
    tmp_path,
    case,
    reason,
):
    settings = _settings(tmp_path)
    if case == "schema":
        initialize_phase2b(settings)
        with connect(settings.database_path, 5000) as conn:
            evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        assert evaluation.reason == reason
        return

    _prepare_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET delete_candidate_status = ? WHERE id = 1",
            (NOT_CANDIDATE,),
        )
        if case == "asset":
            asset_id = 999
        else:
            asset_id = 1
        if case == "session":
            conn.execute("DROP TRIGGER prevent_completed_upload_session_update")
            conn.execute(
                "UPDATE upload_sessions SET status = 'uploading' WHERE asset_id = 1"
            )
        elif case == "upload_limit":
            conn.execute("DROP TRIGGER prevent_completed_upload_session_update")
            conn.execute(
                "UPDATE upload_sessions SET size_bytes = ? WHERE asset_id = 1",
                (MAX_UPLOAD_SESSION_SIZE_BYTES + 1,),
            )
        elif case == "chunk_limit":
            conn.execute("DROP TRIGGER prevent_completed_upload_session_update")
            conn.execute(
                "UPDATE upload_sessions SET chunk_size_bytes = ? WHERE asset_id = 1",
                (MAX_UPLOAD_CHUNK_SIZE_BYTES + 1,),
            )
        elif case == "chunks":
            conn.execute("DROP TRIGGER prevent_completed_upload_chunk_delete")
            conn.execute("DELETE FROM upload_chunks")
        elif case == "file_identity":
            conn.execute("DROP TRIGGER prevent_finalized_session_asset_update")
            conn.execute(
                "UPDATE assets SET server_sha256 = ? WHERE id = 1",
                ("f" * 64,),
            )
        elif case == "formal_ready":
            conn.execute(
                "UPDATE assets SET preview_status = 'preview_generating' WHERE id = 1"
            )
        elif case == "formal_provenance":
            conn.execute("DROP TRIGGER prevent_preview_provenance_update")
            conn.execute(
                """
                UPDATE preview_provenance
                SET source_profile = 'mismatch'
                WHERE asset_id = 1
                """
            )
        elif case == "review":
            conn.execute(
                "UPDATE assets SET review_status = 'not_reviewed' WHERE id = 1"
            )

        evaluation = evaluate_safe_delete_candidate(conn, asset_id=asset_id)

    assert evaluation.eligible is False
    assert evaluation.reason == reason


def test_candidate_evaluator_uses_four_sql_statements_without_transaction_or_io(
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        statements = []
        conn.set_trace_callback(
            lambda sql: statements.append(sql)
            if sql.lstrip().upper().startswith("SELECT")
            else None
        )
        conn.execute("BEGIN")
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        conn.set_trace_callback(None)

        assert evaluation == CandidateEvaluation(eligible=True, reason=None)
        assert len(statements) == 4
        assert conn.in_transaction is True
        conn.rollback()

    source = inspect.getsource(evaluate_safe_delete_candidate)
    for forbidden in (
        "filename",
        "is_log",
        "original_path",
        "active_processed_result_id",
        "open(",
    ):
        assert forbidden not in source


def test_candidate_evaluator_skips_chunk_aggregate_after_upload_limit(
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute("DROP TRIGGER prevent_completed_upload_session_update")
        conn.execute(
            "UPDATE upload_sessions SET size_bytes = ? WHERE asset_id = 1",
            (MAX_UPLOAD_SESSION_SIZE_BYTES + 1,),
        )
        statements = []
        conn.set_trace_callback(lambda sql: statements.append(sql))
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        conn.set_trace_callback(None)

    assert evaluation.reason == "upload_limit_exceeded"
    assert not any("FROM upload_chunks INDEXED BY" in sql for sql in statements)


def test_candidate_projection_follows_caller_transaction_and_all_states(tmp_path):
    settings = _settings(tmp_path)
    _prepare_phase2c(settings)
    eligible = CandidateEvaluation(eligible=True, reason=None)
    ineligible = CandidateEvaluation(
        eligible=False,
        reason="preview_not_confirmed",
    )
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET delete_candidate_status = ? WHERE id = 1",
            (NOT_CANDIDATE,),
        )
        conn.commit()
        conn.execute("BEGIN")
        assert project_candidate_status(
            conn,
            asset_id=1,
            evaluation=eligible,
            allow_promotion=False,
        ) == NOT_CANDIDATE
        assert project_candidate_status(
            conn,
            asset_id=1,
            evaluation=eligible,
            allow_promotion=True,
        ) == SAFE_TO_DELETE_CANDIDATE
        assert project_candidate_status(
            conn,
            asset_id=1,
            evaluation=ineligible,
            allow_promotion=False,
        ) == NOT_CANDIDATE
        assert conn.in_transaction is True
        conn.rollback()
        assert conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0] == NOT_CANDIDATE

        with pytest.raises(
            CandidateProjectionError,
            match="candidate_asset_not_found",
        ):
            project_candidate_status(
                conn,
                asset_id=999,
                evaluation=eligible,
                allow_promotion=True,
            )


def test_candidate_projection_rejects_unsupported_legacy_status(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            """
            INSERT INTO assets (
                id, type, filename, delete_candidate_status
            ) VALUES (1, 'video', 'fixture.mov', 'unsupported')
            """
        )
        with pytest.raises(
            CandidateProjectionError,
            match="candidate_status_invalid",
        ):
            project_candidate_status(
                conn,
                asset_id=1,
                evaluation=CandidateEvaluation(eligible=True, reason=None),
                allow_promotion=True,
            )


def test_candidate_evaluator_131072_chunks_uses_index_under_two_seconds(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.execute("DELETE FROM upload_chunks")
        conn.execute(
            """
            UPDATE upload_sessions
            SET size_bytes = ?, chunk_size_bytes = ?
            WHERE asset_id = 1
            """,
            (MAX_UPLOAD_SESSION_SIZE_BYTES, MAX_UPLOAD_CHUNK_SIZE_BYTES),
        )
        conn.execute(
            "UPDATE assets SET size_bytes = ? WHERE id = 1",
            (MAX_UPLOAD_SESSION_SIZE_BYTES,),
        )
        session_id = conn.execute(
            "SELECT id FROM upload_sessions WHERE asset_id = 1"
        ).fetchone()[0]
        conn.executemany(
            """
            INSERT INTO upload_chunks (
                session_id, chunk_index, start_offset, end_offset,
                size_bytes, sha256, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'verified')
            """,
            (
                (
                    session_id,
                    index,
                    index * MAX_UPLOAD_CHUNK_SIZE_BYTES,
                    ((index + 1) * MAX_UPLOAD_CHUNK_SIZE_BYTES) - 1,
                    MAX_UPLOAD_CHUNK_SIZE_BYTES,
                    "b" * 64,
                )
                for index in range(MAX_UPLOAD_CHUNKS)
            ),
        )
        conn.commit()
    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT COUNT(*)
            FROM upload_chunks INDEXED BY idx_upload_chunks_session
            WHERE session_id = ?
            """,
            ("session-00000001",),
        ).fetchall()
        started = time.perf_counter()
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        elapsed = time.perf_counter() - started

    assert any("idx_upload_chunks_session" in row["detail"] for row in plan)
    assert evaluation.eligible is True
    assert tuple(evaluation.__dict__) == ("eligible", "reason")
    assert elapsed < 2.0


def test_completed_session_rejects_nonverified_chunk_insert(tmp_path):
    settings = _settings(tmp_path)
    _prepare_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="completed_upload_chunk_insert_not_allowed",
        ):
            conn.execute(
                """
                INSERT INTO upload_chunks (
                    session_id, chunk_index, start_offset, end_offset,
                    size_bytes, sha256, status
                ) VALUES (
                    'session-00000001', 1, 8, 15, 8, ?, 'received'
                )
                """,
                ("b" * 64,),
            )


def _set_formal_claim(conn, variant):
    claims = {
        "ordinary": {
            "detection": "not_log",
            "requested": "compress-only",
            "applied": "compress-only",
            "version": None,
            "manifest": None,
            "lut": None,
            "kind": "none",
            "status": "not_requested",
            "error": None,
        },
        "unknown": {
            "detection": "unknown",
            "requested": "compress-only",
            "applied": "compress-only",
            "version": None,
            "manifest": None,
            "lut": None,
            "kind": "none",
            "status": "not_requested",
            "error": None,
        },
        "apple_log_fallback": {
            "detection": "apple_log",
            "profile": "apple-log-1",
            "requested": "generated-apple-log-rec709",
            "applied": "compress-only",
            "version": None,
            "manifest": None,
            "lut": None,
            "kind": "none",
            "status": "unavailable",
            "error": "lut_preset_unavailable",
        },
        "future_lut": {
            "detection": "apple_log",
            "profile": "apple-log-1",
            "requested": "generated-apple-log-rec709",
            "applied": "generated-apple-log-rec709",
            "version": "2026.1",
            "manifest": "f" * 64,
            "lut": "1" * 64,
            "kind": "lut",
            "status": "applied",
            "error": None,
        },
    }
    claim = claims[variant]
    if variant == "ordinary":
        return
    trigger_names = (
        "prevent_terminal_formal_preview_attempt_update",
        "prevent_preview_provenance_update",
    )
    trigger_sql = [
        conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger' AND name = ?
            """,
            (name,),
        ).fetchone()[0]
        for name in trigger_names
    ]
    for name in trigger_names:
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute(
        """
        UPDATE formal_preview_attempts
        SET detection_status = ?,
            source_profile = ?,
            requested_preset_id = ?,
            applied_preset_id = ?,
            preset_version = ?,
            manifest_sha256 = ?,
            expected_lut_sha256 = ?,
            transform_kind = ?,
            color_transform_status = ?,
            color_transform_error_code = ?
        WHERE asset_id = 1
        """,
        (
            claim["detection"],
            claim.get("profile"),
            claim["requested"],
            claim["applied"],
            claim["version"],
            claim["manifest"],
            claim["lut"],
            claim["kind"],
            claim["status"],
            claim["error"],
        ),
    )
    conn.execute(
        """
        UPDATE preview_provenance
        SET detection_status = ?,
            source_profile = ?,
            requested_preset_id = ?,
            applied_preset_id = ?,
            preset_version = ?,
            manifest_sha256 = ?,
            lut_sha256 = ?,
            transform_kind = ?,
            color_transform_status = ?,
            color_transform_error_code = ?
        WHERE asset_id = 1
        """,
        (
            claim["detection"],
            claim.get("profile"),
            claim["requested"],
            claim["applied"],
            claim["version"],
            claim["manifest"],
            claim["lut"],
            claim["kind"],
            claim["status"],
            claim["error"],
        ),
    )
    for sql in trigger_sql:
        conn.execute(sql)
    conn.execute(
        "UPDATE assets SET log_detection_status = ?, source_profile = ? WHERE id = 1",
        (claim["detection"], claim.get("profile")),
    )


@pytest.mark.parametrize(
    "variant",
    ["ordinary", "unknown", "apple_log_fallback"],
)
def test_candidate_trigger_and_evaluator_accept_allowed_formal_matrix(
    tmp_path,
    variant,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        _set_formal_claim(conn, variant)
        conn.commit()

    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        status = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0]
    assert evaluation.eligible is True
    assert status == SAFE_TO_DELETE_CANDIDATE


def test_candidate_evaluator_rejects_future_apple_log_applied_claim(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        _set_formal_claim(conn, "future_lut")
        conn.commit()

    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        status = conn.execute(
            "SELECT delete_candidate_status FROM assets WHERE id = 1"
        ).fetchone()[0]
    assert evaluation.reason == "formal_preview_provenance_invalid"
    assert status == NOT_CANDIDATE


def test_candidate_trigger_and_evaluator_reject_same_mixed_formal_claim(
    tmp_path,
):
    settings = _settings(tmp_path)
    _prepare_phase2c(settings)
    with connect(settings.database_path, 5000) as conn:
        conn.execute(
            "UPDATE assets SET delete_candidate_status = ? WHERE id = 1",
            (NOT_CANDIDATE,),
        )
        trigger_sql = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'prevent_preview_provenance_update'
            """
        ).fetchone()[0]
        conn.execute("DROP TRIGGER prevent_preview_provenance_update")
        conn.execute(
            """
            UPDATE preview_provenance
            SET source_profile = 'mismatch'
            WHERE asset_id = 1
            """
        )
        conn.execute(trigger_sql)

        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        with pytest.raises(
            sqlite3.IntegrityError,
            match="safe_delete_candidate_relation_invalid",
        ):
            conn.execute(
                """
                UPDATE assets
                SET delete_candidate_status = 'safe_to_delete_candidate'
                WHERE id = 1
                """
            )

    assert evaluation.reason == "formal_preview_provenance_invalid"


@pytest.mark.parametrize(
    "invalid_hash",
    [
        "invalid",
        b"a" * 64,
    ],
)
def test_candidate_trigger_rejects_equal_noncanonical_original_hashes(
    tmp_path,
    invalid_hash,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        conn.execute(
            """
            UPDATE upload_sessions
            SET expected_file_sha256 = ?
            WHERE asset_id = 1
            """,
            (invalid_hash,),
        )
        conn.execute(
            "UPDATE assets SET server_sha256 = ? WHERE id = 1",
            (invalid_hash,),
        )
        conn.commit()

    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        with pytest.raises(
            sqlite3.IntegrityError,
            match="safe_delete_candidate_relation_invalid",
        ):
            conn.execute(
                """
                UPDATE assets
                SET delete_candidate_status = 'safe_to_delete_candidate'
                WHERE id = 1
                """
            )

    assert evaluation.reason == "file_identity_mismatch"


def test_candidate_trigger_rejects_empty_future_lut_preset_version(
    tmp_path,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(conn)
        _set_formal_claim(conn, "future_lut")
        trigger_sql = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'prevent_preview_provenance_update'
            """
        ).fetchone()[0]
        conn.execute("DROP TRIGGER prevent_preview_provenance_update")
        conn.execute(
            """
            UPDATE preview_provenance
            SET preset_version = ''
            WHERE asset_id = 1
            """
        )
        conn.execute(trigger_sql)
        conn.commit()

    _apply(settings)

    with connect(settings.database_path, 5000) as conn:
        evaluation = evaluate_safe_delete_candidate(conn, asset_id=1)
        with pytest.raises(
            sqlite3.IntegrityError,
            match="safe_delete_candidate_relation_invalid",
        ):
            conn.execute(
                """
                UPDATE assets
                SET delete_candidate_status = 'safe_to_delete_candidate'
                WHERE id = 1
                """
            )

    assert evaluation.reason == "formal_preview_provenance_invalid"
