import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'

/* ---------- Toasts (replaces alert()) ---------- */
export interface Toast { id: number; kind: 'success' | 'error' | 'info' | 'warn'; title: string; desc?: string }
let toastId = 1
const ToastCtx = createContext<{ push: (t: Omit<Toast, 'id'>) => void }>({ push: () => {} })
export const useToast = () => useContext(ToastCtx)

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const timers = useRef<Map<number, number>>(new Map())
  const push = useCallback((t: Omit<Toast, 'id'>) => {
    const id = toastId++
    setItems((xs) => [...xs.slice(-4), { ...t, id }])
    const ms = t.kind === 'error' ? 7000 : 4200
    const h = window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), ms)
    timers.current.set(id, h)
  }, [])
  useEffect(() => () => { timers.current.forEach((h) => clearTimeout(h)) }, [])
  const dismiss = (id: number) => setItems((xs) => xs.filter((x) => x.id !== id))
  const Icon = (k: Toast['kind']) =>
    k === 'success' ? <CheckCircle2 size={15} style={{ color: 'var(--ok)' }} /> :
    k === 'error' ? <XCircle size={15} style={{ color: 'var(--err)' }} /> :
    k === 'warn' ? <AlertTriangle size={15} style={{ color: 'var(--warn)' }} /> :
    <Info size={15} style={{ color: 'var(--accent)' }} />
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="toasts" role="status" aria-live="polite" aria-atomic="false">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {Icon(t.kind)}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="toast-t">{t.title}</div>
              {t.desc && <div className="toast-d">{t.desc}</div>}
            </div>
            <button className="icon-btn" aria-label="Dismiss notification" onClick={() => dismiss(t.id)}>
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

/* ---------- Confirm dialog (replaces confirm()) ---------- */
export function useConfirm() {
  const [req, setReq] = useState<{ title: string; body?: string; danger?: boolean; resolve: (v: boolean) => void } | null>(null)
  const prevFocus = useRef<HTMLElement | null>(null)
  const ask = useCallback((title: string, body?: string, danger?: boolean) => {
    prevFocus.current = document.activeElement as HTMLElement | null
    return new Promise<boolean>((resolve) => setReq({ title, body, danger, resolve }))
  }, [])
  const close = (v: boolean) => {
    req?.resolve(v)
    setReq(null)
    prevFocus.current?.focus?.()
  }
  const dialog = req ? (
    <div className="modal-back" onClick={() => close(false)}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-label={req.title}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') close(false) }}>
        <div className="modal-head"><h3>{req.title}</h3></div>
        {req.body && <p className="muted" style={{ margin: '4px 0 8px', fontSize: 13 }}>{req.body}</p>}
        <div className="modal-foot">
          <button className="btn" autoFocus onClick={() => close(false)}>Cancel</button>
          <button className={`btn ${req.danger ? 'danger' : 'primary'}`} onClick={() => close(true)}>
            {req.danger ? 'Delete' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  ) : null
  return { ask, dialog }
}

/* ---------- Small primitives ---------- */
export function Skeleton({ lines = 3, block = false }: { lines?: number; block?: boolean }) {
  return (
    <div aria-hidden="true" aria-label="Loading">
      {block && <div className="skel skel-block" />}
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skel skel-line" style={{ width: `${92 - i * 13}%` }} />
      ))}
    </div>
  )
}

export function EmptyState({ icon, title, body, action }: {
  icon?: React.ReactNode; title: string; body?: string; action?: React.ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-card">
        {icon && <span className="empty-icon">{icon}</span>}
        <h3>{title}</h3>
        {body && <p>{body}</p>}
        {action}
      </div>
    </div>
  )
}

export function ErrorState({ title = 'Something failed', body, onRetry }: {
  title?: string; body?: string; onRetry?: () => void
}) {
  return (
    <div className="errorbox" role="alert">
      <XCircle size={14} />
      <span><b>{title}</b>{body ? ` — ${body}` : ''}</span>
      {onRetry && <button className="btn sm" onClick={onRetry}>Retry</button>}
    </div>
  )
}

/* ---------- Theme ---------- */
export function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try {
      const s = localStorage.getItem('atlas.theme')
      if (s === 'light' || s === 'dark') return s
      return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    } catch { return 'light' }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('atlas.theme', theme) } catch {}
  }, [theme])
  return { theme, toggle: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')) }
}
