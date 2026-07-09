import { useChatStore } from "@/stores/chat-store";
import { Clock } from "lucide-react";

export function TimerDisplay() {
  const totalElapsedMs = useChatStore((s) => s.totalElapsedMs);
  const isGenerating = useChatStore((s) => s.isGenerating);

  if (!totalElapsedMs && !isGenerating) return null;

  const formatTime = (ms: number | null): string => {
    if (!ms) return "0s";
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;

    if (minutes > 0) {
      return `${minutes}m ${remainingSeconds}s`;
    }
    return `${remainingSeconds}s`;
  };

  return (
    <div className="flex items-center gap-2 text-xs text-slate-500 bg-slate-100 px-3 py-1.5 rounded-full">
      <Clock size={12} className="text-slate-400" />
      <span className="font-medium">
        {isGenerating ? "Working: " : "Total: "}
        {formatTime(totalElapsedMs)}
      </span>
    </div>
  );
}