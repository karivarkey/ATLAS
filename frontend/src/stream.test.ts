import { describe, expect, it } from 'vitest'

// Pure-logic tests for streaming event routing (mirrors store.applyEvent)
// and SSE dedupe semantics. No DOM needed.

type Tool = { name: string; status: string; output: string }
interface S { text: string; tools: Tool[]; status: string; pending: Record<string, unknown> }

function apply(stream: S, ev: { type: string; payload: any }): S {
  const p = ev.payload || {}
  if (ev.type === 'text_delta') return { ...stream, text: stream.text + (p.delta || '') }
  if (ev.type === 'tool_started')
    return { ...stream, tools: [...stream.tools, { name: p.name, status: 'running', output: '' }] }
  if (ev.type === 'tool_completed' || ev.type === 'tool_failed') {
    const tools = [...stream.tools]
    const i = tools.map((x) => x.status).lastIndexOf('running')
    if (i >= 0) tools[i] = { ...tools[i], status: ev.type === 'tool_completed' ? 'done' : 'failed',
                             output: p.output || p.error || '' }
    return { ...stream, tools }
  }
  if (ev.type === 'question_asked') return { ...stream, status: 'waiting_input', pending: p }
  if (ev.type === 'status_changed') return { ...stream, status: p.to }
  return stream
}

describe('stream event routing', () => {
  it('accumulates text deltas', () => {
    let s: S = { text: '', tools: [], status: 'running', pending: {} }
    s = apply(s, { type: 'text_delta', payload: { delta: 'hel' } })
    s = apply(s, { type: 'text_delta', payload: { delta: 'lo' } })
    expect(s.text).toBe('hello')
  })

  it('pairs tool start/complete without crossing conversations', () => {
    let a: S = { text: '', tools: [], status: 'running', pending: {} }
    let b: S = { text: '', tools: [], status: 'running', pending: {} }
    a = apply(a, { type: 'tool_started', payload: { name: 'fs_read' } })
    b = apply(b, { type: 'tool_started', payload: { name: 'shell_exec' } })
    a = apply(a, { type: 'tool_completed', payload: { output: 'A-out' } })
    expect(a.tools[0]).toMatchObject({ name: 'fs_read', status: 'done', output: 'A-out' })
    expect(b.tools[0].status).toBe('running')
    expect(b.tools[0].output).toBe('')
  })

  it('dedupe by seq keeps replay idempotent', () => {
    const seen = new Set<number>()
    const ingest = (seq: number) => {
      if (seen.has(seq)) return false
      seen.add(seq)
      return true
    }
    expect([1, 2, 2, 3, 1].filter(ingest)).toEqual([1, 2, 3])
  })

  it('question flow preserves status transitions', () => {
    let s: S = { text: '', tools: [], status: 'running', pending: {} }
    s = apply(s, { type: 'question_asked', payload: { question: 'which repo?' } })
    expect(s.status).toBe('waiting_input')
    s = apply(s, { type: 'status_changed', payload: { to: 'running' } })
    expect(s.status).toBe('running')
  })

  it('dedupeSentences collapses consecutive repeats', async () => {
    const { dedupeSentences } = await import('./markdown')
    expect(dedupeSentences('Got it, Gee. Got it, Gee. Got it, Gee.')).toBe('Got it, Gee.')
    expect(dedupeSentences('Hello! How are you?')).toBe('Hello! How are you?')
  })
})
