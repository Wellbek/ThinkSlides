// stores/annotation-store.ts
//
// Early version of slide-targeted editing: the preview's mark and pin tools
// drop numbered points on a slide, and "Send mark to chat" / "Mention slide"
// turn them into plain text in the composer. The agent reads that text like
// any other message - nothing on the server knows about marks yet.
import { create } from "zustand";

export type MarkKind = "mark" | "pin";

export interface SlideMark {
  id: number;
  sessionId: string;
  slide: number; // 0-based index into the preview's slide list
  kind: MarkKind;
  x: number; // percent of slide width
  y: number; // percent of slide height
}

interface AnnotationState {
  previewSlide: number;
  slideCount: number;
  marks: SlideMark[];
  // Text waiting for the composer to append. Taken, not read, so a composer
  // that remounts does not append it a second time.
  composerInsert: { text: string; nonce: number } | null;
  setPreviewSlide: (index: number, count: number) => void;
  addMark: (sessionId: string, slide: number, kind: MarkKind, x: number, y: number) => void;
  clearMarks: (sessionId: string, slide: number) => void;
  insertIntoComposer: (text: string) => void;
  takeComposerInsert: () => string | null;
}

let nextId = 1;

export const useAnnotationStore = create<AnnotationState>()((set, get) => ({
  previewSlide: 0,
  slideCount: 0,
  marks: [],
  composerInsert: null,
  setPreviewSlide: (index, count) => set({ previewSlide: index, slideCount: count }),
  addMark: (sessionId, slide, kind, x, y) =>
    set((s) => ({ marks: [...s.marks, { id: nextId++, sessionId, slide, kind, x, y }] })),
  clearMarks: (sessionId, slide) =>
    set((s) => ({ marks: s.marks.filter((m) => m.sessionId !== sessionId || m.slide !== slide) })),
  insertIntoComposer: (text) => set({ composerInsert: { text, nonce: Date.now() } }),
  takeComposerInsert: () => {
    const pending = get().composerInsert;
    if (pending) set({ composerInsert: null });
    return pending?.text ?? null;
  },
}));

/** Marks on one slide as a sentence the agent can act on. */
export function describeMarks(marks: SlideMark[]): string {
  const where = marks
    .map((m, i) => `${m.kind === "pin" ? "comment" : "mark"} ${i + 1} at ${Math.round(m.x)}% across, ${Math.round(m.y)}% down`)
    .join("; ");
  return `On slide ${marks[0].slide + 1} (${where}): `;
}
