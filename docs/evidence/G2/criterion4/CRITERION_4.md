# G2 criterion 4: per-context detection ≤ 2 s

**Status: MET under R1 at pooled p95 (2026-10-27, re-measured 2026-11-07 on two engine shards), SLS v1.0.18 §14 row 2 and Roadmap v2.2.1. Open: (1) R1 pooled or per-context has not been ruled; per context, 4 of 75 contexts are over 2 s at p95; (2) Constitution §20.2 has not been ruled against R1. Shard 0's p99 is 2.048 s. SLS §14 row 1, read per boundary type (owner, 2026-11-16), is NOT MET on H4 boundaries (p95 31.89 s) and D1 boundaries (p95 41.07 s).**

## The ruling
- G2 named "close→detection p95 ≤ 2 s full-universe".
- The owner was shown two readings, with the measurements in `SPEED_PLAN_G2_C4.md`:
  - **R1:** one symbol-TF's detection work.
  - **R2:** close → every context's pass done.
- On 2026-10-27 the owner ruled **R1**.
- SLS v1.0.18 rewords §14 row 2 to say what it measures, and Roadmap v2.2.1 points G2 at that row.
- Under R2 the criterion is not reachable on any shared-core system: the stages before the engine take ~0.9 s at p50, so every context would need its own core at the moment of its close.

## The measurement
The v38 soak ran 2026-10-18T20:20:39Z → 10-25T20:20:39Z, release `p1b-531cbfc`, on all 15 scanned symbols × 5 timeframes.

**Per-context detection work.** This is `elapsed_seconds` on every `detection_pass_completed` line, read from the engine's own log:

| passes | p50 | p95 | max |
|---|---|---|---|
| 43,575 (every close of the soak; 15 × 2,905) | 0.65 s | **0.86 s** | 1.93 s |

`latency_after_deploy_20261025T2029Z.txt` gives the same stage over its 6,000 matched passes: p50 0.654 s, p95 0.867 s, p99 1.097 s, max 1.865 s.

On the one-engine v38 soak: **not met, and not this criterion: SLS §14 row 1**, the full-universe cycle at ≤ 30 s p95 and ≤ 60 s p99. `boundary_after_deploy_20261025T2029Z.txt` puts it at **30.69 s p95**, 42.03 s p99 across 2,017 boundaries. Most of that is queueing on one engine, at 22.8 s p95 from stream to start. Speed step 1, two engine shards, is built to close that gap. It is a binding SLS maximum, tracked separately.

## Re-measured on two engine shards

