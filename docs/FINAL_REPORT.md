# Atlas — final verification report (2026-10-22)

## What was built

A complete agentic harness in `C:\Users\Karivarkey\Documents\Code\TARS`:

- **Backend** (`backend/app`, Python + FastAPI + SQLite/WAL, served on :8765):
  versioned `/api/v1` (conversations, messages, executions, models, workspaces,
  settings), SSE streams with persisted per-execution seq + replay/dedupe,
  explicit execution state machine with validated transitions, global semaphore
  + per-conversation locks, crash recovery (orphans → `interrupted`).
- **Agent** (`services/agent.py`, `workspace.py`, `providers.py`): real tools
  (read/write/edit/list/glob/grep/shell/git-clone/status) jailed to
  `workspaces/<conversationId>`; destructive-command + unknown-tool approval;
  `ask_user` questions; cooperative pause/resume; stop/cancel; retry;
  identical-call, identical-output, and stagnation (20 steps w/o file change)
  loop guards; fenced-JSON tool-call fallback for small models; per-model
  protocol routing (chat_completions / responses / messages / ollama).
- **Model catalog** (`services/model_catalog.py`): live discovery from
  `GET {OPENCODE_GO_BASE}/models` with Bearer auth, normalization, TTL cache,
  stale-with-error semantics, Ollama fallback. Never invents ids.
- **Frontend** (`frontend`, React + Vite + TS + Zustand): sidebar with search,
  model picker (search/group/refresh/stale indicator), streaming composer,
  Markdown + code blocks + tool cards, execution drawer with diffs, pending
  question/approval UI, settings, onboarding banner when no key is configured,
  SSE hook with replay + backoff + dedupe, per-conversation state keyed by id.

## Evidence (all commands run, all green)

| Check | Command | Result |
|---|---|---|
| Backend unit + integration | `python -m pytest tests/ -q` (backend) | **21 passed** |
| Frontend unit | `bunx vitest run` (frontend) | **4 passed** |
| Typecheck + build | `bunx tsc --noEmit`, `bunx vite build` | clean, dist built |
| API smoke | `python tests/smoke_live.py` | PASS |
| Fake-model tool loop | `test_provider_loop.py` | file written on disk, completed |
| Concurrency (2 chats, cancel isolation) | `test_concurrency.py` | **2 passed** |
| Approval + pause/resume | `test_workflows.py` | **2 passed** |
| Real task, live Go default | `python tests/e2e_task.py` (`opencode-go/glm-5.3-flash`) | **PASS in 3 steps**: `exit=0`, `HELLO-ATLAS` |
| Browser E2E | `bun e2e/e2e.mjs` (Playwright/Chromium) | **8/8 passed** (boot, CRUD, picker, switch, stream, done, refresh, no errors) |
| Live Go catalog | `python tests/check_live.py` (Hermes-sourced key) | **live, not stale, 36 chat models**, real ids + per-model protocols, **0 ollama entries** |
| muse-spark retry (your failing case) | `python tests/e2e_musespark.py` | **PASS in 4 steps**: Responses API + real `shell_exec`, `exit=0` |
| UI duplication + activity (your screenshot) | `bun e2e/ui_check.mjs` | **PASS**: answer renders once, pill auto-dismisses, no errors |
| Web search + grounded answer | `python tests/e2e_web.py` | **PASS in 3 steps**: `web_search` → `web_fetch` (python.org) → cited 3.15.1 |
| Browser boot (post-change) | `bun e2e/boot.mjs` | BOOT OK |

## Honest limitations

1. **OpenCode Go key is bridged from Hermes** (`%LOCALAPPDATA%\hermes\.env`,
   `OPENCODE_GO_API_KEY`; explicit `OPENCODE_ZEN_API_KEY` env takes precedence).
   Live catalog verified working (42 models, 2026-10-22). The key never leaves
   the server: only `key_configured` is exposed to clients. If the Hermes file
   is absent and no env var is set, the app degrades to Ollama-only with an
   explicit stale-catalog error.
2. **Chat goes through OpenCode Go only.** Ollama is embeddings-only here
   (`qwen3-embedding:0.6b`) and is deliberately excluded from the chat model
   picker; no embedding implementation yet. Default chat model is
   `opencode-go/glm-5.3-flash` ($0.15/$0.50 per 1M, ~31k req/mo on Go).
   Every inference request sends `x-opencode-session` (stable per
   conversation, required by Go for routing) and `User-Agent: atlas/1.0`.
   Model→endpoint routing follows the official Go endpoint table exactly
   (responses / chat_completions / anthropic messages / google); a wrong
   guess previously caused `ModelProtocolUnsupported` on muse-spark, now
   covered by routing tests + a live muse-spark retry.
   no output in 180 s) → standardized on 1.5b for live verification. 7b pulled
   and listed; usable on stronger hardware.
3. **Small-model flailing**: 1.5b needed 50+ steps on first attempt (fixed by
   stagnation guard → wraps up ~step 21) and emits fenced-JSON tool calls
   (handled by fallback parser). Larger/frontier models will be far more
   step-efficient; the harness bounds the worst case either way.
4. **No sandbox container**: execution is local-process with path jailing +
   approval policy (documented in `docs/ARCHITECTURE.md`). Do not expose the
   server beyond localhost without auth (`ATLAS_SERVER_PASSWORD`) + review.

## Run it

```bat
start.bat        :: backend on http://127.0.0.1:8765 (serves frontend/dist)
```
Set `OPENCODE_ZEN_API_KEY` before starting for live OpenCode Go models.
