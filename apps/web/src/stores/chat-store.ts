import { create } from "zustand";
import type { AgentEvent } from "@slideagent/schemas/agent-events";
import type { Slide } from "@slideagent/schemas/slide";
import { api } from "@/lib/api";

interface Attachment {
  fileName: string;
  mimeType: string;
  size: number;
  url: string;
}

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  attachments: Attachment[];
  createdAt: string;
}

interface SessionListItem {
  id: string;
  title: string;
  templateBadge?: string;
  slideCount: number;
  updatedAt: string;
  createdAt: string;
}

interface TemplateItem {
  id: string;
  name: string;
  tags: string[];
  version: number;
  isDefault: boolean;
}

interface GeneratedFile {
  id: string;
  fileType: string;
  fileName: string;
  fileSizeBytes: number;
  downloadUrl: string;
  createdAt: string;
}

interface ChatState {
  // Sessions
  sessions: SessionListItem[];
  activeSessionId: string | null;

  // Session detail (loaded when active)
  activeSession: {
    id: string;
    title: string;
    templateBadge?: string;
    slideCount: number;
    messages: Message[];
    slides: Slide[];
    createdAt: string;
    updatedAt: string;
  } | null;

  // SSE events for active session
  activeEvents: AgentEvent[];

  // Templates
  templates: TemplateItem[];

  // Generated files for active session
  generatedFiles: GeneratedFile[];

  // Loading states
  isLoadingSession: boolean;
  isSendingMessage: boolean;

  // Actions
  setActiveSession: (id: string | null) => void;
  addSession: (session: SessionListItem) => void;
  deleteSession: (id: string) => void;
  loadSessionDetail: (id: string, detail: Record<string, unknown>) => void;
  addMessage: (sessionId: string, message: Message) => void;
  addAgentEvent: (sessionId: string, event: AgentEvent) => void;
  updateSlideCount: (sessionId: string, count: number) => void;
  setSessionTemplate: (sessionId: string, templateId: string | null) => void;
  searchSessions: (query: string) => SessionListItem[];
  createSession: (data: { title?: string; templateId?: string; brief?: string }) => Promise<string>;
  sendMessage: (sessionId: string, data: { content: string; attachments?: unknown[] }) => Promise<void>;
  fetchSessions: () => Promise<void>;
  fetchTemplates: () => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  activeSession: null,
  activeEvents: [],
  templates: [],
  generatedFiles: [],
  isLoadingSession: false,
  isSendingMessage: false,

  setActiveSession: (id) => {
    set({
      activeSessionId: id,
      activeEvents: [],
      activeSession: id ? null : null,
    });

    if (id) {
      get().loadSessionDetail(id, {} as Record<string, unknown>);
      api.getSession(id).then((detail) => {
        get().loadSessionDetail(id, detail);
      });
    }
  },

  addSession: (session) =>
    set((state) => ({
      sessions: [session, ...state.sessions],
    })),

  deleteSession: async (id) => {
    try {
      await api.deleteSession(id);
    } catch {
      // optimistically remove anyway
    }
    set((state) => ({
      sessions: state.sessions.filter((s) => s.id !== id),
      activeSessionId:
        state.activeSessionId === id ? null : state.activeSessionId,
      activeSession:
        state.activeSessionId === id ? null : state.activeSession,
    }));
  },

  loadSessionDetail: (id, detail) => {
    const d = detail as Record<string, unknown>;
    const messages: Message[] = (d.messages as Message[]) ?? [];
    const slides: Slide[] = (d.slides as Slide[]) ?? [];
    const files: GeneratedFile[] = ((d.generatedFiles as GeneratedFile[]) ?? []);

    set({
      activeSession: {
        id,
        title: (d.title as string) ?? "Untitled",
        templateBadge: d.templateBadge as string | undefined,
        slideCount: (d.slideCount as number) ?? 0,
        messages,
        slides,
        createdAt: (d.createdAt as string) ?? new Date().toISOString(),
        updatedAt: (d.updatedAt as string) ?? new Date().toISOString(),
      },
      generatedFiles: files,
    });
  },

  addMessage: (sessionId, message) =>
    set((state) => {
      if (state.activeSession?.id === sessionId) {
        return {
          activeSession: {
            ...state.activeSession,
            messages: [...state.activeSession.messages, message],
          },
        };
      }
      return state;
    }),

  addAgentEvent: (sessionId, event) =>
    set((state) => {
      if (state.activeSessionId === sessionId) {
        return {
          activeEvents: [...state.activeEvents, event],
        };
      }
      return state;
    }),

  updateSlideCount: (sessionId, count) =>
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === sessionId ? { ...s, slideCount: count } : s
      ),
      activeSession:
        state.activeSession?.id === sessionId
          ? { ...state.activeSession, slideCount: count }
          : state.activeSession,
    })),

  setSessionTemplate: (sessionId, templateId) => {
    const templates = get().templates;
    const tpl = templateId
      ? templates.find((t) => t.id === templateId)
      : null;

    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === sessionId
          ? { ...s, templateBadge: tpl?.name }
          : s
      ),
      activeSession:
        state.activeSession?.id === sessionId
          ? { ...state.activeSession, templateBadge: tpl?.name }
          : state.activeSession,
    }));
  },

  searchSessions: (query) => {
    const lower = query.toLowerCase();
    return get().sessions.filter((s) =>
      s.title.toLowerCase().includes(lower)
    );
  },

  createSession: async (data) => {
    const response = await api.createSession(data);
    const session: SessionListItem = {
      id: response.session_id,
      title: response.title,
      slideCount: 0,
      updatedAt: new Date().toISOString(),
      createdAt: response.created_at,
    };
    set((state) => ({
      sessions: [session, ...state.sessions],
      activeSessionId: session.id,
    }));
    return session.id;
  },

  sendMessage: async (sessionId, data) => {
    set({ isSendingMessage: true });
    try {
      // Add user message optimistically
      get().addMessage(sessionId, {
        id: `temp-${Date.now()}`,
        role: "user",
        content: data.content,
        attachments: (data.attachments as Attachment[]) ?? [],
        createdAt: new Date().toISOString(),
      });

      await api.sendMessage(sessionId, data);
    } finally {
      set({ isSendingMessage: false });
    }
  },

  fetchSessions: async () => {
    try {
      const sessions = await api.listSessions();
      set({ sessions: sessions as SessionListItem[] });
    } catch (error) {
      console.error("Failed to fetch sessions:", error);
    }
  },

  fetchTemplates: async () => {
    try {
      const templates = await api.listTemplates();
      set({ templates: templates as TemplateItem[] });
    } catch (error) {
      console.error("Failed to fetch templates:", error);
    }
  },
}));