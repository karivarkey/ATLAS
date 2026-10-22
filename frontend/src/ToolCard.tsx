import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, CircleDashed,
  FileDiff, Globe, Loader2, Search, Terminal, Wrench, XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { ToolCall } from './store'

const ICONS: Record<string, any> = {
  fs_read: FileDiff, fs_write: FileDiff, fs_edit: FileDiff,
  fs_list: Search, fs_glob: Search, fs_grep: Search,
  shell_exec: Terminal, git_clone: Terminal, git_status: Terminal,
  web_search: Search, web_fetch: Globe,
  ask_user: AlertTriangle,
}

export function ToolCard({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false)
  const Icon = ICONS[tool.name] || Wrench
  const color =
    tool.status === 'done' ? 'var(--ok)' :
    tool.status === 'failed' ? 'var(--err)' :
    tool.status === 'approval' ? 'var(--warn)' : 'var(--accent)'
  return (
    <div className={`toolcard ${tool.status}`}>
      <button className="toolcard-head" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Icon size={14} style={{ color }} />
        <span className="toolcard-name">{tool.name}</span>
        <span className="toolcard-summary">{tool.summary}</span>
        <span className="toolcard-state">
          {tool.status === 'running' && <Loader2 size={13} className="spin" />}
          {tool.status === 'done' && <CheckCircle2 size={13} style={{ color: 'var(--ok)' }} />}
          {tool.status === 'failed' && <XCircle size={13} style={{ color: 'var(--err)' }} />}
          {tool.status === 'approval' && <CircleDashed size={13} style={{ color: 'var(--warn)' }} />}
        </span>
      </button>
      {open && (
        <div className="toolcard-body">
          {tool.diff && (
            <pre className="toolcard-diff">{tool.diff}</pre>
          )}
          {tool.output && (
            <pre className="toolcard-output">{tool.output.slice(0, 6000)}</pre>
          )}
          {!tool.output && !tool.diff && <div className="muted">Running…</div>}
        </div>
      )}
    </div>
  )
}
