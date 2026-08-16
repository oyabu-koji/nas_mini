import sqlite3

import pytest

from app.db import detector_v2
from app.db.detector_v2 import (
    DETECTOR_V2_MIGRATION_VERSION,
    EXPECTED_PREVIOUS_MIGRATION_VERSION,
    EXPECTED_PREVIOUS_SCHEMA_SHA256,
    predecessor_schema_matches,
)
from app.db.detector_v2.schema import (
    DETECTOR_V2_METADATA_TABLE_SQL,
    DETECTOR_V2_METADATA_TABLE_SQL_SHA256,
    DETECTOR_V2_OBJECT_SQL_SHA256,
    DETECTOR_V2_REFERENCING_TRIGGER_NAMES,
    DETECTOR_V2_TRIGGER_SQL_SHA256,
    EXPECTED_DETECTION_IDENTITY_TRIGGER_SQL,
    assets_rebuild_table_sql,
    formal_preview_attempts_rebuild_table_sql,
    preview_provenance_rebuild_table_sql,
    detector_v2_schema_identity_sha256,
)
from app.db.phase2c import EXPECTED_ASSETS_TABLE_SQL as PHASE2C_ASSETS_TABLE_SQL
from app.db.phase_schema_identity import (
    PhaseSchemaIdentityError,
    resolve_managed_phase_schema,
)
from app.core.settings import Settings
from app.services.detector_v2_migration import apply_detector_v2_migration
from app.services.phase2c_migration import apply_phase2c_migration
from tests.phase2c_test_support import initialize_phase2b


def test_detector_v2_schema_package_is_importable():
    assert detector_v2.__doc__


def test_detector_v2_migration_version_is_fixed():
    assert DETECTOR_V2_MIGRATION_VERSION == "010_apple_log_container_signaling"


def test_detector_v2_preflight_pins_exact_009_predecessor_digest():
    assert EXPECTED_PREVIOUS_MIGRATION_VERSION == "009_safe_delete_candidate"
    assert EXPECTED_PREVIOUS_SCHEMA_SHA256 == (
        "0655f8bae3267bad74f60b6110084327a48c4cb010b60267288c858bb5822d6e"
    )
    assert predecessor_schema_matches() is True


@pytest.mark.parametrize(
    ("status", "profile", "accepted"),
    [
        ("apple_log", "apple-log-1", True),
        ("apple_log", "apple-log-2", True),
        ("apple_log", None, False),
        ("apple_log", "unexpected", False),
        ("not_evaluated", None, True),
        ("not_log", None, True),
        ("unknown", None, True),
        ("not_log", "apple-log-1", False),
        ("unknown", "apple-log-2", False),
    ],
)
def test_successor_assets_table_enforces_status_profile_pairs(
    status, profile, accepted
):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE processed_results (id TEXT PRIMARY KEY)")
    conn.execute(assets_rebuild_table_sql())
    operation = lambda: conn.execute(
        """
        INSERT INTO assets_detector_v2_new (
            type, filename, log_detection_status, source_profile
        ) VALUES ('video', 'fixture.mov', ?, ?)
        """,
        (status, profile),
    )

    if accepted:
        operation()
    else:
        with pytest.raises(sqlite3.IntegrityError):
            operation()


