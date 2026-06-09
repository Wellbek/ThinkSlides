package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"

	"github.com/thinkslides/orchestrator/internal/repository"
)

// ── mock ──────────────────────────────────────────────────────────────

type mockUserStore struct {
	users map[string]*repository.User // email → user
}

func newMockUserStore() *mockUserStore {
	return &mockUserStore{users: make(map[string]*repository.User)}
}

func (m *mockUserStore) GetByEmail(_ context.Context, email string) (*repository.User, error) {
	u, ok := m.users[email]
	if !ok {
		return nil, repository.ErrNotFound
	}
	return u, nil
}

func (m *mockUserStore) DB() interface{ ExecContext(context.Context, string, ...interface{}) (interface{}, error) } {
	return nil // not used in unit tests — dev-login + register need *repository.UserRepository
}

func seedUser(m *mockUserStore, email, password, role string) {
	hash, _ := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	orgID := "00000000-0000-0000-0000-000000000001"
	m.users[email] = &repository.User{
		ID:             "user-1",
		OrganisationID: &orgID,
		Email:          email,
		PasswordHash:   string(hash),
		Name:           "Test User",
		Role:           role,
	}
}

// ── helpers ───────────────────────────────────────────────────────────

func setupRouter(h *AuthHandler) *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/auth/login", h.HandleLogin)
	r.POST("/auth/register", h.HandleRegister)
	r.POST("/auth/dev-login", h.HandleDevLogin)
	return r
}

func parseBody(body string) map[string]interface{} {
	var m map[string]interface{}
	json.Unmarshal([]byte(body), &m)
	return m
}

// ── login tests ───────────────────────────────────────────────────────

func TestHandleLogin_Success(t *testing.T) {
	store := newMockUserStore()
	seedUser(store, "alice@example.com", "password123", "admin")

	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	body := `{"email":"alice@example.com","password":"password123"}`
	req := httptest.NewRequest(http.MethodPost, "/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	resp := parseBody(w.Body.String())
	if resp["token"] == nil {
		t.Fatal("expected token in response")
	}
}

func TestHandleLogin_InvalidPassword(t *testing.T) {
	store := newMockUserStore()
	seedUser(store, "alice@example.com", "password123", "admin")

	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	body := `{"email":"alice@example.com","password":"wrongpassword"}`
	req := httptest.NewRequest(http.MethodPost, "/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestHandleLogin_UserNotFound(t *testing.T) {
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	body := `{"email":"nobody@example.com","password":"password123"}`
	req := httptest.NewRequest(http.MethodPost, "/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestHandleLogin_MissingFields(t *testing.T) {
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	body := `{"email":"alice@example.com"}`
	req := httptest.NewRequest(http.MethodPost, "/auth/login", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

// ── register validation tests ─────────────────────────────────────────

func TestHandleRegister_MissingEmail(t *testing.T) {
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	body := `{"password":"password123"}`
	req := httptest.NewRequest(http.MethodPost, "/auth/register", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestHandleRegister_ShortPassword(t *testing.T) {
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	body := `{"email":"bob@example.com","password":"short"}`
	req := httptest.NewRequest(http.MethodPost, "/auth/register", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestHandleRegister_InvalidBody(t *testing.T) {
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", false)
	r := setupRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/auth/register", strings.NewReader("not json"))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

// ── dev-login tests ───────────────────────────────────────────────────

func TestHandleDevLogin_Disabled(t *testing.T) {
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", false) // devMode=false
	r := setupRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/auth/dev-login", nil)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", w.Code)
	}
}

func TestHandleDevLogin_EnabledButNoDB(t *testing.T) {
	// When devMode=true but the store is a mock (not *repository.UserRepository),
	// the handler should return 500 because it can't start a transaction.
	store := newMockUserStore()
	h := NewAuthHandler(store, "test-secret", true) // devMode=true
	r := setupRouter(h)

	req := httptest.NewRequest(http.MethodPost, "/auth/dev-login", nil)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	r.ServeHTTP(w, req)

	// Mock store can't create the dev user, so it returns 500
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 (no real DB in unit test), got %d", w.Code)
	}
}