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

// SendMessage sends a message to the AI service for processing.
func (c *AIServiceClient) SendMessage(sessionID string, content string, attachments []map[string]interface{}) (map[string]interface{}, error) {
	payload := map[string]interface{}{
		"content":     content,
		"attachments": attachments,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal error: %w", err)
	}

	url := fmt.Sprintf("%s/api/v1/generate/sessions/%s/messages", c.baseURL, sessionID)
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