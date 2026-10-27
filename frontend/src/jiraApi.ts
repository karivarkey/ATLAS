// Typed client for the Atlas Jira integration (/api/v1/jira/*).
export interface JiraConnection {
  id: string
  display_name: string
  base_url: string
  email: string
  project_keys: string[]
  sync_prefs: Record<string, any>
  ai_write: boolean
  require_approval_deletes: boolean
  notify_prefs: Record<string, any>
  status: string
  account_id: string
  account_name: string
  last_sync_at: number
  last_sync_error: string
  last_validated_at: number
  has_token: boolean
  indexed_issues: number
  sync_phase: string
  sync_detail: string
  sync_processed: number
  project_count: number
}

export interface TicketCard {
  key: string
  summary: string
  status: string
  status_category: string
  priority: string
  issue_type: string
  project: string
  assignee: string
  reporter: string
  labels: string[]
  sprint: string
  parent: string
  epic: string
  due: string
  updated: string
  url: string
  excerpt: string
}

export interface IssueDetail extends TicketCard {
  description_md: string
  comments: { id: string; author: string; created: string; updated: string; body_md: string }[]
  changelog: { id: string; created: string; author: string; items: { field?: string; from?: string; to?: string }[] }[]
  attachments: { id: string; filename: string; mime: string; size: number; author: string; created: string; url: string }[]
  links: { id: string; type: string; direction: string; key: string; summary: string }[]
}

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

const q = (p: Record<string, any>) => {
  const parts: string[] = []
  for (const k of Object.keys(p)) {
    const v = p[k]
    if (v === undefined || v === '') continue
    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(v)))
  }
  return parts.join('&')
}

