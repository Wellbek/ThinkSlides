/**
 * SSE client for consuming agent events from the orchestrator.
 * Parses typed events and invokes callbacks.
 */

import type { AgentEvent, AgentEventType } from "@slideagent/schemas/agent-events";

export interface SSEClientOptions {
  onEvent: (event: AgentEvent) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
}

export class SSEClient {
  private eventSource: EventSource | null = null;
  private url: string;
  private options: SSEClientOptions;

  constructor(url: string, options: SSEClientOptions) {
    this.url = url;
    this.options = options;
  }

  connect(): void {
    this.disconnect();

    this.eventSource = new EventSource(this.url);

    this.eventSource.onopen = () => {
      this.options.onOpen?.();
    };

    // Listen for all typed events
    const eventTypes: AgentEventType[] = [
      "thought",
      "tool_call",
      "tool_result",
      "slide_finalized",
      "error",
      "progress",
    ];

    for (const type of eventTypes) {
      this.eventSource.addEventListener(type, (e: MessageEvent) => {
        try {
          const data: AgentEvent = JSON.parse(e.data);
          this.options.onEvent(data);
        } catch (err) {
          console.error(`Failed to parse SSE event [${type}]:`, err, e.data);
        }
      });
    }

    // Generic message handler (fallback)
    this.eventSource.onmessage = (e: MessageEvent) => {
      try {
        const data: AgentEvent = JSON.parse(e.data);
        this.options.onEvent(data);
      } catch {
        // ignore unparseable messages
      }
    };

    this.eventSource.onerror = (e: Event) => {
      this.options.onError?.(e);
      // Auto-reconnect is handled by EventSource spec
    };
  }

  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  get connected(): boolean {
    return this.eventSource !== null && this.eventSource.readyState !== EventSource.CLOSED;
  }
}