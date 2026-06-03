package handler

import (
	"context"
	"encoding/json"
	"fmt"
	// "io"
	"net/http"
	"time"
	"log"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/slideagent/orchestrator/internal/client"
	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/sse"
)

type SessionHandler struct {
	queue    *queue.RedisQueue
	hub      *sse.Hub
	aiClient *client.AIServiceClient
	dbURL    string
	pool     *pgxpool.Pool
}

func NewSessionHandler(q *queue.RedisQueue, hub *sse.Hub, aiClient *client.AIServiceClient, dbURL string) *SessionHandler {
    // Create pool synchronously
    ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
    defer cancel()
    
    pool, err := pgxpool.New(ctx, dbURL)
    if err != nil {
        log.Printf("Failed to create connection pool: %v", err)
    }
    
    return &SessionHandler{
        queue:    q,
        hub:      hub,
        aiClient: aiClient,
        dbURL:    dbURL,
        pool:     pool,
    }
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
			"id":          id,
			"title":       title,
			"slide_count": slideCount,
			"updated_at":  updatedAt.Format(time.RFC3339),
			"created_at":  createdAt.Format(time.RFC3339),
		})
	}

	c.JSON(http.StatusOK, sessions)
}

// CreateSession creates a new chat session.
// Users without an organisation must create or join one first.
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

	// Users without an org must create or join one first.
	if orgID == "" {
		c.JSON(http.StatusForbidden, gin.H{
			"error": "You must belong to an organisation to create sessions. Create or join one in Settings.",
		})
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
		"id":         sessionID,
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
				"created_at":  msgCreatedAt.Format(time.RFC3339),
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
				"id":         id,
				"event_type": eventType,
				"phase":      phase,
				"content":    contentData,
				"created_at": evtCreatedAt.Format(time.RFC3339),
			}
			if toolName != nil {
				evt["tool_name"] = *toolName
			}
			events = append(events, evt)
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"id":              sessionID,
		"title":           title,
		"slide_count":     slideCount,
		"messages":        messages,
		"events":          events,
		"generated_files": []interface{}{},
		"created_at":      createdAt.Format(time.RFC3339),
		"updated_at":      updatedAt.Format(time.RFC3339),
	})
}

// GetSessionMessages returns just the messages for a session.
func (h *SessionHandler) GetSessionMessages(c *gin.Context) {
	sessionID := c.Param("id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()

	// Verify session exists (implicit org check via auth middleware scoping)
	var exists int
	err := h.pool.QueryRow(ctx,
		`SELECT 1 FROM chat_sessions WHERE id = $1`, sessionID,
	).Scan(&exists)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Session not found"})
		return
	}

	rows, err := h.pool.Query(ctx,
		`SELECT id, role, content, attachments, created_at
		 FROM chat_messages
		 WHERE session_id = $1
		 ORDER BY created_at`, sessionID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Query failed"})
		return
	}
	defer rows.Close()

	messages := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, role, content string
		var attachments []byte
		var createdAt time.Time
		if err := rows.Scan(&id, &role, &content, &attachments, &createdAt); err != nil {
			continue
		}
		var atts interface{}
		json.Unmarshal(attachments, &atts)
		messages = append(messages, gin.H{
			"id":          id,
			"role":        role,
			"content":     content,
			"attachments": atts,
			"created_at":  createdAt.Format(time.RFC3339),
		})
	}

	c.JSON(http.StatusOK, messages)
}

// GetSessionFiles returns the generated files for a session.
func (h *SessionHandler) GetSessionFiles(c *gin.Context) {
	sessionID := c.Param("id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()

	// Verify session exists
	var exists int
	err := h.pool.QueryRow(ctx,
		`SELECT 1 FROM chat_sessions WHERE id = $1`, sessionID,
	).Scan(&exists)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Session not found"})
		return
	}

	rows, err := h.pool.Query(ctx,
		`SELECT id, session_id, file_type, file_path, original_filename, file_size_bytes, created_at
		 FROM generated_files
		 WHERE session_id = $1
		 ORDER BY created_at`, sessionID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Query failed"})
		return
	}
	defer rows.Close()

	files := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, sessionID, fileType, filePath string
		var originalFilename *string
		var fileSizeBytes *int64
		var createdAt time.Time
		if err := rows.Scan(&id, &sessionID, &fileType, &filePath, &originalFilename, &fileSizeBytes, &createdAt); err != nil {
			continue
		}
		f := gin.H{
			"id":         id,
			"session_id": sessionID,
			"file_type":  fileType,
			"file_path":  filePath,
			"created_at": createdAt.Format(time.RFC3339),
		}
		if originalFilename != nil {
			f["filename"] = *originalFilename
		}
		if fileSizeBytes != nil {
			f["file_size"] = *fileSizeBytes
		}
		files = append(files, f)
	}

	c.JSON(http.StatusOK, files)
}

