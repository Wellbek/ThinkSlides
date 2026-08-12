package worker

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// A session's completion waiter holds a Redis subscription and is what decides
// whether a job is recorded completed or failed. There used to be one per job:
// a paused run keeps its waiter listening (it must - the run is not over), so
// every resume added another subscriber to the same channel, and each of them
// wrote the assistant message when the final done landed.
//
// These tests measure the thing that was wrong - how many subscribers a
// session actually has - rather than the shape of the code around it.

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

func processorForTest(t *testing.T) (*Processor, *redis.Client) {
	t.Helper()
	client := redisForTest(t)
	// queue/hub/aiClient/dbPool are not exercised by the waiter lifecycle.
	// A nil dbPool makes saveAssistantMessage a logged no-op.
	p := NewProcessor(nil, nil, nil, client, nil)
	return p, client
}

// subscriberCount asks Redis how many subscribers a session's channel has.
func subscriberCount(t *testing.T, client *redis.Client, sessionID string) int64 {
	t.Helper()
	channel := "agent:events:" + sessionID
	res, err := client.PubSubNumSub(context.Background(), channel).Result()
	if err != nil {
		t.Fatalf("PUBSUB NUMSUB: %v", err)
	}
	return res[channel]
}

// waitForSubscribers blocks until the channel has the expected subscriber
// count, so a publish cannot race ahead of the goroutine subscribing.
func waitForSubscribers(t *testing.T, client *redis.Client, sessionID string, want int64) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if got := subscriberCount(t, client, sessionID); got == want {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("session %s: wanted %d subscriber(s), got %d",
		sessionID, want, subscriberCount(t, client, sessionID))
}

func publish(t *testing.T, client *redis.Client, sessionID string, event map[string]interface{}) {
	t.Helper()
	payload, err := json.Marshal(event)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := client.Publish(context.Background(), "agent:events:"+sessionID, payload).Err(); err != nil {
		t.Fatalf("publish: %v", err)
	}
}

func TestSessionKeepsOneSubscriberAcrossConcurrentJobs(t *testing.T) {
	p, client := processorForTest(t)
	session := "sess-one-subscriber"

	// A generate job, then two resumes - the shape that used to accumulate
	// three subscribers on one channel.
	w1 := p.acquireWaiter(session)
	w2 := p.acquireWaiter(session)
	w3 := p.acquireWaiter(session)

	waitForSubscribers(t, client, session, 1)

	if w1 != w2 || w2 != w3 {
		t.Error("jobs on one session were handed different waiters")
	}
	if got := len(p.waiters); got != 1 {
		t.Errorf("processor tracked %d waiters, want 1", got)
	}

	p.releaseWaiter(session, w1)
	p.releaseWaiter(session, w2)
	p.releaseWaiter(session, w3)
}

func TestWaiterSurvivesUntilTheLastJobReleasesIt(t *testing.T) {
	p, client := processorForTest(t)
	session := "sess-refcount"

	w := p.acquireWaiter(session)
	_ = p.acquireWaiter(session)
	waitForSubscribers(t, client, session, 1)

	// The generate job resolving must not tear down the subscription the
	// resume job is still relying on.
	p.releaseWaiter(session, w)
	time.Sleep(100 * time.Millisecond)
	if got := subscriberCount(t, client, session); got != 1 {
		t.Fatalf("subscription dropped while a job was still waiting: %d subscribers", got)
	}

	p.releaseWaiter(session, w)
	waitForSubscribers(t, client, session, 0)

	if got := len(p.waiters); got != 0 {
		t.Errorf("processor still tracks %d waiters after the last release", got)
	}
}

func TestPausedDoneKeepsTheWaiterListening(t *testing.T) {
	p, client := processorForTest(t)
	session := "sess-paused"

	w := p.acquireWaiter(session)
	t.Cleanup(func() { p.releaseWaiter(session, w) })
	waitForSubscribers(t, client, session, 1)

	publish(t, client, session, map[string]interface{}{"type": "done", "paused": true})

	// The run is parked on a question, not finished.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if paused, _ := w.state(); paused {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	paused, _ := w.state()
	if !paused {
		t.Error("waiter did not record the run as paused")
	}
	select {
	case <-w.done:
		t.Fatal("a paused done ended the job; the run has not finished")
	default:
	}
	if got := subscriberCount(t, client, session); got != 1 {
		t.Errorf("waiter stopped listening while paused: %d subscribers", got)
	}
}

func TestUnpausedDoneCompletesTheWaiter(t *testing.T) {
	p, client := processorForTest(t)
	session := "sess-done"

	w := p.acquireWaiter(session)
	t.Cleanup(func() { p.releaseWaiter(session, w) })
	waitForSubscribers(t, client, session, 1)

	publish(t, client, session, map[string]interface{}{
		"type": "done", "paused": false, "content": "here is your deck",
	})

	select {
	case <-w.done:
	case <-time.After(3 * time.Second):
		t.Fatal("final done did not complete the waiter")
	}
}

func TestAResumedRunReusesTheOriginalWaiter(t *testing.T) {
	p, client := processorForTest(t)
	session := "sess-resume"

	// Generate job starts; the run pauses on a question.
	generate := p.acquireWaiter(session)
	waitForSubscribers(t, client, session, 1)
	publish(t, client, session, map[string]interface{}{"type": "done", "paused": true})

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if paused, _ := generate.state(); paused {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	// The user answers: a resume job joins the same subscription.
	resume := p.acquireWaiter(session)
	if resume != generate {
		t.Fatal("the resume job started a second subscriber on the same channel")
	}
	if got := subscriberCount(t, client, session); got != 1 {
		t.Errorf("resume added a subscriber: %d total", got)
	}

	publish(t, client, session, map[string]interface{}{"type": "done", "paused": false})
	select {
	case <-generate.done:
	case <-time.After(3 * time.Second):
		t.Fatal("final done did not complete the shared waiter")
	}

	p.releaseWaiter(session, generate)
	p.releaseWaiter(session, resume)
	waitForSubscribers(t, client, session, 0)
}

// The stall deadline is what decides "failed", so its inputs are worth pinning
// directly: a paused run must never look like a stalled one, however long the
// user takes to answer.
func TestPausedRunIsNeverStale(t *testing.T) {
	w := &sessionWaiter{done: make(chan struct{}), lastActivity: time.Now()}

	w.noteActivity(false)
	if paused, _ := w.state(); paused {
		t.Error("a live run reported itself paused")
	}

	w.noteActivity(true)
	paused, _ := w.state()
	if !paused {
		t.Fatal("a paused run did not report itself paused")
	}

	// Simulate a user who has been thinking for an hour - far past the stall
	// timeout that used to mark their healthy job failed.
	w.mu.Lock()
	w.lastActivity = time.Now().Add(-time.Hour)
	w.mu.Unlock()

	paused, idle := w.state()
	if !paused {
		t.Error("a long wait cleared the paused flag")
	}
	if idle < stallTimeout {
		t.Fatalf("test setup: idle %s should exceed the stall timeout %s", idle, stallTimeout)
	}
	// processJobAsync consults paused first and skips the failure branch, which
	// is the whole point: the job stays alive while the question is open.
}
