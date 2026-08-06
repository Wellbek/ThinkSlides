package handler

import (
	"bytes"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// Roles allowed to change the organisation's AI settings or spend its API
// quota on a credential test. Everyone else may still read the redacted
// settings, because the composer needs the readiness summary to know whether
// Deep Thinking can be offered.
var settingsWriteRoles = map[string]bool{
	"owner": true,
	"admin": true,
}

// SettingsHandler proxies the AI service's settings API.
//
// The frontend never talks to the AI service directly - the orchestrator owns
// authentication, and this is where the caller's verified identity becomes the
// X-Org-Id / X-User-Id headers the AI service trusts. Proxying rather than
// reimplementing also keeps the credential handling (encryption, redaction) in
// exactly one service.
type SettingsHandler struct {
	aiBaseURL  string
	httpClient *http.Client
}

func NewSettingsHandler(aiBaseURL string) *SettingsHandler {
	return &SettingsHandler{
		aiBaseURL: strings.TrimSuffix(aiBaseURL, "/"),
		httpClient: &http.Client{
			// Credential tests call out to a third-party provider, so allow
			// noticeably longer than a database-backed request would need.
			Timeout: 60 * time.Second,
		},
	}
}

// Proxy forwards one settings request to the AI service and relays its
// response verbatim, so the AI service's error messages - which are written to
// be shown to the user - reach the UI unchanged.
func (h *SettingsHandler) Proxy(c *gin.Context) {
	if c.Request.Method != http.MethodGet && !settingsWriteRoles[c.GetString("role")] {
		c.JSON(http.StatusForbidden, gin.H{
			"error": "Only an organisation owner or admin can change AI settings",
		})
		return
	}

	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Could not read request body"})
		return
	}

	url := h.aiBaseURL + "/api/v1/settings" + c.Param("path")
	if raw := c.Request.URL.RawQuery; raw != "" {
		url += "?" + raw
	}

	req, err := http.NewRequestWithContext(c.Request.Context(), c.Request.Method, url, bytes.NewReader(body))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Could not build upstream request"})
		return
	}
	req.Header.Set("Content-Type", "application/json")
	// Deliberately not forwarding the caller's Authorization header: the AI
	// service trusts these two headers, and re-sending the bearer token would
	// spread credentials further than they need to go.
	if orgID := c.GetString("org_id"); orgID != "" {
		req.Header.Set("X-Org-Id", orgID)
	}
	if userID := c.GetString("user_id"); userID != "" {
		req.Header.Set("X-User-Id", userID)
	}

	resp, err := h.httpClient.Do(req)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "Could not reach the AI service"})
		return
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "Could not read the AI service response"})
		return
	}

	contentType := resp.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "application/json"
	}
	c.Data(resp.StatusCode, contentType, respBody)
}
