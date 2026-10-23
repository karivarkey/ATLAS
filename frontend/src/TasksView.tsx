import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bell, CalendarDays, Check, CheckSquare, CircleDashed, Clock3, Inbox,
  ListTodo, Plus, Sparkles, StickyNote, Trash2,
} from 'lucide-react'
import { notesApi, Task, tasksApi } from './notesApi'
import { taskBucket } from './taskLogic'

interface Props {
  onOpenConversation?: (convId: string) => void
  initialTaskId?: string | null
}

type Filter = 'today' | 'overdue' | 'waiting' | 'inbox' | 'all'

const FILTERS: { id: Filter; label: string; icon: React.ReactNode; hint: string }[] = [
  { id: 'today', label: 'Today', icon: <CalendarDays size={15} />, hint: 'Due before tomorrow' },
  { id: 'overdue', label: 'Overdue', icon: <Clock3 size={15} />, hint: 'Past due' },
  { id: 'waiting', label: 'Waiting', icon: <CircleDashed size={15} />, hint: 'Needs input or approval' },
  { id: 'inbox', label: 'Inbox', icon: <Inbox size={15} />, hint: 'Not triaged yet' },
  { id: 'all', label: 'All tasks', icon: <ListTodo size={15} />, hint: 'Everything' },
]

const NEXT: Record<string, string[]> = {
  inbox: ['planned', 'in_progress', 'cancelled'],
  planned: ['in_progress', 'cancelled', 'inbox'],
  in_progress: ['waiting_input', 'waiting_approval', 'completed', 'cancelled', 'planned'],
  waiting_input: ['in_progress', 'completed', 'cancelled'],
  waiting_approval: ['in_progress', 'completed', 'cancelled'],
  completed: ['archived'],
  cancelled: ['archived'],
  archived: ['planned'],
}

const PRIO_ORDER = ['critical', 'high', 'normal', 'low'] as const

