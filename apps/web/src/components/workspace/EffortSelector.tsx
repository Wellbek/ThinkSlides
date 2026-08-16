"use client";

/**
 * Simple / Deep Thinking toggle for the composer.
 *
 * Deep Thinking is only selectable when the organisation has a working
 * deep-search backend. Without one the control is disabled and explains why,
 * with a shortcut into Settings - rather than letting the user pick a mode
 * that would silently be downgraded on the server.
 */

import { useEffect } from "react";
import { Sparkles, Telescope } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { UNCONFIGURED_STATUS, useSettingsStore } from "@/stores/settings-store";
import type { EffortMode } from "@thinkslides/schemas";

export function EffortSelector({ size = "sm" }: { size?: "sm" | "md" }) {
  const mode = useChatStore((s) => s.mode);
  const setMode = useChatStore((s) => s.setMode);
  const setSettingsOpen = useChatStore((s) => s.setSettingsOpen);
  const stored = useSettingsStore((s) => s.stored);
  const refreshStatus = useSettingsStore((s) => s.refreshStatus);

  // The composer is often the first thing rendered, so it fetches the
  // readiness summary itself rather than depending on Settings being opened.
  useEffect(() => {
    if (!stored) refreshStatus();
  }, [stored, refreshStatus]);

  const status = stored?.status ?? UNCONFIGURED_STATUS;
  const deepAvailable = status.deepThinkingAvailable;

  // Never leave the UI showing a mode the server would refuse to honour.
  useEffect(() => {
    if (mode === "deep" && stored && !deepAvailable) setMode("simple");
  }, [mode, stored, deepAvailable, setMode]);

  const options: { id: EffortMode; label: string; icon: React.ReactNode; title: string }[] = [
    {
      id: "simple",
      label: "Simple",
      icon: <Sparkles size={12} />,
      title: "Answer from the configured chat model alone. Fastest.",
    },
    {
      id: "deep",
      label: "Deep Thinking",
      icon: <Telescope size={12} />,
      title: deepAvailable
        ? "Research the topic on the web first and cite the sources used."
        : status.deepThinkingUnavailableReason ??
          "Configure a deep-search backend in Settings to enable Deep Thinking.",
    },
  ];

  return (
    <div className={`ws-effort ${size === "md" ? "md" : ""}`} role="radiogroup" aria-label="Effort level">
      {options.map((opt) => {
        const disabled = opt.id === "deep" && !deepAvailable;
        return (
          <button
            key={opt.id}
            type="button"
            role="radio"
            aria-checked={mode === opt.id}
            disabled={disabled}
            title={opt.title}
            className={`ws-effort-opt ${mode === opt.id ? "active" : ""}`}
            onClick={() => setMode(opt.id)}
          >
            {opt.icon}
            {opt.label}
          </button>
        );
      })}
      {/* Only when Deep Thinking specifically is missing. A workspace with no
          model at all already carries the top-bar alert, and a second "set up"
          link beside it is the same instruction twice. */}
      {status.chatConfigured && !deepAvailable && (
        <button
          type="button"
          className="ws-effort-setup"
          onClick={() => setSettingsOpen(true)}
          title={status.deepThinkingUnavailableReason ?? undefined}
        >
          Set up
        </button>
      )}
    </div>
  );
}
