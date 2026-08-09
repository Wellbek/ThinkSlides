"use client";

import { useMemo, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { collectPreviewSlides, type PreviewSlide } from "@/lib/slides";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import {
  MousePointer2, Highlighter, MessageSquareDot, Pencil,
  ChevronLeft, ChevronRight, ZoomIn, ZoomOut, Send, PanelLeftClose,
  Download,
} from "lucide-react";
import type { TextRun, SlideContentItem } from "@thinkslides/schemas";

export function SlideStage({ onCollapse }: { collapsed: boolean; onCollapse: () => void }) {
  const activeMessages = useChatStore((s) => s.activeMessages);
  const activeFiles = useChatStore((s) => s.activeFiles);
  const slides = useMemo(() => collectPreviewSlides(activeMessages), [activeMessages]);
  const [current, setCurrent] = useState(0);
  const [tool, setTool] = useState<string>("select");
  const [zoom, setZoom] = useState(75);

  const idx = Math.min(current, Math.max(0, slides.length - 1));
  const slide = slides[idx];
  const total = slides.length;

  const latestFile = activeFiles[activeFiles.length - 1];
  const downloadUrl = buildDownloadUrl(latestFile?.file_url);

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
        <div className="ws-tool-sep" />
        {latestFile && (
          <a className="ws-tb-btn primary" href={downloadUrl} download title={`Download ${latestFile.filename}`}>
            <Download size={13} /> Download
          </a>
        )}
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
              // Approximate HTML reconstruction of the slide JSON, shown while
              // the deck is still being designed. It is deliberately labelled:
              // it lays content out in normal document flow, so it cannot show
              // the overflow or overlap the real renderer might produce, and
              // users were reading it as the finished result and reporting the
              // rendered .pptx as "not aligned with the preview".
              <>
                <SlideCanvas slide={slide} />
                <div className="ws-draft-badge">
                  Draft layout - the rendered slide appears once the deck is finalized
                </div>
              </>
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
            <div className="r-sub">Slides appear here as the agent designs them.</div>
            {latestFile && (
              <a className="ws-restore-btn" href={downloadUrl} download>
                <Download size={14} /> {latestFile.filename}
                {latestFile.file_size ? ` - ${formatFileSize(latestFile.file_size)}` : ""}
              </a>
            )}
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
              <ThumbMock slide={s} />
            )}
            <span className="tl">{i + 1}</span>
          </div>
        ))}
      </div>
    </>
  );
}

/** Faithful slide preview rendered from structured Slide JSON. */
function SlideCanvas({ slide }: { slide: PreviewSlide }) {
  const textItems = slide.contents.filter(
    (c): c is { type: "text"; placeholderIdx: number; runs: TextRun[] } =>
      c.type === "text"
  );
  // First text block is the title; remaining text blocks form the body.
  const [titleItem, ...bodyText] = textItems;
  const other = slide.contents.filter((c) => c.type !== "text") as Array<
    Exclude<SlideContentItem, { type: "text" }>
  >;

  return (
    <div className="ws-slide">
      <div className="ws-slide-brand-bar" />
      <div className="ws-slide-body">
        <div className="ws-slide-kicker">{slide.layoutName}</div>
        {titleItem && (
          <h1 className="ws-slide-h1">{runsToText(titleItem.runs)}</h1>
        )}
        <div className="ws-slide-content">
          {bodyText.map((item, i) => (
            <BulletBlock key={`t-${i}`} runs={item.runs} />
          ))}
          {other.map((item, i) => (
            <ContentBlock key={`o-${i}`} item={item} />
          ))}
        </div>
      </div>
    </div>
  );
}

function BulletBlock({ runs }: { runs: TextRun[] }) {
  const hasBullets = runs.some((r) => r.bullet);
  if (!hasBullets) {
    return <p className="ws-slide-para">{runs.map((r, i) => <Run key={i} r={r} />)}</p>;
  }
  // Group runs by bullet level into list items.
  const items: { level: number; text: string }[] = [];
  let buf = "";
  let level = 0;
  for (const r of runs) {
    if (r.bullet) {
      if (buf) items.push({ level, text: buf.trim() });
      level = r.level ?? 0;
      buf = r.text;
    } else {
      buf += r.text;
    }
  }
  if (buf.trim()) items.push({ level, text: buf.trim() });
  return (
    <ul className="ws-slide-list">
      {items.map((it, i) => (
        <li key={i} className={`lvl-${Math.min(it.level, 2)}`}>{it.text}</li>
      ))}
    </ul>
  );
}

function Run({ r }: { r: TextRun }) {
  const style: React.CSSProperties = {};
  if (r.bold) style.fontWeight = 700;
  if (r.italic) style.fontStyle = "italic";
  if (r.colorHex) style.color = r.colorHex;
  if (r.fontSizePt) style.fontSize = `${r.fontSizePt * 0.6}px`;
  return <span style={style}>{r.text}</span>;
}

function ContentBlock({ item }: { item: Exclude<SlideContentItem, { type: "text" }> }) {
  if (item.type === "image") {
    const alt = item.image.altText || "Image";
    if (item.image.source === "url" || (item.image.source === "base64" && item.image.data.startsWith("data:"))) {
      const src = item.image.source === "url" ? item.image.data : item.image.data;
      return (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="ws-slide-img-content" src={src} alt={alt} />
      );
    }
    return <div className="ws-slide-media"><span>{alt}</span></div>;
  }
  if (item.type === "table") {
    const rows = item.table.rows;
    return (
      <table className="ws-slide-table">
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className={item.table.headerRow && ri === 0 ? "head" : ""}>
              {row.map((cell, ci) => <td key={ci}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  if (item.type === "chart") {
    return <div className="ws-slide-media chart"><span>{item.chart.chartType} chart</span></div>;
  }
  return null;
}

function ThumbMock({ slide }: { slide: PreviewSlide }) {
  const textItems = slide.contents.filter((c) => c.type === "text") as Array<{ runs: TextRun[] }>;
  const title = textItems[0];
  return (
    <div className="ws-thumb-mock">
      <div className="ws-thumb-bar" />
      {title && <div className="ws-thumb-title">{runsToText(title.runs).slice(0, 24)}</div>}
      <div className="ws-thumb-lines">
        <span style={{ width: "85%" }} />
        <span style={{ width: "65%" }} />
        <span style={{ width: "75%" }} />
      </div>
    </div>
  );
}

function runsToText(runs: TextRun[]): string {
  return runs.map((r) => r.text).join(" ").trim();
}
