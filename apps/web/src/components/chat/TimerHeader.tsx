import { useEffect, useState, useRef } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Clock } from "lucide-react";

export function TimerHeader() {
  const totalElapsedMs = useChatStore((s) => s.totalElapsedMs);
  const isGenerating = useChatStore((s) => s.isGenerating);
  const [elapsedMs, setElapsedMs] = useState(0);
  const startTimeRef = useRef<number | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // When generation starts, record start time immediately.
  // Use totalElapsedMs from backend if available (resume), else start fresh.
  useEffect(() => {
    if (isGenerating && startTimeRef.current === null) {
      const base = totalElapsedMs ?? 0;
      startTimeRef.current = Date.now() - base;
    }
    // When generation ends, clear the start time so a future run restarts.
    if (!isGenerating) {
      startTimeRef.current = null;
    }
  }, [isGenerating, totalElapsedMs]);

  // Live-update the elapsed time every 100ms while generating.
  useEffect(() => {
    if (isGenerating && startTimeRef.current !== null) {
      intervalRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startTimeRef.current!);
      }, 100);
      return () => {
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      };
    }
    // Not generating: show the final total from backend if present.
    setElapsedMs(totalElapsedMs ?? 0);
  }, [isGenerating, totalElapsedMs]);

  const formatTime = (ms: number): string => {
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;

    if (minutes > 0) {
      return `${minutes}m ${remainingSeconds}s`;
    }
    return `${remainingSeconds}s`;
  };

  // Hide when there is nothing to show.
  if (!isGenerating && elapsedMs === 0) return null;

  return (
    <div
      className="ws-timer-row"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "6px 16px",
        background: "color-mix(in srgb, var(--color-indigo, #4f46e5) 14%, transparent)",
        borderBottom: "1px solid color-mix(in srgb, var(--color-indigo, #4f46e5) 30%, transparent)",
      }}
    >
      <Clock size={13} style={{ color: "var(--color-indigo, #818cf8)" }} />
      <span
        style={{
          fontSize: "var(--text-caption, 12px)",
          fontWeight: 500,
          color: "var(--color-indigo, #818cf8)",
        }}
      >
        {isGenerating ? "Elapsed time: " : "Total time: "}
        {formatTime(elapsedMs)}
      </span>
    </div>
  );
}
