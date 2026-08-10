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

export type AgentEventType =
  | "thought"
  | "tool_call"
  | "tool_result"
  | "slide_finalized"
  | "file_generated"
  | "error"
  | "progress"
  | "question"
  | "rate_limit"
  | "validation_unresolved"
  | "done";

export interface ThoughtEvent {
  type: "thought";
  phase: AgentEventPhase;
  content: string;
  durationMs?: number;   // optional, for timing
  timestamp: string;
}

export interface ToolCallEvent {
  type: "tool_call";
  phase: AgentEventPhase;
  toolName: string;
  toolArgs: Record<string, unknown>;
  callId: string;
  timestamp: string;
}

export interface SourceInfo {
  url: string;
  title: string;
  type: "web" | "internal";
}

export interface ToolResultEvent {
  type: "tool_result";
  phase: AgentEventPhase;
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
  slideIndex: number;
  slideData: unknown;
  previewUrl?: string;
  timestamp: string;
}

export interface ErrorEvent {
  type: "error";
  phase: AgentEventPhase;
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
  fileId: string;
  fileName: string;
  fileUrl: string;
  fileSizeBytes: number;
  fileType: string;
  slideCount: number;
  timestamp: string;
}

/** One question put to the user, with the answers they can pick in a tap. */
export interface AgentQuestion {
  question: string;
  options: string[];
}

export interface QuestionEvent {
  type: "question";
  phase: "user_input";
  /** Every question asked in this pause. */
  questions: AgentQuestion[];
  /** The first question, also sent flat for consumers written before a
   *  single pause could carry several. */
  question: string;
  options: string[];
  context?: string;
  timestamp: string;
}

/**
 * A provider back-off, announced while it is happening. Without this a rate
 * limit looks exactly like a hang: the retry happens inside the provider call,
 * so the interface would show a step sitting there for half a minute.
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
  attempts: number;
  issueCount: number;
  issues: { slideIndex?: number; type?: string; detail?: string }[];
  timestamp: string;
}

export interface DoneEvent {
  type: "done";
  phase?: "complete";
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
  | RateLimitEvent
  | ValidationUnresolvedEvent
  | DoneEvent;

export interface SSEMessage {
  event: AgentEventType;
  data: AgentEvent;
  id?: string;
  retry?: number;
}
