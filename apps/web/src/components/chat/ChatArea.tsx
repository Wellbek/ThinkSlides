"use client";

import { useChatStore } from "@/stores/chat-store";
import { useLayoutStore } from "@/stores/layout-store";
import { ChatMessageList } from "./ChatMessageList";
import { ChatInput } from "./ChatInput";
import { TemplateSelector } from "./TemplateSelector";
import { MessageSquarePlus, PanelRightClose, Loader2 } from "lucide-react";

export function ChatArea() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useChatStore((s) => s.sessions);
  const createSession = useChatStore((s) => s.createSession);
  const isGenerating = useChatStore((s) => s.isGenerating());
  const toggleChat = useLayoutStore((s) => s.toggleChat);

  const activeSession = activeSessionId
    ? sessions.find((s) => s.id === activeSessionId) ?? null
    : null;

  if (!activeSessionId || !activeSession) {
    return <EmptyState />;
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Session Header */}
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
        <div className="flex items-center gap-2">
          {isGenerating && (
            <div className="flex items-center gap-1.5 text-ice text-caption">
              <Loader2 size={14} className="animate-spin" />
              <span>Generating...</span>
            </div>
          )}
          <TemplateSelector />
          <button
            onClick={toggleChat}
            className="p-1.5 rounded-md hover:bg-slate-dark text-fog hover:text-paper transition-colors"
            title="Collapse chat"
          >
            <PanelRightClose size={16} />
          </button>
        </div>
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