@pytest.mark.parametrize(
    ("status", "profile", "requested_preset", "accepted"),
    [
        ("apple_log", "apple-log-1", "generated-apple-log-rec709", True),
        ("apple_log", "apple-log-2", "generated-apple-log2-rec709", True),
        ("apple_log", "apple-log-1", "generated-apple-log2-rec709", False),
        ("apple_log", "apple-log-2", "generated-apple-log-rec709", False),
        ("apple_log", None, "generated-apple-log-rec709", False),
        ("not_log", None, "compress-only", True),
        ("unknown", None, "compress-only", True),
        ("not_log", "apple-log-1", "compress-only", False),
        ("unknown", None, "generated-apple-log-rec709", False),
        (None, None, None, True),
        (None, None, "compress-only", False),
        (None, "apple-log-1", None, False),
    ],
)
def test_successor_formal_attempt_enforces_profile_requested_preset_pairs(
    status, profile, requested_preset, accepted
):
    conn = sqlite3.connect(":memory:")
    conn.execute(formal_preview_attempts_rebuild_table_sql())
    operation = lambda: conn.execute(
        """
        INSERT INTO formal_preview_attempts_detector_v2_new (
            id, asset_id, job_id, preview_generation, state,
            detection_status, source_profile,
            detector_rule_version, detector_manifest_sha256,
            detector_evidence_sha256, detector_evidence_json,
            requested_preset_id
        ) VALUES (
            '0123456789abcdef0123456789abcdef', 1, 1, 1, 'probing',
            ?, ?,
            CASE WHEN ? IS NULL THEN NULL ELSE 'rule-v2' END,
            CASE WHEN ? IS NULL THEN NULL ELSE ? END,
            CASE WHEN ? IS NULL THEN NULL ELSE ? END,
            CASE WHEN ? IS NULL THEN NULL ELSE x'7b7d' END,
            ?
        )
        """,
        (
            status,
            profile,
            status,
            status,
            "a" * 64,
            status,
            "b" * 64,
            status,
            requested_preset,
        ),
    )

    if accepted:
        operation()
    else:
        with pytest.raises(sqlite3.IntegrityError):
            operation()


@pytest.mark.parametrize(
    (
        "status",
        "profile",
        "requested_preset",
        "applied_preset",
        "transform_kind",
        "transform_status",
        "error_code",
        "with_lut_identity",
        "accepted",
    ),
    [
        (
            "apple_log", "apple-log-1", "generated-apple-log-rec709",
            "compress-only", "none", "unavailable", "lut_preset_unavailable",
            False, True,
        ),
        (
            "apple_log", "apple-log-2", "generated-apple-log2-rec709",
            "compress-only", "none", "unavailable", "lut_preset_unavailable",
            False, True,
        ),
        (
            "apple_log", "apple-log-1", "generated-apple-log2-rec709",
            "compress-only", "none", "unavailable", "lut_preset_unavailable",
            False, False,
        ),
        (
            "apple_log", "apple-log-2", "generated-apple-log-rec709",
            "compress-only", "none", "unavailable", "lut_preset_unavailable",
            False, False,
        ),
        (
            "apple_log", None, "generated-apple-log-rec709", "compress-only",
            "none", "unavailable", "lut_preset_unavailable", False, False,
        ),
        (
            "not_log", None, "compress-only", "compress-only", "none",
            "not_requested", None, False, True,
        ),
        (
            "unknown", None, "compress-only", "compress-only", "none",
            "not_requested", None, False, True,
        ),
        (
            "not_log", "apple-log-1", "compress-only", "compress-only", "none",
            "not_requested", None, False, False,
        ),
        (
            "apple_log", "apple-log-1", "generated-apple-log-rec709",
            "generated-apple-log-rec709", "lut", "applied", None, True, True,
        ),
        (
            "apple_log", "apple-log-2", "generated-apple-log2-rec709",
            "generated-apple-log2-rec709", "lut", "applied", None, True, True,
        ),
        (
            "apple_log", "apple-log-2", "generated-apple-log2-rec709",
            "generated-apple-log-rec709", "lut", "applied", None, True, False,
        ),
        (
            "apple_log", None, "generated-apple-log-rec709",
            "generated-apple-log-rec709", "lut", "applied", None, True, False,
        ),
    ],
)
def test_successor_preview_provenance_enforces_profile_preset_relation(
    status,
    profile,
    requested_preset,
    applied_preset,
    transform_kind,
    transform_status,
    error_code,
    with_lut_identity,
    accepted,
):
    conn = sqlite3.connect(":memory:")
    conn.execute(preview_provenance_rebuild_table_sql())
    operation = lambda: conn.execute(
        """
        INSERT INTO preview_provenance_detector_v2_new (
            id, attempt_id, asset_id, preview_generation, result_id,
            derived_file_id, detection_status, source_profile,
            detector_rule_version, detector_manifest_sha256,
            detector_evidence_sha256, requested_preset_id, applied_preset_id,
            manifest_sha256, lut_sha256, transform_kind,
            color_transform_status, color_transform_error_code
        ) VALUES (
            '0123456789abcdef0123456789abcdef',
            'fedcba9876543210fedcba9876543210', 1, 1,
            'result-1', 1, ?, ?, 'rule-v2', ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            status,
            profile,
            "a" * 64,
            "b" * 64,
            requested_preset,
            applied_preset,
            "c" * 64 if with_lut_identity else None,
            "d" * 64 if with_lut_identity else None,
            transform_kind,
            transform_status,
            error_code,
        ),
    )

    if accepted:
        operation()
    else:
        with pytest.raises(sqlite3.IntegrityError):
            operation()


def test_detection_identity_trigger_watches_source_profile():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE processed_results (id TEXT PRIMARY KEY)")
    conn.execute(PHASE2C_ASSETS_TABLE_SQL)
    conn.execute(EXPECTED_DETECTION_IDENTITY_TRIGGER_SQL)
    asset_id = conn.execute(
        "INSERT INTO assets (type, filename) VALUES ('video', 'fixture.mov')"
    ).lastrowid

    with pytest.raises(
        sqlite3.IntegrityError,
        match="asset_detector_identity_invalid",
    ):
        conn.execute(
            "UPDATE assets SET source_profile = 'apple-log-1' WHERE id = ?",
            (asset_id,),
        )


def test_detector_v2_metadata_table_has_bounded_digest_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute(DETECTOR_V2_METADATA_TABLE_SQL)
    conn.execute(
        """
        INSERT INTO detector_v2_schema_metadata (
            version, predecessor_schema_sha256, schema_identity_sha256
        ) VALUES ('010_apple_log_container_signaling', ?, ?)
        """,
        ("a" * 64, "b" * 64),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO detector_v2_schema_metadata (
                version, predecessor_schema_sha256, schema_identity_sha256
            ) VALUES ('invalid', ?, ?)
            """,
            ("A" * 64, "b" * 64),
        )
    assert len(DETECTOR_V2_METADATA_TABLE_SQL_SHA256) == 64


