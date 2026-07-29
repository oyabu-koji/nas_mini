from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from app.db.phase2b import PHASE2B_MIGRATION_VERSION
from app.db.phase2b import schema_sql_sha256 as phase2b_schema_sql_sha256
from app.db.phase2c import (
    EXPECTED_ASSETS_TABLE_SQL,
    PHASE2C_MIGRATION_VERSION,
    PHASE2C_TRIGGER_SQL_SHA256,
    assets_table_sql_sha256,
)
from app.db.phase2c import schema_sql_sha256 as phase2c_schema_sql_sha256


PHASE2B_ASSET_COLUMNS = frozenset(
    {
        "id",
        "type",
        "filename",
        "original_path",
        "size_bytes",
        "server_sha256",
        "taken_at",
        "latitude",
        "longitude",
        "exif_json",
        "is_log",
        "transfer_status",
        "verification_status",
        "preview_status",
        "review_status",
        "delete_candidate_status",
        "active_processed_result_id",
        "created_at",
        "updated_at",
        "rendition_selection_generation",
        "preview_generation",
        "formal_preview_id",
        "log_detection_status",
        "source_profile",
        "detector_rule_version",
        "detector_manifest_sha256",
        "detector_evidence_sha256",
    }
)
PHASE2B_JOB_COLUMNS = frozenset(
    {
        "id",
        "job_type",
        "status",
        "asset_id",
        "payload_json",
        "error_message",
        "claimed_at",
        "lease_expires_at",
        "created_at",
        "updated_at",
        "dedup_key",
        "preview_generation",
    }
)
PHASE2B_SIGNAL_ASSET_COLUMNS = frozenset(
    {
        "preview_generation",
        "formal_preview_id",
        "log_detection_status",
        "source_profile",
        "detector_rule_version",
        "detector_manifest_sha256",
        "detector_evidence_sha256",
    }
)
PHASE2B_EXCLUSIVE_TRIGGERS = frozenset(
    {
        "validate_phase2b_preview_job_insert",
        "prevent_phase2b_lut_preview_job_insert",
        "validate_non_preview_job_generation_insert",
        "validate_formal_preview_attempt_insert",
        "prevent_formal_preview_attempt_identity_update",
        "prevent_formal_preview_related_job_update",
        "validate_preview_provenance_insert",
        "prevent_current_formal_preview_supersede",
        "prevent_dual_formal_rendition_provenance",
        "validate_managed_result_preview_generation",
        "validate_asset_detection_identity_update",
        "validate_formal_preview_pointer",
        "validate_formal_preview_ready",
        "prevent_terminal_formal_preview_attempt_update",
        "prevent_terminal_formal_preview_attempt_delete",
        "prevent_preview_provenance_update",
        "prevent_preview_provenance_delete",
    }
)
PHASE2B_TRIGGER_SQL_SHA256 = {
    "prevent_current_formal_preview_supersede": "165cd908b9200fa13f88421ccfb13f929652950290505218995cb7cf8dd29a66",
    "prevent_dual_formal_rendition_provenance": "1d5c3099e9e722208d7134aa47ccad65fcb0e4eb44a19521fa1a0942e1414461",
    "prevent_formal_preview_attempt_identity_update": "80a157cc2089b1421af287d3bd8a9d7ea780d4deb1bb1cc89983d709422b6b5d",
    "prevent_formal_preview_related_job_update": "c1bde3d329014b0cce443deb4dbc2087c75942ebc32cb7b383b4770a5c9e6f2e",
    "prevent_phase2b_lut_preview_job_insert": "3d32a4173ea225d1e3f75150d3f70037a92fb37fbb2bcb843046bbcca86afbc3",
    "prevent_preview_provenance_delete": "aa7269117ad7bcda0a0c011ee926ea12f4da4bad28e763ae4ac4b765cbd510de",
    "prevent_preview_provenance_update": "e2960958737a09ab99f81bd4cc38bfb0db1632f0a0c71189be5b29ff2c9ad951",
    "prevent_terminal_formal_preview_attempt_delete": "91b9437cedd3d77bb31cc061bedbe22a2731f4a01898a91004633e6413e28e13",
    "prevent_terminal_formal_preview_attempt_update": "5b2746e208e9aabf749fe997312d772a9dab87161e72d3375d7843c6da08228e",
    "supersede_replaced_active_processed_result": "2d3cb600903ebe6ec7c328b17dd81f9ae0c7a083fbd7bd07cd845770dac4194e",
    "validate_active_processed_result": "7dbc2b2efa9b1bfaa3c2801cb49314868f0a65fb859a9e506a89ac8ff2fc5656",
    "validate_asset_detection_identity_update": "e9d3bd9272e0f2920591d725d034935fc053aa2bd0a026e42feef001e3fdf82a",
    "validate_formal_preview_attempt_insert": "ef0a256339d95690ec88dd92cd824eb74b5d637c9f2039ebbb299f880d721376",
    "validate_formal_preview_pointer": "0f299a7908de11da06012f28418b575d34f85f287d89ab966a528d3a30e75714",
    "validate_formal_preview_ready": "a7544c077adf414a05a3ac7f8e4632c15ca2b843bc69e7e802c37fc2e75eaed9",
    "validate_managed_result_preview_generation": "71cfcf38fe9d816864c5d23682b2d08c73e2f46ac59c0ebffbcfd50164ea6c49",
    "validate_non_preview_job_generation_insert": "9373add7c0c4ba83ec1d7d74f300756f0411280d946acee1dc0bc24c978f018b",
    "validate_phase2b_preview_job_insert": "e4a359d8da2abe6ff9057e8d69e34aa0897cebaa4d91b4fe52fa0fa39a353c27",
    "validate_preview_provenance_insert": "60d5495d689a588c21d9930aeadf1ee239fdbc0d92e28f2823bfed0e5f591480",
}
PHASE2B_OBJECT_SQL_SHA256 = {
    ("table", "phase2b_schema_metadata"): "6fe9486d4ff9069c122f14cd0615ff3a264b9a2e0c269890056f6a79328221e6",
    ("table", "formal_preview_attempts"): "321b83478f3786f8de3e4a937c17e8c4441bbbc2bc03eb5689e8b20eb9ea4a0e",
    ("table", "preview_provenance"): "04133062706f3ad65ccd952d37cdce9c87fb411541788a976d7d1609b86015c6",
    ("index", "idx_formal_preview_attempts_asset_generation"): "b86a90c0a0900d58125c5c825690843cd3e798752edc4e08d5fa478a3d96d8d0",
    ("index", "idx_jobs_preview_generation"): "5d64cca4f4a3d665f84c7f52b6f83d68dfd00aa61aea060c0d04ee9bc39f549a",
}
PHASE2C_METADATA_TABLE_SQL_SHA256 = (
    "148de6df7e800b1814085981d0372cc42ff7a675be6fc286b6092f370c8a96a8"
)


