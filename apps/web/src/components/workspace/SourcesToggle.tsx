"use client";

/**
 * "Cite sources" toggle for the composer.
 *
 * The citation trail already existed in the chat - web_search results are
 * tracked and shown there - but it stopped at the chat. A bullet saying "15%
 * increase in revenue" gave someone reading the .pptx later no way back to
 * where it came from.
 *
 * A toggle rather than a dropdown: unlike effort level and detail level this
 * is genuinely binary. It is off by default, because a deck that cites its
 * sources is a deliberate choice and most decks have no research behind them
 * to cite. When Deep Thinking has not run, it is a no-op by construction -
 * there is nothing to reference - which is why it stays enabled rather than
 * being disabled on simple mode: the setting outlives the current effort
 * choice, and greying it out would suggest it had been turned off.
 */

import { Quote } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";

export function SourcesToggle({ size = "sm" }: { size?: "sm" | "md" }) {
  const includeSources = useChatStore((s) => s.includeSources);
  const setIncludeSources = useChatStore((s) => s.setIncludeSources);
  const mode = useChatStore((s) => s.mode);

  const title = includeSources
    ? mode === "deep"
      ? "Sources will be referenced in the deck"
      : "Sources will be referenced when Deep Thinking finds any"
    : "Add numbered references and a Sources slide to the deck";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={includeSources}
      aria-label="Cite sources in the document"
      className={`ws-src-toggle ${size === "md" ? "md" : ""} ${includeSources ? "on" : ""}`}
      onClick={() => setIncludeSources(!includeSources)}
      title={title}
    >
      <Quote size={size === "md" ? 13 : 12} />
      <span>Cite sources</span>
    </button>
  );
}
