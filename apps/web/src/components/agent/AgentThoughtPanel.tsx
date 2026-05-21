"use client";

import { useChatStore } from "@/stores/chat-store";
import {
  Brain,
  Wrench,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ChevronDown,
} from "lucide-react";
import { useState } from "react";
import type { AgentEvent } from "@slideagent/schemas/agent-events";

export function AgentThoughtPanel() {
  const { activeEvents } = useChatStore();
  const [isCollapsed, setIsCollapsed] = useState(false);

  const events = activeEvents ?? [];

  return (
    <div
      className="flex flex-col border-b border-surface-100"
      style={{ width: 380 }}
    >
      {/* Header */}
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="flex items-center justify-between px-4 py-2.5 hover:bg-surface-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Brain size={16} className="text-brand-400" />
          <span className="text-sm font-medium text-gray-300">
            Agent Thoughts
          </span>
          {events.length > 0 && (
            <span className="text-xs text-gray-500">
              ({events.length} events)
            </span>
          )}
        </div>
        <ChevronDown
          size={14}
          className={`text-gray-500 transition-transform ${isCollapsed ? "" : "rotate-180"}`}
        />
      </button>

      {/* Event Stream */}
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto px-4 pb-3 space-y-2 max-h-[50vh]">
          {events.length === 0 && (
            <p className="text-xs text-gray-500 text-center py-4">
              Agent events will appear here...
            </p>
          )}

          {events.map((event, i) => (
            <AgentEventRenderer key={i} event={event} />
          ))}

          {/* Live indicator */}
          {events.length > 0 &&
            events[events.length - 1].type !== "error" &&
            events[events.length - 1].type !== "slide_finalized" && (
              <div className="flex items-center gap-2 text-xs text-brand-400 py-1">
                <Loader2 size={12} className="animate-spin" />
                Agent working...
              </div>
            )}
        </div>
      )}
    </div>
  );
}

function AgentEventRenderer({ event }: { event: AgentEvent }) {
  switch (event.type) {
    case "thought":
      return (
        <div className="thought-block">
          <div className="flex items-center gap-1.5 mb-1">
            <Brain size={12} className="text-brand-400" />
            <span className="text-xs font-medium text-brand-300 uppercase tracking-wider">
              {event.phase.replace(/_/g, " ")}
            </span>
          </div>
          <p>{event.content}</p>
        </div>
      );

    case "tool_call":
      return (
        <div className="tool-call-block">
          <div className="flex items-center gap-1.5 mb-1">
            <Wrench size={12} className="text-amber-400" />
            <span className="text-xs font-medium text-amber-300">
              {event.toolName}
            </span>
          </div>
          <pre className="text-xs text-gray-400 overflow-x-auto whitespace-pre-wrap">
            {JSON.stringify(event.toolArgs, null, 2)}
          </pre>
        </div>
      );

    case "tool_result":
      return (
        <div className="tool-result-block">
          <div className="flex items-center gap-1.5 mb-1">
            <CheckCircle2 size={12} className="text-emerald-400" />
            <span className="text-xs font-medium text-emerald-300">
              {event.toolName} · {event.durationMs}ms
            </span>
            {event.tokenCount && (
              <span className="text-xs text-gray-500">
                ({event.tokenCount} tokens)
              </span>
            )}
          </div>
          <pre className="text-xs text-gray-400 overflow-x-auto whitespace-pre-wrap max-h-32">
            {typeof event.result === "string"
              ? event.result
              : JSON.stringify(event.result, null, 2)}
          </pre>
        </div>
      );

    case "slide_finalized":
      return (
        <div className="bg-brand-950/30 border border-brand-500/20 rounded-md px-3 py-2 text-sm">
          <div className="flex items-center gap-1.5">
            <CheckCircle2 size={12} className="text-brand-400" />
            <span className="text-xs font-medium text-brand-300">
              Slide {event.slideIndex + 1} finalized
            </span>
          </div>
        </div>
      );

    case "error":
      return (
        <div className="error-block">
          <div className="flex items-center gap-1.5 mb-1">
            <AlertCircle size={12} />
            <span className="text-xs font-medium">Error</span>
          </div>
          <p>{event.message}</p>
        </div>
      );

    case "progress":
      return (
        <div className="flex items-center gap-3 px-3 py-1.5 text-xs text-gray-400">
          <div className="flex-1 h-1 bg-surface-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-brand-500 rounded-full transition-all"
              style={{ width: `${(event.current / event.total) * 100}%` }}
            />
          </div>
          <span>
            {event.current}/{event.total} {event.message}
          </span>
        </div>
      );

    default:
      return null;
  }
}