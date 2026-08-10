// SSE always uses a relative URL so the request is proxied through the Next.js
// middleware rewrite (/api/orchestrator/* → orchestrator /api/*).  Going direct
// to the orchestrator caused CORS issues and bypassed the auth-cookie flow.
const SSE_BASE = 'http://localhost:8080';

export type SSEEventType =
  | 'thought'
  | 'tool_call'
  | 'tool_result'
  | 'slide_finalized'
  | 'file_generated'
  | 'error'
  | 'progress'
  | 'session_title_updated'
  | 'question'
  | 'rate_limit'
  | 'validation_unresolved'
  | 'done'
  | 'timer_update';

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
    // The /api/orchestrator prefix is rewritten by Next.js middleware to the
    // orchestrator's /api/sessions/:id/events endpoint.
    let url = `${SSE_BASE}/api/sessions/${this.sessionId}/events`;
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

    // Listen for each typed event. EventSource dispatches by name, so an
    // event missing from this list is delivered nowhere and only appears
    // after a reload, from the persisted stream - which is exactly what
    // happened to validation_unresolved: the one event telling the user their
    // deck still had layout defects was invisible while it mattered.
    const eventTypes: SSEEventType[] = [
      'thought',
      'tool_call',
      'tool_result',
      'slide_finalized',
      'file_generated',
      'error',
      'progress',
      'session_title_updated',
      'question',
      'rate_limit',
      'validation_unresolved',
      'done',
      'timer_update',
    ];

    for (const eventType of eventTypes) {
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
