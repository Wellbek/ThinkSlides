package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"

	"github.com/thinkslides/orchestrator/internal/client"
	"github.com/thinkslides/orchestrator/internal/queue"
	"github.com/thinkslides/orchestrator/internal/sse"
)

// stallTimeout is how long a job may go without any event from its session
// before the run is presumed dead. It is measured from the last event, not
// from the start of the job: a deck can legitimately take a long time, and a
// run paused on a question can wait indefinitely for a human, so neither is
// a reason to call the job failed. The clock does not run while paused.
const stallTimeout = 10 * time.Minute

// sessionWaiter is the single subscriber to one session's event stream.
//
// There used to be one per job. A paused run keeps its waiter listening (it
// must - the run is not over), so every resume spawned another subscriber on
// the same channel, and each of them ran saveAssistantMessage when the final
// done landed. One waiter per session, reference-counted by the jobs relying
// on it, gives the same behaviour with one writer.
type sessionWaiter struct {
	done   chan struct{} // closed on the first done event that is not paused
	cancel context.CancelFunc
	refs   int // jobs still waiting on this session

	mu           sync.Mutex
	paused       bool
	lastActivity time.Time
}

// state reports whether the run is parked on a question, and how long the
// session has been silent.
func (w *sessionWaiter) state() (paused bool, idle time.Duration) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.paused, time.Since(w.lastActivity)
}

func (w *sessionWaiter) noteActivity(paused bool) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.paused = paused
	w.lastActivity = time.Now()
}

// Processor continuously dequeues jobs and processes them by calling the AI service.
type Processor struct {
	queue       *queue.RedisQueue
	hub         *sse.Hub
	aiClient    *client.AIServiceClient
	redisClient *redis.Client // needed to listen for job completion
	dbPool      *pgxpool.Pool // database connection pool for saving assistant messages

	mu      sync.Mutex
	waiters map[string]*sessionWaiter
}

// NewProcessor creates a new job processor.
func NewProcessor(q *queue.RedisQueue, hub *sse.Hub, aiClient *client.AIServiceClient, redisClient *redis.Client, dbPool *pgxpool.Pool) *Processor {
	return &Processor{
		queue:       q,
		hub:         hub,
		aiClient:    aiClient,
		redisClient: redisClient,
		dbPool:      dbPool,
		waiters:     make(map[string]*sessionWaiter),
	}
}

// acquireWaiter returns the session's waiter, starting one if this is the
// first job for that session.
func (p *Processor) acquireWaiter(sessionID string) *sessionWaiter {
	p.mu.Lock()
	defer p.mu.Unlock()

	w, ok := p.waiters[sessionID]
	if !ok {
		ctx, cancel := context.WithCancel(context.Background())
		w = &sessionWaiter{
			done:         make(chan struct{}),
			cancel:       cancel,
			lastActivity: time.Now(),
		}
		p.waiters[sessionID] = w
		go p.waitForCompletion(ctx, sessionID, w)
	}
	w.refs++
	return w
}

// releaseWaiter drops one job's claim, tearing the subscription down once the
// session's last job has resolved.
func (p *Processor) releaseWaiter(sessionID string, w *sessionWaiter) {
	p.mu.Lock()
	defer p.mu.Unlock()

	w.refs--
	if w.refs > 0 {
		return
	}
	// Only remove the map entry if it is still this waiter: a session that
	// finished and started again already holds a newer one.
	if p.waiters[sessionID] == w {
		delete(p.waiters, sessionID)
	}
	w.cancel()
}

// Start begins the job processing loop.
func (p *Processor) Start(ctx context.Context) {
	log.Println("Job processor started")

	for {
		select {
		case <-ctx.Done():
			log.Println("Job processor stopped")
			return
		default:
		}

		job, err := p.queue.Dequeue(ctx)
		if err != nil {
			log.Printf("Dequeue error: %v", err)
			time.Sleep(2 * time.Second)
			continue
		}

		if job == nil {
			time.Sleep(500 * time.Millisecond)
			continue
		}

		log.Printf("Processing job %s (type=%s, session=%s)", job.ID, job.Type, job.SessionID)

		if err := p.queue.UpdateJobStatus(job.ID, "processing"); err != nil {
			log.Printf("Failed to update job status: %v", err)
		}

		// Process the job asynchronously – do not block the loop.
		go p.processJobAsync(ctx, job)
	}
}

