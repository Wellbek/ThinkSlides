package handler

import (
	"testing"
)

func TestSlugify(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"Hello World", "hello-world"},
		{"My Organisation", "my-organisation"},
		{"Test123", "test123"},
		{"UPPER CASE", "upper-case"},
		{"under_score", "under-score"},
		{"  spaces  ", "--spaces--"},
		{"Special!@#Chars", "specialchars"},
	}

	for _, tt := range tests {
		result := slugify(tt.input)
		if result != tt.expected {
			t.Errorf("slugify(%q) = %q, want %q", tt.input, result, tt.expected)
		}
	}
}

func TestTokenGeneration(t *testing.T) {
	handler := &AuthHandler{
		jwtSecret: "test-secret-key",
	}

	token, err := handler.generateToken("user-123", "org-456", "admin")
	if err != nil {
		t.Fatalf("Failed to generate token: %v", err)
	}

	if token == "" {
		t.Fatal("Token should not be empty")
	}
}