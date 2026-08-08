"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { QuestionCard } from "./QuestionCard";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { ChevronDown, Download, ExternalLink, Globe } from "lucide-react";
import type { AgentEvent, SourceInfo } from "@thinkslides/schemas";

const PHASE_LABEL: Record<string, string> = {
  content_analysis: "Analyzing",
  structure_planning: "Planning structure",
  layout_selection: "Selecting layout",
  content_generation: "Designing content",
  validation: "Validating",
  rendering: "Rendering",
  complete: "Complete",
  user_input: "Awaiting input",
};

function toolLabel(toolName: string): string {
  const map: Record<string, string> = {
    analyze_document: "Analyze document",
    extract_key_data: "Extract key data",
    extract_template_design_system: "Extract template design system",
    plan_presentation_structure: "Plan presentation structure",
    select_layout: "Select layout",
    design_slide_content: "Design slide content",
    validate_slide_json: "Validate slide JSON",
    generate_image_prompt: "Generate image prompt",
    finalize_and_render_pptx: "Finalize and render PPTX",
    ask_user_question: "Ask user",
  };
  return map[toolName] ?? toolName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtTime(ts?: string): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function fmtDuration(ms?: number): string {
  if (ms == null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000);
  const s = Math.round((ms % 60000) / 1000);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

// ──────────────────────────────────────────────
// Step grouping: collapse the flat event stream
// into per-tool "steps" (thoughts → call → result)
// plus standalone event nodes, preserving order.
// ──────────────────────────────────────────────

interface StepNode {
  kind: "step";
  key: string;
  callId: string;
  toolName: string;
  phase?: string;
  thoughts: any[];
  progress: any[];
  args: Record<string, unknown>;
  startTs: string;
  endTs?: string;
  result?: unknown;
  success?: boolean;
  error?: string;
  durationMs?: number;
  tokenCount?: number;
  running: boolean;
  sources?: SourceInfo[];
}
interface ThoughtsNode {
  kind: "thoughts";
  key: string;
  thoughts: any[];
  progress: any[];
}
interface EventNode {
  kind: "event";
  key: string;
  ev: any;
}
type FeedNode = StepNode | ThoughtsNode | EventNode;

function buildNodes(events: AgentEvent[]): FeedNode[] {
  const nodes: FeedNode[] = [];
  let pendingThoughts: any[] = [];
  let pendingProgress: any[] = [];
  let stepCounter = 0;
  const openSteps = new Map<string, StepNode>();

  const flushThoughts = () => {
    if (pendingThoughts.length || pendingProgress.length) {
      nodes.push({
        kind: "thoughts",
        key: `th-${nodes.length}`,
        thoughts: pendingThoughts,
        progress: pendingProgress,
      });
      pendingThoughts = [];
      pendingProgress = [];
    }
  };

  for (const ev of events as any[]) {
    switch (ev.type) {
      case "thought":
        pendingThoughts.push(ev);
        break;
      case "progress":
        pendingProgress.push(ev);
        break;
      case "tool_call": {
        stepCounter++;
        const step: StepNode = {
          kind: "step",
          key: `step-${stepCounter}-${ev.callId}`,
          callId: ev.callId,
          toolName: ev.toolName,
          phase: ev.phase,
          thoughts: pendingThoughts,
          progress: pendingProgress,
          args: ev.toolArgs || {},
          startTs: ev.timestamp,
          running: true,
        };
        pendingThoughts = [];
        pendingProgress = [];
        nodes.push(step);
        openSteps.set(ev.callId, step);
        break;
      }
      case "tool_result": {
        const step = openSteps.get(ev.callId);
        if (step) {
          step.running = false;
          step.endTs = ev.timestamp;
          step.result = ev.result;
          step.success = ev.success;
          step.error = ev.error;
          step.durationMs = ev.durationMs;
          step.tokenCount = ev.tokenCount;
          step.sources = Array.isArray(ev.sources) && ev.sources.length ? ev.sources : undefined;
          openSteps.delete(ev.callId);
        } else {
          // Orphan result (e.g. a reloaded session where the call event was
          // not persisted): synthesize a completed step so the detail still
          // renders.
          stepCounter++;
          nodes.push({
            kind: "step",
            key: `step-${stepCounter}-orphan-${ev.callId}`,
            callId: ev.callId,
            toolName: ev.toolName,
            phase: ev.phase,
            thoughts: [],
            progress: [],
            args: {},
            startTs: ev.timestamp,
            endTs: ev.timestamp,
            result: ev.result,
            success: ev.success,
            error: ev.error,
            durationMs: ev.durationMs,
            tokenCount: ev.tokenCount,
            running: false,
            sources: Array.isArray(ev.sources) && ev.sources.length ? ev.sources : undefined,
          });
        }
        break;
      }
      case "slide_finalized":
      case "file_generated":
      case "question":
      case "error":
      case "done":
        flushThoughts();
        nodes.push({ kind: "event", key: `ev-${nodes.length}-${ev.type}`, ev });
        break;
      default:
        break;
    }
  }

  if (pendingThoughts.length || pendingProgress.length) {
    nodes.push({
      kind: "thoughts",
      key: `th-trailing`,
      thoughts: pendingThoughts,
      progress: pendingProgress,
    });
  }
  return nodes;
}

// ──────────────────────────────────────────────
// ActivityFeed: scroll container + auto-scroll
// ──────────────────────────────────────────────

export function ActivityFeed() {
  const messages = useChatStore((s) => s.activeMessages);

  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  // Signature that changes whenever the visible content grows.
  const signature = useMemo(() => {
    let events = 0;
    for (const m of messages) events += m.agentEventStream?.length ?? 0;
    const last = messages[messages.length - 1];
    return `${messages.length}:${events}:${last?.id ?? ""}`;
  }, [messages]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (atBottomRef.current) {
      el.scrollTop = el.scrollHeight;
      setShowJump(false);
    } else {
      setShowJump(true);
    }
  }, [signature]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atBottomRef.current = atBottom;
    if (atBottom) setShowJump(false);
  };

  const jumpToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    atBottomRef.current = true;
    setShowJump(false);
  };

  return (
    <div className="ws-feed-wrap">
      <div className="ws-feed" ref={scrollRef} onScroll={onScroll}>
        {messages.length === 0 && (
          <div className="ws-empty-note">Send a message to start the agent workflow.</div>
        )}
        {messages.map((m, idx) => {
          // Check if the next message is a user message (meaning this assistant message's questions are answered)
          const isAnswered = idx < messages.length - 1 && messages[idx + 1].role === 'user';
          const userAnswer = isAnswered && messages[idx + 1].role === 'user' ? messages[idx + 1].content : undefined;

          return m.role === "user" ? (
            <FeedItem key={m.id} dot={<span className="ws-av indigo">WK</span>} dotClass="ws-dot-user">
              <div className="ws-feed-header"><span className="lbl">You</span><span className="ts">{fmtTime(m.created_at)}</span></div>
              <div className="ws-feed-text">{m.content}</div>
            </FeedItem>
          ) : (
            <AssistantBlock key={m.id} message={m} isAnswered={isAnswered} answerText={userAnswer} />
          );
        })}
      </div>

      {showJump && (
        <button className="ws-jump" onClick={jumpToBottom} title="Jump to latest">
          <span>New messages</span>
          <ChevronDown size={14} />
          <span className="ws-jump-arrow" />
        </button>
      )}
    </div>
  );
}

