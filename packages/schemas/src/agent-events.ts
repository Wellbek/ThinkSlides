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
  | DoneEvent;

export interface SSEMessage {
  event: AgentEventType;
  data: AgentEvent;
  id?: string;
  retry?: number;
}