def test_successor_referencing_trigger_set_and_digests_are_fixed():
    assert len(DETECTOR_V2_REFERENCING_TRIGGER_NAMES) == 26
    assert set(DETECTOR_V2_TRIGGER_SQL_SHA256) == (
        DETECTOR_V2_REFERENCING_TRIGGER_NAMES
    )
    assert all(
        len(digest) == 64 and digest == digest.lower()
        for digest in DETECTOR_V2_TRIGGER_SQL_SHA256.values()
    )


def test_successor_object_and_aggregate_schema_digests_are_fixed():
    assert set(DETECTOR_V2_OBJECT_SQL_SHA256) == {
        ("table", "assets"),
        ("table", "formal_preview_attempts"),
        ("table", "preview_provenance"),
        ("table", "detector_v2_schema_metadata"),
        ("index", "idx_assets_original_path"),
        ("index", "idx_formal_preview_attempts_asset_generation"),
    }
    assert all(
        len(digest) == 64
        for digest in DETECTOR_V2_OBJECT_SQL_SHA256.values()
    )
    assert len(detector_v2_schema_identity_sha256()) == 64


def test_detector_v2_partial_schema_presence_fails_closed():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY NOT NULL)"
    )
    conn.execute(DETECTOR_V2_METADATA_TABLE_SQL)

    with pytest.raises(
        PhaseSchemaIdentityError,
        match="detector_v2_migration_schema_identity_mismatch",
    ):
        resolve_managed_phase_schema(conn)


def test_detector_v2_identity_rejects_missing_inherited_managed_trigger(tmp_path):
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    settings = Settings(
        media_root=tmp_path / "media",
        api_token="test-token",
        database_path=tmp_path / "db.sqlite3",
        detector_root=tmp_path / "detector",
        built_in_preset_root=built_in,
        user_lut_root=user,
    )
    initialize_phase2b(settings)
    apply_phase2c_migration(
        settings=settings,
        offline_maintenance_confirmed=True,
        runtime_check=lambda _settings: True,
    )
    apply_detector_v2_migration(
        settings=settings,
        mode="apply",
        offline_maintenance_confirmed=True,
        api_stopped_confirmed=True,
        release_040_ready_confirmed=True,
        release_readiness_check=lambda _settings: True,
    )

    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("DROP TRIGGER prevent_completed_upload_chunk_insert")

    with pytest.raises(
        PhaseSchemaIdentityError,
        match="detector_v2_migration_schema_identity_mismatch",
    ):
        resolve_managed_phase_schema(conn)
    conn.close()
