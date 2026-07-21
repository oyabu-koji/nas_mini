import re
from datetime import datetime, timezone
from pathlib import Path

from app.core.settings import Settings
from app.db.connection import connect


GENERATED_PREVIEW_NAME = re.compile(r"^[0-9a-f]{32}\.(?:mp4|jpg)$")


def recover_unreferenced_generated_previews(
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Remove only old, generated previews that no DB row references anymore."""
    preview_directory = settings.media_root / "previews"
    if not preview_directory.is_dir():
        return 0

    current_time = now or datetime.now(timezone.utc)
    minimum_age_seconds = (
        settings.job_lease_seconds + settings.processed_result_recovery_grace_seconds
    )
    cutoff = current_time.timestamp() - minimum_age_seconds
    removed_count = 0

    for candidate_path in preview_directory.iterdir():
        if not _is_expired_generated_preview(candidate_path, preview_directory, cutoff):
            continue
        relative_path = f"previews/{candidate_path.name}"

        with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                referenced = conn.execute(
                    "SELECT 1 FROM derived_files WHERE path = ? LIMIT 1",
                    (relative_path,),
                ).fetchone()
                if referenced is None:
                    try:
                        candidate_path.unlink()
                    except FileNotFoundError:
                        pass
                    else:
                        removed_count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    return removed_count


def _is_expired_generated_preview(
    candidate_path: Path,
    preview_directory: Path,
    cutoff: float,
) -> bool:
    if candidate_path.parent != preview_directory:
        return False
    if not GENERATED_PREVIEW_NAME.fullmatch(candidate_path.name):
        return False
    try:
        return candidate_path.is_file() and candidate_path.stat().st_mtime <= cutoff
    except OSError:
        return False
