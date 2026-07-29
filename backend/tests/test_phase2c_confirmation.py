import pytest

from app.core.settings import Settings
from app.db.connection import connect
from app.services.asset_read import (
    PreviewProvenanceInvalidError,
    confirm_preview,
)
from app.services.phase2c_migration import apply_phase2c_migration
from tests.phase2c_test_support import (
    initialize_phase2b,
    insert_eligible_confirmed_asset,
)


def _settings(tmp_path):
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
    )
    (settings.media_root / "previews").mkdir(parents=True)
    return settings


def _prepare(settings, *, review_status="not_reviewed"):
    content = b"phase2c-formal-preview"
    with connect(settings.database_path, 5000) as conn:
        insert_eligible_confirmed_asset(
            conn,
            review_status=review_status,
            result_bytes=content,
        )
        conn.commit()
    (settings.media_root / "previews/1.mp4").write_bytes(content)
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )


def test_confirmation_promotes_review_and_candidate_atomically_and_repeats(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings)

    first = confirm_preview(
        settings,
        asset_id=1,
        allow_candidate_promotion=True,
    )
    repeated = confirm_preview(
        settings,
        asset_id=1,
        allow_candidate_promotion=True,
    )

    assert first.review_status == "preview_confirmed"
    assert first.delete_candidate_status == "safe_to_delete_candidate"
    assert repeated.review_status == "preview_confirmed"
    assert repeated.delete_candidate_status == "safe_to_delete_candidate"


def test_confirmation_runtime_false_records_review_without_new_promotion(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings)

    response = confirm_preview(
        settings,
        asset_id=1,
        allow_candidate_promotion=False,
    )

    assert response.review_status == "preview_confirmed"
    assert response.delete_candidate_status == "not_candidate"


def test_confirmation_failure_after_review_rolls_back_review_and_candidate(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings)

    def fail(step):
        if step == "after_review":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        confirm_preview(
            settings,
            asset_id=1,
            allow_candidate_promotion=True,
            fault_injector=fail,
        )

    with connect(settings.database_path, 5000) as conn:
        row = conn.execute(
            """
            SELECT review_status, delete_candidate_status
            FROM assets WHERE id = 1
            """
        ).fetchone()
    assert tuple(row) == ("not_reviewed", "not_candidate")


def test_confirmation_rejects_relation_changed_after_filesystem_preflight(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings)

    def change_relation(step):
        if step != "after_preflight":
            return
        with connect(settings.database_path, 5000) as writer:
            writer.execute(
                """
                UPDATE assets
                SET formal_preview_id = NULL,
                    preview_status = 'preview_generating',
                    review_status = 'not_reviewed',
                    delete_candidate_status = 'not_candidate'
                WHERE id = 1
                """
            )
            writer.commit()

    with pytest.raises(PreviewProvenanceInvalidError):
        confirm_preview(
            settings,
            asset_id=1,
            allow_candidate_promotion=True,
            fault_injector=change_relation,
        )

    with connect(settings.database_path, 5000) as conn:
        row = conn.execute(
            """
            SELECT review_status, delete_candidate_status
            FROM assets WHERE id = 1
            """
        ).fetchone()
    assert tuple(row) == ("not_reviewed", "not_candidate")


def _mutate_with_restored_triggers(settings, trigger_names, statement):
    with connect(settings.database_path, 5000) as conn:
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
        if statement == "derived_file_id":
            new_derived_id = conn.execute(
                """
                INSERT INTO derived_files (
                    asset_id, kind, path, mime_type, size_bytes
                ) VALUES (1, 'preview', 'previews/other.mp4', 'video/mp4', 22)
                """
            ).lastrowid
            conn.execute(
                """
                UPDATE processed_results SET derived_file_id = ?
                WHERE id = (SELECT formal_preview_id FROM assets WHERE id = 1)
                """,
                (new_derived_id,),
            )
        elif statement == "result_asset_id":
            conn.execute(
                """
                INSERT INTO assets (
                    id, type, filename, transfer_status,
                    verification_status, preview_status
                ) VALUES (
                    2, 'video', 'other.mov', 'transferred',
                    'file_verified', 'preview_generating'
                )
                """
            )
            conn.execute(
                """
                UPDATE processed_results SET asset_id = 2
                WHERE id = (SELECT formal_preview_id FROM assets WHERE id = 1)
                """
            )
        else:
            conn.execute(statement)
        for sql in trigger_sql:
            conn.execute(sql)
        conn.commit()


