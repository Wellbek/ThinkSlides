package sse

import (
	"sync"
	"fmt"
	"net/http"

	"github.com/gin-gonic/gin"
)

// Event represents a single SSE event to be broadcast.
type Event struct {
	Event string      // event type (thought, tool_call, tool_result, etc.)
	Data  interface{} // event payload
}

// Hub manages SSE subscriptions per session.
// Each session can have multiple subscribers (e.g., browser tabs).
type Hub struct {
	mu          sync.RWMutex
	subscribers map[string]map[chan Event]struct{}
}

// NewHub creates a new SSE hub.
func NewHub() *Hub {
	return &Hub{
		subscribers: make(map[string]map[chan Event]struct{}),
	}
}

// Subscribe registers a channel to receive events for a session.
func (h *Hub) Subscribe(sessionID string) chan Event {
	h.mu.Lock()
	defer h.mu.Unlock()

	ch := make(chan Event, 64) // Buffered channel to avoid blocking

	if h.subscribers[sessionID] == nil {
		h.subscribers[sessionID] = make(map[chan Event]struct{})
	}
	h.subscribers[sessionID][ch] = struct{}{}

	return ch
}

// Unsubscribe removes a channel from receiving events for a session.
func (h *Hub) Unsubscribe(sessionID string, ch chan Event) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if subs, ok := h.subscribers[sessionID]; ok {
		delete(subs, ch)
		if len(subs) == 0 {
			delete(h.subscribers, sessionID)
		}
	}
	close(ch)
}

// Broadcast sends an event to all subscribers of a session.
func (h *Hub) Broadcast(sessionID string, event Event) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	if subs, ok := h.subscribers[sessionID]; ok {
		for ch := range subs {
			select {
			case ch <- event:
				// Event sent successfully
			default:
				// Channel full — drop event to avoid blocking
				// In production, log this as a warning
			}
		}
	}
}

// Run starts the hub (placeholder for any periodic cleanup).
func (h *Hub) Run() {
	// No-op — broadcast is push-based via Broadcast()
	// Could add periodic cleanup of stale subscribers here
}

// Handler returns a gin.HandlerFunc that streams SSE events for a session.
func Handler(hub *Hub, sessionID string) gin.HandlerFunc {
	return func(c *gin.Context) {
		// Set SSE headers
		c.Writer.Header().Set("Content-Type", "text/event-stream")
		c.Writer.Header().Set("Cache-Control", "no-cache")
		c.Writer.Header().Set("Connection", "keep-alive")
		c.Writer.Header().Set("X-Accel-Buffering", "no") // Disable nginx buffering
		c.Writer.Flush()

		// Subscribe to the hub
		eventChan := hub.Subscribe(sessionID)
		defer hub.Unsubscribe(sessionID, eventChan)

		// Create a close notifier for client disconnect
		closeNotify := c.Writer.(http.CloseNotifier).CloseNotify()

		for {
			select {
			case event, ok := <-eventChan:
				if !ok {
					// Channel closed, exit
					return
				}
				// Write SSE event
				fmt.Fprintf(c.Writer, "event: %s\n", event.Event)
				fmt.Fprintf(c.Writer, "data: %v\n\n", event.Data) // You may want to JSON encode Data
				c.Writer.Flush()

			case <-closeNotify:
				// Client disconnected
				return

			case <-c.Request.Context().Done():
				// Request context cancelled
				return
			}
		}
	}
}