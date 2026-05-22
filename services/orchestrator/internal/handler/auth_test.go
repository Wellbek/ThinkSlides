package handler

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"golang.org/x/crypto/bcrypt"

	"github.com/slideagent/orchestrator/internal/repository"
)

// ── Mock UserStore ───────────────────────────────────────────────────

type mockUserStore struct {
	users  map[string]*repository.User // keyed by email
	nextID int
}

func newMockStore() *mockUserStore {
	return &mockUserStore{
		users: make(map[string]*repository.User),
	}
}

func (m *mockUserStore) GetByEmail(_ context.Context, email string) (*repository.User, error) {
	u, ok := m.users[email]
	if !ok {
		return nil, repository.ErrNotFound
	}
	return u, nil
}

// DB is not used in login tests. HandleRegister is covered by integration
// tests since it needs a real *sql.DB for transactions.
func (m *mockUserStore) DB() *sql.DB {
	return nil
}

// seedUser adds a user to the mock store with a bcrypt-hashed password.
func (m *mockUserStore) seedUser(email, password, displayName, role string) {
	hash, _ := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	m.nextID++
	m.users[email] = &repository.User{
		ID:             fmt.Sprintf("user-%d", m.nextID),
		OrganisationID: "org-1",
		Email:          email,
		PasswordHash:   string(hash),
		DisplayName:    displayName,
		Role:           role,
	}
}

// ── Tests ────────────────────────────────────────────────────────────

func TestHandleLogin_Success(t *testing.T) {
	store := newMockStore()
	store.seedUser("alice@example.com", "secret123", "Alice", "owner")

	h := NewAuthHandler(store, "test-secret")

	body, _ := json.Marshal(LoginRequest{Email: "alice@example.com", Password: "secret123"})
	req := httptest.NewRequest(http.MethodPost, "/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleLogin(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp AuthResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Token == "" {
		t.Error("expected non-empty token")
	}
	if resp.User.Email != "alice@example.com" {
		t.Errorf("expected email alice@example.com, got %s", resp.User.Email)
	}
	if resp.User.DisplayName != "Alice" {
		t.Errorf("expected display name Alice, got %s", resp.User.DisplayName)
	}
	if resp.OrgID != "org-1" {
		t.Errorf("expected org_id org-1, got %s", resp.OrgID)
	}
}

func TestHandleLogin_InvalidPassword(t *testing.T) {
	store := newMockStore()
	store.seedUser("alice@example.com", "secret123", "Alice", "owner")

	h := NewAuthHandler(store, "test-secret")

	body, _ := json.Marshal(LoginRequest{Email: "alice@example.com", Password: "wrongpassword"})
	req := httptest.NewRequest(http.MethodPost, "/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleLogin(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestHandleLogin_UserNotFound(t *testing.T) {
	store := newMockStore()

	h := NewAuthHandler(store, "test-secret")

	body, _ := json.Marshal(LoginRequest{Email: "nobody@example.com", Password: "secret123"})
	req := httptest.NewRequest(http.MethodPost, "/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleLogin(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestHandleLogin_MissingFields(t *testing.T) {
	store := newMockStore()
	h := NewAuthHandler(store, "test-secret")

	// Empty email.
	body, _ := json.Marshal(LoginRequest{Email: "", Password: "secret123"})
	req := httptest.NewRequest(http.MethodPost, "/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleLogin(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing email, got %d", w.Code)
	}

	// Empty password.
	body, _ = json.Marshal(LoginRequest{Email: "alice@example.com", Password: ""})
	req = httptest.NewRequest(http.MethodPost, "/auth/login", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()

	h.HandleLogin(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing password, got %d", w.Code)
	}
}

func TestHandleRegister_MissingFields(t *testing.T) {
	store := newMockStore()
	h := NewAuthHandler(store, "test-secret")

	// Missing email.
	body, _ := json.Marshal(RegisterRequest{Password: "secret123"})
	req := httptest.NewRequest(http.MethodPost, "/auth/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleRegister(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing email, got %d", w.Code)
	}

	// Short password.
	body, _ = json.Marshal(RegisterRequest{Email: "bob@example.com", Password: "short"})
	req = httptest.NewRequest(http.MethodPost, "/auth/register", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()

	h.HandleRegister(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for short password, got %d", w.Code)
	}
}

func TestHandleRegister_InvalidBody(t *testing.T) {
	store := newMockStore()
	h := NewAuthHandler(store, "test-secret")

	req := httptest.NewRequest(http.MethodPost, "/auth/register", bytes.NewReader([]byte("not json")))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.HandleRegister(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid body, got %d", w.Code)
	}
}