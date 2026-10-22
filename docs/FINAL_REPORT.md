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
| Self-hosted search (SearXNG) | local engine @ :8888 | **41 results**, JSON API enabled, no rate limits |
| Deep research | `python tests/e2e_research.py` | **PASS in 3 steps**: `web_research` + fetch, 7 sources cited |
| Voice input (AssemblyAI universal-2, en) | `python tests/e2e_voice.py` | **PASS**: SAPI speech → "Hello? Atlas, this is a voice test." |
| 1.3 default task | `python tests/e2e_task.py` (`opencode-go/muse-spark-1.3-contributor`) | **PASS in 3 steps**: `exit=0`, `HELLO-ATLAS` |
| Memory engine (A–H) | `pytest tests/test_memory.py tests/test_dream.py` | **12 passed**: immediate lexical (xyzz), semantic, isolation, deletion, concurrency, dreaming, recovery, human-edit reconcile, scheduler dedupe |
| Live memory on real data | `python tests/e2e_memory.py` | **PASS**: 174/174 embedded (qwen3-embedding:0.6b), hybrid search hits, real dream → 7 records (Kodomo, stack, GitHub…) |
| Settings SOUL/USER editor | `bun e2e/settings_mem.mjs` | **PASS**: render, edit+save, history, rollback |
| Browser boot (post-change) | `bun e2e/boot.mjs` | BOOT OK |
| Decision dock (permission/question fix) | backend `pytest tests/test_workflows.py -q`, frontend `bunx vitest run` + `tsc` + `vite build`, TestClient HTTP round-trip, live 409/404 probes, `bun e2e/boot.mjs` | **PASS**: 6/6 backend (conv-scoped answer+approve routing, stale-id 409s, options validation), 9/9 vitest, BOOT OK, stale exec-id answer/approve → clear 409 over live HTTP, ask_user(options+kind) → conv answer → completed over full HTTP stack, zero model spend |

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

## Addition: Obsidian-style vault + AI task management (2026-10-23)

- **Backend** (`services/vault.py`, `services/tasks.py`, `services/noteagent.py`,
  `routers/notes.py`, `routers/tasks.py`; additive DB tables in `db.py` SCHEMA,
  additive router registration in `main.py`): real Markdown files under `./vault`
  (`ATLAS_VAULT_DIR`, gitignored) as source of truth with atomic writes,
  stable frontmatter `id` across renames, `[[wikilink]]` backlinks/outlinks +
  broken-link detection + autocomplete, external-edit adoption, version history
  with rollback-as-new, and optimistic-concurrency (`409 note_conflict`).
  Structured tasks with a guarded status machine, stable-id note links,
  idempotent RRULE-lite occurrences, snooze/dismiss/quiet-hours reminders with
  dedupe (no infinite nagging), notifications, and REAL-execution
  task running (`POST /tasks/:id/execute` → `executions.submit`) with
  verify-only-on-criteria completion (`POST /tasks/:id/verify`).
- **Frontend** (new files only): `NotesView.tsx`, `TasksView.tsx`,
  `Notifications.tsx`, `notesApi.ts`, `taskLogic.ts` (+ vitest). `agent.py`
  wiring and `App.tsx` view-switch are documented in `HOOKS.md` for the owners.
- **Verification**: `pytest tests/test_vault.py tests/test_tasks.py` **15 passed**;
  `bunx vitest run` **18 passed** (incl. 9 new task-logic cases);
  `bunx tsc --noEmit` clean; `bunx vite build` clean; live smoke
  (create note + task, link, execute stubbed-AI task, verify gating) passed.
  No model-time tests were needed (all stubs); nothing skipped.

## Integration: vault + tasks wired into Atlas (2026-10-23)

Owner wiring completed on top of the agent's work (no conflicts — disjoint files):

- `agent.py`: task executions auto-load linked-note context (`note_read` versions
  recorded per execution); chat-aware reminders injected at turn start
  (max 2, 6h per-task dedupe, quiet-hours respected); task attempts closed out
  with completion/failure notifications on terminal states (verify still
  required to complete a task — never automatic).
- `workspace.py`: `note_read/note_write/task_get/task_update` dispatched to
  `noteagent.run_notetask_tool` with conversation scope + execution id, so the
  stale-write guard (`409 note_conflict` → re-read → retry) works inside real
  agent runs; reads are free, writes follow the auto-approve policy.
- UI: sidebar Notes/Tasks entries, Notes/Tasks views wired with deep links
  (notifications → task/note, task → originating conversation), bell with
  unread badge in the sidebar footer.
- Scheduler: maintenance loop (occurrences + reminder evaluation) alongside
  the indexer and dream scheduler.
- **Verification**: backend **66 passed**; `bun e2e/notes_tasks.mjs` UI **PASS**
  (views render, note+task created via UI, bell present, no errors); live API
  proof — stale write rejected with `409 note_conflict` naming current version;
  AI task executed to `completed`, `verify` honored criteria; overdue reminder
  correctly suppressed during quiet hours (02:00 local) per policy; reroute:
  models that write to the workspace instead of the vault get explicit
  `note_write` direction in the task prompt.
- Remaining limitation: quiet window is a fixed 22:00–07:00 local (no per-user
  setting yet); the 03:00 dream and maintenance loops run only while the
  server process is up.

## Run it

```bat
start.bat        :: backend on http://127.0.0.1:8765 (serves frontend/dist)
```
Set `OPENCODE_ZEN_API_KEY` before starting for live OpenCode Go models.
