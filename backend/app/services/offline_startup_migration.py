from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parents[1] / "db" / "migrations"
MIGRATION_CONTRACT = (
    (
        "001_initial",
        "ad1070489641a6d964a44415d24d0e62702aab80da01159b9e591f75534a2f35",
    ),
    (
        "002_invalidate_identity_log_previews",
        "3868eca4e6b21390fd60fe00982b24ec8d093956e4b33c59f661f2d13353fedd",
    ),
    (
        "003_phase2a_resumable_uploads",
        "aadf4575a23b93805df17423d6b764ebf8dc3e57547209e2729e9f169e4e260e",
    ),
    (
        "004_processed_video_delivery",
        "053678c304a2bcae581e382ebea4c907f8a52409bea62081b11c8c75958ead75",
    ),
    (
        "005_enforce_processed_result_derived_file_immutability",
        "e11ff60be8853b3e25a80a2991f28a78a0636cdd962c04b0719ff07d72da3ef3",
    ),
    (
        "006_enforce_processed_result_lifecycle_immutability",
        "a93270b5f1c90b519e12d6275f41791174500332b50f0d1d61087875e7aa9f89",
    ),
    (
        "007_managed_preview_presets",
        "06198b6aef5d2936ce9e16ef520dfe868d4b4f6aac724c6313e87de2be61efd9",
    ),
)

FaultInjector = Callable[[str], None]
SqlLoader = Callable[[str], str]


class OfflineStartupMigrationError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        last_committed_version: str | None = None,
        restore_required: bool = False,
    ):
        super().__init__(code)
        self.code = code
        self.last_committed_version = last_committed_version
        self.restore_required = restore_required


@dataclass(frozen=True)
class OfflineStartupMigrationResult:
    status: str
    last_committed_version: str
    applied_count: int
    restore_required: bool = False


def apply_offline_startup_migrations(
    *,
    database_path: Path,
    offline_maintenance_confirmed: bool,
    busy_timeout_ms: int = 5000,
    fault_injector: FaultInjector | None = None,
    sql_loader: SqlLoader | None = None,
) -> OfflineStartupMigrationResult:
    """Apply exact startup migrations 002-007 without application startup."""
    if not offline_maintenance_confirmed:
        raise OfflineStartupMigrationError(
            "offline_migration_maintenance_confirmation_required"
        )
    if not database_path.is_file() or database_path.is_symlink():
        raise OfflineStartupMigrationError("offline_migration_database_invalid")
    if busy_timeout_ms <= 0:
        raise OfflineStartupMigrationError("offline_migration_configuration_invalid")

    load_sql = sql_loader or _load_sql
    sql_by_version = _load_and_verify_sql(load_sql)
    expected_schema = _build_expected_schema_identities(sql_by_version)

    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        versions = _read_exact_versions(conn)
        last_committed = versions[-1] if versions else None
        if versions == [version for version, _digest in MIGRATION_CONTRACT]:
            _require_database_valid(conn, expected_schema[last_committed])
            return OfflineStartupMigrationResult(
                status="already_complete",
                last_committed_version=last_committed,
                applied_count=0,
            )
        if versions != [MIGRATION_CONTRACT[0][0]]:
            raise OfflineStartupMigrationError(
                "offline_migration_partial_commit_restore_required",
                last_committed_version=last_committed,
                restore_required=bool(last_committed),
            )
        _require_database_valid(conn, expected_schema[MIGRATION_CONTRACT[0][0]])

        applied_count = 0
        for version, _digest in MIGRATION_CONTRACT[1:]:
            try:
                _apply_one(
                    conn,
                    version=version,
                    sql=sql_by_version[version],
                    fault_injector=fault_injector,
                )
                applied_count += 1
                last_committed = version
                _require_database_valid(conn, expected_schema[version])
                _inject(fault_injector, f"after_{version}_validation")
            except Exception as exc:
                if conn.in_transaction:
                    conn.rollback()
                committed = _read_exact_versions(conn)
                current = committed[-1] if committed else None
                restore_required = current != MIGRATION_CONTRACT[0][0]
                code = (
                    "offline_migration_partial_commit_restore_required"
                    if restore_required
                    else "offline_migration_failed"
                )
                if isinstance(exc, OfflineStartupMigrationError):
                    code = exc.code
                raise OfflineStartupMigrationError(
                    code,
                    last_committed_version=current,
                    restore_required=restore_required,
                ) from exc

        return OfflineStartupMigrationResult(
            status="applied",
            last_committed_version=MIGRATION_CONTRACT[-1][0],
            applied_count=applied_count,
        )
    except sqlite3.DatabaseError as exc:
        if conn.in_transaction:
            conn.rollback()
        raise OfflineStartupMigrationError(
            "offline_migration_database_invalid"
        ) from exc
    finally:
        conn.close()


def _load_and_verify_sql(load_sql: SqlLoader) -> dict[str, str]:
    result = {}
    for version, expected_digest in MIGRATION_CONTRACT:
        sql = load_sql(version)
        actual = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if actual != expected_digest:
            raise OfflineStartupMigrationError(
                "offline_migration_sql_identity_mismatch"
            )
        result[version] = sql
    return result


def _load_sql(version: str) -> str:
    return (MIGRATIONS_DIR / f"{version}.sql").read_text(encoding="utf-8")


def _read_exact_versions(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY applied_at, rowid"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise OfflineStartupMigrationError(
            "offline_migration_precondition_changed"
        ) from exc
    versions = [str(row["version"]) for row in rows]
    expected = [version for version, _digest in MIGRATION_CONTRACT]
    if len(versions) != len(set(versions)) or versions != expected[: len(versions)]:
        raise OfflineStartupMigrationError(
            "offline_migration_precondition_changed",
            last_committed_version=versions[-1] if versions else None,
            restore_required=len(versions) > 1,
        )
    return versions


def _apply_one(
    conn: sqlite3.Connection,
    *,
    version: str,
    sql: str,
    fault_injector: FaultInjector | None,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        for index, statement in enumerate(_iter_statements(sql), start=1):
            conn.execute(statement)
            _inject(fault_injector, f"after_{version}_statement_{index}")
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (version,),
        )
        _inject(fault_injector, f"after_{version}_marker")
        conn.commit()
        _inject(fault_injector, f"after_{version}_commit")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _iter_statements(sql: str) -> Iterator[str]:
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                yield pending.strip()
            pending = ""
    if pending.strip():
        raise OfflineStartupMigrationError("offline_migration_sql_invalid")


def _build_expected_schema_identities(
    sql_by_version: dict[str, str],
) -> dict[str, str]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    identities = {}
    try:
        for version, _digest in MIGRATION_CONTRACT:
            _apply_one(
                conn,
                version=version,
                sql=sql_by_version[version],
                fault_injector=None,
            )
            identities[version] = _schema_identity(conn)
        return identities
    finally:
        conn.close()


def _schema_identity(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    canonical = "\n".join(
        "\x1f".join(str(row[key]) for key in ("type", "name", "tbl_name", "sql"))
        for row in rows
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_database_valid(conn: sqlite3.Connection, expected_schema: str) -> None:
    if _schema_identity(conn) != expected_schema:
        raise OfflineStartupMigrationError("offline_migration_schema_identity_mismatch")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise OfflineStartupMigrationError("offline_migration_integrity_invalid")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise OfflineStartupMigrationError("offline_migration_foreign_key_invalid")


def _inject(injector: FaultInjector | None, step: str) -> None:
    if injector is not None:
        injector(step)
