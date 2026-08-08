"use client";

/**
 * Concise / Detailed / Comprehensive dropdown for the composer.
 *
 * Controls how much content depth generated slides aim for - separate from
 * EffortSelector's Simple/Deep Thinking, which only controls whether the
 * agent researches facts on the web. Defaults to "detailed" on the store,
 * which already raises the baseline over the old de-facto concise output
 * even for a user who never touches this control.
 *
 * A dropdown rather than a 3-way button group (like EffortSelector) because
 * the composer toolbar only has so much width - a second button group here
 * wrapped the row onto two lines.
 */

import { useEffect, useRef, useState } from "react";
import { AlignLeft, Check, ChevronDown, List, Rows3 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import type { DetailLevel } from "@thinkslides/schemas";

const OPTIONS: { id: DetailLevel; label: string; icon: React.ReactNode; description: string }[] = [
  {
    id: "concise",
    label: "Concise",
    icon: <List size={12} />,
    description: "A few short bullets per slide - fastest to skim.",
  },
  {
    id: "detailed",
    label: "Detailed",
    icon: <Rows3 size={12} />,
    description: "Fuller bullets with supporting detail on each slide.",
  },
  {
    id: "comprehensive",
    label: "Comprehensive",
    icon: <AlignLeft size={12} />,
    description: "Maximum depth - sub-bullets, richer notes, more elaboration.",
  },
];

export function DetailLevelSelector({ size = "sm" }: { size?: "sm" | "md" }) {
  const detailLevel = useChatStore((s) => s.detailLevel);
  const setDetailLevel = useChatStore((s) => s.setDetailLevel);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const active = OPTIONS.find((o) => o.id === detailLevel) ?? OPTIONS[1];

  return (
    <div className={`ws-dd ${size === "md" ? "md" : ""}`} ref={rootRef}>
      <button
        type="button"
        className={`ws-dd-trigger ${open ? "open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Presentation detail level"
        onClick={() => setOpen((o) => !o)}
      >
        {active.icon}
        {active.label}
        <ChevronDown size={12} className={`ws-dd-chev ${open ? "open" : ""}`} />
      </button>
      {open && (
        <div className="ws-dd-menu" role="listbox" aria-label="Presentation detail level">
          {OPTIONS.map((opt) => (
            <button
              key={opt.id}
              type="button"
              role="option"
              aria-selected={detailLevel === opt.id}
              className={`ws-dd-item ${detailLevel === opt.id ? "active" : ""}`}
              onClick={() => {
                setDetailLevel(opt.id);
                setOpen(false);
              }}
            >
              <span className="ws-dd-item-icon">{opt.icon}</span>
              <span className="ws-dd-item-body">
                <span className="ws-dd-item-label">{opt.label}</span>
                <span className="ws-dd-item-desc">{opt.description}</span>
              </span>
              {detailLevel === opt.id && <Check size={13} className="ws-dd-item-check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
