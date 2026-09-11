# Gate G1b — Certification Record

**Gate:** G1b (Roadmap §9) — *"The doctrine is observable."*
**Certified:** 2026-09-11
**Verdict:** **PASSED**, with the build attribution below read in full.

Legend: ✅ measured with recorded evidence · 🟡 argued rather than measured

## The four criteria

| # | Criterion (Roadmap §9, verbatim) | State | Evidence |
|---|---|---|---|
| 1 | Engine runs unattended ≥ 72 h | ✅ | T0 `2026-09-07T18:44:31Z` → 72 h 3 m elapsed with `RestartCount = 0` on engine, worker, ingest and api, and identical `StartedAt` across all four. |
| 2 | Every candle close for the seeded universe produces a detection pass with no manual invocation | ✅ | **2484 of 2484.** Counted from the engine's own `detection_pass_completed` lines over the soak: M5 1728, M15 576, H1 144, H4 36 — each exactly `closes/hour × 72 × 2 symbols`. |
| 3 | The chart renders live structure/liquidity/zone objects for a symbol the developer did not pre-select | ✅ | `scripts/g1b_unselected_symbol.py` picks by a rule fixed before the outcome (USDT, not DELISTED, ordered by symbol, middle of the list): **LISTAUSDT 905** drawable objects, **LITEBUSDT 980**. Produced by the engine in normal operation, not by a one-off invocation. |
| 4 | `kill -9` on the engine loses no closes (resume proven, not assumed) | ✅ | `scripts/g1b_resume_proof.py`: 12 entries published, **11 unacknowledged at the moment of SIGKILL**, pending list empty after restart. The unacked count is the part that matters — it is what separates an interrupted batch from a drained queue. |

## Which build each criterion was measured on

They are not all one build, and saying so is the point of this section.

* Criteria **1, 2 and 4**: `main@4d8538c`, the batch deployed 2026-09-07.
* Criterion **3**: `main@c800b9c`, deployed 2026-09-11 — fifteen commits later.

The gap is one engine change (`s6-ob-v6`, PR #222) plus the ingest universe
widening from 2 symbols to 15. The engine change only removes a crash, so
criteria 1, 2 and 4 hold at least as well on the newer build — but that is an
argument, not a measurement, and it is recorded here as one. `main@c800b9c`
began its own 72-hour window at `2026-09-11T16:13:35Z`; when that elapses,
criteria 1 and 2 will have been measured on it directly.

## What the soak found

The soak was not decoration. It surfaced a real defect that three days of unit
tests and a green CI had not:

**23 `detection_pass_failed` errors**, all `IndexError`, all on M5/M15 where
the 500-candle window slides fastest:

```
ict_ob_replay.py:1152 in _has_failure_swing_before_invalidation
confirmed_at = candles[ob.confirmed_index].open_time
IndexError: list index out of range
```

The function's own docstring warns that persisted indices freeze in the window
that recorded them; the next line indexed today's candles with one. A sibling,
`_origin_has_sweep`, did the same on both of its bounds.

**No close was lost** — every one of the 23 was redelivered by the stream and
completed on retry, which is why criterion 2 reads 2484 of 2484 and why the
defect survived three days unnoticed. When it did not raise, it read the wrong
candle instead. Fixed in PR #222; both helpers now derive their bounds from the
OB's own `created_at` plus the index delta, which is durable where the indices
are not.

## Reading the release stamp

`SCANNER_RELEASE` is hand-set in `ops/env/dev.env` and was never updated, so the
soak's log lines all read `p1b-1842b4d` — a commit from 2026-08-29. **The build
identities in this record come from the deployed commit and the algo-version
constants in the running container, not from that stamp.** PR #214 makes the
deploy script stamp and verify it; the correction lands with the next deploy.

## What G1b does not claim

G1b is about observability, not correctness of the doctrine's output. Gate G2
is where that is settled, and it is not close: **62 of 130 enumerated SLS rules
have a golden case** (Roadmap §8.1 requires all of them, CI-enforced), three
datasets still await developer verification, and `close→detection p95` is
**5.67 s at 15 symbols** against G2's `≤ 2 s full-universe`.
