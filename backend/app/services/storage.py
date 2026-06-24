from pathlib import Path
from uuid import uuid4


REQUIRED_DIRECTORIES = ("originals", "previews", "thumbnails", "jobs", "tmp")


class StorageError(RuntimeError):
    pass


def initialize_storage(media_root: Path) -> None:
    try:
        media_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError("MEDIA_ROOT cannot be initialized") from exc

    if not media_root.is_dir():
        raise StorageError("MEDIA_ROOT is not a directory")

    for directory in REQUIRED_DIRECTORIES:
        try:
            (media_root / directory).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError("MEDIA_ROOT required directories cannot be initialized") from exc

    _verify_write_access(media_root)


def _verify_write_access(media_root: Path) -> None:
    probe_path = media_root / "tmp" / ".write-check"
    try:
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        raise StorageError("MEDIA_ROOT is not writable") from exc


def generate_tmp_upload_path(media_root: Path) -> Path:
    return resolve_media_path(media_root, f"tmp/{uuid4().hex}.upload")


def generate_original_relative_path(filename: str) -> str:
    extension = _safe_extension(filename)
    generated_name = f"{uuid4().hex}{extension}"
    return f"originals/{generated_name}"


def resolve_media_path(media_root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise StorageError("media path must be relative")

    media_root_resolved = media_root.resolve()
    resolved_path = (media_root_resolved / relative_path).resolve()
    try:
        resolved_path.relative_to(media_root_resolved)
    except ValueError as exc:
        raise StorageError("media path escapes MEDIA_ROOT") from exc
    return resolved_path


def _safe_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 16:
        return ""
    extension = suffix[1:]
    if not extension.isalnum():
        return ""
    return suffix
