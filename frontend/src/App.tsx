import {
  AlertTriangle, Brain, Check, ChevronDown, CircleStop, FileDiff, Loader2, MessageSquarePlus,
  Mic, Pause, Play, RotateCcw, Send, Settings as SettingsIcon, PanelRight, Search,
  SquarePen, Trash2, Wrench, X,
} from 'lucide-react'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api, ChatMessage } from './api'
import { Markdown } from './markdown'
import { MemoryFilePanel } from './MemoryFiles'
import { MemoryView } from './MemoryView'
import { useStore } from './store'
import { ToolCard } from './ToolCard'
import { useExecutionStream } from './useStream'

export default function App() {
  const [view, setView] = useState<'chat' | 'memory'>('chat')
  const activeId = useStore((s) => s.activeId)
  const loadConversations = useStore((s) => s.loadConversations)
  const loadModels = useStore((s) => s.loadModels)
  const checkHealth = useStore((s) => s.checkHealth)
  const keyConfigured = useStore((s) => s.keyConfigured)
  const settingsOpen = useStore((s) => s.settingsOpen)

  useEffect(() => {
    loadConversations()
    loadModels()
    checkHealth()
  }, [loadConversations, loadModels, checkHealth])

  return (
    <div className="app">
      <Sidebar onMemory={() => setView('memory')} inMemory={view === 'memory'} />
      <main className="main">
        {view === 'memory' ? (
          <MemoryView onClose={() => setView('chat')} />
        ) : activeId ? (
          <ChatView key={activeId} convId={activeId} />
        ) : (
          <EmptyState />
        )}
      </main>
      {settingsOpen && <SettingsModal />}
      {keyConfigured === false && <KeyBanner />}
    </div>
  )
}

function EmptyState() {
  const createConversation = useStore((s) => s.createConversation)
  return (
    <div className="empty">
      <div className="empty-card">
        <h1>Atlas</h1>
        <p>An agentic coding environment. Start a conversation, pick a model, and give it a task.</p>
        <button className="btn primary" onClick={() => createConversation()}>
          <MessageSquarePlus size={16} /> New conversation
        </button>
      </div>
    </div>
  )
}

function KeyBanner() {
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  return (
    <div className="keybanner">
      <AlertTriangle size={15} />
      <span>No OpenCode Go API key configured. Live models are unavailable — local Ollama models still work.</span>
      <button className="mini-btn" onClick={() => setSettingsOpen(true)}>Setup</button>
    </div>
  )
}

/* ---------------- Sidebar ---------------- */

