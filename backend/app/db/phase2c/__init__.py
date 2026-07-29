from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path


PHASE2C_MIGRATION_VERSION = "009_safe_delete_candidate"
PHASE2C_SQL_PATH = Path(__file__).parent / f"{PHASE2C_MIGRATION_VERSION}.sql"
EXPECTED_PREVIOUS_MIGRATION_VERSION = "008_apple_log_formal_preview"

PHASE2C_TRIGGER_SQL_SHA256 = {
    "prevent_safe_delete_candidate_asset_insert": "8f064500ec4ef2b6d360857dcb75beec5ea0c6bcf79334d7c15e61bf2ed23265",
    "enforce_safe_delete_candidate_asset_update": "f3f6dbc20e775a392ceaa01e917eeb6a01afdefedebae8f0df26c747d8832c53",
    "prevent_completed_upload_session_update": "766ab50afdf900f519a4acfda9697b9daf4434d45b49fb7d81057d736ab353ab",
    "prevent_completed_upload_session_delete": "a8d426ccbbb17d982a7c70cbfda5ee460b0f661af0354b614520d9179faf2ebb",
    "prevent_completed_upload_chunk_insert": "fdadd26d9352ed48ae3486426df576928056642f98811b5a17e90c0b7d1faf1b",
    "prevent_completed_upload_chunk_update": "b4c2e9638d8c7029b48f1478fb5d2af240abb827d9353d067b3a28ffa4989166",
    "prevent_completed_upload_chunk_delete": "df20cfa01d19fd441f711068f6a452f49ebddcbeddf3df3515dca3fb836b6f18",
    "prevent_finalized_session_asset_update": "c005f17569a47a012dc189cb1cca73f90304d4d106d3691b1260892a4e6e4e20",
    "prevent_finalized_session_asset_delete": "a899dc4f4a396152a510fabd969463127c4cfc448df1f4fb292b310e9bd64c9a",
    "prevent_current_formal_derived_file_update": "f4e75e738cbc39b0bada0d6c945f87efe41f104458234e8e064c4da7ceee6dd6",
    "prevent_current_formal_derived_file_delete": "620730884249a51aef2b934253e6d0b8365f4b8196ed8a4461a7054e30884aec",
}

EXPECTED_ASSETS_TABLE_SQL = """CREATE TABLE "assets" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('image', 'video')),
    filename TEXT NOT NULL,
    original_path TEXT,
    size_bytes INTEGER,
    server_sha256 TEXT,
    taken_at TEXT,
    latitude REAL,
    longitude REAL,
    exif_json TEXT,
    is_log INTEGER NOT NULL DEFAULT 0 CHECK (is_log IN (0, 1)),
    transfer_status TEXT NOT NULL DEFAULT 'local_only',
    verification_status TEXT NOT NULL DEFAULT 'not_started',
    preview_status TEXT NOT NULL DEFAULT 'not_started',
    review_status TEXT NOT NULL DEFAULT 'not_reviewed',
    delete_candidate_status TEXT NOT NULL DEFAULT 'not_candidate',
    active_processed_result_id TEXT
        REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rendition_selection_generation INTEGER NOT NULL DEFAULT 0
        CHECK (rendition_selection_generation >= 0),
    preview_generation INTEGER NOT NULL DEFAULT 0
        CHECK (preview_generation >= 0),
    formal_preview_id TEXT
        REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED,
    log_detection_status TEXT NOT NULL DEFAULT 'not_evaluated'
        CHECK (
            log_detection_status IN (
                'not_evaluated', 'apple_log', 'not_log', 'unknown'
            )
        ),
    source_profile TEXT
        CHECK (source_profile IS NULL OR length(source_profile) BETWEEN 1 AND 128),
    detector_rule_version TEXT
        CHECK (
            detector_rule_version IS NULL
            OR length(detector_rule_version) BETWEEN 1 AND 64
        ),
    detector_manifest_sha256 TEXT
        CHECK (
            detector_manifest_sha256 IS NULL
            OR (
                length(detector_manifest_sha256) = 64
                AND detector_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
    detector_evidence_sha256 TEXT
        CHECK (
            detector_evidence_sha256 IS NULL
            OR (
                length(detector_evidence_sha256) = 64
                AND detector_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
    CONSTRAINT ck_assets_delete_candidate_status
        CHECK (
            delete_candidate_status IN (
                'not_candidate', 'safe_to_delete_candidate'
            )
        )
)"""

_TRANSACTION_TOKENS = frozenset(
    {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}
)


def schema_sql_sha256() -> str:
    return hashlib.sha256(PHASE2C_SQL_PATH.read_bytes()).hexdigest()


def assets_table_sql_sha256(sql: str = EXPECTED_ASSETS_TABLE_SQL) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def execute_phase2c_sql(
    conn: sqlite3.Connection,
    *,
    sql: str | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> int:
    if not conn.in_transaction:
        raise RuntimeError("phase2c_migration_transaction_lost")
    statement_count = 0
    for statement in iter_complete_statements(
        PHASE2C_SQL_PATH.read_text(encoding="utf-8") if sql is None else sql
    ):
        _reject_unsafe_statement(statement)
        if not conn.in_transaction:
            raise RuntimeError("phase2c_migration_transaction_lost")
        conn.execute(statement)
        statement_count += 1
        if not conn.in_transaction:
            raise RuntimeError("phase2c_migration_transaction_lost")
        if fault_injector is not None:
            fault_injector(f"after_statement_{statement_count}")
    return statement_count


def iter_complete_statements(sql: str) -> Iterator[str]:
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if _strip_leading_comments(pending).strip():
                yield pending.strip()
            pending = ""
    if _strip_leading_comments(pending).strip():
        raise RuntimeError("phase2c_migration_sql_incomplete")


def _reject_unsafe_statement(statement: str) -> None:
    body = _strip_leading_comments(statement).lstrip()
    token_match = re.match(r"([A-Za-z]+)", body)
    if token_match is None:
        raise RuntimeError("phase2c_migration_sql_invalid")
    first_token = token_match.group(1).upper()
    if first_token in _TRANSACTION_TOKENS:
        raise RuntimeError("phase2c_migration_transaction_control_not_allowed")
    if first_token == "PRAGMA" and re.match(
        r"PRAGMA\s+(?:main\.)?foreign_keys\b",
        body,
        flags=re.IGNORECASE,
    ):
        raise RuntimeError("phase2c_migration_foreign_keys_pragma_not_allowed")


def _strip_leading_comments(value: str) -> str:
    remaining = value
    while True:
        stripped = remaining.lstrip()
        if stripped.startswith("--"):
            newline = stripped.find("\n")
            return "" if newline < 0 else _strip_leading_comments(stripped[newline + 1 :])
        if stripped.startswith("/*"):
            end = stripped.find("*/", 2)
            return stripped if end < 0 else _strip_leading_comments(stripped[end + 2 :])
        return stripped
