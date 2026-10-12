import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.api import candidates, health, jobs, session
from app.api import settings as settings_api
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.errors import install_error_handlers
from app.llm.client import LLMClient
from app.llm.openai_client import OpenAIClient
from app.security import (
    BodyLimitMiddleware,
    DbRateLimiter,
    SecurityHeadersMiddleware,
    require_api_key,
)
from app.services.pipeline import LLMProvider
from app.services.retention import maintenance_loop

STATIC_DIR = Path(__file__).parent / "static"


def _default_llm_provider(settings: Settings) -> LLMProvider:
    cache: dict[str, LLMClient] = {}

    def provider() -> LLMClient:
        if "client" not in cache:
            cache["client"] = OpenAIClient(settings)  # raises LLMUnavailable without a key
        return cache["client"]

    return provider


def create_app(
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    llm_provider: LLMProvider | None = None,
    run_background_jobs: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    factory = session_factory or get_sessionmaker()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if run_background_jobs:
            task = asyncio.create_task(maintenance_loop(factory, settings.retention_days))
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(
        title="Resume Screener & Job Matcher",
        version=__version__,
        description="Decision support for recruiters. A human makes the hiring decision.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.llm_provider = llm_provider or _default_llm_provider(settings)
    app.state.rate_limiter = DbRateLimiter(factory, settings.upload_rate_limit_per_minute)

    install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)

    # Reject oversized request bodies by counting received bytes (chunked uploads included).
    max_body = (settings.max_files_per_upload * settings.max_upload_mb + 1) * 1024 * 1024
    app.add_middleware(BodyLimitMiddleware, max_bytes=max_body)

    app.include_router(health.router, prefix="/api")
    app.include_router(session.public_router, prefix="/api")
    protected = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    protected.include_router(session.protected_router)
    protected.include_router(jobs.router)
    protected.include_router(candidates.router)
    protected.include_router(settings_api.router)
    app.include_router(protected)

    if (STATIC_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
