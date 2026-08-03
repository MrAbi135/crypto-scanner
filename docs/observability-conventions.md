# Observability Conventions (S0.3 §8.5)

The single naming law for logs, metrics, dashboards, and alerts. Every PR that
adds an event key or a metric updates this doc in the same PR (cache-registry
discipline applied to observability).

## Log events (structlog, TAD §17)
- **Event key** is dot-namespaced `area.subject.verb` — e.g. `ingest.candle.closed`,
  `backfill.gap.recorded`, `auth.session.revoked`. The key is the `event` field.
- Every line carries: `timestamp` (UTC ISO), `level`, `service` (=process),
  `release`, plus bound context. `correlation_id` binds at edges; `flow_id`
  (candle-close trace) binds from S2.
- **Secrets are redacted** by the logging processor (keys containing
  `password/token/secret/dsn/api_key/authorization`). The same redaction is the
  Sentry `before_send` scrubber (one redaction truth).
- Domain code never logs (import-linter domain-purity forbids structlog).

## Metrics (Prometheus, TAD §25)
- **Name:** `scanner_<area>_<name>_<unit>` — e.g. `scanner_ingest_gap_recovery_seconds`,
  `scanner_engine_pipeline_stage_seconds`. Lowercase snake; the `metrics.py`
  factories enforce the `scanner_` prefix + snake_case.
- **Latency** uses the one governed bucket set (5ms → 10s, `LATENCY_BUCKETS`).
- **Labels** are lowercase snake with **bounded cardinality** — label values must
  be closed sets. `tier` is a label; **`symbol` is never a label** on
  high-frequency metrics (unbounded cardinality).
- `scanner_process_info{process,version}` carries per-process metadata.

## Dashboards (Grafana)
- Provisioned JSON in git only — never hand-edited in the UI (Constitution §33.2).
- Four folders: `ops/` (platform health), `doctrine/` (detectors, from S4),
  `business/` (funnel, from beta), `release/` (deploy annotations from
  `scanner_process_info`).

## Alerts (Prometheus rules)
- Live in `ops/prometheus/alerts/*.yml`. Each alert carries `severity` and an
  annotation `summary`/`description`. Seed rules: process `up == 0` (2m),
  readiness failing, restart-looping.

## The correlation contract
A Sentry issue, its Loki log trail, and its metrics are joined by
`correlation_id` (and `flow_id` for candle-close traces). High-cardinality ids
stay in the **log body / event tags**, never as Loki labels or metric labels —
they are queried via LogQL json filters.

## The debugging path
symptom → **Grafana** (which golden signal moved; adjacent release marker) →
**Loki** (LogQL on service+window, json-filter on `correlation_id`) →
**Sentry** (exception + breadcrumbs, same `correlation_id`) → runbook/fix.
See [runbooks/debugging.md](runbooks/debugging.md).
