// Typed client for the notes/vault + tasks API. (New file — api.ts is owned
// by another agent and stays untouched.)
async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      detail = typeof j === 'object' ? JSON.stringify(j) : String(j)
    } catch {
      detail = await res.text().catch(() => '')
    }
    throw new Error(`HTTP ${res.status}: ${detail.slice(0, 600)}`)
  }
  return res.json() as Promise<T>
}

export interface VaultNote {
  id: string
  path: string
  title: string
  version: number
  content_hash: string
  content: string
  frontmatter: Record<string, string>
  word_count: number
  outlinks: { target: string; heading: string | null; alias: string | null; broken: boolean }[]
  backlinks: { path: string; title: string; matches: any[] }[]
  broken_links: { target: string }[]
  stale_readers: { execution_id: string; read_version: number; current_version: number; stale: boolean }[]
  linked_tasks: { id: string; title: string; status: string }[]
  created_at: number
  updated_at: number
}

export interface TreeNode {
  name: string
  path: string
  type: 'folder' | 'note'
  title?: string
  id?: string | null
  children?: TreeNode[]
}

export type TaskStatus =
  | 'inbox' | 'planned' | 'in_progress' | 'waiting_input'
  | 'waiting_approval' | 'completed' | 'cancelled' | 'archived'
export type TaskPriority = 'critical' | 'high' | 'normal' | 'low'

export interface Task {
  id: string
  title: string
  description: string
  status: TaskStatus
  priority: TaskPriority
  due_at: number
  due_tz: string
  project: string
  source_conv: string
  source_msg: string
  recurrence: string
  recur_end: number
  recur_max: number
  approval_required: boolean
  eligibility: Record<string, any>
  notes: { note_id: string; path: string; title: string }[]
  created_at: number
  updated_at: number
}

export interface Notification {
  id: string
  kind: string
  title: string
  body: string
  task_id: string
  note_id: string
  dedupe_key: string | null
  status: 'unread' | 'read' | 'dismissed'
  created_at: number
}

