"""Health and meta endpoints."""

from __future__ import annotations

import platform

from fastapi import APIRouter, Request

from .. import __version__
from ..config import get_settings
from ..models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = get_settings()
    llm = request.app.state.llm
    store = request.app.state.sessions
    return HealthResponse(
        status="ok",
        version=__version__,
        model=settings.llm_model,
        provider=settings.llm_provider,
        llm_configured=llm.configured,
        sessions_active=store.active_count(),
        python_version=platform.python_version(),
    )
