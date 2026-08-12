package client

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// capture records what the AI service actually received, so the tests assert
// on the wire format rather than on the struct that produced it.
type capture struct {
	path    string
	headers http.Header
	body    map[string]interface{}
}

func newStubAIService(t *testing.T, got *capture) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("reading request body: %v", err)
		}
		if err := json.Unmarshal(raw, &got.body); err != nil {
			t.Fatalf("request body was not JSON: %v (%s)", err, raw)
		}
		got.path = r.URL.Path
		got.headers = r.Header.Clone()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"message_id":"m1"}`))
	}))
}

func TestSendMessageForwardsModeAndIdentity(t *testing.T) {
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.SendMessage(GenerateRequest{
		SessionID: "session-1",
		Content:   "Build me a deck",
		Mode:      "deep",
		Identity:  Identity{OrgID: "org-1", UserID: "user-1"},
	}); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	if got.path != "/api/v1/generate/sessions/session-1/messages" {
		t.Errorf("path = %q", got.path)
	}
	if got.body["mode"] != "deep" {
		t.Errorf("mode = %v, want deep", got.body["mode"])
	}
	if h := got.headers.Get("X-Org-Id"); h != "org-1" {
		t.Errorf("X-Org-Id = %q, want org-1", h)
	}
	if h := got.headers.Get("X-User-Id"); h != "user-1" {
		t.Errorf("X-User-Id = %q, want user-1", h)
	}
}

func TestSendMessageDefaultsToSimpleMode(t *testing.T) {
	// A job queued before the mode field existed carries no mode; the AI
	// service must still be told which level to run at explicitly.
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.SendMessage(GenerateRequest{SessionID: "s", Content: "hi"}); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	if got.body["mode"] != "simple" {
		t.Errorf("mode = %v, want simple", got.body["mode"])
	}
}

func TestIdentityHeadersAreOmittedWhenUnknown(t *testing.T) {
	// Direct/dev calls have no identity. Sending empty headers would make the
	// AI service resolve an empty org id rather than fall back to the default.
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.SendMessage(GenerateRequest{SessionID: "s", Content: "hi"}); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	if _, ok := got.headers["X-Org-Id"]; ok {
		t.Error("X-Org-Id should not be sent when the org is unknown")
	}
	if _, ok := got.headers["X-User-Id"]; ok {
		t.Error("X-User-Id should not be sent when the user is unknown")
	}
}

func TestAnswerQuestionSendsTheQuestionAndIdentity(t *testing.T) {
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.AnswerQuestion(
		"session-2", "Option B", "Option B", "Which audience?",
		Identity{OrgID: "org-2"}, "user-msg-2", "assistant-msg-2",
	); err != nil {
		t.Fatalf("AnswerQuestion: %v", err)
	}

	if got.path != "/api/v1/generate/sessions/session-2/answer-question" {
		t.Errorf("path = %q", got.path)
	}
	if got.body["answer"] != "Option B" {
		t.Errorf("answer = %v", got.body["answer"])
	}
	if got.body["selected_option"] != "Option B" {
		t.Errorf("selected_option = %v", got.body["selected_option"])
	}
	if got.body["question_text"] != "Which audience?" {
		t.Errorf("question_text = %v", got.body["question_text"])
	}
	if h := got.headers.Get("X-Org-Id"); h != "org-2" {
		t.Errorf("X-Org-Id = %q, want org-2", h)
	}
	// The resume path deliberately sends no mode - the agent restores it from
	// the working state it saved when it paused.
	if _, ok := got.body["mode"]; ok {
		t.Error("resume must not override the paused run's mode")
	}
}

func TestErrorResponseIsSurfacedWithItsBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(`{"detail":"upstream exploded"}`))
	}))
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	_, err := c.SendMessage(GenerateRequest{SessionID: "s", Content: "hi"})
	if err == nil {
		t.Fatal("expected an error for a 502 response")
	}
	if want := "upstream exploded"; !strings.Contains(err.Error(), want) {
		t.Errorf("error %q does not mention %q", err, want)
	}
}

// The orchestrator writes the user message and the empty assistant placeholder
// before it queues the job. It has to tell the AI service which rows those
// are, or the AI service writes its own pair and the exchange ends up with two
// user rows and two placeholders - the second of which saveAssistantMessage
// can then fill instead of the right one.

func TestSendMessageForwardsTheRowsItAlreadyWrote(t *testing.T) {
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.SendMessage(GenerateRequest{
		SessionID:          "session-3",
		Content:            "Build me a deck",
		UserMessageID:      "user-row-1",
		AssistantMessageID: "assistant-row-1",
	}); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	if got.body["user_message_id"] != "user-row-1" {
		t.Errorf("user_message_id = %v, want user-row-1", got.body["user_message_id"])
	}
	if got.body["assistant_message_id"] != "assistant-row-1" {
		t.Errorf("assistant_message_id = %v, want assistant-row-1", got.body["assistant_message_id"])
	}
}

func TestAnswerQuestionForwardsTheRowsItAlreadyWrote(t *testing.T) {
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.AnswerQuestion(
		"session-4", "Option A", "Option A", "Which audience?",
		Identity{OrgID: "org-4"}, "user-row-2", "assistant-row-2",
	); err != nil {
		t.Fatalf("AnswerQuestion: %v", err)
	}

	if got.body["user_message_id"] != "user-row-2" {
		t.Errorf("user_message_id = %v, want user-row-2", got.body["user_message_id"])
	}
	if got.body["assistant_message_id"] != "assistant-row-2" {
		t.Errorf("assistant_message_id = %v, want assistant-row-2", got.body["assistant_message_id"])
	}
}

// Driven directly, with no orchestrator in front of it, the AI service must
// still own the rows - so the fields are omitted rather than sent empty.
func TestRowIdsAreOmittedWhenThereAreNone(t *testing.T) {
	var got capture
	server := newStubAIService(t, &got)
	defer server.Close()

	c := NewAIServiceClient(server.URL)
	if _, err := c.SendMessage(GenerateRequest{
		SessionID: "session-5",
		Content:   "Build me a deck",
	}); err != nil {
		t.Fatalf("SendMessage: %v", err)
	}

	if _, ok := got.body["user_message_id"]; ok {
		t.Error("user_message_id was sent when this service had not written a row")
	}
	if _, ok := got.body["assistant_message_id"]; ok {
		t.Error("assistant_message_id was sent when this service had not written a row")
	}
}
