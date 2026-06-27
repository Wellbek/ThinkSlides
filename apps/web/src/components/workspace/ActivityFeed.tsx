"use client";

import { useChatStore } from "@/stores/chat-store";
import { QuestionCard } from "./QuestionCard";
import type { AgentEvent } from "@thinkslides/schemas";

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

export function ActivityFeed() {
  const messages = useChatStore((s) => s.activeMessages);

  return (
    <div className="ws-feed">
      {messages.length === 0 && (
        <div className="ws-empty-note">Send a message to start the agent workflow.</div>
      )}
      {messages.map((m) =>
        m.role === "user" ? (
          <FeedItem key={m.id} dot={<span className="ws-av indigo">WK</span>} dotClass="ws-dot-user">
            <div className="ws-feed-header"><span className="lbl">You</span><span className="ts">{fmtTime(m.created_at)}</span></div>
            <div className="ws-feed-text">{m.content}</div>
          </FeedItem>
        ) : (
          <AssistantBlock key={m.id} message={m} />
        )
      )}
    </div>
  );
}

function AssistantBlock({ message }: { message: any }) {
  const events: AgentEvent[] = message.agentEventStream || [];
  const isLive = message.isComplete === false;

  const lastThought = [...events].reverse().find((e) => e.type === "thought") as any;
  const lastProgress = [...events].reverse().find((e) => e.type === "progress") as any;

  return (
    <>
      {events.map((ev: any, i) => (
        <EventItem key={`${message.id}-${i}`} ev={ev} />
      ))}

      {isLive && (
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
              {lastThought ? PHASE_LABEL[lastThought.phase] ?? "Working" : "Working..."}
            </span>
          </div>
          <span className="ws-shimmer-body">
            {lastThought ? lastThought.content : "The agent is reasoning through the next step."}
            <span className="ws-ai-cursor" />
          </span>
          {lastProgress && (
            <div className="ws-ai-progress">
              <div className="track">
                <div className="fill" style={{ width: `${pct(lastProgress.current, lastProgress.total)}%` }} />
              </div>
              <div className="meta">
                <span>{lastProgress.message || `${lastProgress.current} / ${lastProgress.total}`}</span>
                <span><b>{pct(lastProgress.current, lastProgress.total)}%</b></span>
              </div>
            </div>
          )}
        </FeedItem>
      )}
    </>
  );
}

function EventItem({ ev }: { ev: any }) {
  switch (ev.type) {
    case "thought":
      return (
        <FeedItem dot="◆" dotClass="ws-dot-fact">
          <div className="ws-feed-header"><span className="lbl">{PHASE_LABEL[ev.phase] ?? "Thinking"}</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          {ev.content && <div className="ws-feed-text">{ev.content}</div>}
        </FeedItem>
      );
    case "tool_call":
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header"><span className="lbl">{toolLabel(ev.toolName)}</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          {Object.keys(ev.toolArgs || {}).length > 0 && (
            <div className="ws-feed-text">Args: <code>{truncate(JSON.stringify(ev.toolArgs), 140)}</code></div>
          )}
        </FeedItem>
      );
    case "tool_result":
      return (
        <FeedItem dot={ev.success ? "◆" : "●"} dotClass={ev.success ? "ws-dot-fact" : "ws-dot-error"}>
          <div className="ws-feed-header"><span className="lbl">{toolLabel(ev.toolName)} - result</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-card">
            <div className="ws-card-head">
              <div className="ws-card-title">{ev.success ? "completed" : "failed"} <span className="tag">{ev.durationMs ? `${ev.durationMs}ms` : "tool_result"}</span></div>
            </div>
            {ev.error ? (
              <div className="ws-card-row bad"><span className="v">{ev.error}</span></div>
            ) : (
              <div className="ws-card-row"><span className="v">{truncate(stringify(ev.result), 240)}</span></div>
            )}
          </div>
        </FeedItem>
      );
    case "slide_finalized":
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">Slide <b>{(ev.slideIndex ?? 0) + 1}</b> finalized<span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.previewUrl ? <code>preview ready</code> : "Layout cloned from template; placeholders filled."}</div>
        </FeedItem>
      );
    case "error":
      return (
        <FeedItem dot="●" dotClass="ws-dot-error">
          <div className="ws-feed-header"><span className="lbl" style={{ color: "#F87171" }}>Error</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.message}{ev.code ? <code> ({ev.code})</code> : null}</div>
        </FeedItem>
      );
    case "progress":
      return (
        <FeedItem dot="◆" dotClass="ws-dot-fact">
          <div className="ws-feed-header"><span className="lbl">Progress</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.message}</div>
          <div className="ws-ai-progress">
            <div className="track"><div className="fill" style={{ width: `${pct(ev.current, ev.total)}%` }} /></div>
            <div className="meta"><span>{ev.current} / {ev.total}</span><span><b>{pct(ev.current, ev.total)}%</b></span></div>
          </div>
        </FeedItem>
      );
    case "file_generated":
      return (
        <FeedItem dot="↺" dotClass="ws-dot-status">
          <div className="ws-feed-header sys">File generated - <b>{ev.fileName}</b><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <div className="ws-feed-text">{ev.slideCount ? `${ev.slideCount} slides` : "ready to download"}</div>
        </FeedItem>
      );
    case "question":
      return (
        <FeedItem dot="?" dotClass="ws-dot-q">
          <div className="ws-feed-header"><span className="lbl">Agent question</span><span className="ws-qbadge">needs your input</span><span className="ts">{fmtTime(ev.timestamp)}</span></div>
          <QuestionCard question={ev.question} options={ev.options} context={ev.context} />
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

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "..." : s;
}

function stringify(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
