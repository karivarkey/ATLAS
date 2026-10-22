# Atlas — agentic coding environment

A production-grade, ChatGPT-quality agentic harness: real tool-using agents,
multi-conversation concurrency, streaming tool visibility, durable SQLite
persistence, and OpenCode Go model discovery.

## Quick start (Windows)

```bat
start.bat
```

Then open **http://127.0.0.1:8765** (backend serves the built frontend).
For frontend dev with hot reload:

```sh
cd frontend && bun install && bun run dev   # http://localhost:5173
```

Backend dev:

```sh
cd backend && pip install -r requirements.txt
python -m uvicorn app.main:app --port 8765 --app-dir backend
```

## Configuration

Copy `.env.example` to real environment variables (never commit secrets):

- `OPENCODE_ZEN_API_KEY` — required for OpenCode Go live models.
- `OLLAMA_BASE` — local models (default `http://localhost:11434`).
- `ATLAS_DEFAULT_MODEL`, `ATLAS_MAX_CONCURRENT`, `ATLAS_MAX_STEPS`, …

If no key is configured, the app shows an onboarding banner and serves
local Ollama models; the catalog endpoint reports `stale: true` with the
exact error instead of pretending to be current.

## What was built

- `backend/app` — FastAPI API (`/api/v1`), SQLite persistence, SSE event
  streams with replay, per-execution state machines, concurrency manager.
- `backend/app/services/model_catalog.py` — OpenCode Go discovery
  (`GET {base}/models`, Bearer auth), normalization, TTL cache, per-model
  protocol routing (responses / chat_completions / messages), Ollama fallback.
- `backend/app/services/agent.py` — tool-call loop (read/write/edit/list/
  glob/grep/shell/git-clone), approvals, `ask_user` questions, pause/resume,
  bounded retries, verification pass.
- `frontend/src` — React + Vite + TypeScript + Zustand: sidebar, model picker,
  streaming composer, tool cards, execution drawer, settings, SSE hook with
  reconnect + dedupe.

## Verification

```sh
cd backend && python -m pytest tests/ -q        # unit + concurrency
cd ../frontend && bunx tsc --noEmit && bunx vite build
```

See `docs/ARCHITECTURE.md` and `docs/FINAL_REPORT.md`.
