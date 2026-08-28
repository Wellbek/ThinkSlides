import { create } from 'zustand';
import { apiGet, apiPost, apiDelete, apiPut, apiUpload } from '@/lib/api';
import { SSEClient, type SSEEventHandler } from '@/lib/sse';
import { useSettingsStore } from '@/stores/settings-store';
import { AGENT_EVENT_TYPES } from '@thinkslides/schemas';
import type {
  AgentEvent,
  AgentEventType,
  CodeDeltaEvent,
  CodeReadyEvent,
  DetailLevel,
  DoneEvent,
  EffortMode,
  RateLimitEvent,
} from '@thinkslides/schemas';

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
  // Set on individual chart/image assets (#187), unset on the whole-deck
  // pptx row - which slide the asset was rendered onto.
  slide_index?: number;
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
  /**
   * How much content depth the next generation should aim for. Kept on the
   * store for the same reason as `mode` - shared between the home view and
   * the in-session composer.
   */
  detailLevel: DetailLevel;
  /** Whether generated decks carry their research sources as references. */
  includeSources: boolean;
  totalElapsedMs: number | null;
  isGenerating: boolean;
  /**
   * The provider back-off happening right now, or null when none is.
   *
   * Transient state rather than a recorded event: a wait is worth showing
   * while it is on and worth nothing once it is over, so it lives here and is
   * dropped as soon as the run moves on (see `beginRetry`). The transcript
   * then records what the agent did, not how long it spent waiting.
   */
  activeRetry: RateLimitEvent | null;
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
  setDetailLevel: (detailLevel: DetailLevel) => void;
  setIncludeSources: (includeSources: boolean) => void;
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

/**
 * How long a finished wait stays on screen after its countdown reaches zero.
 *
 * The retried call itself takes as long as it takes, and nothing announces
 * that it started - so without this the strip would vanish the instant the
 * countdown ran out, too fast to read the "retrying now" it ends on.
 */
const RETRY_LINGER_MS = 900;

/** Retires the current back-off strip when its wait is up. */
let retryExpiryTimer: ReturnType<typeof setTimeout> | null = null;

