import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft, Check, Eye, FilePlus2, FileText, FolderPlus, Link2, Pencil,
  Plus, RefreshCw, Search, Trash2, X,
} from 'lucide-react'
import { Markdown } from './markdown'
import { notesApi, TreeNode, VaultNote } from './notesApi'

interface Props {
  onOpenConversation?: (convId: string) => void
  initialPath?: string | null
}

type SaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error'
type Mode = 'edit' | 'preview' | 'split'

const SAVE_LABEL: Record<SaveState, string> = {
  clean: 'Saved', dirty: 'Unsaved changes', saving: 'Saving…',
  saved: 'Saved', conflict: 'Conflict', error: 'Save failed',
}

export function NotesView({ initialPath }: Props) {
  const [tree, setTree] = useState<TreeNode[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [note, setNote] = useState<VaultNote | null>(null)
  const [draft, setDraft] = useState('')
  const [mode, setMode] = useState<Mode>('edit')
  const [saveState, setSaveState] = useState<SaveState>('clean')
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<any[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(['']))
  const [showNew, setShowNew] = useState(false)
  const [showRename, setShowRename] = useState(false)
  const [showDelete, setShowDelete] = useState(false)
  const [loading, setLoading] = useState(false)
  const timer = useRef<number | null>(null)
  const baseRef = useRef<{ version: number; hash: string }>({ version: 0, hash: '' })

  const refreshTree = useCallback(async () => {
    try {
      const t = await notesApi.tree()
      setTree(t.tree)
      // auto-expand folders on first load so the tree isn't empty-looking
      setExpanded((prev) => {
        if (prev.size > 1) return prev
        const next = new Set(prev)
        const walk = (ns: TreeNode[]) => {
          for (const n of ns) {
            if (n.type === 'folder') { next.add(n.path); if (n.children) walk(n.children) }
          }
        }
        walk(t.tree)
        return next
      })
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 200))
    }
  }, [])

  useEffect(() => { refreshTree() }, [refreshTree])

  const open = useCallback(async (path: string) => {
    setSel(path)
    setErr('')
    setSaveState('clean')
    setLoading(true)
    try {
      const n = await notesApi.get(path)
      setNote(n)
      setDraft(n.content)
      baseRef.current = { version: n.version, hash: n.content_hash }
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialPath) open(initialPath)
  }, [initialPath, open])

  const dirty = note != null && draft !== note.content

  const saveNow = useCallback(async (content: string) => {
    if (!sel || !note) return
    setSaveState('saving')
    try {
      const n = await notesApi.update(sel, content, baseRef.current.version, baseRef.current.hash)
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
  }, [sel, note, refreshTree])

  // Autosave (debounced) + Cmd+S to save immediately.
  useEffect(() => {
    if (!note || !dirty) {
      if (note && !dirty && saveState === 'dirty') setSaveState('clean')
      return
    }
    setSaveState('dirty')
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { saveNow(draft) }, 900)
    return () => { if (timer.current) window.clearTimeout(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        if (!note) return
        e.preventDefault()
        if (timer.current) window.clearTimeout(timer.current)
        saveNow(draft)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [draft, note, saveNow])

  const doSearch = async () => {
    if (!q.trim()) { setResults(null); return }
    setSearching(true)
    try {
      const r = await notesApi.search(q.trim())
      setResults(r.results)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 200))
    } finally {
      setSearching(false)
    }
  }

  const toggleFolder = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const noteCount = useMemo(() => {
    let c = 0
    const walk = (ns: TreeNode[]) => {
      for (const n of ns) {
        if (n.type === 'note') c++
        else if (n.children) walk(n.children)
      }
    }
    walk(tree)
    return c
  }, [tree])

  const words = draft ? draft.split(/\s+/).filter(Boolean).length : 0
  const minutes = Math.max(1, Math.ceil(words / 200))

  return (
    <div className="notes">
      <div className="notes-head">
        <h2><FileText size={17} /> Notes</h2>
        <span className="sub">{noteCount} note{noteCount === 1 ? '' : 's'}</span>
        <span className="spacer" />
        <button className="mini-btn" title="Refresh tree" onClick={refreshTree}>
          <RefreshCw size={12} style={{ verticalAlign: -2 }} /> Refresh
        </button>
        <button className="btn primary sm" onClick={() => setShowNew(true)}>
          <Plus size={14} /> New note
        </button>
      </div>

      <div className="notes-body">
        {/* ------- sidebar ------- */}
        <div className="notes-side">
          <div className="notes-tools">
            <div className="notes-search">
              <Search size={14} />
              <input
                value={q}
                onChange={(e) => {
                  setQ(e.target.value)
                  if (!e.target.value.trim()) setResults(null)
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') doSearch() }}
                placeholder="Search notes…  (Enter)"
              />
              {q && (
                <button className="icon-btn" style={{ padding: 2 }} onClick={() => { setQ(''); setResults(null) }}>
                  <X size={13} />
                </button>
              )}
            </div>
            <div className="notes-newrow">
              <button className="btn sm" onClick={() => setShowNew(true)}>
                <FilePlus2 size={13} /> Note
              </button>
              <button
                className="btn sm" title="New folder"
                onClick={async () => {
                  const p = prompt('New folder path (e.g. ideas/projects):')
                  if (!p?.trim()) return
                  try { await notesApi.mkdir(p.trim()); refreshTree() }
                  catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
                }}
              >
                <FolderPlus size={13} />
              </button>
            </div>
          </div>

          <div className="notes-tree">
            {results ? (
              <>
                <button className="mini-btn notes-back" onClick={() => { setResults(null); setQ('') }}>
                  <ArrowLeft size={12} style={{ verticalAlign: -2 }} /> back to all notes
                </button>
                {searching && <div className="muted pad">Searching…</div>}
                {!searching && results.map((r) => (
                  <div key={r.path} className="notes-result" onClick={() => open(r.path)}>
                    <div className="t">{r.title || r.path}</div>
                    <div className="p">{r.path}</div>
                    {r.snippet && <div className="s">{stripSnippet(r.snippet)}</div>}
                  </div>
                ))}
                {!searching && results.length === 0 && (
                  <div className="muted pad">No matches for “{q}”. <br />
                    <button className="mini-btn" style={{ marginTop: 8 }} onClick={() => setShowNew(true)}>
                      Create it as a note
                    </button>
                  </div>
                )}
              </>
            ) : (
              <TreeNodes nodes={tree} depth={0} sel={sel} expanded={expanded}
                onToggle={toggleFolder} onOpen={open} />
            )}
            {!results && tree.length === 0 && (
              <div className="muted pad">No notes yet.<br />Create your first note →</div>
            )}
          </div>
        </div>

        {/* ------- editor ------- */}
        <div className="notes-main">
          {!note ? (
            <div className="notes-empty">
              <div className="notes-empty-card">
                <FileText size={28} color="var(--muted)" />
                <h3>{loading ? 'Opening…' : 'Pick a note'}</h3>
                <p>Select a note on the left, search above, or create a new one. Notes autosave as you type (Ctrl+S saves now).</p>
                <button className="btn primary sm" onClick={() => setShowNew(true)}>
                  <Plus size={14} /> New note
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="notes-toolbar">
                <div className="notes-crumb">
                  <div className="path">{note.path}</div>
                  <div className="title">{note.title || note.path.split('/').pop()}</div>
                </div>
                <div className="notes-meta">
                  <span className={`save-dot ${saveState}`} title={SAVE_LABEL[saveState]} />
                  <span>{SAVE_LABEL[saveState]} · v{note.version} · {words} words · ~{minutes} min</span>
                </div>
                <div className="seg" role="tablist" aria-label="Editor mode">
                  {([['edit', Pencil, 'Write'], ['preview', Eye, 'Preview'], ['split', Check, 'Split']] as const).map(
                    ([m, Icon, label]) => (
                      <button key={m} className={mode === m ? 'sel' : ''} onClick={() => setMode(m)}>
                        <Icon size={13} /> {label}
                      </button>
                    ),
                  )}
                </div>
                <div className="notes-actions">
                  <button className="mini-btn" onClick={() => setShowRename(true)}>Rename</button>
                  <button className="mini-btn" title="Delete note" onClick={() => setShowDelete(true)}>
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>

              {saveState === 'conflict' && (
                <div className="errorbox notes-conflict">
                  This note changed elsewhere — your autosave was paused to avoid overwriting it.
                  <button className="btn sm" onClick={() => sel && open(sel)}>Reload latest</button>
                </div>
              )}
              {err && <div className="errorbox notes-conflict">{err}</div>}

              <div className="notes-editor-wrap">
                <div className="notes-editor">
                  {(mode === 'edit' || mode === 'split') && (
                    <div className="notes-splitpane">
                      {mode === 'split' && <div className="notes-pane-label">Write</div>}
                      <textarea
                        className="notes-textarea"
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        placeholder="# Title&#10;&#10;Start writing… [[link to another note]] works."
                        spellCheck
                      />
                    </div>
                  )}
                  {(mode === 'preview' || mode === 'split') && (
                    <div className="notes-splitpane">
                      {mode === 'split' && <div className="notes-pane-label">Preview</div>}
                      <div className={`notes-preview ${mode === 'split' ? 'split' : ''}`}>
                        <Markdown text={draft || '*Nothing to preview yet.*'} />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* ------- context rail ------- */}
        {note && (
          <div className="notes-context">
            <h4><Link2 size={12} /> Connected</h4>
            {note.outlinks.length === 0 && note.backlinks.length === 0 && note.linked_tasks.length === 0 && (
              <div className="muted small">No connections yet. Use [[note name]] to link notes.</div>
            )}
            {note.outlinks.map((o, i) => (
              <div key={i} className="link-card">
                <span>→</span>
                <span className="mono">{o.target}{o.heading ? `#${o.heading}` : ''}</span>
                {o.broken && <span className="broken-tag">broken</span>}
              </div>
            ))}
            {note.backlinks.map((b) => (
              <div key={b.path} className="link-card clickable" onClick={() => open(b.path)}>
                <span>←</span>
                <span className="mono">{b.title || b.path}</span>
              </div>
            ))}
            {note.linked_tasks.map((t) => (
              <div key={t.id} className="link-card">
                <span className="chip">{t.status}</span>
                <span>{t.title}</span>
              </div>
            ))}
            {note.broken_links.length > 0 && (
              <>
                <h4>Needs attention</h4>
                {note.broken_links.map((b, i) => (
                  <div key={i} className="link-card">⛔ <span className="mono">[[{b.target}]]</span></div>
                ))}
              </>
            )}
            {note.stale_readers.filter((s) => s.stale).length > 0 && (
              <div className="muted small" style={{ marginTop: 8 }}>
                ⚠ {note.stale_readers.filter((s) => s.stale).length} AI run(s) read an older version.
              </div>
            )}
            <div className="muted small" style={{ marginTop: 10 }}>
              Updated {new Date(note.updated_at).toLocaleString()}
            </div>
          </div>
        )}
      </div>

      {showNew && (
        <NewNoteModal
          initialQuery={results !== null ? q : ''}
          onClose={() => setShowNew(false)}
          onCreated={(path) => { setShowNew(false); refreshTree(); open(path) }}
          onError={setErr}
        />
      )}
      {showRename && note && (
        <RenameModal
          path={note.path}
          onClose={() => setShowRename(false)}
          onDone={(np) => { setShowRename(false); setSel(np); refreshTree(); open(np) }}
          onError={setErr}
        />
      )}
      {showDelete && note && (
        <div className="nm-back" onClick={() => setShowDelete(false)}>
          <div className="nm" onClick={(e) => e.stopPropagation()}>
            <h3>Delete note?</h3>
            <div className="mono muted small" style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{note.path}</div>
            <div className="hint">This moves the file out of the vault. Links pointing to it will break.</div>
            <div className="row">
              <button className="btn sm" onClick={() => setShowDelete(false)}>Cancel</button>
              <button
                className="btn sm danger" onClick={async () => {
                  try {
                    await notesApi.remove(note.path)
                    setShowDelete(false)
                    setNote(null); setSel(null); setDraft('')
                    refreshTree()
                  } catch (e: any) { setErr(String(e.message || e).slice(0, 300)); setShowDelete(false) }
                }}
              >
                <Trash2 size={13} /> Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function TreeNodes({ nodes, depth, sel, expanded, onToggle, onOpen }: {
  nodes: TreeNode[]; depth: number; sel: string | null
  expanded: Set<string>; onToggle: (p: string) => void; onOpen: (p: string) => void
}) {
  return (
    <>
      {nodes.map((n) => n.type === 'folder' ? (
        <div key={n.path || n.name}>
          <button className="notes-folder" style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => onToggle(n.path)}>
            <span style={{ display: 'inline-block', transition: 'transform .12s',
              transform: expanded.has(n.path) ? 'rotate(90deg)' : 'none', fontSize: 10 }}>▶</span>
            <span>📁</span><span>{n.name}</span>
            {n.children && <span className="muted" style={{ fontWeight: 400 }}> · {countNotes(n)}</span>}
          </button>
          {expanded.has(n.path) && n.children && (
            <TreeNodes nodes={n.children} depth={depth + 1} sel={sel}
              expanded={expanded} onToggle={onToggle} onOpen={onOpen} />
          )}
        </div>
      ) : (
        <button key={n.path} className={`notes-note ${sel === n.path ? 'active' : ''}`}
          style={{ marginLeft: depth * 14 }} onClick={() => onOpen(n.path)}>
          <FileText size={14} className="notes-icon" />
          <span style={{ minWidth: 0, flex: 1 }}>
            <span className="t" style={{ display: 'block' }}>{n.title || n.name}</span>
            <span className="p" style={{ display: 'block' }}>{n.path}</span>
          </span>
        </button>
      ))}
    </>
  )
}

function countNotes(n: TreeNode): number {
  if (n.type === 'note') return 1
  return (n.children || []).reduce((a, c) => a + countNotes(c), 0)
}

function stripSnippet(s: string): string {
  return s.replace(/<[^>]+>/g, '').slice(0, 160)
}

function NewNoteModal({ initialQuery, onClose, onCreated, onError }: {
  initialQuery: string; onClose: () => void; onCreated: (p: string) => void; onError: (s: string) => void
}) {
  const [name, setName] = useState(initialQuery.replace(/\.md$/, ''))
  const [folder, setFolder] = useState('')
  const [busy, setBusy] = useState(false)
  const full = [folder.trim().replace(/^\/+|\/+$/g, ''), name.trim().replace(/^\/+/, '')]
    .filter(Boolean).join('/') || name.trim()
  const path = full.endsWith('.md') || !full ? full : `${full}.md`

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      const title = name.trim().split('/').pop()!.replace(/\.md$/, '')
      await notesApi.create(path, `# ${title}\n\n`)
      onCreated(path)
    } catch (e: any) {
      onError(String(e.message || e).slice(0, 300))
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="nm-back" onClick={onClose}>
      <div className="nm" onClick={(e) => e.stopPropagation()}>
        <h3><Plus size={14} style={{ verticalAlign: -2 }} /> New note</h3>
        <label>Title / file name</label>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
          placeholder="e.g. Meeting notes" />
        <label>Folder <span className="muted" style={{ fontWeight: 400 }}>(optional)</span></label>
        <input value={folder} onChange={(e) => setFolder(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
          placeholder="e.g. ideas" />
        <div className="hint">Saves as <b style={{ fontFamily: 'var(--font-mono)' }}>{path || '…'}</b></div>
        <div className="row">
          <button className="btn sm" onClick={onClose}>Cancel</button>
          <button className="btn primary sm" disabled={!name.trim() || busy} onClick={submit}>
            {busy ? 'Creating…' : 'Create note'}
          </button>
        </div>
      </div>
    </div>
  )
}

function RenameModal({ path, onClose, onDone, onError }: {
  path: string; onClose: () => void; onDone: (p: string) => void; onError: (s: string) => void
}) {
  const [np, setNp] = useState(path)
  const [busy, setBusy] = useState(false)
  return (
    <div className="nm-back" onClick={onClose}>
      <div className="nm" onClick={(e) => e.stopPropagation()}>
        <h3>Rename / move note</h3>
        <label>New path</label>
        <input autoFocus value={np} onChange={(e) => setNp(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && np.trim() && np !== path) go() }} />
        <div className="hint">Links from other notes are updated automatically.</div>
        <div className="row">
          <button className="btn sm" onClick={onClose}>Cancel</button>
          <button className="btn primary sm" disabled={!np.trim() || np === path || busy}
            onClick={go}>Rename</button>
        </div>
      </div>
    </div>
  )

  async function go() {
    setBusy(true)
    try {
      await notesApi.move(path, np.trim())
      onDone(np.trim())
    } catch (e: any) {
      onError(String(e.message || e).slice(0, 300))
      onClose()
    } finally {
      setBusy(false)
    }
  }
}
