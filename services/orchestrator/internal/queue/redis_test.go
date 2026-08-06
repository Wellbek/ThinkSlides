package queue

import (
	"encoding/json"
	"testing"
	"time"
)

func TestJobSerialization(t *testing.T) {
	job := Job{
		ID:        "test-123",
		SessionID: "session-456",
		Type:      "generate",
		Payload: map[string]interface{}{
			"content": "Create a quarterly report",
		},
		Status:    "pending",
		CreatedAt: time.Now(),
	}

	data, err := json.Marshal(job)
	if err != nil {
		t.Fatalf("Failed to marshal job: %v", err)
	}

	var decoded Job
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("Failed to unmarshal job: %v", err)
	}

	if decoded.ID != job.ID {
		t.Errorf("ID mismatch: got %s, want %s", decoded.ID, job.ID)
	}
	if decoded.Type != job.Type {
		t.Errorf("Type mismatch: got %s, want %s", decoded.Type, job.Type)
	}
	if decoded.SessionID != job.SessionID {
		t.Errorf("SessionID mismatch: got %s, want %s", decoded.SessionID, job.SessionID)
	}
}

func TestJobStatusTransition(t *testing.T) {
	job := Job{
		ID:     "test-123",
		Type:   "generate",
		Status: "pending",
	}

	if job.Status != "pending" {
		t.Errorf("Initial status should be pending, got %s", job.Status)
	}

	job.Status = "processing"
	if job.Status != "processing" {
		t.Errorf("Status should be processing, got %s", job.Status)
	}

	job.Status = "completed"
	if job.Status != "completed" {
		t.Errorf("Status should be completed, got %s", job.Status)
	}
}
