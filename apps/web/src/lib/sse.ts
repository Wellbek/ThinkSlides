import { AGENT_EVENT_TYPES, type AgentEventType } from '@thinkslides/schemas';

// SSE goes through the same /api/orchestrator proxy as every other API call,
// as a same-origin relative URL. A hardcoded absolute base only ever works
// when the browser and the orchestrator are the same machine - from any other
// device the event stream points at the *viewer's* localhost.
//
// Set NEXT_PUBLIC_ORCHESTRATOR_URL to connect straight to the orchestrator
// instead (it serves Access-Control-Allow-Origin: *), for deployments that
// terminate the stream somewhere that buffers it.
const SSE_BASE = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL || '';

/** Path prefix: the proxy exposes the orchestrator's /api as /api/orchestrator. */
const SSE_PREFIX = SSE_BASE ? '/api' : '/api/orchestrator';

/**
 * The event names come from @thinkslides/schemas rather than being restated
 * here. A local copy is how an event goes missing: EventSource dispatches by
 * name, so one this client was never told about is delivered nowhere, with no
 * error and no type error to catch it.
 */
export type SSEEventType = AgentEventType;

export interface SSEEvent {
  id?: string;
  event: SSEEventType;
  data: string;
}

export type SSEEventHandler = (event: SSEEvent) => void;

export class SSEClient {
  private eventSource: EventSource | null = null;
  private sessionId: string;
  private handlers: Map<SSEEventType, SSEEventHandler[]> = new Map();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private intentionallyClosed = false;

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  connect(): void {
    this.disconnect();
    // disconnect() raises intentionallyClosed so an in-flight teardown is not
    // mistaken for a dropped connection. Clear it again here, or the very
    // first connect would permanently disable reconnection for this client.
    this.intentionallyClosed = false;

    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;

    // EventSource cannot set custom headers, so pass token as query param.
    // The Go middleware checks both Authorization header and ?token= query param.
    let url = `${SSE_BASE}${SSE_PREFIX}/sessions/${this.sessionId}/events`;
    if (token) {
      url += `?token=${encodeURIComponent(token)}`;
    }

    this.eventSource = new EventSource(url);

    this.eventSource.onopen = () => {
      console.log('SSE OPEN');
      this.reconnectAttempts = 0;
    };

    this.eventSource.onerror = () => {
      if (this.intentionallyClosed) return;
      // A transient drop leaves readyState at CONNECTING and EventSource
      // retries on its own, so stay out of its way. CLOSED means the browser
      // has given up (bad status, wrong content type, orchestrator restarting)
      // and will never retry - that is the case worth handling, because the
      // alternative is a chat that silently stops updating until a reload.
      if (this.eventSource?.readyState === EventSource.CLOSED) {
        this.tryReconnect();
      }
    };

    // Subscribe to every declared event name. Adding one to AGENT_EVENT_TYPES
    // is all it takes to receive it here.
    for (const eventType of AGENT_EVENT_TYPES) {
      this.eventSource.addEventListener(eventType, (e: MessageEvent) => {
        // 'error' is both one of our event names and EventSource's own
        // connection-failure event, which carries no data. Without this the
        // onerror path would also be reported to handlers as an agent error.
        if (e.data === undefined) return;

        console.log('SSE EVENT', eventType, e.data);

        const sseEvent: SSEEvent = {
          id: (e as MessageEvent & { lastEventId?: string }).lastEventId,
          event: eventType,
          data: e.data,
        };

        this.emit(sseEvent);

        // Auto-close on 'done' — the agent loop has finished
        // BUT: don't close if paused (workflow will resume)
        if (eventType === 'done') {
          const eventData = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
          const isPaused = eventData.paused === true;
          if (!isPaused) {
            this.disconnect();
          }
        }
      });
    }
  }

  /**
   * Whether there is a live EventSource right now. `connect()` auto-closes on
   * an unpaused `done` (see the listener below), and nothing else in this
   * client ever reopens it - a caller that wants to keep receiving events for
   * a session that has already finished a turn needs to check this and call
   * `connect()` again itself before sending the next message.
   */
  isConnected(): boolean {
    return this.eventSource !== null && this.eventSource.readyState !== EventSource.CLOSED;
  }

  on(eventType: SSEEventType, handler: SSEEventHandler): void {
    const existing = this.handlers.get(eventType) || [];
    existing.push(handler);
    this.handlers.set(eventType, existing);
  }

  off(eventType: SSEEventType, handler: SSEEventHandler): void {
    const existing = this.handlers.get(eventType) || [];
    this.handlers.set(
      eventType,
      existing.filter((h) => h !== handler)
    );
  }

  disconnect(): void {
    this.intentionallyClosed = true;
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  private emit(event: SSEEvent): void {
    const handlers = this.handlers.get(event.event) || [];
    for (const handler of handlers) {
      try {
        handler(event);
      } catch (err) {
        console.error('SSE handler error:', err);
      }
    }
  }

  private tryReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max SSE reconnect attempts reached');
      this.disconnect();
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000);

    setTimeout(() => {
      this.connect();
    }, delay);
  }
}
