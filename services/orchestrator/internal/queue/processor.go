package queue

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/slideagent/orchestrator/internal/handler"
	"github.com/slideagent/orchestrator/internal/sse"
)

// Processor continuously dequeues jobs and processes them by calling the AI service.
type Processor struct {
	queue    *RedisQueue
	hub      *sse.Hub
	aiClient *handler.AIServiceClient
}

// NewProcessor creates a new job processor.
func NewProcessor(q *RedisQueue, hub *sse.Hub, aiClient *handler.AIServiceClient) *Processor {
	return &Processor{
		queue:    q,
		hub:      hub,
		aiClient: aiClient,
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
			// No jobs available, brief pause
			time.Sleep(500 * time.Millisecond)
			continue
		}

		log.Printf("Processing job %s (type=%s, session=%s)", job.ID, job.Type, job.SessionID)

		// Mark job as processing
		if err := p.queue.UpdateJobStatus(job.ID, "processing"); err != nil {
			log.Printf("Failed to update job status: %v", err)
		}

		// Process the job
		if err := p.processJob(ctx, job); err != nil {
			log.Printf("Job %s failed: %v", job.ID, err)
			_ = p.queue.UpdateJobStatus(job.ID, "failed")

			// Broadcast error event
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
			continue
		}

		// Mark job as completed
		if err := p.queue.UpdateJobStatus(job.ID, "completed"); err != nil {
			log.Printf("Failed to update job status: %v", err)
		}

		log.Printf("Job %s completed", job.ID)
	}
}

// processJob handles a single job by dispatching to the AI service.
func (p *Processor) processJob(ctx context.Context, job *Job) error {
	switch job.Type {
	case "generate":
		return p.processGenerateJob(ctx, job)
	default:
		log.Printf("Unknown job type: %s", job.Type)
		return nil
	}
}

// processGenerateJob sends the message to the AI service and streams events back.
func (p *Processor) processGenerateJob(ctx context.Context, job *Job) error {
	content, _ := job.Payload["content"].(string)
	attachmentsRaw, _ := job.Payload["attachments"].([]interface{})

	attachments := make([]map[string]interface{}, 0)
	for _, att := range attachmentsRaw {
		if m, ok := att.(map[string]interface{}); ok {
			attachments = append(attachments, m)
		}
	}

	// Broadcast thought event: starting
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

	// Call the AI service
	result, err := p.aiClient.SendMessage(job.SessionID, content, attachments)
	if err != nil {
		return err
	}

	// Broadcast completion event
	completeData, _ := json.Marshal(map[string]interface{}{
		"type":      "thought",
		"phase":     "complete",
		"content":   "Generation job dispatched to AI service",
		"timestamp": time.Now().Format(time.RFC3339),
		"result":    result,
	})
	p.hub.Broadcast(job.SessionID, sse.Event{
		Event: "thought",
		Data:  string(completeData),
	})

	return nil
}