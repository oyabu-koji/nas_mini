from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import require_bearer_token
from app.core.settings import load_settings
from app.schemas.upload_sessions import (
    UploadSessionCreateRequest,
    UploadSessionFinalizeResponse,
    UploadSessionResponse,
    parse_content_range,
    validate_chunk_sha256,
)
from app.services.upload_sessions import (
    UploadSessionServiceError,
    build_finalize_response,
    cancel_upload_session,
    create_upload_session,
    finalize_upload_session,
    get_upload_session,
    upload_session_chunk,
)


router = APIRouter(
    prefix="/upload-sessions",
    tags=["upload-sessions"],
    dependencies=[Depends(require_bearer_token)],
)


@router.post("", response_model=UploadSessionResponse)
def create_session(request: UploadSessionCreateRequest):
    try:
        settings = load_settings()
        session, created = create_upload_session(settings=settings, request=request)
        response = get_upload_session(settings=settings, session_id=session["id"])
        return JSONResponse(
            status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            content=response,
        )
    except UploadSessionServiceError as exc:
        return _error_response(exc)


@router.post("/{session_id}/finalize", response_model=UploadSessionFinalizeResponse)
def finalize_session(session_id: str):
    try:
        settings = load_settings()
        session, job_id = finalize_upload_session(settings=settings, session_id=session_id)
        return JSONResponse(
            status_code=status.HTTP_200_OK if session["status"] == "completed" else status.HTTP_202_ACCEPTED,
            content=build_finalize_response(settings=settings, session=session, job_id=job_id),
        )
    except UploadSessionServiceError as exc:
        return _error_response(exc)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_session(session_id: str):
    try:
        cancel_upload_session(settings=load_settings(), session_id=session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except UploadSessionServiceError as exc:
        return _error_response(exc)


@router.get("/{session_id}", response_model=UploadSessionResponse)
def get_session(session_id: str):
    try:
        return get_upload_session(settings=load_settings(), session_id=session_id)
    except UploadSessionServiceError as exc:
        return _error_response(exc)


@router.put("/{session_id}/chunks/{chunk_index}")
async def put_chunk(
    session_id: str,
    chunk_index: int,
    request: Request,
    content_range: Annotated[str | None, Header(alias="Content-Range")] = None,
    chunk_sha256: Annotated[str | None, Header(alias="X-Chunk-SHA256")] = None,
):
    try:
        parsed_range = parse_content_range(content_range)
        expected_sha256 = validate_chunk_sha256(chunk_sha256)
        _session, chunk, inserted = await upload_session_chunk(
            settings=load_settings(),
            session_id=session_id,
            chunk_index=chunk_index,
            content_range=parsed_range,
            expected_chunk_sha256=expected_sha256,
            body_stream=request.stream(),
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED if inserted else status.HTTP_200_OK,
            content={"chunk": chunk, "idempotent": not inserted},
        )
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "chunk_request_invalid", "retryable": False},
        )
    except UploadSessionServiceError as exc:
        return _error_response(exc)


def _error_response(error: UploadSessionServiceError) -> JSONResponse:
    status_code = {
        "session_not_found": status.HTTP_404_NOT_FOUND,
        "session_expired": status.HTTP_410_GONE,
        "active_session_limit": status.HTTP_429_TOO_MANY_REQUESTS,
        "session_size_limit": status.HTTP_413_CONTENT_TOO_LARGE,
    }.get(error.code, status.HTTP_409_CONFLICT)
    headers = (
        {"Retry-After": str(error.retry_after_seconds)}
        if error.retry_after_seconds is not None
        else None
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "code": error.code,
            "retryable": error.retryable,
            "retry_after_seconds": error.retry_after_seconds,
        },
        headers=headers,
    )
