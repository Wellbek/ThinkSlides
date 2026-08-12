"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { QuestionCard } from "./QuestionCard";
import { MarkdownText } from "./MarkdownText";
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
    web_search: "Search the web",
    plan_presentation_structure: "Plan presentation structure",
    select_layout: "Select layout",
    build_chart: "Build a chart",
    build_table: "Build a table",
    build_key_metrics: "Build a metric row",
    design_slide_content: "Design slide content",
    validate_slide_json: "Validate slide JSON",
    select_source_image: "Check for a matching source image",
    finalize_and_render_pptx: "Finalize and render PPTX",
    validate_rendered_output: "Validating rendered output with LibreOffice",
    ask_user_question: "Ask the user",
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
  // A pure-Python schema check genuinely finishes inside a millisecond, so
  // "0ms" is honest - it just reads as broken, six times in a row down a
  // group. "<1ms" says the same thing without the eye tripping on it.
  if (ms < 1) return "<1ms";
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
  summary?: string;
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
  /** How many identical-type events this node stands for (1 = not merged). */
  merged: any[];
}
/** A run of consecutive calls to the same tool, shown as one line. */
interface StepGroupNode {
  kind: "group";
  key: string;
  toolName: string;
  steps: StepNode[];
}
type FeedNode = StepNode | ThoughtsNode | EventNode | StepGroupNode;

/** Event types where a run of them in a row says nothing more than one does. */
const MERGEABLE_EVENTS = new Set(["slide_finalized"]);

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
          step.summary = ev.summary;
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
            summary: ev.summary,
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
      case "validation_unresolved":
      case "done":
        flushThoughts();
        nodes.push({ kind: "event", key: `ev-${nodes.length}-${ev.type}`, ev, merged: [ev] });
        break;
      default:
        // rate_limit lands here on purpose. It is persisted like every other
        // event, so a reloaded session still carries every wait it hit - but a
        // wait is state, not history: it is shown live by RetryStrip while it
        // is true, and replaying it afterwards would fill the transcript with
        // the pauses instead of the work.
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
  return collapseRepeats(nodes);
}

/**
 * Fold runs of the same thing into one line.
 *
 * A ten-slide deck produced ten identical "Design slide content" entries and
 * ten "Slide N finalized" entries in a row, which reads as noise and buries
 * the steps that are actually different. A run of the same tool becomes one
 * expandable group; a run of the same mergeable event becomes one entry that
 * says how many. Nothing is dropped - the individual steps are one click away.
 */
function collapseRepeats(nodes: FeedNode[]): FeedNode[] {
  const out: FeedNode[] = [];
  let i = 0;

  while (i < nodes.length) {
    const node = nodes[i];

    if (node.kind === "step") {
      let end = i + 1;
      while (
        end < nodes.length &&
        nodes[end].kind === "step" &&
        (nodes[end] as StepNode).toolName === node.toolName
      ) {
        end++;
      }
      if (end - i > 1) {
        const steps = nodes.slice(i, end) as StepNode[];
        out.push({
          kind: "group",
          key: `grp-${node.key}`,
          toolName: node.toolName,
          steps,
        });
        i = end;
        continue;
      }
    }

    if (node.kind === "event" && MERGEABLE_EVENTS.has(node.ev.type)) {
      let end = i + 1;
      while (
        end < nodes.length &&
        nodes[end].kind === "event" &&
        (nodes[end] as EventNode).ev.type === node.ev.type
      ) {
        end++;
      }
      if (end - i > 1) {
        const merged = (nodes.slice(i, end) as EventNode[]).map((n) => n.ev);
        // The last one is the current state of the thing being reported (the
        // latest retry attempt, the last slide finalized).
        out.push({ ...node, ev: merged[merged.length - 1], merged });
        i = end;
        continue;
      }
    }

    out.push(node);
    i++;
  }

  return out;
}

// ──────────────────────────────────────────────
// ActivityFeed: scroll container + auto-scroll
// ──────────────────────────────────────────────

