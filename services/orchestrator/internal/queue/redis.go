package queue

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// Job represents a generation job in the queue.
type Job struct {
	ID        string                 `json:"id"`
	SessionID string                 `json:"session_id"`
	Type      string                 `json:"type"` // "generate", "ingest"
	Payload   map[string]interface{} `json:"payload"`
	Status    string                 `json:"status"` // "pending", "processing", "completed", "failed"
	CreatedAt time.Time              `json:"created_at"`
}

// RedisQueue implements a Redis-backed job queue using LPUSH/RPOP.
type RedisQueue struct {
	client *redis.Client
}

// NewRedisQueue creates a new Redis-backed job queue.
func NewRedisQueue(redisURL string) (*RedisQueue, error) {
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}

	client := redis.NewClient(opts)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := client.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("redis ping failed: %w", err)
	}

	return &RedisQueue{client: client}, nil
}

// Close closes the Redis connection.
func (q *RedisQueue) Close() error {
	return q.client.Close()
}

// Enqueue adds a job to the queue.
func (q *RedisQueue) Enqueue(job Job) error {
	ctx := context.Background()
	job.Status = "pending"
	job.CreatedAt = time.Now()

	data, err := json.Marshal(job)
	if err != nil {
		return fmt.Errorf("marshal job: %w", err)
	}

	if err := q.client.LPush(ctx, "slideagent:jobs", data).Err(); err != nil {
		return fmt.Errorf("lpush job: %w", err)
	}

	if err := q.client.Set(ctx, fmt.Sprintf("slideagent:job:%s", job.ID), data, 24*time.Hour).Err(); err != nil {
		return fmt.Errorf("store job: %w", err)
	}

	return nil
}

// Dequeue pops a job from the queue (blocking with timeout).
func (q *RedisQueue) Dequeue(ctx context.Context) (*Job, error) {
	result, err := q.client.BRPop(ctx, 5*time.Second, "slideagent:jobs").Result()
	if err != nil {
		if err == redis.Nil {
			return nil, nil
		}
		return nil, err
	}

	if len(result) < 2 {
		return nil, nil
	}

	var job Job
	if err := json.Unmarshal([]byte(result[1]), &job); err != nil {
		return nil, fmt.Errorf("unmarshal job: %w", err)
	}

	return &job, nil
}

// GetJob retrieves a job by ID.
func (q *RedisQueue) GetJob(jobID string) (*Job, error) {
	ctx := context.Background()
	data, err := q.client.Get(ctx, fmt.Sprintf("slideagent:job:%s", jobID)).Bytes()
	if err != nil {
		if err == redis.Nil {
			return nil, nil
		}
		return nil, err
	}

	var job Job
	if err := json.Unmarshal(data, &job); err != nil {
		return nil, fmt.Errorf("unmarshal job: %w", err)
	}

	return &job, nil
}

// UpdateJobStatus updates the status of a job.
func (q *RedisQueue) UpdateJobStatus(jobID string, status string) error {
	ctx := context.Background()
	job, err := q.GetJob(jobID)
	if err != nil {
		return err
	}
	if job == nil {
		return fmt.Errorf("job not found: %s", jobID)
	}

	job.Status = status
	data, err := json.Marshal(job)
	if err != nil {
		return fmt.Errorf("marshal job: %w", err)
	}

	return q.client.Set(ctx, fmt.Sprintf("slideagent:job:%s", jobID), data, 24*time.Hour).Err()
}