export function TasksView({ onOpenConversation, initialTaskId }: Props) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [filter, setFilter] = useState<Filter>('today')
  const [project, setProject] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [err, setErr] = useState('')
  const [title, setTitle] = useState('')
  const [quickPrio, setQuickPrio] = useState('normal')
  const [quickDue, setQuickDue] = useState('')
  const [execState, setExecState] = useState<'idle' | 'running' | 'verifying' | 'done'>('idle')
  const [execId, setExecId] = useState('')
  const [evidence, setEvidence] = useState('')
  const [occ, setOcc] = useState<any[]>([])
  const [reminders, setReminders] = useState<any[]>([])
  const [history, setHistory] = useState<any[]>([])

  const refresh = useCallback(async () => {
    try {
      const r = await tasksApi.list()
      setTasks(r.tasks)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const open = useCallback(async (id: string) => {
    setSel(id)
    setExecState('idle')
    setEvidence('')
    setErr('')
    try {
      const t = await tasksApi.get(id)
      setTask(t)
      setOcc((await tasksApi.occurrences(id)).occurrences)
      setReminders((await tasksApi.listReminders(id)).reminders)
      try { setHistory((await tasksApi.history(id)).history) } catch { setHistory([]) }
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }, [])

  useEffect(() => {
    if (initialTaskId) open(initialTaskId)
  }, [initialTaskId, open])

  const now = Date.now()
  const projects = useMemo(() => [...new Set(tasks.map((t) => t.project).filter(Boolean))].sort(), [tasks])

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { today: 0, overdue: 0, waiting: 0, inbox: 0, all: tasks.length }
    for (const t of tasks) {
      if (project && t.project !== project) continue
      const b = taskBucket(t, now)
      if (b !== 'all') c[b]++
    }
    return c
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, project])

  const visible = tasks
    .filter((t) => {
      if (project && t.project !== project) return false
      if (filter === 'all') return t.status !== 'archived'
      return taskBucket(t, now) === filter
    })
    .sort((a, b) => {
      const ad = a.due_at || Infinity, bd = b.due_at || Infinity
      if (ad !== bd) return ad - bd
      return PRIO_ORDER.indexOf(a.priority as any) - PRIO_ORDER.indexOf(b.priority as any)
    })

  const create = async () => {
    if (!title.trim()) return
    try {
      const body: any = { title: title.trim(), project: project || undefined, priority: quickPrio }
      if (quickDue) body.due_at = new Date(quickDue).getTime()
      const t = await tasksApi.create(body)
      setTitle('')
      setQuickDue('')
      await refresh()
      open(t.id)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }

  const quickComplete = async (t: Task) => {
    try {
      if (t.status === 'completed') {
        setTasks((ts) => ts.map((x) => (x.id === t.id ? { ...x, status: 'archived' } : x)))
        await tasksApi.transition(t.id, 'archived')
      } else if ((NEXT[t.status] || []).includes('completed')) {
        setTasks((ts) => ts.map((x) => (x.id === t.id ? { ...x, status: 'completed' } : x)))
        await tasksApi.transition(t.id, 'completed')
      } else {
        for (const step of ['planned', 'in_progress', 'completed'] as const) {
          const cur = (await tasksApi.get(t.id)).status
          if (cur === 'completed' || !(NEXT[cur] || []).includes(step)) break
          await tasksApi.transition(t.id, step)
        }
      }
      await refresh()
      if (sel === t.id) open(t.id)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
      refresh()
    }
  }

  const runAI = async () => {
    if (!task) return
    setExecState('running')
    setErr('')
    try {
      const r = await tasksApi.execute(task.id)
      setExecId(r.execution_id)
      if (onOpenConversation) onOpenConversation(r.conversation_id)
      const { api } = await import('./api')
      for (let i = 0; i < 300; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        try {
          const ex = await api.getExecution(r.execution_id)
          if (['completed', 'failed', 'cancelled', 'timed_out', 'interrupted'].includes(ex.status)) break
        } catch { break }
      }
      setExecState('verifying')
      open(task.id)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 400))
      setExecState('idle')
    }
  }

  const verify = async (ok: boolean) => {
    if (!task || !execId) return
    try {
      await tasksApi.verify(task.id, execId, ok, evidence)
      setExecState('done')
      setExecId('')
      setEvidence('')
      await refresh()
      open(task.id)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 400))
    }
  }

  const activeCount = tasks.filter((t) => !['completed', 'cancelled', 'archived'].includes(t.status)).length

  return (
    <div className="tasks">
      <div className="tasks-head">
        <h2><CheckSquare size={17} /> Tasks</h2>
        <span className="sub">{activeCount} open · {counts.overdue} overdue</span>
        <span style={{ flex: 1 }} />
        <select value={project} onChange={(e) => setProject(e.target.value)}
          style={{ fontSize: 12.5, borderRadius: 8, border: '1px solid var(--line)', padding: '6px 8px' }}>
          <option value="">All projects</option>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>

      <div className="tasks-body">
        {/* ------- left ------- */}
        <div className="tasks-side">
          <div className="tasks-composer">
            <div className="tasks-composer-row">
              <input value={title} onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') create() }}
                placeholder="New task… type and hit Enter" />
              <button className="btn primary sm" disabled={!title.trim()} onClick={create} title="Add task">
                <Plus size={14} />
              </button>
            </div>
            <div className="tasks-quickrow">
              <select value={quickPrio} onChange={(e) => setQuickPrio(e.target.value)} title="Priority">
                {PRIO_ORDER.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <input type="date" value={quickDue} onChange={(e) => setQuickDue(e.target.value)} title="Due date" />
              {quickDue && <button className="mini-btn" onClick={() => setQuickDue('')}>clear</button>}
            </div>
          </div>

          <div className="tasks-filters">
            {FILTERS.map((f) => (
              <button key={f.id} className={`tasks-filter ${filter === f.id ? 'sel' : ''}`}
                onClick={() => setFilter(f.id)} title={f.hint}>
                {f.icon}<span>{f.label}</span>
                <span className={`count ${f.id === 'overdue' && counts[f.id] > 0 ? 'alert' : ''}`}>
                  {counts[f.id]}
                </span>
              </button>
            ))}
          </div>

          <div className="tasks-list">
            {err && <div className="errorbox" style={{ marginBottom: 8 }}>{err}</div>}
            {visible.map((t) => {
              const done = t.status === 'completed'
              const overdue = !!t.due_at && t.due_at < now && !['completed', 'cancelled', 'archived'].includes(t.status)
              const isToday = !!t.due_at && !overdue && t.due_at < now - (now % 86400000) + 86400000
              return (
                <div key={t.id} className={`task-row ${sel === t.id ? 'active' : ''} ${done ? 'done' : ''}`}
                  onClick={() => open(t.id)}>
                  <button className={`task-check ${done ? 'done' : ''}`}
                    title={done ? 'Archive' : 'Mark complete'}
                    onClick={(e) => { e.stopPropagation(); quickComplete(t) }}>
                    {done && <Check size={12} />}
                  </button>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="tt">{t.title}</div>
                    <div className="tm">
                      <span className={`prio-dot prio-${t.priority}`} title={`${t.priority} priority`} />
                      <span className={`status-pill s-${t.status}`}>{prettyStatus(t.status)}</span>
                      {t.due_at ? (
                        <span className={`due-chip ${overdue ? 'overdue' : isToday ? 'today' : ''}`}>
                          {overdue ? `Overdue · ${fmtDate(t.due_at)}` : fmtDate(t.due_at)}
                        </span>
                      ) : (
                        <span className="due-chip">no date</span>
                      )}
                      {t.project && <span className="proj-tag">{t.project}</span>}
                    </div>
                  </div>
                </div>
              )
            })}
            {visible.length === 0 && (
              <div className="muted pad" style={{ textAlign: 'center' }}>
                <Check size={22} color="var(--muted)" />
                <div style={{ marginTop: 6, fontWeight: 600, color: 'var(--ink2)' }}>All clear</div>
                <div className="small">No tasks in {FILTERS.find((f) => f.id === filter)?.label}.</div>
              </div>
            )}
          </div>
        </div>

        {/* ------- detail ------- */}
        <div className="tasks-detail">
          {!task ? (
            <div className="task-detail-empty">
              <div className="notes-empty-card">
                <ListTodo size={28} color="var(--muted)" />
                <h3>Select a task</h3>
                <p>Pick one on the left to see details, set dates, link notes, or hand it to the AI.</p>
              </div>
            </div>
          ) : (
            <TaskDetail
              key={task.id}
              task={task}
              occ={occ}
              reminders={reminders}
              history={history}
              execState={execState}
              evidence={evidence}
              setEvidence={setEvidence}
              setErr={setErr}
              setTask={setTask}
              refresh={async () => { await refresh(); open(task.id) }}
              runAI={runAI}
              verify={verify}
              quickComplete={quickComplete}
            />
          )}
        </div>
      </div>
    </div>
  )
}

