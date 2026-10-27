import {
  AlertTriangle, Brain, CheckCircle2, ChevronDown, ChevronRight, CircleDashed,
  ClipboardList, FileDiff, FileText, Globe, History, Loader2, Database,
  Search, Terminal, Wrench, XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { ToolCall } from './store'

const ICONS: Record<string, any> = {
  fs_read: FileDiff, fs_write: FileDiff, fs_edit: FileDiff,
  fs_list: Search, fs_glob: Search, fs_grep: Search,
  shell_exec: Terminal, git_clone: Terminal, git_status: Terminal,
  web_search: Search, web_fetch: Globe, web_research: Globe,
  search_memory: Brain, search_chat_history: History,
  get_source_message: FileText, get_conversation_context: FileText,
  get_memory_record: Database,
  note_read: FileText, note_write: FileDiff,
  task_get: ClipboardList, task_update: ClipboardList,
  ask_user: AlertTriangle,
}

export function ToolCard({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const Icon = ICONS[tool.name] || Wrench
  const color =
    tool.status === 'done' ? 'var(--ok)' :
    tool.status === 'failed' ? 'var(--err)' :
    tool.status === 'approval' ? 'var(--warn)' : 'var(--accent)'
  const stateLabel = tool.status === 'done' ? 'completed' : tool.status === 'failed' ? 'failed' : tool.status === 'approval' ? 'needs approval' : 'running'
  return (
    <div className={`toolcard ${tool.status}`}>
      <button className="toolcard-head" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={`${tool.name}, ${stateLabel}`}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Icon size={14} style={{ color }} aria-hidden="true" />
        <span className="toolcard-name">{tool.name}</span>
        <span className="toolcard-summary">{tool.summary}</span>
        <span className="toolcard-state">
          {tool.status === 'running' && <Loader2 size={13} className="spin" aria-label="running" />}
          {tool.status === 'done' && <CheckCircle2 size={13} style={{ color: 'var(--ok)' }} aria-label="done" />}
          {tool.status === 'failed' && <XCircle size={13} style={{ color: 'var(--err)' }} aria-label="failed" />}
          {tool.status === 'approval' && <CircleDashed size={13} style={{ color: 'var(--warn)' }} aria-label="approval" />}
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
          {tool.output && (
            <div><button className="mini-btn" onClick={() => {
              navigator.clipboard.writeText(tool.output).catch(() => {})
              setCopied(true); setTimeout(() => setCopied(false), 1200)
            }}>{copied ? 'Copied' : 'Copy output'}</button></div>
          )}
          {!tool.output && !tool.diff && <div className="muted small">Running…</div>}
        </div>
      )}
    </div>
  )
}
