# Runbook — Incident Debugging Workflow (S0.3 §10)

The standing diagnostic path. Every investigation follows it so the
observability triad grows from real incidents, not speculation.

## The path
1. **Symptom → Grafana.** Which golden signal moved (up, readiness, latency)?
   Which release marker is adjacent (release folder annotation)? Note the window.
2. **Grafana → Loki.** LogQL on the service + window:
   `{service="api", env="staging"}`. Find a suspicious line; json-filter on its
   `correlation_id` (or `flow_id` from S2): `| json | correlation_id="..."`.
3. **Loki → Sentry.** Open the exception with the same `correlation_id`; read the
   breadcrumbs and stack. `environment` + `release` tags scope it.
4. **→ Runbook or fix.** Apply the matching runbook, or fix in code and redeploy
   (never hand-patch prod — Constitution §33.2).

## Rules
- Every investigation appends one line to the ops diary (Roadmap §12).
- A session that needed a **missing** log or metric files an `observability-gap`
  issue before it closes — the triad grows from real gaps.
- Correlation ids live in the log body / Sentry tags, never as Loki labels
  (cardinality). See [observability-conventions.md](../observability-conventions.md).
