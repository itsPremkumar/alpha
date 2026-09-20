# Alpha Frontend (Next.js) — multi-stage production build
# Stage 1 (deps):    install node_modules with a persistent pnpm store cache
# Stage 2 (builder): compile the standalone Next.js production bundle
# Stage 3 (prod):    minimal runtime image running the standalone server
#
# Build context is the REPOSITORY ROOT (see docker/docker-compose.yaml), so all
# COPY paths are prefixed with frontend/.

ARG NODE_IMAGE=node:22-alpine
ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store
ARG NPM_REGISTRY

# ── Stage 1: dependencies ──────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS deps
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@latest --activate
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi

COPY frontend/package.json frontend/pnpm-lock.yaml ./

# Persist the pnpm store across builds; fail loudly if the lockfile is stale.
RUN --mount=type=cache,target=${PNPM_STORE_PATH} \
    pnpm install --frozen-lockfile

# ── Stage 2: build ─────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS builder
ARG NPM_REGISTRY
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@latest --activate
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

ENV NEXT_TELEMETRY_DISABLED=1
# Produce the self-contained server (frontend/next.config.mjs keys off this).
ENV NEXT_CONFIG_BUILD_OUTPUT=standalone

RUN pnpm build

# ── Stage 3: production runtime ────────────────────────────────────────────
FROM ${NODE_IMAGE} AS prod
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Non-root runtime user.
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs

# The standalone output already contains a minimal node_modules and server.js.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
# public/ is optional (static assets); only copied when it exists in the repo.

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]

# ── Dev target (compose-dev): full source, hot reload ──────────────────────
FROM ${NODE_IMAGE} AS dev
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@latest --activate
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=${PNPM_STORE_PATH} pnpm install
COPY frontend/ ./
EXPOSE 3000
CMD ["pnpm", "dev", "-p", "3000"]
