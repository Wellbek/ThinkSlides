package handler

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"

	"github.com/slideagent/orchestrator/internal/repository"
)

// UserStore abstracts the persistence operations needed by AuthHandler.
type UserStore interface {
	// GetByEmail retrieves a user by email. Returns repository.ErrNotFound if missing.
	GetByEmail(ctx context.Context, email string) (*repository.User, error)
	// DB returns the underlying *sql.DB for transactions.
	DB() *sql.DB
}

// Ensure UserRepository satisfies UserStore at compile time.
var _ UserStore = (*repository.UserRepository)(nil)

// AuthHandler handles registration and login endpoints.
type AuthHandler struct {
	store     UserStore
	jwtSecret []byte
}

// NewAuthHandler creates an AuthHandler with the given user store and JWT secret.
func NewAuthHandler(store UserStore, jwtSecret string) *AuthHandler {
	return &AuthHandler{
		store:     store,
		jwtSecret: []byte(jwtSecret),
	}
}

// RegisterRequest is the JSON body for POST /auth/register.
type RegisterRequest struct {
	Email       string `json:"email"`
	Password    string `json:"password"`
	DisplayName string `json:"display_name"`
	OrgName     string `json:"org_name"`
}

// LoginRequest is the JSON body for POST /auth/login.
type LoginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

// AuthResponse is the JSON body returned on successful auth.
type AuthResponse struct {
	Token string       `json:"token"`
	User  UserResponse `json:"user"`
	OrgID string       `json:"org_id"`
}

// UserResponse is the user subset returned in auth responses.
type UserResponse struct {
	ID          string `json:"id"`
	Email       string `json:"email"`
	DisplayName string `json:"display_name"`
	Role        string `json:"role"`
}

// HandleRegister a new organisation and user, returning a JWT.
func (h *AuthHandler) HandleRegister(w http.ResponseWriter, r *http.Request) {
	var req RegisterRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	if req.Email == "" || req.Password == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "email and password are required"})
		return
	}
	if len(req.Password) < 8 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "password must be at least 8 characters"})
		return
	}

	// Hash the password with bcrypt.
	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to hash password"})
		return
	}

	// Create the organisation and user in a single transaction.
	orgID := uuid.New().String()
	orgName := req.OrgName
	if orgName == "" {
		orgName = req.Email + "'s Organisation"
	}

	db := h.store.DB()
	tx, err := db.BeginTx(r.Context(), nil)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to begin transaction"})
		return
	}
	defer tx.Rollback()

	// Insert organisation.
	now := time.Now()
	_, err = tx.ExecContext(r.Context(), `
		INSERT INTO organisations (id, name, created_at, updated_at)
		VALUES ($1, $2, $3, $4)
	`, orgID, orgName, now, now)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to create organisation"})
		return
	}

	// Insert user.
	userID := uuid.New().String()
	_, err = tx.ExecContext(r.Context(), `
		INSERT INTO users (id, organisation_id, email, password_hash, display_name, role, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
	`, userID, orgID, req.Email, string(hash), req.DisplayName, "owner", now, now)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to create user"})
		return
	}

	if err := tx.Commit(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to commit transaction"})
		return
	}

	// Generate JWT.
	token, err := h.generateToken(userID, orgID, "owner")
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to generate token"})
		return
	}

	writeJSON(w, http.StatusCreated, AuthResponse{
		Token: token,
		User: UserResponse{
			ID:          userID,
			Email:       req.Email,
			DisplayName: req.DisplayName,
			Role:        "owner",
		},
		OrgID: orgID,
	})
}

// HandleLogin authenticates a user by email/password and returns a JWT.
func (h *AuthHandler) HandleLogin(w http.ResponseWriter, r *http.Request) {
	var req LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	if req.Email == "" || req.Password == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "email and password are required"})
		return
	}

	user, err := h.store.GetByEmail(r.Context(), req.Email)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid email or password"})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	// Compare password with stored bcrypt hash.
	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)); err != nil {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid email or password"})
		return
	}

	// Generate JWT.
	token, err := h.generateToken(user.ID, user.OrganisationID, user.Role)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to generate token"})
		return
	}

	displayName := user.DisplayName
	if displayName == "" {
		displayName = user.Email
	}

	writeJSON(w, http.StatusOK, AuthResponse{
		Token: token,
		User: UserResponse{
			ID:          user.ID,
			Email:       user.Email,
			DisplayName: displayName,
			Role:        user.Role,
		},
		OrgID: user.OrganisationID,
	})
}

// generateToken creates a signed JWT with user claims.
func (h *AuthHandler) generateToken(userID, orgID, role string) (string, error) {
	claims := jwt.MapClaims{
		"user_id": userID,
		"org_id":  orgID,
		"role":    role,
		"exp":     time.Now().Add(72 * time.Hour).Unix(),
		"iat":     time.Now().Unix(),
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(h.jwtSecret)
}

// writeJSON writes a JSON response with the given status code.
func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}