package sse

import (
    "context"
    "encoding/json"
    "fmt"
    "net/http"
    "sync"

    "github.com/gin-gonic/gin"
    "github.com/redis/go-redis/v9"
)

type Event struct {
    Event string      // event type (thought, tool_call, tool_result, etc.)
    Data  interface{} // payload (will be JSON)
}

type Hub struct {
    mu          sync.RWMutex
    subscribers map[string]map[chan Event]struct{}
    redisClient *redis.Client // new
}

// NewHub now accepts a Redis client
func NewHub(redisClient *redis.Client) *Hub {
    return &Hub{
        subscribers: make(map[string]map[chan Event]struct{}),
        redisClient: redisClient,
    }
}

// Subscribe registers a channel and automatically starts listening to Redis for that session.
func (h *Hub) Subscribe(sessionID string) chan Event {
    h.mu.Lock()
    defer h.mu.Unlock()

    ch := make(chan Event, 64)

    if h.subscribers[sessionID] == nil {
        h.subscribers[sessionID] = make(map[chan Event]struct{})
        // First subscriber for this session: start Redis listener
        go h.listenToRedis(sessionID)
    }
    h.subscribers[sessionID][ch] = struct{}{}
    return ch
}

// Unsubscribe removes a channel and cleans up the Redis listener if no subscribers remain.
func (h *Hub) Unsubscribe(sessionID string, ch chan Event) {
    h.mu.Lock()
    defer h.mu.Unlock()

    if subs, ok := h.subscribers[sessionID]; ok {
        delete(subs, ch)
        if len(subs) == 0 {
            delete(h.subscribers, sessionID)
            // No more subscribers → stop Redis listener (context cancel is internal)
        }
    }
    close(ch)
}

// Broadcast sends an event to all local subscribers of a session.
func (h *Hub) Broadcast(sessionID string, event Event) {
    h.mu.RLock()
    defer h.mu.RUnlock()

    if subs, ok := h.subscribers[sessionID]; ok {
        for ch := range subs {
            select {
            case ch <- event:
            default:
                // drop
            }
        }
    }
}

// listenToRedis subscribes to the session's Redis channel and forwards messages to Broadcast.
func (h *Hub) listenToRedis(sessionID string) {
    ctx := context.Background()
    channel := fmt.Sprintf("agent:events:%s", sessionID)
    pubsub := h.redisClient.Subscribe(ctx, channel)
    defer pubsub.Close()

    ch := pubsub.Channel()
    for msg := range ch {
        // The AI service sends JSON strings. Wrap as SSE event.
        // The original event type is inside the JSON, but we use "message" as the SSE event name.
        // The frontend already listens to "thought", "tool_call", etc. – they are parsed from the data.
        // So we keep the original JSON as the Data field and set Event to "message".
        var raw map[string]interface{}
        if err := json.Unmarshal([]byte(msg.Payload), &raw); err == nil {
            if eventType, ok := raw["type"].(string); ok {
                // Use the original type as SSE event name for better compatibility
                h.Broadcast(sessionID, Event{Event: eventType, Data: msg.Payload})
                continue
            }
        }
        // Fallback
        h.Broadcast(sessionID, Event{Event: "message", Data: msg.Payload})
    }
}

// Handler returns a gin handler that streams SSE for a session.
func (h *Hub) Handler(sessionID string) gin.HandlerFunc {
    return func(c *gin.Context) {
        c.Writer.Header().Set("Content-Type", "text/event-stream")
        c.Writer.Header().Set("Cache-Control", "no-cache")
        c.Writer.Header().Set("Connection", "keep-alive")
        c.Writer.Header().Set("X-Accel-Buffering", "no")
        c.Writer.Flush()

        eventChan := h.Subscribe(sessionID)
        defer h.Unsubscribe(sessionID, eventChan)

        closeNotify := c.Writer.(http.CloseNotifier).CloseNotify()

        for {
            select {
            case event, ok := <-eventChan:
                if !ok {
                    return
                }
                fmt.Fprintf(c.Writer, "event: %s\n", event.Event)
                fmt.Fprintf(c.Writer, "data: %s\n\n", event.Data) // Data is already JSON string
                c.Writer.Flush()

            case <-closeNotify:
                return
            case <-c.Request.Context().Done():
                return
            }
        }
    }
}