class PhaseSchemaIdentityError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ManagedPhaseSchemaState:
    phase2b_present: bool
    phase2b_valid: bool
    phase2c_present: bool
    phase2c_valid: bool
    minimum_client_version: str | None


def resolve_managed_phase_schema(
    conn: sqlite3.Connection,
) -> ManagedPhaseSchemaState:
    objects = _sqlite_objects(conn)
    asset_columns = _columns(conn, "assets")
    job_columns = _columns(conn, "jobs")
    markers = _migration_markers(conn)
    phase2b_present = _phase2b_present(
        objects=objects,
        asset_columns=asset_columns,
        job_columns=job_columns,
        markers=markers,
    )
    if phase2b_present:
        _validate_phase2b(
            conn,
            objects=objects,
            asset_columns=asset_columns,
            job_columns=job_columns,
            markers=markers,
        )

    phase2c_present = _phase2c_present(objects=objects, markers=markers)
    if phase2c_present:
        if not phase2b_present:
            raise PhaseSchemaIdentityError(
                "phase2b_migration_schema_identity_mismatch"
            )
        _validate_phase2c(conn, objects=objects, markers=markers)

    minimum = "0.3.0" if phase2c_present else "0.2.0" if phase2b_present else None
    return ManagedPhaseSchemaState(
        phase2b_present=phase2b_present,
        phase2b_valid=phase2b_present,
        phase2c_present=phase2c_present,
        phase2c_valid=phase2c_present,
        minimum_client_version=minimum,
    )


def _phase2b_present(*, objects, asset_columns, job_columns, markers) -> bool:
    names = {name for _, name in objects}
    return bool(
        PHASE2B_MIGRATION_VERSION in markers
        or "phase2b_schema_metadata" in names
        or {"formal_preview_attempts", "preview_provenance"} & names
        or {
            "idx_formal_preview_attempts_asset_generation",
            "idx_jobs_preview_generation",
        }
        & names
        or PHASE2B_SIGNAL_ASSET_COLUMNS & asset_columns
        or "preview_generation" in job_columns
        or PHASE2B_EXCLUSIVE_TRIGGERS & names
    )


def _phase2c_present(*, objects, markers) -> bool:
    names = {name for _, name in objects}
    assets_sql = objects.get(("table", "assets"), "")
    return bool(
        PHASE2C_MIGRATION_VERSION in markers
        or "phase2c_schema_metadata" in names
        or "ck_assets_delete_candidate_status" in assets_sql
        or set(PHASE2C_TRIGGER_SQL_SHA256) & names
    )


