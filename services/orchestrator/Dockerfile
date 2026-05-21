FROM golang:1.25-alpine AS builder

WORKDIR /app
# Copy go mod files
COPY go.mod go.sum ./
RUN go mod download

# Install dependencies
RUN apk add --no-cache git

# Copy source code
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY proto/ ./proto/

# Build the binary
RUN CGO_ENABLED=0 GOOS=linux go build -o /bin/orchestrator ./cmd/server

# Final image
FROM alpine:3.20

RUN apk add --no-cache ca-certificates

COPY --from=builder /bin/orchestrator /bin/orchestrator

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:8080/health || exit 1

CMD ["/bin/orchestrator"]