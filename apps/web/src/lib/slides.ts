/**
 * Collect structured slide data from the agent event stream.
 *
 * The backend does not render per-slide preview images - the
 * `slide_finalized` event only carries PPTX file metadata, and its
 * `previewUrl` is never populated. The faithful per-slide content lives in
 * the `design_slide_content` tool results, which return the full Slide JSON
 * (layoutName + contents with text runs / images / tables / charts).
 *
 * This module reconstructs an ordered, deduplicated slide list from those
 * tool results so the preview and outline can render real slide content.
 */
import type { ChatMessage } from "@/stores/chat-store";
import type {
  TextRun,
  SlideContentItem,
  SlideFinalizedEvent,
} from "@thinkslides/schemas";

export interface PreviewSlide {
  key: string;
  slideIndex: number;
  layoutName: string;
  title: string;
  contents: SlideContentItem[];
  previewUrl?: string;
}

interface RawSlide {
  slideIndex?: number;
  layoutName?: string;
  layoutId?: string;
  title?: string;
  contents?: SlideContentItem[];
  raw_content?: string;
}

/** Pull a readable title from a slide's content items. */
function deriveTitle(slide: RawSlide): string {
  if (slide.title && typeof slide.title === "string") return slide.title;
  const textItems = (slide.contents || []).filter(
    (c): c is { type: "text"; placeholderIdx: number; runs: TextRun[] } =>
      c.type === "text"
  );
  // The first text content item is conventionally the title placeholder.
  const first = textItems[0];
  if (first) {
    const t = first.runs.map((r) => r.text).join(" ").trim();
    if (t) return t;
  }
  return slide.layoutName || `Slide ${(slide.slideIndex ?? 0) + 1}`;
}

export function collectPreviewSlides(messages: ChatMessage[]): PreviewSlide[] {
  const byIndex = new Map<number, PreviewSlide>();
  // Track the latest previewUrl per slideIndex from slide_finalized events
  // (currently always absent, but wired for the day the renderer publishes
  // per-slide images).
  const previewUrls = new Map<number, string>();

  for (const m of messages) {
    for (const ev of m.agentEventStream || []) {
      if (ev.type === "slide_finalized") {
        const sf = ev as SlideFinalizedEvent;
        const url = sf.previewUrl;
        if (url) previewUrls.set(sf.slideIndex, url);
        continue;
      }
      if (ev.type !== "tool_result") continue;
      const tr = ev as any;
      if (tr.toolName !== "design_slide_content") continue;
      if (tr.success === false) continue;
      const slide = tr.result as RawSlide;
      if (!slide || typeof slide !== "object") continue;
      const idx = typeof slide.slideIndex === "number" ? slide.slideIndex : 0;
      // Skip malformed LLM output that only carries raw_content.
      if (!slide.contents && !slide.title) continue;
      byIndex.set(idx, {
        key: `slide-${idx}-${m.id}`,
        slideIndex: idx,
        layoutName: slide.layoutName || "Default",
        title: deriveTitle(slide),
        contents: Array.isArray(slide.contents) ? slide.contents : [],
        previewUrl: previewUrls.get(idx),
      });
    }
  }

  return Array.from(byIndex.values()).sort(
    (a, b) => a.slideIndex - b.slideIndex
  );
}
