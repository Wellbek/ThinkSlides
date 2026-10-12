"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Share2 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { buildDownloadUrl } from "@/lib/download";

/**
 * Early version of sharing. Shared links need per-session URLs and access
 * control, which do not exist yet, so for now this hands over the file.
 * The download URL carries the user's own token and must never be offered
 * as something to copy and send.
 */
export function ShareButton() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const activeFiles = useChatStore((s) => s.activeFiles);
  const decks = activeFiles.filter((f) => !f.file_type || f.file_type === "pptx");
  const deck = decks[decks.length - 1];

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="ws-share" ref={ref}>
      <button className="ws-tb-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Share2 size={13} /> Share
      </button>
      {open && (
        <div className="ws-share-pop" role="dialog" aria-label="Share">
          <div className="ws-share-head">
            Share <span className="ws-dev-tag">In development</span>
          </div>
          <p className="ws-share-text">
            Shared links and collaborators are not available yet. For now, download the deck and send the file.
          </p>
          {deck ? (
            <>
              <span className="ws-share-file" title={deck.filename}>{deck.filename}</span>
              <a className="ws-tb-btn primary" href={buildDownloadUrl(deck.file_url)} download onClick={() => setOpen(false)}>
                <Download size={13} /> Download deck
              </a>
            </>
          ) : (
            <p className="ws-share-text muted">The deck can be downloaded once it has been generated.</p>
          )}
        </div>
      )}
    </div>
  );
}