export function ActivityFeed() {
  const messages = useChatStore((s) => s.activeMessages);
  // Both this and the back-off strip hang off the bottom edge of the panel;
  // the jump pill steps up out of the way while a wait is on screen.
  const retrying = useChatStore((s) => s.activeRetry !== null);

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
        <button
          className={`ws-jump${retrying ? " raised" : ""}`}
          onClick={jumpToBottom}
          title="Jump to latest"
        >
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
  // override the auto behaviour. Steps inside a group count too, so the group
  // holding the live step opens itself.
  const autoKey = useMemo(() => {
    const steps: StepNode[] = [];
    for (const n of nodes) {
      if (n.kind === "step") steps.push(n);
      else if (n.kind === "group") steps.push(...n.steps);
    }
    for (let i = steps.length - 1; i >= 0; i--) {
      if (steps[i].running) return steps[i].key;
    }
    for (let i = steps.length - 1; i >= 0; i--) {
      if (!steps[i].running) return steps[i].key;
    }
    return null;
  }, [nodes]);

  const [manual, setManual] = useState<Record<string, boolean>>({});
  const isExpanded = (key: string) => (key in manual ? manual[key] : key === autoKey);
  const toggle = (key: string, currentlyOpen?: boolean) =>
    setManual((m) => ({ ...m, [key]: !(currentlyOpen ?? isExpanded(key)) }));

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
        if (node.kind === "group") {
          const holdsAuto = node.steps.some((s) => s.key === autoKey);
          return (
            <StepGroup
              key={node.key}
              group={node}
              expanded={node.key in manual ? manual[node.key] : holdsAuto}
              onToggle={() => toggle(node.key, holdsAuto)}
              isExpanded={isExpanded}
              onToggleStep={toggle}
              runLive={isLive}
            />
          );
        }
        if (node.kind === "thoughts") {
          // Trailing thoughts while live → shimmer working block.
          if (node === nodes[nodes.length - 1] && isLive) return null; // rendered via shimmer below
          return <ThoughtsBlock key={node.key} node={node} />;
        }
        return (
          <EventItem
            key={node.key}
            ev={node.ev}
            merged={node.merged}
            isAnswered={isAnswered}
            answerText={answerText}
          />
        );
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
          <div className="ws-shimmer-body">
            <MarkdownText className="ws-md-inline">
              {liveThought ? liveThought.content : "The agent is reasoning through the next step."}
            </MarkdownText>
            <span className="ws-ai-cursor" />
          </div>
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
          {t.content && <MarkdownText className="ws-feed-text">{t.content}</MarkdownText>}
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

/**
 * A run of consecutive calls to the same tool, as one line.
 *
 * Designing ten slides is ten calls to design_slide_content; listing them
 * individually says the same thing ten times. Collapsed, this reports what
 * the run as a whole did; expanded, the individual steps are all still there.
 */
function StepGroup({
  group,
  expanded,
  onToggle,
  isExpanded,
  onToggleStep,
  runLive,
}: {
  group: StepGroupNode;
  expanded: boolean;
  onToggle: () => void;
  isExpanded: (key: string) => boolean;
  onToggleStep: (key: string) => void;
  /** Whether the run as a whole is still going, which the group's own steps
   *  cannot tell it - they can all be finished while the run waits on a
   *  provider back-off with no open step to hang a timer on. */
  runLive: boolean;
}) {
  const running = group.steps.some((s) => s.running);
  const failed = group.steps.filter((s) => s.success === false).length;
  const totalMs = group.steps.reduce((sum, s) => sum + (s.durationMs ?? 0), 0);
  const dotClass = failed ? "ws-dot-error" : running ? "ws-dot-status" : "ws-dot-fact";
  const dot = failed ? "●" : running ? "↺" : "✓";

  // Everything the run cited, gathered onto the one line that represents it -
  // collapsing three searches into a group must not hide the sources they
  // found, which are the whole reason those searches happened.
  const sources = useMemo(() => {
    const byUrl = new Map<string, SourceInfo>();
    for (const step of group.steps) {
      for (const source of step.sources ?? []) {
        if (!byUrl.has(source.url)) byUrl.set(source.url, source);
      }
    }
    return [...byUrl.values()];
  }, [group.steps]);

  return (
    <>
      <FeedItem dot={dot} dotClass={dotClass}>
        <div
          className="ws-step-summary ws-step-group"
          onClick={onToggle}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
        >
          <span className="lbl">{toolLabel(group.toolName)}</span>
          <span className="ws-step-count">×{group.steps.length}</span>
          <span className="ws-step-group-note">
            {running
              ? `${group.steps.filter((s) => !s.running).length} of ${group.steps.length} done`
              : failed
                ? `${failed} failed`
                : runLive
                  // The group's own steps really have all finished, but while
                  // the run continues elsewhere "all complete" reads as a
                  // verdict on the whole run. A completed group sat there
                  // saying it through a 25s rate-limit wait, and the run
                  // looked finished-but-hung. A count is the same fact
                  // without the finality.
                  ? `${group.steps.length} of ${group.steps.length} done`
                  : "all complete"}
          </span>
          <span className="ws-spacer-sm" />
          <span className="ws-step-dur">{fmtDuration(totalMs)}</span>
          {!!sources.length && (
            <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
              <SourcesInline sources={sources} />
            </span>
          )}
          <span className="ws-step-toggle">{expanded ? "▴ collapse" : `▾ show ${group.steps.length} steps`}</span>
        </div>
      </FeedItem>
      {expanded && (
        <div className="ws-step-group-body">
          {group.steps.map((step) => (
            <StepItem
              key={step.key}
              step={step}
              expanded={isExpanded(step.key)}
              onToggle={() => onToggleStep(step.key)}
            />
          ))}
        </div>
      )}
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
          {/* What it did, in words - the collapsed line is where most steps
              are read, so it carries the finding rather than the phase tag. */}
          {step.summary
            ? <span className="ws-step-said one-line">{step.summary}</span>
            : step.phase && <span className="ws-step-phase">{PHASE_LABEL[step.phase] ?? step.phase}</span>}
          <span className="ws-spacer-sm" />
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
          <MarkdownText className="ws-feed-text ws-step-thought" key={`th-${i}`}>
            {t.content}
          </MarkdownText>
        ))}

        {/* What the step actually did, in a sentence. */}
        {step.summary && <div className="ws-step-said">{step.summary}</div>}

        {step.progress.map((p: any, i: number) => (
          <div className="ws-step-progress" key={`pg-${i}`}>
            <div className="ws-feed-text">{p.message}</div>
            <ProgressBar current={p.current} total={p.total} />
          </div>
        ))}

        {/* A failure is never hidden - the error is the point of the step. */}
        {!step.running && step.error && (
          <DetailBlock label="Error" content={String(step.error)} error />
        )}

        {/* Everything the step was given and returned, verbatim, one more
            click away. It is here for when the summary is not enough, not as
            the default way to read the feed. */}
        <RawDetails args={step.args} result={step.running ? undefined : step.result} />

        {/* Cited sources that this step produced (web_search). Sits right
            under the step that fetched them: a compact overlapping-circles
            icon by default, expanding downward to the titled links. */}
        {!!step.sources?.length && <SourcesInline sources={step.sources} />}

        {lastThought?.phase && step.running && (
          <div className="ws-shimmer-body ws-step-live">
            <MarkdownText className="ws-md-inline">
              {lastThought.content || "Working..."}
            </MarkdownText>
            <span className="ws-ai-cursor" />
          </div>
        )}
      </div>
    </FeedItem>
  );
}

