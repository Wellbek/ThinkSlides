package sse

import (
	"testing"
	"time"
)

func TestHubSubscribe(t *testing.T) {
	hub := NewHub(nil)

	ch := hub.Subscribe("session-1")
	if ch == nil {
		t.Fatal("Subscribe returned nil channel")
	}

	hub.Unsubscribe("session-1", ch)
}

func TestHubBroadcast(t *testing.T) {
	hub := NewHub(nil)

	ch := hub.Subscribe("session-1")

	event := Event{
		Event: "thought",
		Data:  `{"type":"thought","content":"hello"}`,
	}

	hub.Broadcast("session-1", event)

	select {
	case received := <-ch:
		if received.Event != "thought" {
			t.Errorf("Event type mismatch: got %s, want thought", received.Event)
		}
	case <-time.After(1 * time.Second):
		t.Fatal("Timed out waiting for event")
	}

	hub.Unsubscribe("session-1", ch)
}

func TestHubBroadcastNoSubscribers(t *testing.T) {
	hub := NewHub(nil)

	// Should not panic when broadcasting to a session with no subscribers
	event := Event{
		Event: "thought",
		Data:  `{"type":"thought","content":"hello"}`,
	}
	hub.Broadcast("nonexistent-session", event)
}

func TestHubMultipleSubscribers(t *testing.T) {
	hub := NewHub(nil)

	ch1 := hub.Subscribe("session-1")
	ch2 := hub.Subscribe("session-1")

	event := Event{
		Event: "tool_call",
		Data:  `{"toolName":"analyze_document"}`,
	}

	hub.Broadcast("session-1", event)

	// Both subscribers should receive the event
	select {
	case <-ch1:
	case <-time.After(1 * time.Second):
		t.Fatal("Subscriber 1 did not receive event")
	}

	select {
	case <-ch2:
	case <-time.After(1 * time.Second):
		t.Fatal("Subscriber 2 did not receive event")
	}

	hub.Unsubscribe("session-1", ch1)
	hub.Unsubscribe("session-1", ch2)
}

func TestHubSessionIsolation(t *testing.T) {
	hub := NewHub(nil)

	ch1 := hub.Subscribe("session-1")
	ch2 := hub.Subscribe("session-2")

	event := Event{
		Event: "thought",
		Data:  `{"content":"only for session-1"}`,
	}

	hub.Broadcast("session-1", event)

	// session-1 should receive
	select {
	case <-ch1:
	case <-time.After(1 * time.Second):
		t.Fatal("session-1 did not receive event")
	}

	// session-2 should NOT receive
	select {
	case <-ch2:
		t.Fatal("session-2 should not have received event for session-1")
	case <-time.After(100 * time.Millisecond):
		// Expected — no event
	}

	hub.Unsubscribe("session-1", ch1)
	hub.Unsubscribe("session-2", ch2)
}

func TestHubUnsubscribeCleansUp(t *testing.T) {
	hub := NewHub(nil)

	ch := hub.Subscribe("session-1")
	hub.Unsubscribe("session-1", ch)

	// After unsubscribe, the session map should be cleaned up
	hub.mu.RLock()
	_, exists := hub.subscribers["session-1"]
	hub.mu.RUnlock()

	if exists {
		t.Error("session-1 should be removed from subscribers after unsubscribe")
	}
}