export const notesApi = {
  tree: () => req<{ tree: TreeNode[] }>('/api/v1/notes/tree'),
  search: (q: string) =>
    req<{ results: any[] }>(`/api/v1/notes/search?q=${encodeURIComponent(q)}`),
  suggest: (prefix: string) =>
    req<{ suggestions: { path: string; title: string; insert: string }[] }>(
      `/api/v1/notes/suggest?prefix=${encodeURIComponent(prefix)}`),
  broken: () => req<{ broken: any[] }>('/api/v1/notes/broken'),
  sync: () => req<Record<string, string[]>>('/api/v1/notes/sync', { method: 'POST' }),
  create: (path: string, content: string) =>
    req<VaultNote>('/api/v1/notes', { method: 'POST', body: JSON.stringify({ path, content }) }),
  get: (path: string) =>
    req<VaultNote>(`/api/v1/notes?path=${encodeURIComponent(path)}`),
  update: (path: string, content: string, expected_version?: number, expected_hash?: string) =>
    req<VaultNote>(`/api/v1/notes?path=${encodeURIComponent(path)}`, {
      method: 'PUT',
      body: JSON.stringify({ content, expected_version, expected_hash }),
    }),
  remove: (path: string) =>
    req<{ ok: boolean }>(`/api/v1/notes?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  move: (old_path: string, new_path: string) =>
    req<{ id: string; from: string; to: string; references_updated: string[] }>(
      '/api/v1/notes/move', { method: 'POST', body: JSON.stringify({ old_path, new_path }) }),
  mkdir: (path: string) =>
    req<{ ok: boolean }>('/api/v1/notes/folder', { method: 'POST', body: JSON.stringify({ path }) }),
  copyNote: (src: string, dst: string) =>
    req<VaultNote>('/api/v1/notes/copy', { method: 'POST', body: JSON.stringify({ src, dst }) }),
  removeFolder: (path: string) =>
    req<{ ok: boolean; removed: string[] }>(
      `/api/v1/notes/folder?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  moveFolder: (old_path: string, new_path: string) =>
    req<{ ok: boolean; from: string; to: string }>(
      '/api/v1/notes/folder/move', { method: 'POST', body: JSON.stringify({ old_path, new_path }) }),
  copyFolder: (src: string, dst: string) =>
    req<{ ok: boolean; copied: string[] }>(
      '/api/v1/notes/folder/copy', { method: 'POST', body: JSON.stringify({ src, dst }) }),
  semanticSearch: (q: string, limit = 10) =>
    req<{ results: { path: string; title: string; id?: string; score: number; snippet: string; via: string[] }[]; semantic: boolean }>(
      `/api/v1/notes/semantic-search?q=${encodeURIComponent(q)}&limit=${limit}`),
  ask: (question: string, limit = 6, history: { role: string; content: string }[] = []) =>
    req<{ answer: string; citations: { path: string; title: string; snippet: string }[]; semantic: boolean; fallback?: boolean; model?: string; touched: string[]; opened: string | null; trace: { tool: string; ok: boolean }[] }>(
      '/api/v1/notes/ask', { method: 'POST', body: JSON.stringify({ question, limit, history }) }),
  versions: (path: string) =>
    req<{ versions: any[] }>(`/api/v1/notes/versions?path=${encodeURIComponent(path)}`),
  versionContent: (path: string, version: number) =>
    req<{ path: string; version: number; content: string }>(
      `/api/v1/notes/versions/content?path=${encodeURIComponent(path)}&version=${version}`),
  rollback: (path: string, version: number) =>
    req<VaultNote>('/api/v1/notes/rollback', {
      method: 'POST', body: JSON.stringify({ path, version }),
    }),
};

export const tasksApi = {
  list: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString()
    return req<{ tasks: Task[] }>(`/api/v1/tasks${qs ? `?${qs}` : ''}`)
  },
  get: (id: string) => req<Task>(`/api/v1/tasks/${id}`),
  create: (body: Partial<Task> & { title: string }) =>
    req<Task>('/api/v1/tasks', { method: 'POST', body: JSON.stringify(body) }),
  patch: (id: string, body: Record<string, any>) =>
    req<Task>(`/api/v1/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  transition: (id: string, to: TaskStatus) =>
    req<Task>(`/api/v1/tasks/${id}/transition`, {
      method: 'POST', body: JSON.stringify({ to }),
    }),
  remove: (id: string) =>
    req<{ ok: boolean }>(`/api/v1/tasks/${id}`, { method: 'DELETE' }),
  history: (id: string) => req<{ history: any[] }>(`/api/v1/tasks/${id}/history`),
  linkNote: (id: string, note_path: string) =>
    req<Task>(`/api/v1/tasks/${id}/notes`, {
      method: 'POST', body: JSON.stringify({ note_path }),
    }),
  unlinkNote: (id: string, note_id: string) =>
    req<Task>(`/api/v1/tasks/${id}/notes?note_id=${encodeURIComponent(note_id)}`, {
      method: 'DELETE',
    }),
  genOccurrences: (id: string, horizon_days = 30) =>
    req<{ new: number; total: number }>(`/api/v1/tasks/${id}/occurrences/generate`, {
      method: 'POST', body: JSON.stringify({ horizon_days }),
    }),
  occurrences: (id: string) =>
    req<{ occurrences: any[] }>(`/api/v1/tasks/${id}/occurrences`),
  attempts: (id: string) => req<{ attempts: any[] }>(`/api/v1/tasks/${id}/attempts`),
  execute: (id: string, body: { conversation_id?: string; model?: string } = {}) =>
    req<{ task_id: string; execution_id: string; conversation_id: string; attempt_id: string }>(
      `/api/v1/tasks/${id}/execute`, { method: 'POST', body: JSON.stringify(body) }),
  verify: (id: string, execution_id: string, criteria_met: boolean, evidence: string) =>
    req<{ verified: boolean; task: Task }>(`/api/v1/tasks/${id}/verify`, {
      method: 'POST', body: JSON.stringify({ execution_id, criteria_met, evidence }),
    }),
  createReminder: (task_id: string, fire_at: number) =>
    req<any>('/api/v1/reminders', { method: 'POST', body: JSON.stringify({ task_id, fire_at }) }),
  listReminders: (task_id = '', status = '') =>
    req<{ reminders: any[] }>(
      `/api/v1/reminders?task_id=${encodeURIComponent(task_id)}&status=${status}`),
  snoozeReminder: (id: string, until: number) =>
    req<any>(`/api/v1/reminders/${id}/snooze`, {
      method: 'POST', body: JSON.stringify({ until }),
    }),
  dismissReminder: (id: string) =>
    req<any>(`/api/v1/reminders/${id}/dismiss`, { method: 'POST' }),
  evaluate: () => req<{ notified: any[] }>('/api/v1/reminders/evaluate', { method: 'POST' }),
  notifications: (status = '') =>
    req<{ notifications: Notification[] }>(
      `/api/v1/notifications${status ? `?status=${status}` : ''}`),
  unreadCount: () => req<{ unread: number }>('/api/v1/notifications/unread-count'),
  markRead: (id: string) => req<Notification>(`/api/v1/notifications/${id}/read`, { method: 'POST' }),
  dismiss: (id: string) =>
    req<Notification>(`/api/v1/notifications/${id}/dismiss`, { method: 'POST' }),
};
