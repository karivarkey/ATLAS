# Atlas Jira Integration — Setup & Operations

First-class Jira Cloud workspace inside Atlas: connection settings with encrypted
credentials, paginated sync + local search index, full issue CRUD, boards/sprints,
attachments, and 26 typed agent tools sharing one backend service.

## 1. Setup (Jira Cloud)

1. Create an Atlassian API token at
   <https://id.atlassian.com/manage-profile/security/api-tokens>
   (labeled, e.g. "Atlas").
2. Open Atlas → **Jira → Settings** and fill in:
   - **Base URL** — site root only, e.g. `https://your-site.atlassian.net`
     (paths like `/rest/...`, embedded credentials, and non-HTTPS hosts other
     than loopback are rejected).
   - **Email** — your Atlassian account email.
   - **API token** — stored encrypted, never returned to the browser.
   - **Project restrictions** (optional) — e.g. `COS, WEB`. The agent and the UI
     refuse to touch anything outside this scope.
   - **AI write permissions** — master switch for agent mutations (reads always on).
3. Click **Test connection**. It verifies reachability → auth → identity
   (`/myself`) → permissions (`/mypermissions`) → search usability. Only a fully
   passing test flips the status to `connected`.
4. Run **Sync now** (Overview tab). Initial sync paginates every accessible issue;
   progress checkpoints per page, so interruptions resume instead of restarting.

Credential storage: tokens are encrypted with PBKDF2-HMAC-SHA256 + HMAC stream
cipher + Encrypt-then-MAC (`backend/app/services/jira/crypto.py`, stdlib only).
The master key comes from `ATLAS_CREDENTIAL_KEY` or a `0600` file at
`<data>/.jira_master_key` — never the DB or source. API responses, logs, and
agent prompts never contain the token.

## 2. Everyday use

- **Issues tab** — debounced search with exact-key recognition (`COS-5904` opens
  the ticket directly), filters (project/status/assignee/type/sprint/label), and
  a **JQL mode** with server-side validation. Searching `kiosk menu v4
  out-of-stock logic` runs live JQL `text ~` search first, then falls back to the
  local FTS index; provenance is labeled.
- **Issue detail** — status/priority/type/assignee/reporter/labels, ADF-rendered
  description, inline edit, workflow **transitions fetched live** (never hardcoded),
  assign (by display name or accountId, ambiguity asks instead of guessing),
  comments, attachment **preview** (images/PDF/text/code; 25 MB cap; downloads via
  an authenticated backend proxy with safe headers) + download, changelog
  timeline, issue links, hierarchy (parent/epic/sprint).
- **Projects / Boards** — project browser, board list + detail (columns, config),
  sprint list with dates, sprint issues. Drag-free rank changes go through the
  real Agile `issue/rank` API (UI: rank endpoint; agent: `jira_rank_issues`).
- **Saved tab** — Atlas-local saved searches are clearly labeled `local` and are
  NOT Jira filters; Jira-native shared filters are shown read-only.
- **Natural language** — ask in any chat: "find kiosk tickets", "move COS-5904 to
  In Review", "assign the kiosk ticket to Kevin", "create a ticket for the payment
  retry issue". The agent uses real tool calls and renders structured ticket cards
  (key, summary, status, priority, type, project, assignee, updated, excerpt, link).

## 3. Safety model

| Operation | Policy |
|---|---|
| Reads (search, get, boards, attachments metadata/preview) | automatic |
| Ordinary mutations (create, edit, comment, assign, transition, link, upload) | automatic when explicitly requested; follow workspace auto-approve |
| Deletion, comment deletion, unlinking, rank changes, bulk ops | **always require approval** |
| Out-of-scope projects / AI writes disabled | refused with an explanation |

Every mutation writes an audit row (`jira_ops`: actor, op, issue, before/after,
execution + conversation, approval record) visible in Overview → Recent activity.
Idempotency keys prevent duplicate creates/comments on retries; ambiguous
network timeouts report "uncertain — verify before retrying" semantics by
re-fetching the issue before any second attempt.

## 4. Sync & source of truth

Jira is authoritative; SQLite is a searchable cache (FTS5 + normalized columns).
Incremental sync uses `updated >= <cursor>` ordered by `updated ASC`; after every
mutation the issue is re-fetched and the index refreshed. Stale reads are labeled
`stale` with a retry action. Deleted/inaccessible issues are dropped from the
index on 404. Rate limits honor `Retry-After` with bounded exponential backoff;
auth failures pause (never spin); the app stays usable while Jira is down.

## 5. Tests

```sh
cd backend && python -m pytest tests/test_jira.py -q   # 13 tests, mock Jira transport
cd backend && python -m pytest tests/ -q               # full suite (no regressions)
cd ../frontend && bunx tsc --noEmit && bunx vite build # typecheck + build
cd ../frontend && bunx vitest run                      # 19 tests
```

Mock vs real: all backend Jira tests run against a deterministic
`httpx.MockTransport` emulating Cloud REST v3 + Agile. They are labeled as such
by construction (no network).

## 6. Real-instance verification (requires credentials)

Not yet run — no Jira credentials exist in this environment. To verify:

1. Set the connection in Jira → Settings and **Test connection** (expect green).
2. **Sync now** → issue count matches the JQL `ORDER BY updated ASC` total.
3. Search `COS-5904` (or any real key) → exact ticket opens.
4. Keyword search for a known summary phrase → ticket appears with live provenance.
5. Transition a test ticket → status changes and is re-verified in the response.
6. Create → comment → assign → link → delete a scratch ticket; confirm each step
   in both Atlas and jira.atlassian.net, plus audit rows in Overview.
7. Attach an image + a PDF → preview renders; a `.exe` → download-only.
8. Restrict projects to one key → searches/creates outside it are refused.
9. Revoke the token → sync pauses with `auth` error, cached reads labeled stale.

## 7. Limitations

- Jira Cloud REST v3 only. Data Center/Server needs a separate adapter (client
  is structured for one: `normalize_base_url` + per-resource methods).
- No sprint mutation endpoints (read-only sprints) — Agile sprint create/update
  requires elevated scopes most tokens lack; documented, not stubbed.
- Board rank order display uses Jira's ordering; Atlas does not fake drag-and-drop
  persistence — rank calls hit the real API and report the result.
- Vote/watchers: watchers supported via tool; votes UI omitted (low value).
- Attachment text extraction for PDFs uses a lightweight literal-string parser
  (no new deps); scanned-image PDFs show metadata + download.
- Single-user scope (`user:default`); multi-user isolation point is the
  `scope_id` column on `jira_connections`.
