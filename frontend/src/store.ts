import { create } from 'zustand'
import { api, ChatMessage, Conversation, ExecutionSnapshot, ModelInfo } from './api'

export interface ToolCall {
  name: string
  summary: string
  status: 'running' | 'done' | 'failed' | 'approval'
  output: string
  diff?: string
  startedAt: number
}

export interface StreamState {
  text: string
  tools: ToolCall[]
  status: string
  pending: Record<string, any>
}

// Per-conversation UI state, keyed by conversation id. Never a single global
// "current execution" authority — routing is always by conversation/execution id.
interface ChatState {
  messages: ChatMessage[]
  stream: StreamState | null
  execution: ExecutionSnapshot | null
  scrollKey: number
  completedAt: number | null
}

interface Store {
  conversations: Conversation[]
  activeId: string | null
  chats: Record<string, ChatState>
  models: ModelInfo[]
  modelsMeta: { stale: boolean; error: string; source: string }
  sidebarOpen: boolean
  search: string
  settingsOpen: boolean
  detailOpen: boolean
  keyConfigured: boolean | null

  loadConversations: () => Promise<void>
  selectConversation: (id: string | null) => Promise<void>
  createConversation: () => Promise<string>
  deleteConversation: (id: string) => Promise<void>
  renameConversation: (id: string, title: string) => Promise<void>
  setModel: (convId: string, model: string) => Promise<void>
  loadModels: (refresh?: boolean) => Promise<void>
  sendMessage: (convId: string, content: string) => Promise<void>
  applyEvent: (convId: string, ev: any) => void
  setExecution: (convId: string, ex: ExecutionSnapshot | null) => void
  setSidebar: (open: boolean) => void
  setSearch: (s: string) => void
  setSettingsOpen: (o: boolean) => void
  setDetailOpen: (o: boolean) => void
  checkHealth: () => Promise<void>
}

function emptyChat(): ChatState {
  return { messages: [], stream: null, execution: null, scrollKey: 0, completedAt: null }
}

