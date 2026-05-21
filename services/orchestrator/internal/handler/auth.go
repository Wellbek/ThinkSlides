package handler

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/crypto/bcrypt"
	"context"
)

type AuthHandler struct {
	jwtSecret  string
	dbURL      string
	pool       *pgxpool.Pool
}

func NewAuthHandler(jwtSecret string, dbURL string) *AuthHandler {
	h := &AuthHandler{
		jwtSecret: jwtSecret,
		dbURL:     dbURL,
	}
	// Initialize connection pool lazily
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		pool, err := pgxpool.New(ctx, dbURL)
		if err == nil {
			h.pool = pool
		}
	}()
	return h
}

type LoginRequest struct {
	Email    string `json:"email" binding:"required"`
	Password string `json:"password" binding:"required"`
}

type RegisterRequest struct {
	Email       string `json:"email" binding:"required"`
	Password    string `json:"password" binding:"required"`
	DisplayName string `json:"display_name"`
	OrgName     string `json:"org_name"`
}

func (h *AuthHandler) Login(c *gin.Context) {
	var req LoginRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()

	// Look up user
	var userID, orgID string
	var passwordHash, displayName, role string
	err := h.pool.QueryRow(ctx,
		`SELECT u.id, u.organisation_id, u.password_hash, u.display_name, u.role
		 FROM users u WHERE u.email = $1`, req.Email,
	).Scan(&userID, &orgID, &passwordHash, &displayName, &role)

	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "Invalid credentials"})
		return
	}

	// Verify password
	if err := bcrypt.CompareHashAndPassword([]byte(passwordHash), []byte(req.Password)); err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "Invalid credentials"})
		return
	}

	// Generate JWT
	token, err := h.generateToken(userID, orgID, role)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Token generation failed"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"token": token,
		"user": gin.H{
			"id":             userID,
			"email":          req.Email,
			"displayName":    displayName,
			"organisationId": orgID,
			"role":           role,
		},
	})
}

func (h *AuthHandler) Register(c *gin.Context) {
	var req RegisterRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
		return
	}

	if h.pool == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "Database not ready"})
		return
	}

	ctx := c.Request.Context()

	// Hash password
	hashedPassword, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Password hashing failed"})
		return
	}

	// Create or get organisation
	var orgID string
	orgName := req.OrgName
	if orgName == "" {
		orgName = "My Organisation"
	}

	err = h.pool.QueryRow(ctx,
		`INSERT INTO organisations (name, slug) VALUES ($1, $2)
		 ON CONFLICT (slug) DO UPDATE SET updated_at = NOW()
		 RETURNING id`,
		orgName, slugify(orgName),
	).Scan(&orgID)

	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Failed to create organisation"})
		return
	}

	// Create user
	var userID string
	displayName := req.DisplayName
	if displayName == "" {
		displayName = req.Email
	}

	err = h.pool.QueryRow(ctx,
		`INSERT INTO users (organisation_id, email, password_hash, display_name, role)
		 VALUES ($1, $2, $3, $4, 'member')
		 RETURNING id`,
		orgID, req.Email, string(hashedPassword), displayName,
	).Scan(&userID)

	if err != nil {
		c.JSON(http.StatusConflict, gin.H{"error": "User already exists"})
		return
	}

	// Generate JWT
	token, err := h.generateToken(userID, orgID, "member")
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Token generation failed"})
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"token": token,
		"user": gin.H{
			"id":             userID,
			"email":          req.Email,
			"displayName":    displayName,
			"organisationId": orgID,
			"role":           "member",
		},
	})
}

func (h *AuthHandler) generateToken(userID, orgID, role string) (string, error) {
	claims := jwt.MapClaims{
		"user_id":   userID,
		"org_id":    orgID,
		"role":      role,
		"iat":       time.Now().Unix(),
		"exp":       time.Now().Add(72 * time.Hour).Unix(),
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString([]byte(h.jwtSecret))
}

func slugify(name string) string {
	// Simple slugify — replace spaces with hyphens, lowercase
	result := make([]byte, 0, len(name))
	for _, c := range name {
		if c >= 'a' && c <= 'z' || c >= '0' && c <= '9' {
			result = append(result, byte(c))
		} else if c >= 'A' && c <= 'Z' {
			result = append(result, byte(c+32))
		} else if c == ' ' || c == '_' {
			result = append(result, '-')
		}
	}
	return string(result)
}