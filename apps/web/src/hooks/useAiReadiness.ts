"use client";

/**
 * Whether this workspace can generate anything yet.
 *
 * A model comes from the workspace's saved settings and from nowhere else, so
 * "nothing configured" is a real state a user can be in - on a fresh install,
 * it is the state everyone starts in. Sending a prompt in it produces a run
 * that fails at the first LLM call, which is a bad way to learn that setup was
 * required. Both places that start a generation ask this first, and they get
 * the same answer from the same fetch.
 */

import { useEffect } from "react";
import { useSettingsStore } from "@/stores/settings-store";

export interface AiReadiness {
  /** False only once the summary has arrived and says nothing is usable. */
  ready: boolean;
  /** What to tell the user when they are blocked; empty when they are not. */
  reason: string;
}

export function useAiReadiness(): AiReadiness {
  const stored = useSettingsStore((s) => s.stored);
  const refreshStatus = useSettingsStore((s) => s.refreshStatus);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // Until the summary arrives, assume things are fine. Blocking the composer
  // on every page load for as long as it takes to find out would be worse for
  // the configured majority than letting an unconfigured user through by a
  // fraction of a second - the run itself refuses either way.
  if (!stored) return { ready: true, reason: "" };

  if (!stored.status.chatConfigured) {
    return {
      ready: false,
      reason:
        "No AI model is set up for this workspace yet. Add a provider and API key to start generating.",
    };
  }
  if (!stored.status.encryptionAvailable) {
    return {
      ready: false,
      reason:
        "Credential encryption is not configured on the AI service, so API keys cannot be saved or read.",
    };
  }
  return { ready: true, reason: "" };
}