def _validate_phase2b(
    conn,
    *,
    objects,
    asset_columns,
    job_columns,
    markers,
) -> None:
    try:
        if PHASE2B_MIGRATION_VERSION not in markers:
            raise ValueError
        rows = conn.execute(
            """
            SELECT version, schema_sql_sha256
            FROM phase2b_schema_metadata
            """
        ).fetchall()
        if (
            len(rows) != 1
            or rows[0]["version"] != PHASE2B_MIGRATION_VERSION
            or rows[0]["schema_sql_sha256"] != phase2b_schema_sql_sha256()
            or asset_columns != PHASE2B_ASSET_COLUMNS
            or job_columns != PHASE2B_JOB_COLUMNS
        ):
            raise ValueError
        expected = {
            **PHASE2B_OBJECT_SQL_SHA256,
            **{
                ("trigger", name): digest
                for name, digest in PHASE2B_TRIGGER_SQL_SHA256.items()
            },
        }
        if not _object_digests_match(objects, expected):
            raise ValueError
        if _reserved_phase2b_objects(objects) != set(expected):
            raise ValueError
    except (sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
        raise PhaseSchemaIdentityError(
            "phase2b_migration_schema_identity_mismatch"
        ) from exc


def _validate_phase2c(conn, *, objects, markers) -> None:
    try:
        if PHASE2C_MIGRATION_VERSION not in markers:
            raise ValueError
        rows = conn.execute(
            """
            SELECT version, schema_sql_sha256, assets_table_sql_sha256
            FROM phase2c_schema_metadata
            """
        ).fetchall()
        actual_assets_sql = objects.get(("table", "assets"))
        if (
            len(rows) != 1
            or rows[0]["version"] != PHASE2C_MIGRATION_VERSION
            or rows[0]["schema_sql_sha256"] != phase2c_schema_sql_sha256()
            or rows[0]["assets_table_sql_sha256"] != assets_table_sql_sha256()
            or actual_assets_sql != EXPECTED_ASSETS_TABLE_SQL
            or "ck_assets_delete_candidate_status" not in actual_assets_sql
            or _sha256(objects.get(("table", "phase2c_schema_metadata")))
            != PHASE2C_METADATA_TABLE_SQL_SHA256
        ):
            raise ValueError
        expected = {
            ("trigger", name): digest
            for name, digest in PHASE2C_TRIGGER_SQL_SHA256.items()
        }
        if not _object_digests_match(objects, expected):
            raise ValueError
        expected_reserved = {
            ("table", "phase2c_schema_metadata"),
            *{("trigger", name) for name in PHASE2C_TRIGGER_SQL_SHA256},
        }
        if _reserved_phase2c_objects(objects) != expected_reserved:
            raise ValueError
    except (sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
        raise PhaseSchemaIdentityError(
            "phase2c_migration_schema_identity_mismatch"
        ) from exc


def _reserved_phase2c_objects(objects) -> set[tuple[str, str]]:
    reserved = set()
    for object_type, name in objects:
        if object_type not in {"table", "index", "trigger"}:
            continue
        if (
            name.startswith("phase2c_")
            or "safe_delete_candidate" in name
            or name.startswith("prevent_completed_upload_")
            or name.startswith("prevent_finalized_session_")
            or name.startswith("prevent_current_formal_derived_file_")
        ):
            reserved.add((object_type, name))
    return reserved


def _reserved_phase2b_objects(objects) -> set[tuple[str, str]]:
    expected_names = {
        name for _, name in PHASE2B_OBJECT_SQL_SHA256
    } | set(PHASE2B_TRIGGER_SQL_SHA256)
    return {
        (object_type, name)
        for object_type, name in objects
        if (
            name in expected_names
            or name.startswith("phase2b_")
            or name.startswith("formal_preview_")
            or name.startswith("preview_provenance_")
            or name.startswith("idx_formal_preview_")
            or name.startswith("idx_jobs_preview_")
        )
    }


def _object_digests_match(objects, expected) -> bool:
    return all(_sha256(objects.get(key)) == digest for key, digest in expected.items())


def _sqlite_objects(conn) -> dict[tuple[str, str], str]:
    return {
        (row["type"], row["name"]): row["sql"]
        for row in conn.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger') AND sql IS NOT NULL
            """
        )
    }


def _columns(conn, table: str) -> frozenset[str]:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is None:
        return frozenset()
    return frozenset(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))


def _migration_markers(conn) -> frozenset[str]:
    if conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone() is None:
        return frozenset()
    return frozenset(
        row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
    )


def _sha256(value: str | None) -> str | None:
    return (
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        if isinstance(value, str)
        else None
    )
