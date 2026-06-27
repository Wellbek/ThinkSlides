// components/layout/AppShell.tsx
"use client";

import { useRef, useCallback } from "react";
import { ChatHistorySidebar } from "./ChatHistorySidebar";
import { ChatArea } from "../chat/ChatArea";
import { SlidePreviewSidebar } from "../preview/SlidePreviewSidebar";
import { SettingsModal } from "./SettingsModal";
import { ResizeHandle } from "./ResizeHandle";
import { useChatStore } from "@/stores/chat-store";
import { useLayoutStore } from "@/stores/layout-store";
import { COLLAPSED_WIDTH } from "@/stores/layout-store";
import { MessageSquare, Loader2 } from "lucide-react";

export function AppShell() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const activeMessages = useChatStore((s) => s.activeMessages);
  const activeEvents = useChatStore((s) => s.activeEvents);
  const {
    sidebarWidth,
    sidebarCollapsed,
    previewWidth,
    previewCollapsed,
    chatCollapsed,
    setSidebarWidth,
    setPreviewWidth,
    toggleChat,
  } = useLayoutStore();

  const sidebarActualWidth = sidebarCollapsed ? COLLAPSED_WIDTH : sidebarWidth;
  const previewActualWidth = previewCollapsed ? COLLAPSED_WIDTH : previewWidth;

  // Sidebar drag state
  const sidebarDrag = useRef({ startX: 0, startWidth: sidebarWidth });
  const handleSidebarStart = useCallback(() => {
    sidebarDrag.current = { startX: 0, startWidth: sidebarWidth };
  }, [sidebarWidth]);
  const handleSidebarMove = useCallback(
    (delta: number) => {
      if (sidebarCollapsed) return;
      const newWidth = sidebarDrag.current.startWidth + delta;
      setSidebarWidth(Math.max(200, Math.min(500, newWidth)));
      sidebarDrag.current.startWidth = newWidth;
    },
    [sidebarCollapsed, setSidebarWidth]
  );

  // Preview drag state (right handle, subtract delta)
  const previewDrag = useRef({ startX: 0, startWidth: previewWidth });
  const handlePreviewStart = useCallback(() => {
    previewDrag.current = { startX: 0, startWidth: previewWidth };
  }, [previewWidth]);
  const handlePreviewMove = useCallback(
    (delta: number) => {
      if (previewCollapsed) return;
      const newWidth = previewDrag.current.startWidth - delta;
      setPreviewWidth(Math.max(240, Math.min(600, newWidth)));
      previewDrag.current.startWidth = newWidth;
    },
    [previewCollapsed, setPreviewWidth]
  );

  // Activity indicator
  const isGenerating =
    activeMessages.some((m: any) => m.isStreaming === true) ||
    activeEvents.some((e: any) => e.type === "thinking" || e.status === "pending");

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-void">
      {/* Left Sidebar */}
      <div
        style={{ width: sidebarActualWidth, minWidth: sidebarActualWidth }}
        className="flex-shrink-0"
      >
        <ChatHistorySidebar collapsed={sidebarCollapsed} />
      </div>

      {/* Resize handle for sidebar */}
      {!sidebarCollapsed && (
        <ResizeHandle onResizeStart={handleSidebarStart} onResizeMove={handleSidebarMove} />
      )}

      {/* Center Chat Area */}
      {!chatCollapsed ? (
        <main className="flex-1 flex flex-col min-w-0 bg-charcoal">
          <ChatArea />
        </main>
      ) : (
        <div className="w-12 flex-shrink-0 bg-charcoal border-r border-iron flex flex-col items-center py-4 gap-2">
          <button
            onClick={toggleChat}
            className="p-2 rounded-md hover:bg-slate-dark text-fog hover:text-paper transition-colors"
            title="Expand chat"
          >
            <MessageSquare size={20} />
          </button>
          {activeSessionId && isGenerating && (
            <Loader2 size={18} className="text-ice animate-spin mt-1" />
          )}
        </div>
      )}

      {/* Right Preview Sidebar */}
      {activeSessionId && (
        <>
          {!chatCollapsed && !previewCollapsed && (
            <ResizeHandle onResizeStart={handlePreviewStart} onResizeMove={handlePreviewMove} />
          )}
          <div
            style={{ width: previewActualWidth, minWidth: previewActualWidth }}
            className="flex-shrink-0 border-l border-iron bg-charcoal"
          >
            <SlidePreviewSidebar collapsed={previewCollapsed} width={previewActualWidth} />
          </div>
        </>
      )}

      <SettingsModal />
    </div>
  );
}