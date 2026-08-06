package handler

import (
	"context"
	"database/sql"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"

	"github.com/thinkslides/orchestrator/internal/repository"
)

// UserStore abstracts the persistence operations needed by AuthHandler.
type UserStore interface {
	GetByEmail(ctx context.Context, email string) (*repository.User, error)
	DB() *sql.DB
}

// Compile-time check that UserRepository satisfies UserStore.
var _ UserStore = (*repository.UserRepository)(nil)

// AuthHandler handles registration, login, and dev-login endpoints.
type AuthHandler struct {
	store     UserStore
	jwtSecret []byte
	devMode   bool
}

// NewAuthHandler creates an AuthHandler.
// devMode enables the /auth/dev-login endpoint (auto-creates a dev account).
func NewAuthHandler(store UserStore, jwtSecret string, devMode bool) *AuthHandler {
	return &AuthHandler{
		store:     store,
		jwtSecret: []byte(jwtSecret),
		devMode:   devMode,
	}
}

// HandleRegister creates a new user with an auto-generated "Personal Workspace"
// organisation. No org name or slug is required from the user — both are generated.
// Display name defaults to the part of the email before "@" if not provided.
func (h *AuthHandler) HandleRegister(c *gin.Context) {
	var req struct {
		Email       string `json:"email"`
		Password    string `json:"password"`
		DisplayName string `json:"display_name"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request body"})
		return
	}

	req.Email = strings.TrimSpace(strings.ToLower(req.Email))
	req.DisplayName = strings.TrimSpace(req.DisplayName)

	if req.Email == "" || req.Password == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "email and password are required"})
		return
	}
	if len(req.Password) < 8 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "password must be at least 8 characters"})
		return
	}

	// Default display name to email prefix if not provided
	if req.DisplayName == "" {
		if idx := strings.Index(req.Email, "@"); idx > 0 {
			req.DisplayName = req.Email[:idx]
		} else {
			req.DisplayName = req.Email
		}
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to hash password"})
		return
	}

	ctx := c.Request.Context()
	db := h.store.DB()
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to begin transaction"})
		return
	}
	defer tx.Rollback()

	// Auto-create a "Personal Workspace" organisation with a generated slug.
	orgID := uuid.New().String()
	orgSlug := "pw-" + uuid.New().String()[:8]
	now := time.Now()

	_, err = tx.ExecContext(ctx,
		`INSERT INTO organisations (id, name, slug, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5)`,
		orgID, "Personal Workspace", orgSlug, now, now,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create personal workspace"})
		return
	}

	// Create the user.
	userID := uuid.New().String()
	_, err = tx.ExecContext(ctx,
		`INSERT INTO users (id, organisation_id, email, password_hash, display_name, role, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		userID, orgID, req.Email, string(hash), req.DisplayName, "owner", now, now,
	)
	if err != nil {
		if strings.Contains(err.Error(), "23505") {
			c.JSON(http.StatusConflict, gin.H{"error": "email already registered"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create user"})
		return
	}

	if err = tx.Commit(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to commit transaction"})
		return
	}

	token, err := h.generateToken(userID, orgID, "owner", req.Email)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate token"})
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"token": token,
		"user": gin.H{
			"id":              userID,
			"organisation_id": orgID,
			"email":           req.Email,
			"display_name":    req.DisplayName,
			"role":            "owner",
		},
	})
}

// HandleLogin authenticates an existing user with email and password.
func (h *AuthHandler) HandleLogin(c *gin.Context) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request body"})
		return
	}

	req.Email = strings.TrimSpace(strings.ToLower(req.Email))

	if req.Email == "" || req.Password == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "email and password are required"})
		return
	}

	ctx := c.Request.Context()
	user, err := h.store.GetByEmail(ctx, req.Email)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid email or password"})
		return
	}

	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)); err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid email or password"})
		return
	}

	// Handle nullable organisation_id — empty string in JWT if user has no org.
	orgID := ""
	if user.OrganisationID != nil {
		orgID = *user.OrganisationID
	}

	token, err := h.generateToken(user.ID, orgID, user.Role, user.Email)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate token"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"token": token,
		"user": gin.H{
			"id":              user.ID,
			"organisation_id": orgID,
			"email":           user.Email,
			"display_name":    user.DisplayName,
			"role":            user.Role,
		},
	})
}

// HandleDevLogin provides a one-click login for local development.
// Only available when DEV_MODE=true. On first call it creates a dev organisation
// and dev user; on subsequent calls it just returns a JWT for the existing user.
func (h *AuthHandler) HandleDevLogin(c *gin.Context) {
	if !h.devMode {
		c.JSON(http.StatusForbidden, gin.H{"error": "dev mode is not enabled"})
		return
	}

	ctx := c.Request.Context()
	devEmail := "dev@thinkslices.local"

	// Try to find existing dev user.
	user, err := h.store.GetByEmail(ctx, devEmail)
	if err == nil {
		orgID := ""
		if user.OrganisationID != nil {
			orgID = *user.OrganisationID
		}

		token, terr := h.generateToken(user.ID, orgID, user.Role, user.Email)
		if terr != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate token"})
			return
		}

		c.JSON(http.StatusOK, gin.H{
			"token": token,
			"user": gin.H{
				"id":              user.ID,
				"organisation_id": orgID,
				"email":           user.Email,
				"display_name":    user.DisplayName,
				"role":            user.Role,
			},
		})
		return
	}

	// First time: create dev org + dev user in a transaction.
	db := h.store.DB()
	if db == nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "database not available"})
		return
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to begin transaction"})
		return
	}
	defer tx.Rollback()

	orgID := uuid.New().String()
	now := time.Now()

	_, err = tx.ExecContext(ctx,
		`INSERT INTO organisations (id, name, slug, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5)`,
		orgID, "Dev Organisation", "dev-org", now, now,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create dev organisation"})
		return
	}

	userID := uuid.New().String()
	hash, _ := bcrypt.GenerateFromPassword([]byte("dev"), bcrypt.DefaultCost)

	_, err = tx.ExecContext(ctx,
		`INSERT INTO users (id, organisation_id, email, password_hash, display_name, role, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		userID, orgID, devEmail, string(hash), "Dev User", "owner", now, now,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create dev user"})
		return
	}

	if err = tx.Commit(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to commit transaction"})
		return
	}

	token, err := h.generateToken(userID, orgID, "owner", devEmail)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate token"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"token": token,
		"user": gin.H{
			"id":              userID,
			"organisation_id": orgID,
			"email":           devEmail,
			"display_name":    "Dev User",
			"role":            "owner",
		},
	})
}

// generateToken creates a signed JWT with user_id, org_id, role, and email claims.
// org_id may be an empty string for users without an organisation.
func (h *AuthHandler) generateToken(userID, orgID, role, email string) (string, error) {
	claims := jwt.MapClaims{
		"user_id": userID,
		"org_id":  orgID,
		"role":    role,
		"email":   email,
		"exp":     time.Now().Add(72 * time.Hour).Unix(),
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(h.jwtSecret)
}