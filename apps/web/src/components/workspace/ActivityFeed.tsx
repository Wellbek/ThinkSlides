"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat-store";
import { QuestionCard } from "./QuestionCard";
import { MarkdownText } from "./MarkdownText";
import { buildDownloadUrl, formatFileSize } from "@/lib/download";
import { ChevronDown, Download, ExternalLink, Globe } from "lucide-react";
import type { AgentEvent, SourceInfo } from "@thinkslides/schemas";

// ──────────────────────────────────────────────
// The phase timeline groups the flat event stream into the five things a
// deck actually goes through - Understand, Research, Design, Draft, Render &
// check - rather than the ten-odd pipeline steps underneath, several of which
// (a clarifying question and the brief that prompted it; a render and the
// check that follows it) only make sense read together.
//
// `step` is the fine pipeline phase name the backend now carries on every
// event (see loop.py's `_event`); `phase` is the coarser label several steps
// share and is kept only as a fallback for sessions recorded before `step`
// existed.
// ──────────────────────────────────────────────

type PhaseGroupKey = "understand" | "research" | "design" | "draft" | "render";

const GROUP_LABEL: Record<PhaseGroupKey, string> = {
  understand: "Understand",
  research: "Research",
  design: "Design",
  draft: "Draft",
  render: "Render & check",
};

/** The pipeline's steps in the order a run walks them, mirroring PIPELINE in
 *  services/ai/app/agent/pipeline.py, each with the timeline box it belongs
 *  to. Several steps share a box, and the ORDER is what tells a revisit from
 *  ordinary progress: `design` (pick a direction) and `plan` (outline the
 *  deck) both live in the Design box but sit either side of `research`, so
 *  the Design box legitimately appears twice in a run that never went
 *  backwards. Treating a repeated box as a revisit labelled every such run
 *  "fixing — the design" when nothing had gone wrong. */
const PIPELINE_STEPS: { step: string; group: PhaseGroupKey }[] = [
  { step: "intake", group: "understand" },
  { step: "scout", group: "research" },
  { step: "consult", group: "understand" },
  { step: "design", group: "design" },
  { step: "research", group: "research" },
  { step: "plan", group: "design" },
  { step: "review", group: "design" },
  { step: "draft", group: "draft" },
  { step: "render", group: "render" },
  { step: "validate", group: "render" },
];

const STEP_TO_GROUP: Record<string, PhaseGroupKey> = Object.fromEntries(
  PIPELINE_STEPS.map(({ step, group }) => [step, group])
);

/** How far through the pipeline each step is. A block whose step ranks
 *  earlier than the furthest the run has reached is the run going back. */
const STEP_ORDER: Record<string, number> = Object.fromEntries(
  PIPELINE_STEPS.map(({ step }, i) => [step, i])
);

/** The two groups a validation-retry loop alternates between - see
 *  `currentBlock`'s reuse logic below. No other group pair loops this
 *  way, so this stays deliberately narrow rather than a general rule. */
const RETRY_PAIR: Partial<Record<PhaseGroupKey, PhaseGroupKey>> = {
  render: "draft",
  draft: "render",
};

/** Coarse `phase` → best-guess fine step, for events recorded before `step`
 *  existed. Degrades gracefully: Understand and Research fold into one box
 *  rather than crashing on an unrecognised value. */
const PHASE_FALLBACK_STEP: Record<string, string> = {
  content_analysis: "intake",
  user_input: "consult",
  structure_planning: "plan",
  layout_selection: "design",
  content_generation: "draft",
  validation: "validate",
  rendering: "render",
  complete: "complete",
};

function stepOf(ev: any): string {
  return ev.step || PHASE_FALLBACK_STEP[ev.phase] || "intake";
}

/** null for a step that does not belong on the timeline (the closing word). */
function groupOf(ev: any): PhaseGroupKey | null {
  const step = stepOf(ev);
  if (step === "complete") return null;
  return STEP_TO_GROUP[step] ?? null;
}

