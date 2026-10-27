import {
  AlertTriangle, Brain, Check, CheckSquare, ChevronDown, CircleStop, FileDiff, FileText,
  Loader2, MessageSquarePlus, Mic, Moon, Pause, PanelLeftClose, PanelLeftOpen, Play, Plus,
  RotateCcw, Send, Settings as SettingsIcon, PanelRight, Search, SquarePen, Sun, Trash2,
  Wrench, X, Ticket,
} from 'lucide-react'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api, ChatMessage } from './api'
import { Markdown } from './markdown'
import { MemoryFilePanel } from './MemoryFiles'
import { MemoryView } from './MemoryView'
import { JiraView } from './JiraView'
import { NotesView } from './NotesView'
import { Notifications } from './Notifications'
import { OnboardingWizard } from './OnboardingWizard'
import { TasksView } from './TasksView'
import { PendingDock } from './PendingDock'
import { useStore } from './store'
import { ToolCard } from './ToolCard'
import { useExecutionStream } from './useStream'
import { EmptyState as DS_EmptyState, useConfirm, useTheme, useToast } from './ui'

export default function App() {
  const [view, setView] = useState<'chat' | 'memory' | 'notes' | 'tasks' | 'jira'>('chat')
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [taskSel, setTaskSel] = useState<string | null>(null)
  const [noteSel, setNoteSel] = useState<string | null>(null)
  const activeId = useStore((s) => s.activeId)
  const loadConversations = useStore((s) => s.loadConversations)
  const loadModels = useStore((s) => s.loadModels)
  const checkHealth = useStore((s) => s.checkHealth)
  const selectConversation = useStore((s) => s.selectConversation)
  const keyConfigured = useStore((s) => s.keyConfigured)
  const settingsOpen = useStore((s) => s.settingsOpen)

  useTheme() // applies data-theme + persists; toggle lives in sidebar/header

  useEffect(() => {
    loadConversations()
    loadModels()
    checkHealth()
    api.onboardingStatus()
      .then((s) => { if (!s.onboarded) setShowOnboarding(true) })
      .catch(() => {})
  }, [loadConversations, loadModels, checkHealth])

  return (
    <div className="app">
      <Sidebar
        onChat={() => setView('chat')}
        onMemory={() => setView('memory')}
        onNotes={() => setView('notes')}
        onTasks={() => setView('tasks')}
        onJira={() => setView('jira')}
        onBellTask={(id) => { setTaskSel(id); setView('tasks') }}
        onBellNote={(path) => { setNoteSel(path); setView('notes') }}
        inChat={view === 'chat'}
        inMemory={view === 'memory'}
        inNotes={view === 'notes'}
        inTasks={view === 'tasks'}
        inJira={view === 'jira'}
      />
      <main className="main">
        {view === 'memory' ? (
          <MemoryView onClose={() => setView('chat')} />
        ) : view === 'jira' ? (
          <JiraView />
        ) : view === 'notes' ? (
          <NotesView
            initialPath={noteSel}
            onOpenConversation={(id) => { selectConversation(id); setView('chat') }}
          />
        ) : view === 'tasks' ? (
          <TasksView
            initialTaskId={taskSel}
            onOpenConversation={(id) => { selectConversation(id); setView('chat') }}
          />
        ) : activeId ? (
          <ChatView key={activeId} convId={activeId} />
        ) : (
          <EmptyState />
        )}
      </main>
      {settingsOpen && <SettingsModal onReonboard={() => setShowOnboarding(true)} />}
      {keyConfigured === false && <KeyBanner />}
      {showOnboarding && (
        <OnboardingWizard
          onDone={() => {
            setShowOnboarding(false)
            loadConversations()
          }}
        />
      )}
    </div>
  )
}

