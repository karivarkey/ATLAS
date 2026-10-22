import { Check, FileText, History, RotateCcw, X } from 'lucide-react'
import React, { useEffect, useState } from 'react'
import { api } from './api'
import { Markdown } from './markdown'

/** Shared SOUL.md / USER.md viewer + editor, used in Settings and Memory view. */
export function MemoryFilePanel({ name, compact }: { name: string; compact?: boolean }) {
  const [content, setContent] = useState('')
  const [versions, setVersions] = useState<any[]>([])
  const [editing, setEditing] = useState(false)
  const [preview, setPreview] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const dirty = editing && draft !== content

  const load = async () => {
    try {
      const r = await api.memoryFile(name)
      setContent(r.content)
      setVersions(r.versions)
      if (!editing) setDraft(r.content)
      setError('')
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }
  useEffect(() => { load() }, [name])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      await api.saveMemoryFile(name, draft)
      setEditing(false)
      setPreview(false)
      await load()
    } catch (e: any) {
      setError(String(e.message || e).slice(0, 300))
    } finally {
      setSaving(false)
    }
  }

  const latest = versions[0]
  return (
    <div className="memfile-panel">
      <div className="mem-searchrow">
        <FileText size={14} />
        <b>{name}</b>
        {latest && (
          <span className="muted small">
            v{latest.version} · {latest.source} · {new Date(latest.created_at).toLocaleString()}
          </span>
        )}
        <span className="memfile-spacer" />
        {!editing && (
          <button className="mini-btn" onClick={() => { setDraft(content); setEditing(true) }}>
            Edit
          </button>
        )}
        {editing && (
          <>
            <button
              className={`mini-btn ${preview ? 'on' : ''}`}
              onClick={() => setPreview((v) => !v)}
            >
              {preview ? 'Raw' : 'Preview'}
            </button>
            <button className="mini-btn" onClick={() => { setEditing(false); setPreview(false); setDraft(content) }}>
              <X size={12} /> Discard{dirty ? '*' : ''}
            </button>
            <button className="btn primary sm" disabled={!dirty || saving} onClick={save}>
              {saving ? 'Saving…' : (<><Check size={13} /> Save</>)}
            </button>
          </>
        )}
      </div>
      {error && <div className="errorbox sm">{error}</div>}
      {editing ? (
        preview ? (
          <div className="mem-file"><Markdown text={draft} /></div>
        ) : (
          <textarea
            className="mem-editor"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
          />
        )
      ) : (
        <div className="mem-file"><Markdown text={content || '*Empty — click Edit to write it.*'} /></div>
      )}
      <button className="history-toggle" onClick={() => setShowHistory((v) => !v)}>
        <History size={12} /> {showHistory ? 'Hide history' : `History (${versions.length})`}
      </button>
      {showHistory && (
        <div className="mem-history">
          {versions.map((v) => (
            <div key={v.version} className="mem-version">
              <span>v{v.version} · {v.source} · {new Date(v.created_at).toLocaleString()}</span>
              {v.version !== latest?.version && (
                <button
                  className="mini-btn"
                  title="Restore this version as a new version (nothing is erased)"
                  onClick={async () => {
                    await api.rollbackMemoryFile(name, v.version)
                    setEditing(false)
                    load()
                  }}
                >
                  <RotateCcw size={12} /> Roll back here
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
