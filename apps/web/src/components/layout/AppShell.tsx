"use client";

import { ChatHistorySidebar } from "./ChatHistorySidebar";
import { ChatArea } from "../chat/ChatArea";
import { AgentThoughtPanel } from "../agent/AgentThoughtPanel";
import { SlidePreviewSidebar } from "../preview/SlidePreviewSidebar";
import { useChatStore } from "@/stores/chat-store";

export function AppShell() {
  const activeSession = useChatStore((s) => s.activeSession);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Left: Chat History Sidebar */}
      <ChatHistorySidebar />

      {/* Center: Main Chat Area */}
      <main className="flex-1 flex flex-col min-w-0">
        <ChatArea />
      </main>

      {/* Right: Agent Thought Stream + Slide Preview */}
      {activeSession && (
        <div className="flex flex-col border-l border-surface-100">
          <AgentThoughtPanel />
          <SlidePreviewSidebar />
        </div>
      )}
    </div>
  );
}