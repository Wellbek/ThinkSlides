"use client";

import { useChatStore } from "@/stores/chat-store";
import { ChatMessageList } from "./ChatMessageList";
import { ChatInput } from "./ChatInput";
import { TemplateSelector } from "./TemplateSelector";
import { MessageSquarePlus } from "lucide-react";

export function ChatArea() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const createSession = useChatStore((s) => s.createSession);

  const activeSession = activeSessionId
    ? sessions.find((s) => s.id === activeSessionId) ?? null
    : null;

  if (!activeSessionId || !activeSession) {
    return <EmptyState />;
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Session Header — Obsidian bar */}
      <div className="px-6 py-3 border-b border-iron bg-obsidian flex items-center justify-between">
        <div>
          <h2 className="text-caption font-medium text-paper">
            {activeSession.title}
          </h2>
          {activeSession.template_id && (
            <span className="text-caption text-ice">
              Template: {activeSession.template_id}
            </span>
          )}
        </div>
        <TemplateSelector />
      </div>

      {/* Messages */}
      <ChatMessageList />

      {/* Input */}
      <ChatInput />
    </div>
  );
}

function EmptyState() {
  const createSession = useChatStore((s) => s.createSession);

  const handleNewChat = async () => {
    try {
      await createSession();
    } catch (err) {
      console.error("Failed to create session:", err);
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-lg bg-ice/10 flex items-center justify-center shadow-subtle">
        <MessageSquarePlus size={32} className="text-ice" />
      </div>
      <div>
        <h2 className="text-heading-sm font-medium text-paper mb-1">
          Create a presentation
        </h2>
        <p className="text-caption text-fog max-w-md">
          Start a new chat session to generate a fully branded PowerPoint
          presentation. Upload documents, describe your goal, or paste a brief.
        </p>
      </div>
      <button
        onClick={handleNewChat}
        className="btn-outlined mt-2"
      >
        New Chat
      </button>
    </div>
  );
}
