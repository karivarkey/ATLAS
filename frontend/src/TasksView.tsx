import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { notesApi, Task, tasksApi } from './notesApi'
import { taskBucket } from './taskLogic'

interface Props {
  onOpenConversation?: (convId: string) => void
  initialTaskId?: string | null
}

type Filter = 'today' | 'overdue' | 'waiting' | 'inbox' | 'all'

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

export function TasksView({ onOpenConversation, initialTaskId }: Props) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [filter, setFilter] = useState<Filter>('today')
  const [project, setProject] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [err, setErr] = useState('')
  const [title, setTitle] = useState('')
  const [execState, setExecState] = useState<'idle' | 'running' | 'verifying' | 'done'>('idle')
  const [execId, setExecId] = useState('')
  const [evidence, setEvidence] = useState('')
  const [occ, setOcc] = useState<any[]>([])
  const [reminders, setReminders] = useState<any[]>([])

  const refresh = useCallback(async () => {
    try {
      const r = await tasksApi.list()
      setTasks(r.tasks)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (initialTaskId) open(initialTaskId)
  }, [initialTaskId])

  const open = useCallback(async (id: string) => {
    setSel(id)
    setExecState('idle')
    setEvidence('')
    try {
      const t = await tasksApi.get(id)
      setTask(t)
      setOcc((await tasksApi.occurrences(id)).occurrences)
      setReminders((await tasksApi.listReminders(id)).reminders)
    } catch (e: any) {
      setErr(String(e.message || e).slice(0, 300))
    }
  }, [])

  const now = Date.now()
  const projects = useMemo(() => [...new Set(tasks.map((t) => t.project).filter(Boolean))], [tasks])
  const visible = tasks.filter((t) => {
    if (project && t.project !== project) return false
    if (filter === 'all') return true
    return taskBucket(t, now) === filter
  }).sort((a, b) => (a.due_at || Infinity) - (b.due_at || Infinity))

  const create = async () => {
    if (!title.trim()) return
    try {
      const t = await tasksApi.create({ title: title.trim(), project: project || undefined })
      setTitle('')
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
        // walk inbox → planned → in_progress → completed
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
      // poll execution via conversation status until terminal
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

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* list */}
      <div style={{ width: 330, minWidth: 330, borderRight: '1px solid var(--line)',
        display: 'flex', flexDirection: 'column', background: 'var(--panel)' }}>
        <div style={{ padding: 10, display: 'flex', gap: 6 }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') create() }}
            placeholder="New task… (Enter)" style={{ flex: 1, padding: '7px 9px',
              border: '1px solid var(--line)', borderRadius: 8, fontSize: 13 }} />
        </div>
        <div style={{ padding: '0 10px 6px', display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {(['today', 'overdue', 'waiting', 'inbox', 'all'] as Filter[]).map((f) => (
            <button key={f} className={`mini-btn ${filter === f ? 'on' : ''}`}
              onClick={() => setFilter(f)}>{f[0].toUpperCase() + f.slice(1)}</button>
          ))}
          <select value={project} onChange={(e) => setProject(e.target.value)}
            style={{ fontSize: 12, borderRadius: 6, border: '1px solid var(--line)' }}>
            <option value="">All projects</option>
            {projects.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 6px 10px' }}>
          {visible.map((t) => (
            <div key={t.id} className={`conv ${sel === t.id ? 'active' : ''}`}
              onClick={() => open(t.id)}>
              <input type="checkbox" checked={t.status === 'completed'}
                onChange={(e) => { e.stopPropagation(); quickComplete(t) }}
                onClick={(e) => e.stopPropagation()} title="quick complete" />
              <div className="conv-title">{t.title}</div>
              <div className="conv-meta">
                <span className="chip">{t.priority}</span>
                <span className="conv-model">{t.status}{t.due_at ? ' · ' +
                  new Date(t.due_at).toLocaleDateString() : ''}</span>
              </div>
            </div>
          ))}
          {visible.length === 0 && <div className="muted pad">No tasks here.</div>}
        </div>
      </div>

      {/* detail */}
      <div style={{ flex: 1, padding: 16, overflowY: 'auto', minWidth: 0 }}>
        {err && <div className="errorbox">{err}</div>}
        {!task ? <div className="muted">Select a task.</div> : (
          <div style={{ maxWidth: 640 }}>
            <h2 style={{ margin: '0 0 4px' }}>{task.title}</h2>
            <div className="muted small" style={{ marginBottom: 10 }}>
              {task.id} · {task.status} · {task.priority}
              {task.due_at ? ` · due ${new Date(task.due_at).toLocaleString()}` : ' · no due date'}
              {task.project ? ` · ${task.project}` : ''}
            </div>
            <textarea value={task.description}
              onChange={(e) => setTask({ ...task, description: e.target.value })}
              onBlur={async () => {
                try { await tasksApi.patch(task.id, { description: task.description }) } catch {}
              }}
              placeholder="Description…" rows={3}
              style={{ width: '100%', padding: 8, border: '1px solid var(--line)',
                borderRadius: 8, fontSize: 13 }} />
            <DetailRow task={task} refresh={async () => { await refresh(); open(task.id) }} setErr={setErr} />

            <h4 style={h4}>Linked notes</h4>
            {task.notes.map((n) => (
              <div key={n.note_id} className="mem-ref">
                📝 {n.path}
                <button className="mini-btn" style={{ marginLeft: 8 }}
                  onClick={async () => {
                    await tasksApi.unlinkNote(task.id, n.note_id)
                    open(task.id); refresh()
                  }}>unlink</button>
              </div>
            ))}
            <LinkNote taskId={task.id} onDone={() => { open(task.id); refresh() }} setErr={setErr} />

            <h4 style={h4}>Recurrence</h4>
            <RecurrenceEditor task={task} occ={occ}
              onDone={() => { open(task.id); refresh() }} setErr={setErr} />

            <h4 style={h4}>Reminders</h4>
            {reminders.filter((r) => r.status !== 'dismissed').map((r) => (
              <div key={r.id} className="mem-ref">
                ⏰ {r.fire_at ? new Date(r.fire_at).toLocaleString() : 'no time'} · {r.status}
                {r.snoozed_until ? ` (snoozed to ${new Date(r.snoozed_until).toLocaleString()})` : ''}
                <button className="mini-btn" style={{ marginLeft: 6 }}
                  onClick={async () => {
                    await tasksApi.snoozeReminder(r.id, Date.now() + 3600000)
                    open(task.id)
                  }}>+1h</button>
                <button className="mini-btn" style={{ marginLeft: 4 }}
                  onClick={async () => {
                    await tasksApi.dismissReminder(r.id)
                    open(task.id)
                  }}>dismiss</button>
              </div>
            ))}
            <button className="mini-btn"
              onClick={async () => {
                await tasksApi.createReminder(task.id, Date.now() + 3600000)
                open(task.id)
              }}>+ remind in 1h</button>

            <h4 style={h4}>Let AI do this</h4>
            {execState === 'idle' && (
              <button className="btn primary sm" onClick={runAI}>Let AI do this</button>
            )}
            {execState === 'running' && (
              <div className="notice">AI is working… progress streams in the chat view.</div>
            )}
            {execState === 'verifying' && (
              <div className="pending">
                <div className="pending-q">Execution finished. Verify the criteria before completing:</div>
                <input value={evidence} onChange={(e) => setEvidence(e.target.value)}
                  placeholder="Evidence: what changed, exit codes, files…"
                  style={{ width: '100%', padding: '7px 9px', border: '1px solid var(--line)',
                    borderRadius: 8, marginBottom: 8 }} />
                <div className="pending-row">
                  <button className="btn primary sm" disabled={!evidence.trim()}
                    onClick={() => verify(true)}>Verified — complete task</button>
                  <button className="btn sm" onClick={() => verify(false)}>Not done</button>
                </div>
              </div>
            )}
            {execState === 'done' && <div className="muted">Attempt recorded. See history below.</div>}
          </div>
        )}
      </div>
    </div>
  )
}

function DetailRow({ task, refresh, setErr }: {
  task: Task; refresh: () => void; setErr: (s: string) => void
}) {
  const [prio, setPrio] = useState(task.priority)
  const [due, setDue] = useState(task.due_at
    ? new Date(task.due_at).toISOString().slice(0, 16) : '')
  useEffect(() => {
    setPrio(task.priority)
    setDue(task.due_at ? new Date(task.due_at).toISOString().slice(0, 16) : '')
  }, [task.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    try {
      await tasksApi.patch(task.id, {
        priority: prio, due_at: due ? new Date(due).getTime() : 0,
      } as any)
      refresh()
    } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
  }
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '10px 0', flexWrap: 'wrap' }}>
      <select value={prio} onChange={(e) => setPrio(e.target.value as any)}
        style={{ padding: 6, borderRadius: 6, border: '1px solid var(--line)' }}>
        {['critical', 'high', 'normal', 'low'].map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)}
        style={{ padding: 6, borderRadius: 6, border: '1px solid var(--line)' }} />
      <button className="mini-btn" onClick={save}>Save</button>
      {(NEXT[task.status] || []).map((s) => (
        <button key={s} className="mini-btn"
          onClick={async () => {
            try { await tasksApi.transition(task.id, s as any); refresh() } catch (e: any) {
              setErr(String(e.message || e).slice(0, 300))
            }
          }}>{s.replace('_', ' ')}</button>
      ))}
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
        placeholder="link note: type title…" list="note-suggest"
        style={{ flex: 1, padding: '6px 8px', border: '1px solid var(--line)', borderRadius: 8 }} />
      <datalist id="note-suggest">
        {opts.map((o) => <option key={o.path} value={o.path}>{o.title}</option>)}
      </datalist>
      <button className="mini-btn" onClick={async () => {
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
          style={{ padding: 6, borderRadius: 6, border: '1px solid var(--line)' }}>
          <option value="">none</option>
          <option value="daily">daily</option>
          <option value="weekdays">weekdays</option>
          <option value="weekly">weekly</option>
          <option value="monthly">monthly</option>
          <option value="interval:2">every 2 days</option>
          <option value="interval:7">every 7 days</option>
        </select>
        <button className="mini-btn" onClick={async () => {
          try {
            await tasksApi.patch(task.id, { recurrence: rec } as any)
            if (rec) await tasksApi.genOccurrences(task.id, 30)
            onDone()
          } catch (e: any) { setErr(String(e.message || e).slice(0, 300)) }
        }}>Save + generate</button>
      </div>
      {occ.length > 0 && (
        <div className="muted small" style={{ marginTop: 4 }}>
          {occ.length} occurrences: {occ.slice(0, 5).map((o) => o.occurrence_key).join(', ')}
          {occ.length > 5 ? '…' : ''}
        </div>
      )}
    </div>
  )
}

const h4: React.CSSProperties = {
  margin: '14px 0 6px', fontSize: 12, textTransform: 'uppercase',
  letterSpacing: '0.05em', color: 'var(--muted)',
}
