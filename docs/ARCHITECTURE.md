# Atlas architecture note

## Decision log (Phase 1 discovery, 2026-10-22)

Environment found: Windows 11, Python 3.15 (+FastAPI/uvicorn/httpx preinstalled),
Bun 1.4.3, Docker, Ollama (embedding models only), no Node, no Git, no OpenCode
binary, no `OPENCODE_ZEN_API_KEY`. Network available. Port 8000 blocked → serve on 8765.

Consequences:

- **Single-process Python backend** (FastAPI + stdlib sqlite3, WAL) instead of
  Node/Fastify + Postgres/Redis. The execution and persistence interfaces are
  seam-separated (`services/executions.py`, `db.py`) so a multi-worker rollout
  stays possible without rewrites. No Postgres/Redis running locally, and the
  spec explicitly allows deferring that infrastructure.
- **Frontend: React + Vite + TS built with Bun** (no Next.js/Node). Bun
  installs and builds the app; the backend serves `frontend/dist` in production
  and Vite proxies `/api` in dev. Styling is hand-written CSS (quiet neutral
  theme) instead of Tailwind to keep the build deterministic; shadcn/Radix
  primitives were reviewed and deliberately skipped — the needed components
  (picker, drawer, cards, modal) are small and fully owned.
- **Agent runtime: direct OpenCode Go HTTPS adapters** (`services/providers.py`)
  with per-model protocol routing, plus an Ollama adapter. No OpenCode server
  binary exists here, so driving `opencode serve` was not an option; the tool
  loop (`services/agent.py` + `services/workspace.py`) executes real filesystem
  and shell operations in per-conversation sandboxes. This is a documented,
  honest substitution, not a second-rate mock: every tool card shows real output.
- **Git** was installed via winget during setup; `git_clone`/`git_status`/diff
  use the real binary when present and report honestly when absent.
- **Icons**: Lucide (`lucide-react`). **State**: Zustand with per-conversation
  keying — no global `currentExecution` authority; SSE routing is by execution id.
- **Markdown**: owned streaming-safe renderer (no raw HTML injection) instead of
  react-markdown, to guarantee partial-fence safety during deltas.

## Runtime model

`submit()` → execution row (`queued`) → asyncio task under a global semaphore
(fair scheduling) + per-conversation lock (workspace serialization) → state
machine `queued→starting→running⇄(waiting_input|waiting_approval|paused)→
verifying→completed`, with `failed/cancelled/timed_out/interrupted` terminals.
Pause is cooperative at checkpoint boundaries. Every state change and tool event
is persisted with a monotonic per-execution seq before broadcast; SSE
replays from `?from_seq=` and dedupes by seq. Restart recovery marks orphaned
live executions `interrupted` with a retry path (never silently resumed).

## Security posture

Secrets stay server-side; the frontend only learns `key_configured`. Path
resolution is jailed to `workspaces/<conversationId>`; destructive shell
patterns and unknown tools require approval; `ask_user` pauses for genuine
blockers. Retrieved repo/web content is treated as untrusted data. No sandbox
container is claimed — execution is local-process, documented in the final report.
