"use client";

import { useEffect, useMemo, useState } from "react";
import { Clock } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import type { RateLimitEvent } from "@thinkslides/schemas";

/**
 * The provider back-off currently in progress, counted down live.
 *
 * Rate limiting is the most common thing to interrupt a generation, and an
 * unexplained pause reads as a hang - so a wait has to be visible while it is
 * happening. It does not have to be remembered: this floats over the bottom of
 * the chat panel for the seconds it is true and leaves no trace, rather than
 * settling into the transcript where a handful of waits would outweigh the
 * work they interrupted.
 */
export function RetryStrip() {
  const retry = useChatStore((s) => s.activeRetry);
  if (!retry) return null;
  // Keyed per wait so each new attempt gets its own countdown and its own
  // entrance, rather than the numbers jumping inside a strip already there.
  return <Strip key={`${retry.attempt}-${retry.timestamp}`} ev={retry} />;
}

function Strip({ ev }: { ev: RateLimitEvent }) {
  // Anchored to when the event arrived, not to `ev.timestamp`: the wait is
  // announced the moment it begins, so arrival is off by the network hop,
  // whereas the server's clock is off by however far it has drifted from the
  // browser's - which on a 10s back-off is the difference between a countdown
  // and a stuck number.
  const startedAt = useMemo(() => Date.now(), []);
  const total = Number(ev.retryInSeconds) || 0;
  const [now, setNow] = useState(() => Date.now());

  const remaining = Math.max(0, total - (now - startedAt) / 1000);
  useEffect(() => {
    if (remaining <= 0) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [remaining]);

  const isRateLimit = ev.reason !== "transient";
  const who = ev.provider ? ` by ${ev.provider}` : "";
  const headline = isRateLimit ? `Rate limited${who}` : "Provider error";
  const when =
    remaining > 0.05
      ? `retrying in ${remaining < 10 ? remaining.toFixed(1) : Math.ceil(remaining)}s`
      : "retrying now";
  const attempt = ev.maxAttempts ? ` (${ev.attempt} of ${ev.maxAttempts})` : "";

  return (
    <div
      className="ws-retry-strip"
      role="status"
      aria-live="polite"
      // The countdown reruns four times a second; announcing a label that
      // leaves the number out says the same thing once.
      aria-label={`${headline}, retrying${attempt}`}
    >
      <Clock size={13} aria-hidden />
      <span aria-hidden>
        {headline} - {when}
        {attempt}
      </span>
    </div>
  );
}
