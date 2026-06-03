"use client";

import { ChatHistorySidebar } from "./ChatHistorySidebar";
import { ChatArea } from "../chat/ChatArea";
import { SlidePreviewSidebar } from "../preview/SlidePreviewSidebar";
import { useChatStore } from "@/stores/chat-store";

export function AppShell() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Left: Chat History Sidebar */}
      <ChatHistorySidebar />

      {/* Center: Main Chat Area */}
      <main className="flex-1 flex flex-col min-w-0">
        <ChatArea />
      </main>

      {/* Right: Slide Preview Sidebar (only when a session is active) */}
      {activeSessionId && (
        <div className="flex flex-col border-l border-gray-800 w-[320px]">
          <SlidePreviewSidebar />
        </div>
      )}
    </div>
  );
}