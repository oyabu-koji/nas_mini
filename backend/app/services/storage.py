from pathlib import Path


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
