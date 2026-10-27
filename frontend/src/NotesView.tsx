import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft, Brain, Check, ClipboardPaste, Copy, Eye, FilePlus2, FileText, FolderPlus,
  Link2, PanelRight, Pencil, Plus, RefreshCw, Scissors, Search, Send, Sparkles, Trash2, X,
} from 'lucide-react'
import { Markdown, stripFrontmatter } from './markdown'
import { notesApi, TreeNode, VaultNote } from './notesApi'

interface Props {
  onOpenConversation?: (convId: string) => void
  initialPath?: string | null
}

type SaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error'
type Mode = 'edit' | 'preview' | 'split'
type RailTab = 'ai' | 'links'

const SAVE_LABEL: Record<SaveState, string> = {
  clean: 'Saved', dirty: 'Unsaved changes', saving: 'Saving…',
  saved: 'Saved', conflict: 'Conflict', error: 'Save failed',
}

interface Clip {
  op: 'copy' | 'cut'
  kind: 'note' | 'folder'
  src: string
}

interface CtxMenu {
  x: number
  y: number
  kind: 'note' | 'folder' | 'blank'
  path: string
  title?: string
}

interface InlineCreate {
  parentDir: string // '' = vault root
  mode: 'file' | 'folder'
}

export function NotesView({ initialPath }: Props) {
  const [tree, setTree] = useState<TreeNode[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [note, setNote] = useState<VaultNote | null>(null)
  const [draft, setDraft] = useState('') // body only — frontmatter hidden, id preserved server-side
  const [mode, setMode] = useState<Mode>('edit')
  const [saveState, setSaveState] = useState<SaveState>('clean')
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<any[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(['']))
  const [showNew, setShowNew] = useState(false)
  const [renameTarget, setRenameTarget] = useState<{ path: string; kind: 'note' | 'folder' } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<{ path: string; kind: 'note' | 'folder' } | null>(null)
  const [loading, setLoading] = useState(false)
  const [clip, setClip] = useState<Clip | null>(null)
  const [ctx, setCtx] = useState<CtxMenu | null>(null)
  const [inline, setInline] = useState<InlineCreate | null>(null)
  const [railTab, setRailTab] = useState<RailTab>('ai')
  const [railOpen, setRailOpen] = useState(() => {
    try { return localStorage.getItem('notes.railOpen') !== '0' } catch { return true }
  })
  const [railW, setRailW] = useState(() => {
    try { return Number(localStorage.getItem('notes.railW')) || 300 } catch { return 300 }
  })
  const timer = useRef<number | null>(null)
  const baseRef = useRef<{ version: number; hash: string }>({ version: 0, hash: '' })

  const refreshTree = useCallback(async () => {
    try {
      const t = await notesApi.tree()
      setTree(t.tree)
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
      setDraft(stripFrontmatter(n.content || ''))
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

  // close context menu on outside click / escape
  useEffect(() => {
    if (!ctx) return
    const close = () => setCtx(null)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setCtx(null) }
    window.addEventListener('click', close)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('click', close); window.removeEventListener('keydown', onKey) }
  }, [ctx])

  const dirty = note != null && draft !== stripFrontmatter(note.content || '')

  const saveNow = useCallback(async (content: string) => {
    if (!sel || !note) return
    setSaveState('saving')
    try {
      const n = await notesApi.update(sel, content, baseRef.current.version, baseRef.current.hash)
      setNote(n)
      setDraft(stripFrontmatter(n.content || ''))
      baseRef.current = { version: n.version, hash: n.content_hash }
      setSaveState('saved')
      refreshTree()
    } catch (e: any) {
      const msg = String(e.message || e)
      setSaveState(msg.includes('409') ? 'conflict' : 'error')
      setErr(msg.slice(0, 400))
    }
  }, [sel, note, refreshTree])

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

  const parentDirOf = (p: string) => {
    const i = p.lastIndexOf('/')
    return i < 0 ? '' : p.slice(0, i)
  }

  const uniqueDest = (dir: string, base: string): string => {
    const join = (d: string, b: string) => (d ? `${d}/${b}` : b)
    if (!existsInTree(tree, join(dir, base))) return join(dir, base)
    const dot = base.lastIndexOf('.')
    const stem = dot > 0 ? base.slice(0, dot) : base
    const ext = dot > 0 ? base.slice(dot) : ''
    for (let i = 2; i < 50; i++) {
      const cand = join(dir, `${stem} copy ${i}${ext}`)
      if (!existsInTree(tree, cand)) return cand
    }
    return join(dir, `${stem} copy ${Date.now()}${ext}`)
  }

  const doPaste = useCallback(async (destDir: string) => {
    if (!clip) return
    setErr('')
    try {
      const base = clip.src.split('/').pop() || 'untitled.md'
      if (clip.kind === 'note') {
        const dst = uniqueDest(destDir, base.endsWith('.md') ? base : `${base}.md`)
        if (clip.op === 'copy') await notesApi.copyNote(clip.src, dst)
        else {
          if (parentDirOf(clip.src) !== destDir) await notesApi.move(clip.src, dst)
          if (sel === clip.src) setSel(dst)
        }
        if (clip.op === 'cut') setClip(null)
        await refreshTree()
        if (clip.op === 'copy') open(dst)
      } else {
        const dst = uniqueDest(destDir, base)
        if (clip.op === 'copy') await notesApi.copyFolder(clip.src, dst)
        else {
          if (parentDirOf(clip.src) !== destDir && dst !== clip.src) {
            await notesApi.moveFolder(clip.src, dst)
          }
        }
        if (clip.op === 'cut') setClip(null)
        await refreshTree()
      }
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }, [clip, tree, sel, refreshTree, open])

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

  const startRailDrag = (e: React.MouseEvent) => {
    e.preventDefault()
    const x0 = e.clientX, w0 = railW
    let w = w0
    const mv = (ev: MouseEvent) => {
      w = Math.min(560, Math.max(232, w0 + (x0 - ev.clientX)))
      setRailW(w)
    }
    const up = () => {
      window.removeEventListener('mousemove', mv)
      window.removeEventListener('mouseup', up)
      try { localStorage.setItem('notes.railW', String(Math.round(w))) } catch { /* ignore */ }
    }
    window.addEventListener('mousemove', mv)
    window.addEventListener('mouseup', up)
  }

  const openWikilink = useCallback((target: string) => {
    const clean = target.split('#')[0].trim()
    if (!clean) return
    const withExt = clean.toLowerCase().endsWith('.md') ? clean : `${clean}.md`
    const candidates: string[] = []
    if (sel) {
      const dir = parentDirOf(sel)
      if (dir) candidates.push(`${dir}/${withExt}`)
    }
    candidates.push(withExt)
    // case-insensitive / suffix match against known notes
    const all = flattenNotes(tree)
    const norm = (s: string) => s.toLowerCase()
    for (const c of candidates) {
      const hit = all.find((p) => norm(p) === norm(c))
      if (hit) { open(hit); return }
    }
    const base = clean.split('/').pop() || clean
    const hit = all.find((p) => norm(p).endsWith('/' + norm(base) + '.md') || norm(p) === norm(base) + '.md')
    if (hit) { open(hit); return }
    // doesn't exist yet — offer to create it next to current note
    const dir = sel ? parentDirOf(sel) : ''
    const rel = dir ? `${dir}/${withExt}` : withExt
    if (confirm(`Create note "${rel}"?`)) {
      notesApi.create(rel, `# ${base}\n\n`).then(() => { refreshTree(); open(rel) })
        .catch((e: any) => setErr(String(e.message || e).slice(0, 300)))
    }
  }, [sel, tree, open, refreshTree])

  return (
    <div className="notes">
      <div className="notes-head">
        <h2><FileText size={17} /> Notes</h2>
        <span className="sub">{noteCount} note{noteCount === 1 ? '' : 's'}</span>
        {clip && (
          <span className="clip-pill" title={clip.src}>
            <Scissors size={11} /> {clip.op === 'copy' ? 'copying' : 'moving'}: {clip.src.split('/').pop()}
            <button className="icon-btn" style={{ padding: 1 }} onClick={() => setClip(null)} title="Clear clipboard">
              <X size={12} />
            </button>
          </span>
        )}
        <span className="spacer" />
        <button className="mini-btn" title="Refresh tree" onClick={refreshTree}>
          <RefreshCw size={12} style={{ verticalAlign: -2 }} /> Refresh
        </button>
        <button className="mini-btn" title={railOpen ? 'Hide assistant panel' : 'Show assistant panel'}
          onClick={() => {
            setRailOpen((v) => {
              try { localStorage.setItem('notes.railOpen', v ? '0' : '1') } catch { /* ignore */ }
              return !v
            })
          }}>
          <PanelRight size={12} style={{ verticalAlign: -2 }} /> {railOpen ? 'Hide AI' : 'Show AI'}
        </button>
        <button className="btn primary sm" onClick={() => setShowNew(true)}>
          <Plus size={14} /> New note
        </button>
      </div>

      <div className="notes-body">
        {/* ------- explorer ------- */}
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
          </div>

          <div className="notes-tree" onContextMenu={(e) => {
            e.preventDefault()
            setCtx({ x: e.clientX, y: e.clientY, kind: 'blank', path: '' })
          }}>
            <div className="explorer-label">Explorer</div>
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
              <>
                {inline && inline.parentDir === '' && (
                  <InlineCreateInput
                    mode={inline.mode}
                    onCancel={() => setInline(null)}
                    onCommit={(rel) => commitInlineCreate('', rel, inline.mode, setInline, refreshTree, open, setErr)}
                  />
                )}
                <TreeNodes nodes={tree} depth={0} sel={sel} expanded={expanded}
                  inlineParent={inline?.parentDir ?? null} inlineMode={inline?.mode ?? 'file'}
                  onToggle={toggleFolder} onOpen={open}
                  onContext={(c) => setCtx(c)}
                  onHoverNew={(dir, m) => setInline({ parentDir: dir, mode: m })}
                  onInlineCancel={() => setInline(null)}
                  onInlineCommit={(dir, rel, m) => commitInlineCreate(dir, rel, m, setInline, refreshTree, open, setErr)}
                  onRename={(p, k) => setRenameTarget({ path: p, kind: k })}
                  onDelete={(p, k) => setDeleteTarget({ path: p, kind: k })}
                  onCopy={(c) => setClip(c)}
                />
              </>
            )}
            {!results && tree.length === 0 && !inline && (
              <div className="muted pad">No notes yet.<br />Right-click here → New file, or use + Note.</div>
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
                  <div className="title-row">
                    <span className="title">{note.title || note.path.split('/').pop()}</span>
                    {note.id && <span className="id-chip" title={`Stable note id: ${note.id}`}>{shortId(note.id)}</span>}
                  </div>
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
                  <button className="mini-btn" onClick={() => setRenameTarget({ path: note.path, kind: 'note' })}>Rename</button>
                  <button className="mini-btn" title="Delete note" onClick={() => setDeleteTarget({ path: note.path, kind: 'note' })}>
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>

              {saveState === 'conflict' && (
                <div className="errorbox notes-conflict">
                  This note changed elsewhere — autosave paused to avoid overwriting it.
                  <button className="btn sm" onClick={() => sel && open(sel)}>Reload latest</button>
                </div>
              )}
              {err && <div className="errorbox notes-conflict">{err}</div>}

              <div className="notes-editor-wrap">
                <div className="notes-editor">
                  {(mode === 'edit' || mode === 'split') && (
                    <div className="notes-splitpane">
                      {mode === 'split' && <div className="notes-pane-label">Write — frontmatter hidden, id kept automatically</div>}
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
                        <Markdown text={draft || '*Nothing to preview yet.*'} onOpenNote={openWikilink} />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* ------- right rail: AI chat + links (collapsible, resizable) ------- */}
        {railOpen && (
        <div className="notes-context" style={{ width: railW, minWidth: railW, maxWidth: railW }}>
          <div className="rail-drag" onMouseDown={startRailDrag} title="Drag to resize" />
          <button className="rail-collapse" title="Hide panel" onClick={() => {
            setRailOpen(false)
            try { localStorage.setItem('notes.railOpen', '0') } catch { /* ignore */ }
          }}>→</button>
          <div className="rail-tabs">
            <button className={railTab === 'ai' ? 'sel' : ''} onClick={() => setRailTab('ai')}>
              <Sparkles size={12} /> Ask notes
            </button>
            <button className={railTab === 'links' ? 'sel' : ''} onClick={() => setRailTab('links')}>
              <Link2 size={12} /> Links
            </button>
          </div>
          {railTab === 'ai' ? (
            <NotesAI onOpen={open} onMutated={(touched, opened) => {
              refreshTree()
              if (opened) {
                open(opened)
              } else if (sel && touched.includes(sel)) {
                // the open note was deleted — close the editor
                setNote(null); setSel(null); setDraft('')
              }
            }} />
          ) : note ? (
            <LinksPane note={note} onOpen={open} />
          ) : (
            <div className="muted small">Open a note to see its connections.</div>
          )}
        </div>
        )}
      </div>

      {ctx && (
        <ExplorerMenu ctx={ctx} clip={clip}
          onClose={() => setCtx(null)}
          onNew={(dir, m) => { setInline({ parentDir: dir, mode: m }); setCtx(null) }}
          onRename={(p, k) => { setRenameTarget({ path: p, kind: k }); setCtx(null) }}
          onDelete={(p, k) => { setDeleteTarget({ path: p, kind: k }); setCtx(null) }}
          onCopy={(c) => { setClip(c); setCtx(null) }}
          onPaste={(dir) => { setCtx(null); doPaste(dir) }}
          onRefresh={() => { setCtx(null); refreshTree() }}
          onOpen={(p) => { setCtx(null); open(p) }}
          onError={setErr}
        />
      )}

      {showNew && (
        <NewNoteModal
          initialQuery={results !== null ? q : ''}
          onClose={() => setShowNew(false)}
          onCreated={(path) => { setShowNew(false); refreshTree(); open(path) }}
          onError={setErr}
        />
      )}
      {renameTarget && (
        <RenameModal
          path={renameTarget.path} kind={renameTarget.kind}
          onClose={() => setRenameTarget(null)}
          onDone={(np) => {
            setRenameTarget(null)
            if (renameTarget.kind === 'note' && sel === renameTarget.path) setSel(np)
            refreshTree()
            if (renameTarget.kind === 'note') open(np)
          }}
          onError={setErr}
        />
      )}
      {deleteTarget && (
        <div className="nm-back" onClick={() => setDeleteTarget(null)}>
          <div className="nm" onClick={(e) => e.stopPropagation()}>
            <h3>Delete {deleteTarget.kind}?</h3>
            <div className="mono muted small" style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{deleteTarget.path}</div>
            <div className="hint">
              {deleteTarget.kind === 'folder'
                ? 'Deletes the folder and every note inside it. This cannot be undone.'
                : 'This moves the file out of the vault. Links pointing to it will break.'}
            </div>
            <div className="row">
              <button className="btn sm" onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button
                className="btn sm danger" onClick={async () => {
                  const t = deleteTarget
                  try {
                    if (t.kind === 'folder') await notesApi.removeFolder(t.path)
                    else await notesApi.remove(t.path)
                    if (t.kind === 'note' && sel === t.path) {
                      setNote(null); setSel(null); setDraft('')
                    }
                    setDeleteTarget(null)
                    refreshTree()
                  } catch (e: any) { setErr(String(e.message || e).slice(0, 300)); setDeleteTarget(null) }
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

/* ---------------- explorer tree ---------------- */

function TreeNodes({ nodes, depth, sel, expanded, inlineParent, inlineMode, onToggle, onOpen, onContext, onHoverNew, onInlineCancel, onInlineCommit, onRename, onDelete, onCopy }: {
  nodes: TreeNode[]; depth: number; sel: string | null
  expanded: Set<string>; inlineParent: string | null; inlineMode: 'file' | 'folder'
  onToggle: (p: string) => void; onOpen: (p: string) => void
  onContext: (c: CtxMenu) => void
  onHoverNew: (dir: string, mode: 'file' | 'folder') => void
  onInlineCancel: () => void
  onInlineCommit: (dir: string, rel: string, mode: 'file' | 'folder') => void
  onRename: (p: string, k: 'note' | 'folder') => void
  onDelete: (p: string, k: 'note' | 'folder') => void
  onCopy: (c: Clip) => void
}) {
  const [hover, setHover] = useState<string | null>(null)
  return (
    <>
      {nodes.map((n) => n.type === 'folder' ? (
        <div key={n.path || n.name}>
          <div className="notes-folderrow"
            style={{ paddingLeft: 8 + depth * 14 }}
            onMouseEnter={() => setHover(n.path)}
            onMouseLeave={() => setHover((h) => (h === n.path ? null : h))}
            onClick={() => onToggle(n.path)}
            onContextMenu={(e) => {
              e.preventDefault(); e.stopPropagation()
              onContext({ x: e.clientX, y: e.clientY, kind: 'folder', path: n.path, title: n.name })
            }}>
            <span style={{ display: 'inline-block', transition: 'transform .12s',
              transform: expanded.has(n.path) ? 'rotate(90deg)' : 'none', fontSize: 10 }}>▶</span>
            <span>📁</span><span className="fname">{n.name}</span>
            {n.children && <span className="muted" style={{ fontWeight: 400 }}> · {countNotes(n)}</span>}
            <span className="spacer" />
            {hover === n.path && (
              <span className="hover-new" onClick={(e) => e.stopPropagation()}>
                <button title={`New file in ${n.path}`} onClick={() => onHoverNew(n.path, 'file')}>
                  <FilePlus2 size={13} />
                </button>
                <button title={`New folder in ${n.path}`} onClick={() => onHoverNew(n.path, 'folder')}>
                  <FolderPlus size={13} />
                </button>
              </span>
            )}
          </div>
          {inlineParent === n.path && (
            <div style={{ paddingLeft: 8 + (depth + 1) * 14 }}>
              <InlineCreateInput mode={inlineMode}
                onCancel={onInlineCancel}
                onCommit={(rel) => onInlineCommit(n.path, rel, inlineMode)} />
            </div>
          )}
          {expanded.has(n.path) && n.children && (
            <TreeNodes nodes={n.children} depth={depth + 1} sel={sel}
              expanded={expanded} inlineParent={inlineParent} inlineMode={inlineMode}
              onToggle={onToggle} onOpen={onOpen} onContext={onContext}
              onHoverNew={onHoverNew} onInlineCancel={onInlineCancel}
              onInlineCommit={onInlineCommit} onRename={onRename}
              onDelete={onDelete} onCopy={onCopy} />
          )}
        </div>
      ) : (
        <button key={n.path} className={`notes-note ${sel === n.path ? 'active' : ''}`}
          style={{ marginLeft: depth * 14 }} onClick={() => onOpen(n.path)}
          onContextMenu={(e) => {
            e.preventDefault(); e.stopPropagation()
            onContext({ x: e.clientX, y: e.clientY, kind: 'note', path: n.path, title: n.title || n.name })
          }}>
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

function InlineCreateInput({ mode, onCancel, onCommit }: {
  mode: 'file' | 'folder'; onCancel: () => void; onCommit: (rel: string) => void
}) {
  const [val, setVal] = useState('')
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { ref.current?.focus() }, [])
  return (
    <div className="inline-create">
      <span className="muted" style={{ fontSize: 12 }}>{mode === 'file' ? '📄' : '📁'}</span>
      <input ref={ref} value={val} onChange={(e) => setVal(e.target.value)}
        placeholder={mode === 'file' ? 'test.md or test/test.md' : 'folder name or a/b/c'}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && val.trim()) onCommit(val.trim())
          if (e.key === 'Escape') onCancel()
        }}
        onBlur={() => { if (!val.trim()) onCancel() }} />
    </div>
  )
}

/** IDE-style nested create: `test.md` → file here, `test/test.md` → folder + file. */
async function commitInlineCreate(
  parentDir: string, rel: string, mode: 'file' | 'folder',
  setInline: (v: InlineCreate | null) => void,
  refreshTree: () => Promise<void>, open: (p: string) => void,
  onError: (s: string) => void,
) {
  setInline(null)
  const clean = rel.replace(/\\/g, '/').replace(/^\/+/, '').trim()
  if (!clean) return
  try {
    if (mode === 'folder' || clean.endsWith('/')) {
      const dir = joinRel(parentDir, clean.replace(/\/+$/, ''))
      await notesApi.mkdir(dir)
      await refreshTree()
      return
    }
    // file mode: allow `a/b/c.md` or `a/b/c` (→ c.md); intermediate dirs auto-created
    const full = joinRel(parentDir, clean)
    const path = full.toLowerCase().endsWith('.md') ? full : `${full}.md`
    const title = path.split('/').pop()!.replace(/\.md$/, '')
    await notesApi.create(path, `# ${title}\n\n`)
    await refreshTree()
    open(path)
  } catch (e: any) {
    onError(String(e.message || e).slice(0, 300))
  }
}

function joinRel(dir: string, rel: string) {
  const r = rel.replace(/^\/+|\/+$/g, '')
  return dir ? `${dir}/${r}` : r
}

/* ---------------- right-click menu ---------------- */

function ExplorerMenu({ ctx, clip, onClose, onNew, onRename, onDelete, onCopy, onPaste, onRefresh, onOpen, onError }: {
  ctx: CtxMenu; clip: Clip | null
  onClose: () => void
  onNew: (dir: string, mode: 'file' | 'folder') => void
  onRename: (p: string, k: 'note' | 'folder') => void
  onDelete: (p: string, k: 'note' | 'folder') => void
  onCopy: (c: Clip) => void
  onPaste: (dir: string) => void
  onRefresh: () => void
  onOpen: (p: string) => void
  onError: (s: string) => void
}) {
  const dirOf = ctx.kind === 'folder' ? ctx.path : ctx.path.includes('/') ? ctx.path.slice(0, ctx.path.lastIndexOf('/')) : ''
  const items: { label: string; icon?: React.ReactNode; danger?: boolean; disabled?: boolean; run: () => void }[] = []
  if (ctx.kind === 'note') {
    items.push(
      { label: 'Open', run: () => onOpen(ctx.path) },
      { label: 'Rename / Move…', run: () => onRename(ctx.path, 'note') },
      { label: 'Duplicate', icon: <Copy size={13} />, run: async () => {
        try {
          const base = ctx.path.split('/').pop() || 'note.md'
          const dot = base.lastIndexOf('.')
          const dup = `${base.slice(0, dot)} copy${base.slice(dot)}`
          const dst = dirOf ? `${dirOf}/${dup}` : dup
          const n = await notesApi.copyNote(ctx.path, dst)
          onOpen(n.path)
        } catch (e: any) { onError(String(e.message || e).slice(0, 300)) }
      } },
      { label: 'Copy', icon: <Copy size={13} />, run: () => onCopy({ op: 'copy', kind: 'note', src: ctx.path }) },
      { label: 'Cut', icon: <Scissors size={13} />, run: () => onCopy({ op: 'cut', kind: 'note', src: ctx.path }) },
      { label: 'Copy path', run: () => { navigator.clipboard.writeText(ctx.path).catch(() => {}) } },
      { label: 'Delete', icon: <Trash2 size={13} />, danger: true, run: () => onDelete(ctx.path, 'note') },
    )
  } else if (ctx.kind === 'folder') {
    items.push(
      { label: 'New file here', icon: <FilePlus2 size={13} />, run: () => onNew(ctx.path, 'file') },
      { label: 'New folder here', icon: <FolderPlus size={13} />, run: () => onNew(ctx.path, 'folder') },
      { label: 'Rename / Move…', run: () => onRename(ctx.path, 'folder') },
      { label: 'Copy', icon: <Copy size={13} />, run: () => onCopy({ op: 'copy', kind: 'folder', src: ctx.path }) },
      { label: 'Cut', icon: <Scissors size={13} />, run: () => onCopy({ op: 'cut', kind: 'folder', src: ctx.path }) },
      { label: 'Paste here', icon: <ClipboardPaste size={13} />, disabled: !clip, run: () => onPaste(ctx.path) },
      { label: 'Copy path', run: () => { navigator.clipboard.writeText(ctx.path).catch(() => {}) } },
      { label: 'Delete folder', icon: <Trash2 size={13} />, danger: true, run: () => onDelete(ctx.path, 'folder') },
    )
  } else {
    items.push(
      { label: 'New file', icon: <FilePlus2 size={13} />, run: () => onNew('', 'file') },
      { label: 'New folder', icon: <FolderPlus size={13} />, run: () => onNew('', 'folder') },
      { label: 'Paste here', icon: <ClipboardPaste size={13} />, disabled: !clip, run: () => onPaste('') },
      { label: 'Refresh', icon: <RefreshCw size={13} />, run: onRefresh },
    )
  }
  const style: React.CSSProperties = {
    left: Math.min(ctx.x, window.innerWidth - 220),
    top: Math.min(ctx.y, window.innerHeight - items.length * 34 - 20),
  }
  return (
    <div className="ctxmenu" style={style} onClick={(e) => e.stopPropagation()}>
      {ctx.title && <div className="ctxmenu-title">{ctx.title}</div>}
      {items.map((it, i) => (
        <button key={i} className={`ctxmenu-item ${it.danger ? 'danger' : ''}`}
          disabled={it.disabled} onClick={() => { if (!it.disabled) { onClose(); it.run() } }}>
          {it.icon}<span>{it.label}</span>
        </button>
      ))}
    </div>
  )
}

/* ---------------- right rail: AI chat over notes ---------------- */

interface ChatMsg {
  id: number
  role: 'user' | 'assistant'
  text: string
  cites: { path: string; title: string; snippet: string }[]
  touched: string[]
  trace: { tool: string; ok: boolean }[]
  fallback?: boolean
}

const AI_SUGGESTIONS = [
  'Create a note called Atlas for tracking features and bugs',
  'What do my notes say about pricing?',
  'Summarize my open ideas',
]

function NotesAI({ onOpen, onMutated }: {
  onOpen: (p: string) => void
  onMutated: (touched: string[], opened: string | null) => void
}) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([])
  const [qq, setQq] = useState('')
  const [busy, setBusy] = useState(false)
  const [semantic, setSemantic] = useState(false)
  const [err, setErr] = useState('')
  const idRef = useRef(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs, busy])

  const send = async (text?: string) => {
    const query = (text ?? qq).trim()
    if (!query || busy) return
    setQq('')
    setErr('')
    if (taRef.current) taRef.current.style.height = 'auto'
    const history = msgs.slice(-8).map((m) => ({ role: m.role, content: m.text.slice(0, 1200) }))
    const um: ChatMsg = { id: idRef.current++, role: 'user', text: query, cites: [], touched: [], trace: [] }
    setMsgs((prev) => [...prev, um])
    setBusy(true)
    try {
      const r = await notesApi.ask(query, 6, history)
      setSemantic(r.semantic)
      setMsgs((prev) => [...prev, {
        id: idRef.current++, role: 'assistant', text: r.answer,
        cites: r.citations, touched: r.touched || [], trace: r.trace || [], fallback: r.fallback,
      }])
      if ((r.touched && r.touched.length > 0) || r.opened) onMutated(r.touched || [], r.opened || null)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 250))
    } finally {
      setBusy(false)
    }
  }

  const clear = () => {
    setMsgs([])
    setErr('')
  }

  return (
    <div className="notes-ai-chat">
      <div className="ai-chat-head">
        <span className="ai-chat-title"><Brain size={13} /> Notes AI</span>
        <span className={`sem-badge ${semantic ? 'on' : ''}`}
          title={semantic ? 'Embedding vectors used' : 'Keyword + token-overlap ranking (embeddings offline)'}>
          {semantic ? '● semantic' : '○ lexical'}
        </span>
        <span className="spacer" />
        {msgs.length > 0 && (
          <button className="mini-btn" title="Start a new chat" onClick={clear}>New chat</button>
        )}
      </div>

      <div className="ai-chat" ref={scrollRef}>
        {msgs.length === 0 && !busy && (
          <div className="ai-welcome">
            <Sparkles size={20} color="var(--accent)" />
            <div className="ai-welcome-t">Ask anything about your notes</div>
            <div className="muted small">Semantic search finds related notes even when the words differ. Click any cited note to open it. Follow-ups keep context.</div>
            <div className="ai-sugg">
              {AI_SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>“{s}”</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m) => m.role === 'user' ? (
          <div key={m.id} className="ai-msg user">
            <div className="ai-bubble">{m.text}</div>
          </div>
        ) : (
          <div key={m.id} className="ai-msg assistant">
            <div className="ai-answer">
              <Markdown text={m.text} onOpenNote={onOpen} />
            </div>
            {m.trace.length > 0 && (
              <div className="ai-trace muted small" title="Tool calls this turn">
                {m.trace.map((t, i) => (
                  <span key={i} className={t.ok ? 'tok' : 'tfail'}>
                    {t.ok ? '✓' : '✗'} {t.tool.replace(/^(note|folder|task|reminder|bulk)_/, '')}
                  </span>
                ))}
              </div>
            )}
            {m.touched.length > 0 && (
              <div className="ai-touched">
                <span className="muted small">⚡ touched:</span>
                {m.touched.map((p) => (
                  <button key={p} className="touch-chip" title={p} onClick={() => onOpen(p)}>
                    {p.split('/').pop()}
                  </button>
                ))}
              </div>
            )}
            {m.cites.length > 0 && (
              <div className="ai-hits">
                {m.cites.map((h) => (
                  <button key={h.path} className="ai-hit" onClick={() => onOpen(h.path)}>
                    <div className="t">{h.title || h.path}</div>
                    <div className="p">{h.path}</div>
                    {h.snippet && <div className="s">{h.snippet.slice(0, 180)}</div>}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="ai-msg assistant">
            <div className="ai-bubble thinking-bubble">
              <span className="thinking" aria-hidden="true"><span /><span /><span /></span>
              <span className="muted small">Searching notes…</span>
            </div>
          </div>
        )}
      </div>

      {err && <div className="errorbox sm">{err}</div>}

      <div className="ai-composer">
        <textarea ref={taRef} value={qq}
          onChange={(e) => {
            setQq(e.target.value)
            const el = e.target
            el.style.height = 'auto'
            el.style.height = Math.min(el.scrollHeight, 130) + 'px'
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
          }}
          placeholder="Ask about your notes…  (Enter sends)"
          rows={1} />
        <button className="btn primary sm ai-send" disabled={!qq.trim() || busy}
          onClick={() => send()} title="Send">
          <Send size={14} />
        </button>
      </div>
      <div className="muted small" style={{ textAlign: 'center' }}>Enter ↵ send · Shift+Enter newline</div>
    </div>
  )
}

/* ---------------- right rail: links ---------------- */

function LinksPane({ note, onOpen }: { note: VaultNote; onOpen: (p: string) => void }) {
  return (
    <div className="links-pane">
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
        <div key={b.path} className="link-card clickable" onClick={() => onOpen(b.path)}>
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
      <div className="muted small" style={{ marginTop: 10 }}>
        Updated {new Date(note.updated_at).toLocaleString()}
      </div>
    </div>
  )
}

/* ---------------- helpers ---------------- */

function countNotes(n: TreeNode): number {
  if (n.type === 'note') return 1
  return (n.children || []).reduce((a, c) => a + countNotes(c), 0)
}

function flattenNotes(ns: TreeNode[]): string[] {
  const out: string[] = []
  const walk = (arr: TreeNode[]) => {
    for (const n of arr) {
      if (n.type === 'note') out.push(n.path)
      else if (n.children) walk(n.children)
    }
  }
  walk(ns)
  return out
}

function existsInTree(tree: TreeNode[], path: string): boolean {
  return flattenNotes(tree).some((p) => p.toLowerCase() === path.toLowerCase())
}

function shortId(id: string) {
  return id.length > 14 ? `${id.slice(0, 10)}…${id.slice(-4)}` : id
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
        <label>Title / file name <span className="muted" style={{ fontWeight: 400 }}>(tip: “ideas/upsell.md” creates folders)</span></label>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
          placeholder="e.g. Meeting notes or ideas/upsell" />
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

function RenameModal({ path, kind, onClose, onDone, onError }: {
  path: string; kind: 'note' | 'folder'; onClose: () => void; onDone: (p: string) => void; onError: (s: string) => void
}) {
  const [np, setNp] = useState(path)
  const [busy, setBusy] = useState(false)
  return (
    <div className="nm-back" onClick={onClose}>
      <div className="nm" onClick={(e) => e.stopPropagation()}>
        <h3>Rename / move {kind}</h3>
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
      if (kind === 'folder') await notesApi.moveFolder(path, np.trim())
      else await notesApi.move(path, np.trim())
      onDone(np.trim())
    } catch (e: any) {
      onError(String(e.message || e).slice(0, 300))
      onClose()
    } finally {
      setBusy(false)
    }
  }
}
