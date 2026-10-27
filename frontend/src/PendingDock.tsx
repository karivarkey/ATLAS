import { AlertTriangle, Check, X } from 'lucide-react'
import { useState } from 'react'
import { api } from './api'

export interface PendingOption {
  id: string
  label: string
}

export interface PendingPayload {
  question?: string
  options?: PendingOption[]
  kind?: string
  tool?: string
  arguments?: Record<string, any>
  reason?: string
  summary?: string
}

/** Pure routing helper: classify a pending payload for the dock. No DOM needed. */
export function describePending(status: string, pending: PendingPayload) {
  if (status === 'waiting_approval') {
    return {
      kind: 'approval' as const,
      options: [] as PendingOption[],
      question: pending.summary || pending.tool || 'Approval required',
    }
  }
  const options = Array.isArray(pending.options) ? pending.options.slice(0, 6) : []
  return { kind: 'question' as const, options, question: pending.question || 'Input needed' }
}

function commandSummary(pending: PendingPayload): string {
  const t = pending.tool || ''
  const a = pending.arguments || {}
  if (t === 'shell_exec' && a.command) return `$ ${String(a.command).slice(0, 300)}`
  if ((t === 'fs_read' || t === 'fs_write' || t === 'fs_edit') && a.path) {
    return `${t} ${String(a.path).slice(0, 200)}`
  }
  return pending.summary || t || 'Approval required'
}

/**
 * Single decision dock, pinned ABOVE the composer (not inline in the message
 * list). Always uses the conversation-scoped endpoints so the server resolves
 * the actually-waiting execution — never a stale execution id.
 */
export function PendingDock({
  convId,
  status,
  pending,
}: {
  convId: string
  status: string
  pending: PendingPayload
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  if (status !== 'waiting_input' && status !== 'waiting_approval') return null

  const send = async (value: string) => {
    const v = value.trim()
    if (!v || busy) return
    setBusy(true)
    setError('')
    try {
      await api.answerConversation(convId, v)
      setText('')
    } catch (e: any) {
      setError(`Send failed: ${String(e?.message || e).slice(0, 200)}`)
    } finally {
      setBusy(false)
    }
  }

  const decide = async (approved: boolean) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await api.approveConversation(convId, approved)
    } catch (e: any) {
      setError(`Decision failed: ${String(e?.message || e).slice(0, 200)}`)
    } finally {
      setBusy(false)
    }
  }

  if (status === 'waiting_approval') {
    return (
      <div className="dock" data-testid="pending-dock-approval" role="alertdialog" aria-label="Approval required">
        <div className="dock-q">
          <AlertTriangle size={14} aria-hidden="true" /> Approval required: {commandSummary(pending)}
        </div>
        {pending.reason && <div className="muted small">{pending.reason}</div>}
        {error && <div className="dock-error" role="alert">{error}</div>}
        <div className="dock-row">
          <button className="btn primary sm" disabled={busy} onClick={() => decide(true)}>
            <Check size={14} /> Approve
          </button>
          <button className="btn sm" disabled={busy} onClick={() => decide(false)}>
            <X size={14} /> Deny
          </button>
        </div>
      </div>
    )
  }

  const view = describePending(status, pending)
  return (
    <div className="dock" data-testid="pending-dock-question" role="alertdialog" aria-label="Input needed">
      <div className="dock-q">
        <AlertTriangle size={14} aria-hidden="true" /> {view.question}
      </div>
      {view.options.length > 0 && (
        <div className="dock-row">
          {view.options.map((o) => (
            <button key={o.id} className="btn sm" disabled={busy} onClick={() => send(o.label)}>
              {o.label}
            </button>
          ))}
        </div>
      )}
      {error && <div className="dock-error" role="alert">{error}</div>}
      <div className="dock-row">
        <label className="sr-only" htmlFor="pending-answer">Your answer</label>
        <input
          id="pending-answer"
          placeholder="Type your answer… (free text always works)"
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && text.trim()) send(text)
          }}
        />
        <button
          className="btn primary sm"
          disabled={busy || !text.trim()}
          onClick={() => send(text)}
        >
          Send
        </button>
      </div>
    </div>
  )
}
