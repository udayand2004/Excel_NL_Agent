"""Chat endpoint: LLM code generation, sandboxed execution, self-repair, charts."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request

from ..charts import render_chart
from ..llm import LLMError, strip_fences
from ..models import ChatRequest, ChatResponse
from ..profiling import build_summary
from ..sandbox import execute_safely
from ..serialize import to_jsonable
from ..sessions import SessionStore

logger = logging.getLogger(__name__)
router = APIRouter()


def _result_kind(value) -> str:
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        return "table"
    if isinstance(value, pd.Series):
        return "series"
    if value is None:
        return "none"
    return "scalar"


def _preview(value):
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        return to_jsonable(value.head(50).to_dict(orient="records"))
    if isinstance(value, pd.Series):
        return to_jsonable(value.head(50).to_dict())
    return to_jsonable(value)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    settings = request.app.state.settings
    store: SessionStore = request.app.state.sessions
    llm = request.app.state.llm

    session = store.get(payload.session_id)
    if session is None:
        raise HTTPException(404, "Session not found or expired. Please upload a file first.")

    summary = build_summary(session.filename, session.sheets, session.active_sheet)
    question = payload.message.strip()
    session.history.append({"role": "user", "content": question})

    if not llm.configured:
        answer = (
            "No LLM backend is available. Set `OPENAI_API_KEY` (and optionally "
            "`OPENAI_BASE_URL`), or run a local Ollama server — this app auto-detects "
            "Ollama at `127.0.0.1:11434` when no OpenAI key is set. See `backend/.env.example`."
        )
        session.history.append({"role": "assistant", "content": answer})
        return ChatResponse(session_id=session.session_id, answer=answer)

    code = None
    last_error = None
    attempts = 0
    max_attempts = 1 + max(0, settings.max_code_retries)
    started = time.perf_counter()

    try:
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            code = await llm.generate_code(
                question=question,
                summary=summary,
                sheets=session.sheets,
                context=session.context,
                history=session.history,
                error_feedback=last_error,
                previous_code=code,
            )
            code = strip_fences(code)

            execution = execute_safely(
                code, session.df, session.sheets, timeout=settings.code_timeout_seconds
            )
            if execution.ok:
                result = execution.value
                preview = _preview(result)
                kind = _result_kind(result)

                chart_spec = await llm.plan_chart(question, result, summary)
                chart_b64 = render_chart(result, chart_spec, question) if kind != "none" else None

                explanation = await llm.explain(
                    question=question,
                    code=code,
                    result_preview=preview,
                    summary=summary,
                    stdout=execution.stdout,
                )

                execution_ms = int((time.perf_counter() - started) * 1000)
                session.history.append({"role": "assistant", "content": explanation})

                return ChatResponse(
                    session_id=session.session_id,
                    answer=explanation,
                    generated_code=code,
                    result_preview=preview,
                    result_kind=kind,
                    chart=chart_b64,
                    chart_type=chart_spec.type.value if chart_b64 else None,
                    attempts=attempts,
                    execution_ms=execution_ms,
                )

            last_error = execution.error or "Unknown execution error"
            logger.warning("Code attempt %s failed: %s", attempt, last_error)

        # All attempts failed — explain the last error to the user.
        explanation = await llm.friendly_error(question, last_error or "unknown", summary)
        session.history.append({"role": "assistant", "content": explanation})
        return ChatResponse(
            session_id=session.session_id,
            answer=explanation,
            generated_code=code,
            result_preview=None,
            result_kind="none",
            attempts=attempts,
            execution_ms=int((time.perf_counter() - started) * 1000),
        )

    except LLMError as exc:
        logger.exception("LLM failure in /chat")
        answer = f"I couldn't reach the language model: {exc}"
        session.history.append({"role": "assistant", "content": answer})
        return ChatResponse(session_id=session.session_id, answer=answer, generated_code=code)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected failure in /chat")
        answer = await llm.friendly_error(question, f"{type(exc).__name__}: {exc}", summary)
        session.history.append({"role": "assistant", "content": answer})
        return ChatResponse(session_id=session.session_id, answer=answer, generated_code=code)


@router.post("/clarify")
async def clarify(request: Request, payload: dict) -> dict:
    store: SessionStore = request.app.state.sessions
    session_id = payload.get("session_id", "")
    text = (payload.get("clarification") or "").strip()
    if not text:
        raise HTTPException(400, "clarification text is required")

    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found or expired")

    session.context = (session.context + f"\n- {text}").strip()
    session.history.append({"role": "user", "content": f"[Context] {text}"})
    return {"status": "ok", "message": "Context saved", "context": session.context}


@router.delete("/session/{session_id}")
async def reset_session(request: Request, session_id: str) -> dict:
    store: SessionStore = request.app.state.sessions
    removed = store.delete(session_id)
    if not removed:
        raise HTTPException(404, "Session not found")
    return {"status": "ok", "message": "Session deleted"}
