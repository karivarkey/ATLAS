import { Brain, Check, Database, FileText, History, Play, Search, X } from 'lucide-react'
import React, { useEffect, useState } from 'react'
import { api } from './api'
import { useStore } from './store'
import { Markdown } from './markdown'

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
  const [content, setContent] = useState('')
  const [versions, setVersions] = useState<any[]>([])
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const load = async () => {
    const r = await api.memoryFile(name)
    setContent(r.content)
    setVersions(r.versions)
    setDraft(r.content)
  }
  useEffect(() => { load() }, [name])
  return (
    <div>
      <div className="mem-searchrow">
        <FileText size={14} />
        <span className="muted small">v{versions[0]?.version || 0} · {versions[0]?.source}</span>
        <button className="mini-btn" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Cancel' : 'Edit'}
        </button>
        {editing && (
          <button
            className="btn primary sm"
            onClick={async () => {
              await api.saveMemoryFile(name, draft)
              setEditing(false)
              load()
            }}
          >
            <Check size={13} /> Save
          </button>
        )}
      </div>
      {editing ? (
        <textarea className="mem-editor" value={draft} onChange={(e) => setDraft(e.target.value)} />
      ) : (
        <div className="mem-file"><Markdown text={content} /></div>
      )}
      <h4>History (rollback preserves everything)</h4>
      {versions.map((v) => (
        <div key={v.version} className="mem-version">
          <span>v{v.version} · {v.source} · {new Date(v.created_at).toLocaleString()}</span>
          <button className="mini-btn" onClick={() => api.rollbackMemoryFile(name, v.version).then(load)}>
            Roll back to here
          </button>
        </div>
      ))}
    </div>
  )
}

function JobsTab() {
  const [jobs, setJobs] = useState<any[]>([])
  const [health, setHealth] = useState<any>(null)
  const [proposals, setProposals] = useState<any[]>([])
  const load = async () => {
    setJobs((await api.memoryJobs()).jobs)
    setHealth(await api.memoryHealth())
    setProposals((await api.memoryProposals()).proposals)
  }
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [])
  return (
    <div>
      <div className="mem-searchrow">
        <History size={14} />
        <button className="btn primary sm" onClick={() => api.runDream([]).then(load)}>
          <Play size={13} /> Dream now
        </button>
        <button className="mini-btn" onClick={load}>Refresh</button>
      </div>
      {health && (
        <div className="healthbox">
          <div>Embeddings: {health.embeddings.model} · {health.embeddings.ok ? 'ok' : `ERROR: ${health.embeddings.last_error}`}</div>
          <div>Index backlog: {health.indexing.pending} pending · {health.indexing.failed} failed · {health.indexing.embedded}/{health.indexing.chunks} embedded</div>
          <div>Last dream: {health.last_dream ? `${health.last_dream.id} (${health.last_dream.status})` : 'never'}</div>
        </div>
      )}
      <h4>Pending approvals (SOUL.md changes + low-confidence facts)</h4>
      {proposals.map((p) => (
        <ProposalCard key={p.id} p={p} reload={load} />
      ))}
      {proposals.length === 0 && <div className="muted">No pending proposals.</div>}
      <h4>Dream jobs</h4>
      {jobs.map((j) => (
        <div key={j.id} className="mem-version">
          <span>{j.id} · {j.trigger} · <b>{j.status}</b> · {new Date(j.created_at).toLocaleString()}</span>
          <span className="muted small">{j.stats_json}</span>
        </div>
      ))}
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