func (p *Processor) processJobAsync(ctx context.Context, job *queue.Job) {
	// Start the AI job (non‑blocking HTTP call).
	if err := p.processJob(ctx, job); err != nil {
		log.Printf("Job %s failed to start: %v", job.ID, err)
		_ = p.queue.UpdateJobStatus(job.ID, "failed")

		errorData, _ := json.Marshal(map[string]interface{}{
			"type":      "error",
			"phase":     "content_analysis",
			"message":   err.Error(),
			"timestamp": time.Now().Format(time.RFC3339),
		})
		p.hub.Broadcast(job.SessionID, sse.Event{
			Event: "error",
			Data:  string(errorData),
		})
		return
	}

	// Wait for the "done" event from Redis, which signals the agent has
	// finished. All of a session's in-flight jobs share one subscription; a
	// resume job joins the waiter its generate job is already using.
	w := p.acquireWaiter(job.SessionID)
	defer p.releaseWaiter(job.SessionID, w)

	// Poll rather than arm a single long timer, so the deadline can account
	// for events arriving and for the run parking on a question.
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-w.done:
			log.Printf("Job %s completed (done event received)", job.ID)
			if err := p.queue.UpdateJobStatus(job.ID, "completed"); err != nil {
				log.Printf("Failed to update job status: %v", err)
			}
			return

		case <-ctx.Done():
			// The worker is shutting down. The job is not finished, but it is
			// not failed either - say nothing rather than record a verdict.
			log.Printf("Job %s abandoned: worker shutting down", job.ID)
			return

		case <-ticker.C:
			paused, idle := w.state()
			if paused {
				// Parked on a question. However long the user takes to answer
				// is their business; the previous code marked the job failed
				// after ten minutes of someone thinking.
				continue
			}
			if idle >= stallTimeout {
				log.Printf("Job %s failed: no events for %s", job.ID, idle.Round(time.Second))
				_ = p.queue.UpdateJobStatus(job.ID, "failed")
				return
			}
		}
	}
}

// waitForCompletion subscribes to Redis for the session's events, captures the
// final assistant reply, saves it to the database, and closes the waiter's
// done channel on the first done event that is not a pause.
//
// Exactly one of these runs per session, however many jobs are waiting on it.
func (p *Processor) waitForCompletion(ctx context.Context, sessionID string, w *sessionWaiter) {
	channel := "agent:events:" + sessionID
	pubsub := p.redisClient.Subscribe(ctx, channel)
	defer pubsub.Close()

	ch := pubsub.Channel()
	var assistantContent string
	var paused bool = false

	log.Printf("Session %s waiting for completion", sessionID)

	for {
		select {
		case <-ctx.Done():
			return
		case msg, ok := <-ch:
			if !ok {
				return
			}
			var event map[string]interface{}
			if err := json.Unmarshal([]byte(msg.Payload), &event); err != nil {
				continue
			}

			// Any event is proof the run is alive, which is what the stall
			// deadline is measured against.
			w.noteActivity(false)

			// DEBUG: Log all events to see what's coming
			// log.Printf("DEBUG - Event type: %v, keys: %v", event["type"], getKeys(event))

			eventType, _ := event["type"].(string)

			// Capture from slide_finalized event
			if eventType == "slide_finalized" {
				if content, ok := event["content"].(string); ok && content != "" {
					assistantContent = content
					// log.Printf("Captured content from slide_finalized: %s", content[:min(100, len(content))])
				}
				if result, ok := event["result"].(string); ok && result != "" {
					assistantContent = result
				}
				if presentation, ok := event["presentation"].(string); ok && presentation != "" {
					assistantContent = presentation
				}
			}

			if eventType == "tool_result" {
				if toolName, ok := event["tool_name"].(string); ok && toolName == "finalize_and_render_pptx" {
					if result, ok := event["result"].(string); ok && result != "" {
						assistantContent = result
						// log.Printf("Captured content from tool_result: %s", result[:min(100, len(result))])
					}
				}
			}

			if eventType == "thought" && assistantContent == "" {
				if phase, ok := event["phase"].(string); ok && phase == "complete" {
					if content, ok := event["content"].(string); ok && content != "" {
						assistantContent = content
					}
				}
			}

			if eventType == "done" {
				paused, _ = event["paused"].(bool)
				if assistantContent != "" {
					if err := p.saveAssistantMessage(sessionID, assistantContent); err != nil {
						// log.Printf("Failed to save assistant message for session %s: %v", sessionID, err)
					} else {
						// log.Printf("Saved assistant message for session %s (length: %d)", sessionID, len(assistantContent))
					}
				} else {
					// log.Printf("Warning: done event with no assistant content for session %s", sessionID)
					// Save a fallback message
					p.saveAssistantMessage(sessionID, "Presentation generation completed. Check the preview panel for your slides.")
				}

				if !paused {
					// Only close done channel if not paused
					close(w.done)
					return
				}
				// The run is parked on a question. Keep listening - the resume
				// will come back on this same subscription - and stop the
				// stall clock, because waiting on a human is not a stall.
				w.noteActivity(true)
				log.Printf("Session %s: done event with paused=true, continuing to listen", sessionID)
			}
		}
	}
}

