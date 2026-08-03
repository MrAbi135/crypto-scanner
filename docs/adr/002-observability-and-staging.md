# ADR-002 — Observability Conventions & Staging Topology

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Project owner (lead engineer)
- **Governance reference:** TAD §15, §17, §22, §25; TDR §18, §20, §24; Roadmap S0; Sprint S0.3 Implementation Guide

## Context

Sprint S0.3 completes Roadmap Sprint S0: the shared packages (backend + frontend),
the staging environment as code, and the observability triad (Sentry +
Prometheus/Grafana + Loki). This ADR records the conventions and topology
decisions, and the coverage policy adopted while closing the Gate-G0 green-CI
requirement over the pre-existing Sprint S1 code.

## Decision

### 1. Observability naming law
Logs, metrics, dashboards, and alerts follow one documented law
([observability-conventions.md](../observability-conventions.md)): dot-namespaced
log events `area.subject.verb`; metrics `scanner_<area>_<name>_<unit>` (factory-
enforced) with bounded-cardinality labels (`symbol` is never a high-frequency
label); provisioned-only Grafana dashboards in four folders (ops/doctrine/
business/release); alerts carry severity + summary. The correlation contract
joins Sentry ↔ Loki ↔ metrics by `correlation_id`/`flow_id`, kept in the log
body, never as labels.

### 2. Sentry role separation
Sentry owns exceptions; Prometheus owns latency (traces sampling off). Sentry is
initialized in `runtime/wiring/bootstrap.py` ONLY (composition-root law),
DSN-gated (disabled without `SCANNER_SENTRY_DSN`), tagged `environment=SCANNER_ENV`
and `release=SCANNER_RELEASE`, with a `before_send` scrubber sharing the logging
redaction list (one redaction truth). Frontend `@sentry/react` mirrors this,
DSN-gated in `main.tsx`, errors only, no session replay.

### 3. Staging topology
One dedicated-vCPU Hetzner node (`ops/terraform/staging/`), private network,
firewall allowing only 80/443 + admin-IP SSH (no DB/Redis/process ports),
docker+compose via cloud-init, hardened SSH, unattended upgrades. TLS via Caddy
ACME on the real domain (local runs use Caddy's internal CA). `/internal/*` is
edge-blocked (403). Terraform state is local + encrypted (remote state is an S21
trigger). Domains are config (`SCANNER_PUBLIC_HOST`), not code.

### 4. Shared package completion
`scanner/shared` is completed per S0.3 §2 (adds `types.py`, `events.py`,
`guards.ensure_range`, `ids.monotonic_factory`/`as_ulid`) at 100% branch
coverage with hypothesis property suites; `float` never appears in a signature
(no-float guard). Frontend `src/shared/{config,lib}` mirrors backend semantics
without shape-coupling (types flow only via OpenAPI generation).

### 5. Coverage policy
Two enforced gates: `scanner/shared` at **100%** (scoped CI step) and the repo
at **≥85%**. The unit floor **omits** modules that require live services or are
non-logic composition/entrypoints — the asyncpg COPY repository + engine factory,
the Alembic env, the four process entrypoints, the CLI composition root, and the
settings shim — because they are exercised by the integration suite
(`tests/integration/`) or by running the process, not by unit tests. The omit
list lives in `pyproject.toml` `[tool.coverage.run]`. This keeps the floor
meaningful for domain/application/shared logic without pretending server loops
are unit-testable.

## Consequences

- The observability triad is convention-compliant from an empty platform; future
  metrics/events/dashboards inherit the naming law and release markers for free.
- Staging is reproducible from code and self-heals on reboot.
- **Sprint S1 code debt raised for Gate G0 was resolved this sprint** (it blocked
  green CI): mypy is clean (type-only fixes), ruff/format clean, and the CLI was
  refactored to a `runtime/cli.py` composition root so the import-linter *Layered*
  contract passes — the documented CLI entrypoint changed to
  `python -m scanner.runtime.cli` (runbook + `verify-s1.sh` updated).

## Alternatives considered
- **Convert the three `(str, Enum)` domain enums to `StrEnum`** (ruff UP042):
  rejected — it changes `str()` semantics and risks S1 serialization; suppressed
  via config instead.
- **Add unit tests mocking asyncpg to cover the COPY repository:** rejected —
  heavy mocking of the driver would test the mock, not the COPY path, which the
  integration suite already covers; omit-from-unit-floor is more honest.
