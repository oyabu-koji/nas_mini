from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.assets import router as assets_router
from app.api.health import router as health_router
from app.api.upload_sessions import router as upload_sessions_router
from app.core.settings import load_settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.services.processed_result_backfill import backfill_eligible_processed_results
from app.services.storage import initialize_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
    backfill_eligible_processed_results(settings=settings)
    yield


app = FastAPI(title="MediaVault Backend", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error_response(_request: Request, _error: RequestValidationError) -> JSONResponse:
    """Do not reflect upload metadata, paths, or token-adjacent request details."""
    return JSONResponse(
        status_code=422,
        content={"code": "validation_error", "retryable": False},
    )


app.include_router(health_router)
app.include_router(assets_router)
app.include_router(upload_sessions_router)