/** The step's raw arguments and result, behind one disclosure. */
function RawDetails({ args, result }: { args?: Record<string, unknown>; result?: unknown }) {
  const [open, setOpen] = useState(false);
  const hasArgs = Object.keys(args || {}).length > 0;
  const hasResult = result !== undefined && result !== null && result !== "";
  if (!hasArgs && !hasResult) return null;

  return (
    <div className="ws-raw">
      <button type="button" className="ws-raw-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronDown size={11} className={`ws-raw-chev ${open ? "open" : ""}`} />
        Raw
      </button>
      {open && (
        <>
          {hasArgs && <DetailBlock label="Args" content={stringify(args)} />}
          {hasResult && <DetailBlock label="Result" content={stringify(result)} />}
        </>
      )}
    </div>
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

function EventItem({
  ev,
  merged,
  isAnswered,
  answerText,
}: { ev: any; merged?: any[]; isAnswered?: boolean; answerText?: string }) {
  const repeats = merged && merged.length > 1 ? merged : null;

  switch (ev.type) {
    case "slide_finalized": {
      // A whole deck's worth of these arrive in a row; one line covers them.
      if (repeats) {
        const indices = repeats
          .map((e: any) => (e.slideIndex ?? 0) + 1)
          .sort((a: number, b: number) => a - b);
        const contiguous = indices[indices.length - 1] - indices[0] === indices.length - 1;
        return (
          <FeedItem dot="↺" dotClass="ws-dot-status">
            <div className="ws-feed-header sys">
              <b>{indices.length}</b> slides finalized
              <span className="ts">{fmtTime(ev.timestamp)}</span>
            </div>
            <div className="ws-feed-text">
              {contiguous ? `Slides ${indices[0]}-${indices[indices.length - 1]}` : `Slides ${indices.join(", ")}`}
              {" - previews ready."}
            </div>
          </FeedItem>
        );
      }
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">Slide <b>{(ev.slideIndex ?? 0) + 1}</b> finalized<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.previewUrl ? <code>preview ready</code> : "Layout cloned from template; placeholders filled."}</div>
        </FeedItem>
      );
    }
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
    case "question": {
      // A single pause can carry several questions; events from before that
      // was possible only have the flat pair.
      const questions = Array.isArray(ev.questions) && ev.questions.length
        ? ev.questions
        : [{ question: ev.question, options: ev.options || [] }];
      const label = questions.length > 1 ? `${questions.length} questions for you` : "Agent question";
      return (
        <FeedItem dot={isAnswered ? "✓" : "?"} dotClass={isAnswered ? "ws-dot-success" : "ws-dot-q"}>
          <div className="ws-feed-header"><span className="lbl">{label}</span>{isAnswered ? <span className="ws-qbadge answered">answered</span> : <span className="ws-qbadge">needs your input</span>}<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <QuestionCard questions={questions} context={ev.context} answered={isAnswered} answerText={answerText} />
        </FeedItem>
      );
    }
    case "error":
      return (
        <FeedItem dot="●" dotClass="ws-dot-error">
          <div className="ws-feed-header"><span className="lbl" style={{ color: "#F87171" }}>Error</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.message}{ev.code ? <code> ({ev.code})</code> : null}</div>
        </FeedItem>
      );
    case "validation_unresolved": {
      // The deck ran out of revision attempts with defects still in it. It is
      // still downloadable - withholding it would leave the user with nothing
      // - but it must not read as clean, so the specific defects are listed.
      const issues: any[] = Array.isArray(ev.issues) ? ev.issues : [];
      return (
        <FeedItem dot="●" dotClass="ws-dot-error">
          <div className="ws-feed-header">
            <span className="lbl" style={{ color: "#FBBF24" }}>Known layout issues</span>
            <span className="ts">{fmtTime(ev.timestamp)}</span>
          </div>
          <div className="ws-feed-text">
            The deck is available to download, but after {ev.attempts} revision
            {ev.attempts === 1 ? "" : "s"} it still has {ev.issueCount ?? issues.length} layout
            {(ev.issueCount ?? issues.length) === 1 ? " problem" : " problems"}. Ask the agent
            to simplify the affected slides, or adjust them yourself.
            <ul className="ws-md-issues">
              {issues.slice(0, 6).map((issue: any, i: number) => (
                <li key={i}>
                  {typeof issue.slideIndex === "number" ? `Slide ${issue.slideIndex + 1}: ` : ""}
                  {issue.detail || issue.type}
                </li>
              ))}
            </ul>
          </div>
        </FeedItem>
      );
    }
    case "done":
      if (ev.paused) return null;
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">Done<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          {ev.content && <MarkdownText className="ws-feed-text">{ev.content}</MarkdownText>}
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
