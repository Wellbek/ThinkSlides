"use client";

import { ChatHistorySidebar } from "./ChatHistorySidebar";
import { ChatArea } from "../chat/ChatArea";
import { SlidePreviewSidebar } from "../preview/SlidePreviewSidebar";
import { useChatStore } from "@/stores/chat-store";

export function AppShell() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-void">
      {/* Left: Chat History Sidebar — Void surface */}
      <ChatHistorySidebar />

      {/* Center: Main Chat Area — Charcoal canvas */}
      <main className="flex-1 flex flex-col min-w-0 bg-charcoal">
        <ChatArea />
      </main>

      {/* Right: Slide Preview Sidebar — Charcoal with Iron border */}
      {activeSessionId && (
        <div className="flex flex-col border-l border-iron w-[320px] bg-charcoal">
          <SlidePreviewSidebar />
        </div>
      )}
    </div>
  );
}