function AssistantBlock({ message, isAnswered, answerText }: { message: any; isAnswered?: boolean; answerText?: string }) {
  const events: AgentEvent[] = useMemo(
    () => message.agentEventStream || [],
    [message.agentEventStream]
  );
  const isLive = message.isComplete === false;

  const nodes = useMemo(() => buildNodes(events), [events]);

  // Which step is auto-expanded: a running step wins, else the most recent
  // completed step. Everything else auto-collapses to a one-line summary so
  // the feed keeps only the latest step in full detail. Manual toggles
  // override the auto behaviour.
  const autoKey = useMemo(() => {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (n.kind === "step" && n.running) return n.key;
    }
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (n.kind === "step" && !n.running) return n.key;
    }
    return null;
  }, [nodes]);

  const [manual, setManual] = useState<Record<string, boolean>>({});
  const isExpanded = (key: string) => (key in manual ? manual[key] : key === autoKey);
  const toggle = (key: string) => setManual((m) => ({ ...m, [key]: !isExpanded(key) }));

  // Live "working" shimmer: shown when the agent is live but there is no open
  // tool step to render the timer against (it is still reasoning).
  const showLiveShimmer =
    isLive && (nodes.length === 0 || nodes[nodes.length - 1].kind !== "step" || (nodes[nodes.length - 1] as StepNode).running === false);
  const liveThought = useMemo(() => {
    const last = nodes[nodes.length - 1];
    if (last && last.kind === "thoughts") {
      return [...last.thoughts].reverse()[0];
    }
    return null;
  }, [nodes]);

  return (
    <>
      {nodes.map((node) => {
        if (node.kind === "step") {
          return (
            <StepItem
              key={node.key}
              step={node}
              expanded={isExpanded(node.key)}
              onToggle={() => toggle(node.key)}
            />
          );
        }
        if (node.kind === "thoughts") {
          // Trailing thoughts while live → shimmer working block.
          if (node === nodes[nodes.length - 1] && isLive) return null; // rendered via shimmer below
          return <ThoughtsBlock key={node.key} node={node} />;
        }
        return <EventItem key={node.key} ev={node.ev} isAnswered={isAnswered} answerText={answerText} />;
      })}

      {showLiveShimmer && (
        <FeedItem
          dot={
            <span className="ws-ai-dot">
              <svg viewBox="0 0 10 10" aria-hidden="true">
                <polygon points="5,0.5 6.5,3.8 10,4.2 7.5,6.5 8.1,9.8 5,8.2 1.9,9.8 2.5,6.5 0,4.2 3.5,3.8" fill="#818CF8" />
              </svg>
            </span>
          }
        >
          <div className="ws-feed-header">
            <span className="ws-shimmer-headline">
              {liveThought ? PHASE_LABEL[liveThought.phase] ?? "Working" : "Working..."}
            </span>
          </div>
          <span className="ws-shimmer-body">
            {liveThought ? liveThought.content : "The agent is reasoning through the next step."}
            <span className="ws-ai-cursor" />
          </span>
        </FeedItem>
      )}
    </>
  );
}

