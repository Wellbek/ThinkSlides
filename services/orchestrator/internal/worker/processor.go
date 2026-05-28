package worker

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/slideagent/orchestrator/internal/client"
	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/sse"
	"github.com/redis/go-redis/v9"
)

// Processor continuously dequeues jobs and processes them by calling the AI service.
type Processor struct {
	queue       *queue.RedisQueue
	hub         *sse.Hub
	aiClient    *client.AIServiceClient
	redisClient *redis.Client // needed to listen for job completion
}

// NewProcessor creates a new job processor.
func NewProcessor(q *queue.RedisQueue, hub *sse.Hub, aiClient *client.AIServiceClient, redisClient *redis.Client) *Processor {
	return &Processor{
		queue:       q,
		hub:         hub,
		aiClient:    aiClient,
		redisClient: redisClient,
	}
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

	// Wait for the "done" event from Redis, which signals the agent has finished.
	done := make(chan struct{})
	ctxDone, cancel := context.WithCancel(context.Background())
	defer cancel()

	go p.waitForCompletion(ctxDone, job.SessionID, job.ID, done)

	select {
	case <-done:
		log.Printf("Job %s completed (done event received)", job.ID)
		if err := p.queue.UpdateJobStatus(job.ID, "completed"); err != nil {
			log.Printf("Failed to update job status: %v", err)
		}
	case <-time.After(10 * time.Minute):
		log.Printf("Job %s timed out waiting for done event", job.ID)
		_ = p.queue.UpdateJobStatus(job.ID, "failed")
	}
}

// waitForCompletion subscribes to Redis for the session's events and waits for a "done" event.
func (p *Processor) waitForCompletion(ctx context.Context, sessionID, jobID string, done chan struct{}) {
	channel := "agent:events:" + sessionID
	pubsub := p.redisClient.Subscribe(ctx, channel)
	defer pubsub.Close()

	ch := pubsub.Channel()
	for {
		select {
		case <-ctx.Done():
			return
		case msg := <-ch:
			var event map[string]interface{}
			if err := json.Unmarshal([]byte(msg.Payload), &event); err != nil {
				continue
			}
			if eventType, ok := event["type"].(string); ok && eventType == "done" {
				close(done)
				return
			}
		}
	}
}

// processJob now only starts the AI job and does NOT mark it completed.
// It also does NOT broadcast a fake "complete" thought.
func (p *Processor) processJob(ctx context.Context, job *queue.Job) error {
	switch job.Type {
	case "generate":
		return p.processGenerateJob(ctx, job)
	default:
		log.Printf("Unknown job type: %s", job.Type)
		return nil
	}
}

func (p *Processor) processGenerateJob(ctx context.Context, job *queue.Job) error {
	content, _ := job.Payload["content"].(string)
	attachmentsRaw, _ := job.Payload["attachments"].([]interface{})

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
	_, err := p.aiClient.SendMessage(job.SessionID, content, attachments)
	if err != nil {
		return err
	}
	return nil
}