export const jiraApi = {
  connection: () => req<{ connection: JiraConnection | null }>('/api/v1/jira/connection'),
  saveConnection: (body: Record<string, any>) =>
    req<{ connection: JiraConnection }>('/api/v1/jira/connection', {
      method: 'PUT', body: JSON.stringify(body),
    }),
  deleteConnection: () =>
    req<{ ok: boolean }>('/api/v1/jira/connection', { method: 'DELETE' }),
  testConnection: () => req<any>('/api/v1/jira/connection/test', { method: 'POST' }),
  sync: (mode: 'initial' | 'incremental' = 'incremental') =>
    req<any>('/api/v1/jira/sync', { method: 'POST', body: JSON.stringify({ mode }) }),
  searchLocal: (params: Record<string, any>) =>
    req<{ issues: TicketCard[]; total: number; connected: boolean }>(
      `/api/v1/jira/issues?${q(params)}`),
  getIssue: (key: string, refresh = true) =>
    req<{ issue: IssueDetail; stale: boolean; error?: string }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}?refresh=${refresh ? 1 : 0}`),
  createIssue: (body: Record<string, any>) =>
    req<{ issue: IssueDetail }>('/api/v1/jira/issues', {
      method: 'POST', body: JSON.stringify(body),
    }),
  updateIssue: (key: string, fields: Record<string, any>) =>
    req<{ issue: IssueDetail }>(`/api/v1/jira/issues/${encodeURIComponent(key)}`, {
      method: 'PATCH', body: JSON.stringify({ fields }),
    }),
  deleteIssue: (key: string) =>
    req<{ ok: boolean }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}?confirm=${encodeURIComponent(key)}`,
      { method: 'DELETE' }),
  transitions: (key: string) =>
    req<{ transitions: { id: string; name: string; to?: any }[] }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}/transitions`),
  transition: (key: string, transition: string) =>
    req<{ issue: IssueDetail }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}/transition`,
      { method: 'POST', body: JSON.stringify({ transition }) }),
  assign: (key: string, assignee: string) =>
    req<{ issue: IssueDetail }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}/assign`,
      { method: 'POST', body: JSON.stringify({ assignee }) }),
  comments: (key: string) =>
    req<{ comments: IssueDetail['comments']; total: number }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}/comments`),
  addComment: (key: string, body: string) =>
    req<any>(`/api/v1/jira/issues/${encodeURIComponent(key)}/comments`, {
      method: 'POST', body: JSON.stringify({ body }),
    }),
  attachments: (key: string) =>
    req<{ attachments: IssueDetail['attachments'] }>(
      `/api/v1/jira/issues/${encodeURIComponent(key)}/attachments`),
  attachmentPreview: (id: string, issue = '') =>
    req<any>(`/api/v1/jira/attachments/${encodeURIComponent(id)}/preview?issue=${encodeURIComponent(issue)}`),
  attachmentDownloadUrl: (id: string) => `/api/v1/jira/attachments/${encodeURIComponent(id)}/download`,
  projects: () => req<{ projects: any[] }>('/api/v1/jira/projects'),
  projectIssues: (key: string, params: Record<string, any> = {}) =>
    req<{ issues: TicketCard[]; total: number }>(
      `/api/v1/jira/projects/${encodeURIComponent(key)}/issues?${q(params)}`),
  boards: (params: Record<string, any> = {}) =>
    req<{ boards: any[] }>(`/api/v1/jira/boards?${q(params)}`),
  board: (id: number) => req<any>(`/api/v1/jira/boards/${id}`),
  boardIssues: (id: number, params: Record<string, any> = {}) =>
    req<{ issues: TicketCard[]; total: number }>(
      `/api/v1/jira/boards/${id}/issues?${q(params)}`),
  sprints: (boardId: number) =>
    req<{ sprints: any[] }>(`/api/v1/jira/boards/${boardId}/sprints`),
  sprintIssues: (id: number) =>
    req<{ issues: TicketCard[]; total: number }>(`/api/v1/jira/sprints/${id}/issues`),
  rank: (keys: string[], before?: string, after?: string) =>
    req<{ ok: boolean }>('/api/v1/jira/rank', {
      method: 'POST', body: JSON.stringify({ keys, before, after }),
    }),
  jqlValidate: (jql: string) =>
    req<any>('/api/v1/jira/jql/validate', { method: 'POST', body: JSON.stringify({ jql }) }),
  jqlSearch: (jql: string, limit = 25, cursor?: string) =>
    req<{ issues: TicketCard[]; cursor?: string; total: number }>(
      '/api/v1/jira/jql/search',
      { method: 'POST', body: JSON.stringify({ jql, limit, cursor }) }),
  filters: () => req<{ filters: any[] }>('/api/v1/jira/filters'),
  savedList: () => req<{ searches: any[] }>('/api/v1/jira/saved'),
  savedCreate: (name: string, jql: string) =>
    req<{ id: string }>('/api/v1/jira/saved', {
      method: 'POST', body: JSON.stringify({ name, jql }),
    }),
  savedDelete: (id: string) =>
    req<{ ok: boolean }>(`/api/v1/jira/saved/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  link: (key: string, other: string, link_type = 'Relates') =>
    req<{ ok: boolean }>(`/api/v1/jira/issues/${encodeURIComponent(key)}/links`, {
      method: 'POST', body: JSON.stringify({ other, link_type }),
    }),
  unlink: (linkId: string) =>
    req<{ ok: boolean }>(`/api/v1/jira/links/${encodeURIComponent(linkId)}`, { method: 'DELETE' }),
  worklogs: (key: string) => req<{ worklogs: any[] }>(
    `/api/v1/jira/issues/${encodeURIComponent(key)}/worklogs`),
  addWorklog: (key: string, time_spent: string, comment = '') =>
    req<any>(`/api/v1/jira/issues/${encodeURIComponent(key)}/worklogs`, {
      method: 'POST', body: JSON.stringify({ time_spent, comment }),
    }),
  ops: () => req<{ ops: any[] }>('/api/v1/jira/ops'),
};
