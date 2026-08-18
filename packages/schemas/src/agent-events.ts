/**
 * Agent Event Schema
 * Typed SSE events streamed from the orchestrator to the frontend.
 */

export type AgentEventPhase =
  | "content_analysis"
  | "structure_planning"
  | "layout_selection"
  | "content_generation"
  | "validation"
  | "rendering"
  | "complete";

/**
 * The actual pipeline step behind an event's coarser `phase` label.
 *
 * `phase` is what several steps share (intake, scout and research are all
 * "content_analysis"), which is all a prompt ever needed. `step` is what the
 * interface's phase timeline groups by, so steps that share a `phase` still
 * render as the distinct things they are - "Understand" and "Research" do not
 * fold into one box just because the model sees them as the same phase.
 * Optional and may be absent on events from before this field existed, or on
 * ones (like a session-load failure) that happen outside any pipeline step.
 */
export type PipelineStep =
  | "intake"
  | "scout"
  | "consult"
  | "design"
  | "research"
  | "plan"
  | "review"
  | "draft"
  | "render"
  | "validate"
  | "complete";

/**
 * Every event name the browser subscribes to, in one place.
 *
 * This is the list, not a copy of it: `AgentEventType` is derived from it and
 * `sse.ts` iterates it to register listeners, so adding a name here subscribes
 * to it everywhere. That matters because `EventSource` dispatches by event
 * name - a name it was never told about is delivered nowhere, with no error,
 * no warning and no type error. The event just never arrives, and since the
 * orchestrator persists the stream regardless, it reappears on reload, which
 * reads as a rendering bug rather than a missing subscription.
 *
 * `validation_unresolved` - the event telling a user their deck still has
 * layout defects - was emitted from day one and lost exactly this way.
 */
export const AGENT_EVENT_TYPES = [
  "thought",
  "tool_call",
  "tool_result",
  "slide_finalized",
  "file_generated",
  "error",
  "progress",
  "question",
  "rate_limit",
  "validation_unresolved",
  "connection_degraded",
  "timer_update",
  "session_title_updated",
  "done",
] as const;

export type AgentEventType = (typeof AGENT_EVENT_TYPES)[number];

/**
 * Events the AI service publishes that are consumed server-side and are not
 * meant for the browser. `deck_finalized` is what tells `api/generate.py` to
 * persist the rendered deck and emit `file_generated`; the client hears about
 * the deck through that, not through this.
 *
 * They are listed rather than left undeclared so that "not subscribed" is a
 * recorded decision. A new event name that appears in neither list fails the
 * contract test in the AI service, which is the point: every name has to be
 * classified as client-facing or internal by someone.
 */
export const INTERNAL_AGENT_EVENT_TYPES = [
  "deck_finalized",
  "message_complete",
] as const;

export type InternalAgentEventType = (typeof INTERNAL_AGENT_EVENT_TYPES)[number];

export interface ThoughtEvent {
  type: "thought";
  phase: AgentEventPhase;
  step?: PipelineStep;
  content: string;
  durationMs?: number;   // optional, for timing
  timestamp: string;
}

export interface ToolCallEvent {
  type: "tool_call";
  phase: AgentEventPhase;
  step?: PipelineStep;
  toolName: string;
  toolArgs: Record<string, unknown>;
  callId: string;
  timestamp: string;
  // Present only for a design_slide_content call redesigning a slide
  // validation flagged - distinguishes a fix-up pass from an original one,
  // which otherwise look identical in the activity feed.
  isRevision?: boolean;
  revisionReason?: string;
}

export interface SourceInfo {
  url: string;
  title: string;
  type: "web" | "internal";
}

export interface ToolResultEvent {
  type: "tool_result";
  phase: AgentEventPhase;
  step?: PipelineStep;
  toolName: string;
  callId: string;
  result: unknown;
  /** Server-authored plain-language description of what this step did.
   *  The feed leads with it and keeps `result` behind a "Raw" disclosure. */
  summary?: string;
  durationMs: number;
  success: boolean;      // required – indicates tool execution success
  error?: string;        // optional error message if success is false
  tokenCount?: number;
  timestamp: string;
  sources?: SourceInfo[];
}

export interface SlideFinalizedEvent {
  type: "slide_finalized";
  phase: "content_generation";
  step?: PipelineStep;
  slideIndex: number;
  slideData: unknown;
  previewUrl?: string;
  timestamp: string;
}

export interface ErrorEvent {
  type: "error";
  phase: AgentEventPhase;
  step?: PipelineStep;
  message: string;
  code?: string;
  timestamp: string;
}

export interface ProgressEvent {
  type: "progress";
  phase: AgentEventPhase;
  current: number;
  total: number;
  message: string;
  timestamp: string;
}

