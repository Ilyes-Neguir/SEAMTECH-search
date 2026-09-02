# syntax=docker/dockerfile:1

# ---- deps: install dependencies with pnpm (uses the committed lockfile) ----
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# ---- builder: compile the production Next.js build ----
FROM node:22-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Disable telemetry and build in standalone mode (see next.config.mjs).
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# ---- runner: minimal runtime image, no build tooling or dev deps ----
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
  && adduser --system --uid 1001 nextjs

# Standalone output: a self-contained server.js plus only the node_modules
# it actually needs. Static assets and public files are copied alongside it.
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/api/health >/dev/null 2>&1 || exit 1

CMD ["node", "server.js"]
