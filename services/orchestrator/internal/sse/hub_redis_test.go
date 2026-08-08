package sse

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// The Redis listener's lifetime must follow the subscriber set, not the
// contents of the event stream. An agent run pauses on ask_user_question and
// resumes when the user answers, so a "done" event is not the end of the
// session - a listener that stopped there left the session's SSE clients
// attached to nothing, and every event from the resumed run was dropped.

func redisForTest(t *testing.T) *redis.Client {
	t.Helper()

	addr := os.Getenv("REDIS_TEST_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	client := redis.NewClient(&redis.Options{Addr: addr})

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		client.Close()
		t.Skipf("Redis not reachable at %s: %v", addr, err)
	}
	t.Cleanup(func() { client.Close() })
	return client
}

// waitForListener blocks until the hub's goroutine has actually subscribed to
// the session's Redis channel, so a publish cannot race ahead of it.
func waitForListener(t *testing.T, client *redis.Client, session string) {
	t.Helper()

	channel := "agent:events:" + session
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); {
		counts, err := client.PubSubNumSub(context.Background(), channel).Result()
		if err != nil {
			t.Fatalf("PUBSUB NUMSUB failed: %v", err)
		}
		if counts[channel] > 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("hub never subscribed to %s", channel)
}

// waitForNoListener blocks until the cancelled goroutine has released its Redis
// subscription, so a later waitForListener cannot match the outgoing one.
func waitForNoListener(t *testing.T, client *redis.Client, session string) {
	t.Helper()

	channel := "agent:events:" + session
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); {
		counts, err := client.PubSubNumSub(context.Background(), channel).Result()
		if err != nil {
			t.Fatalf("PUBSUB NUMSUB failed: %v", err)
		}
		if counts[channel] == 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("hub never released its subscription to %s", channel)
}

// publishAndExpect publishes a payload and asserts it reaches the subscriber.
func publishAndExpect(t *testing.T, client *redis.Client, session string, ch chan Event, payload, wantType string) {
	t.Helper()

	waitForListener(t, client, session)
	if err := client.Publish(context.Background(), "agent:events:"+session, payload).Err(); err != nil {
		t.Fatalf("publish failed: %v", err)
	}

	select {
	case ev := <-ch:
		if ev.Event != wantType {
			t.Fatalf("event type = %q, want %q", ev.Event, wantType)
		}
	case <-time.After(3 * time.Second):
		t.Fatalf("no %q event forwarded from Redis", wantType)
	}
}

func TestListenerKeepsForwardingAfterADoneEvent(t *testing.T) {
	client := redisForTest(t)
	hub := NewHub(client)
	session := "sse-done-then-resume"

	ch := hub.Subscribe(session)
	defer hub.Unsubscribe(session, ch)

	publishAndExpect(t, client, session, ch, `{"type":"thought","content":"working"}`, "thought")

	// A terminal-looking done, with no paused flag, as a finished run emits.
	publishAndExpect(t, client, session, ch, `{"type":"done","content":"finished"}`, "done")

	// The same subscriber must still receive whatever comes next.
	publishAndExpect(t, client, session, ch, `{"type":"thought","content":"resumed"}`, "thought")
}

func TestListenerStopsWhenTheLastSubscriberLeaves(t *testing.T) {
	client := redisForTest(t)
	hub := NewHub(client)
	session := "sse-listener-lifetime"

	ch1 := hub.Subscribe(session)
	ch2 := hub.Subscribe(session)

	hub.mu.RLock()
	_, running := hub.listeners[session]
	hub.mu.RUnlock()
	if !running {
		t.Fatal("no Redis listener registered for the first subscriber")
	}

	hub.Unsubscribe(session, ch1)

	hub.mu.RLock()
	_, running = hub.listeners[session]
	hub.mu.RUnlock()
	if !running {
		t.Fatal("listener was cancelled while a subscriber was still attached")
	}

	hub.Unsubscribe(session, ch2)

	hub.mu.RLock()
	_, running = hub.listeners[session]
	hub.mu.RUnlock()
	if running {
		t.Fatal("listener outlived its last subscriber")
	}
}

func TestResubscribingAfterTeardownStartsAFreshListener(t *testing.T) {
	client := redisForTest(t)
	hub := NewHub(client)
	session := "sse-resubscribe"

	ch := hub.Subscribe(session)
	publishAndExpect(t, client, session, ch, `{"type":"thought","content":"first"}`, "thought")
	hub.Unsubscribe(session, ch)
	waitForNoListener(t, client, session)

	ch = hub.Subscribe(session)
	defer hub.Unsubscribe(session, ch)
	publishAndExpect(t, client, session, ch, `{"type":"thought","content":"second"}`, "thought")
}
