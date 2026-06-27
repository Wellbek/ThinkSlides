"use client";

import { useCallback } from "react";
import { useChatStore } from "@/stores/chat-store";

export function useChat() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sendMessageAction = useChatStore((s) => s.sendMessage);
  const setActiveSession = useChatStore((s) => s.setActiveSession);
  const createSessionAction = useChatStore((s) => s.createSession);
  const clearActiveSession = useChatStore((s) => s.clearActiveSession);

  const sendMessage = useCallback(
    async (sessionId: string, data: { content: string; attachments?: File[] }) => {
      if (activeSessionId !== sessionId) {
        console.error("Cannot send message: session is not active");
        return;
      }
      await sendMessageAction(data.content, data.attachments);
    },
    [activeSessionId, sendMessageAction]
  );

  const loadSession = useCallback(
    async (sessionId: string) => {
      await setActiveSession(sessionId);
    },
    [setActiveSession]
  );

  const createSession = useCallback(
    async (data: { title?: string; templateId?: string; brief?: string }) => {
      const sessionId = await createSessionAction(data.templateId);
      return sessionId;
    },
    [createSessionAction]
  );

  const disconnect = useCallback(() => {
    clearActiveSession();
  }, [clearActiveSession]);

  return {
    sendMessage,
    loadSession,
    createSession,
    disconnect,
  };
}
