declare module "@slideagent/schemas/agent-events" {
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
    | "error"
    | "progress";

  export interface ThoughtEvent {
    type: "thought";
    phase: AgentEventPhase;
    content: string;
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

  export interface ToolResultEvent {
    type: "tool_result";
    phase: AgentEventPhase;
    toolName: string;
    callId: string;
    result: unknown;
    durationMs: number;
    tokenCount?: number;
    timestamp: string;
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

  export type AgentEvent =
    | ThoughtEvent
    | ToolCallEvent
    | ToolResultEvent
    | SlideFinalizedEvent
    | ErrorEvent
    | ProgressEvent;
}

declare module "@slideagent/schemas/slide" {
  export interface Slide {
    slideIndex: number;
    layoutId: string;
    layoutName: string;
    contents: unknown[];
    notes?: string;
    speakerNotes?: string;
  }
}