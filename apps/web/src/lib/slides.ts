/**
 * Collect structured slide data from the agent event stream.
 *
 * The backend renders a real per-slide preview image once the deck is
 * finalized: `finalize_and_render_pptx` converts the rendered .pptx to PDF
 * via LibreOffice and rasterizes each page to a PNG, and `slide_finalized`
 * events carry that image's URL as `previewUrl`. Until that arrives, the
 * `design_slide_content` tool results (full Slide JSON - layoutName +
 * contents with text runs / images / tables / charts) are used to render an
 * approximate live preview.
 *
 * This module reconstructs an ordered, deduplicated slide list from those
 * tool results, merging in `previewUrl` once it becomes available so the
 * approximate live view is replaced by the true rendered output.
 *
 * A `slide_finalized` event can also *create* an entry rather than only
 * decorating one. Not every page of the rendered file was designed by the
 * agent: `finalize_and_render_pptx` appends a Sources appendix after the
 * model is finished, and previews are rasterised from every page of the
 * result. Those pages have no `design_slide_content` behind them, so keying
 * the list purely on design results dropped them - the preview reported fewer
 * pages than the file the user downloaded.
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
  /** Added by the renderer, not designed by the agent. Such a slide has no
   *  `contents`, so it can only ever be shown as its rendered image. */
  injected?: boolean;
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
  // Track the latest previewUrl per slideIndex from slide_finalized events,
  // emitted once finalize_and_render_pptx has produced the real rendered
  // output.
  const previewUrls = new Map<number, string>();
  // The rendered pages themselves, so one with no design result behind it can
  // still be shown. Keyed the same way, and the last render wins.
  const rendered = new Map<number, { title: string; injected: boolean }>();

  for (const m of messages) {
    for (const ev of m.agentEventStream || []) {
      if (ev.type === "slide_finalized") {
        const sf = ev as SlideFinalizedEvent;
        const url = sf.previewUrl;
        if (url) previewUrls.set(sf.slideIndex, url);
        rendered.set(sf.slideIndex, {
          title: (sf.slideTitle || "").trim(),
          injected: !!sf.injected,
        });
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
      });
    }
  }

  // Merge preview URLs in a separate pass, after the whole stream has been
  // walked. `slide_finalized` always arrives *after* the `design_slide_content`
  // result for the same slide, so reading `previewUrls` inline above would
  // always observe an empty map and leave `previewUrl` permanently undefined -
  // which silently pinned the UI to the approximate CSS mock and hid the real
  // LibreOffice render even once it was available.
  for (const [idx, url] of previewUrls) {
    const slide = byIndex.get(idx);
    if (slide) {
      slide.previewUrl = url;
      continue;
    }
    // A rendered page the agent never designed. The image is the whole of it,
    // so there is nothing to reconstruct - but it is genuinely in the file the
    // user downloads, and omitting it made the preview disagree with the deck.
    const page = rendered.get(idx);
    byIndex.set(idx, {
      key: `rendered-${idx}`,
      slideIndex: idx,
      layoutName: "",
      title: page?.title || `Slide ${idx + 1}`,
      contents: [],
      previewUrl: url,
      injected: page?.injected ?? true,
    });
  }

  return Array.from(byIndex.values()).sort(
    (a, b) => a.slideIndex - b.slideIndex
  );
}