function ThoughtsBlock({ node }: { node: ThoughtsNode }) {
  return (
    <>
      {node.thoughts.map((t: any, i: number) => (
        <FeedItem key={`th-${i}`} dot="◆" dotClass="ws-dot-fact">
          <div className="ws-feed-header"><span className="lbl">{PHASE_LABEL[t.phase] ?? "Thinking"}</span><span className="ts">{fmtTime(t.timestamp)}</span></div>
          {t.content && <div className="ws-feed-text">{t.content}</div>}
        </FeedItem>
      ))}
      {node.progress.map((p: any, i: number) => (
        <FeedItem key={`pg-${i}`} dot="◆" dotClass="ws-dot-fact">
          <div className="ws-feed-header"><span className="lbl">Progress</span><span className="ts">{fmtTime(p.timestamp)}</span></div>
          <div className="ws-feed-text">{p.message}</div>
          <ProgressBar current={p.current} total={p.total} />
        </FeedItem>
      ))}
    </>
  );
}

function StepItem({ step, expanded, onToggle }: { step: StepNode; expanded: boolean; onToggle: () => void }) {
  const failed = step.success === false;
  const ok = step.success === true;
  const dotClass = failed ? "ws-dot-error" : ok ? "ws-dot-fact" : "ws-dot-status";
  const dot = failed ? "●" : ok ? "✓" : "↺";

  if (!expanded) {
    return (
      <FeedItem dot={dot} dotClass={dotClass}>
        <div className="ws-step-summary" onClick={onToggle} role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}>
          <span className="lbl">{toolLabel(step.toolName)}</span>
          {step.phase && <span className="ws-step-phase">{PHASE_LABEL[step.phase] ?? step.phase}</span>}
          <StepTimer startTs={step.startTs} endTs={step.endTs} durationMs={step.durationMs} running={step.running} />
          {/* Sources stay visible and independently expandable even while the
              step itself is collapsed - previously this was a static icon
              with no way to open it, so cited sources effectively vanished
              the moment the next step became the auto-expanded one. */}
          {!!step.sources?.length && (
            <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
              <SourcesInline sources={step.sources} />
            </span>
          )}
          <span className="ws-step-toggle">▾ expand</span>
        </div>
      </FeedItem>
    );
  }

  const lastThought = [...step.thoughts].reverse()[0];
  return (
    <FeedItem dot={dot} dotClass={dotClass}>
      <div className="ws-step">
        <div className="ws-feed-header ws-step-head" onClick={onToggle} role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}>
          <span className="lbl">{toolLabel(step.toolName)}</span>
          {step.phase && <span className="ws-step-phase">{PHASE_LABEL[step.phase] ?? step.phase}</span>}
          <span className="ws-spacer-sm" />
          <StepTimer startTs={step.startTs} endTs={step.endTs} durationMs={step.durationMs} running={step.running} />
          <span className={`ws-step-mark ${failed ? "bad" : ok ? "ok" : "run"}`}>
            {step.running ? "running" : failed ? "failed" : "complete"}
          </span>
          <span className="ws-step-toggle">▴ collapse</span>
        </div>

        {/* Reasoning - full detail, no truncation. */}
        {step.thoughts.map((t: any, i: number) => (
          <div className="ws-feed-text ws-step-thought" key={`th-${i}`}>
            {t.content}
          </div>
        ))}

        {Object.keys(step.args || {}).length > 0 && (
          <DetailBlock label="Args" content={stringify(step.args)} />
        )}

        {step.progress.map((p: any, i: number) => (
          <div className="ws-step-progress" key={`pg-${i}`}>
            <div className="ws-feed-text">{p.message}</div>
            <ProgressBar current={p.current} total={p.total} />
          </div>
        ))}

        {/* Result - full detail; only clamped when extremely long. */}
        {!step.running && (
          step.error ? (
            <DetailBlock label="Error" content={String(step.error)} error />
          ) : (
            <DetailBlock label="Result" content={stringify(step.result)} />
          )
        )}

        {/* Cited sources that this step produced (web_search). Sits right
            under the step that fetched them: a compact overlapping-circles
            icon by default, expanding downward to the titled links. */}
        {!!step.sources?.length && <SourcesInline sources={step.sources} />}

        {lastThought?.phase && step.running && (
          <span className="ws-shimmer-body ws-step-live">
            {lastThought.content || "Working..."}
            <span className="ws-ai-cursor" />
          </span>
        )}
      </div>
    </FeedItem>
  );
}

