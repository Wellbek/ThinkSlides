"use client";

import { useChatStore } from "@/stores/chat-store";
import type {
  AgentEvent,
  ThoughtEvent,
  ToolCallEvent,
  ToolResultEvent,
} from "@slideagent/schemas";
import { useState, useEffect, useRef, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { motion, AnimatePresence } from "framer-motion";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import {
  Loader2,
  ChevronRight,
  ChevronDown,
  Brain,
  Wrench,
  CheckCircle2,
  XCircle,
  Sparkles,
  ArrowLeft,
  Clock,
  X,
} from "lucide-react";

// Type guards 
function isThought(e: AgentEvent): e is ThoughtEvent {
  return e.type === "thought";
}
function isToolCall(e: AgentEvent): e is ToolCallEvent {
  return e.type === "tool_call";
}
function isToolResult(e: AgentEvent): e is ToolResultEvent {
  return e.type === "tool_result";
}

// Helpers 
function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  if (m > 0) return `${m}:${String(s % 60).padStart(2, "0")}`;
  return `${s}s`;
}

function toolLabel(name: string) {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// Live wall‑clock timer (with RAF) 
function useLiveClock(startMs: number | null, frozen?: number | null) {
  const [elapsed, setElapsed] = useState(frozen ?? 0);
  const rafRef = useRef<number>();

  useEffect(() => {
    if (frozen != null) {
      setElapsed(frozen);
      return;
    }
    if (startMs == null) return;
    const tick = () => {
      setElapsed(Date.now() - startMs);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [startMs, frozen]);

  return elapsed;
}

// Syntax‑highlighted code block (with fallback) 
function CodeBlock({ content, language = "json" }: { content: string; language?: string }) {
  try {
    return (
      <div className="rounded-lg overflow-hidden border border-gray-700/50 shadow-inner">
        <SyntaxHighlighter
          language={language}
          style={vscDarkPlus}
          customStyle={{
            background: "rgba(0,0,0,0.3)",
            borderRadius: "0.5rem",
            padding: "0.75rem",
            fontSize: "11px",
            margin: 0,
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          }}
          wrapLongLines
        >
          {content}
        </SyntaxHighlighter>
      </div>
    );
  } catch {
    return (
      <pre className="bg-black/40 rounded-lg p-3 overflow-x-auto font-mono text-[11px] text-gray-300 whitespace-pre-wrap border border-gray-700/30">
        {content}
      </pre>
    );
  }
}

// Step shape 
interface Step {
  id: string;
  phase: string;
  kind: "thought" | "tool";
  thoughtContent?: string;
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  toolResult?: unknown;
  toolSuccess?: boolean;
  backendDurationMs?: number;
  startMs: number;
  endMs?: number;
  revertReason?: string;
}

// Build steps from raw events (with revert detection) 
const PHASE_ORDER = [
  "content_analysis",
  "structure_planning",
  "layout_selection",
  "content_generation",
  "validation",
  "rendering",
];

function deriveSteps(events: AgentEvent[]): Step[] {
  const steps: Step[] = [];
  const resultMap = new Map<string, ToolResultEvent>();
  for (const e of events) {
    if (isToolResult(e)) resultMap.set(e.callId, e);
  }

  for (const e of events) {
    if (isToolResult(e)) continue;

    const id = `${e.type}-${e.timestamp}`;
    const startMs = new Date(e.timestamp).getTime();

    if (isThought(e)) {
      if (e.phase === "complete") continue;
      const last = steps[steps.length - 1];
      if (last && last.kind === "thought" && last.phase === e.phase) {
        last.thoughtContent = e.content;
        continue;
      }
      steps.push({
        id,
        phase: e.phase,
        kind: "thought",
        thoughtContent: e.content,
        startMs,
      });
    } else if (isToolCall(e)) {
      const result = resultMap.get(e.callId);
      const step: Step = {
        id,
        phase: e.phase,
        kind: "tool",
        toolName: e.toolName,
        toolArgs: e.toolArgs,
        startMs,
      };
      if (result) {
        step.toolResult = result.result;
        step.toolSuccess = (result as any).success !== false;
        step.backendDurationMs = result.durationMs;
        step.endMs = startMs + (result.durationMs ?? 0);
      }
      steps.push(step);
    }
  }

  // Set endMs for steps that don't already have it (using next step's start time)
  for (let i = 0; i < steps.length; i++) {
    if (steps[i].endMs == null) {
      const next = steps[i + 1];
      if (next) {
        steps[i].endMs = next.startMs;
      }
    }
  }

  // Detect backward jumps and mark the causing step
  for (let i = 0; i < steps.length - 1; i++) {
    const current = steps[i];
    const next = steps[i + 1];
    const currentIdx = PHASE_ORDER.indexOf(current.phase);
    const nextIdx = PHASE_ORDER.indexOf(next.phase);
    if (currentIdx !== -1 && nextIdx !== -1 && nextIdx < currentIdx && !current.revertReason) {
      let reason = "Re‑evaluating";
      if (current.phase === "validation") reason = "Validation failed, reverting";
      else if (current.phase === "rendering") reason = "Rendering issue, stepping back";
      else if (current.phase === "content_generation") reason = "Content restructure needed";
      current.revertReason = reason;
    }
  }

  return steps;
}

// Individual step row (full detail) 
function StepRow({ step, isLive, isLastStep, isStreaming }: { step: Step; isLive: boolean; isLastStep: boolean; isStreaming: boolean }) {
  const [open, setOpen] = useState(false);
  
  // A step is considered complete if:
  // - it has an explicit endMs, OR
  // - it's the last step and streaming has finished (so we mark it as done)
  const isComplete = step.endMs != null || (!isStreaming && isLastStep);
  const isPending = !isComplete;

  let frozenDuration: number | null = null;
  if (step.endMs != null) {
    frozenDuration = step.endMs - step.startMs;
  } else if (step.backendDurationMs != null) {
    frozenDuration = step.backendDurationMs;
  } else if (!isStreaming && isLastStep) {
    // For the final step when streaming ends, we can compute its duration as (current time - startMs)
    // but we'll just show the elapsed time live (it will freeze once the component re-renders).
    frozenDuration = Date.now() - step.startMs;
  }
  const elapsed = useLiveClock(
    isPending && isLive ? step.startMs : null,
    frozenDuration,
  );

  const hasArgs = step.kind === "tool" && step.toolArgs != null;
  const hasResult = step.kind === "tool" && step.toolResult != null;
  const hasDetail = hasArgs || hasResult;

  // Status icon & colour
  let StatusIcon = null;
  let statusColor = "";
  if (isComplete) {
    if (step.kind === "tool" && step.toolSuccess === false) {
      StatusIcon = XCircle;
      statusColor = "text-red-400 drop-shadow-sm";
    } else {
      StatusIcon = CheckCircle2;
      statusColor = "text-emerald-400 drop-shadow-sm";
    }
  } else {
    StatusIcon = Loader2;
    statusColor = "text-blue-400 drop-shadow-sm";
  }

  const PhaseIcon = step.kind === "tool" ? Wrench : Brain;
  const durationLabel = isComplete && frozenDuration != null && frozenDuration >= 50
    ? fmtMs(frozenDuration)
    : null;
  const hasRevert = !!step.revertReason;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -8, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.97 }}
      transition={{ type: "spring", stiffness: 500, damping: 30 }}
      className={`group rounded-lg transition-all duration-200 ${
        hasRevert ? "bg-yellow-500/5 border-l-2 border-yellow-500/60 pl-2 -ml-1" : ""
      }`}
    >
      <button
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all ${
          hasDetail ? "hover:bg-white/[0.04] cursor-pointer" : "cursor-default"
        }`}
      >
        <PhaseIcon size={14} className="text-gray-400 group-hover:text-gray-300 shrink-0 transition-colors" />

        <span className="flex-1 text-[13px] font-medium text-gray-200 truncate leading-snug">
          {step.kind === "thought"
            ? step.thoughtContent?.split("\n")[0].slice(0, 90) ?? "Thinking…"
            : toolLabel(step.toolName ?? "")}
          {hasRevert && (
            <span className="ml-2 text-[10px] text-yellow-400 inline-flex items-center gap-1 font-normal">
              <ArrowLeft size={10} />
              {step.revertReason}
            </span>
          )}
        </span>

        <div className="flex items-center gap-2 shrink-0">
          {durationLabel && (
            <span className="text-[10px] font-mono text-gray-500 flex items-center gap-1">
              <Clock size={10} />
              {durationLabel}
            </span>
          )}
          {StatusIcon && (
            <span className={`shrink-0 ${statusColor}`}>
              <StatusIcon size={13} className={isPending && isLive ? "animate-spin" : ""} />
            </span>
          )}
          {hasDetail && (
            <ChevronRight
              size={13}
              className={`shrink-0 text-gray-500 transition-all duration-200 group-hover:text-gray-400 ${
                open ? "rotate-90" : ""
              }`}
            />
          )}
        </div>
      </button>

      <AnimatePresence>
        {open && hasDetail && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            className="overflow-hidden"
          >
            <div className="ml-10 mr-3 mb-3 mt-1 space-y-3">
              {hasArgs && step.toolArgs !== undefined && step.toolArgs !== null && (
                <div>
                  <p className="text-[10px] font-mono text-gray-400 uppercase tracking-wider mb-1.5">Input</p>
                  <CodeBlock content={JSON.stringify(step.toolArgs, null, 2)} language="json" />
                </div>
              )}
              {hasResult && step.toolResult !== undefined && step.toolResult !== null && (
                <div>
                  <p className="text-[10px] font-mono text-gray-400 uppercase tracking-wider mb-1.5">Output</p>
                  <CodeBlock content={JSON.stringify(step.toolResult, null, 2)} language="json" />
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// Phase badge (with active underline) 
const PHASE_LABELS: Record<string, string> = {
  content_analysis: "Analyze",
  structure_planning: "Plan",
  layout_selection: "Design",
  content_generation: "Write",
  validation: "Validate",
  rendering: "Render",
};

function PhaseBadge({ phase, active, showSeparator }: { phase: string; active: boolean; showSeparator: boolean }) {
  const label = PHASE_LABELS[phase] || phase;
  return (
    <div className="flex items-center gap-2">
      {showSeparator && <span className="text-gray-700 text-[10px] font-light"> </span>}
      <span
        className={`text-[10px] font-semibold tracking-wide transition-all duration-200 ${
          active
            ? "text-blue-400 border-b border-blue-400/50 pb-0.5"
            : "text-gray-500 hover:text-gray-400"
        }`}
      >
        {label}
      </span>
    </div>
  );
}

// Compact step icon (used in collapsed view) 
function CompactStepIcon({ step, onClick }: { step: Step; onClick: () => void }) {
  const isComplete = step.endMs != null;
  const isFailed = step.kind === "tool" && step.toolSuccess === false;

  let StatusIcon = Brain;
  let statusColor = "text-gray-500";
  if (isComplete) {
    if (isFailed) {
      StatusIcon = XCircle;
      statusColor = "text-red-400";
    } else {
      StatusIcon = CheckCircle2;
      statusColor = "text-emerald-400";
    }
  } else {
    StatusIcon = Loader2;
    statusColor = "text-blue-400";
  }

  if (step.kind === "tool") StatusIcon = Wrench;

  return (
    <motion.button
      layoutId={`compact-${step.id}`}
      onClick={onClick}
      className="relative group"
      whileHover={{ scale: 1.1 }}
      transition={{ type: "spring", stiffness: 500, damping: 30 }}
    >
      <div className="w-7 h-7 rounded-full bg-gray-800/60 border border-gray-700/50 flex items-center justify-center backdrop-blur-sm">
        <StatusIcon size={12} className={statusColor} />
      </div>
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-1.5 py-0.5 bg-gray-900 text-gray-300 text-[9px] rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition pointer-events-none z-10">
        {step.kind === "thought" ? step.phase : step.toolName}
      </div>
    </motion.button>
  );
}

// Main EventStream component (with collapsed/expanded states) 
function EventStream({ events, isStreaming }: { events: AgentEvent[]; isStreaming: boolean }) {
  const steps = useMemo(() => deriveSteps(events), [events]);

  const globalStartMs = useMemo(() => {
    if (steps.length === 0) return Date.now();
    return steps[0].startMs;
  }, [steps]);

  const [frozenTotal, setFrozenTotal] = useState<number | null>(null);
  useEffect(() => {
    if (!isStreaming && frozenTotal == null) {
      const lastStep = steps[steps.length - 1];
      if (lastStep && lastStep.endMs) {
        setFrozenTotal(lastStep.endMs - globalStartMs);
      } else {
        setFrozenTotal(Date.now() - globalStartMs);
      }
    }
  }, [isStreaming, steps, globalStartMs, frozenTotal]);

  const totalElapsed = useLiveClock(isStreaming ? globalStartMs : null, frozenTotal);

  // Active step = last incomplete step
  const activeStepIndex = useMemo(() => {
    for (let i = steps.length - 1; i >= 0; i--) {
      if (steps[i].endMs == null) return i;
    }
    return -1;
  }, [steps]);

  const currentPhase = activeStepIndex >= 0 ? steps[activeStepIndex].phase : steps[steps.length - 1]?.phase || "idle";

  const [historyOpen, setHistoryOpen] = useState(false);
  const TAIL = 3;
  const completedSteps = steps.filter((s) => s.endMs != null);
  const historySteps = completedSteps.slice(0, -TAIL);
  const recentSteps = steps.slice(-TAIL);

  // Determine overall status after streaming ends
  const hasFailure = steps.some((s) => s.kind === "tool" && s.toolSuccess === false);
  const overallStatus = hasFailure ? "Failed" : "Complete";
  const OverallIcon = hasFailure ? XCircle : CheckCircle2;
  const overallColor = hasFailure ? "text-red-400" : "text-emerald-400";

  // Collapsed state for when streaming is done
  const [isCollapsed, setIsCollapsed] = useState(false);
  // Auto-collapse when streaming ends
  useEffect(() => {
    if (!isStreaming && !isCollapsed && steps.length > 0) {
      setIsCollapsed(true);
    }
  }, [isStreaming, steps.length, isCollapsed]);

  // Expanded view (full step list)
  if (!isCollapsed) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
        className="w-full rounded-2xl border border-gray-700/40 bg-gradient-to-br from-gray-900/30 to-gray-800/20 backdrop-blur-md overflow-hidden shadow-xl shadow-black/10"
      >
        {/* Header with phase pipeline and close button (if streaming is done) */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-700/40 bg-gray-900/20">
          <div className="flex items-center gap-1">
            {PHASE_ORDER.map((p, idx) => (
              <PhaseBadge key={p} phase={p} active={p === currentPhase} showSeparator={idx > 0} />
            ))}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="bg-gray-800/40 rounded-full px-2 py-0.5 flex items-center gap-1.5">
              {isStreaming && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />}
              <span className="text-[10px] font-mono text-gray-400 tabular-nums">{fmtElapsed(totalElapsed)}</span>
            </div>
            {!isStreaming && (
              <button
                onClick={() => setIsCollapsed(true)}
                className="p-1 rounded-md hover:bg-gray-700/50 transition-colors"
                title="Collapse"
              >
                <X size={12} className="text-gray-400" />
              </button>
            )}
          </div>
        </div>

        {/* Steps container */}
        <div className="px-2 py-2 space-y-0.5">
          {historySteps.length > 0 && (
            <>
              <button
                onClick={() => setHistoryOpen((o) => !o)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-medium text-gray-500 hover:text-gray-300 transition-colors"
              >
                <ChevronDown size={11} className={`transition-transform duration-200 ${historyOpen ? "rotate-180" : ""}`} />
                {historyOpen ? "Hide" : "Show"} {historySteps.length} earlier {historySteps.length === 1 ? "step" : "steps"}
              </button>
              <AnimatePresence>
                {historyOpen && (
                  <motion.div
                    initial={{ height: 0 }}
                    animate={{ height: "auto" }}
                    exit={{ height: 0 }}
                    transition={{ type: "spring", stiffness: 400, damping: 35 }}
                    className="overflow-hidden"
                  >
                    <div className="pl-3 border-l border-gray-700/30 ml-1 space-y-0.5">
                      {historySteps.map((step) => (
                        <StepRow
                          key={step.id}
                          step={step}
                          isLive={false}
                          isLastStep={false}
                          isStreaming={isStreaming}
                        />
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </>
          )}

          {recentSteps.map((step, idx) => (
            <StepRow
              key={step.id}
              step={step}
              isLive={idx === recentSteps.length - 1 && isStreaming && step.endMs == null}
              isLastStep={idx === recentSteps.length - 1}
              isStreaming={isStreaming}
            />
          ))}

          {steps.length === 0 && (
            <div className="flex items-center gap-2 px-3 py-2 text-[12px] text-gray-400">
              <Loader2 size={12} className="animate-spin text-blue-400" />
              <span>Initializing context...</span>
            </div>
          )}
        </div>
      </motion.div>
    );
  }

  // Collapsed view: horizontal icon row + summary
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
      className="w-full"
    >
      <button
        onClick={() => setIsCollapsed(false)}
        className="w-full flex items-center justify-between gap-3 px-3 py-2 rounded-xl border border-gray-700/40 bg-gray-800/30 backdrop-blur-sm hover:bg-gray-800/50 transition-all group"
      >
        <div className="flex items-center gap-1.5 flex-wrap">
          {steps.slice(0, 8).map((step) => (
            <CompactStepIcon key={step.id} step={step} onClick={() => setIsCollapsed(false)} />
          ))}
          {steps.length > 8 && (
            <span className="text-[10px] text-gray-500 font-mono">+{steps.length - 8}</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] font-mono text-gray-500">{fmtElapsed(totalElapsed)}</span>
          <div className="flex items-center gap-1 bg-gray-800/60 rounded-full px-2 py-0.5">
            <OverallIcon size={10} className={overallColor} />
            <span className="text-[10px] font-medium text-gray-300">{overallStatus}</span>
          </div>
          <span className="text-[10px] text-gray-500">({steps.length})</span>
        </div>
      </button>
    </motion.div>
  );
}

// Typing Indicator 
function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 px-4 py-2">
      {[0, 0.15, 0.3].map((delay, i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
          style={{ animationDelay: `${delay}s` }}
        />
      ))}
    </div>
  );
}

// User Message 
function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <motion.div
        initial={{ opacity: 0, y: 8, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 500, damping: 30 }}
        className="max-w-[78%] bg-blue-500/15 text-gray-100 rounded-2xl px-4 py-2.5 text-sm border border-blue-500/30 shadow-md shadow-blue-900/10 backdrop-blur-sm"
      >
        <p className="whitespace-pre-wrap leading-relaxed font-sans">{content}</p>
      </motion.div>
    </div>
  );
}

// Assistant Message (wrapper) 
function AssistantMessage({ message }: { message: any }) {
  const events: AgentEvent[] = message.agentEventStream ?? [];
  const isComplete = !!message.isComplete;
  const isStreaming = !isComplete;

  if (!events.length && !message.content && !isComplete) {
    return (
      <div className="flex justify-start">
        <div className="bg-gray-800/50 rounded-2xl px-4 py-2 border border-gray-700/40 backdrop-blur-sm">
          <TypingIndicator />
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[88%] space-y-3">
        {events.length > 0 && <EventStream events={events} isStreaming={isStreaming} />}

        <AnimatePresence>
          {message.content && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
              className="text-sm text-gray-300 leading-relaxed prose prose-invert prose-sm max-w-none prose-p:text-gray-300 prose-code:text-gray-300 font-sans"
            >
              <ReactMarkdown
                components={{
                  code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || "");
                    const isInline = !match;
                    const content = String(children).replace(/\n$/, "");
                    if (!isInline && match) {
                      return <CodeBlock content={content} language={match[1]} />;
                    }
                    return (
                      <code className="bg-gray-800/80 rounded-md px-1.5 py-0.5 text-xs font-mono text-blue-300" {...props}>
                        {children}
                      </code>
                    );
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

// Empty State 
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center">
      <div className="space-y-3">
        <div className="mx-auto w-10 h-10 rounded-xl bg-gray-800/50 border border-gray-700/50 flex items-center justify-center backdrop-blur-sm">
          <Sparkles size={18} className="text-gray-500" />
        </div>
        <p className="text-sm text-gray-500 font-sans">
          Upload a document or describe your presentation
        </p>
      </div>
    </div>
  );
}

// Root 
export function ChatMessageList() {
  const messages = useChatStore((s) => s.activeMessages);
  const isSending = useChatStore((s) => s.isSending);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  if (messages.length === 0 && !isSending) {
    return (
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <EmptyState />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5 font-sans scroll-smooth">
      {messages.map((msg) =>
        msg.role === "user" ? (
          <UserMessage key={msg.id} content={msg.content} />
        ) : (
          <AssistantMessage key={msg.id} message={msg} />
        )
      )}
      <div ref={bottomRef} />
    </div>
  );
}