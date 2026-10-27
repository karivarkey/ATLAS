import React, { useState } from 'react'

// Minimal streaming-safe markdown: code fences (partial ok), inline code,
// bold, italic, links, headings, lists, tables. No raw HTML is ever injected.
export function stripFrontmatter(text: string): string {
  const t = (text || '').replace(/^\ufeff/, '')
  // compact single-line: --- id: x ---  (legacy clients wrote these)
  if (t.startsWith('---') && !t.startsWith('---\n') && !t.startsWith('---\r')) {
    const nl = t.indexOf('\n')
    const first = nl < 0 ? t : t.slice(0, nl)
    let inner = first.trim().slice(3).trim()
    if (inner.endsWith('---')) inner = inner.slice(0, -3).trim()
    if (/^[A-Za-z0-9_-]+\s*:/.test(inner)) {
      return nl < 0 ? '' : t.slice(nl + 1).replace(/^\n+/, '')
    }
    return t
  }
  if (!t.startsWith('---\n') && !t.startsWith('---\r')) return t
  const lines = t.split('\n')
  for (let i = 1; i < Math.min(lines.length, 60); i++) {
    if (lines[i].trim() === '---' || lines[i].trim() === '...') {
      // only treat as frontmatter if lines[1..i) look like key: value
      const fmLines = lines.slice(1, i).filter((l) => l.trim() && !l.trim().startsWith('#'))
      if (fmLines.length > 0 && fmLines.every((l) => /^\s*[A-Za-z0-9_-]+\s*:/.test(l))) {
        return lines.slice(i + 1).join('\n').replace(/^\n+/, '')
      }
      break
    }
  }
  return t
}

export function Markdown({ text, onOpenNote }: { text: string; onOpenNote?: (path: string) => void }) {
  const blocks = dedupe(splitBlocks(stripFrontmatter(text)))
  return (
    <div className="md">
      {blocks.map((b, i) => (
        <Block key={i} block={b} onOpenNote={onOpenNote} />
      ))}
    </div>
  )
}

// Drop exact-duplicate consecutive blocks (models sometimes repeat a
// summary across steps; the persisted message keeps everything, the
// view stays readable).
function dedupe(blocks: Block[]): Block[] {
  const out: Block[] = []
  let prev = ''
  for (const b of blocks) {
    const key = JSON.stringify(b)
    if (key === prev) continue
    prev = key
    out.push(b)
  }
  return out
}

// Drop consecutively repeated sentences inside a paragraph
// ("Got it, Gee. Got it, Gee." → "Got it, Gee.").
export function dedupeSentences(text: string): string {
  const parts = text.split(/(?<=[.!?…])\s+(?=[A-Z0-9"'“(\[])/)
  const out: string[] = []
  let prev = ''
  for (const p of parts) {
    const key = p.trim().toLowerCase()
    if (key && key === prev) continue
    prev = key
    out.push(p)
  }
  return out.join(' ').replace(/\s{2,}/g, ' ')
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

function Block({ block, onOpenNote }: { block: Block; onOpenNote?: (path: string) => void }) {
  if (block.kind === 'code') return <CodeBlock lang={block.lang} code={block.code} open={block.open} />
  if (block.kind === 'heading') {
    const Tag = `h${block.level}` as 'h1' | 'h2' | 'h3' | 'h4'
    return <Tag className="md-h"><Inline text={block.text} onOpenNote={onOpenNote} /></Tag>
  }
  if (block.kind === 'list') {
    return (
      <ul className="md-ul">
        {block.items.map((it, i) => (
          <li key={i}><Inline text={it} onOpenNote={onOpenNote} /></li>
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
                  <td key={j}><Inline text={c} onOpenNote={onOpenNote} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  return (
    <p className="md-p"><Inline text={block.text} onOpenNote={onOpenNote} /></p>
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
          aria-label={copied ? 'Copied' : `Copy ${lang || 'code'} to clipboard`}
          onClick={() => {
            navigator.clipboard.writeText(code).catch(() => {})
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          }}
        >
          {copied ? 'Copied ✓' : 'Copy'}
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  )
}

// Inline formatting without dangerouslySetInnerHTML.
export function Inline({ text, onOpenNote }: { text: string; onOpenNote?: (path: string) => void }) {
  const parts: React.ReactNode[] = []
  const re = /(`[^`]+`|\[\[[^\]]+\]\]|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let k = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(<span key={k++}>{text.slice(last, m.index)}</span>)
    const tok = m[0]
    if (tok.startsWith('[[')) {
      const inner = tok.slice(2, -2)
      const aliasSplit = inner.split('|')
      const target = aliasSplit[0].split('#')[0].trim()
      const label = aliasSplit[1]?.trim() || aliasSplit[0].trim()
      parts.push(
        <button key={k++} className="md-wikilink" title={target}
          onClick={(e) => {
            e.stopPropagation()
            if (onOpenNote && target) {
              const p = target.toLowerCase().endsWith('.md') ? target : `${target}.md`
              onOpenNote(p)
            }
          }}>
          {label}
        </button>,
      )
    } else if (tok.startsWith('`')) {
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
