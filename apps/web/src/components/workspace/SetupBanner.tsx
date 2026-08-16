"use client";

/**
 * Top-bar alert shown until the organisation has a usable chat model.
 *
 * Without one every generation fails at the first LLM call, so this is a
 * blocking condition and gets a persistent banner rather than an error the
 * user only discovers after typing a prompt.
 */

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { UNCONFIGURED_STATUS, useSettingsStore } from "@/stores/settings-store";

export function SetupBanner() {
  const stored = useSettingsStore((s) => s.stored);
  const refreshStatus = useSettingsStore((s) => s.refreshStatus);
  const setSettingsOpen = useChatStore((s) => s.setSettingsOpen);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // Until the summary arrives, assume things are fine: flashing a setup
  // warning at every configured user on every page load would be worse than
  // showing it a moment late.
  if (!stored) return null;

  const status = stored.status ?? UNCONFIGURED_STATUS;
  if (status.chatConfigured && status.encryptionAvailable) return null;

  // Phrased as the offer it is. Nothing can be generated until this is done,
  // so the banner's job is to get the user into settings, not to inform them
  // of a fact they can do nothing with.
  const message = !status.chatConfigured
    ? "No AI model is set up for this workspace yet, so nothing can be generated. Set one up now?"
    : "Credential encryption is not configured on the AI service, so API keys cannot be saved. See the SETTINGS_ENCRYPTION_KEY setup step.";

  return (
    <div className="ws-setup-banner" role="alert">
      <AlertTriangle size={13} className="ws-setup-banner-icon" />
      <span className="ws-setup-banner-text">{message}</span>
      <button className="ws-setup-banner-cta" onClick={() => setSettingsOpen(true)}>
        {status.chatConfigured ? "Open settings" : "Set up now"}
      </button>
    </div>
  );
}
