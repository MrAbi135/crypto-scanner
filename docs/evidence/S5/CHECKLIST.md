# Sprint S5 — Definition of Done Checklist (honest state)

Legend: ✅ verified with recorded evidence · 🟡 implemented + partially evidenced · ⬜ not done

**Written retroactively on 2026-08-16**, while building golden datasets for the
liquidity engine. S5 shipped without a checklist; this reconstructs its true
state rather than leaving "sprint complete" as the only record. Nothing here is
a criticism of the code that exists — the sweep engine is solid and now has
golden coverage. The gaps are gaps.

## Roadmap S5 backend work

| # | Item | State | Evidence |
|---|---|---|---|
| 1 | Pool detection + strength scoring (component breakdown) | ✅ | `domain/liquidity/pools.py`; SLS §4.2's four-component formula implemented exactly; unit-covered incl. every guard and cluster tier; wired via `LiquidityReplayService._persist_swing_pool`. |
| 2 | **EQH/EQL clustering (ATR-banded)** | ⬜ | `domain/liquidity/clusters.py` **is implemented and unit-tested but has no caller.** A repo-wide grep for `detect_equal_level_clusters` outside `domain/liquidity/` returns nothing. The engine never produces a cluster. |
| 3 | Classification (internal/external) | 🟡 | `LiquidityClass` is assigned, but statically: internal swings become INTERNAL pools and external swings become EXTERNAL pools in `_persist_swing_pool`. SLS §4.4's positional reading (relative to the dealing range) is not implemented. |
| 4 | Sweep detection (penetration + rejection close, 2-candle window) | ✅ | `domain/liquidity/sweeps.py`; golden cases `s5-bsl-sweep-single-candle`, `s5-bsl-close-through-is-a-break`, `s5-bsl-two-candle-sweep` pin the disambiguation the roadmap calls the doctrine's hardest call. |
| 5 | 15-candle sweep expiry + `reclaimed` flag | 🟡 | `setup_expiry_index` and `sweep_reclaimed` implemented and unit-covered on both sides; no golden case yet, and no consumer reads the flag because nothing downstream of the sweep exists at S5. |
| 6 | **Stop-hunt composite** | ⬜ | `domain/liquidity/stop_hunts.py` **is implemented and unit-tested but has no caller.** Same grep result as clustering. The engine never produces a stop hunt. |
| 7 | Pool state machines | ✅ | ACTIVE → SWEPT / BROKEN / EXPIRED in `liquidity_replay`; terminal permanence proven against real Postgres in `tests/integration/test_detection_persistence_pg.py`. |

## Database work

| # | Item | State | Evidence |
|---|---|---|---|
| 8 | T14 pools + T15 transitions | ✅ | Migration `005_liquidity_detection`; applied to a live TimescaleDB and asserted by the integration suite. |
| 9 | Resting-liquidity Redis snapshot + cache-registry entry | 🟡 | `RedisLiquidityStateStore` exists and is written on every run; **not recorded in `docs/cache-registry.md`**, which Roadmap §12 makes a DoD item wherever Redis is touched. |
| 10 | Table for clusters / stop hunts | ⬜ | None exists. Consistent with items 2 and 6 — there is nowhere to persist output the engine does not produce. |

## Testing

| # | Item | State | Evidence |
|---|---|---|---|
| 11 | Golden dataset ≥ 50 liquidity cases | ⬜ | **3 of 50.** The harness did not exist until 2026-08-16; the three that exist cover sweep vs break. |
| 12 | Strength-component attribution tests | ✅ | `test_sweeps_stop_hunts_pools.py` asserts each component and every cluster tier. |
| 13 | State-resurrection prohibition tests | ✅ | Proven at the SQL level: a terminal pool swallows a later upsert, and transitions are one-way (integration suite). |

## DoD

| # | Item | State | Note |
|---|---|---|---|
| 14 | Golden 100% | 🟡 | 100% of the cases that exist pass, but 3 cases is not the ≥ 50 the roadmap asks for. "100% green" here means the gate is honest, not that coverage is adequate. |
| 15 | Every pool/sweep carries full evidence | ✅ | Both carry an evidence JSON; pool evidence includes the strength component breakdown, sweep evidence the §15.2 slice. |
| 16 | Terminal states proven permanent | ✅ | Integration suite, per item 13. |

## The finding that prompted this file

Two detectors named explicitly in the Roadmap's S5 backend list — **EQH/EQL
clustering** and the **stop-hunt composite** — exist as pure domain functions
with unit tests, are exported from `domain/liquidity/__init__.py`, and are
called by nothing. They are unreachable from the running engine.

One measurable consequence: `pool_from_swing` is the only pool constructor in
use and always passes `member_count=1`, so SLS §4.2's `cluster_factor` is
permanently `0.25`. **A quarter of the pool-strength score — 25 of 100 points —
can never vary in production**, always contributing exactly 6.25. Any downstream
ranking that leans on pool strength is working with a narrower range than the
spec designed.

This is unbuilt scope, not a code defect: nothing is wrong with the functions
themselves. It wants a decision about when to wire them, and until then S5
cannot honestly be called complete against §4.3 and §4.7.
