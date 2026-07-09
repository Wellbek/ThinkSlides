import { useEffect, useState, useRef } from "react";
import { useChatStore } from "@/stores/chat-store";
import { Clock } from "lucide-react";

export function TimerHeader() {
  const totalElapsedMs = useChatStore((s) => s.totalElapsedMs);
  const isGenerating = useChatStore((s) => s.isGenerating);
  const [elapsedMs, setElapsedMs] = useState(0);
  const startTimeRef = useRef<number | null>(null);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // When generation starts, record start time
  useEffect(() => {
    if (isGenerating && !startTimeRef.current && totalElapsedMs !== null) {
      startTimeRef.current = Date.now() - (totalElapsedMs || 0);
    }
  }, [isGenerating, totalElapsedMs]);

  // Update timer every second while generating
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
    } else {
      setElapsedMs(totalElapsedMs || 0);
    }
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

  if (!isGenerating && elapsedMs === 0) return null;

  return (
    <div className="px-6 py-2 bg-indigo-dark/50 border-b border-indigo-light/30 flex items-center gap-2">
      <Clock size={14} className="text-indigo" />
      <span className="text-caption font-medium text-indigo">
        {isGenerating ? "Elapsed time: " : "Total time: "}
        {formatTime(elapsedMs)}
      </span>
    </div>
  );
}