import { create } from 'zustand';
import { apiGet, apiPost, apiDelete, apiPut, apiUpload } from '@/lib/api';
import { SSEClient } from '@/lib/sse';
import { useSettingsStore } from '@/stores/settings-store';
import type { AgentEvent, DoneEvent, EffortMode } from '@thinkslides/schemas';

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
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string;
  attachments?: string[];
  agentEventStream?: AgentEvent[];
  generatedFiles?: GeneratedFile[];
  isComplete?: boolean;
  isCollapsed?: boolean;
  metadata?: {
    type?: 'completion';
    elapsedMs?: number;
  };
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
  /**
   * Effort level applied to the next message. Kept on the store rather than in
   * component state so the home view and the in-session composer share one
   * selection, and it survives moving between them.
   */
  mode: EffortMode;
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
  setMode: (mode: EffortMode) => void;
}

/**
 * Reconciles the optimistic user/assistant pair with the server's response,
 * carrying forward anything SSE already attached to the optimistic assistant
 * message (agentEventStream, generatedFiles, a genuine isComplete/content
 * from a done event) rather than discarding it.
 *
 * Why this matters: the POST that creates the message rows and the SSE
 * events for the resulting generation race independently. On a fast path -
 * most visibly resuming after ask_user_question, where the first event can
 * land within tens of milliseconds - SSE events reliably arrive and get
 * appended to the optimistic assistant message *before* this POST response
 * comes back. The old code then swapped in the server's freshly-created (and
 * necessarily event-less) row wholesale, silently erasing everything that
 * had just arrived - the chat looked permanently stuck on "Working..." even
 * though the run had already finished successfully.
 */
function reconcileServerMessages(
  currentMessages: ChatMessage[],
  optimisticUserId: string,
  optimisticAssistantId: string,
  serverMessages: ChatMessage[]
): ChatMessage[] {
  const optimisticAssistant = currentMessages.find((m) => m.id === optimisticAssistantId);

  const reconciled = serverMessages.map((m) => {
    if (m.role !== 'assistant') return { ...m, isComplete: true };
    return {
      ...m,
      agentEventStream: optimisticAssistant?.agentEventStream ?? m.agentEventStream,
      generatedFiles: optimisticAssistant?.generatedFiles ?? m.generatedFiles,
      // A done event may have already raced ahead and completed the
      // optimistic message - don't regress a genuine completion back to
      // "in progress".
      isComplete: optimisticAssistant?.isComplete ?? false,
      content: optimisticAssistant?.isComplete ? optimisticAssistant.content : m.content,
    };
  });

  return [
    ...currentMessages.filter((m) => m.id !== optimisticUserId && m.id !== optimisticAssistantId),
    ...reconciled,
  ];
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
  mode: 'simple',
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
        activeMessages: messages.map((m: any) => ({
          ...m,
          // The orchestrator persists the live event stream (incl. cited
          // sources) as agent_event_stream; surface it so thoughts, tool
          // calls, and sources render on reload, not just during live SSE.
          agentEventStream: m.agent_event_stream ?? m.agentEventStream,
          isComplete: true,
        })),
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
      client.on('thought', (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      });
      client.on('tool_call', (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      });
      client.on('tool_result', (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      });
      client.on('slide_finalized', (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      });
      client.on('error', (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      });
      client.on('progress', (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      });
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
          const totalElapsedMs = (data as any).totalElapsedMs;
          // A done event fires on ask_user_question too - the run has
          // paused, not finished, and will continue once the user answers
          // (see sendMessage's resume path). Marking the message complete
          // here would be wrong even though nothing currently depends on it
          // staying false, since the run genuinely isn't done yet.
          const isPaused = (data as any).paused === true;

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
                    ...(data.content && !isPaused ? { content: data.content } : {}),
                    generatedFiles: [...existingFiles, ...newFiles],
                    isComplete: !isPaused,
                  }
                : msg
            );

            // Add completion message if we have total elapsed time
            let completionMessage: ChatMessage | null = null;
            if (totalElapsedMs && totalElapsedMs > 0) {
              const seconds = Math.floor(totalElapsedMs / 1000);
              const minutes = Math.floor(seconds / 60);
              const remainingSeconds = seconds % 60;
              let timeString: string;
              if (minutes > 0) {
                timeString = `${minutes}m ${remainingSeconds}s`;
              } else {
                timeString = `${remainingSeconds}s`;
              }
              completionMessage = {
                id: crypto.randomUUID(),
                role: 'system',
                content: `Finished in ${timeString}`,
                created_at: new Date().toISOString(),
                isCollapsed: true,
                metadata: {
                  type: 'completion',
                  elapsedMs: totalElapsedMs,
                },
              };
            }

            return {
              activeMessages: completionMessage
                ? [...updatedMessages, completionMessage]
                : updatedMessages,
              activeFiles: [...state.activeFiles, ...newFiles],
              isSending: false,
              isGenerating: false,
              totalElapsedMs: totalElapsedMs ?? state.totalElapsedMs,
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
    const { activeSessionId, mode } = get();
    if (!activeSessionId) return;

    // Deep Thinking needs a configured search backend. The selector is already
    // disabled without one, but settings can change (or be cleared) between
    // picking a mode and sending, so resolve it once more at send time.
    const deepAvailable = useSettingsStore.getState().status().deepThinkingAvailable;
    const effectiveMode: EffortMode = mode === 'deep' && deepAvailable ? 'deep' : 'simple';
    if (effectiveMode !== mode) set({ mode: effectiveMode });

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
        formData.append('mode', effectiveMode);
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
            activeMessages: reconcileServerMessages(
              state.activeMessages, optimisticUser.id, optimisticAssistant.id, serverMessages
            ),
          }));
        }
      } else {
        const data = await apiPost<{ messages?: ChatMessage[] }>(
          `/api/orchestrator/sessions/${activeSessionId}/messages`,
          { content, mode: effectiveMode }
        );
        const serverMessages = data?.messages;
        if (serverMessages && Array.isArray(serverMessages)) {
          set((state) => ({
            activeMessages: reconcileServerMessages(
              state.activeMessages, optimisticUser.id, optimisticAssistant.id, serverMessages
            ),
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

  setMode: (mode: EffortMode) => set({ mode }),
}));