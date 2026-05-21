package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/sse"
)

type SessionHandler struct {
	queue     *queue.RedisQueue
	hub       *sse.Hub
	aiClient  *AIServiceClient
	dbURL     string
	pool      *pgxpool.Pool
}

func NewSessionHandler(q *queue.RedisQueue, hub *sse.Hub, aiClient *AIServiceClient, dbURL string) *SessionHandler {
	h := &SessionHandler{
		queue:    q,
		hub:      hub,
		aiClient: aiClient,
		dbURL:    dbURL,
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		pool, err := pgxpool.New(ctx, dbURL)
		if err == nil {
			h.pool = pool
		}
	}()
	return h
}

// ListSessions returns all chat sessions for the current user.
func (h *SessionHandler) ListSessions(c *gin.Context) {
	userID := c.GetString("user_id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()
	rows, err := h.pool.Query(ctx,
		`SELECT id, title, slide_count, updated_at, created_at
		 FROM chat_sessions
		 WHERE user_id = $1
		 ORDER BY updated_at DESC
		 LIMIT 100`, userID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Query failed"})
		return
	}
	defer rows.Close()

	sessions := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, title string
		var slideCount int
		var updatedAt, createdAt time.Time
		if err := rows.Scan(&id, &title, &slideCount, &updatedAt, &createdAt); err != nil {
			continue
		}
		sessions = append(sessions, gin.H{
			"id":         id,
			"title":      title,
			"slideCount": slideCount,
			"updatedAt":  updatedAt.Format(time.RFC3339),
			"createdAt":  createdAt.Format(time.RFC3339),
		})
	}

	c.JSON(http.StatusOK, sessions)
}

// CreateSession creates a new chat session.
func (h *SessionHandler) CreateSession(c *gin.Context) {
	var req struct {
		Title      *string `json:"title"`
		TemplateID *string `json:"template_id"`
		Brief      *string `json:"brief"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	userID := c.GetString("user_id")
	orgID := c.GetString("org_id")

	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	title := "New Chat"
	if req.Title != nil {
		title = *req.Title
	}

	ctx := c.Request.Context()
	var sessionID string
	err := h.pool.QueryRow(ctx,
		`INSERT INTO chat_sessions (organisation_id, user_id, title, template_id)
		 VALUES ($1, $2, $3, $4)
		 RETURNING id`,
		orgID, userID, title, req.TemplateID,
	).Scan(&sessionID)

	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create session"})
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"session_id": sessionID,
		"title":      title,
		"created_at": time.Now().Format(time.RFC3339),
	})
}

// GetSession returns full session detail with messages, events, and files.
func (h *SessionHandler) GetSession(c *gin.Context) {
	sessionID := c.Param("id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()

	// Get session
	var title string
	var slideCount int
	var createdAt, updatedAt time.Time
	err := h.pool.QueryRow(ctx,
		`SELECT title, slide_count, created_at, updated_at
		 FROM chat_sessions WHERE id = $1`, sessionID,
	).Scan(&title, &slideCount, &createdAt, &updatedAt)

	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Session not found"})
		return
	}

	// Get messages
	msgRows, err := h.pool.Query(ctx,
		`SELECT id, role, content, attachments, created_at
		 FROM chat_messages
		 WHERE session_id = $1
		 ORDER BY created_at`, sessionID,
	)
	messages := make([]map[string]interface{}, 0)
	if err == nil {
		defer msgRows.Close()
		for msgRows.Next() {
			var id, role, content string
			var attachments []byte
			var msgCreatedAt time.Time
			if err := msgRows.Scan(&id, &role, &content, &attachments, &msgCreatedAt); err != nil {
				continue
			}
			var atts interface{}
			json.Unmarshal(attachments, &atts)
			messages = append(messages, gin.H{
				"id":          id,
				"role":        role,
				"content":     content,
				"attachments": atts,
				"createdAt":   msgCreatedAt.Format(time.RFC3339),
			})
		}
	}

	// Get events
	evtRows, err := h.pool.Query(ctx,
		`SELECT id, event_type, phase, content, tool_name, created_at
		 FROM agent_events
		 WHERE session_id = $1
		 ORDER BY created_at`, sessionID,
	)
	events := make([]map[string]interface{}, 0)
	if err == nil {
		defer evtRows.Close()
		for evtRows.Next() {
			var id, eventType, phase string
			var content []byte
			var toolName *string
			var evtCreatedAt time.Time
			if err := evtRows.Scan(&id, &eventType, &phase, &content, &toolName, &evtCreatedAt); err != nil {
				continue
			}
			var contentData interface{}
			json.Unmarshal(content, &contentData)
			evt := gin.H{
				"id":        id,
				"eventType": eventType,
				"phase":     phase,
				"content":   contentData,
				"createdAt": evtCreatedAt.Format(time.RFC3339),
			}
			if toolName != nil {
				evt["toolName"] = *toolName
			}
			events = append(events, evt)
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"id":              sessionID,
		"title":           title,
		"slideCount":      slideCount,
		"messages":        messages,
		"events":          events,
		"generatedFiles":  []interface{}{},
		"createdAt":       createdAt.Format(time.RFC3339),
		"updatedAt":       updatedAt.Format(time.RFC3339),
	})
}

// DeleteSession deletes a chat session.
func (h *SessionHandler) DeleteSession(c *gin.Context) {
	sessionID := c.Param("id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()
	_, err := h.pool.Exec(ctx, `DELETE FROM chat_sessions WHERE id = $1`, sessionID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Delete failed"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"deleted": true})
}

// SendMessage sends a message and enqueues a generation job.
func (h *SessionHandler) SendMessage(c *gin.Context) {
	sessionID := c.Param("id")

	var req struct {
		Content     string                   `json:"content" binding:"required"`
		Attachments []map[string]interface{} `json:"attachments"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	// Enqueue job to Redis
	jobID := uuid.New().String()
	job := queue.Job{
		ID:        jobID,
		SessionID: sessionID,
		Type:      "generate",
		Payload: map[string]interface{}{
			"content":     req.Content,
			"attachments": req.Attachments,
		},
	}

	if err := h.queue.Enqueue(job); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to enqueue job"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{
		"message_id": jobID,
		"session_id": sessionID,
		"job_id":     jobID,
	})
}

