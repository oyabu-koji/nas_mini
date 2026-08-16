import sqlite3
from hashlib import sha256

from app.db.phase2b import PHASE2B_SQL_PATH
from app.db.phase2c import (
    EXPECTED_ASSETS_TABLE_SQL as PHASE2C_ASSETS_TABLE_SQL,
    PHASE2C_SQL_PATH,
)


_MIGRATION_ROOT = PHASE2B_SQL_PATH.parent.parent / "migrations"
_TRIGGER_SOURCE_PATHS = (
    PHASE2C_SQL_PATH,
    PHASE2B_SQL_PATH,
    _MIGRATION_ROOT / "007_managed_preview_presets.sql",
    _MIGRATION_ROOT / "006_enforce_processed_result_lifecycle_immutability.sql",
    _MIGRATION_ROOT / "005_enforce_processed_result_derived_file_immutability.sql",
    _MIGRATION_ROOT / "004_processed_video_delivery.sql",
)

DETECTOR_V2_REFERENCING_TRIGGER_NAMES = frozenset(
    {
        "enforce_safe_delete_candidate_asset_update",
        "prevent_active_processed_result_supersede",
        "prevent_current_formal_derived_file_delete",
        "prevent_current_formal_derived_file_update",
        "prevent_current_formal_preview_supersede",
        "prevent_dual_formal_rendition_provenance",
        "prevent_finalized_session_asset_delete",
        "prevent_finalized_session_asset_update",
        "prevent_formal_preview_attempt_identity_update",
        "prevent_formal_preview_related_job_update",
        "prevent_new_asset_active_processed_result",
        "prevent_preview_provenance_delete",
        "prevent_preview_provenance_update",
        "prevent_processed_result_delete",
        "prevent_safe_delete_candidate_asset_insert",
        "prevent_terminal_formal_preview_attempt_delete",
        "prevent_terminal_formal_preview_attempt_update",
        "supersede_replaced_active_processed_result",
        "validate_active_processed_result",
        "validate_asset_detection_identity_update",
        "validate_formal_preview_attempt_insert",
        "validate_formal_preview_pointer",
        "validate_formal_preview_ready",
        "validate_managed_result_preview_generation",
        "validate_phase2b_preview_job_insert",
        "validate_preview_provenance_insert",
    }
)


ASSET_PROFILE_CONSTRAINT = """    CONSTRAINT ck_assets_detection_profile
        CHECK (
            (
                log_detection_status = 'apple_log'
                AND source_profile IS NOT NULL
                AND source_profile IN ('apple-log-1', 'apple-log-2')
            )
            OR (
                log_detection_status IN (
                    'not_evaluated', 'not_log', 'unknown'
                )
                AND source_profile IS NULL
            )
        ),
"""


EXPECTED_ASSETS_TABLE_SQL = PHASE2C_ASSETS_TABLE_SQL.replace(
    "    CONSTRAINT ck_assets_delete_candidate_status\n",
    ASSET_PROFILE_CONSTRAINT
    + "    CONSTRAINT ck_assets_delete_candidate_status\n",
)


FORMAL_ATTEMPT_PROFILE_CONSTRAINTS = """    CONSTRAINT ck_formal_attempt_detection_profile
        CHECK (
            (detection_status IS NULL AND source_profile IS NULL)
            OR (
                detection_status IS NOT NULL
                AND
                detection_status = 'apple_log'
                AND source_profile IS NOT NULL
                AND source_profile IN ('apple-log-1', 'apple-log-2')
            )
            OR (
                detection_status IS NOT NULL
                AND
                detection_status IN ('not_log', 'unknown')
                AND source_profile IS NULL
            )
        ),
    CONSTRAINT ck_formal_attempt_requested_preset
        CHECK (
            (detection_status IS NULL AND requested_preset_id IS NULL)
            OR (
                detection_status IS NOT NULL
                AND (
                    requested_preset_id IS NULL
                    OR (
                        detection_status = 'apple_log'
                        AND source_profile = 'apple-log-1'
                        AND requested_preset_id = 'generated-apple-log-rec709'
                    )
                    OR (
                        detection_status = 'apple_log'
                        AND source_profile = 'apple-log-2'
                        AND requested_preset_id = 'generated-apple-log2-rec709'
                    )
                    OR (
                        detection_status IN ('not_log', 'unknown')
                        AND source_profile IS NULL
                        AND requested_preset_id = 'compress-only'
                    )
                )
            )
        ),
"""

