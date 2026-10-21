"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .llm import LLMService
from .routers import chat, health, upload
from .sessions import SessionStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 300


async def _session_cleanup_loop(store: SessionStore) -> None:
    while True:
        try:
            removed = store.cleanup_expired()
            if removed:
                logger.info("Expired %s session(s)", removed)
        except Exception:  # noqa: BLE001
            logger.exception("Session cleanup failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_session_cleanup_loop(app.state.sessions))
        logger.info(
            "Excel AI Agent v%s | model=%s | llm_configured=%s",
            __version__,
            settings.llm_model,
            bool(settings.openai_api_key),
        )
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Excel AI Agent",
        version=__version__,
        lifespan=lifespan,
    )

    # Note: allow_credentials=False because we use wildcard-free explicit origins
    # and the frontend sends no cookies.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = settings
    app.state.sessions = SessionStore(
        ttl_seconds=settings.session_ttl_seconds,
        max_sessions=settings.max_sessions,
    )
    app.state.llm = LLMService(settings)

    app.include_router(health.router)
    app.include_router(upload.router)
    app.include_router(chat.router)

    return app


app = create_app()
