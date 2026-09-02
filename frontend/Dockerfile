# Production image for the Dibs Next.js frontend.
#
# The visitor's BROWSER calls the backend, not this container, so the API base
# URL is baked into the client bundle at build time and must be a URL the
# browser can reach (the api service's host-published port), never the
# compose-internal hostname `http://api:8000`.

# ---- build ----------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app

# Inlined into the client bundle at build time (NEXT_PUBLIC_* is compile-time).
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1

# Install deps against the lockfile first so the layer caches independently of
# source-only changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- runtime --------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `output: "standalone"` bundles the server and its trimmed deps; the static
# assets are copied alongside it.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# Dedicated non-root runtime user.
RUN addgroup -g 10001 -S nodejs \
    && adduser -u 10001 -S nextjs -G nodejs
USER nextjs

EXPOSE 3000

# Finite, dependency-free health check against the served app.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
