# Runbook — Candle Backfill Operations (Sprint S1)

## Purpose
Fill or repair the historical candle record for one series. Backfill is
idempotent: re-running a completed range is a no-op (resume from the
persisted tail + conflict-skip insert).

## Commands (run via `scripts/with-env.sh`, from `backend/`)

```bash
# 1. Registry first — a symbol must exist before its candles
python -m scanner.runtime.cli sync-symbols

# 2. Backfill one series (end defaults to last closed boundary)
python -m scanner.runtime.cli backfill \
  --symbol BTCUSDT --timeframe H1 --start 2022-01-01

# 3. Prove the stored record hole-free against the incident ledger
python -m scanner.runtime.cli verify-continuity \
  --symbol BTCUSDT --timeframe H1 --start 2022-01-01 --end 2024-01-01
```

Exit codes: `0` clean · `2` needs attention (quarantined batches / uncovered holes).

## Reading the output
- `gaps_recorded=N` — the venue itself lacks candles there (outages,
  pre-listing). Recorded as resolved-`unfillable` incidents (DDD T8).
  This is honest history, not an error.
- `quarantined=N` — a batch failed the SLS §2.15 battery twice (fetch +
  one refetch). NOTHING from that span was persisted; an OPEN
  `validation_failure` incident exists. Escalate below.
- `UNCOVERED HOLE` from verify-continuity — a missing candle with no
  incident covering it. This is a defect in our pipeline, not the venue.
  File an issue; do not hand-patch data (Constitution §33.2 — no manual
  writes, rerun the pipeline after the fix).

## Quarantine escalation
1. Re-run the exact range once more (transient corruption clears itself).
2. Still quarantined: inspect the raw response (`curl` the klines URL from
   the incident notes' span) — venue-side corruption goes to the ops
   diary + a bounded exclusion decision recorded as an ADR note.
3. Resolve the incident (`resolution=unfillable` or `backfilled`) ONLY
   after the decision is recorded — open incidents are the work queue.

## Rate limits
The adapter runs under the weight budget authority (1100/min default,
`SCANNER_BINANCE_WEIGHT_CAPACITY`). Full-universe backfills self-throttle;
expect ~2h for 400 symbols × H1 × 2y. Never bypass the budget with
parallel ad-hoc scripts — the budget object is the ONLY Binance caller.

## Verification cadence
After any backfill session: `verify-continuity` over the touched ranges;
output goes into the ops diary (Roadmap §12).