/* ---------------- detail card ---------------- */

function TaskDetail({ task, occ, reminders, history, execState, evidence, setEvidence, setErr, setTask, refresh, runAI, verify, quickComplete }: {
  task: Task; occ: any[]; reminders: any[]; history: any[]
  execState: 'idle' | 'running' | 'verifying' | 'done'
  evidence: string; setEvidence: (s: string) => void; setErr: (s: string) => void
  setTask: (t: Task) => void; refresh: () => void
  runAI: () => void; verify: (ok: boolean) => void; quickComplete: (t: Task) => void
}) {
  const [prio, setPrio] = useState(task.priority)
  const [due, setDue] = useState(task.due_at ? new Date(task.due_at).toISOString().slice(0, 16) : '')
  const [proj, setProj] = useState(task.project || '')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setPrio(task.priority)
    setDue(task.due_at ? new Date(task.due_at).toISOString().slice(0, 16) : '')
    setProj(task.project || '')
    setDirty(false)
  }, [task.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveMeta = async () => {
    setSaving(true)
    try {
      await tasksApi.patch(task.id, { priority: prio, due_at: due ? new Date(due).getTime() : 0, project: proj } as any)
      refresh()
    } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
    finally { setSaving(false); setDirty(false) }
  }

  const saveDesc = async (desc: string) => {
    try { await tasksApi.patch(task.id, { description: desc }) } catch { /* keep local */ }
  }

  const done = task.status === 'completed'

  return (
    <div className="task-card">
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <button className={`task-check ${done ? 'done' : ''}`} style={{ marginTop: 4 }}
          title={done ? 'Archive' : 'Mark complete'}
          onClick={() => quickComplete(task)}>
          {done && <Check size={12} />}
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <input
            className="task-title-input"
            value={task.title}
            onChange={(e) => setTask({ ...task, title: e.target.value })}
            onBlur={async () => {
              if (!task.title.trim()) return
              try { await tasksApi.patch(task.id, { title: task.title.trim() }); refresh() } catch {}
            }}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
          />
          <div className="task-idline">
            {task.id.slice(0, 8)} · created {new Date(task.created_at).toLocaleDateString()}
          </div>
        </div>
        <button
          className="icon-btn danger" title="Delete task"
          onClick={async () => {
            if (!confirm(`Delete “${task.title}”?`)) return
            try {
              await tasksApi.remove(task.id)
              window.location.reload()
            } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
          }}
        >
          <Trash2 size={14} />
        </button>
      </div>

      <div className="task-grid">
        <span className="lbl">Status</span>
        <div className="status-flow">
          <span className={`status-pill s-${task.status} status-current`}>{prettyStatus(task.status)}</span>
          {(NEXT[task.status] || []).map((s) => (
            <button key={s} className="mini-btn"
              onClick={async () => {
                try { await tasksApi.transition(task.id, s as any); refresh() }
                catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
              }}>
              → {prettyStatus(s)}
            </button>
          ))}
        </div>

        <span className="lbl">Priority</span>
        <div className="seg">
          {PRIO_ORDER.map((p) => (
            <button key={p} className={prio === p ? 'sel' : ''}
              onClick={() => { setPrio(p as any); setDirty(true) }}>
              <span className={`prio-dot prio-${p}`} /> {p}
            </button>
          ))}
        </div>

        <span className="lbl">Due</span>
        <div>
          <input type="datetime-local" value={due}
            onChange={(e) => { setDue(e.target.value); setDirty(true) }}
            style={{ padding: 7, borderRadius: 8, border: '1px solid var(--line)', fontSize: 13 }} />
          <div className="due-helpers">
            <button className="mini-btn" onClick={() => { setDue(todayAt(18)); setDirty(true) }}>Today eve</button>
            <button className="mini-btn" onClick={() => { setDue(tomorrowAt(9)); setDirty(true) }}>Tomorrow morn</button>
            <button className="mini-btn" onClick={() => { setDue(nextWeek()); setDirty(true) }}>Next week</button>
            {due && <button className="mini-btn" onClick={() => { setDue(''); setDirty(true) }}>Clear</button>}
          </div>
        </div>

        <span className="lbl">Project</span>
        <input value={proj} onChange={(e) => { setProj(e.target.value); setDirty(true) }}
          placeholder="e.g. home, work…" list="proj-list"
          style={{ padding: 7, borderRadius: 8, border: '1px solid var(--line)', fontSize: 13, maxWidth: 240 }} />
      </div>
      {dirty && (
        <div style={{ marginTop: 4 }}>
          <button className="btn primary sm" disabled={saving} onClick={saveMeta}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      )}

      <div className="task-section">
        <h4><StickyNote size={12} /> Notes</h4>
        <textarea
          className="task-desc"
          value={task.description}
          onChange={(e) => setTask({ ...task, description: e.target.value })}
          onBlur={(e) => saveDesc(e.target.value)}
          placeholder="What does “done” look like? Add context, links, acceptance criteria…"
          rows={3}
        />
      </div>

      <div className="task-section">
        <h4><StickyNote size={12} /> Linked notes</h4>
        {task.notes.length === 0 && <div className="muted small" style={{ marginBottom: 6 }}>None yet — link context from the vault.</div>}
        {task.notes.map((n) => (
          <div key={n.note_id} className="link-card">
            <span>📝</span>
            <span className="mono">{n.path}</span>
            <span style={{ flex: 1 }} />
            <button className="mini-btn"
              onClick={async () => {
                await tasksApi.unlinkNote(task.id, n.note_id)
                refresh()
              }}>unlink</button>
          </div>
        ))}
        <LinkNote taskId={task.id} onDone={refresh} setErr={setErr} />
      </div>

      <div className="task-section">
        <h4><CalendarDays size={12} /> Recurrence</h4>
        <RecurrenceEditor task={task} occ={occ} onDone={refresh} setErr={setErr} />
      </div>

      <div className="task-section">
        <h4><Bell size={12} /> Reminders</h4>
        {reminders.filter((r) => r.status !== 'dismissed').length === 0 &&
          <div className="muted small" style={{ marginBottom: 6 }}>No active reminders.</div>}
        {reminders.filter((r) => r.status !== 'dismissed').map((r) => (
          <div key={r.id} className="link-card">
            <span>⏰</span>
            <span>{r.fire_at ? new Date(r.fire_at).toLocaleString() : 'no time'} · {r.status}</span>
            {r.snoozed_until ? <span className="muted small"> (snoozed to {new Date(r.snoozed_until).toLocaleString()})</span> : null}
            <span style={{ flex: 1 }} />
            <button className="mini-btn"
              onClick={async () => {
                await tasksApi.snoozeReminder(r.id, Date.now() + 3600000)
                refresh()
              }}>+1h</button>
            <button className="mini-btn"
              onClick={async () => {
                await tasksApi.dismissReminder(r.id)
                refresh()
              }}>dismiss</button>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
          <button className="mini-btn"
            onClick={async () => {
              await tasksApi.createReminder(task.id, Date.now() + 3600000)
              refresh()
            }}>+ remind in 1h</button>
          <button className="mini-btn"
            onClick={async () => {
              await tasksApi.createReminder(task.id, tomorrowAtMs(9))
              refresh()
            }}>+ tomorrow 9am</button>
        </div>
      </div>

      <div className="task-section">
        <h4><Sparkles size={12} /> Let AI do this</h4>
        {execState === 'idle' && (
          <button className="btn primary sm" onClick={runAI}>
            <Sparkles size={13} /> Let AI do this
          </button>
        )}
        {execState === 'running' && (
          <div className="notice">AI is working… progress streams in the chat view.</div>
        )}
        {execState === 'verifying' && (
          <div className="pending">
            <div className="pending-q">Execution finished. What evidence shows it worked?</div>
            <input value={evidence} onChange={(e) => setEvidence(e.target.value)}
              placeholder="e.g. tests pass, file updated, screenshot…"
              style={{ width: '100%', padding: '8px 10px', border: '1px solid var(--line)',
                borderRadius: 8, marginBottom: 8, fontSize: 13 }} />
            <div className="pending-row">
              <button className="btn primary sm" disabled={!evidence.trim()}
                onClick={() => verify(true)}>Verified — complete</button>
              <button className="btn sm" onClick={() => verify(false)}>Not done</button>
            </div>
          </div>
        )}
        {execState === 'done' && <div className="muted small">Attempt recorded — see history below.</div>}
      </div>

      {history.length > 0 && (
        <div className="task-section">
          <h4><Clock3 size={12} /> History ({history.length})</h4>
          {history.slice(0, 8).map((h, i) => (
            <div key={i} className="muted small" style={{ padding: '3px 0', borderBottom: '1px dashed var(--line)' }}>
              {new Date(h.created_at || h.at || Date.now()).toLocaleString()} · {h.event || h.action || h.to || JSON.stringify(h).slice(0, 100)}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function LinkNote({ taskId, onDone, setErr }: {
  taskId: string; onDone: () => void; setErr: (s: string) => void
}) {
  const [prefix, setPrefix] = useState('')
  const [opts, setOpts] = useState<any[]>([])
  useEffect(() => {
    if (!prefix.trim()) { setOpts([]); return }
    const t = window.setTimeout(async () => {
      try { setOpts((await notesApi.suggest(prefix.trim())).suggestions) } catch {}
    }, 250)
    return () => window.clearTimeout(t)
  }, [prefix])
  return (
    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
      <input value={prefix} onChange={(e) => setPrefix(e.target.value)}
        placeholder="Type a note title to link…"
        list="note-suggest-task"
        style={{ flex: 1, padding: '8px 10px', border: '1px solid var(--line)', borderRadius: 9, fontSize: 13 }} />
      <datalist id="note-suggest-task">
        {opts.map((o) => <option key={o.path} value={o.path}>{o.title}</option>)}
      </datalist>
      <button className="mini-btn" disabled={!prefix.trim()} onClick={async () => {
        if (!prefix.trim()) return
        try {
          await tasksApi.linkNote(taskId, prefix.trim())
          setPrefix('')
          onDone()
        } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
      }}>Link</button>
    </div>
  )
}

function RecurrenceEditor({ task, occ, onDone, setErr }: {
  task: Task; occ: any[]; onDone: () => void; setErr: (s: string) => void
}) {
  const [rec, setRec] = useState(task.recurrence || '')
  useEffect(() => setRec(task.recurrence || ''), [task.id]) // eslint-disable-line
  return (
    <div>
      <div style={{ display: 'flex', gap: 6 }}>
        <select value={rec} onChange={(e) => setRec(e.target.value)}
          style={{ padding: 7, borderRadius: 8, border: '1px solid var(--line)', fontSize: 13 }}>
          <option value="">Does not repeat</option>
          <option value="daily">Daily</option>
          <option value="weekdays">Weekdays</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="interval:2">Every 2 days</option>
          <option value="interval:7">Every 7 days</option>
        </select>
        <button className="mini-btn" onClick={async () => {
          try {
            await tasksApi.patch(task.id, { recurrence: rec } as any)
            if (rec) await tasksApi.genOccurrences(task.id, 30)
            onDone()
          } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
        }}>Save</button>
      </div>
      {occ.length > 0 && (
        <div className="muted small" style={{ marginTop: 6 }}>
          {occ.length} upcoming: {occ.slice(0, 5).map((o) => o.occurrence_key).join(', ')}
          {occ.length > 5 ? '…' : ''}
        </div>
      )}
    </div>
  )
}

function prettyStatus(s: string): string {
  return s.replace(/_/g, ' ')
}

function fmtDate(ms: number): string {
  const d = new Date(ms)
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  if (sameDay) return `Today ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  const tomorrow = new Date(today.getTime() + 86400000)
  if (d.toDateString() === tomorrow.toDateString()) return `Tmrw ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function todayAt(h: number): string {
  const d = new Date()
  d.setHours(h, 0, 0, 0)
  return toLocalInput(d.getTime())
}
function tomorrowAt(h: number): string {
  return toLocalInput(tomorrowAtMs(h))
}
function tomorrowAtMs(h: number): number {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  d.setHours(h, 0, 0, 0)
  return d.getTime()
}
function nextWeek(): string {
  const d = new Date()
  d.setDate(d.getDate() + 7)
  d.setHours(9, 0, 0, 0)
  return toLocalInput(d.getTime())
}
function toLocalInput(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