Speed step 1 (#343) moved the same code to two engine processes on the same 2-OCPU VM.
Window: exactly 7 days, 2026-10-28T04:20:39Z → 2026-11-04T04:20:39Z, release `p1b-2173be3`,
15 symbols × 5 timeframes.

Passes: 43,575 (M5 30,240 · M15 10,080 · H1 2,520 · H4 630 · D1 105), which equals 15 × 2,905.
Duplicates on (symbol, timeframe, close): 0.

**Per-context detection work** (`elapsed_seconds` on every `detection_pass_completed` line):

| | passes | p50 | p95 | p99 | max | over 2 s |
|---|---|---|---|---|---|---|
| **both engines** | 43,575 | 0.782 s | **1.492 s** | 1.881 s | 4.111 s | 231 (0.53%) |
| engine (shard 0, 6 symbols) | 17,430 | 0.856 s | 1.687 s | 2.048 s | 4.111 s | 211 (1.21%) |
| engine-shard1 (shard 1, 9 symbols) | 26,145 | 0.735 s | 1.308 s | 1.625 s | 2.739 s | 20 (0.08%) |

The criterion is p95 ≤ 2 s and still holds. Against the one-engine soak, p95 rose from 0.86 s to
1.49 s, so the margin fell from 1.14 s to 0.51 s.

**Four things this status does not hide:**
- **`engine` (shard 0) has a p99 of 2.048 s**, over 2 s. The criterion is written at p95, so it does not fail on this.
- **231 of 43,575 passes (0.53%) took more than 2 s**: 211 on shard 0 and 20 on shard 1.
- **Constitution §20.2 is open.** Whether R1 is compatible with "tightened, never loosened" has not been ruled. This status holds under R1 only.
- **R1 pooled or per-context is not ruled.** SLS §14 row 2 says "for one symbol-TF". So far it has been read **pooled**: one p95 over all passes. Read per context, each of the 75 contexts would need its own p95 ≤ 2 s, and 4 do not. **Status under that reading: NOT MET. This decision is pending with Abii.**

**Per-context worst, 7 days:**

| context | passes | p95 | p99 | over 2 s |
|---|---|---|---|---|
| BNBUSDT D1 | 7 | 2.955 s | 2.955 s | 2 |
| ADAUSDT D1 | 7 | 2.739 s | 2.739 s | 2 |
| BNBUSDT H4 | 42 | 2.654 s | 3.080 s | 15 |
| BNBUSDT H1 | 168 | 2.148 s | 2.345 s | 12 |
| BTCUSDT M5 | 2,016 | 1.945 s | 2.227 s | 79 |
| BNBUSDT M15 | 672 | 1.898 s | 2.266 s | 24 |

- Contexts with p95 over 2 s: 4 of 75. With p99 over 2 s: 10 of 75.
- D1's p95 rests on 7 passes, so it is close to the maximum.
- By symbol, over 2 s: BNBUSDT 95, BTCUSDT 89, ETHUSDT 24, ADAUSDT 12.

**Why the work rose: the same pass is slower while the other engine is running.** Same engine,
M5 only, split by how much of the pass overlapped a pass on the other engine:

| | overlap | passes | p50 | p95 |
|---|---|---|---|---|
| engine-shard1 | under 10% | 5,458 | 0.621 s | 0.749 s |
| engine-shard1 | 90% or more | 11,288 | 0.858 s | 1.387 s |
| engine | under 10% | 78 | 0.730 s | 0.974 s |
| engine | 90% or more | 11,949 | 0.863 s | 1.705 s |

**SLS §14 row 1** (full-universe cycle, ≤ 30 s p95, ≤ 60 s p99), same window, 2,016 complete boundaries:

| passes at the boundary | boundaries | p95 | p99 | max |
|---|---|---|---|---|
| 15 (M5) | 1,344 | 9.42 s | 9.84 s | 14.75 s |
| 30 (M5+M15) | 504 | 16.73 s | 17.24 s | 18.62 s |
| 45 (+H1) | 126 | 24.36 s | 25.49 s | 25.66 s |
| 60 (+H4) | 35 | 31.89 s | 32.97 s | 32.97 s |
| 75 (+D1) | 7 | 41.07 s | 41.07 s | 41.07 s |
| **all** | **2,016** | **21.74 s** | **30.85 s** | 41.07 s |

**Row 1 is read per boundary type** (owner ruling 2026-11-16: "all due TFs at any close boundary"
means every boundary type meets ≤ 30 s p95 and ≤ 60 s p99 on its own). On this window:

- **NOT MET on H4 boundaries** (60 passes): p95 31.89 s, over 30 s by 1.89 s, on 35 boundaries.
- **NOT MET on D1 boundaries** (75 passes): p95 41.07 s, over 30 s by 11.07 s. That is only 7 boundaries, so the p95 is in effect the maximum.
- Met on M5, M15 and H1 boundaries (p95 9.42 s, 16.73 s, 24.36 s). p99 is under 60 s on every type.
- Pooled over all 2,016 boundaries the p95 would be 21.74 s. That reading is **not** the ruled one, and it is shown only so the number can be recognised.

Speed work to close this (rebalancing the shards, a third shard, a larger VM, or a cheaper pass) is
deferred by the owner until the quality gate has ruled on signal quality. Row 1 stays NOT MET on H4
and D1 until then.


## Files
- `latency_after_deploy_20261025T2029Z.txt`: close → stored → stream → start → pass, per stage.
- `boundary_after_deploy_20261025T2029Z.txt`: close → last pass of each boundary (row 1).
- `SPEED_PLAN_G2_C4.md`: the two readings, the CPU measurement, and the shard plan.
- `passes_post_shards_7d.txt`: the raw output behind the two-shard tables.
- `passes7d.py`: the read-only script that produced it. It reads `docker logs`, so it reproduces the counts only while the logs still hold the window.

## Reproduce
Read-only, on the VM, from the checkout:
```
python3 - < ops/soak/latency_baseline.py
python3 - < ops/soak/boundary_last_pass.py
python3 - < docs/evidence/G2/criterion4/passes7d.py
```
