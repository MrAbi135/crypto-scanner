# G2 criterion 4: per-context detection ≤ 2 s

**Status: MET (2026-10-27), under SLS v1.0.18 §14 row 2 and Roadmap v2.2.1.**

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

**Not met, and not this criterion: SLS §14 row 1**, the full-universe cycle at ≤ 30 s p95 and ≤ 60 s p99. `boundary_after_deploy_20261025T2029Z.txt` puts it at **30.69 s p95**, 42.03 s p99 across 2,017 boundaries. Most of that is queueing on one engine, at 22.8 s p95 from stream to start. Speed step 1, two engine shards, is built to close that gap. It is a binding SLS maximum, tracked separately.

## Files
- `latency_after_deploy_20261025T2029Z.txt`: close → stored → stream → start → pass, per stage.
- `boundary_after_deploy_20261025T2029Z.txt`: close → last pass of each boundary (row 1).
- `SPEED_PLAN_G2_C4.md`: the two readings, the CPU measurement, and the shard plan.

## Reproduce
Read-only, on the VM, from the checkout:
```
python3 - < ops/soak/latency_baseline.py
python3 - < ops/soak/boundary_last_pass.py
```