function Sidebar({ onMemory, inMemory }: { onMemory: () => void; inMemory: boolean }) {
  const conversations = useStore((s) => s.conversations)
  const activeId = useStore((s) => s.activeId)
  const selectConversation = useStore((s) => s.selectConversation)
  const createConversation = useStore((s) => s.createConversation)
  const deleteConversation = useStore((s) => s.deleteConversation)
  const search = useStore((s) => s.search)
  const setSearch = useStore((s) => s.setSearch)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const setSidebar = useStore((s) => s.setSidebar)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return conversations
    return conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [conversations, search])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        document.getElementById('conv-search')?.focus()
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault()
        createConversation()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [createConversation])

  if (!sidebarOpen) {
    return (
      <button className="rail-btn" onClick={() => setSidebar(true)} title="Open sidebar">☰</button>
    )
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">A</span>
        <span className="brand-name">Atlas</span>
        <button className="icon-btn" onClick={() => setSidebar(false)} title="Close sidebar">☰</button>
      </div>
      <button className="btn newchat" onClick={() => createConversation()}>
        <SquarePen size={15} /> New chat
      </button>
      <button className={`btn newchat ${inMemory ? 'active' : ''}`} onClick={onMemory}>
        <Brain size={15} /> Memory
      </button>
      <div className="searchbox">
        <Search size={14} />
        <input
          id="conv-search"
          placeholder="Search conversations  (Ctrl+K)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="convlist">
        {filtered.map((c) => (
          <div
            key={c.id}
            className={`conv ${c.id === activeId ? 'active' : ''}`}
            onClick={() => selectConversation(c.id)}
          >
            <div className="conv-title">{c.title || 'New conversation'}</div>
            <div className="conv-meta">
              {c.active_execution && c.active_execution.status !== 'completed' ? (
                <span className="dot live" title={c.active_execution.status} />
              ) : (
                <span className="dot idle" />
              )}
              <span className="conv-model">{shortModel(c.model)}</span>
            </div>
            <button
              className="icon-btn danger"
              title="Delete"
              onClick={(e) => {
                e.stopPropagation()
                if (confirm('Delete this conversation?')) deleteConversation(c.id)
              }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {filtered.length === 0 && <div className="muted pad">No conversations.</div>}
      </div>
      <div className="sidebar-foot">
        <button className="btn ghost" onClick={() => setSettingsOpen(true)}>
          <SettingsIcon size={15} /> Settings
        </button>
      </div>
    </aside>
  )
}

function shortModel(id: string) {
  if (!id) return 'no model'
  const base = id.includes('/') ? id.split('/').slice(-1)[0] : id
  return base.length > 26 ? base.slice(0, 26) + '…' : base
}

/* ---------------- Chat view ---------------- */

function ChatView({ convId }: { convId: string }) {
  const chat = useStore((s) => s.chats[convId])
  const conv = useStore((s) => s.conversations.find((c) => c.id === convId))
  const executionId = chat?.execution?.id || null
  useExecutionStream(convId, executionId)

  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const sendMessage = useStore((s) => s.sendMessage)
  const listRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  const messages = chat?.messages || []
  const stream = chat?.stream || null

  useEffect(() => {
    if (stickRef.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages.length, stream?.text, stream?.tools.length, stream?.status])

  const onScroll = () => {
    const el = listRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }

  const send = async () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    setSending(true)
    stickRef.current = true
    try {
      // Optimistic user message.
      useStore.setState((s) => ({
        chats: {
          ...s.chats,
          [convId]: {
            ...(s.chats[convId] || { messages: [], stream: null, execution: null, scrollKey: 0, completedAt: null }),
            messages: [...(s.chats[convId]?.messages || []),
              { id: `local-${Date.now()}`, role: 'user', content: text,
                model: '', execution_id: '', status: 'done', created_at: Date.now() } as ChatMessage],
          },
        },
      }))
      await sendMessage(convId, text)
      // Refresh persisted history (replaces optimistic row).
      const { messages } = await api.listMessages(convId)
      useStore.setState((s) => ({
        chats: { ...s.chats, [convId]: { ...s.chats[convId]!, messages } },
      }))
    } catch (e: any) {
      alert(`Could not send: ${e.message}`)
    } finally {
      setSending(false)
    }
  }

  const running = !!executionId && stream?.status !== undefined &&
    !['turn_completed', 'turn_failed', 'cancelled'].includes(stream?.status || '') &&
    (chat?.execution?.status ? !['completed', 'failed', 'cancelled', 'timed_out', 'interrupted'].includes(chat.execution.status) : !!executionId)

  return (
    <div className="chat">
      <Header convId={convId} running={running} />
      <div className="msglist" ref={listRef} onScroll={onScroll}>
        {messages.map((m) => (
          <MessageRow key={m.id} msg={m} />
        ))}
        {stream && stream.text && (
          <div className="msg assistant streaming">
            <div className="avatar">A</div>
            <div className="bubble">
              <Markdown text={stream.text} />
              <span className="caret" />
            </div>
          </div>
        )}
        {stream && <Activity convId={convId} executionId={executionId} />}
        {stream && ['waiting_input', 'waiting_approval'].includes(stream.status) && (
          <PendingCard convId={convId} executionId={executionId!} stream={stream} />
        )}
        {stream && stream.status === 'paused' && (
          <div className="notice">Paused at a checkpoint. Resume to continue.</div>
        )}
        {chat?.execution?.error && ['failed'].includes(chat.execution.status) && (
          <div className="errorbox">
            <AlertTriangle size={14} /> {chat.execution.error}
            <RetryButton executionId={executionId} />
          </div>
        )}
        {chat?.execution?.status === 'interrupted' && (
          <div className="notice">
            Execution was interrupted (server restart). Nothing was lost.
            <RetryButton executionId={executionId} />
          </div>
        )}
        {!stickRef.current && (
          <button className="jump" onClick={() => {
            stickRef.current = true
            if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
          }}>
            Jump to latest ↓
          </button>
        )}
      </div>
      <Composer
        convId={convId}
        draft={draft}
        setDraft={setDraft}
        send={send}
        sending={sending}
        running={running}
        executionId={executionId}
      />
    </div>
  )
}

function MessageRow({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'user') {
    return (
      <div className="msg user">
        <div className="bubble user-bubble">{msg.content}</div>
      </div>
    )
  }
  return (
    <div className="msg assistant">
      <div className="avatar">A</div>
      <div className="bubble">
        <Markdown text={msg.content} />
        {msg.status === 'error' && <div className="muted">Delivery failed.</div>}
      </div>
    </div>
  )
}

function PendingCard({ convId, executionId, stream }: { convId: string; executionId: string; stream: any }) {
  const [text, setText] = useState('')
  if (stream.status === 'waiting_input') {
    return (
      <div className="pending">
        <div className="pending-q"><AlertTriangle size={14} /> {stream.pending.question}</div>
        <div className="pending-row">
          <input
            placeholder="Type your answer…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && text.trim()) {
                api.answer(executionId, text.trim()).catch(() => alert('Answer failed'))
                setText('')
              }
            }}
          />
          <button
            className="btn primary sm"
            onClick={() => {
              if (!text.trim()) return
              api.answer(executionId, text.trim()).catch(() => alert('Answer failed'))
              setText('')
            }}
          >
            Send
          </button>
        </div>
      </div>
    )
  }
  return (
    <div className="pending">
      <div className="pending-q"><AlertTriangle size={14} /> Approval required: {stream.pending.summary}</div>
      {stream.pending.reason && <div className="muted">{stream.pending.reason}</div>}
      <div className="pending-row">
        <button className="btn primary sm" onClick={() => api.approve(executionId, true).catch(() => {})}>
          <Check size={14} /> Approve
        </button>
        <button className="btn sm" onClick={() => api.approve(executionId, false).catch(() => {})}>
          <X size={14} /> Reject
        </button>
      </div>
    </div>
  )
}

