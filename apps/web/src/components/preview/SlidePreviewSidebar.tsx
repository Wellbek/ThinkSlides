"use client";

import { useChatStore } from "@/stores/chat-store";
import { Download, FilePresentation } from "lucide-react";
import type { Slide } from "@slideagent/schemas/slide";

export function SlidePreviewSidebar() {
  const { activeSession, generatedFiles } = useChatStore();

  const slides: Slide[] = activeSession?.slides ?? [];
  const files = generatedFiles ?? [];

  return (
    <div className="flex flex-col" style={{ width: 320 }}>
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-surface-100 flex items-center justify-between">
        <span className="text-sm font-medium text-gray-300">
          Slide Preview
        </span>
        {slides.length > 0 && (
          <span className="text-xs text-gray-500">{slides.length} slides</span>
        )}
      </div>

      {/* Slide thumbnails */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {slides.length === 0 && (
          <div className="text-center py-8">
            <FilePresentation
              size={32}
              className="mx-auto text-gray-600 mb-2"
            />
            <p className="text-xs text-gray-500">
              Slides will appear here as they are generated
            </p>
          </div>
        )}

        {slides.map((slide, i) => (
          <div key={i} className="slide-preview-card">
            <div className="p-3">
              <div className="bg-white rounded aspect-video flex items-center justify-center text-gray-800 text-xs relative">
                <div className="text-center">
                  <p className="font-semibold text-sm">
                    Slide {slide.slideIndex + 1}
                  </p>
                  <p className="text-gray-500 text-[10px] mt-0.5">
                    {slide.layoutName}
                  </p>
                </div>
                <span className="absolute top-1 left-1 bg-gray-200 text-gray-600 rounded-full w-5 h-5 flex items-center justify-center text-[10px] font-medium">
                  {slide.slideIndex + 1}
                </span>
              </div>
            </div>
            <div className="px-3 pb-2">
              <p className="text-xs text-gray-400 truncate">
                {slide.contents
                  .filter((c) => c.type === "text")
                  .map((c) => (c as { type: "text"; runs: { text: string }[] }).runs.map((r) => r.text).join(" "))
                  .join(" · ") || "No text content"}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Download section */}
      {files.length > 0 && (
        <div className="border-t border-surface-100 p-3 space-y-2">
          {files.map((file) => (
            <a
              key={file.id}
              href={file.downloadUrl}
              className="flex items-center gap-2 px-3 py-2 bg-brand-600/10 hover:bg-brand-600/20 rounded-lg text-sm text-brand-300 transition-colors"
            >
              <Download size={14} />
              <span className="truncate">{file.fileName}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}