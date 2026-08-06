package worker

import (
	"testing"

	"github.com/thinkslides/orchestrator/internal/queue"
)

func TestJobIdentity(t *testing.T) {
	cases := []struct {
		name    string
		payload map[string]interface{}
		wantOrg string
		wantUsr string
	}{
		{
			name:    "identity travels on the job",
			payload: map[string]interface{}{"org_id": "org-1", "user_id": "user-1"},
			wantOrg: "org-1",
			wantUsr: "user-1",
		},
		{
			name: "a job queued before identity existed yields empty ids",
			// Empty is the correct answer here: the client omits the headers,
			// and the AI service falls back to the default organisation.
			payload: map[string]interface{}{"content": "hi"},
		},
		{
			name:    "non-string values are ignored rather than panicking",
			payload: map[string]interface{}{"org_id": 42, "user_id": nil},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := jobIdentity(&queue.Job{Payload: tc.payload})
			if got.OrgID != tc.wantOrg {
				t.Errorf("OrgID = %q, want %q", got.OrgID, tc.wantOrg)
			}
			if got.UserID != tc.wantUsr {
				t.Errorf("UserID = %q, want %q", got.UserID, tc.wantUsr)
			}
		})
	}
}
