package handler

import (
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
)

// AuthHandler handles authentication endpoints.
type AuthHandler struct {
	jwtSecret string
	logger    *slog.Logger
}

// NewAuthHandler creates a new auth handler.
func NewAuthHandler(jwtSecret string, logger *slog.Logger) *AuthHandler {
	if logger == nil {
		logger = slog.Default()
	}
	return &AuthHandler{
		jwtSecret: jwtSecret,
		logger:    logger,
	}
}

// LoginRequest is the JSON body for login requests.
type LoginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

// RegisterRequest is the JSON body for register requests.
type RegisterRequest struct {
	Email     string `json:"email"`
	Password  string `json:"password"`
	FirstName string `json:"first_name"`
	LastName  string `json:"last_name"`
	OrgName   string `json:"org_name"`
}

// TokenResponse is the JSON response after successful auth.
type TokenResponse struct {
	Token     string `json:"token"`
	ExpiresAt int64  `json:"expires_at"`
}

// HandleLogin handles POST /auth/login
func (h *AuthHandler) HandleLogin(c *gin.Context) {
	var req LoginRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request body"})
		return
	}

	// TODO: Look up user in database, verify password
	// For now, placeholder logic
	hashedPassword, _ := bcrypt.GenerateFromPassword([]byte("demo"), bcrypt.DefaultCost)
	if err := bcrypt.CompareHashAndPassword(hashedPassword, []byte(req.Password)); req.Email == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid credentials"})
		return
	}

	token, expiresAt, err := h.generateToken("user-placeholder", "org-placeholder", "member")
	if err != nil {
		h.logger.Error("failed to generate token", slog.String("error", err.Error()))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "token generation failed"})
		return
	}

	c.JSON(http.StatusOK, TokenResponse{
		Token:     token,
		ExpiresAt: expiresAt,
	})
}

// HandleRegister handles POST /auth/register
func (h *AuthHandler) HandleRegister(c *gin.Context) {
	var req RegisterRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request body"})
		return
	}

	if req.Email == "" || req.Password == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "email and password are required"})
		return
	}

	// Hash the password
	hashedPassword, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		h.logger.Error("failed to hash password", slog.String("error", err.Error()))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "registration failed"})
		return
	}

	// TODO: Store user in database
	_ = hashedPassword

	token, expiresAt, err := h.generateToken("user-placeholder", "org-placeholder", "member")
	if err != nil {
		h.logger.Error("failed to generate token", slog.String("error", err.Error()))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "token generation failed"})
		return
	}

	c.JSON(http.StatusCreated, TokenResponse{
		Token:     token,
		ExpiresAt: expiresAt,
	})
}

func (h *AuthHandler) generateToken(userID, orgID, role string) (string, int64, error) {
	expiresAt := time.Now().Add(72 * time.Hour).Unix()

	claims := jwt.MapClaims{
		"user_id": userID,
		"org_id":  orgID,
		"role":    role,
		"exp":     expiresAt,
		"iat":     time.Now().Unix(),
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	tokenString, err := token.SignedString([]byte(h.jwtSecret))
	if err != nil {
		return "", 0, err
	}

	return tokenString, expiresAt, nil
}