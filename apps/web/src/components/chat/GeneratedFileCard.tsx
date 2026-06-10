"use client";

import { motion } from "framer-motion";
import {
  Download,
  Presentation,
  CheckCircle2,
  SlidersHorizontal,
} from "lucide-react";
import type { GeneratedFile } from "@/stores/chat-store";
import type { SlideFinalizedEvent } from "@thinkslides/schemas";

interface GeneratedFileCardProps {
  file: GeneratedFile;
  slideCount: number;
  slideThumbnails: { slideIndex: number; layoutName: string }[];
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function GeneratedFileCard({
  file,
  slideCount,
  slideThumbnails,
}: GeneratedFileCardProps) {
  const fileName = file.filename || "presentation.pptx";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 400, damping: 28 }}
      className="w-full max-w-md rounded-xl border border-gray-700/50 bg-gradient-to-br from-gray-900/60 to-gray-800/40 backdrop-blur-md overflow-hidden shadow-lg shadow-black/20"
    >
      {/* Header: file icon + meta */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-3">
        <div className="shrink-0 w-10 h-10 rounded-lg bg-orange-500/15 border border-orange-500/30 flex items-center justify-center">
          <Presentation size={20} className="text-orange-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-100 truncate leading-snug">
            {fileName}
          </p>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[11px] text-gray-500">PowerPoint</span>
            {slideCount > 0 && (
              <>
                <span className="text-gray-700">·</span>
                <span className="text-[11px] text-gray-500">
                  {slideCount} {slideCount === 1 ? "slide" : "slides"}
                </span>
              </>
            )}
            {"file_size" in file && file.file_size && (
              <>
                <span className="text-gray-700">·</span>
                <span className="text-[11px] text-gray-500">
                  {formatFileSize(file.file_size as number)}
                </span>
              </>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-2 py-0.5">
          <CheckCircle2 size={10} className="text-emerald-400" />
          <span className="text-[10px] font-medium text-emerald-400">
            Ready
          </span>
        </div>
      </div>

      {/* Slide thumbnail grid */}
      {slideThumbnails.length > 0 && (
        <div className="px-4 pb-3">
          <div className="grid grid-cols-3 gap-1.5">
            {slideThumbnails.slice(0, 6).map((slide) => (
              <div
                key={slide.slideIndex}
                className="bg-white/[0.04] border border-gray-700/30 rounded-md aspect-video flex items-center justify-center relative overflow-hidden"
              >
                <div className="text-center">
                  <p className="text-[10px] font-semibold text-gray-300">
                    {slide.slideIndex + 1}
                  </p>
                </div>
                {/* Mini slide mockup lines */}
                <div className="absolute inset-x-2 top-2 space-y-0.5">
                  <div className="h-[2px] bg-gray-600/40 rounded-full w-3/4" />
                  <div className="h-[1px] bg-gray-700/30 rounded-full w-1/2" />
                </div>
                <div className="absolute inset-x-2 bottom-1.5 space-y-0.5">
                  <div className="h-[1px] bg-gray-700/30 rounded-full w-full" />
                  <div className="h-[1px] bg-gray-700/20 rounded-full w-5/6" />
                  <div className="h-[1px] bg-gray-700/20 rounded-full w-2/3" />
                </div>
              </div>
            ))}
          </div>
          {slideThumbnails.length > 6 && (
            <p className="text-[10px] text-gray-500 mt-1.5 text-center">
              +{slideThumbnails.length - 6} more slides
            </p>
          )}
        </div>
      )}

      {/* Download bar */}
      <div className="border-t border-gray-700/40 bg-gray-900/30 px-4 py-2.5 flex items-center justify-between">
        <button
          onClick={() => {
            const token =
              typeof window !== "undefined"
                ? localStorage.getItem("auth_token")
                : null;
            const url = file.file_url.includes("?")
              ? `${file.file_url}${token ? `&token=${encodeURIComponent(token)}` : ""}`
              : `${file.file_url}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
            window.open(url, "_blank");
          }}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium rounded-lg transition-colors shadow-sm shadow-brand-900/30"
        >
          <Download size={14} />
          Download
        </button>
        <button
          onClick={() => {
            const token =
              typeof window !== "undefined"
                ? localStorage.getItem("auth_token")
                : null;
            const url = file.file_url.includes("?")
              ? `${file.file_url}${token ? `&token=${encodeURIComponent(token)}` : ""}`
              : `${file.file_url}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
            window.open(url, "_blank");
          }}
          className="flex items-center gap-1.5 px-3 py-2 text-gray-400 hover:text-gray-200 text-sm transition-colors rounded-lg hover:bg-white/[0.04]"
        >
          <SlidersHorizontal size={14} />
          <span className="text-xs">Open</span>
        </button>
      </div>
    </motion.div>
  );
}