PREVIEW_PROVENANCE_PROFILE_CONSTRAINT = """    CONSTRAINT ck_preview_provenance_profile_preset
        CHECK (
            (
                detection_status = 'apple_log'
                AND source_profile IS NOT NULL
                AND source_profile = 'apple-log-1'
                AND requested_preset_id = 'generated-apple-log-rec709'
                AND applied_preset_id = 'compress-only'
                AND transform_kind = 'none'
                AND color_transform_status = 'unavailable'
                AND color_transform_error_code = 'lut_preset_unavailable'
                AND manifest_sha256 IS NULL
                AND lut_sha256 IS NULL
            )
            OR (
                detection_status = 'apple_log'
                AND source_profile IS NOT NULL
                AND source_profile = 'apple-log-2'
                AND requested_preset_id = 'generated-apple-log2-rec709'
                AND applied_preset_id = 'compress-only'
                AND transform_kind = 'none'
                AND color_transform_status = 'unavailable'
                AND color_transform_error_code = 'lut_preset_unavailable'
                AND manifest_sha256 IS NULL
                AND lut_sha256 IS NULL
            )
            OR (
                detection_status IN ('not_log', 'unknown')
                AND source_profile IS NULL
                AND requested_preset_id = 'compress-only'
                AND applied_preset_id = 'compress-only'
                AND transform_kind = 'none'
                AND color_transform_status = 'not_requested'
                AND color_transform_error_code IS NULL
                AND manifest_sha256 IS NULL
                AND lut_sha256 IS NULL
            )
            OR (
                detection_status = 'apple_log'
                AND source_profile IS NOT NULL
                AND source_profile = 'apple-log-1'
                AND requested_preset_id = 'generated-apple-log-rec709'
                AND applied_preset_id = 'generated-apple-log-rec709'
                AND transform_kind = 'lut'
                AND color_transform_status = 'applied'
                AND color_transform_error_code IS NULL
                AND manifest_sha256 IS NOT NULL
                AND lut_sha256 IS NOT NULL
            )
            OR (
                detection_status = 'apple_log'
                AND source_profile IS NOT NULL
                AND source_profile = 'apple-log-2'
                AND requested_preset_id = 'generated-apple-log2-rec709'
                AND applied_preset_id = 'generated-apple-log2-rec709'
                AND transform_kind = 'lut'
                AND color_transform_status = 'applied'
                AND color_transform_error_code IS NULL
                AND manifest_sha256 IS NOT NULL
                AND lut_sha256 IS NOT NULL
            )
        ),
"""


def _migration_statement(sql_path, prefix: str) -> str:
    pending = ""
    for line in sql_path.read_text(encoding="utf-8").splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement.startswith(prefix):
                return statement[:-1] if statement.endswith(";") else statement
            pending = ""
    raise RuntimeError(f"missing migration statement: {prefix}")


def _latest_trigger_statement(name: str) -> str:
    prefix = f"CREATE TRIGGER {name}"
    for sql_path in _TRIGGER_SOURCE_PATHS:
        try:
            return _migration_statement(sql_path, prefix)
        except RuntimeError:
            continue
    raise RuntimeError(f"missing predecessor trigger: {name}")


