from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.settings import load_settings
from app.db.connection import connect
from app.db.migrations import run_migrations
from app.services.storage import initialize_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    initialize_storage(settings.media_root)
    with connect(settings.database_path, settings.sqlite_busy_timeout_ms) as conn:
        run_migrations(conn)
    yield


app = FastAPI(title="MediaVault Backend", lifespan=lifespan)
app.include_router(health_router)
