# Excel AI Agent

Full-stack AI agent that analyzes Excel/CSV files and answers natural-language questions with auto-generated charts. **Groq (`openai/gpt-oss-120b`) is the primary LLM; local Ollama is the automatic fallback.**

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

The backend serves `frontend/` automatically, so opening
**http://localhost:8000** is enough. For standalone static development:

```bash
cd frontend
python3 -m http.server 3000
# open http://localhost:3000
```

## LLM Backends

Groq is used first. If it fails, the backend automatically retries the same
request against local Ollama.

| Backend | Setup |
|---------|-------|
| **Groq (primary)** | Set `GROQ_API_KEY` in `backend/.env`. Defaults: `GROQ_MODEL=openai/gpt-oss-120b`, `GROQ_BASE_URL=https://api.groq.com/openai/v1`. |
| **Ollama (fallback)** | Run `ollama serve`; auto-detected at `127.0.0.1:11434`. Set `OLLAMA_MODEL` (default `qwen2.5:7b`) and `OLLAMA_TIMEOUT_SECONDS` (default `25`). |
| **OpenAI (optional)** | Set `OPENAI_API_KEY` and optionally `LLM_MODEL=gpt-4o-mini`. |
| **Any compatible proxy** | Set `OPENAI_BASE_URL=http://host:port/v1`, `OPENAI_API_KEY=anything`, `LLM_MODEL=your-model`. |

`GET /health` reports the active provider/model and the configured fallback.
See `backend/.env.example` for all options.

For Groq's 8,000 input-TPM free tier, prompts are compacted automatically:
column selection is question-aware, sample rows are capped, history is summarized,
and each workflow stage has its own token budget. The per-minute limiter waits
briefly, then returns a retry message instead of sending a request that would
exceed the limit. Ollama is used only for backend failures, not rate limits.

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

## Deployment

- **Backend**: AWS EC2 (`t3.medium`, Ubuntu 24.04) running FastAPI with Groq
  (`openai/gpt-oss-120b`) as primary and Ollama (`qwen2.5:3b` by default) as
  fallback, with Caddy providing HTTPS. See
  [`deploy/aws/README.md`](deploy/aws/README.md).
- **Frontend**: Vercel static deployment. `npm run build` copies `frontend/`
  into `dist/`; set `frontend/config.js` to the AWS backend URL before deploying.
- **Code mirror**: the repository is pushed to GitHub and, when available, to
  AWS CodeCommit.

```bash
AWS_PROFILE=aws-agent AWS_REGION=us-west-2 ./deploy/aws/provision.sh
npm run build
```

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
  config.js            # backend URL injected at deploy time
scripts/
  build-frontend.mjs   # copies frontend/ to dist/ for Vercel
deploy/aws/
  provision.sh         # create EC2 + Ollama + Caddy + IAM/S3
  update.sh            # redeploy backend code
  teardown.sh          # destroy all created resources
```

## License

MIT
