const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';
const SSE_BASE = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL || API_BASE;

export type SSEEventType =
  | 'thought'
  | 'tool_call'
  | 'tool_result'
  | 'slide_finalized'
  | 'error'
  | 'progress'
  | 'done';

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

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  connect(): void {
    this.disconnect();

    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;

    // EventSource cannot set custom headers, so pass token as query param.
    // The Go middleware checks both Authorization header and ?token= query param.
    let url = `${SSE_BASE}/api/orchestrator/sessions/${this.sessionId}/events`;
    if (token) {
      url += `?token=${encodeURIComponent(token)}`;
    }

    this.eventSource = new EventSource(url);

    this.eventSource.onopen = () => {
      this.reconnectAttempts = 0;
    };

    this.eventSource.onerror = () => {
      if (this.eventSource?.readyState === EventSource.CLOSED) {
        // Auth failure or server explicitly closed — don't reconnect
        return;
      }
      this.tryReconnect();
    };

    // Listen for each typed event
    const eventTypes: SSEEventType[] = [
      'thought',
      'tool_call',
      'tool_result',
      'slide_finalized',
      'error',
      'progress',
      'done',
    ];

    for (const eventType of eventTypes) {
      this.eventSource.addEventListener(eventType, (e: MessageEvent) => {
        const sseEvent: SSEEvent = {
          id: (e as MessageEvent & { lastEventId?: string }).lastEventId,
          event: eventType,
          data: e.data,
        };
        this.emit(sseEvent);
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