// StreamEvents handles SSE connections for real-time agent event streaming.
func (h *SessionHandler) StreamEvents(c *gin.Context) {
	sessionID := c.Param("id")

	// Set SSE headers
	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")

	// Subscribe to events for this session
	eventChan := h.hub.Subscribe(sessionID)
	defer h.hub.Unsubscribe(sessionID, eventChan)

	// Stream existing events from database first (if reconnecting)
	if h.pool != nil {
		ctx := c.Request.Context()
		rows, err := h.pool.Query(ctx,
			`SELECT event_type, content, created_at
			 FROM agent_events
			 WHERE session_id = $1
			 ORDER BY created_at
			 LIMIT 100`, sessionID,
		)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var eventType string
				var content []byte
				var createdAt time.Time
				if err := rows.Scan(&eventType, &content, &createdAt); err != nil {
					continue
				}
				fmt.Fprintf(c.Writer, "event: %s\n", eventType)
				fmt.Fprintf(c.Writer, "data: %s\n\n", content)
				c.Writer.(http.Flusher).Flush()
			}
		}
	}

	// Stream live events
	c.Stream(func(w io.Writer) bool {
		select {
		case event, ok := <-eventChan:
			if !ok {
				return false
			}
			c.SSEvent(event.Event, event.Data)
			return true
		case <-c.Request.Context().Done():
			return false
		}
	})
}

// DownloadFile serves a generated file for download.
func (h *SessionHandler) DownloadFile(c *gin.Context) {
	fileID := c.Param("id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()
	var filePath, fileName string
	err := h.pool.QueryRow(ctx,
		`SELECT file_path, original_filename FROM generated_files WHERE id = $1`, fileID,
	).Scan(&filePath, &fileName)

	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "File not found"})
		return
	}

	c.FileAttachment(filePath, fileName)
}