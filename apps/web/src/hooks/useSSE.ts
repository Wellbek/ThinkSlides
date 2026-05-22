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

    // SSEClient takes sessionId and builds the URL internally,
    // including the ?token= query param for auth.
    const client = new SSEClient(sessionId);

    client.on("thought", (e) => {
      try {
        const parsed: AgentEvent = JSON.parse(e.data);
        onEventRef.current(parsed);
      } catch {
        onEventRef.current({
          type: "thought",
          phase: "content_analysis",
          content: e.data,
          timestamp: new Date().toISOString(),
        } as AgentEvent);
      }
    });

    client.on("tool_call", (e) => {
      try {
        const parsed: AgentEvent = JSON.parse(e.data);
        onEventRef.current(parsed);
      } catch {
        onEventRef.current({
          type: "tool_call",
          phase: "content_generation",
          toolName: "unknown",
          toolArgs: {},
          callId: "",
          timestamp: new Date().toISOString(),
        } as AgentEvent);
      }
    });

    client.on("tool_result", (e) => {
      try {
        const parsed: AgentEvent = JSON.parse(e.data);
        onEventRef.current(parsed);
      } catch {
        onEventRef.current({
          type: "tool_result",
          phase: "content_generation",
          toolName: "unknown",
          callId: "",
          result: e.data,
          durationMs: 0,
          timestamp: new Date().toISOString(),
        } as AgentEvent);
      }
    });

    client.on("slide_finalized", (e) => {
      try {
        const parsed: AgentEvent = JSON.parse(e.data);
        onEventRef.current(parsed);
      } catch {
        console.error("Failed to parse slide_finalized event");
      }
    });

    client.on("error", (e) => {
      try {
        const parsed: AgentEvent = JSON.parse(e.data);
        onEventRef.current(parsed);
      } catch {
        onEventRef.current({
          type: "error",
          phase: "content_analysis",
          message: e.data,
          timestamp: new Date().toISOString(),
        } as AgentEvent);
      }
    });

    client.on("progress", (e) => {
      try {
        const parsed: AgentEvent = JSON.parse(e.data);
        onEventRef.current(parsed);
      } catch {
        onEventRef.current({
          type: "progress",
          phase: "content_analysis",
          current: 0,
          total: 0,
          message: e.data,
          timestamp: new Date().toISOString(),
        } as AgentEvent);
      }
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