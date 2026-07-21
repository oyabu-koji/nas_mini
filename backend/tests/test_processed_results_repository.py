from app.db.connection import connect
from app.db.migrations import run_migrations
from app.repositories.assets import insert_asset
from app.repositories.derived_files import insert_derived_file
from app.repositories.processed_results import (
    RESULT_STATUS_SUPERSEDED,
    clear_active_processed_result,
    generate_result_id,
    get_active_processed_result,
    get_processed_result,
    insert_ready_processed_result,
    set_active_processed_result,
)


def _insert_asset_and_preview(conn):
    asset = insert_asset(
        conn,
        type="video",
        filename="clip.mov",
        original_path="originals/clip.mov",
        size_bytes=10,
        server_sha256="a" * 64,
        taken_at=None,
        latitude=None,
        longitude=None,
        exif_json=None,
        is_log=False,
    )
    derived = insert_derived_file(
        conn,
        asset_id=asset["id"],
        kind="preview",
        path="previews/clip.mp4",
        mime_type="video/mp4",
        size_bytes=10,
    )
    return asset, derived


def test_ready_result_insert_is_uuid_hex_idempotent_and_transaction_neutral(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        with conn:
            asset, derived = _insert_asset_and_preview(conn)

        result_id = generate_result_id()
        conn.execute("BEGIN IMMEDIATE")
        result, created = insert_ready_processed_result(
            conn,
            asset_id=asset["id"],
            derived_file_id=derived["id"],
            mime_type="video/mp4",
            size_bytes=10,
            sha256="b" * 64,
            result_id=result_id,
        )
        same_result, created_again = insert_ready_processed_result(
            conn,
            asset_id=asset["id"],
            derived_file_id=derived["id"],
            mime_type="video/mp4",
            size_bytes=10,
            sha256="b" * 64,
            result_id=result_id,
        )

        assert conn.in_transaction
        assert created is True
        assert created_again is False
        assert result == same_result
        assert len(result["id"]) == 32
        assert result["id"] == result["id"].lower()

        conn.rollback()
        assert get_processed_result(conn, asset_id=asset["id"], result_id=result_id) is None


def test_active_pointer_helpers_do_not_commit_and_supersede_old_result(tmp_path):
    database_path = tmp_path / "db.sqlite3"

    with connect(database_path, 5000) as conn:
        run_migrations(conn)
        with conn:
            asset, derived = _insert_asset_and_preview(conn)
            result, _ = insert_ready_processed_result(
                conn,
                asset_id=asset["id"],
                derived_file_id=derived["id"],
                mime_type="video/mp4",
                size_bytes=10,
                sha256="c" * 64,
                result_id="c" * 32,
            )

        conn.execute("BEGIN IMMEDIATE")
        set_active_processed_result(conn, asset_id=asset["id"], result_id=result["id"])
        active = get_active_processed_result(conn, asset_id=asset["id"])
        assert conn.in_transaction
        assert active is not None
        assert active["id"] == result["id"]

        clear_active_processed_result(conn, asset_id=asset["id"])
        assert conn.in_transaction
        conn.commit()

        active_after_clear = get_active_processed_result(conn, asset_id=asset["id"])
        superseded = get_processed_result(conn, asset_id=asset["id"], result_id=result["id"])

    assert active_after_clear is None
    assert superseded is not None
    assert superseded["status"] == RESULT_STATUS_SUPERSEDED
