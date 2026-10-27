import React from 'react'
import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { describePending, PendingDock } from './PendingDock'

// Pure render/routing tests for the single decision dock. No DOM needed:
// react-dom/server renderToString runs in plain node.

describe('pending dock routing', () => {
  it('question with options renders option buttons + free-text input', () => {
    const html = renderToString(
      React.createElement(PendingDock, {
        convId: 'c1',
        status: 'waiting_input',
        pending: {
          question: 'Pick one?',
          options: [
            { id: 'a', label: 'Alpha' },
            { id: 'b', label: 'Beta' },
          ],
          kind: 'choice',
        },
      }),
    )
    expect(html).toContain('Pick one?')
    expect(html).toContain('Alpha')
    expect(html).toContain('Beta')
    expect(html).toContain('<input')
    expect(html).toContain('Send')
  })

  it('question without options still renders free-text input', () => {
    const html = renderToString(
      React.createElement(PendingDock, {
        convId: 'c1',
        status: 'waiting_input',
        pending: { question: 'Which repo?' },
      }),
    )
    expect(html).toContain('Which repo?')
    expect(html).toContain('<input')
  })

  it('approval renders Approve / Deny plus reason and command summary', () => {
    const html = renderToString(
      React.createElement(PendingDock, {
        convId: 'c1',
        status: 'waiting_approval',
        pending: {
          tool: 'shell_exec',
          arguments: { command: 'rm -rf /tmp/x' },
          reason: 'Command looks destructive and requires approval.',
          summary: 'Run `rm -rf /tmp/x`',
        },
      }),
    )
    expect(html).toContain('Approve')
    expect(html).toContain('Deny')
    expect(html).toContain('destructive')
    expect(html).toContain('rm -rf /tmp/x')
  })

  it('describePending classifies approval vs question', () => {
    expect(describePending('waiting_approval', { tool: 'shell_exec' }).kind).toBe('approval')
    const q = describePending('waiting_input', {
      question: 'q?',
      options: [{ id: 'a', label: 'A' }],
    })
    expect(q.kind).toBe('question')
    expect(q.options.length).toBe(1)
  })

  it('renders nothing when not waiting', () => {
    const html = renderToString(
      React.createElement(PendingDock, { convId: 'c1', status: 'running', pending: {} }),
    )
    expect(html).toBe('')
  })
})
