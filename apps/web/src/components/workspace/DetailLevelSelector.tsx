"use client";

/**
 * Concise / Detailed / Comprehensive toggle for the composer.
 *
 * Controls how much content depth generated slides aim for - separate from
 * EffortSelector's Simple/Deep Thinking, which only controls whether the
 * agent researches facts on the web. Defaults to "detailed" on the store,
 * which already raises the baseline over the old de-facto concise output
 * even for a user who never touches this control.
 */

import { AlignLeft, List, Rows3 } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import type { DetailLevel } from "@thinkslides/schemas";

export function DetailLevelSelector({ size = "sm" }: { size?: "sm" | "md" }) {
  const detailLevel = useChatStore((s) => s.detailLevel);
  const setDetailLevel = useChatStore((s) => s.setDetailLevel);

  const options: { id: DetailLevel; label: string; icon: React.ReactNode; title: string }[] = [
    {
      id: "concise",
      label: "Concise",
      icon: <List size={12} />,
      title: "A few short bullets per slide - fastest to skim.",
    },
    {
      id: "detailed",
      label: "Detailed",
      icon: <Rows3 size={12} />,
      title: "Fuller bullets with supporting detail on each slide.",
    },
    {
      id: "comprehensive",
      label: "Comprehensive",
      icon: <AlignLeft size={12} />,
      title: "Maximum depth - sub-bullets, richer notes, more elaboration.",
    },
  ];

  return (
    <div className={`ws-effort ${size === "md" ? "md" : ""}`} role="radiogroup" aria-label="Presentation detail level">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          role="radio"
          aria-checked={detailLevel === opt.id}
          title={opt.title}
          className={`ws-effort-opt ${detailLevel === opt.id ? "active" : ""}`}
          onClick={() => setDetailLevel(opt.id)}
        >
          {opt.icon}
          {opt.label}
        </button>
      ))}
    </div>
  );
}
