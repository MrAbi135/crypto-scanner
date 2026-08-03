# ADR-001 — Development Stack Conventions

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Project owner (lead engineer)
- **Governance reference:** Roadmap S0; TAD §14, §16, §17, §22, §23; TDR §8–§9, §21–§25; Constitution §18–§19, §33; Sprint S0.2 Implementation Guide

## Context

Sprint S0.2 builds the development infrastructure: config/logging/error
foundations, the four process entrypoints with health plumbing, the Docker
compose stack, observability provisioning, CI, and deploy workflows. Because
this repository already contained Sprint S1 backend code (the guides assume S0.2
precedes S1), several reconciliations were required to add S0.2 infrastructure
without regressing S1. This ADR records the dev-stack conventions and those
reconciliations.

## Decision

### 1. Single-container PostgreSQL + TimescaleDB
Dev and staging run one `timescale/timescaledb:2.15.2-pg16` container (TDR §8).
`ops/db/initdb/00-extensions.sql` enables the extension once on volume creation;
migrations own all schema from S1.

### 2. Host-run frontend in dev; containerized only for staging/prod
The frontend runs on the host (`pnpm dev`) for hot-reload speed; its Docker
image (`frontend.Dockerfile`, Caddy static serve) is a staging/prod artifact.

### 3. Process/health-port scheme
`api` binds `SCANNER_API_PORT` (8000) and serves `/internal/*` on a FastAPI app;
`ingest`/`engine`/`worker` each serve the same three internal routes on
`SCANNER_HEALTH_PORT` (8001/8002/8003) via a shared Starlette health app. All
four are served by uvicorn, which owns graceful SIGTERM/SIGINT shutdown — the
"idle main loop" is uvicorn's serve loop. Readiness probes PG (`SELECT 1`) and
Redis (`PING`), each timeboxed 500ms, returning 503 with per-dependency detail.

### 4. Edge-blocked internals
The Caddy edge (`ops/caddy/Caddyfile`) responds 403 to `/internal/*` — the
API-Spec §18.18 "never publicly routable" guarantee is enforced, not hoped for.

### 5. Config layer (evolved from the S1 placeholder)
`scanner.config` exposes `get_settings(process)` (typed via overloads) over
`BaseProcessSettings` + per-process classes. The S1 `IngestSettings` /
`load_ingest_settings` public API is preserved (the ops CLI still imports them);
`settings.py` became a backward-compat shim.

### 6. `extra="ignore"` retained on settings
Kept (not the guide's "forbid") because all processes share one env file, so
per-process "forbid" would reject sibling processes' variables. This is the S1
deviation #1 and remains an **ADR-003** item.

### 7. Dependency & tooling additions (justified)
- `redis>=5.0` — real Redis PING in the readiness probe (TDR §9).
- `pydantic.mypy` mypy plugin — lets pydantic-settings construct-from-env
  typecheck under `--strict` (cleared the pre-existing `settings.py` errors).
- `include_external_packages = true` under `[tool.importlinter]` — required so
  the Domain-purity forbidden-contract can resolve external modules.
The existing `pyproject.toml` (a superset carrying S1's `httpx/sqlalchemy/
asyncpg/alembic/testcontainers`) was **kept, not overwritten** with the guide's
S1-less version, to preserve backward compatibility.

## Consequences

- The four-process topology is real and health-checked; organs arrive by sprint.
- `redis_url` and `db_dsn` are now required base config for every process
  (including the ingest settings the S1 CLI inherits); `ops/env/.env.example`
  and `dev.env` provide them.
- **Pre-existing S1 code debt is unchanged by this sprint and is scoped to S1
  closure** (not S0.2): ruff (75 lint / 18 format), mypy (7 strict errors in
  backfill/repositories/alembic-env/cli-main), and the import-linter *Layered*
  contract (the CLI imports infrastructure directly — the `runtime/wiring`
  composition-root pattern this sprint introduces is the eventual fix). All
  **new** S0.2 code is ruff + mypy + import-linter clean; the *Domain purity* and
  *Engine acyclicity* contracts pass. CI is authored to enforce all gates and
  will go green once S1's DoD item 5 is closed.

## Alternatives considered

- **Overwrite `pyproject.toml` with the guide's verbatim content:** rejected —
  it would delete S1's runtime dependencies (backward-compat break).
- **Hand-rolled asyncio HTTP health listener** (guide's literal "aiohttp-free
  listener"): rejected in favour of uvicorn+Starlette (TDR-approved, avoids
  hand-parsing HTTP) — still aiohttp-free.
- **Refactor the S1 CLI now** to fix the Layered contract: deferred — it would
  change the documented `python -m scanner.interfaces.cli.main` entrypoint
  (breaking the S1 runbook/`verify-s1.sh`) and needs CLI behaviour tests that
  belong to S1 verification.