// ListTemplates returns all templates for the current user's organisation.
func (h *SessionHandler) ListTemplates(c *gin.Context) {
	orgID := c.GetString("org_id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	if orgID == "" {
		c.JSON(http.StatusOK, []interface{}{})
		return
	}

	ctx := c.Request.Context()
	rows, err := h.pool.Query(ctx,
		`SELECT id, name, COALESCE(description, ''), tags, version, is_default, created_at, updated_at
		 FROM templates
		 WHERE organisation_id = $1
		 ORDER BY created_at DESC`, orgID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Query failed"})
		return
	}
	defer rows.Close()

	templates := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, name, description string
		var tags []string
		var version int
		var isDefault bool
		var createdAt, updatedAt time.Time
		if err := rows.Scan(&id, &name, &description, &tags, &version, &isDefault, &createdAt, &updatedAt); err != nil {
			continue
		}
		templates = append(templates, gin.H{
			"id":          id,
			"name":        name,
			"description": description,
			"tags":        tags,
			"version":     version,
			"is_default":  isDefault,
			"created_at":  createdAt.Format(time.RFC3339),
			"updated_at":  updatedAt.Format(time.RFC3339),
		})
	}

	c.JSON(http.StatusOK, templates)
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
// It persists both the user message and an empty assistant placeholder to the
// database so that the frontend can replace its optimistic messages with the
// server-assigned IDs immediately.
func (h *SessionHandler) SendMessage(c *gin.Context) {
	sessionID := c.Param("id")
	// userID := c.GetString("user_id")

	var req struct {
		Content     string                   `json:"content" binding:"required"`
		Attachments []map[string]interface{} `json:"attachments"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()

	// Persist user message
	userMsgID := uuid.New().String()
	_, err := h.pool.Exec(ctx,
		`INSERT INTO chat_messages (id, session_id, role, content, attachments, created_at)
		 VALUES ($1, $2, 'user', $3, $4, NOW())`,
		userMsgID, sessionID, req.Content, toJSON(req.Attachments),
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save user message"})
		return
	}

	// Persist assistant placeholder (empty content, will be filled later)
	assistantMsgID := uuid.New().String()
	_, err = h.pool.Exec(ctx,
		`INSERT INTO chat_messages (id, session_id, role, content, attachments, created_at)
		 VALUES ($1, $2, 'assistant', '', '[]', NOW())`,
		assistantMsgID, sessionID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save assistant placeholder"})
		return
	}

	// Enqueue generation job
	jobID := uuid.New().String()
	job := queue.Job{
		ID:        jobID,
		SessionID: sessionID,
		Type:      "generate",
		Payload: map[string]interface{}{
			"content":             req.Content,
			"attachments":         req.Attachments,
			"user_message_id":     userMsgID,
			"assistant_message_id": assistantMsgID,
		},
	}

	if err := h.queue.Enqueue(job); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to enqueue job"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{
		"messages": []map[string]interface{}{
			{
				"id":         userMsgID,
				"session_id": sessionID,
				"role":       "user",
				"content":    req.Content,
				"created_at": time.Now().Format(time.RFC3339),
			},
			{
				"id":         assistantMsgID,
				"session_id": sessionID,
				"role":       "assistant",
				"content":    "",
				"created_at": time.Now().Format(time.RFC3339),
			},
		},
		"job_id": jobID,
	})
}

// StreamEvents handles SSE connections for real-time agent event streaming.
func (h *SessionHandler) StreamEvents(c *gin.Context) {
	sessionID := c.Param("id")
	log.Printf("SSE: StreamEvents entered session=%s", sessionID)

	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")

	eventChan := h.hub.Subscribe(sessionID)
	defer h.hub.Unsubscribe(sessionID, eventChan)

	// Replay historical events from database
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
				log.Printf("SSE: replaying event=%s", eventType)
				fmt.Fprintf(c.Writer, "event: %s\n", eventType)
				fmt.Fprintf(c.Writer, "data: %s\n\n", content)
				c.Writer.(http.Flusher).Flush()
			}
		}
	}

	// Stream live events
	// c.Stream(func(w io.Writer) bool {
	// 	select {
	// 	case event, ok := <-eventChan:
	// 		if !ok {
	// 			return false
	// 		}
	// 		c.SSEvent(event.Event, event.Data)
	// 		return true
	// 	case <-c.Request.Context().Done():
	// 		return false
	// 	}
	// })

	ctx := c.Request.Context()

	for {
		select {
		case event, ok := <-eventChan:
			if !ok {
				return
			}

			log.Printf("SSE: writing event=%s", event.Event)

			fmt.Fprintf(c.Writer, "event: %s\n", event.Event)
			fmt.Fprintf(c.Writer, "data: %s\n\n", event.Data)

			c.Writer.Flush()

		case <-ctx.Done():
			return
		}
	}
}

// toJSON is a small helper that marshals a value to JSON, returning a empty
// JSON array on error.
func toJSON(v interface{}) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "[]"
	}
	return string(b)
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
