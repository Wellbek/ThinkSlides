"use client";

import { motion } from "framer-motion";
import {
  Download,
  FileDown,
  Presentation,
  CheckCircle2,
  ExternalLink,
} from "lucide-react";
import type { GeneratedFile } from "@/stores/chat-store";

interface GeneratedFileCardProps {
  file: GeneratedFile;
  slideCount: number;
  slideThumbnails: { slideIndex: number; layoutName: string }[];
}

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Build an authenticated download URL by appending the JWT as a query param. */
function buildDownloadUrl(fileUrl: string): string {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
  if (!token) return fileUrl;
  const sep = fileUrl.includes("?") ? "&" : "?";
  return `${fileUrl}${sep}token=${encodeURIComponent(token)}`;
}

export function GeneratedFileCard({
  file,
  slideCount,
  slideThumbnails,
}: GeneratedFileCardProps) {
  const fileName = file.filename || "presentation.pptx";
  const fileSizeStr = formatFileSize(file.file_size ?? 0);
  const downloadUrl = buildDownloadUrl(file.file_url);

  return (
    <motion.div
      initial={{ opacity: 0, y: 14, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 420, damping: 26 }}
      className="w-full max-w-md rounded-2xl border border-gray-700/40 bg-gradient-to-br from-gray-900/70 via-gray-800/50 to-gray-900/60 backdrop-blur-xl overflow-hidden shadow-2xl shadow-black/30"
    >
      {/* Header */}
      <div className="flex items-start gap-3.5 px-5 pt-5 pb-3">
        <div className="shrink-0 w-11 h-11 rounded-xl bg-gradient-to-br from-orange-500/20 to-amber-500/10 border border-orange-500/25 flex items-center justify-center shadow-inner shadow-orange-500/5">
          <Presentation size={22} className="text-orange-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-100 truncate leading-snug">
            {fileName}
          </p>
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            <span className="inline-flex items-center gap-1 text-[11px] text-gray-500">
              <FileDown size={10} className="text-gray-500" />
              PPTX
            </span>
            {slideCount > 0 && (
              <>
                <span className="text-gray-700">·</span>
                <span className="text-[11px] text-gray-500">
                  {slideCount} {slideCount === 1 ? "slide" : "slides"}
                </span>
              </>
            )}
            {fileSizeStr && (
              <>
                <span className="text-gray-700">·</span>
                <span className="text-[11px] text-gray-500">{fileSizeStr}</span>
              </>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-2.5 py-1 shadow-sm shadow-emerald-500/5">
          <CheckCircle2 size={11} className="text-emerald-400" />
          <span className="text-[10px] font-semibold text-emerald-400 tracking-wide">
            Ready
          </span>
        </div>
      </div>

      {/* Slide thumbnail grid */}
      {slideThumbnails.length > 0 && (
        <div className="px-5 pb-4">
          <div className="grid grid-cols-3 gap-2">
            {slideThumbnails.slice(0, 6).map((slide, idx) => (
              <motion.div
                key={slide.slideIndex}
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: idx * 0.06, type: "spring", stiffness: 500, damping: 30 }}
                className="group relative bg-white/[0.03] border border-gray-700/25 rounded-lg aspect-video flex flex-col items-center justify-center overflow-hidden hover:border-orange-500/30 hover:bg-white/[0.05] transition-all duration-200"
              >
                {/* Slide number badge */}
                <span className="absolute top-1 left-1 bg-gray-800/80 text-gray-300 rounded-md w-4 h-4 flex items-center justify-center text-[8px] font-bold shadow-sm backdrop-blur-sm">
                  {slide.slideIndex + 1}
                </span>

                {/* Decorative content mockup */}
                <div className="absolute inset-x-2.5 top-2.5 space-y-[3px]">
                  <div className="h-[3px] bg-gradient-to-r from-orange-500/20 to-orange-500/10 rounded-full w-4/5" />
                  <div className="h-[2px] bg-gray-600/25 rounded-full w-3/5" />
                </div>
                <div className="absolute inset-x-2.5 bottom-2 space-y-[2px]">
                  <div className="h-[2px] bg-gray-600/20 rounded-full w-full" />
                  <div className="h-[2px] bg-gray-700/15 rounded-full w-5/6" />
                  <div className="h-[2px] bg-gray-700/10 rounded-full w-2/3" />
                </div>

                {/* Hover overlay */}
                <div className="absolute inset-0 bg-orange-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" />
              </motion.div>
            ))}
          </div>
          {slideThumbnails.length > 6 && (
            <p className="text-[10px] text-gray-500 mt-2 text-center font-medium">
              +{slideThumbnails.length - 6} more slides
            </p>
          )}
        </div>
      )}

      {/* Empty state when no slides */}
      {slideThumbnails.length === 0 && (
        <div className="px-5 pb-4">
          <div className="rounded-lg border border-dashed border-gray-700/40 py-4 flex flex-col items-center gap-1.5 bg-white/[0.01]">
            <Presentation size={18} className="text-gray-600" />
            <p className="text-[11px] text-gray-500">
              {slideCount > 0
                ? `${slideCount} slides generated`
                : "Presentation ready"}
            </p>
          </div>
        </div>
      )}

      {/* Action bar */}
      <div className="border-t border-gray-700/30 bg-gray-900/40 px-5 py-3 flex items-center gap-2">
        <a
          href={downloadUrl}
          download
          className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-brand-600 to-brand-500 hover:from-brand-500 hover:to-brand-400 text-white text-sm font-semibold rounded-lg transition-all shadow-md shadow-brand-900/30 hover:shadow-lg hover:shadow-brand-800/30 active:scale-[0.97]"
        >
          <Download size={14} />
          Download
        </a>
        <button
          onClick={() => window.open(downloadUrl, "_blank")}
          className="flex items-center gap-1.5 px-3 py-2 text-gray-400 hover:text-gray-200 text-sm transition-colors rounded-lg hover:bg-white/[0.06] border border-transparent hover:border-gray-700/40"
          title="Open in new tab"
        >
          <ExternalLink size={14} />
          <span className="text-xs font-medium">Open</span>
        </button>
      </div>
    </motion.div>
  );
}

