"use client";

import { useChatStore } from "@/stores/chat-store";
import { Download, Presentation } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import type { SlideFinalizedEvent } from "@thinkslides/schemas";

export function SlidePreviewSidebar() {
  const { activeEvents, activeFiles, activeMessages } = useChatStore();

  // Extract slides from slide_finalized events in both activeEvents and message event streams
  const slidesFromActiveEvents = activeEvents
    .filter((event): event is SlideFinalizedEvent => event.type === "slide_finalized")
    .map((event) => event.slideData as any);

  const slidesFromMessages = activeMessages.flatMap(
    (msg) =>
      (msg.agentEventStream || [])
        .filter((event): event is SlideFinalizedEvent => event.type === "slide_finalized")
        .map((event) => event.slideData as any)
  );

  // Deduplicate by slideIndex (prefer activeEvents which are more recent)
  const seenIndices = new Set(slidesFromActiveEvents.map((s: any) => s.slideIndex));
  const allSlides = [
    ...slidesFromActiveEvents,
    ...slidesFromMessages.filter((s: any) => !seenIndices.has(s.slideIndex)),
  ].sort((a: any, b: any) => a.slideIndex - b.slideIndex);

  // Map GeneratedFile to the shape expected by the UI
  const files = activeFiles.map((file) => ({
    id: file.id,
    downloadUrl: file.file_url,
    fileName: file.filename,
  }));

  return (
    <div className="flex flex-col" style={{ width: 320 }}>
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-surface-100 flex items-center justify-between">
        <span className="text-sm font-medium text-gray-300">Slide Preview</span>
        {allSlides.length > 0 && (
          <span className="text-xs text-gray-500 tabular-nums">{allSlides.length} slides</span>
        )}
      </div>

      {/* Slide thumbnails */}
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
              <Presentation size={32} className="mx-auto text-gray-600 mb-2" />
              <p className="text-xs text-gray-500">
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
              className="rounded-xl border border-gray-700/30 bg-gradient-to-b from-gray-800/40 to-gray-900/30 overflow-hidden hover:border-gray-600/40 transition-colors group"
            >
              <div className="p-3">
                <div className="bg-white rounded-lg aspect-video flex items-center justify-center text-gray-800 text-xs relative shadow-inner overflow-hidden">
                  {/* Decorative mockup content */}
                  <div className="absolute inset-x-3 top-3 space-y-[3px]">
                    <div className="h-[3px] bg-gray-300/60 rounded-full w-4/5" />
                    <div className="h-[2px] bg-gray-300/40 rounded-full w-3/5" />
                  </div>
                  <div className="absolute inset-x-3 bottom-2.5 space-y-[2px]">
                    <div className="h-[2px] bg-gray-200/50 rounded-full w-full" />
                    <div className="h-[2px] bg-gray-200/40 rounded-full w-5/6" />
                    <div className="h-[2px] bg-gray-200/30 rounded-full w-2/3" />
                  </div>
                  <div className="text-center z-10">
                    <p className="font-semibold text-sm text-gray-700">
                      {slide.slideIndex + 1}
                    </p>
                    <p className="text-gray-400 text-[9px] mt-0.5 uppercase tracking-wider">
                      {slide.layoutName}
                    </p>
                  </div>
                  {/* Slide number badge */}
                  <span className="absolute top-1.5 left-1.5 bg-gray-800/90 text-white rounded-md w-5 h-5 flex items-center justify-center text-[9px] font-bold shadow-sm backdrop-blur-sm">
                    {slide.slideIndex + 1}
                  </span>
                  {/* Hover shimmer */}
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
                </div>
              </div>
              <div className="px-3 pb-2.5">
                <p className="text-[11px] text-gray-400 truncate leading-relaxed">
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

      {/* Download section */}
      {files.length > 0 && (
        <div className="border-t border-surface-100 p-3 space-y-2">
          {files.map((file) => {
            // Build authenticated download URL
            const token = typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
            const downloadUrl = file.downloadUrl
              ? `${file.downloadUrl}${token ? `${file.downloadUrl.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : ""}`
              : "#";

            return (
              <a
                key={file.id}
                href={downloadUrl}
                download
                className="flex items-center gap-2 px-3 py-2 bg-brand-600/10 hover:bg-brand-600/20 rounded-lg text-sm text-brand-300 transition-colors"
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