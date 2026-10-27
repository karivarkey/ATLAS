import { Brain, Check, Database, History, Play, Search, X } from 'lucide-react'
import React, { useEffect, useState } from 'react'
import { api } from './api'
import { useStore } from './store'
import { MemoryFilePanel } from './MemoryFiles'

type Tab = 'search' | 'records' | 'soul' | 'user' | 'jobs'

export function MemoryView({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('search')
  return (
    <div className="memoryview">
      <div className="mem-head">
        <h2><Brain size={17} /> Memory</h2>
        <button className="icon-btn" onClick={onClose} title="Back to chat"><X size={15} /></button>
      </div>
      <div className="mem-tabs">
        {([['search', 'Search'], ['records', 'Knowledge'], ['soul', 'SOUL.md'],
           ['user', 'USER.md'], ['jobs', 'Dreams']] as [Tab, string][]).map(([t, label]) => (
          <button key={t} className={`mem-tab ${tab === t ? 'sel' : ''}`} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </div>
      <div className="mem-body">
        {tab === 'search' && <SearchTab />}
        {tab === 'records' && <RecordsTab />}
        {(tab === 'soul' || tab === 'user') && <FileTab name={tab === 'soul' ? 'SOUL.md' : 'USER.md'} />}
        {tab === 'jobs' && <JobsTab />}
      </div>
    </div>
  )
}

function SearchTab() {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [meta, setMeta] = useState('')
  const selectConversation = useStore((s) => s.selectConversation)
  const go = async () => {
    if (!q.trim()) return
    setMeta('Searching…')
    try {
      const r = await api.memorySearch(q.trim())
      setResults(r.results)
      setMeta(`${r.results.length} result(s) · ${r.semantic ? 'hybrid' : 'lexical only'} · scope ${r.scope}`)
    } catch (e: any) {
      setMeta(`Error: ${e.message}`)
    }
  }
  return (
    <div>
      <div className="mem-searchrow">
        <Search size={14} />
        <input
          placeholder="Search every authorized conversation… (exact text or meaning)"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') go() }}
        />
        <button className="btn primary sm" onClick={go}>Search</button>
      </div>
      <div className="muted small">{meta}</div>
      <div className="mem-results">
        {results.map((r) => (
          <div key={r.chunk_id} className="mem-result">
            <div className="mem-result-head">
              <span className="chip">{r.match}</span>
              <span className="muted small">score {r.score} · {new Date(r.created_at).toLocaleString()}</span>
            </div>
            <div className="mem-result-text">{(r.text || '').slice(0, 500)}</div>
            <button
              className="mini-btn"
              onClick={() => selectConversation(r.conversation_id)}
              title="Open original conversation"
            >
              Open {r.conversation_id.slice(0, 18)}…
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function RecordsTab() {
  const [records, setRecords] = useState<any[]>([])
  const [filter, setFilter] = useState('')
  const load = async () => {
    const r = await api.memoryRecords('', filter)
    setRecords(r.records)
  }
  useEffect(() => { load() }, [filter])
  return (
    <div>
      <div className="mem-searchrow">
        <Database size={14} />
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">all statuses</option>
          <option value="active">active</option>
          <option value="proposed">proposed</option>
          <option value="superseded">superseded</option>
          <option value="archived">archived</option>
        </select>
        <button className="mini-btn" onClick={load}>Refresh</button>
      </div>
      {records.map((r) => (
        <div key={r.id} className="mem-result">
          <div className="mem-result-head">
            <span className="chip">{r.memory_type}</span>
            <span className="chip stale">{r.status}</span>
            <span className="muted small">conf {r.confidence}</span>
          </div>
          <div><b>{r.subject}</b> · {r.predicate} · <b>{r.object_value}</b></div>
          <div className="muted">{r.summary}</div>
          <div className="muted small">src: {(JSON.parse(r.source_conversation_ids || '[]')).join(', ')}</div>
          {r.status !== 'archived' && (
            <button
              className="mini-btn"
              onClick={() => api.deleteRecord(r.id).then(load)}
            >
              Archive
            </button>
          )}
        </div>
      ))}
      {records.length === 0 && <div className="muted pad">No records yet. Run a dream to consolidate.</div>}
    </div>
  )
}

function FileTab({ name }: { name: string }) {
  return <MemoryFilePanel name={name} />;
}

function JobsTab() {
  const [jobs, setJobs] = useState<any[]>([])
  const [health, setHealth] = useState<any>(null)
  const [proposals, setProposals] = useState<any[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const load = async () => {
    setJobs((await api.memoryJobs()).jobs)
    setHealth(await api.memoryHealth())
    setProposals((await api.memoryProposals()).proposals)
  }
  useEffect(() => {
    load()
    const t = setInterval(load, 8000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {
    if (!jobs.some((j) => !['completed', 'failed', 'cancelled'].includes(j.status))) return
    const fast = setInterval(load, 2500)
    return () => clearInterval(fast)
  }, [jobs])
  return (
    <div>
      <div className="mem-searchrow">
        <History size={14} />
        <button className="btn primary sm" onClick={() => api.runDream([]).then(load)}>
          <Play size={13} /> Dream now
        </button>
        <button className="mini-btn" onClick={load}>Refresh</button>
        <span className="muted small">Dreams run on their own — daily 3am, after long chats, or on demand. No interaction needed.</span>
      </div>
      {health && (
        <div className="healthbox">
          <div>Embeddings: {health.embeddings.model} · {health.embeddings.ok ? 'ok' : `ERROR: ${health.embeddings.last_error}`}</div>
          <div>Index backlog: {health.indexing.pending} pending · {health.indexing.failed} failed · {health.indexing.embedded}/{health.indexing.chunks} embedded</div>
          <div>Last dream: {health.last_dream ? `${health.last_dream.id} (${health.last_dream.status})` : 'never'}</div>
        </div>
      )}
      <h4>Dreams ({jobs.length})</h4>
      {jobs.length === 0 && <div className="muted pad">No dreams yet. Run one above — it replays recent chats, ponders ideas and open questions, then files durable memories.</div>}
      <div className="dream-grid">
        {jobs.map((j) => (
          <DreamCard key={j.id} job={j} onOpen={() => setSelected(j.id)} />
        ))}
      </div>
      {selected && (
        <DreamModal jobId={selected} onClose={() => { setSelected(null); load() }} />
      )}
      <h4>Pending approvals (SOUL.md changes + low-confidence facts)</h4>
      {proposals.map((p) => (
        <ProposalCard key={p.id} p={p} reload={load} />
      ))}
      {proposals.length === 0 && <div className="muted">No pending proposals.</div>}
    </div>
  )
}

function dreamBody(job: any): string {
  const thinking = (job.thinking_preview || job.thinking || '').slice(0, 280)
  if (thinking) return thinking
  let stats: any = {}
  try { stats = JSON.parse(job.stats_json || '{}') } catch { /* ignore */ }
  const running = !['completed', 'failed', 'cancelled'].includes(job.status)
  if (job.status === 'failed') return (job.error || 'Dream failed.').slice(0, 280)
  if (running) return 'Dreaming…'
  if (stats.note === 'nothing new') return 'Nothing new since the last dream — all caught up.'
  return 'No thinking recorded (dream from before the revamp).'
}

function DreamCard({ job, onOpen }: { job: any; onOpen: () => void }) {
  let stats: any = {}
  try { stats = JSON.parse(job.stats_json || '{}') } catch { /* ignore */ }
  const msgs = stats.messages ?? '—'
  return (
    <button className="dream-card" onClick={onOpen} title="Open dream">
      <div className="dream-card-head">
        <span className="chip">{job.trigger}</span>
        <span className={`chip ${job.status === 'completed' ? '' : 'stale'}`}>{job.status}</span>
        <span className="muted small">{new Date(job.created_at).toLocaleString()}</span>
      </div>
      <div className="dream-card-stats muted small">
        {msgs} msgs · {job.ideas_count ?? 0} ideas · {job.questions_count ?? 0} questions · {stats.applied_records ?? 0} filed
        {stats.user_md_version ? ` · USER.md v${stats.user_md_version}` : ''}
        {(stats.soul_proposals ?? 0) > 0 ? ` · SOUL.md +${stats.soul_proposals}` : ''}
      </div>
      <div className="dream-card-thinking">{dreamBody(job)}</div>
      <div className="dream-card-open muted small">Tap to open →</div>
    </button>
  )
}

function DreamModal({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let live = true
    api.memoryJobDetail(jobId).then((d) => { if (live) setDetail(d) }).catch((e: any) => { if (live) setErr(String(e?.message || e)) })
    return () => { live = false }
  }, [jobId])
  const job = detail?.job || {}
  let stats: any = {}
  try { stats = JSON.parse(job.stats_json || '{}') } catch { /* ignore */ }
  const decide = async (pid: string, ok: boolean) => {
    await api.decideProposal(pid, ok)
    const d = await api.memoryJobDetail(jobId)
    setDetail(d)
  }
  return (
    <div className="dream-modal-overlay" onClick={onClose}>
      <div className="dream-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Dream details">
        <div className="dream-modal-head">
          <div>
            <b>Dream {String(job.id || jobId).slice(0, 18)}</b>
            <span className="muted small"> · {job.trigger} · {job.status} · {job.created_at ? new Date(job.created_at).toLocaleString() : ''}</span>
          </div>
          <button className="icon-btn" onClick={onClose} title="Close"><X size={15} /></button>
        </div>
        {err && <div className="errorbox">{err}</div>}
        {!detail && !err && <div className="muted pad">Reading the dream…</div>}
        {detail && (
          <div className="dream-modal-body">
            <h5>What the AI was thinking</h5>
            <div className="dream-thinking">{detail.thinking || 'No thinking recorded for this dream.'}</div>
            <h5>Ideas ({(detail.ideas || []).length})</h5>
            {(detail.ideas || []).length === 0 && <div className="muted small">None.</div>}
            {(detail.ideas || []).map((idea: any, i: number) => (
              <div key={i} className="mem-result">
                <div><b>{idea.title}</b> <span className="muted small">conf {idea.confidence}</span></div>
                <div className="muted">{idea.detail}</div>
              </div>
            ))}
            <h5>Open questions ({(detail.questions || []).length})</h5>
            {(detail.questions || []).length === 0 && <div className="muted small">None.</div>}
            {(detail.questions || []).map((q: any, i: number) => (
              <div key={i} className="mem-result">
                <div><b>{q.question}</b> <span className="muted small">conf {q.confidence}</span></div>
                <div className="muted">{q.why}</div>
              </div>
            ))}
            <h5>Filed to memory ({(detail.records || []).length})</h5>
            {(detail.records || []).length === 0 && <div className="muted small">Nothing durable this time.</div>}
            {(detail.records || []).map((r: any) => (
              <div key={r.id} className="mem-result">
                <div className="mem-result-head">
                  <span className="chip">{r.memory_type}</span>
                  <span className="chip stale">{r.status}</span>
                  <span className="muted small">conf {r.confidence}</span>
                </div>
                <div><b>{r.subject}</b> · {r.predicate} · <b>{r.object_value}</b></div>
                <div className="muted">{r.summary}</div>
              </div>
            ))}
            <h5>Effects</h5>
            <div className="muted small">
              Messages replayed: {stats.messages ?? '—'} · proposed: {stats.proposed ?? '—'} · filed: {stats.applied_records ?? '—'}
              {stats.user_md_version ? <> · USER.md → v{stats.user_md_version}</> : ' · USER.md unchanged'}
              {stats.note ? <> · {stats.note}</> : null}
              {stats.error ? <> · error: {String(stats.error).slice(0, 200)}</> : null}
              {(stats.soul_proposals ?? 0) > 0 ? <> · SOUL.md +{stats.soul_proposals} applied</> : ' · no SOUL changes'}
              {' · '}vectors via qwen embedding worker
            </div>
            {(detail.proposals || []).length > 0 && (
              <>
                <h5>Proposals from this dream</h5>
                {(detail.proposals || []).map((p: any) => (
                  <div key={p.id} className="mem-result">
                    <div className="mem-result-head">
                      <span className="chip">{p.kind}</span>
                      <span className="muted small">{p.status} · conf {p.confidence}</span>
                      {p.status === 'proposed' && (
                        <>
                          <button className="btn primary sm" onClick={() => decide(p.id, true)}><Check size={13} /> Approve</button>
                          <button className="btn sm" onClick={() => decide(p.id, false)}>Reject</button>
                        </>
                      )}
                    </div>
                    <pre className="toolcard-output">{JSON.stringify(JSON.parse(p.payload_json || '{}'), null, 1).slice(0, 2000)}</pre>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function ProposalCard({ p, reload }: { p: any; reload: () => void }) {
  const [open, setOpen] = useState(false)
  let payload: any = {}
  try { payload = JSON.parse(p.payload_json) } catch { /* ignore */ }
  return (
    <div className="mem-result">
      <div className="mem-result-head">
        <span className="chip">{p.kind}</span>
        <span className="muted small">conf {p.confidence}</span>
        <button className="mini-btn" onClick={() => setOpen((v) => !v)}>{open ? 'Hide' : 'Evidence'}</button>
        <button className="btn primary sm" onClick={() => api.decideProposal(p.id, true).then(reload)}>
          <Check size={13} /> Approve
        </button>
        <button className="btn sm" onClick={() => api.decideProposal(p.id, false).then(reload)}>
          Reject
        </button>
      </div>
      <pre className="toolcard-output">{JSON.stringify(payload, null, 1).slice(0, 2000)}</pre>
      {open && <pre className="toolcard-output">{p.evidence_json}</pre>}
    </div>
  )
}
