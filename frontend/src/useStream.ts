import { useEffect, useRef } from 'react'
import { api } from './api'
import { useStore } from './store'

// Multiplexed SSE subscription keyed by execution id. Reconnects with
// replay from the last acknowledged seq; never restarts the backend task.
export function useExecutionStream(convId: string | null, executionId: string | null) {
  const applyEvent = useStore((s) => s.applyEvent)
  const lastSeq = useRef(0)
  const doneRef = useRef(false)

  useEffect(() => {
    if (!convId || !executionId) return
    doneRef.current = false
    lastSeq.current = 0
    let es: EventSource | null = null
    let closed = false
    let retries = 0

    const connect = async () => {
      if (closed || doneRef.current) return
      // First, replay anything missed via plain fetch (dedupe by seq).
      try {
        const { events } = await api.executionEvents(executionId, lastSeq.current)
        for (const ev of events) {
          if (ev.seq > lastSeq.current) {
            lastSeq.current = ev.seq
            applyEvent(convId, ev)
            if (['turn_completed', 'turn_failed', 'cancelled'].includes(ev.type)) {
              doneRef.current = true
            }
          }
        }
        if (doneRef.current) {
          try { await useStore.getState().loadConversations() } catch { /* ignore */ }
          // Refresh persisted messages so the final assistant text is durable,
          // and drop the streamed text (the persisted message now owns it —
          // rendering both would duplicate the answer).
          const { messages } = await api.listMessages(convId)
          useStore.setState((s) => ({
            chats: { ...s.chats, [convId]: { ...(s.chats[convId] || { messages: [], stream: null,
              execution: null, scrollKey: 0, completedAt: null }), messages,
              execution: null,
              stream: s.chats[convId]?.stream
                ? { ...s.chats[convId]!.stream!, text: '' }
                : null,
              completedAt: Date.now(),
              scrollKey: (s.chats[convId]?.scrollKey || 0) + 1 } },
          }))
          return
        }
      } catch (e) {
        console.warn('replay failed', e)
      }
      es = new EventSource(`/api/v1/executions/${executionId}/stream?from_seq=${lastSeq.current}`)
      es.onmessage = async (msg) => {
        if (!msg.data || msg.data.startsWith(':')) return
        try {
          const ev = JSON.parse(msg.data)
          if (typeof ev.seq === 'number') {
            if (ev.seq <= lastSeq.current) return // dedupe
            lastSeq.current = ev.seq
            applyEvent(convId, ev)
            retries = 0
            if (['turn_completed', 'turn_failed', 'cancelled'].includes(ev.type)) {
              doneRef.current = true
              es?.close()
              try { await useStore.getState().loadConversations() } catch { /* ignore */ }
              const { messages } = await api.listMessages(convId)
              useStore.setState((s) => ({
                chats: { ...s.chats, [convId]: { ...(s.chats[convId]!),
                  messages, execution: null,
                  stream: s.chats[convId]!.stream
                    ? { ...s.chats[convId]!.stream!, text: '' }
                    : null,
                  completedAt: Date.now(),
                  scrollKey: s.chats[convId]!.scrollKey + 1 } },
              }))
            }
          }
        } catch {
          /* ignore malformed frames */
        }
      }
      es.onerror = () => {
        es?.close()
        if (closed || doneRef.current) return
        retries += 1
        const backoff = Math.min(1000 * 2 ** retries, 10000)
        setTimeout(connect, backoff)
      }
    }
    connect()
    return () => {
      closed = true
      es?.close()
    }
  }, [convId, executionId, applyEvent])
}
