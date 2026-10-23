import { Check, Loader2, Send, Sparkles, X } from 'lucide-react'
import React, { useEffect, useRef, useState } from 'react'
import { api, ChatMessage } from './api'
import { Markdown, dedupeSentences } from './markdown'
import { useExecutionStream } from './useStream'

export function OnboardingWizard({ onDone }: { onDone: () => void }) {
  const [convId, setConvId] = useState<string | null>(null)
  const [executionId, setExecutionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  useExecutionStream(convId, executionId)

  useEffect(() => {
    api.onboardingStart()
      .then(async (r) => {
        setConvId(r.conversation_id)
        setExecutionId(r.execution_id)
        const { messages } = await api.listMessages(r.conversation_id)
        setMessages(messages)
      })
      .catch((e) => setError(String(e.message || e).slice(0, 200)))
  }, [])

  // Watch for completion: poll messages + onboarding status after each turn.
  useEffect(() => {
    if (!convId) return
    let alive = true
    const t = setInterval(async () => {
      try {
        const [{ messages }, st] = await Promise.all([
          api.listMessages(convId),
          api.onboardingStatus(),
        ])
        if (!alive) return
        setMessages(messages)
        if (st.onboarded) {
          setDone(true)
          clearInterval(t)
        }
      } catch { /* keep polling */ }
    }, 2500)
    return () => { alive = false; clearInterval(t) }
  }, [convId])

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages.length])

  const send = async () => {
    const text = draft.trim()
    if (!text || !convId || sending) return
    setDraft('')
    setSending(true)
    setError('')
    try {
      const r = await api.sendOnboardingMessage(convId, text)
      setExecutionId(r.execution_id)
      const { messages } = await api.listMessages(convId)
      setMessages(messages)
    } catch (e: any) {
      setError(String(e.message || e).slice(0, 200))
    } finally {
      setSending(false)
    }
  }

  const skip = async () => {
    await api.skipOnboarding().catch(() => {})
    onDone()
  }

  if (done) {
    return (
      <div className="modal-back">
        <div className="modal">
          <div className="modal-head"><h3><Sparkles size={15} /> You're all set!</h3></div>
          <p className="muted">I saved what I learned to your profile (Settings → USER.md, editable anytime).</p>
          <div className="modal-foot">
            <button className="btn primary" onClick={onDone}>Start using Atlas</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="modal-back">
      <div className="modal wide ob-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3><Sparkles size={15} /> Welcome to Atlas — let's get set up</h3>
          <button className="icon-btn" onClick={skip} title="Skip for now"><X size={15} /></button>
        </div>
        <div className="muted small">A minute of questions so I can actually help. Skip anytime.</div>
        {error && <div className="errorbox sm">{error}</div>}
        <div className="ob-msgs" ref={listRef}>
          {messages.filter((m) => m.role !== 'system').map((m) => (
            <div key={m.id} className={`msg ${m.role === 'user' ? 'user' : 'assistant'}`}>
              {m.role === 'user' ? (
                <div className="bubble user-bubble">{m.content}</div>
              ) : (
                <>
                  <div className="avatar">A</div>
                  <div className="bubble"><Markdown text={dedupeSentences(m.content)} /></div>
                </>
              )}
            </div>
          ))}
          {!messages.length && (
            <div className="muted pad"><Loader2 size={14} className="spin" /> Starting…</div>
          )}
        </div>
        <div className="composer-box ob-composer">
          <textarea
            rows={1}
            placeholder="Type your answer… (Enter to send)"
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
            <button className="mini-btn" onClick={skip}>Skip for now</button>
            <button className="btn primary sm" disabled={!draft.trim() || sending} onClick={send}>
              {sending ? <Loader2 size={14} className="spin" /> : <Send size={14} />} Send
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
