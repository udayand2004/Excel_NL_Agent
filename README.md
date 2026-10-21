# Excel AI Agent

Full-stack AI agent that analyzes Excel/CSV files and answers natural-language questions with auto-generated charts — **no OpenAI key required** (uses local Ollama by default).

## Quick Start

### 1. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional — Ollama is auto-detected
python main.py         # http://localhost:8000
```

### 2. Frontend

```bash
cd frontend
python3 -m http.server 3000
# open http://localhost:3000
```

## LLM Backends

The server speaks the OpenAI chat-completions protocol, so it works with:

| Backend | Setup |
|---------|-------|
| **Ollama (free, default)** | Run `ollama serve`; auto-detected at `127.0.0.1:11434`. Set `OLLAMA_MODEL` in `.env` (default `qwen2.5:7b`). |
| **OpenAI** | Set `OPENAI_API_KEY` and optionally `LLM_MODEL=gpt-4o-mini`. |
| **Any compatible proxy** | Set `OPENAI_BASE_URL=http://host:port/v1`, `OPENAI_API_KEY=anything`, `LLM_MODEL=your-model`. |

See `backend/.env.example` for all options.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Version, provider, model, active sessions |
| `POST` | `/upload` | Upload `.xlsx`/`.xls`/`.csv` → profile + clarifying questions |
| `POST` | `/chat` | Ask a question → answer + code + chart |
| `POST` | `/sheet` | Switch active sheet in a multi-sheet workbook |
| `POST` | `/clarify` | Attach business context to a session |
| `DELETE` | `/session/{id}` | Delete a session |

## Architecture

```
Upload → FastAPI → Pandas profile → LLM (code + chart plan)
       → AST sandbox exec (process-isolated, timeout)
       → Matplotlib dark-theme chart → Explanation → UI
```

- **Sandbox**: LLM-generated code is AST-validated (no `os`/`eval`/`open`/dunders), executed in a separate process with a hard timeout, and auto-repaired on failure.
- **Sessions**: in-memory, TTL-expired, LRU-bounded.
- **Charts**: bar, line, scatter, pie, heatmap, box, area, histogram — chosen by the LLM from the result shape.

## Project Structure

```
backend/
  main.py              # entry point
  app/
    main.py            # FastAPI app factory
    config.py          # env settings + Ollama auto-detect
    llm.py             # OpenAI-compatible client (code gen, repair, explain)
    sandbox.py         # AST-validated process sandbox
    charts.py          # dark-theme matplotlib rendering
    profiling.py       # Excel/CSV load + column profiling
    sessions.py        # TTL + LRU session store
    serialize.py       # numpy/pandas → JSON
    models.py          # pydantic schemas
    routers/           # upload, chat, health
frontend/
  index.html           # single-file UI (XSS-safe, no build step)
```

## License

MIT