/** Two overlapping circles - the compact glyph for a cited-sources cluster. */
function OverlappingCirclesIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} aria-hidden="true">
      <circle cx="9" cy="12" r="6" />
      <circle cx="15" cy="12" r="6" />
    </svg>
  );
}

function faviconFor(url: string): string | null {
  try {
    const domain = new URL(url).hostname;
    return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`;
  } catch {
    return null;
  }
}

/**
 * Inline cited-sources cluster, rendered under the step that produced them.
 * Collapsed by default to just the overlapping-circles icon (with a count);
 * click expands downward to reveal each source's favicon + title as a link.
 */
function SourcesInline({ sources }: { sources: SourceInfo[] }) {
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;
  return (
    <div className="ws-src-inline">
      <button
        type="button"
        className="ws-src-inline-btn"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={`${sources.length} cited source${sources.length === 1 ? "" : "s"}`}
      >
        <OverlappingCirclesIcon size={17} />
        <span className="ws-src-inline-count">{sources.length}</span>
        <ChevronDown size={12} className={`ws-src-inline-chev ${open ? "open" : ""}`} />
      </button>
      {open && (
        <div className="ws-src-inline-list">
          {sources.map((s, i) => (
            <SourceLink key={`${s.url}-${i}`} source={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function SourceLink({ source }: { source: SourceInfo }) {
  const [imgFailed, setImgFailed] = useState(false);
  const favicon = faviconFor(source.url);
  return (
    <a
      className="ws-src-inline-item"
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      title={source.url}
    >
      {favicon && !imgFailed ? (
        <img
          src={favicon}
          alt=""
          className="ws-src-inline-fav"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <Globe size={13} className="ws-src-inline-fav fallback" />
      )}
      <span className="ws-src-inline-title">{source.title}</span>
      <ExternalLink size={11} className="ws-src-inline-ext" />
    </a>
  );
}

/** Timer that ticks while a step is running, freezing on its duration once done. */
function StepTimer({ startTs, endTs, durationMs, running }: {
  startTs: string; endTs?: string; durationMs?: number; running: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [running]);

  if (running && startTs) {
    const elapsed = now - new Date(startTs).getTime();
    return <span className="ws-step-dur live" title={`started ${fmtTime(startTs)}`}>{fmtDuration(elapsed)}</span>;
  }
  return <span className="ws-step-dur" title={endTs ? `finished ${fmtTime(endTs)}` : undefined}>{fmtDuration(durationMs)}</span>;
}

/** Full-detail block; only clamps output that is truly excessive. */
function DetailBlock({ label, content, error }: { label: string; content: string; error?: boolean }) {
  const CLAMP = 4000;
  const [open, setOpen] = useState(false);
  if (!content) return null;
  const long = content.length > CLAMP;
  const shown = long && !open ? content.slice(0, CLAMP) : content;
  return (
    <div className="ws-step-detail">
      <div className="ws-step-detail-head" onClick={() => long && setOpen((o) => !o)} role={long ? "button" : undefined} tabIndex={long ? 0 : undefined}>
        <span className="k">{label}</span>
        {long && (
          <span className="ws-step-more">
            {open ? "show less" : `show ${content.length - CLAMP} more chars`}
          </span>
        )}
      </div>
      <pre className={`ws-step-pre ${error ? "bad" : ""}`}>{shown}</pre>
    </div>
  );
}

function ProgressBar({ current, total }: { current?: number; total?: number }) {
  return (
    <div className="ws-ai-progress">
      <div className="track"><div className="fill" style={{ width: `${pct(current, total)}%` }} /></div>
      <div className="meta"><span>{current ?? 0} / {total ?? 0}</span><span><b>{pct(current, total)}%</b></span></div>
    </div>
  );
}

function EventItem({ ev, isAnswered, answerText }: { ev: any; isAnswered?: boolean; answerText?: string }) {
  switch (ev.type) {
    case "slide_finalized":
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">Slide <b>{(ev.slideIndex ?? 0) + 1}</b> finalized<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.previewUrl ? <code>preview ready</code> : "Layout cloned from template; placeholders filled."}</div>
        </FeedItem>
      );
    case "file_generated": {
      const url = buildDownloadUrl(`/api/orchestrator/files/${ev.fileId}`);
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">File generated - <b>{ev.fileName}</b><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">
            {ev.slideCount ? `${ev.slideCount} slides` : "ready to download"}
            {ev.fileSizeBytes ? ` - ${formatFileSize(ev.fileSizeBytes)}` : ""}
          </div>
          <a className="ws-tb-btn primary ws-file-dl" href={url} download>
            <Download size={13} /> Download
          </a>
        </FeedItem>
      );
    }
    case "question":
      return (
        <FeedItem dot={isAnswered ? "✓" : "?"} dotClass={isAnswered ? "ws-dot-success" : "ws-dot-q"}>
          <div className="ws-feed-header"><span className="lbl">Agent question</span>{isAnswered ? <span className="ws-qbadge answered">answered</span> : <span className="ws-qbadge">needs your input</span>}<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <QuestionCard question={ev.question} options={ev.options} context={ev.context} answered={isAnswered} answerText={answerText} />
        </FeedItem>
      );
    case "error":
      return (
        <FeedItem dot="●" dotClass="ws-dot-error">
          <div className="ws-feed-header"><span className="lbl" style={{ color: "#F87171" }}>Error</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.message}{ev.code ? <code> ({ev.code})</code> : null}</div>
        </FeedItem>
      );
    case "done":
      if (ev.paused) return null;
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">Done<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          {ev.content && <div className="ws-feed-text">{ev.content}</div>}
        </FeedItem>
      );
    default:
      return null;
  }
}

function FeedItem({
  dot,
  dotClass,
  children,
}: {
  dot: React.ReactNode;
  dotClass?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="ws-feed-item">
      <div className={`ws-feed-dot ${dotClass ?? ""}`}>{dot}</div>
      <div className="ws-feed-body">{children}</div>
    </div>
  );
}

function pct(c?: number, t?: number): number {
  if (!t || t <= 0) return 0;
  return Math.min(100, Math.round(((c ?? 0) / t) * 100));
}

function stringify(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
