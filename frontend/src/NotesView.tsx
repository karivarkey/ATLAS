import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Markdown } from './markdown'
import { notesApi, TreeNode, VaultNote } from './notesApi'

interface Props {
  onOpenConversation?: (convId: string) => void
  initialPath?: string | null
}

type SaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error'

function flatten(nodes: TreeNode[], depth = 0): { node: TreeNode; depth: number }[] {
  const out: { node: TreeNode; depth: number }[] = []
  for (const n of nodes) {
    out.push({ node: n, depth })
    if (n.type === 'folder' && n.children) out.push(...flatten(n.children, depth + 1))
  }
  return out
}

export function NotesView({ initialPath }: Props) {
  const [tree, setTree] = useState<TreeNode[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [note, setNote] = useState<VaultNote | null>(null)
  const [draft, setDraft] = useState('')
  const [mode, setMode] = useState<'source' | 'preview'>('source')
  const [saveState, setSaveState] = useState<SaveState>('clean')
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<any[] | null>(null)
  const [newPath, setNewPath] = useState('')
  const timer = useRef<number | null>(null)
  const baseRef = useRef<{ version: number; hash: string }>({ version: 0, hash: '' })

  const refreshTree = useCallback(async () => {
    try {
      const t = await notesApi.tree()
      setTree(t.tree)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 200))
    }
  }, [])

  useEffect(() => { refreshTree() }, [refreshTree])

  useEffect(() => {
    if (initialPath) open(initialPath)
  }, [initialPath])

  const open = useCallback(async (path: string) => {
    setSel(path)
    setErr('')
    setSaveState('clean')
    try {
      const n = await notesApi.get(path)
      setNote(n)
      setDraft(n.content)
      baseRef.current = { version: n.version, hash: n.content_hash }
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }, [])

  const dirty = note != null && draft !== note.content

  // Autosave with optimistic-concurrency guard.
  useEffect(() => {
    if (!note || !dirty) {
      if (note && !dirty && saveState === 'dirty') setSaveState('clean')
      return
    }
    setSaveState('dirty')
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(async () => {
      if (!sel) return
      setSaveState('saving')
      try {
        const n = await notesApi.update(sel, draft, baseRef.current.version, baseRef.current.hash)
        setNote(n)
        setDraft(n.content)
        baseRef.current = { version: n.version, hash: n.content_hash }
        setSaveState('saved')
        refreshTree()
      } catch (e: any) {
        const msg = String(e.message || e)
        setSaveState(msg.includes('409') ? 'conflict' : 'error')
        setErr(msg.slice(0, 400))
      }
    }, 900)
    return () => { if (timer.current) window.clearTimeout(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])

  const doSearch = async () => {
    if (!q.trim()) { setResults(null); return }
    try {
      const r = await notesApi.search(q.trim())
      setResults(r.results)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 200))
    }
  }

  const create = async () => {
    const p = newPath.trim()
    if (!p) return
    setErr('')
    try {
      await notesApi.create(p.endsWith('.md') ? p : `${p}.md`, `# ${p.split('/').pop()!.replace(/\.md$/, '')}\n`)
      setNewPath('')
      await refreshTree()
      open(p.endsWith('.md') ? p : `${p}.md`)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }

  const words = draft ? draft.split(/\s+/).filter(Boolean).length : 0
  const saveLabel: Record<SaveState, string> = {
    clean: 'Saved', dirty: 'Unsaved…', saving: 'Saving…',
    saved: 'Saved ✓', conflict: 'Conflict — reload', error: 'Save failed',
  }

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* left: tree */}
      <div style={{ width: 240, minWidth: 240, borderRight: '1px solid var(--line)',
        display: 'flex', flexDirection: 'column', background: 'var(--panel)' }}>
        <div style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') doSearch() }}
              placeholder="Search notes…" style={{ flex: 1, padding: '6px 8px',
                border: '1px solid var(--line)', borderRadius: 8, fontSize: 13 }} />
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input value={newPath} onChange={(e) => setNewPath(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') create() }}
              placeholder="new/note.md" style={{ flex: 1, padding: '6px 8px',
                border: '1px solid var(--line)', borderRadius: 8, fontSize: 13 }} />
            <button className="mini-btn" onClick={create}>+</button>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 6px 10px' }}>
          {results ? (
            <>
              <button className="mini-btn" onClick={() => { setResults(null); setQ('') }}>
                ← back to tree
              </button>
              {results.map((r) => (
                <div key={r.path} className="conv" onClick={() => open(r.path)}>
                  <div className="conv-title">{r.title || r.path}</div>
                  <div className="conv-model">{r.path}</div>
                </div>
              ))}
              {results.length === 0 && <div className="muted pad">No matches.</div>}
            </>
          ) : flatten(tree).map(({ node, depth }) => (
            node.type === 'folder' ? (
              <div key={node.path} style={{ padding: '5px 8px 5px ' + (8 + depth * 12) + 'px',
                fontSize: 12, color: 'var(--muted)', fontWeight: 600 }}>
                📁 {node.name}
              </div>
            ) : (
              <div key={node.path} className={`conv ${sel === node.path ? 'active' : ''}`}
                onClick={() => open(node.path)} style={{ paddingLeft: 8 + depth * 12 }}>
                <div className="conv-title">{node.title || node.name}</div>
                <div className="conv-meta"><span className="conv-model">{node.path}</span></div>
              </div>
            )
          ))}
        </div>
      </div>

      {/* middle: editor */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, padding: 14 }}>
        {!note ? (
          <div className="muted">Select or create a note.</div>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <b style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{note.path}</b>
              <span className="muted small">v{note.version} · {words} words · {saveLabel[saveState]}</span>
              <button className="mini-btn" onClick={() => setMode(mode === 'source' ? 'preview' : 'source')}>
                {mode === 'source' ? 'Preview' : 'Source'}
              </button>
              <button className="mini-btn" onClick={async () => {
                const np = prompt('Rename/move to:', note.path)
                if (!np || np === note.path) return
                try {
                  await notesApi.move(note.path, np)
                  setSel(np)
                  await refreshTree()
                  open(np)
                } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
              }}>Rename</button>
              <button className="mini-btn" onClick={async () => {
                if (!confirm(`Delete ${note.path}?`)) return
                try {
                  await notesApi.remove(note.path)
                  setNote(null); setSel(null); setDraft('')
                  refreshTree()
                } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
              }}>Delete</button>
            </div>
            {saveState === 'conflict' && (
              <div className="errorbox">
                Stale write rejected — the note changed since you opened it.
                <button className="btn sm" onClick={() => sel && open(sel)}>Reload latest</button>
              </div>
            )}
            {mode === 'source' ? (
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                style={{ flex: 1, fontFamily: 'monospace', fontSize: 13, padding: 12,
                  border: '1px solid var(--line)', borderRadius: 9, resize: 'none' }} />
            ) : (
              <div style={{ flex: 1, overflowY: 'auto', border: '1px solid var(--line)',
                borderRadius: 9, padding: 12, background: 'var(--panel)' }}>
                <Markdown text={draft} />
              </div>
            )}
            {note.stale_readers.length > 0 && (
              <div className="muted small" style={{ marginTop: 6 }}>
                {note.stale_readers.filter((s) => s.stale).length > 0 &&
                  `⚠ ${note.stale_readers.filter((s) => s.stale).length} AI execution(s) read a stale version`}
              </div>
            )}
          </>
        )}
        {err && <div className="errorbox" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      {/* right: links */}
      <div style={{ width: 250, minWidth: 250, borderLeft: '1px solid var(--line)',
        padding: 12, overflowY: 'auto', background: 'var(--panel)' }}>
        {!note ? <div className="muted small">No note selected.</div> : (
          <>
            <h4 style={h4}>Outlinks</h4>
            {note.outlinks.length === 0 && <div className="muted small">None.</div>}
            {note.outlinks.map((o, i) => (
              <div key={i} className="mem-ref">
                {o.broken ? '⛔ ' : '→ '}{o.target}
                {o.alias ? ` (${o.alias})` : ''}{o.heading ? ` #${o.heading}` : ''}
              </div>
            ))}
            <h4 style={h4}>Backlinks</h4>
            {note.backlinks.length === 0 && <div className="muted small">None.</div>}
            {note.backlinks.map((b) => (
              <div key={b.path} className="mem-ref">
                <a href="#" onClick={(e) => { e.preventDefault(); open(b.path) }}>← {b.path}</a>
              </div>
            ))}
            <h4 style={h4}>Linked tasks</h4>
            {note.linked_tasks.length === 0 && <div className="muted small">None.</div>}
            {note.linked_tasks.map((t) => (
              <div key={t.id} className="mem-ref">
                <span className="chip">{t.status}</span> {t.title}
              </div>
            ))}
            {note.broken_links.length > 0 && (
              <>
                <h4 style={h4}>Broken</h4>
                {note.broken_links.map((b, i) => (
                  <div key={i} className="mem-ref">⛔ [[{b.target}]]</div>
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

const h4: React.CSSProperties = {
  margin: '12px 0 6px', fontSize: 12, textTransform: 'uppercase',
  letterSpacing: '0.05em', color: 'var(--muted)',
}