function EmptyState() {
  const createConversation = useStore((s) => s.createConversation)
  const toast = useToast()
  return (
    <DS_EmptyState
      icon={<MessageSquarePlus size={18} />}
      title="Start a conversation"
      body="Atlas is an agentic coding environment. Pick a model, describe the task, and review each step."
      action={
        <button className="btn primary" onClick={() => createConversation().catch((e) => toast.push({ kind: 'error', title: 'Could not create conversation', desc: String(e.message || e).slice(0, 200) }))}>
          <Plus size={15} /> New conversation
        </button>
      }
    />
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

function Sidebar({ onChat, onMemory, onNotes, onTasks, onJira, onBellTask, onBellNote, inChat, inMemory, inNotes, inTasks, inJira }: {
  onChat: () => void; onMemory: () => void; onNotes: () => void; onTasks: () => void; onJira: () => void;
  onBellTask: (id: string) => void; onBellNote: (path: string) => void;
  inChat: boolean; inMemory: boolean; inNotes: boolean; inTasks: boolean; inJira: boolean;
}) {
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
  const { theme, toggle } = useTheme()
  const { ask, dialog } = useConfirm()

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
        createConversation().then(() => onChat())
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [createConversation, onChat])

  if (!sidebarOpen) {
    return (
      <button className="rail-btn" onClick={() => setSidebar(true)} title="Open sidebar" aria-label="Open sidebar">
        <PanelLeftOpen size={16} />
      </button>
    )
  }

  return (
    <aside className="sidebar" aria-label="Workspace navigation">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">A</span>
        <span className="brand-name">Atlas</span>
        <button className="icon-btn" onClick={() => setSidebar(false)} title="Close sidebar" aria-label="Close sidebar">
          <PanelLeftClose size={16} />
        </button>
      </div>
      <nav className="navgrid" aria-label="Primary">
        <button className={`btn navcell ${inChat ? 'active' : ''}`}
          title="New chat (Ctrl+Shift+O)" aria-current={inChat ? 'page' : undefined}
          onClick={() => { createConversation().then(() => onChat()) }}>
          <SquarePen size={15} /> New chat
        </button>
        <button className={`btn navcell ${inMemory ? 'active' : ''}`} onClick={onMemory} aria-current={inMemory ? 'page' : undefined}>
          <Brain size={15} /> Memory
        </button>
        <button className={`btn navcell ${inNotes ? 'active' : ''}`} onClick={onNotes} aria-current={inNotes ? 'page' : undefined}>
          <FileText size={15} /> Notes
        </button>
        <button className={`btn navcell ${inTasks ? 'active' : ''}`} onClick={onTasks} aria-current={inTasks ? 'page' : undefined}>
          <CheckSquare size={15} /> Tasks
        </button>
        <button className={`btn navcell ${inJira ? 'active' : ''}`} onClick={onJira} title="Jira (Ctrl+Shift+J)" aria-current={inJira ? 'page' : undefined}>
          <Ticket size={15} /> Jira
        </button>
      </nav>
      <div className="searchbox">
        <Search size={14} aria-hidden="true" />
        <input
          id="conv-search"
          placeholder="Search conversations"
          aria-label="Search conversations"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <kbd title="Ctrl+K">⌃K</kbd>
      </div>
      <div className="sidebar-sec" id="conv-sec">Conversations</div>
      <div className="convlist" role="listbox" aria-labelledby="conv-sec">
        {filtered.map((c) => (
          <div
            key={c.id}
            role="option"
            aria-selected={c.id === activeId}
            tabIndex={0}
            className={`conv ${c.id === activeId ? 'active' : ''}`}
            onClick={() => { selectConversation(c.id); onChat() }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectConversation(c.id); onChat() }
            }}
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
              title="Delete conversation"
              aria-label={`Delete ${c.title || 'conversation'}`}
              onClick={async (e) => {
                e.stopPropagation()
                if (await ask('Delete this conversation?', `"${(c.title || 'New conversation').slice(0, 80)}" will be removed permanently.`, true)) deleteConversation(c.id)
              }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {filtered.length === 0 && <div className="muted pad small">No conversations match.</div>}
      </div>
      <div className="sidebar-foot">
        <Notifications onOpenTask={onBellTask} onOpenNote={onBellNote} />
        <button className="icon-btn" onClick={toggle} title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} aria-label="Toggle theme">
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
        <button className="btn ghost" onClick={() => setSettingsOpen(true)}>
          <SettingsIcon size={15} /> Settings
        </button>
      </div>
      {dialog}
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
  const toast = useToast()
  const listRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  const messages = chat?.messages || []
  const stream = chat?.stream || null

  // The execution snapshot taken at send-time goes stale (step count,
  // status) — without refreshes the header reads "running · step 0" forever
  // and a missed terminal SSE frame looks like a freeze. Poll while active.
  useEffect(() => {
    if (!executionId) return
    if (stream && ['turn_completed', 'turn_failed', 'cancelled'].includes(stream.status)) return
    const t = window.setInterval(async () => {
      try {
        const snap = await api.getExecution(executionId)
        if (['completed', 'failed', 'cancelled', 'timed_out', 'interrupted'].includes(snap.status)) {
          useStore.setState((s) => ({
            chats: { ...s.chats, [convId]: { ...(s.chats[convId]!), execution: null } },
          }))
        } else {
          useStore.getState().setExecution(convId, snap)
        }
      } catch { /* transient — next tick retries */ }
    }, 3000)
    return () => window.clearInterval(t)
  }, [convId, executionId, stream?.status])

  useEffect(() => {
    if (stickRef.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages.length, stream?.text, stream?.tools.length, stream?.status, sending])

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
      setDraft(text) // restore — never discard user input on failure
      toast.push({ kind: 'error', title: 'Could not send', desc: String(e.message || e).slice(0, 220) })
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
      <div className="msglist" ref={listRef} onScroll={onScroll} role="log" aria-live="off" aria-label="Conversation messages">
        {messages.map((m) => (
          <MessageRow key={m.id} msg={m} />
        ))}
        {/* First-token suspense: stream exists but is empty (queued/starting/thinking),
            or the send POST itself is still in flight. Without this the chat sits
            dead-still between send and the first token/tool event. */}
        {(sending || (stream && !['turn_completed', 'turn_failed', 'cancelled'].includes(stream.status))) &&
          !stream?.text && (
          <div className="msg assistant">
            <div className="avatar">A</div>
            <div className="bubble thinking-bubble">
              <span className="thinking" aria-hidden="true"><span /><span /><span /></span>
              <span className="muted small">
                {sending ? 'Sending…' : stream?.status === 'queued' ? 'Queued…' : 'Thinking…'}
              </span>
            </div>
          </div>
        )}
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
      {stream && ['waiting_input', 'waiting_approval'].includes(stream.status) && (
        <PendingDock convId={convId} status={stream.status} pending={stream.pending || {}} />
      )}
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
  const toast = useToast()
  return (
    <button className="btn sm" onClick={() => api.retry(executionId).catch((e) => toast.push({ kind: 'error', title: 'Retry failed', desc: String(e.message || e).slice(0, 200) }))}>
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
  const pickerRef = useRef<HTMLDivElement>(null)
  const toast = useToast()
  const { ask, dialog } = useConfirm()

  useEffect(() => {
    if (!pickerOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPickerOpen(false) }
    const onClick = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onClick)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('mousedown', onClick) }
  }, [pickerOpen])

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
    if (execution) await api.stop(execution.id).catch((e) => toast.push({ kind: 'error', title: 'Stop failed', desc: String(e.message || e).slice(0, 200) }))
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
        <div className="modelpicker" ref={pickerRef}>
          <button className="btn model-btn" aria-haspopup="listbox" aria-expanded={pickerOpen} onClick={() => { setPickerOpen((o) => !o); loadModels() }}>
            <span>{shortModel(conv?.model || '')}</span> <ChevronDown size={13} />
          </button>
          {pickerOpen && (
            <div className="picker-pop" role="listbox" aria-label="Select model">
              <input
                autoFocus
                placeholder="Search models…"
                aria-label="Search models"
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
                        role="option"
                        aria-selected={conv?.model === m.id}
                        className={`picker-item ${conv?.model === m.id ? 'sel' : ''}`}
                        onClick={async () => {
                          if (running) {
                            if (!await ask('Switch model mid-run?', 'The running response keeps the current model; the new model applies to the next turn.')) return
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
              <button className="btn sm" onClick={() => api.pause(execution.id).catch((e) => toast.push({ kind: 'error', title: 'Pause failed', desc: String(e.message || e).slice(0, 200) }))}>
                <Pause size={13} /> Pause
              </button>
            )}
            <button className="btn sm danger" onClick={stop}><CircleStop size={13} /> Stop</button>
          </>
        )}
        <button className="icon-btn" onClick={() => setDetailOpen(!detailOpen)} title="Execution details" aria-label="Toggle execution details" aria-expanded={detailOpen}>
          <PanelRight size={16} />
        </button>
      </div>
      {dialog}
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
    <div className="drawer" role="complementary" aria-label="Execution details">
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
        <label className="sr-only" htmlFor="composer-input">Message Atlas</label>
        <textarea
          id="composer-input"
          ref={taRef}
          rows={1}
          aria-label="Message Atlas"
          placeholder={running ? 'Working… you can queue context, it sends after this run' : `Message Atlas · ${shortModel(conv?.model || '')}`}
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
            {voiceError && <span className="voice-error" role="alert">{voiceError}</span>}
            <button
              className={`icon-btn mic ${recording ? 'rec' : ''}`}
              title={recording ? 'Stop recording' : 'Voice input'}
              aria-label={recording ? 'Stop voice recording' : 'Start voice input'}
              aria-pressed={recording}
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
            <button className="btn primary sm" disabled={!draft.trim() || sending} onClick={send} aria-label="Send message">
              {sending ? <Loader2 size={14} className="spin" /> : <Send size={14} />} {sending ? 'Sending' : 'Send'}
            </button>
          )}
          </span>
        </div>
      </div>
    </div>
  )
}

/* ---------------- Settings ---------------- */

function SettingsModal({ onReonboard }: { onReonboard: () => void }) {
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
        role="dialog" aria-modal="true" aria-label="Settings"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') setSettingsOpen(false) }}
      >
        <div className="modal-head">
          <h3>Settings</h3>
          <button className="icon-btn" aria-label="Close settings" onClick={() => setSettingsOpen(false)}><X size={15} /></button>
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
              <button
                className="mini-btn"
                onClick={async () => {
                  await api.reopenOnboarding()
                  setSettingsOpen(false)
                  onReonboard()
                }}
              >
                Re-run onboarding interview
              </button>
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
