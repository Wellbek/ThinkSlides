package handler

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// upstream records what the AI service was asked, and replies with a canned
// response so the proxy's relaying can be asserted.
type upstream struct {
	method  string
	path    string
	query   string
	headers http.Header
	body    string

	status      int
	contentType string
	reply       string
}

func newUpstream(t *testing.T, u *upstream) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		u.method, u.path, u.query = r.Method, r.URL.Path, r.URL.RawQuery
		u.headers = r.Header.Clone()
		u.body = string(raw)

		if u.contentType != "" {
			w.Header().Set("Content-Type", u.contentType)
		}
		if u.status != 0 {
			w.WriteHeader(u.status)
		}
		_, _ = w.Write([]byte(u.reply))
	}))
}

// newProxyRouter wires the handler exactly as main.go does, with the auth
// middleware replaced by fixed claims.
func newProxyRouter(aiURL, role string) *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	h := NewSettingsHandler(aiURL)
	r.Use(func(c *gin.Context) {
		c.Set("org_id", "org-1")
		c.Set("user_id", "user-1")
		c.Set("role", role)
	})
	r.Any("/api/settings/*path", h.Proxy)
	return r
}

func do(r *gin.Engine, method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestProxyForwardsPathBodyAndIdentity(t *testing.T) {
	up := &upstream{reply: `{"ok":true}`}
	server := newUpstream(t, up)
	defer server.Close()

	w := do(newProxyRouter(server.URL, "owner"), http.MethodPut,
		"/api/settings/ai", `{"chat":{"provider":"anthropic"}}`)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
	}
	if up.method != http.MethodPut {
		t.Errorf("upstream method = %q", up.method)
	}
	if up.path != "/api/v1/settings/ai" {
		t.Errorf("upstream path = %q", up.path)
	}
	if up.body != `{"chat":{"provider":"anthropic"}}` {
		t.Errorf("upstream body = %q", up.body)
	}
	if got := up.headers.Get("X-Org-Id"); got != "org-1" {
		t.Errorf("X-Org-Id = %q", got)
	}
	if got := up.headers.Get("X-User-Id"); got != "user-1" {
		t.Errorf("X-User-Id = %q", got)
	}
}

func TestProxyDoesNotForwardTheBearerToken(t *testing.T) {
	// The AI service authorises on X-Org-Id; passing the token on would spread
	// the credential further than it needs to go.
	up := &upstream{reply: `{}`}
	server := newUpstream(t, up)
	defer server.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/settings/ai", nil)
	req.Header.Set("Authorization", "Bearer secret-token")
	w := httptest.NewRecorder()
	newProxyRouter(server.URL, "owner").ServeHTTP(w, req)

	if got := up.headers.Get("Authorization"); got != "" {
		t.Errorf("Authorization was forwarded upstream: %q", got)
	}
}

func TestProxyRelaysUpstreamStatusAndBody(t *testing.T) {
	// The AI service's errors are written for the user to read, so they must
	// arrive unchanged rather than being replaced by a generic message.
	up := &upstream{
		status:      http.StatusServiceUnavailable,
		contentType: "application/json",
		reply:       `{"detail":"SETTINGS_ENCRYPTION_KEY is not configured"}`,
	}
	server := newUpstream(t, up)
	defer server.Close()

	w := do(newProxyRouter(server.URL, "owner"), http.MethodPut, "/api/settings/ai", `{}`)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", w.Code)
	}
	var payload map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("response was not JSON: %v", err)
	}
	if !strings.Contains(payload["detail"], "SETTINGS_ENCRYPTION_KEY") {
		t.Errorf("detail = %q", payload["detail"])
	}
}

func TestProxyPreservesTheQueryString(t *testing.T) {
	up := &upstream{reply: `{}`}
	server := newUpstream(t, up)
	defer server.Close()

	do(newProxyRouter(server.URL, "owner"), http.MethodGet, "/api/settings/catalog?refresh=1", "")

	if up.query != "refresh=1" {
		t.Errorf("query = %q, want refresh=1", up.query)
	}
}

func TestNonAdminsMayReadButNotWriteSettings(t *testing.T) {
	up := &upstream{reply: `{}`}
	server := newUpstream(t, up)
	defer server.Close()
	router := newProxyRouter(server.URL, "member")

	if w := do(router, http.MethodGet, "/api/settings/ai", ""); w.Code != http.StatusOK {
		t.Errorf("read status = %d, want 200 - the composer needs the readiness summary", w.Code)
	}

	for _, method := range []string{http.MethodPut, http.MethodPost} {
		w := do(router, method, "/api/settings/ai", `{}`)
		if w.Code != http.StatusForbidden {
			t.Errorf("%s status = %d, want 403", method, w.Code)
		}
	}
}

func TestUnreachableAIServiceIsReportedAsBadGateway(t *testing.T) {
	server := newUpstream(t, &upstream{reply: `{}`})
	url := server.URL
	server.Close() // nothing is listening now

	w := do(newProxyRouter(url, "owner"), http.MethodGet, "/api/settings/ai", "")

	if w.Code != http.StatusBadGateway {
		t.Errorf("status = %d, want 502", w.Code)
	}
}
