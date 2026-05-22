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

  // Derive the active session object from the sessions list
  const activeSession = activeSessionId
    ? sessions.find((s) => s.id === activeSessionId) ?? null
    : null;

  if (!activeSessionId || !activeSession) {
    return <EmptyState />;
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Session Header */}
      <div className="px-6 py-3 border-b border-surface-100 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-gray-200">
            {activeSession.title}
          </h2>
          {activeSession.template_id && (
            <span className="text-xs text-brand-400">
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
      <div className="w-16 h-16 rounded-2xl bg-brand-600/10 flex items-center justify-center">
        <MessageSquarePlus size={32} className="text-brand-400" />
      </div>
      <div>
        <h2 className="text-xl font-semibold text-gray-200 mb-1">
          Create a presentation
        </h2>
        <p className="text-sm text-gray-500 max-w-md">
          Start a new chat session to generate a fully branded PowerPoint
          presentation. Upload documents, describe your goal, or paste a brief.
        </p>
      </div>
      <button
        onClick={handleNewChat}
        className="mt-2 px-6 py-2.5 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-sm font-medium transition-colors"
      >
        New Chat
      </button>
    </div>
  );
}