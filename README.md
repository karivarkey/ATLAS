# Atlas — agentic coding environment

A production-grade, ChatGPT-quality agentic harness: real tool-using agents,
multi-conversation concurrency, streaming tool visibility, durable SQLite
persistence, and OpenCode Go model discovery.

## Quick start (Windows)

```bat
start.bat
```

Then open **http://127.0.0.1:8765** (backend serves the built frontend).

## Local search engine (SearXNG, Docker)

`web_search` / `web_research` use a self-hosted SearXNG (no rate limits).
Atlas expects it at `ATLAS_SEARXNG_URL` (default `http://localhost:8888`).

```sh
docker run -d --name searxng --restart unless-stopped -p 8888:8080 -v ./searxng:/etc/searxng searxng/searxng:latest
```

with `searxng/settings.yml`:

```yaml
use_default_settings: true
server:
  secret_key: "<random-hex>"   # generate your own, keep it local
  limiter: false
search:
  formats: [html, json]        # json is what Atlas calls
```

If SearXNG is unreachable, search transparently falls back to DuckDuckGo
(responses are labeled `[via duckduckgo fallback]`).

## Voice input

The composer has a mic button: record → audio posts to
`POST /api/v1/stt/transcribe` → AssemblyAI (`universal-2`, English-only)
returns text into the draft. Key lives in gitignored `backend/.env.local`
(`ASSEMBLYAI_API_KEY`), never in the repo or browser.

## Memory engine (Layers A–D)

Every chat is archived immutably (Layer A) and indexed: FTS5 lexical search
works immediately after commit; `qwen3-embedding:0.6b` vectors (1024-dim,
Ollama `/api/embed`) are filled in by a background worker with retries and
backlog diagnostics. Retrieval is hybrid RRF (lexical + vector + recency).
The agent gets SOUL.md + USER.md + top memories with provenance refs.

- Structured records + proposals: `GET /api/v1/memory/records`, approve/reject
  SOUL.md changes in the Memory view (SOUL changes always require approval).
- Dream Engine: `POST /api/v1/memory/dream` (manual), event trigger after
  substantial chats, daily 03:00 scheduler (`ATLAS_DREAM_AT`). Low-risk facts
  auto-apply; contradictions supersede with history preserved.
- Vector store is SQLite (float32 BLOB + numpy cosine) — a deliberate
  equivalent to pgvector for this single-node app: zero extra services,
  transactional with the archive, hermetic tests. Swap `memory.py` if needed.
- Exclude a chat via the drawer toggle or `PATCH /conversations/:id`
  `{"memory_excluded": true}` — excluded chats are never indexed or dreamed.
- UI: sidebar **Memory** (search, knowledge, SOUL.md/USER.md + version
  history + rollback, dream jobs + health), per-run memory refs in the drawer.
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
