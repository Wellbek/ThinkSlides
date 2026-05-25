import { create } from 'zustand';
import { apiGet, apiPost, apiDelete, apiPut } from '@/lib/api';
import { SSEClient } from '@/lib/sse';
import type { AgentEvent } from '@slideagent/schemas';

export interface ChatSession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  template_id?: string | null;
  slide_count: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  attachments?: string[];
}

export interface Template {
  id: string;
  name: string;
  version: string;
  tags: string[];
  // ... other fields from your backend
}

export interface GeneratedFile {
  id: string;
  filename: string;
  file_url: string;
  created_at: string;
}

interface ChatState {
  sessions: ChatSession[];
  activeSessionId: string | null;
  activeMessages: ChatMessage[];
  activeEvents: AgentEvent[];
  activeFiles: GeneratedFile[];
  templates: Template[];
  isLoading: boolean;
  isSending: boolean;
  sseClient: SSEClient | null;

  // Actions
  fetchSessions: () => Promise<void>;
  createSession: (templateId?: string) => Promise<string>;
  deleteSession: (sessionId: string) => Promise<void>;
  setActiveSession: (sessionId: string) => Promise<void>;
  sendMessage: (content: string, files?: File[]) => Promise<void>;
  clearActiveSession: () => void;
  
  // New template actions
  fetchTemplates: () => Promise<void>;
  setSessionTemplate: (sessionId: string, templateId: string | null) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  activeMessages: [],
  activeEvents: [],
  activeFiles: [],
  templates: [],
  isLoading: false,
  isSending: false,
  sseClient: null,

  fetchSessions: async () => {
    set({ isLoading: true });
    try {
      const sessions = await apiGet<ChatSession[]>('/api/orchestrator/sessions');
      set({ sessions, isLoading: false });
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
      set({ isLoading: false });
    }
  },

  createSession: async (templateId?: string) => {
    try {
      const body: Record<string, unknown> = {};
      if (templateId) body.template_id = templateId;

      const session = await apiPost<ChatSession>('/api/orchestrator/sessions', body);
      set((state) => ({
        sessions: [session, ...state.sessions],
      }));
      return session.id;
    } catch (err) {
      console.error('Failed to create session:', err);
      throw err;
    }
  },

  deleteSession: async (sessionId: string) => {
    try {
      await apiDelete(`/api/orchestrator/sessions/${sessionId}`);
      set((state) => ({
        sessions: state.sessions.filter((s) => s.id !== sessionId),
        activeSessionId:
          state.activeSessionId === sessionId ? null : state.activeSessionId,
        activeMessages:
          state.activeSessionId === sessionId ? [] : state.activeMessages,
        activeEvents:
          state.activeSessionId === sessionId ? [] : state.activeEvents,
        activeFiles:
          state.activeSessionId === sessionId ? [] : state.activeFiles,
      }));
      if (get().activeSessionId === sessionId) {
        get().sseClient?.disconnect();
        set({ sseClient: null });
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  },

  setActiveSession: async (sessionId: string) => {
    const { sseClient } = get();
    if (sseClient) {
      sseClient.disconnect();
    }

    set({
      activeSessionId: sessionId,
      activeMessages: [],
      activeEvents: [],
      activeFiles: [],
      isLoading: true,
    });

    try {
      const [messages, events, files] = await Promise.all([
        apiGet<ChatMessage[]>(`/api/orchestrator/sessions/${sessionId}/messages`),
        apiGet<AgentEvent[]>(`/api/orchestrator/sessions/${sessionId}/events`),
        apiGet<GeneratedFile[]>(`/api/orchestrator/sessions/${sessionId}/files`),
      ]);

      set({
        activeMessages: messages,
        activeEvents: events,
        activeFiles: files,
        isLoading: false,
      });

      // Open SSE connection for live events
      const client = new SSEClient(sessionId);

      // Parse incoming string data into AgentEvent object
      const appendEvent = (eventType: string, e: { id?: string; data: string }) => {
        try {
          const parsedData = JSON.parse(e.data) as AgentEvent;
          set((state) => ({
            activeEvents: [...state.activeEvents, parsedData],
          }));
        } catch (err) {
          console.error(`Failed to parse ${eventType} event data:`, err);
        }
      };

      client.on('thought', (e) => appendEvent('thought', e));
      client.on('tool_call', (e) => appendEvent('tool_call', e));
      client.on('tool_result', (e) => appendEvent('tool_result', e));
      client.on('slide_finalized', (e) => appendEvent('slide_finalized', e));
      client.on('error', (e) => appendEvent('error', e));
      client.on('progress', (e) => appendEvent('progress', e));
      client.on('done', () => {
        set({ isSending: false });
      });

      client.connect();
      set({ sseClient: client });
    } catch (err) {
      console.error('Failed to load session:', err);
      set({ isLoading: false });
    }
  },

  sendMessage: async (content: string, files?: File[]) => {
    const { activeSessionId } = get();
    if (!activeSessionId) return;

    set({ isSending: true });

    const optimisticMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    set((state) => ({
      activeMessages: [...state.activeMessages, optimisticMessage],
    }));

    try {
      if (files && files.length > 0) {
        const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
        const formData = new FormData();
        formData.append('content', content);
        for (const file of files) {
          formData.append('files', file);
        }

        const res = await fetch(`/api/orchestrator/sessions/${activeSessionId}/messages`, {
          method: 'POST',
          headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          body: formData,
        });

        if (res.status === 401) {
          localStorage.removeItem('auth_token');
          localStorage.removeItem('auth_user');
          window.location.href = '/auth/login';
          return;
        }

        if (!res.ok) throw new Error(`Failed to send message: ${res.status}`);

        const data = await res.json();
        if (data.messages) set({ activeMessages: data.messages });
      } else {
        const data = await apiPost<{ messages: ChatMessage[] }>(
          `/api/orchestrator/sessions/${activeSessionId}/messages`,
          { content }
        );
        if (data.messages) set({ activeMessages: data.messages });
      }
    } catch (err) {
      console.error('Failed to send message:', err);
      set((state) => ({
        activeMessages: state.activeMessages.filter((m) => m.id !== optimisticMessage.id),
        isSending: false,
      }));
    }
  },

  clearActiveSession: () => {
    const { sseClient } = get();
    sseClient?.disconnect();
    set({
      activeSessionId: null,
      activeMessages: [],
      activeEvents: [],
      activeFiles: [],
      sseClient: null,
      isSending: false,
    });
  },

  // --- Template actions ---
  fetchTemplates: async () => {
    try {
      const templates = await apiGet<Template[]>('/api/orchestrator/templates');
      set({ templates });
    } catch (err) {
      console.error('Failed to fetch templates:', err);
    }
  },

  setSessionTemplate: async (sessionId: string, templateId: string | null) => {
    try {
      const updatedSession = await apiPut<ChatSession>(`/api/orchestrator/sessions/${sessionId}/template`, {
        template_id: templateId,
      });
      set((state) => ({
        sessions: state.sessions.map((s) => (s.id === sessionId ? updatedSession : s)),
        // If this is the active session, also update any local reference (though we don't store session detail separately)
      }));
      // Optionally refresh the session data (messages, etc.) if template affects content
    } catch (err) {
      console.error('Failed to set session template:', err);
      throw err;
    }
  },
}));