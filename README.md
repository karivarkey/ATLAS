# ATLAS — Agentic Coding Environment

![Status](https://img.shields.io/badge/status-work_in_progress-yellow)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![React](https://img.shields.io/badge/frontend-React%20%2B%20Vite%20%2B%20TS-61dafb)
![SQLite](https://img.shields.io/badge/storage-SQLite-003b57)
![License](https://img.shields.io/badge/license-MIT-green)

> ⚠️ **Work in progress.** ATLAS is under active development — APIs, schemas,
> and UI are subject to change. Do not use with untrusted workspace content
> yet; review the [Security](#-security) section before running.

ATLAS is a self-hosted, ChatGPT-grade agentic harness: real tool-using agents,
multi-conversation concurrency, streaming tool visibility, durable SQLite
persistence, and multi-provider model routing (hosted + local Ollama).

![ATLAS concept](https://img.shields.io/badge/UI-chat%20%2B%20executions%20%2B%20memory%20%2B%20vault-lightgrey)

---

## ✨ Features

- **Real tool-using agents** — read / write / edit / glob / grep / shell /
  git operations executed in per-conversation sandboxes, with approvals,
  `ask_user` questions, pause/resume, bounded retries, and a verification pass.
- **Multi-conversation concurrency** — fair scheduling with a global semaphore
  plus per-conversation locks; SSE event streams with replay.
- **Streaming tool visibility** — every tool call renders as a live card with
  real output; chat composer with voice input (AssemblyAI STT).
- **Layered memory engine** — immutable chat archive + hybrid retrieval
  (FTS5 lexical + Ollama embeddings + recency via RRF), SOUL.md/USER.md with
  approvals and version history, plus a background "dream" consolidation engine.
- **Knowledge vault + tasks** — Obsidian-style Markdown vault (files are the
  source of truth, SQLite holds the index) with wikilinks, versions, and
  rollback; AI-executable tasks with a guarded state machine and
  verify-to-complete semantics.
- **Integrations** — Jira (browse, transition, comment), web search via
  self-hosted SearXNG (DuckDuckGo fallback), workspaces, notifications.
- **Multi-provider models** — hosted model discovery with per-model protocol
  routing, transparent fallback to local Ollama models, honest stale-catalog
  reporting instead of fake data.

## 🚀 Quick start

**Prerequisites:** Python 3.12+, [Bun](https://bun.sh) (frontend build),
[Ollama](https://ollama.com) (local models/embeddings, optional),
[Docker](https://www.docker.com) (self-hosted SearXNG, optional).

### Windows (easiest)

```bat
start.bat
```

Then open **http://127.0.0.1:8765** — the backend serves the built frontend.

### Manual

```sh
# Backend
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8765 --app-dir .

# Frontend (dev with hot reload)
cd frontend
bun install
bun run dev          # http://localhost:5173 (proxies /api to the backend)
```

### Local search engine (SearXNG, optional)

`web_search` / `web_research` use a self-hosted SearXNG (no rate limits) at
`ATLAS_SEARXNG_URL` (default `http://localhost:8888`):

```sh
docker run -d --name searxng --restart unless-stopped \
  -p 8888:8080 -v ./searxng:/etc/searxng searxng/searxng:latest
```

If SearXNG is unreachable, search transparently falls back to DuckDuckGo
(responses are labeled `[via duckduckgo fallback]`).

## ⚙️ Configuration

Copy `.env.example` to environment variables — **never commit secrets**
(see [Security](#-security)):

| Variable | Required | Description |
|---|---|---|
| `OPENCODE_ZEN_API_KEY` | For hosted models | API key for hosted model catalog |
| `OLLAMA_BASE` | No | Local models (default `http://localhost:11434`) |
| `ASSEMBLYAI_API_KEY` | For voice input | Server-side only, STT (`universal-2`) |
| `ATLAS_SEARXNG_URL` | No | Self-hosted search (default `http://localhost:8888`) |
| `ATLAS_DEFAULT_MODEL` | No | Default model id |
| `ATLAS_MAX_CONCURRENT` / `ATLAS_MAX_STEPS` | No | Concurrency and step budgets |
| `ATLAS_SERVER_PASSWORD` | No | Password-gate the server |
| `ATLAS_DATA_DIR` / `ATLAS_DB_PATH` | No | Data locations (defaults `./data`, `./data/atlas.db`) |

Without a hosted key, the app shows an onboarding banner and serves local
Ollama models; the catalog endpoint reports `stale: true` with the exact
error instead of pretending to be current.

## 🏗️ Architecture

```
ATLAS/
├── backend/               # FastAPI API (/api/v1), SQLite persistence (WAL)
│   └── app/
│       ├── main.py        # App wiring: CORS, error handling, static frontend
│       ├── routers/       # conversations, executions, memory, notes, tasks,
│       │                  # workspaces, jira, stt, onboarding
│       └── services/      # agent loop, providers/model catalog, executions,
│                          # memory + embeddings + dream engine, vault,
│                          # webtools, workspace sandboxing
├── frontend/              # React + Vite + TypeScript + Zustand
│   └── src/               # sidebar, model picker, streaming composer,
│                          # tool cards, execution drawer, notes/tasks views
├── docs/                  # ARCHITECTURE.md, FINAL_REPORT.md, JIRA.md, ...
└── data/ · vault/ · workspaces/   # local-only runtime data (gitignored)
```

- **Runtime model:** `submit()` → execution row (`queued`) → asyncio task
  under a global semaphore + per-conversation lock → state machine
  (`queued → starting → running → (waiting_input | waiting_approval | paused)
  → verifying → completed`, with `failed / cancelled / timed_out /
  interrupted` terminals).
- **Memory:** every chat archived immutably, indexed with FTS5 immediately
  and `qwen3-embedding` vectors via a background worker; retrieval is hybrid
  RRF (lexical + vector + recency). SQLite vector store (float32 BLOB + numpy
  cosine) — a deliberate pgvector equivalent for a single node: zero extra
  services, transactional with the archive.
- **Single-process Python backend** (FastAPI + stdlib sqlite3) with
  seam-separated execution/persistence interfaces, so a multi-worker rollout
  stays possible without rewrites.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full decision log.

## ✅ Verification

```sh
cd backend && python -m pytest tests/ -q        # unit + concurrency
cd ../frontend && bunx tsc --noEmit && bunx vite build
```

## 🗺️ Roadmap

- [ ] Multi-worker backend rollout (seams already separated)
- [ ] Swap-in pgvector for the memory store at scale
- [ ] Richer approval policies + workspace resource limits
- [ ] Plugin system for custom tools
- [ ] Public demo + screenshots

## 🔒 Security

- No secrets are committed to this repo — API keys live in environment
  variables or gitignored local files only. `.env.example` contains
  placeholders.
- Vault content, databases, logs, and workspace files are gitignored and
  never leave the machine unless you configure it.
- Destructive shell commands always require approval, even with auto-approve on.

## 📄 License

MIT — see [LICENSE](LICENSE).
