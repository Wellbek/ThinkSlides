package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
	_ "github.com/jackc/pgx/v5/stdlib"

	"github.com/thinkslides/orchestrator/internal/client"
	"github.com/thinkslides/orchestrator/internal/handler"
	"github.com/thinkslides/orchestrator/internal/middleware"
	"github.com/thinkslides/orchestrator/internal/queue"
	"github.com/thinkslides/orchestrator/internal/repository"
	"github.com/thinkslides/orchestrator/internal/sse"
	"github.com/thinkslides/orchestrator/internal/worker"
)

// retryConnect retries a function with exponential backoff until it succeeds
// or the maximum number of attempts is reached.
func retryConnect(name string, maxAttempts int, baseDelay time.Duration, fn func() error) error {
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		lastErr = fn()
		if lastErr == nil {
			return nil
		}
		if attempt < maxAttempts {
			delay := baseDelay * time.Duration(1<<(attempt-1)) // exponential backoff
			if delay > 30*time.Second {
				delay = 30 * time.Second
			}
			log.Printf("Failed to connect to %s (attempt %d/%d): %v — retrying in %s...",
				name, attempt, maxAttempts, lastErr, delay)
			time.Sleep(delay)
		}
	}
	return fmt.Errorf("failed to connect to %s after %d attempts: %w", name, maxAttempts, lastErr)
}

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

	previewDir := os.Getenv("PREVIEW_DIR")
	if previewDir == "" {
		previewDir = "/app/previews"
	}

	devMode := os.Getenv("DEV_MODE") == "true"
	if devMode {
		log.Println("INFO: DEV_MODE enabled — /auth/dev-login endpoint is active")
	}

	// ── Database (with retry) ──────────────────────────────────────────
	var db *sql.DB
	if err := retryConnect("database", 10, 2*time.Second, func() error {
		var openErr error
		db, openErr = sql.Open("pgx", dbURL)
		if openErr != nil {
			return openErr
		}
		return db.Ping()
	}); err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer db.Close()
	log.Println("Connected to database")

	// Create pgxpool for processor (required for concurrent inserts)
	var dbPool *pgxpool.Pool
	if err := retryConnect("database pool", 10, 2*time.Second, func() error {
		var poolErr error
		dbPool, poolErr = pgxpool.New(context.Background(), dbURL)
		if poolErr != nil {
			return poolErr
		}
		return dbPool.Ping(context.Background())
	}); err != nil {
		log.Fatalf("Failed to create connection pool: %v", err)
	}
	defer dbPool.Close()
	log.Println("Database pool ready")

	userRepo := repository.NewUserRepository(db)

	// ── Services (with retry) ──────────────────────────────────────────
	var redisQueue *queue.RedisQueue
	if err := retryConnect("redis", 10, 2*time.Second, func() error {
		var queueErr error
		redisQueue, queueErr = queue.NewRedisQueue(redisURL)
		return queueErr
	}); err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	log.Println("Connected to Redis")

	redisClient := redisQueue.GetClient()

	// Or simply create a new Redis client directly:
	// import "github.com/redis/go-redis/v9"
	// redisClient := redis.NewClient(&redis.Options{Addr: "redis:6379"})

	hub := sse.NewHub(redisClient)

	aiClient := client.NewAIServiceClient(aiServiceURL)

	// ── Handlers ───────────────────────────────────────────────────────
	authHandler := handler.NewAuthHandler(userRepo, jwtSecret, devMode)
	sessionHandler := handler.NewSessionHandler(redisQueue, hub, aiClient, dbURL, redisClient)
	settingsHandler := handler.NewSettingsHandler(aiServiceURL)
	connectionsHandler := handler.NewConnectionsHandler(aiServiceURL)

	// ── HTTP Server ────────────────────────────────────────────────────
	r := gin.Default()

	// CORS
	r.Use(middleware.CORS("*"))

	// Health check
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	// Serve preview images
	r.Static("/previews", previewDir)

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
		protected.POST("/templates", sessionHandler.UploadTemplate)
		protected.DELETE("/templates/:id", sessionHandler.DeleteTemplate)
		protected.PUT("/sessions/:id/template", sessionHandler.UpdateSessionTemplate)
		protected.PUT("/sessions/:id/title", sessionHandler.UpdateSessionTitle)

		// AI settings live in the Python service; the orchestrator proxies
		// them so the frontend has a single authenticated origin.
		protected.Any("/settings/*path", settingsHandler.Proxy)

		// Connections - MCP servers, image and figure renderers - are the same
		// arrangement: credentials stay in one service, identity is translated
		// here.
		protected.Any("/connections", connectionsHandler.Proxy)
		protected.Any("/connections/*path", connectionsHandler.Proxy)
	}

	// ── Background workers ─────────────────────────────────────────────
	processor := worker.NewProcessor(redisQueue, hub, aiClient, redisClient, dbPool)
	go processor.Start(context.Background())

	// ── Start ──────────────────────────────────────────────────────────
	addr := fmt.Sprintf(":%s", port)
	log.Printf("Orchestrator listening on %s", addr)
	if err := r.Run(addr); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}