/* ---------------- Activity: one live tool pill, auto-dismiss, history behind a toggle ---------------- */

function Activity({ convId, executionId }: { convId: string; executionId: string | null }) {
  const stream = useStore((s) => s.chats[convId]?.stream)
  const completedAt = useStore((s) => s.chats[convId]?.completedAt)
  const [dismissed, setDismissed] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  useEffect(() => {
    setDismissed(false)
    setShowHistory(false)
  }, [executionId])

  useEffect(() => {
    if (!completedAt) return
    const t = setTimeout(() => setDismissed(true), 5000)
    return () => clearTimeout(t)
  }, [completedAt])

  if (!stream || stream.tools.length === 0) return null
  const running = [...stream.tools].reverse().find((t) => t.status === 'running')
  const done = stream.tools.filter((t) => t.status !== 'running')
  const failed = done.filter((t) => t.status === 'failed').length

  return (
    <div className="activity">
      {running ? (
        <div className="activity-pill running" title={running.summary}>
          <Loader2 size={13} className="spin" />
          <Wrench size={13} />
          <span className="activity-text">{running.summary}</span>
        </div>
      ) : !dismissed && done.length > 0 ? (
        <div className="activity-pill done">
          <Check size={13} />
          <span className="activity-text">
            {done.length} step{done.length === 1 ? '' : 's'}
            {failed > 0 ? ` · ${failed} failed` : ''}
          </span>
        </div>
      ) : null}
      {done.length > 0 && (
        <div className="activity-history">
          <button className="history-toggle" onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? 'Hide' : 'Show'} tool calls ({done.length})
          </button>
          {showHistory && (
            <div className="tools">
              {done.map((t, i) => (
                <ToolCard key={i} tool={t} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function RetryButton({ executionId }: { executionId: string | null }) {  if (!executionId) return null
  return (
    <button className="btn sm" onClick={() => api.retry(executionId).catch((e) => alert(e.message))}>
      <RotateCcw size={13} /> Retry
    </button>
  )
}

/* ---------------- Header + model picker ---------------- */

function Header({ convId, running }: { convId: string; running: boolean }) {
  const conv = useStore((s) => s.conversations.find((c) => c.id === convId))
  const models = useStore((s) => s.models)
  const modelsMeta = useStore((s) => s.modelsMeta)
  const setModel = useStore((s) => s.setModel)
  const renameConversation = useStore((s) => s.renameConversation)
  const setDetailOpen = useStore((s) => s.setDetailOpen)
  const detailOpen = useStore((s) => s.detailOpen)
  const loadModels = useStore((s) => s.loadModels)
  const execution = useStore((s) => s.chats[convId]?.execution)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState('')

  const filtered = useMemo(() => {
    const needle = q.toLowerCase()
    return models.filter((m) =>
      !needle || m.id.toLowerCase().includes(needle) || m.name.toLowerCase().includes(needle),
    ).slice(0, 100)
  }, [models, q])

  const groups = useMemo(() => {
    const g = new Map<string, typeof models>()
    for (const m of filtered) {
      const k = m.provider || 'other'
      if (!g.has(k)) g.set(k, [])
      g.get(k)!.push(m)
    }
    return [...g.entries()]
  }, [filtered])

  const stop = async () => {
    if (execution) await api.stop(execution.id).catch((e) => alert(e.message))
  }

  return (
    <header className="chead">
      <div className="chead-title" onDoubleClick={() => { setTitle(conv?.title || ''); setEditing(true) }}>
        {editing ? (
          <input
            value={title}
            autoFocus
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => {
              setEditing(false)
              if (title.trim()) renameConversation(convId, title.trim())
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            }}
          />
        ) : (
          <span title="Double-click to rename">{conv?.title || 'New conversation'}</span>
        )}
      </div>
      <div className="chead-actions">
        <div className="modelpicker">
          <button className="btn model-btn" onClick={() => { setPickerOpen((o) => !o); loadModels() }}>
            {shortModel(conv?.model || '')} <ChevronDown size={13} />
          </button>
          {pickerOpen && (
            <div className="picker-pop">
              <input
                autoFocus
                placeholder="Search models…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <div className="picker-list">
                {groups.map(([prov, ms]) => (
                  <div key={prov}>
                    <div className="picker-group">{prov}</div>
                    {ms.map((m) => (
                      <button
                        key={m.id}
                        className={`picker-item ${conv?.model === m.id ? 'sel' : ''}`}
                        onClick={() => {
                          if (running) {
                            if (!confirm('A response is running with the current model. Switch for the next turn?')) return
                          }
                          setModel(convId, m.id)
                          setPickerOpen(false)
                        }}
                      >
                        <span className="picker-name">{m.name}</span>
                        <span className="picker-id">{m.id}</span>
                        {conv?.model === m.id && <Check size={13} />}
                      </button>
                    ))}
                  </div>
                ))}
                {filtered.length === 0 && (
                  <div className="muted pad">No models match. {modelsMeta.error}</div>
                )}
              </div>
              <button className="mini-btn wide" onClick={() => loadModels(true)}>
                Refresh catalog{modelsMeta.stale ? ' (stale)' : ''} · {modelsMeta.source}
              </button>
            </div>
          )}
        </div>
        {execution && !['completed', 'failed', 'cancelled', 'timed_out'].includes(execution.status) ? (
          <span className="status-pill">{execution.status} · step {execution.current_step}</span>
        ) : null}
        {running && execution && (
          <>
            {execution.status === 'paused' ? (
              <button className="btn sm" onClick={() => api.resume(execution.id)}><Play size={13} /> Resume</button>
            ) : (
              <button className="btn sm" onClick={() => api.pause(execution.id).catch((e) => alert(e.message))}>
                <Pause size={13} /> Pause
              </button>
            )}
            <button className="btn sm danger" onClick={stop}><CircleStop size={13} /> Stop</button>
          </>
        )}
        <button className="icon-btn" onClick={() => setDetailOpen(!detailOpen)} title="Execution details">
          <PanelRight size={16} />
        </button>
      </div>
      {detailOpen && <DetailDrawer convId={convId} />}
    </header>
  )
}

/* ---------------- Detail drawer ---------------- */

function DetailDrawer({ convId }: { convId: string }) {
  const execution = useStore((s) => s.chats[convId]?.execution)
  const stream = useStore((s) => s.chats[convId]?.stream)
  const conv = useStore((s) => s.conversations.find((c) => c.id === convId))
  const [diff, setDiff] = useState<string | null>(null)
  const [files, setFiles] = useState<any>(null)
  const [refs, setRefs] = useState<any[]>([])
  const [excluded, setExcluded] = useState(!!(conv as any)?.memory_excluded)

  useEffect(() => {
    api.workspaceFiles(convId).then(setFiles).catch(() => setFiles({ error: true }))
    api.workspaceDiff(convId).then((d) => setDiff(d.diff || d.note || '(no changes)')).catch(() => setDiff('(unavailable)'))
    const eid = execution?.id
    if (eid) api.executionRefs(eid).then((r) => setRefs(r.refs)).catch(() => {})
    setExcluded(!!(conv as any)?.memory_excluded)
  }, [convId, stream?.tools.length])

  const toggleExclude = async () => {
    await api.patchConversation(convId, { memory_excluded: !excluded } as any)
    setExcluded(!excluded)
  };

  return (
    <div className="drawer">
      <h4>Execution</h4>
      <div className="kv"><span>Status</span><b>{execution?.status || stream?.status || 'idle'}</b></div>
      <div className="kv"><span>Model</span><b>{execution?.model || '—'}</b></div>
      <div className="kv"><span>Step</span><b>{execution ? `${execution.current_step}/${execution.max_steps}` : '—'}</b></div>
      {execution?.error && <div className="errorbox sm">{execution.error}</div>}
      <h4>Workspace</h4>
      <div className="muted">{files?.entries ? `${files.entries.length} entries` : '…'}</div>
      <h4><Brain size={13} /> Memory</h4>
      <label className="check mem-check">
        <input type="checkbox" checked={excluded} onChange={toggleExclude} />
        Exclude this chat from cross-chat memory
      </label>
      {excluded && <div className="muted small">Excluded: nothing here is indexed or dreamed.</div>}
      {refs.length > 0 ? (
        <div>
          <div className="muted small">This run used {refs.length} memor{refs.length === 1 ? 'y' : 'ies'}:</div>
          {refs.slice(0, 8).map((r) => (
            <div key={r.chunk_id} className="mem-ref" title={r.chunk_id}>
              <span className="chip">{Number(r.score).toFixed(2)}</span> {r.snippet}
            </div>
          ))}
        </div>
      ) : (
        <div className="muted small">No cross-chat memory used in this run.</div>
      )}
      <h4><FileDiff size={13} /> Changes</h4>
      <pre className="diffbox">{diff === null ? 'Loading…' : diff.slice(0, 8000) || '(no changes)'}</pre>
    </div>
  )
}

/* ---------------- Composer ---------------- */

function Composer({ convId, draft, setDraft, send, sending, running, executionId }:
  { convId: string; draft: string; setDraft: React.Dispatch<React.SetStateAction<string>>; send: () => void;
    sending: boolean; running: boolean; executionId: string | null }) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const conv = useStore((s) => s.conversations.find((c) => c.id === convId))
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [voiceError, setVoiceError] = useState('')
  const recRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const toggleRecording = async () => {
    setVoiceError('')
    if (recording) {
      recRef.current?.stop()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
        if (blob.size === 0) return
        setTranscribing(true)
        try {
          const fd = new FormData()
          fd.append('file', blob, 'voice.webm')
          const res = await fetch('/api/v1/stt/transcribe', { method: 'POST', body: fd })
          if (!res.ok) throw new Error((await res.text()).slice(0, 200))
          const { text } = await res.json()
          if (text) setDraft((d) => (d ? d + '\n' : '') + text)
          else setVoiceError('No speech detected.')
        } catch (e: any) {
          setVoiceError(`Transcription failed: ${String(e.message || e).slice(0, 120)}`)
        } finally {
          setTranscribing(false)
        }
      }
      recRef.current = rec
      rec.start()
      setRecording(true)
    } catch {
      setVoiceError('Microphone unavailable — check browser permission.')
    }
  }

  useEffect(() => {
    const el = taRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }, [draft])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        taRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="composer">
      <div className="composer-box">
        <textarea
          ref={taRef}
          rows={1}
          placeholder={running ? 'Working… type only to add context after it finishes' : `Message Atlas · ${shortModel(conv?.model || '')} (Enter to send, Shift+Enter newline)`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <div className="composer-row">
          <span className="muted small">Enter ↵ send · Shift+Enter newline · Ctrl+/ focus</span>
          <span className="composer-actions">
            {voiceError && <span className="voice-error">{voiceError}</span>}
            <button
              className={`icon-btn mic ${recording ? 'rec' : ''}`}
              title={recording ? 'Stop recording' : 'Voice input'}
              onClick={toggleRecording}
              disabled={transcribing}
            >
              {transcribing ? <Loader2 size={14} className="spin" /> : <Mic size={14} />}
              {recording && <span className="rec-dot" />}
            </button>
          {running && executionId ? (
            <button className="btn danger sm" onClick={() => api.stop(executionId)}>
              <CircleStop size={14} /> Stop
            </button>
          ) : (
            <button className="btn primary sm" disabled={!draft.trim() || sending} onClick={send}>
              {sending ? <Loader2 size={14} className="spin" /> : <Send size={14} />} Send
            </button>
          )}
          </span>
        </div>
      </div>
    </div>
  )
}

/* ---------------- Settings ---------------- */

function SettingsModal() {
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const models = useStore((s) => s.models)
  const [settings, setSettings] = useState<any>(null)
  const [def, setDef] = useState('')
  const [stab, setStab] = useState<'general' | 'soul' | 'user'>('general')

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s)
      setDef(localStorage.getItem('atlas.defaultModel') || s.default_model || '')
    }).catch(() => setSettings({}))
  }, [])

  const save = async () => {
    await api.putSettings({
      default_model: settings.default_model,
      auto_approve: settings.auto_approve,
      catalog_ttl_seconds: settings.catalog_ttl_seconds,
    })
    localStorage.setItem('atlas.defaultModel', def)
    setSettingsOpen(false)
  }

  return (
    <div className="modal-back" onClick={() => setSettingsOpen(false)}>
      <div
        className={`modal settings-modal ${stab === 'general' ? '' : 'wide'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>Settings</h3>
          <button className="icon-btn" onClick={() => setSettingsOpen(false)}><X size={15} /></button>
        </div>
        <div className="mem-tabs settings-tabs">
          {([['general', 'General'], ['soul', 'SOUL.md'], ['user', 'USER.md']] as const).map(
            ([t, label]) => (
              <button key={t} className={`mem-tab ${stab === t ? 'sel' : ''}`} onClick={() => setStab(t)}>
                {label}
              </button>
            ),
          )}
        </div>
        {stab === 'general' ? (
          !settings ? <div className="muted pad">Loading…</div> : (
            <>
              <label>Default model for new conversations</label>
              <select value={def} onChange={(e) => setDef(e.target.value)}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.id}</option>
                ))}
              </select>
              <label>Server default model</label>
              <input
                value={settings.default_model || ''}
                onChange={(e) => setSettings({ ...settings, default_model: e.target.value })}
              />
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!settings.auto_approve}
                  onChange={(e) => setSettings({ ...settings, auto_approve: e.target.checked })}
                />
                Auto-approve workspace reads/writes (destructive commands always ask)
              </label>
              <label>Catalog cache TTL (seconds)</label>
              <input
                type="number"
                value={settings.catalog_ttl_seconds ?? 3600}
                onChange={(e) => setSettings({ ...settings, catalog_ttl_seconds: Number(e.target.value) })}
              />
              <div className="muted small">
                OpenCode Go key: {settings.key_configured ? 'configured ✓' : 'not configured — set OPENCODE_ZEN_API_KEY on the server and restart.'}
              </div>
              <div className="modal-foot">
                <button className="btn" onClick={() => setSettingsOpen(false)}>Cancel</button>
                <button className="btn primary" onClick={save}>Save</button>
              </div>
            </>
          )
        ) : (
          <div className="settings-mem">
            <div className="muted small mem-note">
              {stab === 'soul'
                ? 'Agent operating principles. Short and behavioral — changes via dream need your approval.'
                : 'What the agent remembers about you. Manual edits are adopted as new versions.'}
            </div>
            <MemoryFilePanel name={stab === 'soul' ? 'SOUL.md' : 'USER.md'} compact />
          </div>
        )}
      </div>
    </div>
  )
}
