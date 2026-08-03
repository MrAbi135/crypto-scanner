# syntax=docker/dockerfile:1
# Multi-stage backend image (TAD §23; S0.2 §6). The SAME image runs in staging
# and prod — parity from day one (Constitution §33.1). The process to run is
# chosen by the compose `command` (scanner.runtime.<process>).
#
# NOTE: base images use tags here; digests are pinned during S21 hardening
# (ADR-000 §8 pinning law) once a release digest is chosen.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Layer 1: dependencies only (cached across code changes).
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
# Layer 2: the project itself.
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd -r scanner && useradd -r -g scanner scanner
WORKDIR /app
COPY --from=builder --chown=scanner:scanner /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER scanner
# No default CMD: compose sets `command: python -m scanner.runtime.<process>`.
