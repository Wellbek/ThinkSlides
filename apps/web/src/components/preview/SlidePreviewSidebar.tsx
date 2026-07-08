// SlidePreviewSidebar.tsx
"use client";

import { useChatStore } from "@/stores/chat-store";
import { useLayoutStore } from "@/stores/layout-store";
import { Download, Presentation, PanelRightOpen, PanelRightClose, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import type { SlideFinalizedEvent } from "@thinkslides/schemas";

interface SlidePreviewSidebarProps {
  collapsed?: boolean;
  width?: number;
}

export function SlidePreviewSidebar({ collapsed = false, width = 320 }: SlidePreviewSidebarProps) {
  const { activeEvents, activeFiles, activeMessages } = useChatStore();
  const togglePreview = useLayoutStore((s) => s.togglePreview);

  // Derive generating state
  const isGenerating = activeMessages.some((msg: any) => msg.isStreaming === true) ||
                       activeEvents.some((event: any) => event.type === "thinking" || event.status === "pending");

  const slidesFromActiveEvents = activeEvents
    .filter((event): event is SlideFinalizedEvent => event.type === "slide_finalized")
    .map((event) => event.slideData as any);

  const slidesFromMessages = activeMessages.flatMap(
    (msg) =>
      (msg.agentEventStream || [])
        .filter((event): event is SlideFinalizedEvent => event.type === "slide_finalized")
        .map((event) => event.slideData as any)
  );

  const seenIndices = new Set(slidesFromActiveEvents.map((s: any) => s.slideIndex));
  const allSlides = [
    ...slidesFromActiveEvents,
    ...slidesFromMessages.filter((s: any) => !seenIndices.has(s.slideIndex)),
  ].sort((a: any, b: any) => a.slideIndex - b.slideIndex);

  const files = activeFiles.map((file) => ({
    id: file.id,
    downloadUrl: file.file_url,
    fileName: file.filename,
  }));

  if (collapsed) {
    return (
      <div className="w-full h-full bg-charcoal flex flex-col items-center py-4 gap-2">
        <button
          onClick={togglePreview}
          className="p-2 rounded-md hover:bg-slate-dark text-fog hover:text-paper transition-colors"
          title="Expand preview"
        >
          <PanelRightOpen size={20} />
        </button>
        {allSlides.length > 0 && (
          <span className="text-caption text-ice font-mono tabular-nums mt-1">
            {allSlides.length}
          </span>
        )}
        {isGenerating && (
          <div className="mt-1">
            <Loader2 size={18} className="text-ice animate-spin" />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full" style={{ width }}>
      <div className="flex-shrink-0 px-4 py-2.5 border-b border-iron flex items-center justify-between">
        <span className="text-caption font-medium text-paper">Slide Preview</span>
        <div className="flex items-center gap-2">
          {allSlides.length > 0 && (
            <span className="text-caption text-fog font-mono tabular-nums">{allSlides.length} slides</span>
          )}
          {isGenerating && (
            <Loader2 size={14} className="text-ice animate-spin" />
          )}
          <button
            onClick={togglePreview}
            className="p-1 rounded-md hover:bg-slate-dark text-fog hover:text-paper transition-colors"
            title="Collapse preview"
          >
            <PanelRightClose size={16} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3 scroll-smooth">
        <AnimatePresence mode="popLayout">
          {allSlides.length === 0 && (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="text-center py-10"
            >
              <Presentation size={32} className="mx-auto text-fog mb-2" />
              <p className="text-caption text-fog">
                Slides will appear here as they are generated
              </p>
            </motion.div>
          )}
          {allSlides.map((slide, idx) => (
            <motion.div
              key={slide.slideIndex}
              initial={{ opacity: 0, y: 12, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ delay: idx * 0.05, type: "spring", stiffness: 500, damping: 30 }}
              className="rounded-lg border border-iron bg-charcoal overflow-hidden hover:border-ice/30 transition-colors group"
            >
              <div className="p-3">
                {(event as SlideFinalizedEvent).previewUrl ? (
                  <div className="bg-paper rounded-lg aspect-video relative shadow-inner overflow-hidden">
                    <img
                      src={(event as SlideFinalizedEvent).previewUrl}
                      alt={`Slide ${slide.slideIndex + 1}`}
                      className="w-full h-full object-contain"
                    />
                    <span className="absolute top-1.5 left-1.5 bg-obsidian/90 text-paper rounded-md w-5 h-5 flex items-center justify-center text-caption font-bold shadow-sm backdrop-blur-sm">
                      {slide.slideIndex + 1}
                    </span>
                  </div>
                ) : (
                  <div className="bg-paper rounded-lg aspect-video flex items-center justify-center text-void text-caption relative shadow-inner overflow-hidden">
                    <div className="absolute inset-x-3 top-3 space-y-[3px]">
                      <div className="h-[3px] bg-iron/60 rounded-full w-4/5" />
                      <div className="h-[2px] bg-iron/40 rounded-full w-3/5" />
                    </div>
                    <div className="absolute inset-x-3 bottom-2.5 space-y-[2px]">
                      <div className="h-[2px] bg-iron/50 rounded-full w-full" />
                      <div className="h-[2px] bg-iron/40 rounded-full w-5/6" />
                      <div className="h-[2px] bg-iron/30 rounded-full w-2/3" />
                    </div>
                    <div className="text-center z-10">
                      <p className="font-semibold text-sm text-gray-700">
                        {slide.slideIndex + 1}
                      </p>
                      <p className="text-gray-400 text-[9px] mt-0.5 uppercase tracking-wider">
                        {slide.layoutName}
                      </p>
                    </div>
                    <span className="absolute top-1.5 left-1.5 bg-obsidian/90 text-paper rounded-md w-5 h-5 flex items-center justify-center text-caption font-bold shadow-sm backdrop-blur-sm">
                      {slide.slideIndex + 1}
                    </span>
                    <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
                  </div>
                )}
              </div>
              <div className="px-3 pb-2.5">
                <p className="text-caption text-fog truncate leading-relaxed">
                  {slide.contents
                    ?.filter(
                      (c: any): c is { type: "text"; runs: { text: string }[] } =>
                        typeof c === "object" && c !== null && "type" in c && c.type === "text"
                    )
                    .map((c: { type: "text"; runs: { text: string }[] }) =>
                      c.runs.map((r) => r.text).join(" ")
                    )
                    .join(" · ") || "No text content"}
                </p>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {files.length > 0 && (
        <div className="flex-shrink-0 border-t border-iron p-3 space-y-2">
          {files.map((file) => {
            const token = typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
            const downloadUrl = file.downloadUrl
              ? `${file.downloadUrl}${token ? `${file.downloadUrl.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : ""}`
              : "#";
            return (
              <a
                key={file.id}
                href={downloadUrl}
                download
                className="flex items-center gap-2 px-3 py-2 bg-ice/5 hover:bg-ice/10 rounded-lg text-body text-ice transition-colors border border-ice/20"
              >
                <Download size={14} />
                <span className="truncate">{file.fileName}</span>
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}