package sse

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync"
	// "time"

	"github.com/gin-gonic/gin"
	// "github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type Event struct {
	Event string      // event type (thought, tool_call, tool_result, etc.)
	Data  interface{} // payload (will be JSON)
}

type Hub struct {
	mu          sync.RWMutex
	subscribers map[string]map[chan Event]struct{}
	redisClient *redis.Client
}

// NewHub accepts a Redis client.
func NewHub(redisClient *redis.Client) *Hub {
	return &Hub{
		subscribers: make(map[string]map[chan Event]struct{}),
		redisClient: redisClient,
	}
}

// Subscribe registers a channel and starts listening to Redis for that session.
func (h *Hub) Subscribe(sessionID string) chan Event {
	log.Printf("SSE: Subscribe called for session %s", sessionID)
	h.mu.Lock()
	defer h.mu.Unlock()

	ch := make(chan Event, 64)

	if h.subscribers[sessionID] == nil {
		h.subscribers[sessionID] = make(map[chan Event]struct{})
		// Start Redis listener only for the first subscriber. A nil client
		// means local-only fan-out (direct Broadcast, and tests) - subscribing
		// on it would panic.
		if h.redisClient != nil {
			go h.listenToRedis(sessionID)
		}
	}
	h.subscribers[sessionID][ch] = struct{}{}
	return ch
}

// Unsubscribe removes a channel and cleans up.
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

// Broadcast sends an event to all local subscribers.
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

// listenToRedis subscribes to the Redis channel and forwards messages.
func (h *Hub) listenToRedis(sessionID string) {
	ctx := context.Background()
	channel := fmt.Sprintf("agent:events:%s", sessionID)
	pubsub := h.redisClient.Subscribe(ctx, channel)
	defer pubsub.Close()

	// Wait for subscription confirmation to catch errors early
	if _, err := pubsub.Receive(ctx); err != nil {
		log.Printf("SSE: Redis subscribe error for session %s: %v", sessionID, err)
		return
	}
	log.Printf("SSE: Redis listener started for session %s", sessionID)

	ch := pubsub.Channel()
	for msg := range ch {
		var raw map[string]interface{}
		if err := json.Unmarshal([]byte(msg.Payload), &raw); err != nil {
			log.Printf("SSE: malformed JSON for session %s: %v", sessionID, err)
			continue
		}
		eventType, _ := raw["type"].(string)
		if eventType == "" {
			eventType = "message" // fallback
		}

		// Broadcast to all local SSE subscribers
		h.Broadcast(sessionID, Event{Event: eventType, Data: msg.Payload})

		// If this is the final "done" event, we can stop listening
		// BUT: don't stop if paused=True (workflow will resume)
		if eventType == "done" {
			isPaused, _ := raw["paused"].(bool)
			if !isPaused {
				log.Printf("SSE: 'done' event received for session %s (not paused), stopping listener", sessionID)
				return
			}
			log.Printf("SSE: 'done' event received for session %s (paused), continuing listener", sessionID)
		}
	}
	log.Printf("SSE: Redis listener stopped for session %s", sessionID)
}

// Handler returns a Gin handler that streams SSE for a session.
// The sessionID is extracted from the URL parameter "sessionId".
func (h *Hub) Handler() gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("id")
		if sessionID == "" {
			c.JSON(http.StatusBadRequest, gin.H{"error": "sessionId required"})
			return
		}

		c.Writer.Header().Set("Content-Type", "text/event-stream")
		c.Writer.Header().Set("Cache-Control", "no-cache")
		c.Writer.Header().Set("Connection", "keep-alive")
		c.Writer.Header().Set("X-Accel-Buffering", "no")
		c.Status(http.StatusOK)
		c.Writer.Flush()

		eventChan := h.Subscribe(sessionID)
		defer h.Unsubscribe(sessionID, eventChan)

		ctx := c.Request.Context()
		for {
			select {
			case event, ok := <-eventChan:
				if !ok {
					return
				}
				fmt.Fprintf(c.Writer, "event: %s\n", event.Event)
				fmt.Fprintf(c.Writer, "data: %s\n\n", event.Data)
				c.Writer.Flush()

			case <-ctx.Done():
				return
			}
		}
	}
}
