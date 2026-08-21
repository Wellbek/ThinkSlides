package client

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// AIServiceClient communicates with the Python AI service.
type AIServiceClient struct {
	baseURL    string
	httpClient *http.Client
}

func NewAIServiceClient(baseURL string) *AIServiceClient {
	return &AIServiceClient{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 120 * time.Second,
		},
	}
}

// Identity of the caller, forwarded to the AI service so it can load the
// right organisation's configured providers and credentials. The background
// worker has no HTTP request context, so identity travels on the queue job and
// is re-attached here as headers.
type Identity struct {
	OrgID  string
	UserID string
}

// GenerateRequest is the payload for a chat message. Mode is the effort level
// the user picked in the composer: "simple" answers from the base model,
// "deep" enables web-search-backed research.
type GenerateRequest struct {
	SessionID   string
	Content     string
	Attachments []map[string]interface{}
	Mode        string
	DetailLevel string
	Identity    Identity
	// Rows this service already wrote for the exchange. The AI service adopts
	// them instead of inserting a second user message and a second empty
	// assistant placeholder.
	UserMessageID      string
	AssistantMessageID string
	// "Include source references in the document", from the composer.
	IncludeSources bool
}

// postJSON issues an authenticated JSON POST and decodes the response.
func (c *AIServiceClient) postJSON(url string, payload map[string]interface{}, identity Identity) (map[string]interface{}, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal error: %w", err)
	}

	req, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("request error: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if identity.OrgID != "" {
		req.Header.Set("X-Org-Id", identity.OrgID)
	}
	if identity.UserID != "" {
		req.Header.Set("X-User-Id", identity.UserID)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http error: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read error: %w", err)
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("AI service error %d: %s", resp.StatusCode, string(respBody))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("unmarshal error: %w", err)
	}

	return result, nil
}

// SendMessage sends a message to the AI service for processing.
func (c *AIServiceClient) SendMessage(gen GenerateRequest) (map[string]interface{}, error) {
	mode := gen.Mode
	if mode == "" {
		mode = "simple"
	}
	detailLevel := gen.DetailLevel
	if detailLevel == "" {
		detailLevel = "detailed"
	}
	payload := map[string]interface{}{
		"content":         gen.Content,
		"attachments":     gen.Attachments,
		"mode":            mode,
		"detail_level":    detailLevel,
		"include_sources": gen.IncludeSources,
	}
	// Omitted when empty so the AI service falls back to owning the rows, as
	// it must when it is driven directly rather than through this service.
	if gen.UserMessageID != "" && gen.AssistantMessageID != "" {
		payload["user_message_id"] = gen.UserMessageID
		payload["assistant_message_id"] = gen.AssistantMessageID
	}
	url := fmt.Sprintf("%s/api/v1/generate/sessions/%s/messages", c.baseURL, gen.SessionID)
	return c.postJSON(url, payload, gen.Identity)
}

// ListTemplates fetches templates from the AI service.
func (c *AIServiceClient) ListTemplates() ([]map[string]interface{}, error) {
	url := fmt.Sprintf("%s/api/v1/templates", c.baseURL)
	resp, err := c.httpClient.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result []map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	return result, nil
}

// ExtractTemplateDesignSystem sends a file path to the AI service to extract design system data.
func (c *AIServiceClient) ExtractTemplateDesignSystem(filePath string) (map[string]interface{}, error) {
	payload := map[string]interface{}{
		"file_path": filePath,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal error: %w", err)
	}

	url := fmt.Sprintf("%s/api/v1/templates/extract", c.baseURL)
	req, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("request error: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http error: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read error: %w", err)
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("AI service error %d: %s", resp.StatusCode, string(respBody))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("unmarshal error: %w", err)
	}

	return result, nil
}

// GenerateTitle sends a message to the AI service to generate a chat title.
func (c *AIServiceClient) GenerateTitle(message string) (string, error) {
	payload := map[string]interface{}{
		"message": message,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal error: %w", err)
	}

	url := fmt.Sprintf("%s/api/v1/titles/generate", c.baseURL)
	req, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("request error: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("http error: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("read error: %w", err)
	}

	if resp.StatusCode >= 400 {
		return "", fmt.Errorf("AI service error %d: %s", resp.StatusCode, string(respBody))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", fmt.Errorf("unmarshal error: %w", err)
	}

	title, ok := result["title"].(string)
	if !ok {
		return "", nil // Title generation failed, return empty string
	}

	return title, nil
}

// AnswerQuestion sends a user's answer to a question to resume the agent workflow.
// The mode the run started in is restored from the agent's saved working state,
// so it does not need to be re-sent here.
func (c *AIServiceClient) AnswerQuestion(sessionID string, selectedOption string, answer string, questionText string, identity Identity, userMessageID, assistantMessageID string) (map[string]interface{}, error) {
	payload := map[string]interface{}{
		"answer":          answer,
		"selected_option": selectedOption,
		"question_text":   questionText,
	}
	if userMessageID != "" && assistantMessageID != "" {
		payload["user_message_id"] = userMessageID
		payload["assistant_message_id"] = assistantMessageID
	}
	url := fmt.Sprintf("%s/api/v1/generate/sessions/%s/answer-question", c.baseURL, sessionID)
	return c.postJSON(url, payload, identity)
}

// ReplaceImageInSlide tells the AI service a replacement image was saved for
// one asset and asks it to resume the agent so the slide using that asset
// gets regenerated with it (#187). The file itself already lives on the
// shared output volume by the time this is called - the AI service reads it
// from filePath rather than the image travelling through this request too.
func (c *AIServiceClient) ReplaceImageInSlide(sessionID, assetID, filePath, originalFilename string, fileSizeBytes int64, slideIndex int, identity Identity) (map[string]interface{}, error) {
	payload := map[string]interface{}{
		"file_path":         filePath,
		"original_filename": originalFilename,
		"file_size_bytes":   fileSizeBytes,
		"slide_index":       slideIndex,
	}
	url := fmt.Sprintf("%s/api/v1/generate/sessions/%s/assets/%s/replace-in-slide", c.baseURL, sessionID, assetID)
	return c.postJSON(url, payload, identity)
}
