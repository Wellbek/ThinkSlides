FROM node:20-alpine AS base

# Install dependencies only when needed
FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

# Copy root package.json and lockfile (if any)
COPY package.json package-lock.json* ./

# Copy all workspace package.json files (required for npm workspaces)
COPY apps/web/package.json apps/web/package.json
COPY packages/schemas/package.json packages/schemas/package.json

# Install dependencies from root, using workspaces
RUN npm ci

# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Build the Next.js application from the web workspace
WORKDIR /app/apps/web
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Production image
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Copy the standalone output correctly
# The standalone output is created at /app/apps/web/.next/standalone
# Inside that, the structure mirrors the workspace
COPY --from=builder /app/apps/web/public ./apps/web/public
COPY --from=builder /app/apps/web/.next/standalone ./
COPY --from=builder /app/apps/web/.next/static ./apps/web/.next/static

# Set the correct working directory for the standalone server
WORKDIR /app/apps/web

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# The server.js is now at /app/apps/web/server.js
CMD ["node", "server.js"]