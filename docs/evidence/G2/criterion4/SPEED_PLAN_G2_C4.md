# Speed plan: G2 criterion 4 (2026-10-27)

Measured read-only on the VM on 2026-10-27. Nothing was changed.

## Where we are
- **VM:** Oracle `VM.Standard.A1.Flex`, **2 OCPU** (Neoverse-N1, aarch64), 12 GB RAM, no CPU limits on any container.
- **One engine process.** During a 30-pass close burst (03:15:00–03:15:18Z), sampled every 2 s, the engine ran at **82–139%** of one core and Postgres at **16–23%**. Total use was about 1.05 of 2 cores, so **the second core sits about half idle** while passes queue behind each other.
- **Pass work:** p50 0.65 s, p95 0.86 s.
- **Before the engine starts:** close → stored p50 0.40 s, then stored → stream p50 0.50 s. The rest of the latency is queueing.
- **Latency over the v38 soak (2,017 boundaries):**

| boundary | p95, close → last pass done |
|---|---|
| 15 passes (M5) | 11.7 s |
| 30 (M5+M15) | 22.1 s |
| 45 (+H1) | 32.5 s |
| 60 (+H4) | 43.2 s |
| 75 (+D1) | 56.6 s |
| **all** | **30.7 s** (p99 42.0 s) |

## What the targets say
| | target | now |
|---|---|---|
| SLS §14 row 1: full-universe scan cycle | ≤ 30 s p95, ≤ 60 s p99 | 30.7 s / 42.0 s |
| SLS §14 row 2: close → all detectors evaluated, per symbol-TF | ≤ 2 s | pass work 0.86 s p95; close → pass done 25.3 s p95 |
| Roadmap G2 #4 | "close→detection p95 ≤ 2 s full-universe" | depends on the reading |

**Two readings of "≤ 2 s", and they lead to very different projects. This is the owner's ruling to make.**

**(R1) Per-context detection work.** One symbol-TF's detectors take ≤ 2 s once they run. This is **already met** (0.86 s p95). Row 1 (≤ 30 s) then carries the end-to-end promise, and the two rows read together make sense: the SLS's own capacity line (~400 symbols × 5 TFs) puts up to 2,000 passes on one boundary, and no design finishes each of them 2 s after the close.

**(R2) End-to-end, close → that context's pass done, for every context.**
- **Not reachable here at any reasonable cost.** The stages before the engine already take about 0.9 s at p50, so every context would need its own core the moment its close lands: 15 cores on an M5 boundary, 75 on a daily one.
- At the SLS's 400-symbol capacity it would take hundreds of cores.

## The plan

### Step 0: owner ruling (no code)
Rule R1 or R2.
- **R1:** amend Roadmap §9's G2 row (and the S9 DoD) to name §14 row 2 as per-context detection work, with row 1 as the end-to-end target. **Criterion 4 is then met on today's numbers**, and steps 1–2 make row 1 comfortable.
- **R2:** criterion 4 cannot pass on this infrastructure. G2 would need the criterion re-ruled or a much larger system.

### Step 1: two engine shards on the current VM (no cost)
#320 already built this: engine processes split the symbols by SHA-256 hash, one consumer group each, one symbol's timeframes always in one process. It is mutation-tested (`speed_step2_shards.json`) and switched off.
- **Split:** 6 / 9 symbols, so a burst finishes when the 9-symbol shard does: **about 1.67× faster**.
- **Estimate** (not measured): all-boundary p95 about **20 s**, 45-pass about 20 s, 75-pass about 34 s. Two engines plus Postgres need about 2.05 cores of 2, so there is slight contention and the gain may land nearer 1.5×.
- **Work:**
  1. Compose: a second engine service with `SCANNER_ENGINE_SHARD_COUNT=2` and an index each.
  2. The deploy script currently *refuses* shards (`post_soak_deploy.sh` lines 172 and 344), so it needs a shard-aware version.
  3. The soak checks, latency scripts and rollback read `scanner-dev-engine-1` only, so they must read both engines.
  4. A dry-run test of the new deploy and rollback.
- **Cost:** one deploy, which means a **new soak T0**. Criterion 5's soak is already closed, so nothing waits on the current T0.
- **Proof:** measure 24 h and 7 d with the same latency and boundary scripts against today's baseline, and require 0 failed passes and clean invariants.

### Step 2 (only if step 1 is not enough): 4 OCPU
- **The resize is done in the Oracle console, by the owner.** An A1.Flex can be resized, and Oracle's Always Free allowance has been 4 OCPU / 24 GB of A1 in total. **Confirm the current limit and price in the console before resizing.** A resize reboots the VM, which means another new T0.
- **With 3 shards:** split 4/7/4, about **2.1×**, all-boundary p95 about **15 s**.
- **With 4 shards:** split 3/6/3/3, about **2.5×**, CPU-bound again (4 × 0.85 + Postgres ≈ 4.1 cores).

### Step 3 (later, optional): cheaper passes
The pass is about 0.65 s, and earlier profiling found it DB-round-trip-heavy. Halving it would halve every number above without new cores. This is engine work, with golden and replay re-verification, so it is a separate project.

## Recommendation
1. Rule **R1**. It is the reading that fits row 1 and the SLS's capacity line, and it is already met.
2. Approve **step 1** (two shards). It is free, the code exists, and it should take row 1 from 30.7 s to about 20 s.
3. Do step 2 only if step 1's measured p95 is not comfortably under 30 s.
