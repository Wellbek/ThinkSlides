package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	_ "github.com/jackc/pgx/v5/stdlib"

	"github.com/slideagent/orchestrator/internal/client"
	"github.com/slideagent/orchestrator/internal/handler"
	"github.com/slideagent/orchestrator/internal/middleware"
	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/repository"
	"github.com/slideagent/orchestrator/internal/sse"
	"github.com/slideagent/orchestrator/internal/worker"
)

func main() {
	// ── Configuration from environment ─────────────────────────────────
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		log.Fatal("DATABASE_URL is required")
	}

	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		log.Fatal("REDIS_URL is required")
	}

	jwtSecret := os.Getenv("JWT_SECRET")
	if jwtSecret == "" {
		jwtSecret = "thinkslides-dev-jwt-secret-change-in-production"
		log.Println("WARNING: JWT_SECRET not set, using dev default")
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	aiServiceURL := os.Getenv("AI_SERVICE_URL")
	if aiServiceURL == "" {
		aiServiceURL = "http://localhost:8000"
	}

	devMode := os.Getenv("DEV_MODE") == "true"
	if devMode {
		log.Println("INFO: DEV_MODE enabled — /auth/dev-login endpoint is active")
	}

	// ── Database ───────────────────────────────────────────────────────
	db, err := sql.Open("pgx", dbURL)
	if err != nil {
		log.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	if err := db.Ping(); err != nil {
		log.Fatalf("Failed to ping database: %v", err)
	}
	log.Println("Connected to database")

	userRepo := repository.NewUserRepository(db)

	// ── Services ───────────────────────────────────────────────────────
	redisQueue, err := queue.NewRedisQueue(redisURL)
	if err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	log.Println("Connected to Redis")

	hub := sse.NewHub()
	go hub.Run()

	aiClient := client.NewAIServiceClient(aiServiceURL)

	// ── Handlers ───────────────────────────────────────────────────────
	authHandler := handler.NewAuthHandler(userRepo, jwtSecret, devMode)
	sessionHandler := handler.NewSessionHandler(redisQueue, hub, aiClient, dbURL)

	// ── HTTP Server ────────────────────────────────────────────────────
	r := gin.Default()

	// CORS
	r.Use(middleware.CORS("*"))

	// Health check
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	// Auth routes (public)
	r.POST("/api/auth/register", authHandler.HandleRegister)
	r.POST("/api/auth/login", authHandler.HandleLogin)
	r.POST("/api/auth/dev-login", authHandler.HandleDevLogin)

	// Protected routes
	protected := r.Group("/api")
	protected.Use(middleware.AuthRequired())
	{
		protected.GET("/sessions", sessionHandler.ListSessions)
		protected.POST("/sessions", sessionHandler.CreateSession)
		protected.GET("/sessions/:id", sessionHandler.GetSession)
		protected.DELETE("/sessions/:id", sessionHandler.DeleteSession)
		protected.POST("/sessions/:id/messages", sessionHandler.SendMessage)
		protected.GET("/sessions/:id/messages", sessionHandler.GetSessionMessages)
		protected.GET("/sessions/:id/files", sessionHandler.GetSessionFiles)
		protected.GET("/sessions/:id/events", sessionHandler.StreamEvents)
		protected.GET("/templates", sessionHandler.ListTemplates)
		protected.GET("/files/:id", sessionHandler.DownloadFile)
	}

	// ── Background workers ─────────────────────────────────────────────
	processor := worker.NewProcessor(redisQueue, hub, aiClient)
	go processor.Start(context.Background())

	// ── Start ──────────────────────────────────────────────────────────
	addr := fmt.Sprintf(":%s", port)
	log.Printf("Orchestrator listening on %s", addr)
	if err := r.Run(addr); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}