// Helper function for debugging
func getKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func (p *Processor) saveAssistantMessage(sessionID, content string) error {
	if p.dbPool == nil {
		log.Printf("saveAssistantMessage: database pool is nil for session %s", sessionID)
		return fmt.Errorf("database pool not initialized")
	}
	ctx := context.Background()

	result, err := p.dbPool.Exec(ctx,
		`UPDATE chat_messages
		 SET content = $1, created_at = NOW()
		 WHERE id = (
		     SELECT id FROM chat_messages
		     WHERE session_id = $2
		       AND role = 'assistant'
		       AND (content = '' OR content IS NULL)
		     ORDER BY created_at DESC
		     LIMIT 1
		 )`,
		content, sessionID,
	)
	if err != nil {
		log.Printf("saveAssistantMessage: update failed for session %s: %v", sessionID, err)
		return fmt.Errorf("update failed: %w", err)
	}

	if result.RowsAffected() == 0 {
		// No placeholder found – insert a new message
		_, err = p.dbPool.Exec(ctx,
			`INSERT INTO chat_messages (session_id, role, content, created_at)
			 VALUES ($1, 'assistant', $2, NOW())`,
			sessionID, content,
		)
		if err != nil {
			log.Printf("saveAssistantMessage: insert failed for session %s: %v", sessionID, err)
			return fmt.Errorf("insert failed: %w", err)
		}
		log.Printf("saveAssistantMessage: inserted new assistant message for session %s", sessionID)
	} else {
		log.Printf("saveAssistantMessage: updated assistant message for session %s, rows affected: %d", sessionID, result.RowsAffected())
	}
	return nil
}

// processJob now only starts the AI job and does NOT mark it completed.
// It also does NOT broadcast a fake "complete" thought.
func (p *Processor) processJob(ctx context.Context, job *queue.Job) error {
	switch job.Type {
	case "generate":
		return p.processGenerateJob(ctx, job)
	case "resume":
		return p.processResumeJob(ctx, job)
	default:
		log.Printf("Unknown job type: %s", job.Type)
		return nil
	}
}

// jobIdentity rebuilds the caller's identity from the queued job. The worker
// has no HTTP request, so this is the only place the AI service can learn which
// organisation's provider settings to use.
func jobIdentity(job *queue.Job) client.Identity {
	orgID, _ := job.Payload["org_id"].(string)
	userID, _ := job.Payload["user_id"].(string)
	return client.Identity{OrgID: orgID, UserID: userID}
}

func (p *Processor) processGenerateJob(ctx context.Context, job *queue.Job) error {
	content, _ := job.Payload["content"].(string)
	mode, _ := job.Payload["mode"].(string)
	detailLevel, _ := job.Payload["detail_level"].(string)
	includeSources, _ := job.Payload["include_sources"].(bool)
	attachmentsRaw, _ := job.Payload["attachments"].([]interface{})
	userMsgID, _ := job.Payload["user_message_id"].(string)
	assistantMsgID, _ := job.Payload["assistant_message_id"].(string)

	attachments := make([]map[string]interface{}, 0)
	for _, att := range attachmentsRaw {
		if m, ok := att.(map[string]interface{}); ok {
			attachments = append(attachments, m)
		}
	}

	thoughtData, _ := json.Marshal(map[string]interface{}{
		"type":      "thought",
		"phase":     "content_analysis",
		"content":   "Starting presentation generation...",
		"timestamp": time.Now().Format(time.RFC3339),
	})
	p.hub.Broadcast(job.SessionID, sse.Event{
		Event: "thought",
		Data:  string(thoughtData),
	})

	// Call AI service – this returns immediately after the AI accepts the job.
	_, err := p.aiClient.SendMessage(client.GenerateRequest{
		SessionID:          job.SessionID,
		Content:            content,
		Attachments:        attachments,
		Mode:               mode,
		DetailLevel:        detailLevel,
		Identity:           jobIdentity(job),
		UserMessageID:      userMsgID,
		AssistantMessageID: assistantMsgID,
		IncludeSources:     includeSources,
	})
	if err != nil {
		return err
	}
	return nil
}

func (p *Processor) processResumeJob(ctx context.Context, job *queue.Job) error {
	answer, _ := job.Payload["answer"].(string)
	questionText, _ := job.Payload["question_text"].(string)
	selectedOption, _ := job.Payload["selected_option"].(string)
	userMsgID, _ := job.Payload["user_message_id"].(string)
	assistantMsgID, _ := job.Payload["assistant_message_id"].(string)

	log.Printf("Resuming job %s for session %s with answer", job.ID, job.SessionID)

	thoughtData, _ := json.Marshal(map[string]interface{}{
		"type":      "thought",
		"phase":     "user_input",
		"content":   fmt.Sprintf("User answered: %s", answer),
		"timestamp": time.Now().Format(time.RFC3339),
	})
	p.hub.Broadcast(job.SessionID, sse.Event{
		Event: "thought",
		Data:  string(thoughtData),
	})

	// Call AI service with answer – this returns immediately after the AI accepts the job
	_, err := p.aiClient.AnswerQuestion(
		job.SessionID, selectedOption, answer, questionText, jobIdentity(job),
		userMsgID, assistantMsgID,
	)
	if err != nil {
		return err
	}
	return nil
}
