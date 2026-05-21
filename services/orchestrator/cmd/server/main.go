package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/slideagent/orchestrator/internal/handler"
	"github.com/slideagent/orchestrator/internal/middleware"
	"github.com/slideagent/orchestrator/internal/queue"
	"github.com/slideagent/orchestrator/internal/sse"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
)

func main() {
	// Read configuration from environment
	port := envOrDefault("ORCHESTRATOR_PORT", "8080")
	redisURL := envOrDefault("REDIS_URL", "redis://redis:6379/0")
	aiServiceURL := envOrDefault("AI_SERVICE_URL", "http://ai-service:8000")
	databaseURL := envOrDefault("DATABASE_URL", "postgresql://slideagent:slideagent_dev_password@postgres:5432/slideagent")
	jwtSecret := envOrDefault("JWT_SECRET", "change-this-to-a-secure-random-string-in-production")

	// Initialize Redis job queue
	jobQueue, err := queue.NewRedisQueue(redisURL)
	if err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	defer jobQueue.Close()

	// Initialize SSE hub
	hub := sse.NewHub()
	go hub.Run()

	// Initialize AI service client
	aiClient := handler.NewAIServiceClient(aiServiceURL)

	// Initialize job processor
	processor := queue.NewProcessor(jobQueue, hub, aiClient)
	go processor.Start(context.Background())

	// Set up Gin router
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.Use(gin.Recovery())
	router.Use(cors.New(cors.Config{
		AllowOrigins:     []string{"*"},
		AllowMethods:     []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Authorization"},
		ExposeHeaders:    []string{"Content-Type"},
		AllowCredentials: true,
		MaxAge:           12 * time.Hour,
	}))

	// Health endpoint
	router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok", "service": "orchestrator"})
	})

	// Auth routes (public)
	authHandler := handler.NewAuthHandler(jwtSecret, databaseURL)
	authGroup := router.Group("/auth")
	{
		authGroup.POST("/login", authHandler.Login)
		authGroup.POST("/register", authHandler.Register)
	}

	// API routes (authenticated)
	apiGroup := router.Group("/")
	apiGroup.Use(middleware.AuthRequired(jwtSecret))
	{
		// Session management
		sessionHandler := handler.NewSessionHandler(jobQueue, hub, aiClient, databaseURL)
		apiGroup.GET("/sessions", sessionHandler.ListSessions)
		apiGroup.POST("/sessions", sessionHandler.CreateSession)
		apiGroup.GET("/sessions/:id", sessionHandler.GetSession)
		apiGroup.DELETE("/sessions/:id", sessionHandler.DeleteSession)

		// Message handling — enqueues generation jobs
		apiGroup.POST("/sessions/:id/messages", sessionHandler.SendMessage)

		// SSE stream for agent events
		apiGroup.GET("/sessions/:id/events", sessionHandler.StreamEvents)

		// Generated files
		apiGroup.GET("/files/:id", sessionHandler.DownloadFile)
	}

	// Start server
	srv := &http.Server{
		Addr:    ":" + port,
		Handler: router,
	}

	go func() {
		log.Printf("Orchestrator starting on port %s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Server failed: %v", err)
		}
	}()

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down orchestrator...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("Server forced to shutdown: %v", err)
	}

	log.Println("Orchestrator stopped")
}

func envOrDefault(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}