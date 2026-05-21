"use client";

import { useEffect, useRef, useCallback } from "react";
import { SSEClient } from "@/lib/sse";
import type { AgentEvent } from "@slideagent/schemas/agent-events";

interface UseSSEOptions {
  sessionId: string | null;
  onEvent: (event: AgentEvent) => void;
  onError?: (error: Event) => void;
}

export function useSSE({ sessionId, onEvent, onError }: UseSSEOptions) {
  const clientRef = useRef<SSEClient | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!sessionId) {
      clientRef.current?.disconnect();
      clientRef.current = null;
      return;
    }

    const ORCHESTRATOR_URL =
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
    const url = `${ORCHESTRATOR_URL}/sessions/${sessionId}/events`;

    const client = new SSEClient(url, {
      onEvent: (event) => onEventRef.current(event),
      onError: (err) => {
        console.error("SSE error:", err);
        onError?.(err);
      },
    });

    client.connect();
    clientRef.current = client;

    return () => {
      client.disconnect();
      clientRef.current = null;
    };
  }, [sessionId]);

  const disconnect = useCallback(() => {
    clientRef.current?.disconnect();
  }, []);

  return { disconnect };
}