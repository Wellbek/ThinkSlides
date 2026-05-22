package main

import (
	"context"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"

	"github.com/slideagent/orchestrator/internal/client"
	"github.com/slideagent/orchestrator/internal/handler"
	"github.com/slideagent/orchestrator/internal/middleware"
	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/sse"
	"github.com/slideagent/orchestrator/internal/worker"
)

func main() {
	// Read environment variables
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}
	aiServiceURL := os.Getenv("AI_SERVICE_URL")
	if aiServiceURL == "" {
		aiServiceURL = "http://localhost:8000"
	}
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		dbURL = "postgres://postgres:password@localhost:5432/slideagent?sslmode=disable"
	}
	jwtSecret := os.Getenv("JWT_SECRET")
	if jwtSecret == "" {
		jwtSecret = "your-secret-key-change-in-production"
	}

	// Logger
	logger := slog.Default()

	// Initialize Redis queue
	redisQueue, err := queue.NewRedisQueue(redisURL)
	if err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	defer redisQueue.Close()

	// Initialize AI client (from shared package)
	aiClient := client.NewAIServiceClient(aiServiceURL)

	// Initialize SSE hub
	hub := sse.NewHub()
	go hub.Run()

	// Initialize HTTP handlers
	authHandler := handler.NewAuthHandler(jwtSecret, logger)                    // correct: uses *slog.Logger
	sessionHandler := handler.NewSessionHandler(redisQueue, hub, aiClient, dbURL)

	// Start background worker (pass hub as second argument)
	workerProc := worker.NewProcessor(redisQueue, hub, aiClient)               // fixed: added hub argument
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go workerProc.Start(ctx)

	// Setup Gin router
	r := gin.Default()
	r.Use(cors.Default())

	// Public routes
	r.POST("/api/auth/login", authHandler.HandleLogin)
	r.POST("/api/auth/register", authHandler.HandleRegister)

	// Protected routes
	authMw := middleware.AuthRequired(jwtSecret)
	api := r.Group("/api")
	api.Use(authMw)
	{
		api.GET("/sessions", sessionHandler.ListSessions)
		api.POST("/sessions", sessionHandler.CreateSession)
		api.GET("/sessions/:id", sessionHandler.GetSession)
		api.DELETE("/sessions/:id", sessionHandler.DeleteSession)
		api.POST("/sessions/:id/messages", sessionHandler.SendMessage)
		api.GET("/sessions/:id/events", sessionHandler.StreamEvents)
		api.GET("/files/:id", sessionHandler.DownloadFile)
	}

	// Start server
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	srv := &http.Server{
		Addr:    ":" + port,
		Handler: r,
	}

	go func() {
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Failed to start server: %v", err)
		}
	}()

	log.Printf("Orchestrator listening on port %s", port)

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Println("Shutting down...")

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	cancel() // stop worker
	_ = srv.Shutdown(shutdownCtx)

	log.Println("Server exited")
}