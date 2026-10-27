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

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open ])

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
      <button className="icon-btn" title="Notifications" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((o) => !o)}
        style={{ position: 'relative' }}>
        <Bell size={16} />
        {unread > 0 && (
          <span className="badge" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>
        )}
      </button>
      {open && (
        <>
          <div className="notif-backdrop" onClick={() => setOpen(false)} />
          <div className="notif-pop" role="dialog" aria-label="Notifications"
            onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}>
            <div className="notif-head">
              <span>Notifications {unread > 0 && `(${unread} unread)`}</span>
              <button className="mini-btn" onClick={() => setOpen(false)}>close</button>
            </div>
            {items.filter((n) => n.status === 'unread').length === 0 &&
              <div className="muted pad small">All caught up.</div>}
            {items.map((n) => (
              <div key={n.id} className={`notif-item ${n.status === 'unread' ? 'unread' : 'read'}`}>
                <div className="notif-item-t">
                  {n.status === 'unread' ? '● ' : ''}{n.title}
                </div>
                {n.body && <div className="muted small">{n.body}</div>}
                <div className="notif-actions">
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
        </>
      )}
    </div>
  )
}
