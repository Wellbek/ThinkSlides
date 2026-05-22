package main

import (
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	_ "github.com/jackc/pgx/v5/stdlib"

	"github.com/slideagent/orchestrator/internal/handler"
	"github.com/slideagent/orchestrator/internal/middleware"
	"github.com/slideagent/orchestrator/internal/sse"
	"github.com/slideagent/orchestrator/internal/repository"
)

func main() {
	// ── Configuration from environment ───────────────────────────────
	dbURL := envOr("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/thinkslides?sslmode=disable")
	jwtSecret := envOr("JWT_SECRET", "dev-secret-change-me")
	port := envOr("PORT", "8080")
	aiServiceURL := envOr("AI_SERVICE_URL", "http://localhost:8000")

	// ── Database ─────────────────────────────────────────────────────
	db, err := sql.Open("pgx", dbURL)
	if err != nil {
		log.Fatalf("failed to open database: %v", err)
	}
	defer db.Close()

	if err := db.Ping(); err != nil {
		log.Fatalf("failed to ping database: %v", err)
	}
	log.Println("connected to PostgreSQL")

	// ── Repositories ─────────────────────────────────────────────────
	userRepo := repository.NewUserRepository(db)

	// ── Services ─────────────────────────────────────────────────────
	hub := sse.NewHub()
	go hub.Run()

	// ── Handlers ─────────────────────────────────────────────────────
	authHandler := handler.NewAuthHandler(userRepo, jwtSecret)

	// ── Router ───────────────────────────────────────────────────────
	r := gin.Default()
	r.Use(middleware.CORS("*"))

	// Public auth routes (now under /api)
	r.POST("/api/auth/register", func(c *gin.Context) {
		authHandler.HandleRegister(c.Writer, c.Request)
	})
	r.POST("/api/auth/login", func(c *gin.Context) {
		authHandler.HandleLogin(c.Writer, c.Request)
	})

	// Protected routes (also under /api)
	api := r.Group("/api")
	api.Use(middleware.AuthRequired())
	{
		// SSE endpoint
		api.GET("/sessions/:id/events", func(c *gin.Context) {
			sessionID := c.Param("id")
			sse.Handler(hub, sessionID)(c)
		})
	}

	// Health check.
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	log.Printf("orchestrator listening on :%s (ai=%s)\n", port, aiServiceURL)
	if err := r.Run(fmt.Sprintf(":%s", port)); err != nil {
		log.Fatalf("server error: %v", err)
	}
}

// envOr returns the environment variable value or the given fallback.
func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}