# Sprint S1 — Definition of Done Checklist (honest state)

Legend: ✅ verified with recorded evidence · 🟡 implemented + unit-executed, live-infra execution pending · ⬜ not done (infra-blocked)

**Environment note (2026-08-03):** the mandated toolchain is now installed (uv 0.12.1 / Python 3.12.13), so all lint/type/architecture/unit checks now **execute and pass** — evidence in `20260803-unit-verification.txt`. The **Docker daemon is unavailable** in this environment, so migration-on-live-PG, testcontainers integration, and staging remain blocked (see §"Infrastructure-blocked" below).

| # | DoD item (Roadmap S1) | State | Evidence |
|---|---|---|---|
| 1 | SLS §2.15 battery implemented | ✅ | `candle.py` (intrinsic OHLC/volume/alignment) + `validation.py` (ordering, duplicate, continuity incl. chunk-seam, cross-TF aggregation). Move-sanity (§2.14, needs aggTrade) is correctly S2 scope, not S1. |
| 2 | SLS battery tested (executed) | ✅ | Unit suites **execute + pass** (`test_validation`/`test_candle`/`test_continuity`) — recorded in evidence file. The battery is pure functions; unit-level is its complete test. |
| 3 | Backfill idempotent (re-run = no-op) | 🟡 | Logic desk-verified (resume-from-tail + COPY conflict-skip, two layers); unit `test_rerun_is_a_noop` **executes + passes**. The live re-run-on-real-PG proof is integration (item 7). |
| 4 | Incidents recorded for injected gaps | ✅ | Unit + continuity cases **execute + pass** (gap→incident, `started_at`=first missing boundary; `verify-continuity` covered-vs-uncovered). |
| 5 | Lint / mypy --strict / import-linter clean | ✅ | **Now clean + recorded** (was 🟡): ruff + ruff-format clean, mypy 0 errors (81 files), import-linter 3/3 contracts KEPT. Resolved in Sprint S0.3 (ADR-002). |
| 6 | Migration applies + hypertable verified | ⬜ | Migration `001_market_schema` written per DDD §13/§14 (T1/T3 hypertable + compression, T8); `docker compose config` validates the stack. **Blocked:** applying it + asserting the hypertable needs a live PG/Timescale (Docker daemon down). |
| 7 | Integration tests vs real PG (COPY path, CHECK tripwires) | ⬜ | `tests/integration/test_persistence_pg.py` written (6 cases) + ruff/mypy-clean. **Blocked:** testcontainers needs the Docker daemon. |
| 8 | Staging: sync-symbols + 2y BTCUSDT backfill + verify-continuity clean | ⬜ | Requires the staging host + network (S0.3 IaC authored; not provisioned). |
| 9 | Runbook: backfill ops | ✅ | `docs/runbooks/backfill.md` (CLI updated to `python -m scanner.runtime.cli` after the S0.3 composition-root refactor). |
| 10 | No S2+ scope leaked (no WS, no events, no API endpoints) | ✅ | Tree audit: no stream/outbox/api-router code exists; the four process entrypoints serve only `/internal/*`. |

## Coverage
- Repo unit coverage **96.96%** (≥85 gate); `scanner/shared` **100%** (scoped). The COPY repository + engine factory + entrypoints are omitted from the *unit* floor (integration-covered) per ADR-002 §5.

## Notes / deviations (disposition)
1. **`extra="ignore"` in Settings** — retained (shared multi-process env file); ADR-003 item (still open).
2. **Rate-budget lock held across sleep** in `acquire` — correct for S1's sequential backfill; revisit before S2's concurrent stream+backfill.
3. **Section-number note:** the roadmap/this file cite "SLS §2.15" for the battery; the SLS itself numbers the 5-check battery **§2.13** and gap-handling **§2.15**. Code matches the spec's *content*; the citation is inherited from the roadmap.

## Infrastructure-blocked (record for Gate G0 / S1 certification)
Run on a Docker + PG + staging-capable host; each flips ⬜/🟡 → ✅ only with recorded evidence (no exceptions):
- **B1** `scripts/with-env.sh uv run alembic upgrade head` (×2) → baseline+market apply; re-run = no-op → **item 6**.
- **B2** hypertable assertion: `SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name='candles'` = 1; compression policy present → **item 6**.
- **B3** `uv run pytest tests/integration -m integration` (testcontainers PG16+Timescale) → COPY path + CHECK tripwires → **item 7**.
- **B4** staging `sync-symbols` → 2y BTCUSDT H1 backfill → `verify-continuity` clean; compression ≥10× → **item 8**.
- **B5** `scripts/verify-s1.sh` end-to-end records B1–B4 to this directory.
