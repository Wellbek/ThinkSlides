package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"github.com/thinkslides/orchestrator/internal/client"
	"github.com/thinkslides/orchestrator/internal/queue"
	"github.com/thinkslides/orchestrator/internal/sse"
)

type SessionHandler struct {
	queue       *queue.RedisQueue
	hub         *sse.Hub
	aiClient    *client.AIServiceClient
	dbURL       string
	pool        *pgxpool.Pool
	redisClient *redis.Client
}

func NewSessionHandler(q *queue.RedisQueue, hub *sse.Hub, aiClient *client.AIServiceClient, dbURL string, redisClient *redis.Client) *SessionHandler {
	// Create pool synchronously
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	pool, err := pgxpool.New(ctx, dbURL)
	if err != nil {
		log.Printf("Failed to create connection pool: %v", err)
	}

	return &SessionHandler{
		queue:       q,
		hub:         hub,
		aiClient:    aiClient,
		dbURL:       dbURL,
		pool:        pool,
		redisClient: redisClient,
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
	messageOrder := make([]string, 0)
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
		messageOrder = append(messageOrder, id)
	}

	// Attach the persisted live-shape event stream (raw_event) to each
	// message so the chat event stream, tool calls, and cited sources can be
	// reconstructed when the session is reloaded (not just during live SSE).
	eventsByMessage := make(map[string][]interface{})
	evtRows, evtErr := h.pool.Query(ctx,
		`SELECT message_id, raw_event
		 FROM agent_events
		 WHERE session_id = $1 AND message_id IS NOT NULL AND raw_event IS NOT NULL
		 ORDER BY created_at`, sessionID,
	)
	if evtErr == nil {
		for evtRows.Next() {
			var msgID string
			var raw []byte
			if err := evtRows.Scan(&msgID, &raw); err != nil {
				continue
			}
			var evt interface{}
			if err := json.Unmarshal(raw, &evt); err != nil {
				continue
			}
			eventsByMessage[msgID] = append(eventsByMessage[msgID], evt)
		}
		evtRows.Close()
	}
	for i, id := range messageOrder {
		if evts, ok := eventsByMessage[id]; ok && len(evts) > 0 {
			messages[i]["agent_event_stream"] = evts
		}
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
		`SELECT id, session_id, file_type, file_path, original_filename, file_size_bytes, metadata, created_at
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
		var metadataRaw []byte
		var createdAt time.Time
		if err := rows.Scan(&id, &sessionID, &fileType, &filePath, &originalFilename, &fileSizeBytes, &metadataRaw, &createdAt); err != nil {
			continue
		}
		f := gin.H{
			"id":         id,
			"session_id": sessionID,
			"file_type":  fileType,
			"file_path":  filePath,
			"file_url":   fmt.Sprintf("/api/files/%s", id),
			"created_at": createdAt.Format(time.RFC3339),
		}
		if originalFilename != nil {
			f["filename"] = *originalFilename
		} else {
			f["filename"] = "presentation.pptx"
		}
		if fileSizeBytes != nil {
			f["file_size"] = *fileSizeBytes
		}
		// slide_index (and any other per-asset context) lives in metadata -
		// individual chart/image assets set it, the whole-deck pptx row
		// doesn't (see #187).
		if len(metadataRaw) > 0 {
			var metadata map[string]interface{}
			if err := json.Unmarshal(metadataRaw, &metadata); err == nil {
				if slideIndex, ok := metadata["slideIndex"]; ok && slideIndex != nil {
					f["slide_index"] = slideIndex
				}
			}
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

// UploadTemplate handles template file upload and design system extraction.
func (h *SessionHandler) UploadTemplate(c *gin.Context) {
	// Get org ID from context
	orgID := c.GetString("org_id")
	if orgID == "" {
		c.JSON(http.StatusForbidden, gin.H{"error": "No organisation associated with user"})
		return
	}

	// Parse multipart form
	file, header, err := c.Request.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "No file provided"})
		return
	}
	defer file.Close()

	name := c.PostForm("name")
	if name == "" {
		name = header.Filename
	}
	description := c.PostForm("description")
	tagsStr := c.PostForm("tags")
	var tags []string
	if tagsStr != "" {
		json.Unmarshal([]byte(tagsStr), &tags)
	}

	// Save file to templates directory
	templateDir := os.Getenv("TEMPLATE_DIR")
	if templateDir == "" {
		templateDir = "/app/templates"
	}
	os.MkdirAll(templateDir, 0755)

	fileID := uuid.New().String()
	ext := ""
	if idx := strings.LastIndex(header.Filename, "."); idx >= 0 {
		ext = header.Filename[idx:]
	}
	filePath := filepath.Join(templateDir, fmt.Sprintf("%s%s", fileID, ext))

	if err := c.SaveUploadedFile(header, filePath); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save file"})
		return
	}

	// Insert template record
	ctx := c.Request.Context()
	var templateID string
	err = h.pool.QueryRow(ctx,
		`INSERT INTO templates (organisation_id, name, description, tags, file_path, version, is_default)
		 VALUES ($1, $2, $3, $4, $5, 1, false)
		 RETURNING id`,
		orgID, name, description, tags, filePath,
	).Scan(&templateID)
	if err != nil {
		os.Remove(filePath)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create template record"})
		return
	}

	// Extract design system via AI service
	designSystem, err := h.aiClient.ExtractTemplateDesignSystem(filePath)
	if err != nil {
		// Log error but continue - template still saved without design system
		log.Printf("Failed to extract design system for template %s: %v", templateID, err)
	} else {
		// Map camelCase keys from the AI extraction response to snake_case DB columns.
		// The extraction tool returns: palette, typography, spacing, layouts,
		// slideDimensions, masterElements, embeddedMedia.
		// The DB table has: palette, typography, spacing, layouts,
		// slide_dimensions, master_elements, embedded_media, raw_xml_snapshot.
		palette := toJSON(designSystem["palette"])
		typography := toJSON(designSystem["typography"])
		spacing := toJSON(designSystem["spacing"])
		layouts := toJSON(designSystem["layouts"])
		slideDimensions := toJSON(designSystem["slideDimensions"])
		masterElements := toJSON(designSystem["masterElements"])
		embeddedMedia := toJSON(designSystem["embeddedMedia"])

		_, err = h.pool.Exec(ctx,
			`INSERT INTO design_system_bundles
			 (template_id, version, palette, typography, spacing, layouts,
			  slide_dimensions, master_elements, embedded_media, raw_xml_snapshot)
			 VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8, $9)`,
			templateID, palette, typography, spacing, layouts,
			slideDimensions, masterElements, embeddedMedia, toJSON(designSystem),
		)
		if err != nil {
			log.Printf("Failed to save design system bundle for template %s: %v", templateID, err)
		}
	}

	c.JSON(http.StatusCreated, gin.H{
		"id":          templateID,
		"name":        name,
		"description": description,
		"tags":        tags,
		"file_path":   filePath,
	})
}

// DeleteTemplate removes a template and its design system bundles.
func (h *SessionHandler) DeleteTemplate(c *gin.Context) {
	templateID := c.Param("id")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()
	// First get file path to delete from disk
	var filePath string
	err := h.pool.QueryRow(ctx,
		`SELECT file_path FROM templates WHERE id = $1`, templateID,
	).Scan(&filePath)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Template not found"})
		return
	}

	// Delete template (cascade deletes design_system_bundles)
	_, err = h.pool.Exec(ctx, `DELETE FROM templates WHERE id = $1`, templateID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Delete failed"})
		return
	}

	// Delete file from disk
	if filePath != "" {
		os.Remove(filePath)
	}

	c.JSON(http.StatusOK, gin.H{"deleted": true})
}

// UpdateSessionTemplate updates the template_id for a session.
func (h *SessionHandler) UpdateSessionTemplate(c *gin.Context) {
	sessionID := c.Param("id")
	var req struct {
		TemplateID *string `json:"template_id"`
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
	_, err := h.pool.Exec(ctx,
		`UPDATE chat_sessions SET template_id = $1, updated_at = NOW() WHERE id = $2`,
		req.TemplateID, sessionID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Update failed"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"success": true})
}

// UpdateSessionTitle updates the title for a session.
func (h *SessionHandler) UpdateSessionTitle(c *gin.Context) {
	sessionID := c.Param("id")
	var req struct {
		Title string `json:"title" binding:"required"`
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
	_, err := h.pool.Exec(ctx,
		`UPDATE chat_sessions SET title = $1, updated_at = NOW() WHERE id = $2`,
		req.Title, sessionID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Update failed"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"success": true, "title": req.Title})
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
// It handles both JSON and multipart/form-data requests (for file uploads).
// It persists both the user message and an empty assistant placeholder to the
// database so that the frontend can replace its optimistic messages with the
// server-assigned IDs immediately.
func (h *SessionHandler) SendMessage(c *gin.Context) {
	sessionID := c.Param("id")

	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	contentType := c.GetHeader("Content-Type")
	var messageContent string
	var attachments []map[string]interface{}
	// Effort level: "simple" answers from the base model, "deep" enables
	// web-search-backed research. Anything unrecognised falls back to simple,
	// so an old client that sends nothing keeps working.
	mode := ModeSimple
	// Content depth requested for the generation. See detail_level.go.
	detailLevel := DetailLevelDetailed
	// Whether the deck should carry its research sources as references.
	includeSources := false

	// Handle multipart/form-data (file uploads) vs JSON
	if len(contentType) > 0 && contentType[:10] == "multipart/" {
		// Multipart form data
		form, err := c.MultipartForm()
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid multipart form"})
			return
		}

		// Extract content text field
		contentValues := form.Value["content"]
		if len(contentValues) > 0 {
			messageContent = contentValues[0]
		}

		// Effort level chosen in the composer.
		if modeValues := form.Value["mode"]; len(modeValues) > 0 {
			mode = modeValues[0]
		}

		// Content depth chosen in the composer.
		if levelValues := form.Value["detail_level"]; len(levelValues) > 0 {
			detailLevel = levelValues[0]
		}

		// "Include source references in the document" from the composer.
		if srcValues := form.Value["include_sources"]; len(srcValues) > 0 {
			includeSources = srcValues[0] == "true"
		}

		// Process uploaded files
		files := form.File["files"]
		uploadDir := os.Getenv("UPLOAD_DIR")
		if uploadDir == "" {
			uploadDir = "/app/uploads"
		}
		os.MkdirAll(uploadDir, 0755)

		for _, fileHeader := range files {
			fileID := uuid.New().String()
			ext := ""
			if idx := strings.LastIndex(fileHeader.Filename, "."); idx >= 0 {
				ext = fileHeader.Filename[idx:]
			}
			filePath := filepath.Join(uploadDir, fmt.Sprintf("%s%s", fileID, ext))

			if err := c.SaveUploadedFile(fileHeader, filePath); err != nil {
				log.Printf("Failed to save uploaded file %s: %v", fileHeader.Filename, err)
				continue
			}

			attachments = append(attachments, map[string]interface{}{
				"original_filename": fileHeader.Filename,
				"file_path":         filePath,
				"file_size_bytes":   fileHeader.Size,
				"mime_type":         fileHeader.Header.Get("Content-Type"),
				"upload_id":         fileID,
			})
		}
	} else {
		// JSON request
		var req struct {
			Content        string                   `json:"content" binding:"required"`
			Attachments    []map[string]interface{} `json:"attachments"`
			Mode           string                   `json:"mode"`
			DetailLevel    string                   `json:"detail_level"`
			IncludeSources bool                     `json:"include_sources"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
			return
		}
		messageContent = req.Content
		attachments = req.Attachments
		mode = req.Mode
		detailLevel = req.DetailLevel
		includeSources = req.IncludeSources
	}

	if messageContent == "" && len(attachments) == 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Message content or files required"})
		return
	}

	ctx := c.Request.Context()

	// Build attachment JSON for storage
	attachmentJSON := toJSON(attachments)

	// Persist user message
	userMsgID := uuid.New().String()
	_, err := h.pool.Exec(ctx,
		`INSERT INTO chat_messages (id, session_id, role, content, attachments, created_at)
		 VALUES ($1, $2, 'user', $3, $4, NOW())`,
		userMsgID, sessionID, messageContent, attachmentJSON,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save user message"})
		return
	}

	// Auto-generate title if this is the first user message in the session
	var existingMessageCount int
	err = h.pool.QueryRow(ctx,
		`SELECT COUNT(*) FROM chat_messages WHERE session_id = $1 AND role = 'user'`,
		sessionID,
	).Scan(&existingMessageCount)

	if err == nil && existingMessageCount == 1 && messageContent != "" {
		// This is the first user message - generate a title asynchronously
		go func() {
			// Use a separate context for the async task
			taskCtx := context.Background()
			title, titleErr := h.aiClient.GenerateTitle(messageContent)
			if titleErr == nil && title != "" {
				_, updateErr := h.pool.Exec(taskCtx,
					`UPDATE chat_sessions SET title = $1, updated_at = NOW() WHERE id = $2`,
					title, sessionID,
				)
				if updateErr != nil {
					log.Printf("Failed to update session title: %v", updateErr)
				} else {
					log.Printf("Auto-generated title '%s' for session %s", title, sessionID)
					// Notify connected SSE clients so the sidebar updates live.
					h.hub.Broadcast(sessionID, sse.Event{
						Event: "session_title_updated",
						Data:  toJSON(map[string]string{"session_id": sessionID, "title": title}),
					})
				}
			} else if titleErr != nil {
				log.Printf("Failed to generate title: %v", titleErr)
			}
		}()
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
	// Check if session is paused (has working_state in Redis)
	jobType := "generate"
	if h.redisClient != nil {
		ctx := c.Request.Context()
		workingStateKey := fmt.Sprintf("working_state:%s", sessionID)
		exists, err := h.redisClient.Exists(ctx, workingStateKey).Result()
		if err == nil && exists > 0 {
			log.Printf("Session %s is paused (has working_state), using resume job type", sessionID)
			jobType = "resume"
		}
	}

	jobID := uuid.New().String()
	job := queue.Job{
		ID:        jobID,
		SessionID: sessionID,
		Type:      jobType,
		Payload: map[string]interface{}{
			"content":              messageContent,
			"attachments":          attachments,
			"user_message_id":      userMsgID,
			"assistant_message_id": assistantMsgID,
			"mode":                 NormalizeMode(mode),
			"detail_level":         NormalizeDetailLevel(detailLevel),
			"include_sources":      includeSources,
			// A resume job answers a pending ask_user_question. The worker
			// reads the answer under its own keys, so send it under both -
			// previously only "content" was set and the resume posted an
			// empty answer to the AI service.
			"answer":          messageContent,
			"selected_option": messageContent,
			// The worker runs outside the request context, so the caller's
			// identity has to travel on the job for the AI service to know
			// whose provider settings to load.
			"org_id":  c.GetString("org_id"),
			"user_id": c.GetString("user_id"),
		},
	}

	if err := h.queue.Enqueue(job); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to enqueue job"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{
		"messages": []map[string]interface{}{
			{
				"id":          userMsgID,
				"session_id":  sessionID,
				"role":        "user",
				"content":     messageContent,
				"attachments": attachments,
				"created_at":  time.Now().Format(time.RFC3339),
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

	// Commit the response head immediately so EventSource reports the stream
	// as open even when the session is idle (an agent paused on a question can
	// sit silent for minutes before the next event).
	c.Status(http.StatusOK)
	c.Writer.Flush()

	// Past events are not replayed here. The messages endpoint already returns
	// each message's persisted agent_event_stream in its live shape, whereas
	// agent_events.content holds only an event's content field - replaying that
	// emitted payloads the client cannot interpret, and would duplicate the
	// history the client just loaded.

	ctx := c.Request.Context()
	heartbeat := time.NewTicker(20 * time.Second)
	defer heartbeat.Stop()

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

		case <-heartbeat.C:
			// A comment line keeps idle connections from being reaped by the
			// browser or any proxy in between, and surfaces a dead peer as a
			// write error so the client can reconnect.
			fmt.Fprint(c.Writer, ": ping\n\n")
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

	// c.FileAttachment sets the Content-Disposition header before checking
	// the file exists, so a stale DB row pointing at a since-removed file
	// downloaded as a tiny "404 page not found" text body under the
	// original .pptx filename - indistinguishable from a corrupt download.
	// Mirrors the AI service's own app/api/files.py, which already guards
	// this case.
	if _, err := os.Stat(filePath); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "File no longer exists on disk"})
		return
	}

	c.FileAttachment(filePath, fileName)
}