export interface FileGeneratedEvent {
  type: "file_generated";
  phase: "rendering";
  step?: PipelineStep;
  fileId: string;
  fileName: string;
  fileUrl: string;
  fileSizeBytes: number;
  fileType: string;
  slideCount: number;
  timestamp: string;
}

/**
 * A design direction offered as something to look at rather than read.
 *
 * A direction is a whole design system - ground, type pairing, motif,
 * density - and five words of text cannot convey the difference between two
 * of them. `previewUrls` are the specimen slides rendered under it, in order.
 */
export interface AgentQuestionPreviewOption {
  kind: "preview";
  id: string;
  /** The direction's human name - "Dark analyst", never "Option A". */
  label: string;
  /** One line on why this direction, shown under the label. */
  description?: string;
  previewUrls: string[];
}

/**
 * One answer a user can pick.
 *
 * A bare string is every question the agent has ever asked, and stays exactly
 * that: sessions persisted before previews existed hold strings, and they
 * must keep rendering and answering unchanged.
 */
export type AgentQuestionOption = string | AgentQuestionPreviewOption;

/** One question put to the user, with the answers they can pick in a tap. */
export interface AgentQuestion {
  question: string;
  options: AgentQuestionOption[];
}

export interface QuestionEvent {
  type: "question";
  phase: "user_input";
  /** Which pipeline step raised this pause: "consult" for the intake
   *  clarifying question, "design" for choosing a direction, "review" for
   *  the outline check. */
  step?: PipelineStep;
  /** Every question asked in this pause. */
  questions: AgentQuestion[];
  /** The first question, also sent flat for consumers written before a
   *  single pause could carry several. */
  question: string;
  options: AgentQuestionOption[];
  context?: string;
  timestamp: string;
}

/**
 * A provider back-off, announced while it is happening. Without this a rate
 * limit looks exactly like a hang: the retry happens inside the provider call,
 * so the interface would show a step sitting there for half a minute.
 *
 * This is state, not history. It says "a wait is on right now", and it stops
 * being true the moment the call goes through - which is why the client keeps
 * the latest one on the store and shows it in a strip that retires itself,
 * rather than appending it to the transcript.
 */
export interface RateLimitEvent {
  type: "rate_limit";
  phase: AgentEventPhase;
  reason: "rate_limit" | "transient";
  attempt: number;
  maxAttempts: number;
  retryInSeconds: number;
  provider?: string;
  model?: string;
  message: string;
  timestamp: string;
}

/** The deck ran out of revision attempts with defects still in it. */
export interface ValidationUnresolvedEvent {
  type: "validation_unresolved";
  phase: "validation";
  step?: PipelineStep;
  attempts: number;
  issueCount: number;
  issues: { slideIndex?: number; type?: string; detail?: string }[];
  timestamp: string;
}

/**
 * A configured connection could not be reached, and the run carried on without
 * it.
 *
 * Reported once per run and never fatal. The alternative failure mode is worse
 * than the outage: a deck quietly built from fewer sources, with the user
 * unable to tell "no relevant results" from "the tool was never there".
 */
export interface ConnectionDegradedEvent {
  type: "connection_degraded";
  phase: AgentEventPhase;
  connections: { label: string; reason: string }[];
  timestamp: string;
}

/** Wall-clock elapsed time for the run, ticked while it is in flight. */
export interface TimerUpdateEvent {
  type: "timer_update";
  elapsedMs: number;
  timestamp?: string;
}

/** The orchestrator renamed the session from its first exchange. */
export interface SessionTitleUpdatedEvent {
  type: "session_title_updated";
  session_id: string;
  title: string;
  timestamp?: string;
}

export interface DoneEvent {
  type: "done";
  phase?: "complete";
  step?: PipelineStep;
  message_id: string;
  content: string;
  files?: FileGeneratedEvent[];
  overallDurationMs?: number;
  timestamp: string;
}

export type AgentEvent =
  | ThoughtEvent
  | ToolCallEvent
  | ToolResultEvent
  | SlideFinalizedEvent
  | FileGeneratedEvent
  | ErrorEvent
  | ProgressEvent
  | QuestionEvent
  | ValidationUnresolvedEvent
  | ConnectionDegradedEvent
  | DoneEvent;
// TimerUpdateEvent, SessionTitleUpdatedEvent and RateLimitEvent are
// deliberately absent: this union models what lands in a message's
// `agentEventStream`, and those three are side-channel updates (run clock,
// sidebar title, the wait currently in progress) that the store applies
// directly without recording them on a message. Every member that is left
// carries a `timestamp`, which is why consumers of this union can rely on that
// field being present.

export interface SSEMessage {
  event: AgentEventType;
  data: AgentEvent;
  id?: string;
  retry?: number;
}