export const useStore = create<Store>((set, get) => ({
  conversations: [],
  activeId: null,
  chats: {},
  models: [],
  modelsMeta: { stale: false, error: '', source: '' },
  sidebarOpen: true,
  search: '',
  settingsOpen: false,
  detailOpen: false,
  keyConfigured: null,

  loadConversations: async () => {
    const conversations = await api.listConversations()
    set({ conversations })
    const saved = localStorage.getItem('atlas.activeId')
    if (saved && conversations.some((c) => c.id === saved)) {
      await get().selectConversation(saved)
    } else if (conversations.length > 0 && !get().activeId) {
      await get().selectConversation(conversations[0].id)
    }
  },

  selectConversation: async (id) => {
    set({ activeId: id, detailOpen: false })
    if (!id) return
    localStorage.setItem('atlas.activeId', id)
    if (!get().chats[id]) set((s) => ({ chats: { ...s.chats, [id]: emptyChat() } }))
    try {
      const [{ messages }, status] = await Promise.all([
        api.listMessages(id),
        api.getStatus(id),
      ])
      set((s) => ({
        chats: {
          ...s.chats,
          [id]: { ...(s.chats[id] || emptyChat()), messages,
                   execution: status.active_execution, stream: null,
                   completedAt: null },
        },
      }))
      // Reconnect: the useExecutionStream hook replays missed events and
      // dedupes by seq, so select only needs messages + snapshot here.
    } catch (e) {
      console.warn('select failed', e)
    }
  },

  createConversation: async () => {
    const def = localStorage.getItem('atlas.defaultModel') || get().models[0]?.id || ''
    const c = await api.createConversation({ model: def })
    set((s) => ({ conversations: [c, ...s.conversations] }))
    await get().selectConversation(c.id)
    return c.id
  },

  deleteConversation: async (id) => {
    await api.deleteConversation(id)
    set((s) => {
      const chats = { ...s.chats }
      delete chats[id]
      return {
        conversations: s.conversations.filter((c) => c.id !== id),
        chats,
        activeId: s.activeId === id ? null : s.activeId,
      }
    })
    const rest = get().conversations
    if (get().activeId === null && rest.length > 0) await get().selectConversation(rest[0].id)
  },

  renameConversation: async (id, title) => {
    const c = await api.patchConversation(id, { title })
    set((s) => ({ conversations: s.conversations.map((x) => (x.id === id ? c : x)) }))
  },

  setModel: async (convId, model) => {
    const c = await api.patchConversation(convId, { model })
    localStorage.setItem('atlas.defaultModel', model)
    set((s) => ({ conversations: s.conversations.map((x) => (x.id === convId ? c : x)) }))
  },

  loadModels: async (refresh = false) => {
    try {
      const r = await api.listModels(refresh)
      // Server default wins for new conversations unless the user picked one.
      if (!localStorage.getItem('atlas.defaultModel')) {
        try {
          const cfg = await api.getSettings()
          if (cfg.default_model) localStorage.setItem('atlas.defaultModel', cfg.default_model)
        } catch { /* ignore */ }
      }
      set({ models: r.models, modelsMeta: { stale: r.stale, error: r.error, source: r.source } })
    } catch (e: any) {
      set((s) => ({ modelsMeta: { ...s.modelsMeta, error: String(e?.message || e) } }))
    }
  },

  sendMessage: async (convId, content) => {
    const conv = get().conversations.find((c) => c.id === convId)
    const model = conv?.model || localStorage.getItem('atlas.defaultModel') || get().models[0]?.id || ''
    if (!model) throw new Error('No model available — configure a model first.')
    const { execution_id } = await api.sendMessage(convId, content, model)
    // Backend renames "New conversation" from the first query instantly;
    // pull it so the sidebar never shows a stale title.
    try {
      const convFresh = await api.getConversation(convId)
      set((s) => ({ conversations: s.conversations.map((c) => (c.id === convId ? convFresh : c)) }))
    } catch { /* ignore — title arrives via title_updated event */ }
    const ex = await api.getExecution(execution_id)
    set((s) => ({
      chats: {
        ...s.chats,
        [convId]: {
          ...(s.chats[convId] || emptyChat()),
          execution: ex,
          stream: { text: '', tools: [], status: 'queued', pending: {} },
          completedAt: null,
        },
      },
    }))
  },

  applyEvent: (convId, ev) => {
    const t0 = (ev as any)?.type as string
    const p0 = ((ev as any)?.payload || {}) as Record<string, any>
    if (t0 === 'title_updated' && p0.title) {
      const cid = (p0.conversation_id as string) || convId
      const title = String(p0.title).slice(0, 200)
      set((s) => ({ conversations: s.conversations.map((c) => (c.id === cid ? { ...c, title } : c)) }))
    }
    set((s) => {
      const chat = s.chats[convId] || emptyChat()
      const stream: StreamState = chat.stream || { text: '', tools: [], status: 'running', pending: {} }
      const t = ev.type as string
      const p = (ev.payload || {}) as Record<string, any>
      let next: StreamState = stream
      if (t === 'text_delta') {
        next = { ...stream, text: stream.text + (p.delta || '') }
      } else if (t === 'tool_started') {
        next = {
          ...stream,
          tools: [...stream.tools, {
            name: p.name, summary: p.summary || p.name, status: 'running',
            output: '', startedAt: Date.now(),
          }],
        }
      } else if (t === 'tool_completed' || t === 'tool_failed') {
        const tools = [...stream.tools]
        const i = tools.map((x) => x.status).lastIndexOf('running')
        const out = t === 'tool_completed' ? (p.output || '') : (p.error || p.output || 'failed')
        if (i >= 0) tools[i] = { ...tools[i], status: t === 'tool_completed' ? 'done' : 'failed',
                                 output: out, diff: p.diff };
        else tools.push({ name: p.name || 'tool', summary: p.summary || 'tool',
                          status: t === 'tool_completed' ? 'done' : 'failed',
                          output: out, startedAt: Date.now() })
        next = { ...stream, tools }
      } else if (t === 'approval_required' || t === 'question_asked') {
        next = { ...stream, status: t === 'approval_required' ? 'waiting_approval' : 'waiting_input',
                 pending: p }
      } else if (t === 'question_answered') {
        next = { ...stream, status: 'running', pending: {} }
      } else if (t === 'status_changed') {
        next = { ...stream, status: p.to || stream.status }
      } else if (t === 'turn_completed' || t === 'turn_failed' || t === 'cancelled') {
        next = { ...stream, status: t }
      }
      return { chats: { ...s.chats, [convId]: { ...chat, stream: next,
        scrollKey: chat.scrollKey + (t === 'text_delta' ? 0 : 1) } } }
    })
  },

  setExecution: (convId, ex) =>
    set((s) => ({ chats: { ...s.chats, [convId]: { ...(s.chats[convId] || emptyChat()), execution: ex } } })),

  setSidebar: (open) => set({ sidebarOpen: open }),
  setSearch: (s) => set({ search: s }),
  setSettingsOpen: (o) => set({ settingsOpen: o }),
  setDetailOpen: (o) => set({ detailOpen: o }),

  checkHealth: async () => {
    try {
      const h = await api.health()
      set({ keyConfigured: h.key_configured })
    } catch {
      set({ keyConfigured: false })
    }
  },
}))