_PHASE2C_DETECTION_IDENTITY_TRIGGER_SQL = _migration_statement(
    PHASE2C_SQL_PATH,
    "CREATE TRIGGER validate_asset_detection_identity_update",
)
EXPECTED_DETECTION_IDENTITY_TRIGGER_SQL = (
    _PHASE2C_DETECTION_IDENTITY_TRIGGER_SQL.replace(
        "    log_detection_status,\n",
        "    log_detection_status,\n    source_profile,\n",
        1,
    )
    .replace(
        "        NEW.log_detection_status = 'not_evaluated'\n",
        "        NEW.log_detection_status = 'not_evaluated'\n"
        "        AND NEW.source_profile IS NULL\n",
        1,
    )
    .replace(
        "        NEW.log_detection_status IN ('apple_log', 'not_log', 'unknown')\n"
        "        AND NEW.detector_rule_version IS NOT NULL\n",
        "        (\n"
        "            (\n"
        "                NEW.log_detection_status = 'apple_log'\n"
        "                AND NEW.source_profile IN ('apple-log-1', 'apple-log-2')\n"
        "            )\n"
        "            OR (\n"
        "                NEW.log_detection_status IN ('not_log', 'unknown')\n"
        "                AND NEW.source_profile IS NULL\n"
        "            )\n"
        "        )\n"
        "        AND NEW.detector_rule_version IS NOT NULL\n",
        1,
    )
)


def _profile_aware_formal_preview_ready_trigger() -> str:
    sql = _latest_trigger_statement("validate_formal_preview_ready")
    needle = (
        "      AND processed_results.preview_generation = NEW.preview_generation\n"
        " )"
    )
    authority = """      AND processed_results.preview_generation = NEW.preview_generation
      AND (
          (
              preview_provenance.detection_status = 'apple_log'
              AND preview_provenance.source_profile = 'apple-log-1'
              AND preview_provenance.requested_preset_id = 'generated-apple-log-rec709'
              AND preview_provenance.applied_preset_id = 'compress-only'
              AND preview_provenance.transform_kind = 'none'
              AND preview_provenance.color_transform_status = 'unavailable'
          )
          OR (
              preview_provenance.detection_status = 'apple_log'
              AND preview_provenance.source_profile = 'apple-log-2'
              AND preview_provenance.requested_preset_id = 'generated-apple-log2-rec709'
              AND preview_provenance.applied_preset_id = 'compress-only'
              AND preview_provenance.transform_kind = 'none'
              AND preview_provenance.color_transform_status = 'unavailable'
          )
          OR (
              preview_provenance.detection_status IN ('not_log', 'unknown')
              AND preview_provenance.source_profile IS NULL
              AND preview_provenance.requested_preset_id = 'compress-only'
              AND preview_provenance.applied_preset_id = 'compress-only'
              AND preview_provenance.transform_kind = 'none'
              AND preview_provenance.color_transform_status = 'not_requested'
          )
      )
 )"""
    if needle not in sql:
        raise RuntimeError("formal preview ready trigger shape changed")
    return sql.replace(needle, authority, 1)


