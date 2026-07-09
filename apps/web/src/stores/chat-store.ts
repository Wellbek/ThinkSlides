import { create } from 'zustand';
import { apiGet, apiPost, apiDelete, apiPut, apiUpload } from '@/lib/api';
import { SSEClient } from '@/lib/sse';
import type { AgentEvent, DoneEvent } from '@thinkslides/schemas';

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
  agentEventStream?: AgentEvent[];
  generatedFiles?: GeneratedFile[];
  isComplete?: boolean;
}

export interface Template {
  id: string;
  name: string;
  version: string;
  tags: string[];
}

export interface GeneratedFile {
  id: string;
  filename: string;
  file_url: string;
  file_size?: number;
  file_type?: string;
  slide_count?: number;
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
  settingsOpen: boolean;
  totalElapsedMs: number | null;
  isGenerating: boolean;
  setIsGenerating: (generating: boolean) => void;

  // Computed getter (not stored)
  // isGenerating: () => boolean;  // Now a stored state

  // Actions
  fetchSessions: () => Promise<void>;
  createSession: (templateId?: string) => Promise<string>;
  deleteSession: (sessionId: string) => Promise<void>;
  setActiveSession: (sessionId: string) => Promise<void>;
  sendMessage: (content: string, files?: File[]) => Promise<void>;
  clearActiveSession: () => void;
  fetchTemplates: () => Promise<void>;
  setSessionTemplate: (sessionId: string, templateId: string | null) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  uploadTemplate: (file: File, name?: string) => Promise<Template>;
  deleteTemplate: (id: string) => Promise<void>;
  setSettingsOpen: (open: boolean) => void;
}