function cancelRetryExpiry(): void {
  if (retryExpiryTimer !== null) {
    clearTimeout(retryExpiryTimer);
    retryExpiryTimer = null;
  }
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
  detailLevel: 'detailed',
  // Off by default: a deck that cites its sources is a deliberate choice, and
  // most decks have no research behind them to cite.
  includeSources: false,
  totalElapsedMs: null,
  isGenerating: false,
  activeRetry: null,

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
      if (get().activeSessionId === sessionId) cancelRetryExpiry();
      set((state) => ({
        sessions: state.sessions.filter((s) => s.id !== sessionId),
        activeSessionId: state.activeSessionId === sessionId ? null : state.activeSessionId,
        activeMessages: state.activeSessionId === sessionId ? [] : state.activeMessages,
        activeEvents: state.activeSessionId === sessionId ? [] : state.activeEvents,
        activeFiles: state.activeSessionId === sessionId ? [] : state.activeFiles,
        activeRetry: state.activeSessionId === sessionId ? null : state.activeRetry,
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
    cancelRetryExpiry();
    set({
      activeSessionId: sessionId,
      activeMessages: [],
      activeEvents: [],
      activeFiles: [],
      activeRetry: null,
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
        // Built from the id, not by prefixing the path the API returned.
        // That path is already the orchestrator's own route
        // ("/api/files/<id>"), so prefixing it produced
        // "/api/orchestrator/api/files/<id>" and every download on a
        // reloaded session 404'd - the deck as well as the assets. Live
        // files were fine, because onFileGenerated builds this same shape
        // itself, which is why it only ever broke after a reload.
        file_url: `/api/orchestrator/files/${f.id}`,
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
      /**
       * Take down the back-off strip: the run has moved on.
       *
       * Any event the agent itself produces is proof the awaited provider
       * call went through, since a wait is announced from inside that call
       * and nothing else comes out until it returns. That makes "the next
       * event" the honest end of a wait - more so than the countdown, which
       * only says when the retry was due to be attempted.
       */
      const endRetry = () => {
        cancelRetryExpiry();
        if (get().activeRetry) set({ activeRetry: null });
      };
      /**
       * Put a wait on screen, and arrange for it to leave.
       *
       * The linger is a backstop for the case the next event is slow: a
       * retried call can take as long as the original, and a countdown frozen
       * at zero would keep claiming a wait that is already over.
       */
      const beginRetry = (ev: RateLimitEvent) => {
        cancelRetryExpiry();
        set({ activeRetry: ev, isGenerating: true });
        const waitMs = Math.max(0, (Number(ev.retryInSeconds) || 0) * 1000);
        retryExpiryTimer = setTimeout(() => {
          retryExpiryTimer = null;
          if (get().activeRetry === ev) set({ activeRetry: null });
        }, waitMs + RETRY_LINGER_MS);
      };
      // Most events differ only in payload: record them on the active
      // message and keep the run marked live.
      const record: SSEEventHandler = (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          endRetry();
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      };
      // The same, without asserting the run is live: a question pauses it, and
      // validation_unresolved is reported once it has stopped trying.
      const recordOnly: SSEEventHandler = (e) => {
        try {
          appendEventToActiveMessage(JSON.parse(e.data));
          endRetry();
        } catch (err) { console.error(err); }
      };
      /**
       * A chunk of a script, folded into the one entry that shows it.
       *
       * Not appended as its own event. The stream is persisted and replayed
       * after a reload, so a run that wrote a 40-chunk script would replay
       * as 40 rows - and, worse, a replay that overlapped what the browser
       * already had would append the script to itself. So the chunks
       * accumulate into a single `code_delta` entry per `stepId`, and a
       * `seq` already folded in is dropped. That is what `seq` is for: it is
       * the only thing that makes a delta idempotent, and this is the
       * failure `validation_unresolved` is the cautionary tale for - an
       * event whose handling nobody checked.
       */
      const onCodeDelta: SSEEventHandler = (e) => {
        try {
          const delta = JSON.parse(e.data) as CodeDeltaEvent;
          set((state) => {
            const activeMsg = findActiveAssistantMessage(state.activeMessages);
            if (!activeMsg) return state;
            const stream = activeMsg.agentEventStream || [];
            const at = stream.findIndex(
              (event): event is CodeDeltaEvent =>
                event.type === "code_delta" && event.stepId === delta.stepId
            );
            if (at === -1) {
              return {
                activeMessages: state.activeMessages.map((msg) =>
                  msg.id === activeMsg.id
                    ? { ...msg, agentEventStream: [...stream, delta] }
                    : msg
                ),
              };
            }
            const existing = stream[at] as CodeDeltaEvent;
            if (delta.seq <= existing.seq) return state;
            const merged: CodeDeltaEvent = {
              ...existing,
              seq: delta.seq,
              text: existing.text + delta.text,
              timestamp: delta.timestamp,
            };
            const next = [...stream];
            next[at] = merged;
            return {
              activeMessages: state.activeMessages.map((msg) =>
                msg.id === activeMsg.id
                  ? { ...msg, agentEventStream: next }
                  : msg
              ),
            };
          });
          endRetry();
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      };
      /**
       * The script the model settled on.
       *
       * Terminal for a step and authoritative: it carries the whole source,
       * so a subscriber that connected halfway through and one that saw
       * every chunk end up showing exactly the same code. The accumulated
       * deltas are replaced rather than added to, which is also what makes a
       * dropped chunk invisible instead of a hole in the middle of a script.
       */
      const onCodeReady: SSEEventHandler = (e) => {
        try {
          const ready = JSON.parse(e.data) as CodeReadyEvent;
          set((state) => {
            const activeMsg = findActiveAssistantMessage(state.activeMessages);
            if (!activeMsg) return state;
            const stream = (activeMsg.agentEventStream || []).filter(
              (event) =>
                !(event.type === "code_delta" &&
                  (event as CodeDeltaEvent).stepId === ready.stepId)
            );
            return {
              activeMessages: state.activeMessages.map((msg) =>
                msg.id === activeMsg.id
                  ? { ...msg, agentEventStream: [...stream, ready] }
                  : msg
              ),
            };
          });
          endRetry();
          set({ isGenerating: true });
        } catch (err) { console.error(err); }
      };
      const onRateLimit: SSEEventHandler = (e) => {
        try {
          beginRetry(JSON.parse(e.data) as RateLimitEvent);
        } catch (err) { console.error(err); }
      };
      const onTimerUpdate: SSEEventHandler = (e) => {
        try {
          const data = JSON.parse(e.data) as { elapsedMs: number };
          set({ totalElapsedMs: data.elapsedMs, isGenerating: true });
        } catch (err) { console.error(err); }
      };
      const onSessionTitleUpdated: SSEEventHandler = (e) => {
        try {
          const data = JSON.parse(e.data) as { session_id: string; title: string };
          set((state) => ({
            sessions: state.sessions.map((s) =>
              s.id === data.session_id ? { ...s, title: data.title } : s
            ),
          }));
        } catch (err) { console.error(err); }
      };
      const onFileGenerated: SSEEventHandler = (e) => {
        try {
          endRetry();
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
                ? {
                    ...msg,
                    generatedFiles: [...(msg.generatedFiles || []), generatedFile],
                    // Also part of the run's own story, not only of the
                    // side panel: the activity timeline has always had a
                    // row for the finished deck and it never rendered,
                    // because this event reached the file list and nothing
                    // else. A run ended with no sign of what it produced.
                    agentEventStream: [...(msg.agentEventStream || []), JSON.parse(e.data)],
                  }
                : msg
            );
            return { activeMessages: updatedMessages, activeFiles: [...state.activeFiles, generatedFile] };
          });
        } catch (err) { console.error(err); }
      };

      const onDone: SSEEventHandler = (e) => {
        try {
          endRetry();
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

            // Add completion message if we have total elapsed time. A paused
            // run is still going, so it gets no "Finished in ..." footer.
            let completionMessage: ChatMessage | null = null;
            if (!isPaused && totalElapsedMs && totalElapsedMs > 0) {
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
      };

      // One handler per declared event name. The Record type makes a missing
      // one a compile error, so an event added to AGENT_EVENT_TYPES cannot end
      // up subscribed but silently ignored - the failure mode that hid
      // validation_unresolved.
      const handlers: Record<AgentEventType, SSEEventHandler> = {
        thought: record,
        tool_call: record,
        tool_result: record,
        slide_finalized: record,
        error: record,
        progress: record,
        // A provider back-off, narrated while it happens - as store state,
        // not as a transcript entry. isGenerating stays true: the run is
        // waiting, not finished.
        rate_limit: onRateLimit,
        question: recordOnly,
        validation_unresolved: recordOnly,
        // A connection that could not be reached. Recorded, not
        // thrown: the run carried on without it, and the user has to
        // be able to tell that from a deck that simply found nothing.
        connection_degraded: recordOnly,
        // The agent writing a script, and the script it settled on. See
        // onCodeDelta for why these are folded rather than appended.
        code_delta: onCodeDelta,
        code_ready: onCodeReady,
        timer_update: onTimerUpdate,
        session_title_updated: onSessionTitleUpdated,
        file_generated: onFileGenerated,
        done: onDone,
      };

      for (const eventType of AGENT_EVENT_TYPES) {
        client.on(eventType, handlers[eventType]);
      }

      client.connect();
      set({ sseClient: client });
    } catch (err) {
      console.error('Failed to load session:', err);
      set({ isLoading: false });
    }
  },

  sendMessage: async (content: string, files?: File[]) => {
    const { activeSessionId, mode, detailLevel, includeSources, sseClient } = get();
    if (!activeSessionId) return;

    // The SSE client auto-closes after an unpaused "done" (see sse.ts) and
    // nothing else reopens it. Without this, a message sent after an earlier
    // turn already finished enqueues a real job that runs to completion on
    // the server while nothing is listening for its events: the UI never
    // shows the result, and isGenerating never clears, since that only
    // happens inside the SSE "done" handler.
    if (sseClient && !sseClient.isConnected()) {
      sseClient.connect();
    }

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
      // Close out any earlier assistant turn before opening a new one. A turn
      // that ended on a question stays deliberately incomplete so its "done"
      // does not read as finished - but once the user replies it is over, and
      // leaving it live would keep its spinner running for the rest of the
      // session and confuse findActiveAssistantMessage about where events go.
      activeMessages: [
        ...state.activeMessages.map((m) =>
          m.role === 'assistant' && !m.isComplete ? { ...m, isComplete: true } : m
        ),
        optimisticUser,
        optimisticAssistant,
      ],
    }));
    try {
      if (files && files.length > 0) {
        const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
        const formData = new FormData();
        formData.append('content', content);
        formData.append('mode', effectiveMode);
        formData.append('detail_level', detailLevel);
        formData.append('include_sources', String(includeSources));
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
          { content, mode: effectiveMode, detail_level: detailLevel, include_sources: includeSources }
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
    cancelRetryExpiry();
    set({
      activeSessionId: null,
      activeMessages: [],
      activeEvents: [],
      activeFiles: [],
      activeRetry: null,
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

  setDetailLevel: (detailLevel: DetailLevel) => set({ detailLevel }),
  setIncludeSources: (includeSources: boolean) => set({ includeSources }),
}));