def _profile_aware_safe_delete_trigger() -> str:
    sql = _latest_trigger_statement(
        "enforce_safe_delete_candidate_asset_update"
    )
    first = sql.index(
        "              (\n"
        "                  preview_provenance.detection_status = 'apple_log'"
    )
    end = sql.index("          )\n    )\n )\nBEGIN", first)
    relation = """              (
                  preview_provenance.detection_status = 'apple_log'
                  AND preview_provenance.source_profile = 'apple-log-1'
                  AND preview_provenance.requested_preset_id = 'generated-apple-log-rec709'
                  AND preview_provenance.applied_preset_id = 'compress-only'
                  AND preview_provenance.transform_kind = 'none'
                  AND preview_provenance.color_transform_status = 'unavailable'
                  AND preview_provenance.color_transform_error_code = 'lut_preset_unavailable'
                  AND preview_provenance.preset_version IS NULL
                  AND preview_provenance.manifest_sha256 IS NULL
                  AND preview_provenance.lut_sha256 IS NULL
              )
              OR (
                  preview_provenance.detection_status = 'apple_log'
                  AND preview_provenance.source_profile = 'apple-log-2'
                  AND preview_provenance.requested_preset_id = 'generated-apple-log2-rec709'
                  AND preview_provenance.applied_preset_id = 'compress-only'
                  AND preview_provenance.transform_kind = 'none'
                  AND preview_provenance.color_transform_status = 'unavailable'
                  AND preview_provenance.color_transform_error_code = 'lut_preset_unavailable'
                  AND preview_provenance.preset_version IS NULL
                  AND preview_provenance.manifest_sha256 IS NULL
                  AND preview_provenance.lut_sha256 IS NULL
              )
              OR (
                  preview_provenance.detection_status IN ('not_log', 'unknown')
                  AND preview_provenance.source_profile IS NULL
                  AND preview_provenance.requested_preset_id = 'compress-only'
                  AND preview_provenance.applied_preset_id = 'compress-only'
                  AND preview_provenance.transform_kind = 'none'
                  AND preview_provenance.color_transform_status = 'not_requested'
                  AND preview_provenance.color_transform_error_code IS NULL
                  AND preview_provenance.preset_version IS NULL
                  AND preview_provenance.manifest_sha256 IS NULL
                  AND preview_provenance.lut_sha256 IS NULL
              )
"""
    return sql[:first] + relation + sql[end:]


EXPECTED_DETECTOR_V2_TRIGGER_SQL = {
    name: (
        EXPECTED_DETECTION_IDENTITY_TRIGGER_SQL
        if name == "validate_asset_detection_identity_update"
        else _profile_aware_formal_preview_ready_trigger()
        if name == "validate_formal_preview_ready"
        else _profile_aware_safe_delete_trigger()
        if name == "enforce_safe_delete_candidate_asset_update"
        else _latest_trigger_statement(name)
    )
    for name in DETECTOR_V2_REFERENCING_TRIGGER_NAMES
}

DETECTOR_V2_TRIGGER_SQL_SHA256 = {
    name: sha256(sql.encode("utf-8")).hexdigest()
    for name, sql in EXPECTED_DETECTOR_V2_TRIGGER_SQL.items()
}