// ReplaceAsset handles a drag-dropped replacement for one generated chart or
// image asset (#187). Two of the confirmation dialog's three options land
// here directly - "asset_only" overwrites the existing row in place,
// "keep_both" adds the drop as a new, distinctly-named asset alongside it.
// The third option, replacing the asset in its slide too, goes through the
// agent-resume endpoint instead (see the image-replace-in-slide handler) -
// updating a slide is a job for the agent, not a plain file swap.
func (h *SessionHandler) ReplaceAsset(c *gin.Context) {
	sessionID := c.Param("id")
	assetID := c.Param("assetId")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	orgID := c.GetString("org_id")
	if orgID == "" {
		c.JSON(http.StatusForbidden, gin.H{"error": "No organisation associated with user"})
		return
	}

	scope := c.PostForm("scope")
	if scope != "asset_only" && scope != "keep_both" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "scope must be asset_only or keep_both"})
		return
	}

	file, header, err := c.Request.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "No file provided"})
		return
	}
	defer file.Close()

	ctx := c.Request.Context()

	// Scoped to the caller's own organisation, not just this session/asset
	// id pair - otherwise an authenticated user who knows or guesses
	// another org's session_id and asset_id could overwrite that org's
	// asset. A mismatch reads identically to "not found" so it doesn't leak
	// whether the id exists in someone else's org.
	var fileType, originalFilename string
	var metadataRaw []byte
	err = h.pool.QueryRow(ctx,
		`SELECT file_type, original_filename, metadata FROM generated_files
		 WHERE id = $1 AND session_id = $2 AND organisation_id = $3`, assetID, sessionID, orgID,
	).Scan(&fileType, &originalFilename, &metadataRaw)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Asset not found"})
		return
	}
	if fileType != "image" && fileType != "chart" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Only individual chart/image assets can be replaced this way"})
		return
	}

	outputDir := os.Getenv("OUTPUT_DIR")
	if outputDir == "" {
		outputDir = "/app/output"
	}
	assetsDir := filepath.Join(outputDir, sessionID, "assets")
	if err := os.MkdirAll(assetsDir, 0755); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not prepare asset storage"})
		return
	}

	ext := filepath.Ext(header.Filename)
	newID := uuid.New().String()
	newPath := filepath.Join(assetsDir, newID+ext)
	if err := c.SaveUploadedFile(header, newPath); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save uploaded file"})
		return
	}
	fileSize := header.Size

	if scope == "asset_only" {
		_, err = h.pool.Exec(ctx,
			`UPDATE generated_files SET file_path = $1, original_filename = $2, file_size_bytes = $3
			 WHERE id = $4`,
			newPath, header.Filename, fileSize, assetID,
		)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to update asset"})
			return
		}
		c.JSON(http.StatusOK, gin.H{
			"id": assetID, "file_url": fmt.Sprintf("/api/files/%s", assetID),
			"filename": header.Filename, "file_size": fileSize, "file_type": fileType,
		})
		return
	}

	// keep_both: name the new asset like a filesystem duplicate, numbered
	// against every other asset already sharing this base name in the
	// session, not just the one that was just dropped on.
	newFilename := nextDuplicateName(ctx, h.pool, sessionID, header.Filename)
	newFileID := uuid.New().String()
	_, err = h.pool.Exec(ctx,
		`INSERT INTO generated_files
		   (id, organisation_id, session_id, file_type, file_path, original_filename, file_size_bytes, metadata, created_at)
		 SELECT $1, organisation_id, $2, $3, $4, $5, $6, $7, NOW()
		 FROM chat_sessions WHERE id = $2`,
		newFileID, sessionID, fileType, newPath, newFilename, fileSize, metadataRaw,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save new asset"})
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"id": newFileID, "file_url": fmt.Sprintf("/api/files/%s", newFileID),
		"filename": newFilename, "file_size": fileSize, "file_type": fileType,
	})
}

