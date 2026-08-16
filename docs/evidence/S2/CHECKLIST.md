# Sprint S2 — Definition of Done Checklist (honest state)

Legend: ✅ verified with recorded evidence · 🟡 implemented + partially evidenced · ⬜ not done

**Environment note (2026-08-16):** Docker is now available (29.6.2 / Compose v5.3.1) and the
dev stack has been running continuously. This unblocks migrations-on-live-PG and
testcontainers integration tests, both of which were ⬜ for all of S1. Staging is still
**not provisioned**, so every staging-scoped row below remains ⬜ — a dev soak is not a
staging soak and this file does not pretend otherwise.

| # | DoD item (Roadmap S2) | State | Evidence |
|---|---|---|---|
| 1 | Binance WS adapter (combined streams, heartbeat/resume) | 🟡 | `infrastructure/exchanges/binance/ws/adapter.py` implemented and live-proven for 2 symbols; unit coverage 49%; resume/heartbeat paths not yet chaos-tested. |
| 2 | Candle-close detection + native-vs-aggregated verification | 🟡 | `application/marketdata/live_ingest.py`; closed klines converted to domain candles and persisted. Cross-TF aggregation verification exists in `validation.py` but is not exercised by the live path (only M5 is streaming). |
| 3 | Freshness state machine (FRESH/STALE/SUSPECT/DEGRADED) | 🟡 | `application/marketdata/freshness.py` at 100% unit coverage; logs show `freshness: FRESH` + `detection_allowed: true`. **Only the FRESH state has been observed live** — the degraded states are unit-reachable but not yet demonstrated on a running feed. |
| 4 | 72-hour soak with zero unexplained gaps | 🟡 | `20260816-dev-soak.txt`: **~177 h continuous** (2026-08-09 01:25 → 2026-08-16 10:20), BTCUSDT + ETHUSDT M5, `actual == expected` (2124 each, delta 0), gap scan returns 0 rows, `data_incidents` = 0. **Scope limits: 2 symbols not top-50; M5 only, not all TFs; dev host, not staging.** Duration exceeds the DoD; breadth does not. |
| 5 | Gap detection → REST backfill → ordered replay | ⬜ | No gap has occurred in the soak, so the recovery path is **unproven in practice**. Chaos harness (kill WS mid-candle, inject out-of-order/duplicate frames) not built. |
| 6 | Transactional outbox (T39) + relay; `candle.closed` on Redis Streams | ⬜ | **Not implemented.** Zero `outbox` / `xadd` hits in `backend/src`. The architecture's ingest→engine event link does not exist yet. |
| 7 | T4 trade aggregates (1m buckets from aggTrade) | ⬜ | Not implemented; no migration creates it. Blocks SLS §2.14 move-sanity guard, which was deferred from S1 to S2 and is still open. |
| 8 | Staging backup regime live (WAL archiving + nightly base) | ⬜ | Staging not provisioned. `ops/terraform/staging/` authored in S0.3; deploy workflow deliberately disabled in commit `78327ad`. |
| 9 | Migration applies on live PG + hypertable verified | ✅ | Alembic head `007_ict_zone_interactions` on the live DB; `market.candles` is a hypertable with compression **enabled**. Closes S1 checklist items B1/B2. |
| 10 | Integration tests vs real PG | ✅ | `uv run pytest tests/integration -m integration --no-cov` → **5 passed** (testcontainers PG16+Timescale). Closes S1 checklist item 7 / B3. |
| 11 | Event ordering property tests | ⬜ | `tests/property/` is an empty `.gitkeep`; hypothesis is used only under `tests/unit/shared/`. |
| 12 | Runbook: feed incidents | ⬜ | Not written. `docs/runbooks/` has backfill, debugging, staging-provision, g0-s1-certification. |

## Known data defect

`market.symbols` contains **0 rows** while `market.candles` holds live data for BTCUSDT and
ETHUSDT. `symbol_sync` has never been run against this database, so the symbol registry and
the candle store disagree. Recorded here rather than silently fixed — it needs a decision on
whether the live ingest path should require a registered symbol.

## Non-golden verification fixtures in the dev DB

S5/S6 were verified by writing hand-made candle series **directly into the dev database**
under synthetic symbols: `S5TESTUSDT`, `S6OBUSDT`, `S6MITUSDT`, `S6SHIFTUSDT`, `S6BPRUSDT`,
`S6IFVGUSDT`, `S6INVUSDT`, `S6FILLUSDT`. These are not versioned, not labeled against SLS
sections, not replayable, and not run by CI — they are **not** golden datasets in the sense
Constitution §32.3–§32.4 requires. They are useful raw material for building real ones.

## Verdict

S2 is **not** closeable as-is. Rows 5, 6, 7, 11 and 12 are genuine unbuilt scope, and row 6
(outbox + Redis Streams) is architecturally load-bearing — without it the engine has no
event path from ingest. Rows 9 and 10 are newly ✅ and retire long-standing S1 debt.
