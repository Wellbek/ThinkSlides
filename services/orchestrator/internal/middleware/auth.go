package middleware

import (
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// getJWTSecret returns the JWT signing secret from the environment,
// falling back to a dev default if not set.
func getJWTSecret() []byte {
	secret := os.Getenv("JWT_SECRET")
	if secret == "" {
		secret = "thinkslides-dev-jwt-secret-change-in-production"
	}
	return []byte(secret)
}

// extractToken looks for the JWT in:
//  1. Authorization: Bearer <token> header
//  2. ?token=<token> query parameter (needed for EventSource SSE connections
//     which cannot set custom HTTP headers)
func extractToken(c *gin.Context) (string, error) {
	authHeader := c.GetHeader("Authorization")
	if authHeader != "" {
		parts := strings.SplitN(authHeader, " ", 2)
		if len(parts) == 2 && strings.EqualFold(parts[0], "Bearer") {
			return parts[1], nil
		}
		return "", fmt.Errorf("malformed Authorization header")
	}

	if tokenParam := c.Query("token"); tokenParam != "" {
		return tokenParam, nil
	}

	return "", fmt.Errorf("no token provided")
}

// AuthRequired is a Gin middleware that validates JWT tokens.
// It sets user_id, org_id, role, and email in the gin context.
// org_id may be an empty string for users who do not yet belong to an organisation.
func AuthRequired() gin.HandlerFunc {
	return func(c *gin.Context) {
		tokenString, err := extractToken(c)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Authorization required",
			})
			return
		}

		jwtSecret := getJWTSecret()

		token, err := jwt.Parse(tokenString, func(token *jwt.Token) (interface{}, error) {
			if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
			}
			return jwtSecret, nil
		})

		if err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Invalid or expired token",
			})
			return
		}

		claims, ok := token.Claims.(jwt.MapClaims)
		if !ok || !token.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
				"error": "Invalid token claims",
			})
			return
		}

		// Check expiry explicitly
		if exp, ok := claims["exp"].(float64); ok {
			if time.Now().Unix() > int64(exp) {
				c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
					"error": "Token expired",
				})
				return
			}
		}

		userID, _ := claims["user_id"].(string)
		orgID, _ := claims["org_id"].(string) // empty string is fine
		role, _ := claims["role"].(string)
		email, _ := claims["email"].(string)

		c.Set("user_id", userID)
		c.Set("org_id", orgID)
		c.Set("role", role)
		c.Set("email", email)

		c.Next()
	}
}