@pytest.mark.parametrize(
    ("trigger_names", "mutation"),
    [
        (
            (
                "prevent_current_formal_derived_file_update",
                "prevent_processed_result_derived_file_update",
            ),
            "UPDATE derived_files SET path = 'previews/changed.mp4' WHERE id = 1",
        ),
        (
            (
                "prevent_current_formal_derived_file_update",
                "prevent_processed_result_derived_file_update",
            ),
            "UPDATE derived_files SET mime_type = 'video/quicktime' WHERE id = 1",
        ),
        (
            (
                "prevent_current_formal_derived_file_update",
                "prevent_processed_result_derived_file_update",
            ),
            "UPDATE derived_files SET size_bytes = 99 WHERE id = 1",
        ),
        (
            ("prevent_ready_processed_result_update",),
            "UPDATE processed_results SET mime_type = 'video/quicktime' WHERE id = (SELECT formal_preview_id FROM assets WHERE id = 1)",
        ),
        (
            ("prevent_ready_processed_result_update",),
            "UPDATE processed_results SET size_bytes = 99 WHERE id = (SELECT formal_preview_id FROM assets WHERE id = 1)",
        ),
        (
            ("prevent_ready_processed_result_update",),
            f"UPDATE processed_results SET sha256 = '{'f' * 64}' WHERE id = (SELECT formal_preview_id FROM assets WHERE id = 1)",
        ),
        (
            (
                "prevent_ready_processed_result_update",
                "prevent_processed_result_derived_file_update",
            ),
            "derived_file_id",
        ),
        (
            (
                "prevent_ready_processed_result_update",
                "prevent_processed_result_derived_file_mismatch_update",
            ),
            "result_asset_id",
        ),
        (
            (),
            "UPDATE assets SET preview_generation = 2 WHERE id = 1",
        ),
    ],
)
def test_confirmation_snapshot_field_race_rejects_without_write(
    tmp_path,
    trigger_names,
    mutation,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings)

    def mutate(step):
        if step == "after_preflight":
            _mutate_with_restored_triggers(
                settings,
                trigger_names,
                mutation,
            )

    with pytest.raises(PreviewProvenanceInvalidError):
        confirm_preview(
            settings,
            asset_id=1,
            allow_candidate_promotion=True,
            fault_injector=mutate,
        )

    with connect(settings.database_path, 5000) as conn:
        row = conn.execute(
            """
            SELECT review_status, delete_candidate_status
            FROM assets WHERE id = 1
            """
        ).fetchone()
    assert tuple(row) == ("not_reviewed", "not_candidate")


def test_confirmation_runtime_false_preserves_valid_existing_safe(tmp_path):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings, review_status="preview_confirmed")

    response = confirm_preview(
        settings,
        asset_id=1,
        allow_candidate_promotion=False,
    )

    assert response.delete_candidate_status == "safe_to_delete_candidate"


def test_confirmation_runtime_false_demotes_relationally_invalid_existing_safe(
    tmp_path,
):
    settings = _settings(tmp_path)
    initialize_phase2b(settings)
    _prepare(settings, review_status="preview_confirmed")

    def invalidate_upload_identity(step):
        if step == "after_preflight":
            _mutate_with_restored_triggers(
                settings,
                ("prevent_completed_upload_session_update",),
                (
                    "UPDATE upload_sessions "
                    f"SET expected_file_sha256 = '{'f' * 64}' "
                    "WHERE asset_id = 1"
                ),
            )

    response = confirm_preview(
        settings,
        asset_id=1,
        allow_candidate_promotion=False,
        fault_injector=invalidate_upload_identity,
    )

    assert response.review_status == "preview_confirmed"
    assert response.delete_candidate_status == "not_candidate"
