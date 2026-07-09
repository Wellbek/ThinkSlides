"use client";

import { useChatStore } from "@/stores/chat-store";
import type {
  AgentEvent,
  ThoughtEvent,
  ToolCallEvent,
  ToolResultEvent,
  SlideFinalizedEvent,
} from "@thinkslides/schemas";
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
import { GeneratedFileCard } from "./GeneratedFileCard";
import { Sources } from "./Sources";
import type { SourceInfo } from "@thinkslides/schemas";
import type { GeneratedFile } from "@/stores/chat-store";
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
// Color constants for status
const STATUS_COLORS = {
  success: "text-success",
  error: "text-error",
  ice: "text-ice",
  fog: "text-fog",
  paper: "text-paper",
  warning: "text-warning",
};

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
      <div className="rounded-md overflow-hidden border border-iron shadow-subtle">
        <SyntaxHighlighter
          language={language}
          style={vscDarkPlus}
          customStyle={{
            background: "var(--surface-obsidian)",
            borderRadius: "var(--radius-md)",
            padding: "var(--spacing-12)",
            fontSize: "var(--text-caption)",
            margin: 0,
            fontFamily: "var(--font-gtamericamono)",
          }}
          wrapLongLines
        >
          {content}
        </SyntaxHighlighter>
      </div>
    );
  } catch {
    return (
      <pre className="bg-obsidian rounded-md p-3 overflow-x-auto font-mono text-caption text-fog whitespace-pre-wrap border border-iron">
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
      statusColor = "text-error drop-shadow-sm";
    } else {
      StatusIcon = CheckCircle2;
      statusColor = "text-success drop-shadow-sm";
    }
  } else {
    StatusIcon = Loader2;
    statusColor = "text-ice drop-shadow-sm";
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
      className={`group rounded-md transition-all duration-200 ${
        hasRevert ? "bg-warning/5 border-l-2 border-warning/60 pl-2 -ml-1" : ""
      }`}
    >
      <button
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-left transition-all ${
          hasDetail ? "hover:bg-graphite cursor-pointer" : "cursor-default"
        }`}
      >
        <PhaseIcon size={14} className="text-fog group-hover:text-paper shrink-0 transition-colors" />

        <span className="flex-1 text-body font-medium text-paper truncate leading-snug">
          {step.kind === "thought"
            ? step.thoughtContent?.split("\n")[0].slice(0, 90) ?? "Thinking…"
            : toolLabel(step.toolName ?? "")}
          {hasRevert && (
            <span className="ml-2 text-caption text-warning inline-flex items-center gap-1 font-normal">
              <ArrowLeft size={10} />
              {step.revertReason}
            </span>
          )}
        </span>

        <div className="flex items-center gap-2 shrink-0">
          {durationLabel && (
            <span className="text-caption font-mono text-fog flex items-center gap-1">
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
                  <p className="text-caption font-mono text-fog uppercase tracking-wider mb-1.5">Input</p>
                  <CodeBlock content={JSON.stringify(step.toolArgs, null, 2)} language="json" />
                </div>
              )}
              {hasResult && step.toolResult !== undefined && step.toolResult !== null && (
                <div>
                  <p className="text-caption font-mono text-fog uppercase tracking-wider mb-1.5">Output</p>
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
      {showSeparator && <span className="text-iron text-caption font-light"> </span>}
      <span
        className={`text-caption font-semibold tracking-wide transition-all duration-200 ${
          active
            ? "text-ice border-b border-ice/50 pb-0.5"
            : "text-fog hover:text-paper"
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
  let statusColor = "text-fog";
  if (isComplete) {
    if (isFailed) {
      StatusIcon = XCircle;
      statusColor = "text-error";
    } else {
      StatusIcon = CheckCircle2;
      statusColor = "text-success";
    }
  } else {
    StatusIcon = Loader2;
    statusColor = "text-ice";
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
      <div className="w-7 h-7 rounded-full bg-graphite/60 border border-iron/50 flex items-center justify-center backdrop-blur-sm">
        <StatusIcon size={12} className={statusColor} />
      </div>
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-1.5 py-0.5 bg-obsidian text-paper text-caption rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition pointer-events-none z-10">
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
  const overallColor = hasFailure ? "text-error" : "text-success";

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
        className="w-full rounded-lg border border-iron bg-gradient-to-br from-obsidian/30 to-graphite/20 backdrop-blur-md overflow-hidden shadow-subtle"
      >
        {/* Header with phase pipeline and close button (if streaming is done) */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-iron bg-obsidian/20">
          <div className="flex items-center gap-1">
            {PHASE_ORDER.map((p, idx) => (
              <PhaseBadge key={p} phase={p} active={p === currentPhase} showSeparator={idx > 0} />
            ))}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="bg-graphite/40 rounded-full px-2 py-0.5 flex items-center gap-1.5">
              {isStreaming && <span className="w-1.5 h-1.5 rounded-full bg-ice animate-pulse" />}
              <span className="text-caption font-mono text-fog tabular-nums">{fmtElapsed(totalElapsed)}</span>
            </div>
            {!isStreaming && (
              <button
                onClick={() => setIsCollapsed(true)}
                className="p-1 rounded-md hover:bg-graphite/50 transition-colors"
                title="Collapse"
              >
                <X size={12} className="text-fog" />
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
                className="flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-fog hover:text-paper transition-colors"
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
                    <div className="pl-3 border-l border-iron/30 ml-1 space-y-0.5">
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
            <div className="flex items-center gap-2 px-3 py-2 text-caption text-fog">
              <Loader2 size={12} className="animate-spin text-ice" />
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
        className="w-full flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-iron bg-graphite/30 backdrop-blur-sm hover:bg-graphite/50 transition-all group"
      >
        <div className="flex items-center gap-1.5 flex-wrap">
          {steps.slice(0, 8).map((step) => (
            <CompactStepIcon key={step.id} step={step} onClick={() => setIsCollapsed(false)} />
          ))}
          {steps.length > 8 && (
            <span className="text-caption text-fog font-mono">+{steps.length - 8}</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-caption font-mono text-fog">{fmtElapsed(totalElapsed)}</span>
          <div className="flex items-center gap-1 bg-graphite/60 rounded-full px-2 py-0.5">
            <OverallIcon size={10} className={overallColor} />
            <span className="text-caption font-medium text-paper">{overallStatus}</span>
          </div>
          <span className="text-caption text-fog">({steps.length})</span>
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
          className="w-1.5 h-1.5 bg-fog rounded-full animate-bounce"
          style={{ animationDelay: `${delay}s` }}
        />
      ))}
    </div>
  );
}

// System Message (e.g., completion status)
function SystemMessage({ message }: { message: any }) {
  const [isCollapsed, setIsCollapsed] = useState(message.isCollapsed ?? true);
  const isCompletion = message.metadata?.type === 'completion';

  if (isCompletion) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex justify-center"
      >
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className="flex items-center gap-2 bg-indigo-dark/30 text-indigo px-4 py-2 rounded-full border border-indigo-light/30 hover:bg-indigo-dark/50 transition-colors text-caption font-medium"
        >
          <Clock size={14} />
          {isCollapsed ? (
            <>
              <span>Finished in {message.content.replace("Finished in ", "")}</span>
              <ChevronDown size={14} />
            </>
          ) : (
            <>
              <span>Finished in {message.content.replace("Finished in ", "")}</span>
              <ChevronRight size={14} />
            </>
          )}
        </button>
      </motion.div>
    );
  }

  return null;
}

// User Message
function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <motion.div
        initial={{ opacity: 0, y: 8, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 500, damping: 30 }}
        className="max-w-[78%] bg-ice/10 text-paper rounded-md px-4 py-2.5 text-body border border-ice/30 shadow-subtle backdrop-blur-sm"
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
  const generatedFiles: GeneratedFile[] = message.generatedFiles ?? [];

  // Extract slide thumbnails from slide_finalized events
  const slideThumbnails = events
    .filter((e): e is SlideFinalizedEvent => e.type === "slide_finalized")
    .map((e) => ({
      slideIndex: e.slideIndex,
      layoutName: "Slide",
    }))
    .sort((a, b) => a.slideIndex - b.slideIndex);

  // Extract sources from tool_result events
  const sources: SourceInfo[] = events
    .filter((e): e is ToolResultEvent => e.type === "tool_result")
    .flatMap((e) => e.sources || []);

  if (!events.length && !message.content && !isComplete) {
    return (
      <div className="flex justify-start">
        <div className="bg-graphite/50 rounded-md px-4 py-2 border border-iron backdrop-blur-sm">
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
              className="text-body text-fog leading-relaxed prose prose-invert max-w-none prose-p:text-fog prose-code:text-paper font-sans"
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
                      <code className="bg-graphite rounded-md px-1.5 py-0.5 text-caption font-mono text-ice" {...props}>
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

        {/* Generated file cards (MS Teams-style preview) */}
        {generatedFiles.length > 0 && (
          <div className="space-y-3">
            {generatedFiles.map((file) => (
              <GeneratedFileCard
                key={file.id}
                file={file}
                slideCount={file.slide_count ?? slideThumbnails.length}
                slideThumbnails={slideThumbnails}
              />
            ))}
          </div>
        )}

        {/* Sources */}
        {sources.length > 0 && <Sources sources={sources} />}
      </div>
    </div>
  );
}

// Empty State 
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center">
      <div className="space-y-3">
        <div className="mx-auto w-10 h-10 rounded-md bg-graphite/50 border border-iron/50 flex items-center justify-center backdrop-blur-sm">
          <Sparkles size={18} className="text-fog" />
        </div>
        <p className="text-body text-fog font-sans">
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

  // Count total events across all messages for scroll triggering
  const totalEventCount = messages.reduce(
    (sum, msg) => sum + (msg.agentEventStream?.length ?? 0),
    0
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, totalEventCount]);

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
        ) : msg.role === "system" ? (
          <SystemMessage key={msg.id} message={msg} />
        ) : (
          <AssistantMessage key={msg.id} message={msg} />
        )
      )}
      <div ref={bottomRef} />
    </div>
  );
}