function toolLabel(toolName: string): string {
  const map: Record<string, string> = {
    analyze_document: "Analyze document",
    extract_key_data: "Extract key data",
    extract_template_design_system: "Extract template design system",
    web_search: "Search the web",
    propose_design_directions: "Offer design directions",
    plan_presentation_structure: "Plan presentation structure",
    select_layout: "Select layout",
    build_chart: "Build a chart",
    build_table: "Build a table",
    build_key_metrics: "Build a metric row",
    build_stat_callouts: "Build stat callouts",
    build_icon_cards: "Build icon cards",
    build_accent_cards: "Build accent cards",
    build_row_bands: "Build row bands",
    build_banner: "Build a banner",
    build_legend: "Build a legend",
    build_image_card_row: "Build an image card row",
    draw_primitives: "Place marks on the slide",
    design_slide_content: "Design slide content",
    validate_slide_json: "Validate slide JSON",
    select_source_image: "Check for a matching source image",
    finalize_and_render_pptx: "Finalize and render PPTX",
    validate_rendered_output: "Validating rendered output with LibreOffice",
    ask_user_question: "Ask the user",
  };
  return map[toolName] ?? toolName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function fmtTime(ts?: string): string {
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
// Building phase blocks: the flat event stream, folded into one block per
// run of consecutive events that share a phase group. A group reappearing
// later - Draft, after Render & check already ran - starts a new block
// rather than merging into the old one, which is what makes a revisit
// visible instead of silently rewriting history.
// ──────────────────────────────────────────────

interface SubStep {
  key: string;
  callId: string;
  toolName: string;
  args: Record<string, unknown>;
  result?: unknown;
  summary?: string;
  success?: boolean;
  error?: string;
  startTs: string;
  endTs?: string;
  /** Raw tool-execution time, as reported by the backend - excludes the
   *  model's reasoning/decision time that preceded the call. Kept around
   *  as a secondary value once `processDurationMs` takes over as the
   *  headline duration. */
  durationMs?: number;
  /** This step's full process span: from the moment the previous step in
   *  this phase finished (or the phase itself started, for the first
   *  step) to this step's own completion. Reasoning time between calls
   *  lands on the step it precedes instead of going unaccounted for, so
   *  step durations sum to the phase's wall-clock total by construction. */
  processDurationMs?: number;
  running: boolean;
  isRevision?: boolean;
  revisionReason?: string;
  revisitKind?: "error" | "adjustment";
  revisitAttempt?: number;
  revisitMaxAttempts?: number;
  slideIndex?: number;
  sources?: SourceInfo[];
}

interface PhaseBlock {
  key: string;
  group: PhaseGroupKey;
  revisit: boolean;
  startTs: string;
  endTs: string;
  subSteps: SubStep[];
  thoughts: { content: string; timestamp: string }[];
  progress: any[];
  slideFinalized: any[];
  questionEvent?: any;
  validationUnresolvedEvent?: any;
  errorEvent?: any;
  fileGeneratedEvent?: any;
}

function buildPhaseBlocks(events: AgentEvent[]): { blocks: PhaseBlock[]; closingText: string } {
  const blocks: PhaseBlock[] = [];
  // How far through the pipeline this run has got. A step that ranks behind
  // it is the run doubling back - the only thing that counts as a revisit.
  let furthestStep = -1;
  const openSubSteps = new Map<string, SubStep>();
  // Per-block cursor for `processDurationMs`: seeded at the block's own
  // start, then advanced to each step's own end as steps complete - so the
  // next step's process span picks up exactly where the last one left off.
  const cursorByBlock = new Map<string, string>();
  // A retry loop's Render block and its Fixing block are visually two
  // blocks, but they're one continuous timeline - the fix happens in the
  // gap between two validate attempts. Once the first fix block is
  // created right after its Render block, they share ONE cursor entry
  // (keyed by the Render block's key) instead of two independent ones, or
  // the fix cycle's wall time gets counted once on each side and the
  // phase's substeps would sum to more than its own wall-clock total.
  const cursorGroupKey = new Map<string, string>();
  const resolveCursorKey = (key: string): string => cursorGroupKey.get(key) ?? key;
  let closingText = "";
  let blockCounter = 0;

  const currentBlock = (group: PhaseGroupKey, step: string, ts: string): PhaseBlock => {
    const last = blocks[blocks.length - 1];
    if (last && last.group === group) {
      last.endTs = ts;
      return last;
    }
    // A validate/fix retry loop alternates strictly between "render" and
    // "draft" - reuse the most recent block of this group instead of
    // starting a new one each time it comes back around, so N attempts
    // read as one "Validate" block and one "Fixing" block, each with an
    // attempt checklist, rather than N near-duplicate blocks. A fix block
    // never absorbs the original (non-revisit) draft pass - only a prior
    // fix attempt - so "Draft" and "Fixing Slide 2" stay visually distinct.
    const pairGroup = RETRY_PAIR[group];
    if (pairGroup) {
      for (let i = blocks.length - 1; i >= 0; i--) {
        const b = blocks[i];
        if (b.group === group) {
          if (group === "draft" && !b.revisit) break;
          b.endTs = ts;
          return b;
        }
        if (b.group !== pairGroup) break;
      }
    }
    blockCounter++;
    const rank = STEP_ORDER[step];
    const revisit = rank !== undefined && rank < furthestStep;
    if (rank !== undefined) furthestStep = Math.max(furthestStep, rank);
    const block: PhaseBlock = {
      key: `phase-${blockCounter}-${group}`,
      group,
      revisit,
      startTs: ts,
      endTs: ts,
      subSteps: [],
      thoughts: [],
      progress: [],
      slideFinalized: [],
    };
    blocks.push(block);
    if (group === "draft" && revisit && last?.group === "render") {
      // This fix block continues the render block's timeline rather than
      // starting its own - see cursorGroupKey above.
      cursorGroupKey.set(block.key, resolveCursorKey(last.key));
    } else {
      cursorByBlock.set(block.key, ts);
    }
    return block;
  };

  for (const ev of events as any[]) {
    const step = stepOf(ev);
    if (step === "complete") {
      if (ev.type === "thought" && ev.content) closingText = ev.content;
      continue;
    }
    const group = groupOf(ev);
    if (!group) continue;
    const block = currentBlock(group, step, ev.timestamp || blocks[blocks.length - 1]?.endTs || "");

    switch (ev.type) {
      case "thought":
        if (ev.content) block.thoughts.push({ content: ev.content, timestamp: ev.timestamp });
        break;
      case "progress":
        block.progress.push(ev);
        break;
      case "tool_call": {
        const sub: SubStep = {
          key: `sub-${block.key}-${ev.callId}`,
          callId: ev.callId,
          toolName: ev.toolName,
          args: ev.toolArgs || {},
          startTs: ev.timestamp,
          running: true,
          isRevision: ev.isRevision,
          revisionReason: ev.revisionReason,
          revisitKind: ev.revisitKind,
          revisitAttempt: ev.revisitAttempt,
          revisitMaxAttempts: ev.revisitMaxAttempts,
          slideIndex: typeof ev.toolArgs?.slide_index === "number" ? ev.toolArgs.slide_index : undefined,
        };
        block.subSteps.push(sub);
        openSubSteps.set(ev.callId, sub);
        break;
      }
      case "tool_result": {
        const cursorKey = resolveCursorKey(block.key);
        const cursor = cursorByBlock.get(cursorKey) ?? block.startTs;
        const sub = openSubSteps.get(ev.callId);
        if (sub) {
          sub.running = false;
          sub.endTs = ev.timestamp;
          sub.result = ev.result;
          sub.summary = ev.summary;
          sub.success = ev.success;
          sub.error = ev.error;
          sub.durationMs = ev.durationMs;
          sub.processDurationMs = new Date(ev.timestamp).getTime() - new Date(cursor).getTime();
          sub.sources = Array.isArray(ev.sources) && ev.sources.length ? ev.sources : undefined;
          openSubSteps.delete(ev.callId);
        } else {
          block.subSteps.push({
            key: `sub-${block.key}-orphan-${ev.callId}`,
            callId: ev.callId,
            toolName: ev.toolName,
            args: {},
            startTs: ev.timestamp,
            endTs: ev.timestamp,
            result: ev.result,
            summary: ev.summary,
            success: ev.success,
            error: ev.error,
            durationMs: ev.durationMs,
            processDurationMs: new Date(ev.timestamp).getTime() - new Date(cursor).getTime(),
            running: false,
            sources: Array.isArray(ev.sources) && ev.sources.length ? ev.sources : undefined,
          });
        }
        cursorByBlock.set(cursorKey, ev.timestamp);
        break;
      }
      case "slide_finalized":
        block.slideFinalized.push(ev);
        break;
      case "question":
        block.questionEvent = ev;
        break;
      case "validation_unresolved":
        block.validationUnresolvedEvent = ev;
        break;
      case "error":
        block.errorEvent = ev;
        break;
      case "file_generated":
        block.fileGeneratedEvent = ev;
        break;
      default:
        break;
    }
  }

  return { blocks, closingText };
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

  // A fresh question is worth interrupting a scrolled-up read for: staying
  // where you scrolled is right for a passing thought, wrong for something
  // that is now waiting on you and easy to miss below the fold.
  const latestQuestionId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const stream = messages[i].agentEventStream ?? [];
      for (let j = stream.length - 1; j >= 0; j--) {
        if (stream[j]?.type === "question") return `${messages[i].id}:${j}`;
      }
    }
    return null;
  }, [messages]);
  const seenQuestionRef = useRef<string | null>(null);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const isNewQuestion = latestQuestionId !== null && latestQuestionId !== seenQuestionRef.current;
    seenQuestionRef.current = latestQuestionId;
    if (atBottomRef.current || isNewQuestion) {
      el.scrollTop = el.scrollHeight;
      atBottomRef.current = true;
      setShowJump(false);
    } else {
      setShowJump(true);
    }
  }, [signature, latestQuestionId]);

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

          if (m.role === "user") {
            return (
              <FeedItem key={m.id} dot={<span className="ws-av indigo">WK</span>} dotClass="ws-dot-user">
                <div className="ws-feed-header"><span className="lbl">You</span><span className="ts">{fmtTime(m.created_at)}</span></div>
                <div className="ws-feed-text">{m.content}</div>
              </FeedItem>
            );
          }
          if (m.role === "system") {
            return <div key={m.id} className="ws-run-complete">{m.content}</div>;
          }
          return (
            <AssistantTimeline key={m.id} message={m} isAnswered={isAnswered} answerText={userAnswer} />
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

function AssistantTimeline({ message, isAnswered, answerText }: { message: any; isAnswered?: boolean; answerText?: string }) {
  const events: AgentEvent[] = useMemo(
    () => message.agentEventStream || [],
    [message.agentEventStream]
  );
  const isLive = message.isComplete === false;

  const { blocks, closingText } = useMemo(() => buildPhaseBlocks(events), [events]);

  const lastBlock = blocks[blocks.length - 1];
  // A block that ended on a question is not "running" any more - the run
  // genuinely stopped there to wait on a human, so nothing downstream of it
  // should read as still in flight.
  const activeIndex = isLive && blocks.length && !lastBlock.questionEvent ? blocks.length - 1 : -1;

  // Nothing has happened yet: the very first event has not arrived.
  const showStartShimmer = isLive && blocks.length === 0;

  const finalText = !isLive ? message.content : (message.content || (activeIndex === -1 ? closingText : ""));

  return (
    <>
      {blocks.map((block, i) => (
        <PhaseBlockItem
          key={block.key}
          block={block}
          active={i === activeIndex}
          isAnswered={isAnswered}
          answerText={answerText}
        />
      ))}

      {showStartShimmer && (
        <FeedItem
          dot={
            <span className="ws-ai-dot">
              <svg viewBox="0 0 10 10" aria-hidden="true">
                <polygon points="5,0.5 6.5,3.8 10,4.2 7.5,6.5 8.1,9.8 5,8.2 1.9,9.8 2.5,6.5 0,4.2 3.5,3.8" fill="#818CF8" />
              </svg>
            </span>
          }
        >
          <div className="ws-feed-header"><span className="ws-shimmer-headline">Starting</span></div>
          <div className="ws-shimmer-body">
            Reading the request
            <span className="ws-ai-cursor" />
          </div>
        </FeedItem>
      )}

      {finalText && (
        <FeedItem dot="◆" dotClass="ws-dot-fact">
          <div className="ws-feed-header"><span className="lbl">Assistant</span><span className="ts">{fmtTime(message.created_at)}</span></div>
          <MarkdownText className="ws-feed-text">{finalText}</MarkdownText>
        </FeedItem>
      )}
    </>
  );
}

/** One phase group's box on the timeline: dot, name, takeaway, boxed
 *  substeps, and whatever it paused or failed on. */
function PhaseBlockItem({
  block,
  active,
  isAnswered,
  answerText,
}: {
  block: PhaseBlock;
  active: boolean;
  isAnswered?: boolean;
  answerText?: string;
}) {
  const failed = !!block.errorEvent;
  const icon = failed ? "●" : active ? "↻" : block.revisit ? "↺" : "✓";
  const dotClass = failed ? "ws-dot-error" : active ? "ws-dot-status" : block.revisit ? "ws-dot-revisit" : "ws-dot-fact";

  const lastThought = block.thoughts[block.thoughts.length - 1];
  const lastStep = block.subSteps[block.subSteps.length - 1];
  const takeawayText = lastThought?.content || (!active ? lastStep?.summary || "" : "");

  const rows = useMemo(() => {
    if (block.group === "draft") return groupDraftRows(block.subSteps);
    if (block.group === "render") return groupRenderRows(block.subSteps);
    return block.subSteps.map(toRow);
  }, [block.subSteps, block.group]);

  const rawExtra = block.thoughts.length > 1
    ? block.thoughts.slice(0, -1).map((t) => t.content).join("\n\n")
    : "";

  const revisit = block.revisit ? revisitInfo(block) : null;

  return (
    <FeedItem
      dot={icon}
      dotClass={dotClass}
      dotBadge={revisit && (
        <span className="ws-revisit-badge" style={revisitStyleVars(revisit.kind)}>
          {revisit.kind === "adjustment" ? "✎" : "⚠"}
        </span>
      )}
    >
      <div className="ws-phase-head">
        <span className="ws-phase-name">{GROUP_LABEL[block.group]}</span>
        {revisit && (
          <span className="ws-revisit-pill" style={revisitStyleVars(revisit.kind)}>
            {revisit.label}
            {revisit.attempt && <span className="attempt-count">({revisit.attempt})</span>}
          </span>
        )}
        <span className="ws-spacer-sm" />
        <PhaseTimer block={block} active={active} />
      </div>

      {revisit && !!revisit.detail.length && (
        <ul className="ws-revisit-detail">
          {revisit.detail.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
      )}

      {(takeawayText || active) && (
        <div className="ws-phase-takeaway">
          {active ? (
            takeawayText ? (
              <TypewriterText text={takeawayText} />
            ) : (
              <span className="ws-shimmer-body">Reasoning through this step<span className="ws-ai-cursor" /></span>
            )
          ) : (
            <MarkdownText className="ws-md-inline">{takeawayText}</MarkdownText>
          )}
          {active && takeawayText && <span className="ws-ai-cursor" />}
          {rawExtra && <PhaseRawLink content={rawExtra} />}
        </div>
      )}

      {!!rows.length && (
        <div className="ws-phase-checklist">
          {rows.map((row) => <CheckRow key={row.key} row={row} />)}
        </div>
      )}

      {block.progress.map((p: any, i: number) => (
        <div key={`pg-${i}`} className="ws-step-progress">
          <div className="ws-feed-text">{p.message}</div>
          <ProgressBar current={p.current} total={p.total} />
        </div>
      ))}

      {!!block.slideFinalized.length && (
        <div className="ws-feed-text">
          {block.slideFinalized.length === 1
            ? `Slide ${(block.slideFinalized[0].slideIndex ?? 0) + 1} finalized.`
            : `${block.slideFinalized.length} slides finalized.`}
        </div>
      )}

      {block.questionEvent && (
        <QuestionCard
          questions={
            Array.isArray(block.questionEvent.questions) && block.questionEvent.questions.length
              ? block.questionEvent.questions
              : [{ question: block.questionEvent.question, options: block.questionEvent.options || [] }]
          }
          context={block.questionEvent.context}
          answered={isAnswered}
          answerText={answerText}
        />
      )}

      {block.validationUnresolvedEvent && (
        <ValidationUnresolvedNote ev={block.validationUnresolvedEvent} />
      )}

      {block.errorEvent && (
        <div className="ws-feed-text" style={{ color: "#F87171" }}>
          {block.errorEvent.message}
          {block.errorEvent.code ? <code> ({block.errorEvent.code})</code> : null}
        </div>
      )}

      {block.fileGeneratedEvent && <FileGeneratedNote ev={block.fileGeneratedEvent} />}
    </FeedItem>
  );
}

/** What a revisited phase's badge/pill/detail should say: a short label
 *  colored by whether this is the agent fixing its own mistake ("error")
 *  or honoring something the user asked for ("adjustment"), plus any
 *  validator detail as a subordinate list - never swapped in as the
 *  entire message the way the old backtrack banner did. */
/** Last array element matching `pred`, not the first - a merged retry
 *  block's subSteps span every attempt, and the header should reflect
 *  the current (most recent) one, not stay frozen on the first. */
function findLastMatch<T>(arr: T[], pred: (item: T) => boolean): T | undefined {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (pred(arr[i])) return arr[i];
  }
  return undefined;
}

function revisitInfo(block: PhaseBlock): { kind: "error" | "adjustment"; label: string; attempt?: string; detail: string[] } {
  const withKind = findLastMatch(block.subSteps, (s) => !!s.revisitKind);
  // Only the backend knows whether a jump back is the agent repairing its
  // own mistake. Absent that, this is "the run went back here", which is
  // what the word says - not an accusation in alarm red.
  const kind: "error" | "adjustment" = withKind?.revisitKind ?? "adjustment";
  const verb = !withKind ? "revisiting" : kind === "adjustment" ? "adjusting" : "fixing";
  const noun: Record<PhaseGroupKey, string> = {
    understand: "the brief",
    research: "the research",
    design: "the design",
    draft: "content",
    render: "the render",
  };
  const reason = findLastMatch(block.subSteps, (s) => !!s.revisionReason)?.revisionReason;
  const detail = reason ? reason.split(";").map((r) => r.trim()).filter(Boolean) : [];
  const attempt = withKind?.revisitAttempt && withKind?.revisitMaxAttempts
    ? `${withKind.revisitAttempt}/${withKind.revisitMaxAttempts}`
    : undefined;
  return { kind, label: `${verb} — ${noun[block.group]}`, attempt, detail };
}

function revisitStyleVars(kind: "error" | "adjustment"): React.CSSProperties {
  return {
    ["--ws-revisit-c" as string]: kind === "adjustment" ? "var(--ws-yellow-text)" : "var(--ws-red-text)",
    ["--ws-revisit-c-bg" as string]: kind === "adjustment" ? "var(--ws-yellow-bg)" : "var(--ws-red-bg)",
  } as React.CSSProperties;
}

function PhaseTimer({ block, active }: { block: PhaseBlock; active: boolean }) {
  const totalFromSteps = block.subSteps.reduce((s, x) => s + (x.durationMs || 0), 0);
  const wallMs = block.endTs && block.startTs
    ? new Date(block.endTs).getTime() - new Date(block.startTs).getTime()
    : undefined;
  return (
    <StepTimer
      startTs={block.startTs}
      endTs={block.endTs}
      durationMs={totalFromSteps || wallMs}
      running={active}
    />
  );
}

/** A short, cheap approximation of live token streaming: what the model
 *  actually said arrives whole over SSE, so this types it out on the client
 *  rather than pasting it in - not real per-token streaming (that needs the
 *  provider call itself to stream), but visually honest about being in
 *  progress rather than static. */
function TypewriterText({ text }: { text: string }) {
  const [shown, setShown] = useState("");
  const prevTextRef = useRef<string>("");

  useEffect(() => {
    if (text === prevTextRef.current) return;
    prevTextRef.current = text;
    setShown("");
    let i = 0;
    const id = setInterval(() => {
      i++;
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(id);
    }, 18);
    return () => clearInterval(id);
  }, [text]);

  return <MarkdownText className="ws-md-inline">{shown}</MarkdownText>;
}

function PhaseRawLink({ content }: { content: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ws-raw ws-phase-raw">
      <button type="button" className="ws-raw-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronDown size={11} className={`ws-raw-chev ${open ? "open" : ""}`} />
        Raw
      </button>
      {open && <DetailBlock label="Narration" content={content} />}
    </div>
  );
}

// ──────────────────────────────────────────────
// Boxed substep checklist rows
// ──────────────────────────────────────────────

interface Row {
  key: string;
  icon: "✓" | "●" | "↻";
  label: string;
  note: string;
  durationMs?: number;
  /** Raw tool-execution time, kept as a secondary/tooltip value once
   *  `durationMs` carries the fuller process span instead. */
  toolDurationMs?: number;
  running?: boolean;
  startTs?: string;
  args?: Record<string, unknown>;
  result?: unknown;
  sources?: SourceInfo[];
}

function toRow(s: SubStep): Row {
  return {
    key: s.key,
    icon: s.success === false ? "●" : s.running ? "↻" : "✓",
    label: toolLabel(s.toolName),
    note: s.summary || (s.running ? "" : s.error || ""),
    durationMs: s.processDurationMs ?? s.durationMs,
    toolDurationMs: s.durationMs,
    running: s.running,
    startTs: s.startTs,
    args: s.args,
    result: s.result,
    sources: s.sources,
  };
}

/** Every tool call a slide took, folded into one checklist row for that
 *  slide - a slide is drafted through several calls (layout, a chart, the
 *  copy itself), and listing each separately says less than "Slide 3" does. */
function groupDraftRows(subSteps: SubStep[]): Row[] {
  // A merged "Fixing" block (see `currentBlock`'s retry-loop reuse) can
  // cover more than one slide in the same pass - slides_needing_revision
  // isn't always a single index. Once that's true, "Attempt 2" alone is
  // ambiguous between rows, so the slide number has to be in every label,
  // not just implied by a phase header that doesn't name a slide either.
  const distinctSlides = new Set(
    subSteps.map((s) => s.slideIndex).filter((idx): idx is number => idx != null)
  );
  const disambiguateSlide = distinctSlides.size > 1;

  const rows: Row[] = [];
  let i = 0;
  while (i < subSteps.length) {
    const idx = subSteps[i].slideIndex;
    // A merged "Fixing" block accumulates one design_slide_content call
    // per attempt, all for the same slide - group consecutive calls only
    // within the same attempt, or every attempt would collapse into one
    // row and the retry count would disappear from the checklist.
    const attempt = subSteps[i].revisitAttempt;
    let end = i + 1;
    if (idx != null) {
      while (
        end < subSteps.length &&
        subSteps[end].slideIndex === idx &&
        subSteps[end].revisitAttempt === attempt
      ) end++;
    }
    const group = subSteps.slice(i, end);
    const last = group[group.length - 1];
    const anyRunning = group.some((s) => s.running);
    const anyFailed = group.some((s) => s.success === false);
    const sources = group.flatMap((s) => s.sources || []);
    const maxAttempts = group[0].revisitMaxAttempts;
    const slideLabel = idx != null ? `Slide ${idx + 1}` : undefined;
    const attemptLabel = attempt != null ? `Attempt ${attempt}${maxAttempts ? `/${maxAttempts}` : ""}` : undefined;
    const label = attemptLabel
      ? (disambiguateSlide && slideLabel ? `${slideLabel} — ${attemptLabel}` : attemptLabel)
      : slideLabel ?? toolLabel(last.toolName);
    rows.push({
      key: `slide-${idx ?? i}-${attempt ?? 0}`,
      icon: anyFailed ? "●" : anyRunning ? "↻" : "✓",
      label,
      note: last.summary || (anyRunning ? "" : last.error || ""),
      durationMs: group.reduce((s, x) => s + (x.processDurationMs ?? x.durationMs ?? 0), 0),
      toolDurationMs: group.reduce((s, x) => s + (x.durationMs || 0), 0),
      running: anyRunning,
      startTs: group.find((s) => s.running)?.startTs || group[0].startTs,
      args: last.args,
      result: last.result,
      sources: sources.length ? sources : undefined,
    });
    i = end;
  }
  return rows;
}

/** A merged "Validate" block (see `currentBlock`'s retry-loop reuse) runs
 *  one finalize_and_render_pptx + validate_rendered_output pair per
 *  attempt (confirmed against pipeline.py: a redesign invalidates
 *  render_is_current, so every attempt re-renders before re-validating) -
 *  group each pair into one "Attempt N" row instead of listing every call
 *  under its own tool name, N times over. */
function groupRenderRows(subSteps: SubStep[]): Row[] {
  const attemptCount = subSteps.filter((s) => s.toolName === "finalize_and_render_pptx").length;
  if (attemptCount <= 1) return subSteps.map(toRow);

  const rows: Row[] = [];
  let i = 0;
  let attempt = 0;
  while (i < subSteps.length) {
    attempt++;
    let end = i + 1;
    while (end < subSteps.length && subSteps[end].toolName !== "finalize_and_render_pptx") end++;
    const group = subSteps.slice(i, end);
    const last = group[group.length - 1];
    const anyRunning = group.some((s) => s.running);
    const anyFailed = group.some((s) => s.success === false);
    const sources = group.flatMap((s) => s.sources || []);
    rows.push({
      key: `render-attempt-${attempt}`,
      icon: anyFailed ? "●" : anyRunning ? "↻" : "✓",
      label: `Attempt ${attempt}`,
      note: last.summary || (anyRunning ? "" : last.error || ""),
      durationMs: group.reduce((s, x) => s + (x.processDurationMs ?? x.durationMs ?? 0), 0),
      toolDurationMs: group.reduce((s, x) => s + (x.durationMs || 0), 0),
      running: anyRunning,
      startTs: group.find((s) => s.running)?.startTs || group[0].startTs,
      args: last.args,
      result: last.result,
      sources: sources.length ? sources : undefined,
    });
    i = end;
  }
  return rows;
}

function CheckRow({ row }: { row: Row }) {
  const [open, setOpen] = useState(false);
  const hasArgs = Object.keys(row.args || {}).length > 0;
  const hasResult = row.result !== undefined && row.result !== null && row.result !== "";
  const hasRaw = hasArgs || hasResult;
  const iconClass = row.icon === "●" ? "bad" : row.icon === "↻" ? "run" : "ok";

  return (
    <>
      <div className="ws-phase-check-row">
        <div className="ws-check-top">
          <span className={`ws-check-icon ${iconClass}`}>{row.icon}</span>
          <span className="ws-check-label">{row.label}</span>
          {!!row.sources?.length && <SourcesInline sources={row.sources} />}
          <span className="ws-spacer-sm" />
          {row.running ? (
            <StepTimer startTs={row.startTs || ""} running durationMs={undefined} />
          ) : (
            <span
              className="ws-step-dur"
              title={
                row.toolDurationMs != null && row.toolDurationMs !== row.durationMs
                  ? `tool call: ${fmtDuration(row.toolDurationMs)}`
                  : undefined
              }
            >
              {fmtDuration(row.durationMs)}
            </span>
          )}
          {hasRaw && (
            <button type="button" className="ws-check-raw" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
              Raw
            </button>
          )}
        </div>
        {row.note && <div className="ws-check-note">{row.note}</div>}
      </div>
      {open && hasRaw && (
        <div className="ws-phase-check-raw-body">
          {hasArgs && <DetailBlock label="Args" content={stringify(row.args)} />}
          {hasResult && <DetailBlock label="Result" content={stringify(row.result)} />}
        </div>
      )}
    </>
  );
}

/** Quiet, single-color note for "gave up after the retry cap" - a fact
 *  worth noticing, not an alarm. Colored with the same adjustment-driven
 *  yellow the revisit badges use, not a bordered red alert box. */
function ValidationUnresolvedNote({ ev }: { ev: any }) {
  const issues: any[] = Array.isArray(ev.issues) ? ev.issues : [];
  return (
    <div className="ws-feed-text" style={{ color: "var(--ws-yellow-text)" }}>
      Wasn&apos;t able to fully resolve {ev.issueCount ?? issues.length} layout
      {(ev.issueCount ?? issues.length) === 1 ? " issue" : " issues"} after {ev.attempts} attempt
      {ev.attempts === 1 ? "" : "s"}. Continuing with the presentation - you may want to review
      the affected slides manually.
      <ul className="ws-md-issues" style={{ color: "var(--ws-text-muted)" }}>
        {issues.slice(0, 6).map((issue: any, i: number) => (
          <li key={i}>
            {typeof issue.slideIndex === "number" ? `Slide ${issue.slideIndex + 1}: ` : ""}
            {issue.detail || issue.type}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FileGeneratedNote({ ev }: { ev: any }) {
  const url = buildDownloadUrl(`/api/orchestrator/files/${ev.fileId}`);
  return (
    <div className="ws-file-dl-row">
      <span className="ws-feed-text">
        <b>{ev.fileName}</b>
        {ev.slideCount ? ` - ${ev.slideCount} slides` : ""}
        {ev.fileSizeBytes ? ` - ${formatFileSize(ev.fileSizeBytes)}` : ""}
      </span>
      <a className="ws-tb-btn primary ws-file-dl" href={url} download>
        <Download size={13} /> Download
      </a>
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

/** Timer that ticks while running, freezing on its duration once done. */
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

export function FeedItem({
  dot,
  dotClass,
  dotBadge,
  children,
}: {
  dot: React.ReactNode;
  dotClass?: string;
  /** A small self-contained badge anchored to this item's own dot, e.g.
   *  the revisit-kind marker - never a line reaching for another element. */
  dotBadge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="ws-feed-item">
      <div className="ws-feed-dot-wrap">
        <div className={`ws-feed-dot ${dotClass ?? ""}`}>{dot}</div>
        {dotBadge}
      </div>
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