// ImageReplaceInSlide saves a drag-dropped replacement image and asks the AI
// service to resume the agent so the slide using this asset is regenerated
// with it (#187's third dialog option). The heavier logic - reusing the
// image_catalog/slides_needing_revision resume machinery - lives entirely in
// the AI service, since only it has the agent loop and Redis working state;
// this handler's job is just the multipart upload and identity forwarding,
// the same division of labour SendMessage/AnswerQuestion already use.
func (h *SessionHandler) ImageReplaceInSlide(c *gin.Context) {
	sessionID := c.Param("id")
	assetID := c.Param("assetId")
	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	orgID := c.GetString("org_id")
	if orgID == "" {
		c.JSON(http.StatusForbidden, gin.H{"error": "No organisation associated with user"})
		return
	}

	file, header, err := c.Request.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "No file provided"})
		return
	}
	defer file.Close()

	ctx := c.Request.Context()

	// Scoped to the caller's own organisation - without this, an
	// authenticated user who knows or guesses another org's session_id and
	// asset_id could trigger a resume that regenerates that org's slide
	// content with an image they supplied.
	var fileType string
	var metadataRaw []byte
	err = h.pool.QueryRow(ctx,
		`SELECT file_type, metadata FROM generated_files
		 WHERE id = $1 AND session_id = $2 AND organisation_id = $3`,
		assetID, sessionID, orgID,
	).Scan(&fileType, &metadataRaw)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "Asset not found"})
		return
	}
	if fileType != "image" && fileType != "chart" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Only individual chart/image assets can be replaced this way"})
		return
	}
	var metadata map[string]interface{}
	if len(metadataRaw) > 0 {
		json.Unmarshal(metadataRaw, &metadata)
	}
	slideIndexFloat, ok := metadata["slideIndex"].(float64)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "This asset isn't tied to a slide"})
		return
	}
	slideIndex := int(slideIndexFloat)

	outputDir := os.Getenv("OUTPUT_DIR")
	if outputDir == "" {
		outputDir = "/app/output"
	}
	assetsDir := filepath.Join(outputDir, sessionID, "assets")
	if err := os.MkdirAll(assetsDir, 0755); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not prepare asset storage"})
		return
	}
	ext := filepath.Ext(header.Filename)
	newPath := filepath.Join(assetsDir, uuid.New().String()+ext)
	if err := c.SaveUploadedFile(header, newPath); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to save uploaded file"})
		return
	}

	identity := client.Identity{OrgID: orgID, UserID: c.GetString("user_id")}
	result, err := h.aiClient.ReplaceImageInSlide(
		sessionID, assetID, newPath, header.Filename, header.Size, slideIndex, identity,
	)
	if err != nil {
		if strings.Contains(err.Error(), "AI service error 409") {
			c.JSON(http.StatusConflict, gin.H{"error": extractDetail(err.Error())})
			return
		}
		log.Printf("ImageReplaceInSlide: AI service call failed: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to resume the agent for this slide"})
		return
	}

	c.JSON(http.StatusOK, result)
}

