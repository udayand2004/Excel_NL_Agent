"""File upload and session creation."""

from __future__ import annotations

import logging
import os
import tempfile

import pandas as pd
from fastapi import APIRouter, HTTPException, Request, UploadFile

from ..models import SheetRequest, SheetResponse, UploadResponse
from ..profiling import build_summary, load_workbook, suggest_questions
from ..sessions import SessionStore

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_excel(request: Request, file: UploadFile) -> UploadResponse:
    settings = request.app.state.settings
    store: SessionStore = request.app.state.sessions
    llm = request.app.state.llm

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only .xlsx, .xls, or .csv files are supported")

    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) == 0:
        raise HTTPException(400, "Uploaded file is empty")
    if len(content) > max_bytes:
        raise HTTPException(
            413, f"File exceeds the {settings.max_upload_mb} MB upload limit"
        )

    suffix = ext if ext != ".xls" else ".xls"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            sheets = load_workbook(tmp_path, filename)
        except Exception as exc:
            logger.exception("Failed to parse uploaded file")
            raise HTTPException(400, f"Could not parse file: {exc}") from exc

        summary = build_summary(filename, sheets, next(iter(sheets)))
        session = store.create(filename, sheets, summary.model_dump())

        clarification = await llm.clarification_questions(summary, sheets)

        return UploadResponse(
            session_id=session.session_id,
            data_summary=summary,
            suggested_questions=suggest_questions(summary),
            clarification_questions=clarification,
            needs_clarification=bool(clarification),
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/sheet", response_model=SheetResponse)
async def switch_sheet(request: Request, payload: SheetRequest) -> SheetResponse:
    store: SessionStore = request.app.state.sessions
    session = store.get(payload.session_id)
    if session is None:
        raise HTTPException(404, "Session not found or expired. Please upload the file again.")
    if payload.sheet not in session.sheets:
        raise HTTPException(400, f"Sheet '{payload.sheet}' not found. Available: {list(session.sheets)}")

    session.active_sheet = payload.sheet
    df = session.df
    session.profile = build_summary(session.filename, session.sheets, payload.sheet).model_dump()
    return SheetResponse(
        status="ok",
        active_sheet=payload.sheet,
        sheets=list(session.sheets.keys()),
        rows=int(len(df)),
        columns=int(len(df.columns)),
    )
