// Typed API client for the Atlas backend.
export interface Conversation {
  id: string
  title: string
  model: string
  workspace_id: string
  created_at: number
  updated_at: number
  active_execution: ExecutionSnapshot | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  model: string
  execution_id: string
  status: string
  created_at: number
}

export interface ExecutionSnapshot {
  id: string
  conversation_id: string
  status: string
  model: string
  workspace_id: string
  current_step: number
  max_steps: number
  pending: Record<string, any>
  error: string
  updated_at: number
}

export interface ModelInfo {
  id: string
  name: string
  provider: string
  protocol: string
  endpoint: string
  context_window: number | null
  supports_tools: boolean
}

export interface AtlasEvent {
  execution_id: string
  seq: number
  type: string
  payload: Record<string, any>
  created_at: number
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
    throw new Error(`HTTP ${res.status}: ${detail.slice(0, 500)}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => req<{ ok: boolean; key_configured: boolean }>('/api/v1/health'),
  listModels: (refresh = false) =>
    req<{ models: ModelInfo[]; stale: boolean; error: string; source: string }>(
      `/api/v1/models${refresh ? '?refresh=true' : ''}`,
    ),
  refreshModels: () =>
    req<{ models: ModelInfo[]; stale: boolean; error: string; source: string }>(
      '/api/v1/models/refresh',
      { method: 'POST' },
    ),
  listConversations: (q = '') =>
    req<Conversation[]>(`/api/v1/conversations${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  createConversation: (body: { title?: string; model?: string } = {}) =>
    req<Conversation>('/api/v1/conversations', { method: 'POST', body: JSON.stringify(body) }),
  getConversation: (id: string) => req<Conversation>(`/api/v1/conversations/${id}`),
  patchConversation: (id: string, body: { title?: string; model?: string }) =>
    req<Conversation>(`/api/v1/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteConversation: (id: string) =>
    req<{ ok: boolean }>(`/api/v1/conversations/${id}`, { method: 'DELETE' }),
  listMessages: (id: string) =>
    req<{ messages: ChatMessage[] }>(`/api/v1/conversations/${id}/messages`),
  sendMessage: (id: string, content: string, model: string) =>
    req<{ execution_id: string; message_id: string }>(
      `/api/v1/conversations/${id}/messages`,
      { method: 'POST', body: JSON.stringify({ content, model }) },
    ),
  getStatus: (id: string) =>
    req<{ active_execution: ExecutionSnapshot | null }>(
      `/api/v1/conversations/${id}/status`,
    ),
  // Conversation-scoped decision endpoints (server resolves the waiting
  // execution). Always prefer these over execution-scoped ids, which go stale.
  answerConversation: (convId: string, text: string) =>
    req<{ ok: boolean }>(`/api/v1/conversations/${convId}/answer`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  approveConversation: (convId: string, approved: boolean) =>
    req<{ ok: boolean }>(`/api/v1/conversations/${convId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approved }),
    }),
  answer: (id: string, text: string) =>
    req<{ ok: boolean }>(`/api/v1/conversations/${id}/answer`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  approve: (id: string, approved: boolean) =>
    req<{ ok: boolean }>(`/api/v1/conversations/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approved }),
    }),
  getExecution: (eid: string) => req<ExecutionSnapshot>(`/api/v1/executions/${eid}`),
  executionEvents: (eid: string, from_seq = 0) =>
    req<{ events: AtlasEvent[] }>(`/api/v1/executions/${eid}/events?from_seq=${from_seq}`),
  stop: (eid: string) =>
    req<{ ok: boolean }>(`/api/v1/executions/${eid}/stop`, { method: 'POST' }),
  pause: (eid: string) =>
    req<{ ok: boolean }>(`/api/v1/executions/${eid}/pause`, { method: 'POST' }),
  resume: (eid: string) =>
    req<{ ok: boolean }>(`/api/v1/executions/${eid}/resume`, { method: 'POST' }),
  retry: (eid: string, model = '') =>
    req<{ ok: boolean }>(`/api/v1/executions/${eid}/retry`, {
      method: 'POST',
      body: JSON.stringify({ model }),
    }),
  listArtifacts: (cid: string) =>
    req<
      { id: string; execution_id: string; path: string; kind: string; summary: string }[]
    >(`/api/v1/conversations/${cid}/artifacts`),
  memorySearch: (q: string, scope = '') =>
    req<{ results: any[]; scope: string; semantic: boolean }>(
      `/api/v1/memory/search?q=${encodeURIComponent(q)}&scope=${encodeURIComponent(scope)}`,
    ),
  memoryRecords: (scope = '', status = '') =>
    req<{ records: any[] }>(
      `/api/v1/memory/records?scope=${encodeURIComponent(scope)}&status=${status}`,
    ),
  deleteRecord: (id: string) =>
    req<{ ok: boolean }>(`/api/v1/memory/records/${id}`, { method: 'DELETE' }),
  memoryFile: (name: string) =>
    req<{ path: string; content: string; version: number; versions: any[] }>(
      `/api/v1/memory/files/${name}`,
    ),
  saveMemoryFile: (name: string, content: string) =>
    req<{ ok: boolean; version: number }>(`/api/v1/memory/files/${name}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),
  rollbackMemoryFile: (name: string, version: number) =>
    req<{ ok: boolean; version: number }>(
      `/api/v1/memory/files/${name}/rollback/${version}`,
      { method: 'POST' },
    ),
  memoryProposals: (status = 'proposed') =>
    req<{ proposals: any[] }>(`/api/v1/memory/proposals?status=${status}`),
  decideProposal: (id: string, approve: boolean) =>
    req<{ ok: boolean }>(`/api/v1/memory/proposals/${id}/${approve ? 'approve' : 'reject'}`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  memoryJobs: () => req<{ jobs: any[] }>('/api/v1/memory/jobs'),
  memoryJobDetail: (jid: string) => req<any>(`/api/v1/memory/jobs/${jid}/detail`),
  runDream: (conversation_ids: string[] = []) =>
    req<{ job_id: string; status: string }>(`/api/v1/memory/dream`, {
      method: 'POST',
      body: JSON.stringify({ conversation_ids }),
    }),
  memoryHealth: () => req<any>('/api/v1/memory/health'),
  executionRefs: (eid: string) =>
    req<{ refs: any[] }>(`/api/v1/memory/execution/${eid}/refs`),
  workspaceFiles: (cid: string, path = '.') =>
    req<any>(`/api/v1/workspaces/${cid}/files?path=${encodeURIComponent(path)}`),
  workspaceDiff: (cid: string) => req<any>(`/api/v1/workspaces/${cid}/diff`),
  getSettings: () => req<Record<string, any>>('/api/v1/settings'),
  putSettings: (body: Record<string, any>) =>
    req<Record<string, any>>('/api/v1/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  onboardingStatus: () =>
    req<{ onboarded: boolean; key_configured: boolean; default_model: string }>(
      '/api/v1/onboarding/status',
    ),
  onboardingStart: (model = '') =>
    req<{ conversation_id: string; execution_id: string }>('/api/v1/onboarding/start', {
      method: 'POST',
      body: JSON.stringify({ model }),
    }),
  sendOnboardingMessage: (id: string, content: string, model = '') =>
    req<{ execution_id: string; message_id: string }>(
      `/api/v1/conversations/${id}/messages`,
      { method: 'POST', body: JSON.stringify({ content, model }) },
    ),
  skipOnboarding: () =>
    req<{ ok: boolean }>('/api/v1/onboarding/skip', { method: 'POST' }),
  reopenOnboarding: () =>
    req<{ ok: boolean }>('/api/v1/onboarding/reopen', { method: 'POST' }),
};