// extractDetail pulls a FastAPI-style {"detail": "..."} message out of the
// raw "AI service error <code>: <body>" text postJSON wraps errors in, so
// the browser sees the actual explanation instead of a generic failure.
func extractDetail(errText string) string {
	idx := strings.Index(errText, "{")
	if idx < 0 {
		return errText
	}
	var body struct {
		Detail string `json:"detail"`
	}
	if err := json.Unmarshal([]byte(errText[idx:]), &body); err != nil || body.Detail == "" {
		return errText
	}
	return body.Detail
}

// nextDuplicateName finds a filename like the given one that isn't already
// used by another asset in this session, numbering it the way a filesystem
// numbers a duplicate: "chart.png", then "chart (1).png", "chart (2).png".
func nextDuplicateName(ctx context.Context, pool *pgxpool.Pool, sessionID, filename string) string {
	base := strings.TrimSuffix(filename, filepath.Ext(filename))
	ext := filepath.Ext(filename)

	existing := map[string]bool{}
	rows, err := pool.Query(ctx,
		`SELECT original_filename FROM generated_files WHERE session_id = $1 AND original_filename IS NOT NULL`,
		sessionID,
	)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var name string
			if rows.Scan(&name) == nil {
				existing[name] = true
			}
		}
	}

	if !existing[filename] {
		return filename
	}
	for n := 1; ; n++ {
		candidate := fmt.Sprintf("%s (%d)%s", base, n, ext)
		if !existing[candidate] {
			return candidate
		}
	}
}
