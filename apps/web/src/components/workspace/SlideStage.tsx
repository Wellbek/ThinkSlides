"use client";

import { useMemo, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import {
  MousePointer2, Highlighter, MessageSquareDot, Pencil,
  ChevronLeft, ChevronRight, ZoomIn, ZoomOut, Send, PanelLeftClose,
} from "lucide-react";
import type { SlideFinalizedEvent } from "@thinkslides/schemas";

interface SlideItem { key: string; slideIndex: number; previewUrl?: string; title: string }

export function SlideStage({ onCollapse }: { collapsed: boolean; onCollapse: () => void }) {
  const activeMessages = useChatStore((s) => s.activeMessages);
  const slides = useMemo(() => collectSlides(activeMessages), [activeMessages]);
  const [current, setCurrent] = useState(0);
  const [tool, setTool] = useState<string>("select");
  const [zoom, setZoom] = useState(75);

  const idx = Math.min(current, Math.max(0, slides.length - 1));
  const slide = slides[idx];
  const total = slides.length;

  return (
    <>
      <div className="ws-preview-toolbar">
        <div className="ws-tool-group">
          <button className={`ws-tool ${tool === "select" ? "active" : ""}`} onClick={() => setTool("select")} title="Select"><MousePointer2 size={15} /></button>
          <button className={`ws-tool ${tool === "mark" ? "active" : ""}`} onClick={() => setTool("mark")} title="Highlight / mark region"><Highlighter size={15} /></button>
          <button className={`ws-tool ${tool === "pin" ? "active" : ""}`} onClick={() => setTool("pin")} title="Comment pin"><MessageSquareDot size={15} /></button>
          <button className={`ws-tool ${tool === "draw" ? "active" : ""}`} onClick={() => setTool("draw")} title="Draw"><Pencil size={15} /></button>
        </div>
        <div className="ws-tool-sep" />
        <div className="ws-tool-group">
          <button className="ws-tool" disabled={idx === 0} onClick={() => setCurrent(idx - 1)} title="Previous"><ChevronLeft size={15} /></button>
          <span className="ws-pill">{total ? `Slide ${idx + 1} / ${total}` : "No slides"}</span>
          <button className="ws-tool" disabled={idx >= total - 1} onClick={() => setCurrent(idx + 1)} title="Next"><ChevronRight size={15} /></button>
        </div>
        <div className="ws-tool-sep" />
        <div className="ws-tool-group">
          <button className="ws-tool" onClick={() => setZoom((z) => Math.max(40, z - 10))} title="Zoom out"><ZoomOut size={15} /></button>
          <span className="ws-pill" style={{ minWidth: 46, textAlign: "center" }}>{zoom}%</span>
          <button className="ws-tool" onClick={() => setZoom((z) => Math.min(150, z + 10))} title="Zoom in"><ZoomIn size={15} /></button>
        </div>
        <button className="ws-send-edit"><Send size={13} /> Send mark to chat</button>
        <button className="ws-collapse" onClick={onCollapse} title="Hide preview"><PanelLeftClose size={15} /></button>
      </div>

      <div className="ws-stage">
        {slide ? (
          <div className="ws-slide-wrap" style={{ width: `min(100%, ${960 * zoom / 75}px)` }}>
            {slide.previewUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img className="ws-slide-img" src={slide.previewUrl} alt={slide.title} />
            ) : (
              <SlidePlaceholder title={slide.title} />
            )}
            {tool === "mark" && (
              <div className="ws-anno" title="Marked for edit">
                <div className="ws-anno-pin">1</div>
              </div>
            )}
          </div>
        ) : (
          <div className="ws-restore" style={{ width: "100%" }}>
            <div className="r-icon"><MousePointer2 size={26} /></div>
            <div className="r-title">No slide preview yet</div>
            <div className="r-sub">Slides appear here as the agent finalizes them.</div>
          </div>
        )}
      </div>

      <div className="ws-filmstrip">
        {total === 0 && <div className="ws-thumb-empty">No slides</div>}
        {slides.map((s, i) => (
          <div
            key={s.key}
            className={`ws-thumb ${i === idx ? "cur" : ""}`}
            onClick={() => setCurrent(i)}
            title={s.title}
          >
            {s.previewUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={s.previewUrl} alt={s.title} />
            ) : (
              <div className="ws-thumb-empty">{i + 1}</div>
            )}
            <span className="tl">{i + 1}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function SlidePlaceholder({ title }: { title: string }) {
  return (
    <div className="ws-slide">
      <div className="ws-slide-brand-bar" />
      <div className="ws-slide-body">
        <div className="ws-slide-kicker">Generated slide</div>
        <h1 className="ws-slide-h1">{title}</h1>
        <div className="ws-slide-grid">
          <div className="ws-slide-left">
            <div className="ws-big-stat">Preview</div>
            <div className="ws-stat-cap">A rendered preview image will replace this placeholder once the renderer publishes one for this slide.</div>
          </div>
          <div className="ws-slide-left">
            <svg viewBox="0 0 200 120" preserveAspectRatio="none" style={{ width: "100%", height: "62%" }}>
              <g fill="#4F46E5">
                <rect x="8" y="80" width="26" height="40" rx="2" />
                <rect x="44" y="66" width="26" height="54" rx="2" />
                <rect x="80" y="50" width="26" height="70" rx="2" />
                <rect x="116" y="34" width="26" height="86" rx="2" opacity=".55" />
              </g>
              <line x1="4" y1="120" x2="196" y2="120" stroke="#ddd" strokeWidth="1" />
            </svg>
          </div>
        </div>
      </div>
    </div>
  );
}

function collectSlides(messages: any[]): SlideItem[] {
  const seen = new Set<number>();
  const rows: SlideItem[] = [];
  const push = (ev: SlideFinalizedEvent) => {
    if (seen.has(ev.slideIndex)) return;
    seen.add(ev.slideIndex);
    const data = ev.slideData as any;
    rows.push({
      key: `${ev.slideIndex}-${ev.timestamp}`,
      slideIndex: ev.slideIndex,
      previewUrl: ev.previewUrl,
      title: data?.title || data?.layout || `Slide ${ev.slideIndex + 1}`,
    });
  };
  for (const m of messages) {
    for (const ev of m.agentEventStream || []) {
      if (ev.type === "slide_finalized") push(ev);
    }
  }
  return rows.sort((a, b) => a.slideIndex - b.slideIndex);
}