DETECTOR_V2_METADATA_TABLE_SQL = """CREATE TABLE detector_v2_schema_metadata (
    version TEXT PRIMARY KEY NOT NULL,
    predecessor_schema_sha256 TEXT NOT NULL
        CHECK (
            length(predecessor_schema_sha256) = 64
            AND predecessor_schema_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    schema_identity_sha256 TEXT NOT NULL
        CHECK (
            length(schema_identity_sha256) = 64
            AND schema_identity_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

DETECTOR_V2_METADATA_TABLE_SQL_SHA256 = sha256(
    DETECTOR_V2_METADATA_TABLE_SQL.encode("utf-8")
).hexdigest()

EXPECTED_ASSETS_ORIGINAL_PATH_INDEX_SQL = """CREATE UNIQUE INDEX idx_assets_original_path
ON assets (original_path)
WHERE original_path IS NOT NULL"""
EXPECTED_FORMAL_ATTEMPTS_INDEX_SQL = """CREATE INDEX idx_formal_preview_attempts_asset_generation
ON formal_preview_attempts (asset_id, preview_generation DESC)"""

def _phase2b_table_sql(table_name: str, next_marker: str) -> str:
    migration_sql = PHASE2B_SQL_PATH.read_text(encoding="utf-8")
    start_marker = f"CREATE TABLE {table_name} ("
    start = migration_sql.index(start_marker)
    end = migration_sql.index(next_marker, start)
    table_sql = migration_sql[start:end].strip()
    return table_sql[:-1] if table_sql.endswith(";") else table_sql


_PHASE2B_FORMAL_PREVIEW_ATTEMPTS_TABLE_SQL = _phase2b_table_sql(
    "formal_preview_attempts",
    "\n\nCREATE TABLE preview_provenance",
)

EXPECTED_FORMAL_PREVIEW_ATTEMPTS_TABLE_SQL = (
    _PHASE2B_FORMAL_PREVIEW_ATTEMPTS_TABLE_SQL.replace(
        "    FOREIGN KEY (asset_id)",
        FORMAL_ATTEMPT_PROFILE_CONSTRAINTS + "    FOREIGN KEY (asset_id)",
        1,
    ).replace(
        "CREATE TABLE formal_preview_attempts",
        'CREATE TABLE "formal_preview_attempts"',
        1,
    )
)

_PHASE2B_PREVIEW_PROVENANCE_TABLE_SQL = _phase2b_table_sql(
    "preview_provenance",
    "\n\nCREATE INDEX idx_formal_preview_attempts_asset_generation",
)
_preview_relation_start = _PHASE2B_PREVIEW_PROVENANCE_TABLE_SQL.rindex(
    "    CHECK ("
)
_preview_relation_end = _PHASE2B_PREVIEW_PROVENANCE_TABLE_SQL.index(
    "    FOREIGN KEY (attempt_id)",
    _preview_relation_start,
)
EXPECTED_PREVIEW_PROVENANCE_TABLE_SQL = (
    _PHASE2B_PREVIEW_PROVENANCE_TABLE_SQL[:_preview_relation_start]
    + PREVIEW_PROVENANCE_PROFILE_CONSTRAINT
    + _PHASE2B_PREVIEW_PROVENANCE_TABLE_SQL[_preview_relation_end:]
).replace(
    "CREATE TABLE preview_provenance",
    'CREATE TABLE "preview_provenance"',
    1,
)

DETECTOR_V2_OBJECT_SQL_SHA256 = {
    ("table", "assets"): sha256(
        EXPECTED_ASSETS_TABLE_SQL.encode("utf-8")
    ).hexdigest(),
    ("table", "formal_preview_attempts"): sha256(
        EXPECTED_FORMAL_PREVIEW_ATTEMPTS_TABLE_SQL.encode("utf-8")
    ).hexdigest(),
    ("table", "preview_provenance"): sha256(
        EXPECTED_PREVIEW_PROVENANCE_TABLE_SQL.encode("utf-8")
    ).hexdigest(),
    ("table", "detector_v2_schema_metadata"):
        DETECTOR_V2_METADATA_TABLE_SQL_SHA256,
    ("index", "idx_assets_original_path"): sha256(
        EXPECTED_ASSETS_ORIGINAL_PATH_INDEX_SQL.encode("utf-8")
    ).hexdigest(),
    ("index", "idx_formal_preview_attempts_asset_generation"): sha256(
        EXPECTED_FORMAL_ATTEMPTS_INDEX_SQL.encode("utf-8")
    ).hexdigest(),
}


def detector_v2_schema_identity_sha256() -> str:
    rows = [
        f"{kind}:{name}:{digest}"
        for (kind, name), digest in sorted(DETECTOR_V2_OBJECT_SQL_SHA256.items())
    ]
    rows.extend(
        f"trigger:{name}:{digest}"
        for name, digest in sorted(DETECTOR_V2_TRIGGER_SQL_SHA256.items())
    )
    return sha256("\n".join(rows).encode("utf-8")).hexdigest()


def assets_rebuild_table_sql() -> str:
    return EXPECTED_ASSETS_TABLE_SQL.replace(
        'CREATE TABLE "assets"',
        "CREATE TABLE assets_detector_v2_new",
        1,
    )


def formal_preview_attempts_rebuild_table_sql() -> str:
    return EXPECTED_FORMAL_PREVIEW_ATTEMPTS_TABLE_SQL.replace(
        'CREATE TABLE "formal_preview_attempts"',
        "CREATE TABLE formal_preview_attempts_detector_v2_new",
        1,
    )


def preview_provenance_rebuild_table_sql() -> str:
    return EXPECTED_PREVIEW_PROVENANCE_TABLE_SQL.replace(
        'CREATE TABLE "preview_provenance"',
        "CREATE TABLE preview_provenance_detector_v2_new",
        1,
    )
