import os
import shutil
import stat
from pathlib import Path
from uuid import UUID, uuid4


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


def generate_tmp_preview_path(media_root: Path, extension: str) -> Path:
    return resolve_media_path(media_root, f"tmp/{uuid4().hex}{_normalize_extension(extension)}")


def generate_original_relative_path(filename: str) -> str:
    extension = _safe_extension(filename)
    generated_name = f"{uuid4().hex}{extension}"
    return f"originals/{generated_name}"


def generate_preview_relative_path(extension: str) -> str:
    return f"previews/{uuid4().hex}{_normalize_extension(extension)}"


def generate_rendition_candidate_path(media_root: Path, rendition_id: str) -> Path:
    normalized = _lower_hex_id(rendition_id)
    return resolve_media_path(
        media_root,
        f"tmp/renditions/{normalized}-{uuid4().hex}.candidate.mp4",
    )


def generate_rendition_relative_path(rendition_id: str) -> str:
    return f"previews/renditions/{_lower_hex_id(rendition_id)}.mp4"


def promote_rendition_candidate(
    media_root: Path, *, candidate_path: Path, rendition_id: str
) -> tuple[str, Path]:
    relative_path = generate_rendition_relative_path(rendition_id)
    final_path = resolve_media_path(media_root, relative_path)
    expected_candidate_root = resolve_media_path(media_root, "tmp/renditions")
    try:
        candidate_path.resolve(strict=True).relative_to(expected_candidate_root.resolve())
    except (OSError, ValueError) as exc:
        raise StorageError("rendition candidate path is invalid") from exc
    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(candidate_path, final_path)
        candidate_path.unlink()
    except FileExistsError as exc:
        raise StorageError("rendition output already exists") from exc
    except OSError as exc:
        raise StorageError("rendition output cannot be promoted") from exc
    return relative_path, final_path


def cleanup_uncommitted_rendition_output(media_root: Path, rendition_id: str) -> None:
    """Remove only the backend-owned final file for a nonterminal rendition."""
    filename = f"{_lower_hex_id(rendition_id)}.mp4"
    descriptors: list[int] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(media_root.resolve(), flags)
        descriptors.append(root_fd)
        previews_fd = os.open("previews", flags, dir_fd=root_fd)
        descriptors.append(previews_fd)
        renditions_fd = os.open("renditions", flags, dir_fd=previews_fd)
        descriptors.append(renditions_fd)
        try:
            metadata = os.stat(filename, dir_fd=renditions_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(metadata.st_mode):
            raise StorageError("rendition output path is not a file")
        os.unlink(filename, dir_fd=renditions_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StorageError("rendition output cannot be cleaned up") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def generate_session_original_relative_path(session_id: str, filename: str) -> str:
    return f"originals/sessions/{_session_id(session_id)}{_safe_extension(filename)}"


def generate_session_chunk_path(media_root: Path, session_id: str, chunk_index: int) -> Path:
    return resolve_media_path(
        media_root,
        f"tmp/upload-sessions/{_session_id(session_id)}/chunks/{_chunk_index(chunk_index)}.part",
    )


def generate_session_assembly_path(media_root: Path, session_id: str) -> Path:
    return resolve_media_path(
        media_root,
        f"tmp/upload-sessions/{_session_id(session_id)}/assembly.part",
    )


def generate_session_staging_path(media_root: Path, session_id: str) -> Path:
    return resolve_media_path(
        media_root,
        f"tmp/upload-staging/{_session_id(session_id)}-{uuid4().hex}.part",
    )


def cleanup_session_temporary_files(media_root: Path, session_id: str) -> None:
    """Remove only session-owned temporary data, never originals or derived files."""
    normalized_session_id = _session_id(session_id)
    canonical_dir = resolve_media_path(media_root, f"tmp/upload-sessions/{normalized_session_id}")
    staging_dir = resolve_media_path(media_root, "tmp/upload-staging")

    if canonical_dir.exists():
        shutil.rmtree(canonical_dir)
    if staging_dir.exists():
        for staging_path in staging_dir.glob(f"{normalized_session_id}-*.part"):
            staging_path.unlink(missing_ok=True)


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


def _normalize_extension(extension: str) -> str:
    if not extension.startswith("."):
        extension = f".{extension}"
    normalized = extension.lower()
    if normalized not in {".mp4", ".jpg"}:
        raise StorageError("unsupported preview extension")
    return normalized


def _session_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise StorageError("invalid upload session id") from exc


def _chunk_index(value: int) -> int:
    if not isinstance(value, int) or value < 0:
        raise StorageError("invalid upload chunk index")
    return value


def _lower_hex_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StorageError("invalid backend generated ID")
    return value
