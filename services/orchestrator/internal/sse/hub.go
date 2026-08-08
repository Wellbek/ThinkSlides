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
	listeners   map[string]context.CancelFunc
	redisClient *redis.Client
}

// NewHub accepts a Redis client.
func NewHub(redisClient *redis.Client) *Hub {
	return &Hub{
		subscribers: make(map[string]map[chan Event]struct{}),
		listeners:   make(map[string]context.CancelFunc),
		redisClient: redisClient,
	}
}

// Subscribe registers a channel and starts listening to Redis for that session.
//
// The Redis listener's lifetime is bound to the subscriber set: it runs while
// at least one client is attached and is cancelled when the last one leaves.
// It deliberately does not inspect event contents to decide when to stop. An
// agent run pauses and resumes (ask_user_question), so "the stream said done"
// is not the same as "this session will never emit again" - ending the listener
// on a done event left sessions with live subscribers and nothing feeding them,
// silently dropping every event until the user reloaded the page.
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
			ctx, cancel := context.WithCancel(context.Background())
			h.listeners[sessionID] = cancel
			go h.listenToRedis(ctx, sessionID)
		}
	}
	h.subscribers[sessionID][ch] = struct{}{}
	return ch
}

// Unsubscribe removes a channel and cleans up. When the last subscriber for a
// session leaves, the session's Redis listener is cancelled so neither the
// goroutine nor the Redis subscription outlives the clients it feeds.
func (h *Hub) Unsubscribe(sessionID string, ch chan Event) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if subs, ok := h.subscribers[sessionID]; ok {
		delete(subs, ch)
		if len(subs) == 0 {
			delete(h.subscribers, sessionID)
			if cancel, ok := h.listeners[sessionID]; ok {
				delete(h.listeners, sessionID)
				cancel()
			}
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

// listenToRedis subscribes to the Redis channel and forwards messages until
// ctx is cancelled by the last subscriber leaving.
func (h *Hub) listenToRedis(ctx context.Context, sessionID string) {
	channel := fmt.Sprintf("agent:events:%s", sessionID)
	pubsub := h.redisClient.Subscribe(ctx, channel)
	defer pubsub.Close()

	// Wait for subscription confirmation to catch errors early
	if _, err := pubsub.Receive(ctx); err != nil {
		log.Printf("SSE: Redis subscribe error for session %s: %v", sessionID, err)
		return
	}
	log.Printf("SSE: Redis listener started for session %s", sessionID)
	defer log.Printf("SSE: Redis listener stopped for session %s", sessionID)

	ch := pubsub.Channel()
	for {
		select {
		case <-ctx.Done():
			return
		case msg, ok := <-ch:
			if !ok {
				return
			}
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
		}
	}
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
