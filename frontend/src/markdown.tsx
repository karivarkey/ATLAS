import React, { useState } from 'react'

// Minimal streaming-safe markdown: code fences (partial ok), inline code,
// bold, italic, links, headings, lists, tables. No raw HTML is ever injected.
export function Markdown({ text }: { text: string }) {
  const blocks = splitBlocks(text)
  return (
    <div className="md">
      {blocks.map((b, i) => (
        <Block key={i} block={b} />
      ))}
    </div>
  )
}

type Block =
  | { kind: 'code'; lang: string; code: string; open: boolean }
  | { kind: 'table'; rows: string[][] }
  | { kind: 'list'; items: string[] }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'para'; text: string }

function splitBlocks(text: string): Block[] {
  const lines = text.split('\n')
  const out: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.trimStart().startsWith('```')) {
      const lang = line.trimStart().slice(3).trim()
      const buf: string[] = []
      i++
      let open = true
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        buf.push(lines[i])
        i++
      }
      if (i < lines.length) { open = false; i++ }
      out.push({ kind: 'code', lang, code: buf.join('\n'), open })
      continue
    }
    const h = /^(#{1,4})\s+(.*)/.exec(line)
    if (h) {
      out.push({ kind: 'heading', level: h[1].length, text: h[2] })
      i++
      continue
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''))
        i++
      }
      out.push({ kind: 'list', items })
      continue
    }
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      const rows: string[][] = [cells(line)]
      i += 2
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(cells(lines[i]))
        i++
      }
      out.push({ kind: 'table', rows })
      continue
    }
    if (line.trim() === '') { i++; continue }
    const buf = [line]
    i++
    while (i < lines.length && lines[i].trim() !== '' && !lines[i].trimStart().startsWith('```')) {
      buf.push(lines[i])
      i++
    }
    out.push({ kind: 'para', text: buf.join('\n') })
  }
  return out
}

function cells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
}

function Block({ block }: { block: Block }) {
  if (block.kind === 'code') return <CodeBlock lang={block.lang} code={block.code} open={block.open} />
  if (block.kind === 'heading') {
    const Tag = `h${block.level}` as 'h1' | 'h2' | 'h3' | 'h4'
    return <Tag className="md-h"><Inline text={block.text} /></Tag>
  }
  if (block.kind === 'list') {
    return (
      <ul className="md-ul">
        {block.items.map((it, i) => (
          <li key={i}><Inline text={it} /></li>
        ))}
      </ul>
    )
  }
  if (block.kind === 'table') {
    return (
      <div className="md-tablewrap">
        <table className="md-table">
          <tbody>
            {block.rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j}><Inline text={c} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  return (
    <p className="md-p"><Inline text={block.text} /></p>
  )
}

function CodeBlock({ lang, code, open }: { lang: string; code: string; open: boolean }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span>{lang || 'code'}{open ? ' · streaming' : ''}</span>
        <button
          className="mini-btn"
          onClick={() => {
            navigator.clipboard.writeText(code).catch(() => {})
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  )
}

// Inline formatting without dangerouslySetInnerHTML.
export function Inline({ text }: { text: string }) {
  const parts: React.ReactNode[] = []
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let k = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(<span key={k++}>{text.slice(last, m.index)}</span>)
    const tok = m[0]
    if (tok.startsWith('`')) {
      parts.push(<code key={k++} className="inline-code">{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('**')) {
      parts.push(<strong key={k++}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('*')) {
      parts.push(<em key={k++}>{tok.slice(1, -1)}</em>)
    } else {
      const lm = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok)!
      const href = lm[2]
      const safe = /^https?:\/\//.test(href) ? href : '#'
      parts.push(
        <a key={k++} href={safe} target="_blank" rel="noopener noreferrer">{lm[1]}</a>,
      )
    }
    last = m.index + tok.length
  }
  if (last < text.length) parts.push(<span key={k++}>{text.slice(last)}</span>)
  return <>{parts}</>
}
