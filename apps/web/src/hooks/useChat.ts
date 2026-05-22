"use client";

import { useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";

export function useChat() {
  const store = useChatStore();

  const sendMessage = useCallback(
    async (sessionId: string, data: { content: string; attachments?: File[] }) => {
      // Ensure we're sending to the active session
      if (store.activeSessionId !== sessionId) {
        console.error("Cannot send message: session is not active");
        return;
      }
      await store.sendMessage(data.content, data.attachments);
    },
    [store.activeSessionId, store.sendMessage]
  );

  const loadSession = useCallback(
    async (sessionId: string) => {
      await store.setActiveSession(sessionId);
    },
    [store.setActiveSession]
  );

  const createSession = useCallback(
    async (data: { title?: string; templateId?: string; brief?: string }) => {
      // Store's createSession only accepts templateId (title/brief are not supported)
      const sessionId = await store.createSession(data.templateId);
      return sessionId;
    },
    [store.createSession]
  );

  const disconnect = useCallback(() => {
    store.clearActiveSession();
  }, [store.clearActiveSession]);

  return {
    sendMessage,
    loadSession,
    createSession,
    disconnect,
  };
}