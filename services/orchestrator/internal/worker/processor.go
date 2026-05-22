package worker

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/slideagent/orchestrator/internal/client"
	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/sse"
)

// Processor continuously dequeues jobs and processes them by calling the AI service.
type Processor struct {
	queue    *queue.RedisQueue
	hub      *sse.Hub
	aiClient *client.AIServiceClient
}

// NewProcessor creates a new job processor.
func NewProcessor(q *queue.RedisQueue, hub *sse.Hub, aiClient *client.AIServiceClient) *Processor {
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
			time.Sleep(500 * time.Millisecond)
			continue
		}

		log.Printf("Processing job %s (type=%s, session=%s)", job.ID, job.Type, job.SessionID)

		if err := p.queue.UpdateJobStatus(job.ID, "processing"); err != nil {
			log.Printf("Failed to update job status: %v", err)
		}

		if err := p.processJob(ctx, job); err != nil {
			log.Printf("Job %s failed: %v", job.ID, err)
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
			continue
		}

		if err := p.queue.UpdateJobStatus(job.ID, "completed"); err != nil {
			log.Printf("Failed to update job status: %v", err)
		}

		log.Printf("Job %s completed", job.ID)
	}
}

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

	result, err := p.aiClient.SendMessage(job.SessionID, content, attachments)
	if err != nil {
		return err
	}

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