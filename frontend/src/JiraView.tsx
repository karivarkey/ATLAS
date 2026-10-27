// Jira workspace: connection settings, search, issue detail, boards, sync.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jiraApi, IssueDetail, JiraConnection, TicketCard } from './jiraApi'
import { Markdown } from './markdown'

type Tab = 'overview' | 'issues' | 'projects' | 'boards' | 'saved' | 'settings'

export function JiraView({ initialKey }: { initialKey?: string | null }) {
  const [tab, setTab] = useState<Tab>(initialKey ? 'issues' : 'overview')
  const [conn, setConn] = useState<JiraConnection | null>(null)
  const [connLoading, setConnLoading] = useState(true)
  const [selKey, setSelKey] = useState<string | null>(initialKey || null)

  const refresh = useCallback(async () => {
    try {
      const { connection } = await jiraApi.connection()
      setConn(connection)
    } catch {
      setConn(null)
    } finally {
      setConnLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (initialKey) { setSelKey(initialKey); setTab('issues') }
  }, [initialKey])

  if (connLoading) return <div className="muted pad">Loading Jira…</div>

  return (
    <div className="jira">
      <div className="jira-head">
        <h2>Jira</h2>
        <ConnPill conn={conn} />
      </div>
      <div className="mem-tabs jira-tabs">
        {([['overview', 'Overview'], ['issues', 'Issues'], ['projects', 'Projects'],
           ['boards', 'Boards'], ['saved', 'Saved'], ['settings', 'Settings']] as [Tab, string][]).map(
          ([t, label]) => (
            <button key={t} className={`mem-tab ${tab === t ? 'sel' : ''}`} onClick={() => setTab(t)}>
              {label}
            </button>
          ))}
      </div>
      {tab === 'overview' && <Overview conn={conn} refresh={refresh} onOpenIssue={(k) => { setSelKey(k); setTab('issues') }} />}
      {tab === 'issues' && (
        <IssuesTab
          conn={conn} selKey={selKey} onSelect={setSelKey}
          onNeedsConnection={() => setTab('settings')}
        />
      )}
      {tab === 'projects' && <ProjectsTab onOpenProjectIssues={(k) => { setSelKey(null); setTab('issues') }} />}
      {tab === 'boards' && <BoardsTab onOpenIssue={(k) => { setSelKey(k); setTab('issues') }} />}
      {tab === 'saved' && <SavedTab onRun={(jql) => setTab('issues')} />}
      {tab === 'settings' && <SettingsTab conn={conn} refresh={refresh} />}
    </div>
  )
}

function ConnPill({ conn }: { conn: JiraConnection | null }) {
  if (!conn) return <span className="status-pill">not connected</span>
  const ok = conn.status === 'connected'
  return (
    <span className="status-pill" title={`${conn.base_url} · ${conn.account_name}`}>
      <span className={`dot ${ok ? 'live' : 'idle'}`} /> {ok ? conn.account_name || 'connected' : conn.status}
      {conn.indexed_issues > 0 && <span className="muted"> · {conn.indexed_issues} indexed</span>}
    </span>
  )
}

/* ---------------- Overview ---------------- */

function Overview({ conn, refresh, onOpenIssue }: {
  conn: JiraConnection | null; refresh: () => void; onOpenIssue: (k: string) => void
}) {
  const [ops, setOps] = useState<any[]>([])
  const [syncing, setSyncing] = useState(false)
  const [msg, setMsg] = useState('')
  useEffect(() => {
    if (conn) jiraApi.ops().then((o) => setOps(o.ops)).catch(() => {})
  }, [conn])
  if (!conn) {
    return (
      <div className="empty-card">
        <h3>Connect Jira</h3>
        <p className="muted">Add your Jira Cloud credentials in Settings to search and manage tickets.</p>
      </div>
    )
  }
  const sync = async (mode: 'initial' | 'incremental') => {
    setSyncing(true); setMsg('')
    try {
      const r = await jiraApi.sync(mode)
      setMsg(`Sync ${r.status}: ${r.processed} issues (${r.pages} pages).`)
      refresh()
    } catch (e: any) { setMsg(e.message) } finally { setSyncing(false) }
  }
  return (
    <div className="jira-grid">
      <div className="jira-card">
        <h4>Connection</h4>
        <div className="kv"><span>Site</span><b>{conn.base_url}</b></div>
        <div className="kv"><span>Account</span><b>{conn.account_name || conn.email}</b></div>
        <div className="kv"><span>Status</span><b>{conn.status}</b></div>
        <div className="kv"><span>Indexed issues</span><b>{conn.indexed_issues}</b></div>
        <div className="kv"><span>Last sync</span><b>{conn.last_sync_at ? new Date(conn.last_sync_at).toLocaleString() : 'never'}</b></div>
        {conn.last_sync_error && <div className="errorbox sm">{conn.last_sync_error}</div>}
        {conn.sync_phase === 'paused' && (
          <div className="notice">Sync paused — resume to continue from the checkpoint. {conn.sync_detail}</div>
        )}
        <div className="row">
          <button className="btn sm" disabled={syncing} onClick={() => sync('incremental')}>
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
          <button className="btn sm ghost" disabled={syncing} onClick={() => sync('initial')}>
            Full re-sync
          </button>
        </div>
        {msg && <div className="muted small">{msg}</div>}
        <div className="muted small">Jira is the source of truth — the local copy is a searchable cache.</div>
      </div>
      <div className="jira-card">
        <h4>Recent Jira activity</h4>
        {ops.length === 0 && <div className="muted small">No operations yet.</div>}
        {ops.slice(0, 12).map((o) => (
          <div key={o.id} className="jira-op">
            <span className={`chip ${o.status === 'ok' ? '' : 'bad'}`}>{o.status}</span>
            <span className="mono">{o.op}</span>
            {o.issue_key && (
              <button className="mini-btn" onClick={() => onOpenIssue(o.issue_key)}>{o.issue_key}</button>
            )}
            <span className="muted small">{new Date(o.created_at).toLocaleString()} · {o.actor}</span>
            {o.error && <div className="errorbox sm">{o.error}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------- Issues ---------------- */

function statusClass(s: string) {
  const t = s.toLowerCase()
  if (/done|closed|resolved|complete/.test(t)) return 'st-done'
  if (/progress|review|test|verif/.test(t)) return 'st-active'
  if (/to do|open|backlog|new/.test(t)) return 'st-todo'
  return 'st-other'
}

export function TicketRow({ t, onOpen }: { t: TicketCard; onOpen: (k: string) => void }) {
  return (
    <div className="ticket" onClick={() => onOpen(t.key)}>
      <div className="ticket-top">
        <button className="mono linklike" onClick={(e) => { e.stopPropagation(); onOpen(t.key) }}>{t.key}</button>
        <span className={`stchip ${statusClass(t.status)}`}>{t.status}</span>
        {t.priority && <span className="muted small">{t.priority}</span>}
        <span className="muted small">{t.issue_type}</span>
      </div>
      <div className="ticket-summary">{t.summary}</div>
      <div className="ticket-meta muted small">
        {t.project} {t.assignee ? `· @${t.assignee}` : '· unassigned'}
        {t.sprint ? ` · ${t.sprint}` : ''} {t.updated ? ` · ${t.updated.slice(0, 10)}` : ''}
      </div>
      {t.excerpt && <div className="ticket-excerpt muted small">{t.excerpt.slice(0, 180)}</div>}
    </div>
  )
}

function IssuesTab({ conn, selKey, onSelect, onNeedsConnection }: {
  conn: JiraConnection | null; selKey: string | null
  onSelect: (k: string | null) => void; onNeedsConnection: () => void
}) {
  const [q, setQ] = useState('')
  const [deb, setDeb] = useState('')
  const [jqlMode, setJqlMode] = useState(false)
  const [jql, setJql] = useState('project = COS ORDER BY updated DESC')
  const [jqlError, setJqlError] = useState('')
  const [filters, setFilters] = useState({ project: '', status: '', assignee: '', issue_type: '', label: '' })
  const [issues, setIssues] = useState<TicketCard[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const timer = useRef<any>(null)

  useEffect(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setDeb(q), 300)
    return () => clearTimeout(timer.current)
  }, [q])

  const run = useCallback(async () => {
    if (!conn) return
    setLoading(true); setJqlError('')
    try {
      if (jqlMode) {
        const v = await jiraApi.jqlValidate(jql).catch((e) => ({ valid: false, error: String(e) }))
        if (v && v.valid === false) { setJqlError(v.error || 'Invalid JQL'); setLoading(false); return }
        const r = await jiraApi.jqlSearch(jql, 25)
        setIssues(r.issues); setTotal(r.total || r.issues.length)
      } else {
        const r = await jiraApi.searchLocal({ q: deb, ...filters, limit: 25, offset: 0 })
        setIssues(r.issues); setTotal(r.total)
      }
    } catch (e: any) {
      setJqlError(e.message)
    } finally { setLoading(false) }
  }, [conn, deb, filters, jqlMode, jql])

  useEffect(() => { run() }, [run])

  if (!conn) {
    return (
      <div className="empty-card">
        <h3>Jira is not connected</h3>
        <button className="btn primary" onClick={onNeedsConnection}>Open Settings</button>
      </div>
    )
  }

  return (
    <div className={`issues-wrap ${selKey ? 'with-detail' : ''}`}>
      <div className="issues-list">
        <div className="issues-bar">
          {!jqlMode ? (
            <input
              placeholder="Search tickets — try COS-5904 or 'kiosk menu out-of-stock'…"
              value={q} onChange={(e) => setQ(e.target.value)}
            />
          ) : (
            <input value={jql} onChange={(e) => setJql(e.target.value)} placeholder="JQL…" spellCheck={false} />
          )}
          <button className="btn sm" onClick={() => setJqlMode(!jqlMode)}>
            {jqlMode ? 'Filters' : 'JQL'}
          </button>
          <button className="btn sm primary" onClick={() => setShowCreate(true)}>New issue</button>
        </div>
        {!jqlMode && (
          <div className="filter-row">
            {(['project', 'status', 'assignee', 'issue_type', 'label'] as const).map((f) => (
              <input
                key={f} placeholder={f.replace('_', ' ')} value={filters[f]}
                onChange={(e) => setFilters({ ...filters, [f]: e.target.value })} />
            ))}
            <button className="mini-btn" onClick={() => setFilters({ project: '', status: '', assignee: '', issue_type: '', label: '' })}>
              Clear
            </button>
          </div>
        )}
        {jqlError && <div className="errorbox sm">{jqlError}</div>}
        <div className="muted small">{loading ? 'Searching…' : `${total} result${total === 1 ? '' : 's'}`}</div>
        {issues.map((t) => <TicketRow key={t.key} t={t} onOpen={onSelect} />)}
        {issues.length === 0 && !loading && (
          <div className="muted pad">No issues found. Broaden the search or try JQL mode.</div>
        )}
      </div>
      {selKey && <IssueDetailPanel issueKey={selKey} onClose={() => onSelect(null)} onChanged={run} />}
      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreated={(k) => { setShowCreate(false); onSelect(k); run() }} />}
    </div>
  )
}

/* ---------------- Issue detail ---------------- */

function IssueDetailPanel({ issueKey, onClose, onChanged }: {
  issueKey: string; onClose: () => void; onChanged?: () => void
}) {
  const [issue, setIssue] = useState<IssueDetail | null>(null)
  const [stale, setStale] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [transitions, setTransitions] = useState<{ id: string; name: string }[]>([])
  const [comment, setComment] = useState('')
  const [assignTo, setAssignTo] = useState('')
  const [editOpen, setEditOpen] = useState(false)
  const [preview, setPreview] = useState<any>(null)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const r = await jiraApi.getIssue(issueKey, true)
      setIssue(r.issue); setStale(!!r.stale)
      const t = await jiraApi.transitions(issueKey).catch(() => ({ transitions: [] }))
      setTransitions(t.transitions || [])
    } catch (e: any) {
      setErr(e.message)
    } finally { setLoading(false) }
  }, [issueKey])

  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<any>, msg?: string) => {
    try { await fn(); await load(); onChanged?.() }
    catch (e: any) { alert(msg || e.message) }
  }

  if (loading) return <div className="issue-detail muted pad">Loading {issueKey}…</div>
  if (err || !issue) return (
    <div className="issue-detail">
      <div className="errorbox">{err || 'Not found'}</div>
      <button className="btn sm" onClick={onClose}>Close</button>
    </div>
  )

  return (
    <div className="issue-detail">
      <div className="issue-head">
        <button className="mono linklike big" onClick={() => navigator.clipboard.writeText(issue.key).catch(() => {})} title="Click to copy key">
          {issue.key}
        </button>
        <a href={issue.url} target="_blank" rel="noopener noreferrer" className="mini-btn">Open in Jira ↗</a>
        <button className="icon-btn" onClick={onClose} title="Close">✕</button>
      </div>
      {stale && <div className="notice">Showing cached data — Jira is unreachable. <button className="mini-btn" onClick={load}>Retry</button></div>}
      <h3>{issue.summary}</h3>
      <div className="issue-chips">
        <span className={`stchip ${statusClass(issue.status)}`}>{issue.status}</span>
        {issue.priority && <span className="chip">{issue.priority}</span>}
        <span className="chip">{issue.issue_type}</span>
        <span className="chip">{issue.project}</span>
        {issue.labels.map((l) => <span key={l} className="chip ghost">{l}</span>)}
      </div>
      <div className="kv"><span>Assignee</span><b>{issue.assignee || 'Unassigned'}</b></div>
      <div className="kv"><span>Reporter</span><b>{issue.reporter || '—'}</b></div>
      {(issue.parent || issue.epic || issue.sprint) && (
        <div className="kv">
          <span>Hierarchy</span>
          <b>{[issue.parent && `parent ${issue.parent}`, issue.epic && `epic ${issue.epic}`, issue.sprint && issue.sprint].filter(Boolean).join(' · ')}</b>
        </div>
      )}
      {issue.due && <div className="kv"><span>Due</span><b>{issue.due}</b></div>}
      <div className="row">
        <button className="btn sm" onClick={() => setEditOpen(true)}>Edit</button>
        <input placeholder="Assign to…" value={assignTo} onChange={(e) => setAssignTo(e.target.value)} style={{ maxWidth: 140 }} />
        <button className="btn sm ghost" onClick={() => act(() => jiraApi.assign(issue.key, assignTo))}>Assign</button>
      </div>
      {transitions.length > 0 && (
        <div className="row wrap">
          <span className="muted small">Transition:</span>
          {transitions.map((t) => (
            <button key={t.id} className="mini-btn" title={`Move to ${t.name}`}
              onClick={() => { if (confirm(`Move ${issue.key} to "${t.name}"?`)) act(() => jiraApi.transition(issue.key, t.id)) }}>
              {t.name}
            </button>
          ))}
        </div>
      )}
      <h4>Description</h4>
      <div className="issue-desc">
        {issue.description_md ? <Markdown text={issue.description_md} /> : <span className="muted">No description.</span>}
      </div>
      <h4>Comments ({issue.comments.length})</h4>
      {issue.comments.map((c) => (
        <div key={c.id} className="comment">
          <div className="muted small"><b>{c.author}</b> · {c.created}</div>
          <Markdown text={c.body_md} />
        </div>
      ))}
      <div className="row">
        <input placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} style={{ flex: 1 }} />
        <button className="btn sm primary" disabled={!comment.trim()}
          onClick={() => act(() => jiraApi.addComment(issue.key, comment).then(() => setComment('')))}>
          Comment
        </button>
      </div>
      <h4>Attachments ({issue.attachments.length})</h4>
      {issue.attachments.map((a) => (
        <div key={a.id} className="attach-row">
          <span title={`${a.mime} · ${a.size} bytes · ${a.author} · ${a.created}`}>{a.filename}</span>
          <span className="muted small">{Math.round(a.size / 1024)} KB</span>
          <button className="mini-btn" onClick={async () => {
            try { setPreview({ loading: true, ...(await jiraApi.attachmentPreview(a.id, issue.key)) }) }
            catch (e: any) { alert(e.message) }
          }}>Preview</button>
          <a className="mini-btn" href={jiraApi.attachmentDownloadUrl(a.id)} target="_blank" rel="noreferrer">Download</a>
        </div>
      ))}
      {issue.attachments.length === 0 && <div className="muted small">No attachments.</div>}
      {issue.links.length > 0 && (
        <>
          <h4>Linked issues</h4>
          {issue.links.map((l, i) => (
            <div key={i} className="muted small">{l.type} ({l.direction}): <b>{l.key}</b> {l.summary}</div>
          ))}
        </>
      )}
      {issue.changelog.length > 0 && (
        <>
          <h4>Activity</h4>
          <div className="timeline">
            {issue.changelog.slice(-20).reverse().map((h) => (
              <div key={h.id} className="tl-row">
                <span className="muted small">{h.created.slice(0, 16)} · {h.author}</span>
                {h.items.map((it, j) => (
                  <div key={j} className="small">{it.field}: {it.from} → <b>{it.to}</b></div>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
      {editOpen && <EditModal issue={issue} onClose={() => setEditOpen(false)} onSaved={() => { setEditOpen(false); load(); onChanged?.() }} />}
      {preview && (
        <div className="modal-back" onClick={() => setPreview(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head"><h3>Attachment preview</h3>
              <button className="icon-btn" onClick={() => setPreview(null)}>✕</button></div>
            {preview.loading ? 'Loading…' : preview.text
              ? <pre className="diffbox">{preview.text}</pre>
              : <div className="muted">{preview.preview === 'unsupported'
                ? `Preview not supported for ${preview.mime}. Use Download.` : JSON.stringify(preview)}</div>}
          </div>
        </div>
      )}
    </div>
  )
}

function EditModal({ issue, onClose, onSaved }: { issue: IssueDetail; onClose: () => void; onSaved: () => void }) {
  const [summary, setSummary] = useState(issue.summary)
  const [desc, setDesc] = useState(issue.description_md)
  const [priority, setPriority] = useState(issue.priority)
  const [labels, setLabels] = useState(issue.labels.join(', '))
  const [due, setDue] = useState(issue.due)
  const save = async () => {
    await jiraApi.updateIssue(issue.key, { summary, description: desc, priority, labels, duedate: due })
    onSaved()
  }
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>Edit {issue.key}</h3>
          <button className="icon-btn" onClick={onClose}>✕</button></div>
        <label>Summary</label>
        <input value={summary} onChange={(e) => setSummary(e.target.value)} />
        <label>Description (markdown)</label>
        <textarea rows={8} value={desc} onChange={(e) => setDesc(e.target.value)} />
        <div className="row">
          <div style={{ flex: 1 }}><label>Priority</label>
            <input value={priority} onChange={(e) => setPriority(e.target.value)} /></div>
          <div style={{ flex: 1 }}><label>Due</label>
            <input value={due} onChange={(e) => setDue(e.target.value)} placeholder="YYYY-MM-DD" /></div>
        </div>
        <label>Labels (comma-separated)</label>
        <input value={labels} onChange={(e) => setLabels(e.target.value)} />
        <div className="modal-foot">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={save}>Save</button>
        </div>
      </div>
    </div>
  )
}

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: (k: string) => void }) {
  const [f, setF] = useState({ project: '', issue_type: 'Task', summary: '', description: '', priority: '', labels: '', parent: '' })
  const [err, setErr] = useState('')
  const create = async () => {
    setErr('')
    try {
      const r = await jiraApi.createIssue(f)
      onCreated(r.issue.key)
    } catch (e: any) { setErr(e.message) }
  }
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>New Jira issue</h3>
          <button className="icon-btn" onClick={onClose}>✕</button></div>
        <div className="row">
          <div style={{ flex: 1 }}><label>Project *</label><input value={f.project} onChange={set('project')} placeholder="COS" /></div>
          <div style={{ flex: 1 }}><label>Type</label><input value={f.issue_type} onChange={set('issue_type')} /></div>
        </div>
        <label>Summary *</label>
        <input value={f.summary} onChange={set('summary')} />
        <label>Description</label>
        <textarea rows={6} value={f.description} onChange={set('description')} />
        <div className="row">
          <div style={{ flex: 1 }}><label>Priority</label><input value={f.priority} onChange={set('priority')} /></div>
          <div style={{ flex: 1 }}><label>Labels</label><input value={f.labels} onChange={set('labels')} /></div>
          <div style={{ flex: 1 }}><label>Parent/Epic</label><input value={f.parent} onChange={set('parent')} placeholder="COS-1" /></div>
        </div>
        {err && <div className="errorbox sm">{err}</div>}
        <div className="modal-foot">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!f.project.trim() || !f.summary.trim()} onClick={create}>Create</button>
        </div>
      </div>
    </div>
  )
}

/* ---------------- Projects / Boards / Saved ---------------- */

function ProjectsTab({ onOpenProjectIssues }: { onOpenProjectIssues: (k: string) => void }) {
  const [projects, setProjects] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [sel, setSel] = useState<string | null>(null)
  const [issues, setIssues] = useState<TicketCard[]>([])
  useEffect(() => {
    jiraApi.projects().then((r) => setProjects(r.projects)).catch(() => {}).finally(() => setLoading(false))
  }, [])
  const open = async (key: string) => {
    setSel(key); onOpenProjectIssues(key)
    const r = await jiraApi.projectIssues(key, { limit: 25 }).catch(() => ({ issues: [] as TicketCard[] }))
    setIssues(r.issues)
  }
  if (loading) return <div className="muted pad">Loading projects…</div>
  return (
    <div>
      <div className="jira-grid">
        {projects.map((p) => (
          <div key={p.key} className="jira-card clickable" onClick={() => open(p.key)}>
            <h4>{p.key} · {p.name}</h4>
            <div className="muted small">{p.type}</div>
            {p.description && <div className="small">{p.description.slice(0, 200)}</div>}
          </div>
        ))}
      </div>
      {projects.length === 0 && <div className="muted pad">No accessible projects.</div>}
      {sel && issues.length > 0 && (
        <>
          <h4>Recent in {sel}</h4>
          {issues.map((t) => <TicketRow key={t.key} t={t} onOpen={() => {}} />)}
        </>
      )}
    </div>
  )
}

function BoardsTab({ onOpenIssue }: { onOpenIssue: (k: string) => void }) {
  const [boards, setBoards] = useState<any[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [detail, setDetail] = useState<any>(null)
  const [sprints, setSprints] = useState<any[]>([])
  const [issues, setIssues] = useState<TicketCard[]>([])
  useEffect(() => { jiraApi.boards().then((r) => setBoards(r.boards)).catch(() => {}) }, [])
  const open = async (id: number) => {
    setSel(id)
    const [d, s, iss] = await Promise.all([
      jiraApi.board(id).catch((e) => ({ error: String(e) })),
      jiraApi.sprints(id).catch(() => ({ sprints: [] })),
      jiraApi.boardIssues(id, { limit: 25 }).catch(() => ({ issues: [] as TicketCard[] })),
    ])
    setDetail(d); setSprints((s as any).sprints || []); setIssues((iss as any).issues || [])
  }
  return (
    <div className={`issues-wrap ${sel ? 'with-detail' : ''}`}>
      <div className="issues-list">
        {boards.map((b) => (
          <div key={b.id} className="ticket clickable" onClick={() => open(b.id)}>
            <div className="ticket-top"><b>#{b.id} {b.name}</b><span className="chip">{b.type}</span></div>
            <div className="muted small">{b.project}</div>
          </div>
        ))}
        {boards.length === 0 && <div className="muted pad">No boards accessible to this account.</div>}
      </div>
      {sel && (
        <div className="issue-detail">
          <h3>{detail?.board?.name || `Board ${sel}`}</h3>
          {detail?.error && <div className="errorbox sm">{String(detail.error).slice(0, 300)}</div>}
          <SprintList boardId={sel} sprints={sprints} onOpenIssue={onOpenIssue} />
          <h4>Board issues</h4>
          {issues.map((t) => <TicketRow key={t.key} t={t} onOpen={onOpenIssue} />)}
          {issues.length === 0 && <div className="muted small">No issues on this board view.</div>}
        </div>
      )}
    </div>
  )
}

function SprintList({ boardId, sprints, onOpenIssue }: {
  boardId: number; sprints: any[]; onOpenIssue: (k: string) => void
}) {
  const [openId, setOpenId] = useState<number | null>(null)
  const [issues, setIssues] = useState<TicketCard[]>([])
  const open = async (id: number) => {
    setOpenId(id)
    const r = await jiraApi.sprintIssues(id).catch(() => ({ issues: [] as TicketCard[] }))
    setIssues(r.issues)
  }
  return (
    <>
      <h4>Sprints</h4>
      {sprints.map((s) => (
        <div key={s.id}>
          <div className="attach-row">
            <button className="linklike" onClick={() => open(s.id)}>{s.name}</button>
            <span className="chip">{s.state}</span>
            <span className="muted small">{(s.startDate || '').slice(0, 10)} → {(s.endDate || '').slice(0, 10)}</span>
          </div>
          {openId === s.id && issues.map((t) => <TicketRow key={t.key} t={t} onOpen={onOpenIssue} />)}
        </div>
      ))}
      {sprints.length === 0 && <div className="muted small">No sprints on this board.</div>}
    </>
  )
}

function SavedTab({ onRun }: { onRun: (jql: string) => void }) {
  const [items, setItems] = useState<any[]>([])
  const [remote, setRemote] = useState<any[]>([])
  const [name, setName] = useState('')
  const [jql, setJql] = useState('')
  const load = () => {
    jiraApi.savedList().then((r) => setItems(r.searches)).catch(() => {})
    jiraApi.filters().then((r) => setRemote(r.filters)).catch(() => {})
  }
  useEffect(load, [])
  return (
    <div className="jira-grid">
      <div className="jira-card">
        <h4>Atlas saved searches (local)</h4>
        <div className="row">
          <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="JQL" value={jql} onChange={(e) => setJql(e.target.value)} style={{ flex: 2 }} />
          <button className="btn sm" disabled={!name.trim() || !jql.trim()}
            onClick={() => jiraApi.savedCreate(name, jql).then(() => { setName(''); setJql(''); load() })}>
            Save
          </button>
        </div>
        {items.map((s) => (
          <div key={s.id} className="attach-row">
            <b>{s.name}</b><span className="mono small">{s.jql}</span>
            <button className="mini-btn" onClick={() => onRun(s.jql)}>Open</button>
            <button className="mini-btn danger" onClick={() => jiraApi.savedDelete(s.id).then(load)}>Delete</button>
          </div>
        ))}
        {items.length === 0 && <div className="muted small">No saved searches yet.</div>}
      </div>
      <div className="jira-card">
        <h4>Jira-native filters (read-only)</h4>
        {remote.map((f: any) => (
          <div key={f.id} className="attach-row">
            <b>{f.name}</b><span className="mono small">{f.jql}</span>
            <button className="mini-btn" onClick={() => onRun(f.jql)}>Open</button>
          </div>
        ))}
        {remote.length === 0 && <div className="muted small">No shared Jira filters visible to this account.</div>}
      </div>
    </div>
  )
}

/* ---------------- Settings ---------------- */

function SettingsTab({ conn, refresh }: { conn: JiraConnection | null; refresh: () => void }) {
  const [f, setF] = useState({
    display_name: conn?.display_name || '', base_url: conn?.base_url || '',
    email: conn?.email || '', api_token: '', project_keys: (conn?.project_keys || []).join(', '),
    ai_write: conn?.ai_write ?? true, require_approval_deletes: conn?.require_approval_deletes ?? true,
  })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (conn) setF({
      display_name: conn.display_name, base_url: conn.base_url, email: conn.email,
      api_token: '', project_keys: conn.project_keys.join(', '),
      ai_write: conn.ai_write, require_approval_deletes: conn.require_approval_deletes,
    })
  }, [conn?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: string) => (e: any) =>
    setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const save = async () => {
    setBusy(true); setMsg('')
    try {
      const body: any = {
        display_name: f.display_name, base_url: f.base_url, email: f.email,
        project_keys: f.project_keys.split(',').map((s) => s.trim()).filter(Boolean),
        ai_write: f.ai_write, require_approval_deletes: f.require_approval_deletes,
      }
      if (f.api_token.trim()) body.api_token = f.api_token.trim()
      await jiraApi.saveConnection(body)
      setF({ ...f, api_token: '' })
      setMsg('Saved. Run “Test connection” to verify.')
      refresh()
    } catch (e: any) { setMsg(e.message) } finally { setBusy(false) }
  }
  const test = async () => {
    setBusy(true); setMsg('')
    try {
      const r = await jiraApi.testConnection()
      setMsg(`Connected as ${r.me.displayName}. Permissions: ${
        Object.entries(r.permissions).filter(([, v]) => v).map(([k]) => k).join(', ') || 'read-only'}`)
      refresh()
    } catch (e: any) { setMsg(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="jira-grid">
      <div className="jira-card">
        <h4>Jira Cloud connection</h4>
        <div className="muted small">
          Base URL is the site root (https://your-site.atlassian.net). Tokens are encrypted
          server-side and never shown again after saving. Only HTTPS hosts are accepted.
        </div>
        <label>Display name</label>
        <input value={f.display_name} onChange={set('display_name')} placeholder="Work Jira" />
        <label>Base URL *</label>
        <input value={f.base_url} onChange={set('base_url')} placeholder="https://your-site.atlassian.net" />
        <label>Atlassian email *</label>
        <input value={f.email} onChange={set('email')} placeholder="you@company.com" />
        <label>API token {conn?.has_token ? '(leave blank to keep existing)' : '*'}</label>
        <input type="password" value={f.api_token} onChange={set('api_token')}
          placeholder={conn?.has_token ? '•••••• (stored)' : 'Atlassian API token'} autoComplete="off" />
        <label>Project restrictions (optional, comma-separated keys)</label>
        <input value={f.project_keys} onChange={set('project_keys')} placeholder="COS, WEB" />
        <label className="check">
          <input type="checkbox" checked={f.ai_write} onChange={set('ai_write')} />
          Allow the AI agent to mutate Jira (reads always allowed)
        </label>
        <label className="check">
          <input type="checkbox" checked={f.require_approval_deletes} onChange={set('require_approval_deletes')} />
          Require approval for AI-initiated deletions
        </label>
        <div className="row">
          <button className="btn primary sm" disabled={busy} onClick={save}>Save</button>
          <button className="btn sm" disabled={busy || !conn} onClick={test}>Test connection</button>
          {conn && (
            <button className="btn sm danger" disabled={busy}
              onClick={() => {
                if (confirm('Disconnect Jira and clear the local index?')) {
                  jiraApi.deleteConnection().then(refresh)
                }
              }}>
              Disconnect
            </button>
          )}
        </div>
        {msg && <div className={/connect|saved/i.test(msg) ? 'muted' : 'errorbox sm'}>{msg}</div>}
      </div>
      <div className="jira-card">
        <h4>Status</h4>
        {!conn && <div className="muted">Not connected.</div>}
        {conn && (
          <>
            <div className="kv"><span>State</span><b>{conn.status}</b></div>
            <div className="kv"><span>Account</span><b>{conn.account_name || conn.email}</b></div>
            <div className="kv"><span>Last validated</span>
              <b>{conn.last_validated_at ? new Date(conn.last_validated_at).toLocaleString() : 'never'}</b></div>
            <div className="kv"><span>Last sync</span>
              <b>{conn.last_sync_at ? new Date(conn.last_sync_at).toLocaleString() : 'never'}</b></div>
            {conn.last_sync_error && <div className="errorbox sm">{conn.last_sync_error}</div>}
            <div className="kv"><span>Indexed</span><b>{conn.indexed_issues} issues</b></div>
            <div className="muted small">
              Approval policy: reads run automatically; ordinary mutations follow the workspace
              auto-approve setting; deletions, bulk changes, unlinking, and rank changes always
              ask first. Every mutation is audited (see Overview).
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export function useJiraShortcut(setView: (v: string) => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'j') {
        e.preventDefault()
        setView('jira')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setView])
}
