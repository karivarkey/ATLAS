import React, { useEffect, useState } from 'react'
import { Bell } from 'lucide-react'
import { Notification, tasksApi } from './notesApi'

interface Props {
  onOpenTask?: (taskId: string) => void
  onOpenNote?: (path: string) => void
  pollMs?: number
}

/** Notification bell: unread badge, dropdown, mark read/dismiss, deep links. */
export function Notifications({ onOpenTask, onOpenNote, pollMs = 15000 }: Props) {
  const [unread, setUnread] = useState(0)
  const [items, setItems] = useState<Notification[]>([])
  const [open, setOpen] = useState(false)

  const refresh = async () => {
    try {
      setUnread((await tasksApi.unreadCount()).unread)
      if (open) setItems((await tasksApi.notifications()).notifications.slice(0, 30))
    } catch { /* bell must never break the app */ }
  }

  useEffect(() => {
    refresh()
    const t = window.setInterval(refresh, pollMs)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pollMs])

  const act = async (n: Notification, kind: 'read' | 'dismiss') => {
    if (kind === 'read') await tasksApi.markRead(n.id)
    else await tasksApi.dismiss(n.id)
    refresh()
  }

  const jump = (n: Notification) => {
    if (n.task_id && onOpenTask) onOpenTask(n.task_id)
    else if (n.note_id && onOpenNote) onOpenNote(n.note_id)
    setOpen(false)
  }

  return (
    <div style={{ position: 'relative' }}>
      <button className="icon-btn" title="Notifications" onClick={() => setOpen((o) => !o)}
        style={{ position: 'relative' }}>
        <Bell size={16} />
        {unread > 0 && (
          <span style={{ position: 'absolute', top: -4, right: -6, background: 'var(--err)',
            color: '#fff', fontSize: 10, borderRadius: 10, padding: '0 5px',
            minWidth: 16, textAlign: 'center' }}>{unread > 99 ? '99+' : unread}</span>
        )}
      </button>
      {open && (
        <div style={{ position: 'absolute', right: 0, top: 34, width: 340, maxHeight: 420,
          overflowY: 'auto', background: 'var(--panel)', border: '1px solid var(--line)',
          borderRadius: 12, boxShadow: '0 12px 40px rgba(0,0,0,0.14)', zIndex: 80, padding: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '2px 6px 8px' }}>
            <b>Notifications {unread > 0 && `(${unread} unread)`}</b>
            <button className="mini-btn" onClick={() => setOpen(false)}>close</button>
          </div>
          {items.filter((n) => n.status === 'unread').length === 0 &&
            <div className="muted pad small">All caught up.</div>}
          {items.map((n) => (
            <div key={n.id} className="mem-result"
              style={{ opacity: n.status === 'unread' ? 1 : 0.65, marginBottom: 6, padding: 8 }}>
              <div style={{ fontSize: 13, fontWeight: n.status === 'unread' ? 600 : 400 }}>
                {n.status === 'unread' ? '● ' : ''}{n.title}
              </div>
              {n.body && <div className="muted small">{n.body}</div>}
              <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                {(n.task_id || n.note_id) && (
                  <button className="mini-btn" onClick={() => jump(n)}>open</button>
                )}
                {n.status === 'unread' && (
                  <button className="mini-btn" onClick={() => act(n, 'read')}>mark read</button>
                )}
                {n.status !== 'dismissed' && (
                  <button className="mini-btn" onClick={() => act(n, 'dismiss')}>dismiss</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
