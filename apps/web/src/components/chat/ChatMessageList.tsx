"use client";

import { useChatStore } from "@/stores/chat-store";
import type { AgentEvent, ThoughtEvent, ToolCallEvent, ToolResultEvent } from "@slideagent/schemas";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Loader2,
  ChevronDown,
  ChevronRight,
  Brain,
  Wrench,
  CheckCircle,
  XCircle,
  AlertCircle,
  Image,
  BarChart3,
} from "lucide-react";

// Type guards
function isThoughtEvent(event: AgentEvent): event is ThoughtEvent {
  return event.type === "thought";
}

function isToolCallEvent(event: AgentEvent): event is ToolCallEvent {
  return event.type === "tool_call";
}

function isToolResultEvent(event: AgentEvent): event is ToolResultEvent {
  return event.type === "tool_result";
}

// User message
function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] bg-brand-600 text-white rounded-2xl px-4 py-2.5 text-sm">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}

// Typing indicator
function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="bg-gray-800/50 rounded-2xl px-4 py-2.5">
        <div className="flex gap-1">
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]"></span>
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]"></span>
          <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></span>
        </div>
      </div>
    </div>
  );
}

// Thinking block
function ThinkingBlock({ event }: { event: ThoughtEvent }) {
  const [expanded, setExpanded] = useState(false);
  const durationText = event.durationMs ? `${(event.durationMs / 1000).toFixed(1)}s` : "";

  return (
    <div className="bg-gray-800/30 rounded-lg border border-gray-700/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-left text-xs text-gray-400 hover:bg-gray-800/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Brain size={14} />
          <span className="font-mono">Thinking – {event.phase}</span>
          {durationText && <span className="text-gray-500">({durationText})</span>}
        </div>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {expanded && (
        <div className="px-3 pb-3 text-sm text-gray-300 whitespace-pre-wrap border-t border-gray-700/50 pt-2">
          {event.content}
        </div>
      )}
    </div>
  );
}

// Tool card
function ToolCard({ call, result }: { call: ToolCallEvent; result?: ToolResultEvent }) {
  const [expanded, setExpanded] = useState(false);
  const isPending = !result;
  const isSuccess = result?.success === true;
  const duration = result?.durationMs;
  const durationText = duration ? `${(duration / 1000).toFixed(1)}s` : "";

  let icon = <Wrench size={14} />;
  if (isPending) icon = <Loader2 size={14} className="animate-spin text-yellow-500" />;
  else if (isSuccess) icon = <CheckCircle size={14} className="text-green-500" />;
  else icon = <XCircle size={14} className="text-red-500" />;

  return (
    <div className="bg-gray-800/20 rounded-lg border border-gray-700/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-left text-xs"
      >
        <div className="flex items-center gap-2">
          {icon}
          <span className="font-mono text-gray-300">{call.toolName}</span>
          {durationText && <span className="text-gray-500">{durationText}</span>}
        </div>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {expanded && (
        <div className="px-3 pb-3 text-xs text-gray-400 border-t border-gray-700/50 pt-2 space-y-2">
          <div>
            <div className="font-mono text-gray-500 mb-1">Arguments:</div>
            <pre className="bg-gray-900 rounded p-2 overflow-x-auto">
              {JSON.stringify(call.toolArgs, null, 2)}
            </pre>
          </div>
          {result && (
            <div>
              <div className="font-mono text-gray-500 mb-1">Result:</div>
              <pre className="bg-gray-900 rounded p-2 overflow-x-auto">
                {JSON.stringify(result.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Other event renderers
function SlideFinalizedBlock({ event }: { event: AgentEvent & { type: "slide_finalized" } }) {
  return (
    <div className="bg-gray-800/20 rounded-lg border border-gray-700/50 px-3 py-2 text-sm">
      <div className="flex items-center gap-2 text-gray-300">
        <Image size={14} />
        <span>Slide {event.slideIndex} finalized</span>
      </div>
    </div>
  );
}

function ErrorBlock({ event }: { event: AgentEvent & { type: "error" } }) {
  return (
    <div className="bg-red-900/20 rounded-lg border border-red-700/50 px-3 py-2 text-sm text-red-300">
      <div className="flex items-center gap-2">
        <AlertCircle size={14} />
        <span>{event.message}</span>
      </div>
    </div>
  );
}

function ProgressBlock({ event }: { event: AgentEvent & { type: "progress" } }) {
  const percent = Math.round((event.current / event.total) * 100);
  return (
    <div className="bg-gray-800/20 rounded-lg border border-gray-700/50 px-3 py-2 text-sm">
      <div className="flex items-center gap-2 text-gray-300 mb-1">
        <BarChart3 size={14} />
        <span>{event.message}</span>
        <span className="text-gray-500">({event.current}/{event.total})</span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-1.5">
        <div
          className="bg-brand-500 h-1.5 rounded-full transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

// Main assistant message component
function AssistantMessage({ message }: { message: any }) {
  const events: AgentEvent[] = message.agentEventStream || [];
  const hasEvents = events.length > 0;
  const hasContent = !!message.content;
  const isComplete = message.isComplete;

  // Group tool calls
  const toolCallsMap = new Map<string, { call: ToolCallEvent; result?: ToolResultEvent }>();
  const thoughtEvents: ThoughtEvent[] = [];
  const otherEvents: AgentEvent[] = [];

  for (const ev of events) {
    if (isToolCallEvent(ev)) {
      toolCallsMap.set(ev.callId, { call: ev });
    } else if (isToolResultEvent(ev)) {
      const existing = toolCallsMap.get(ev.callId);
      if (existing) existing.result = ev;
      else toolCallsMap.set(ev.callId, { call: ev as any, result: ev });
    } else if (isThoughtEvent(ev)) {
      thoughtEvents.push(ev);
    } else {
      otherEvents.push(ev);
    }
  }

  // Loading state
  if (!hasEvents && !hasContent && !isComplete) {
    return <TypingIndicator />;
  }

  return (
    <div className="flex justify-start max-w-[85%]">
      <div className="w-full space-y-3">
        {thoughtEvents.map((ev, idx) => (
          <ThinkingBlock key={`thought-${idx}`} event={ev} />
        ))}
        {Array.from(toolCallsMap.values()).map((item, idx) => (
          <ToolCard key={`tool-${idx}-${item.call.callId}`} call={item.call} result={item.result} />
        ))}
        {otherEvents.map((ev, idx) => {
          if (ev.type === "slide_finalized") return <SlideFinalizedBlock key={`slide-${idx}`} event={ev} />;
          if (ev.type === "error") return <ErrorBlock key={`error-${idx}`} event={ev} />;
          if (ev.type === "progress") return <ProgressBlock key={`progress-${idx}`} event={ev} />;
          return null;
        })}
        {hasContent && (
          <div className="mt-3 pt-3 border-t border-gray-700/50">
            <ReactMarkdown className="prose prose-invert prose-sm max-w-none">
              {message.content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}

// Main component
export function ChatMessageList() {
  const messages = useChatStore((s) => s.activeMessages);
  const isSending = useChatStore((s) => s.isSending);

  // Debug log
  console.log("ChatMessageList messages:", messages);

  if (messages.length === 0 && !isSending) {
    return (
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="flex flex-col items-center justify-center h-full text-center text-gray-500">
          <p>No messages yet. Start by uploading a document or typing a request.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
      {messages.map((msg) => (
        <div key={msg.id}>
          {msg.role === "user" ? (
            <UserMessage content={msg.content} />
          ) : (
            <AssistantMessage message={msg} />
          )}
        </div>
      ))}
    </div>
  );
}