function findActiveAssistantMessage(messages: ChatMessage[]): ChatMessage | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === 'assistant' && !msg.isComplete) {
      return msg;
    }
  }
  return null;
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
  settingsOpen: false,
  totalElapsedMs: null,
  isGenerating: false,

  // Computed getter for isGenerating
  setIsGenerating: (generating: boolean) => set({ isGenerating: generating }),

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
      set((state) => ({ sessions: [session, ...state.sessions] }));
      await get().setActiveSession(session.id);
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
        activeSessionId: state.activeSessionId === sessionId ? null : state.activeSessionId,
        activeMessages: state.activeSessionId === sessionId ? [] : state.activeMessages,
        activeEvents: state.activeSessionId === sessionId ? [] : state.activeEvents,
        activeFiles: state.activeSessionId === sessionId ? [] : state.activeFiles,
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
      const [messages, files] = await Promise.all([
        apiGet<ChatMessage[]>(`/api/orchestrator/sessions/${sessionId}/messages`),
        apiGet<GeneratedFile[]>(`/api/orchestrator/sessions/${sessionId}/files`),
      ]);
      const normalizedFiles: GeneratedFile[] = files.map((f: any) => ({
        ...f,
        filename: f.filename || 'presentation.pptx',
        file_url: f.file_url
          ? `/api/orchestrator${f.file_url.startsWith('/') ? f.file_url : `/${f.file_url}`}`
          : `/api/orchestrator/files/${f.id}`,
        file_size: f.file_size ?? f.file_size_bytes,
      }));
      set({
        activeMessages: messages.map(m => ({ ...m, isComplete: true })),
        activeFiles: normalizedFiles,
        isLoading: false,
      });
      if (normalizedFiles.length > 0) {
        set((state) => {
          const assistantMessages = state.activeMessages.filter(m => m.role === 'assistant');
          if (assistantMessages.length > 0) {
            const lastAssistant = assistantMessages[assistantMessages.length - 1];
            const updatedMessages = state.activeMessages.map((msg) =>
              msg.id === lastAssistant.id ? { ...msg, generatedFiles: normalizedFiles } : msg
            );
            return { activeMessages: updatedMessages };
          }
          return state;
        });
      }
      const client = new SSEClient(sessionId);
      const appendEventToActiveMessage = (event: AgentEvent) => {
        set((state) => {
          const activeMsg = findActiveAssistantMessage(state.activeMessages);
          if (!activeMsg) return state;
          const updatedMessages = state.activeMessages.map((msg) =>
            msg.id === activeMsg.id
              ? { ...msg, agentEventStream: [...(msg.agentEventStream || []), event] }
              : msg
          );
          return { activeMessages: updatedMessages };
        });
      };
      client.on('thought', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('tool_call', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('tool_result', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('slide_finalized', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('error', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('progress', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('question', (e) => { try { appendEventToActiveMessage(JSON.parse(e.data)); } catch (err) { console.error(err); } });
      client.on('timer_update', (e) => {
        try {
          const data = JSON.parse(e.data) as { elapsedMs: number };
          set({ totalElapsedMs: data.elapsedMs, isGenerating: true });
        } catch (err) { console.error(err); }
      });
      client.on('session_title_updated', (e) => {
        try {
          const data = JSON.parse(e.data) as { session_id: string; title: string };
          set((state) => ({
            sessions: state.sessions.map((s) =>
              s.id === data.session_id ? { ...s, title: data.title } : s
            ),
          }));
        } catch (err) { console.error(err); }
      });
      client.on('file_generated', (e) => {
        try {
          const data = JSON.parse(e.data);
          const generatedFile: GeneratedFile = {
            id: data.fileId,
            filename: data.fileName || 'presentation.pptx',
            file_url: `/api/orchestrator/files/${data.fileId}`,
            file_size: data.fileSizeBytes,
            file_type: data.fileType,
            slide_count: data.slideCount,
            created_at: data.timestamp,
          };
          set((state) => {
            const activeMsg = findActiveAssistantMessage(state.activeMessages);
            if (!activeMsg) return { activeFiles: [...state.activeFiles, generatedFile] };
            const updatedMessages = state.activeMessages.map((msg) =>
              msg.id === activeMsg.id
                ? { ...msg, generatedFiles: [...(msg.generatedFiles || []), generatedFile] }
                : msg
            );
            return { activeMessages: updatedMessages, activeFiles: [...state.activeFiles, generatedFile] };
          });
        } catch (err) { console.error(err); }
      });
      client.on('done', (e) => {
        try {
          const data = JSON.parse(e.data) as DoneEvent;
          set((state) => {
            const activeMsg = findActiveAssistantMessage(state.activeMessages);
            if (!activeMsg) return { isSending: false };
            const doneFiles: GeneratedFile[] = (data as any).files
              ? ((data as any).files.map((f: any) => ({
                  id: f.fileId,
                  filename: f.fileName,
                  file_url: `/api/orchestrator/files/${f.fileId}`,
                  file_size: f.fileSizeBytes,
                  file_type: f.fileType,
                  slide_count: f.slideCount,
                  created_at: f.timestamp,
                })))
              : [];
            const existingFiles = activeMsg.generatedFiles || [];
            const newFiles = doneFiles.filter((nf) => !existingFiles.some((ef) => ef.id === nf.id));
            const updatedMessages = state.activeMessages.map((msg) =>
              msg.id === activeMsg.id
                ? {
                    ...msg,
                    ...(data.content ? { content: data.content } : {}),
                    generatedFiles: [...existingFiles, ...newFiles],
                    isComplete: true,
                  }
                : msg
            );
            return {
              activeMessages: updatedMessages,
              activeFiles: [...state.activeFiles, ...newFiles],
              isSending: false,
              isGenerating: false,
              totalElapsedMs: (data as any).totalElapsedMs ?? state.totalElapsedMs,
            };
          });
          // Refresh the session list so any late state changes (e.g. the
          // auto-generated title landing after this event) are reflected.
          get().fetchSessions();
        } catch (err) {
          console.error(err);
          const { activeSessionId } = get();
          if (activeSessionId) {
            apiGet<ChatMessage[]>(`/api/orchestrator/sessions/${activeSessionId}/messages`)
              .then((messages) => set({ activeMessages: messages, isSending: false }))
              .catch(() => set({ isSending: false }));
          } else {
            set({ isSending: false });
          }
        }
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
    set({ isSending: true, isGenerating: true });
    const optimisticUser: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    const optimisticAssistant: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      agentEventStream: [],
      isComplete: false,
    };
    set((state) => ({
      activeMessages: [...state.activeMessages, optimisticUser, optimisticAssistant],
    }));
    try {
      if (files && files.length > 0) {
        const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
        const formData = new FormData();
        formData.append('content', content);
        for (const file of files) formData.append('files', file);
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
        const serverMessages: ChatMessage[] | undefined = data.messages;
        if (serverMessages && Array.isArray(serverMessages)) {
          set((state) => ({
            activeMessages: [
              ...state.activeMessages.filter(m => m.id !== optimisticUser.id && m.id !== optimisticAssistant.id),
              ...serverMessages.map(m => ({ ...m, isComplete: m.role === 'assistant' ? false : true })),
            ],
          }));
        }
      } else {
        const data = await apiPost<{ messages?: ChatMessage[] }>(
          `/api/orchestrator/sessions/${activeSessionId}/messages`,
          { content }
        );
        const serverMessages = data?.messages;
        if (serverMessages && Array.isArray(serverMessages)) {
          set((state) => ({
            activeMessages: [
              ...state.activeMessages.filter(m => m.id !== optimisticUser.id && m.id !== optimisticAssistant.id),
              ...serverMessages.map(m => ({ ...m, isComplete: m.role === 'assistant' ? false : true })),
            ],
          }));
        }
      }
    } catch (err) {
      console.error('Failed to send message:', err);
      set((state) => ({
        activeMessages: state.activeMessages.filter(m => m.id !== optimisticUser.id && m.id !== optimisticAssistant.id),
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

  fetchTemplates: async () => {
    try {
      const templates = await apiGet<Template[]>('/api/orchestrator/templates');
      set({ templates });
    } catch (err) { console.error(err); }
  },

  uploadTemplate: async (file: File, name?: string) => {
    const formData = new FormData();
    formData.append('file', file);
    if (name) formData.append('name', name);
    const template = await apiUpload<Template>('/api/orchestrator/templates', formData);
    set((state) => ({ templates: [template, ...state.templates] }));
    return template;
  },

  deleteTemplate: async (id: string) => {
    await apiDelete(`/api/orchestrator/templates/${id}`);
    set((state) => ({ templates: state.templates.filter((t) => t.id !== id) }));
  },

  setSessionTemplate: async (sessionId: string, templateId: string | null) => {
    try {
      const updatedSession = await apiPut<ChatSession>(`/api/orchestrator/sessions/${sessionId}/template`, { template_id: templateId });
      set((state) => ({ sessions: state.sessions.map((s) => (s.id === sessionId ? updatedSession : s)) }));
    } catch (err) { console.error(err); throw err; }
  },

  renameSession: async (sessionId: string, title: string) => {
    try {
      await apiPut<{ success: boolean; title: string }>(`/api/orchestrator/sessions/${sessionId}/title`, { title });
      set((state) => ({
        sessions: state.sessions.map((s) => (s.id === sessionId ? { ...s, title } : s)),
      }));
    } catch (err) {
      console.error(err);
      throw err;
    }
  },

  setSettingsOpen: (open: boolean) => set({ settingsOpen: open }),
}));