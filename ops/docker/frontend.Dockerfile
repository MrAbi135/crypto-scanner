# syntax=docker/dockerfile:1
# Multi-stage frontend image (S0.2 §6.1). Builds the SPA and serves the static
# bundle from Caddy internally (the edge Caddy reverse-proxies to it in staging).
# Dev stays host-run (`pnpm dev`); this image is a staging/prod artifact only.
#
# NOTE: base image digests are pinned during S21 hardening (ADR-000 §8).

FROM node:20-slim AS builder
RUN corepack enable
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM caddy:2-alpine AS runtime
COPY --from=builder /app/dist /srv
# The internal file-server Caddyfile; the edge Caddyfile lives in ops/caddy/.
RUN printf ':80 {\n\troot * /srv\n\tencode gzip\n\ttry_files {path} /index.html\n\tfile_server\n}\n' \
    > /etc/caddy/Caddyfile
