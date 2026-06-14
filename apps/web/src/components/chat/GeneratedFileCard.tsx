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
      className="w-full max-w-md rounded-lg border border-iron bg-charcoal shadow-subtle overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-start gap-3.5 px-5 pt-5 pb-3">
        <div className="shrink-0 w-11 h-11 rounded-lg bg-ice/5 border border-ice/20 flex items-center justify-center">
          <Presentation size={22} className="text-ice" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-body font-semibold text-paper truncate leading-snug">
            {fileName}
          </p>
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            <span className="inline-flex items-center gap-1 text-caption text-fog">
              <FileDown size={10} className="text-fog" />
              PPTX
            </span>
            {slideCount > 0 && (
              <>
                <span className="text-fog">·</span>
                <span className="text-caption text-fog">
                  {slideCount} {slideCount === 1 ? "slide" : "slides"}
                </span>
              </>
            )}
            {fileSizeStr && (
              <>
                <span className="text-fog">·</span>
                <span className="text-caption text-fog">{fileSizeStr}</span>
              </>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1 bg-success/10 border border-success/20 rounded-full px-2.5 py-1">
          <CheckCircle2 size={11} className="text-success" />
          <span className="text-caption font-semibold text-success tracking-wide">
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
                className="group relative bg-graphite border border-iron rounded-md aspect-video flex flex-col items-center justify-center overflow-hidden hover:border-ice/30 hover:bg-slate-dark transition-all duration-200"
              >
                {/* Slide number badge */}
                <span className="absolute top-1 left-1 bg-obsidian text-paper rounded-md w-4 h-4 flex items-center justify-center text-caption font-bold">
                  {slide.slideIndex + 1}
                </span>

                {/* Decorative content mockup */}
                <div className="absolute inset-x-2.5 top-2.5 space-y-[3px]">
                  <div className="h-[3px] bg-gradient-to-r from-ice/20 to-ice/10 rounded-full w-4/5" />
                  <div className="h-[2px] bg-iron rounded-full w-3/5" />
                </div>
                <div className="absolute inset-x-2.5 bottom-2 space-y-[2px]">
                  <div className="h-[2px] bg-iron/80 rounded-full w-full" />
                  <div className="h-[2px] bg-iron/60 rounded-full w-5/6" />
                  <div className="h-[2px] bg-iron/40 rounded-full w-2/3" />
                </div>

                {/* Hover overlay */}
                <div className="absolute inset-0 bg-ice/5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" />
              </motion.div>
            ))}
          </div>
          {slideThumbnails.length > 6 && (
            <p className="text-caption text-fog mt-2 text-center font-medium">
              +{slideThumbnails.length - 6} more slides
            </p>
          )}
        </div>
      )}

      {/* Empty state when no slides */}
      {slideThumbnails.length === 0 && (
        <div className="px-5 pb-4">
          <div className="rounded-lg border border-dashed border-iron py-4 flex flex-col items-center gap-1.5 bg-charcoal">
            <Presentation size={18} className="text-fog" />
            <p className="text-caption text-fog">
              {slideCount > 0
                ? `${slideCount} slides generated`
                : "Presentation ready"}
            </p>
          </div>
        </div>
      )}

      {/* Action bar */}
      <div className="border-t border-iron bg-obsidian px-5 py-3 flex items-center gap-2">
        <a
          href={downloadUrl}
          download
          className="btn-outlined"
        >
          <Download size={14} />
          Download
        </a>
        <button
          onClick={() => window.open(downloadUrl, "_blank")}
          className="btn-ghost"
          title="Open in new tab"
        >
          <ExternalLink size={14} />
          <span className="text-xs font-medium">Open</span>
        </button>
      </div>
    </motion.div>
  );
}

