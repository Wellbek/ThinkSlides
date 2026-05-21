"use client";

import { useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";
import { api } from "@/lib/api";
import { useSSE } from "./useSSE";
import type { AgentEvent } from "@slideagent/schemas/agent-events";

export function useChat() {
  const store = useChatStore();
  const activeSessionId = store.activeSessionId;

  const handleSSEEvent = useCallback(
    (event: AgentEvent) => {
      if (!activeSessionId) return;
      store.addAgentEvent(activeSessionId, event);

      if (event.type === "slide_finalized") {
        store.updateSlideCount(activeSessionId, event.slideIndex + 1);
      }
    },
    [activeSessionId, store]
  );

  const { disconnect } = useSSE({
    sessionId: activeSessionId,
    onEvent: handleSSEEvent,
  });

  const sendMessage = useCallback(
    async (
      sessionId: string,
      data: { content: string; attachments?: unknown[] }
    ) => {
      try {
        const response = await api.sendMessage(sessionId, data);
        store.addMessage(sessionId, {
          id: response.message_id,
          role: "assistant",
          content: "",
          attachments: [],
          createdAt: new Date().toISOString(),
        });
      } catch (error) {
        console.error("Failed to send message:", error);
        throw error;
      }
    },
    [store]
  );

  const loadSession = useCallback(
    async (sessionId: string) => {
      try {
        const detail = await api.getSession(sessionId);
        store.setActiveSession(sessionId);
        store.loadSessionDetail(sessionId, detail);
      } catch (error) {
        console.error("Failed to load session:", error);
      }
    },
    [store]
  );

  const createSession = useCallback(
    async (data: { title?: string; templateId?: string; brief?: string }) => {
      try {
        const response = await api.createSession(data);
        store.addSession({
          id: response.session_id,
          title: response.title,
          templateBadge: undefined,
          slideCount: 0,
          updatedAt: new Date().toISOString(),
          createdAt: response.created_at,
        });
        store.setActiveSession(response.session_id);
        return response.session_id;
      } catch (error) {
        console.error("Failed to create session:", error);
        throw error;
      }
    },
    [store]
  );

  return {
    sendMessage,
    loadSession,
    createSession,
    disconnect,
  };
}