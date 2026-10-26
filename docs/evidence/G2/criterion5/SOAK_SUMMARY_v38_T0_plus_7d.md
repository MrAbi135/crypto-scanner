# Soak summary — v38, T0 → T0+7d

Window: **2026-10-18T20:20:39Z → 2026-10-25T20:20:39Z**. Release `p1b-531cbfc`, `s8-confluence-v38`, `s6-ob-v9`. Everything below was read-only from the VM on 2026-10-25 at about 20:30Z.

During the soak the VM checkout moved by `git pull` alone, and no container restarted:
- #335 (notifier layout)
- #336 (grade B pushes, SLS v1.0.17)
- #337 (R shown to two places)
- #338 (certifi lock)

## Engine
- **Detection passes: 43,575.** That is M5 30,240 · M15 10,080 · H1 2,520 · H4 630 · D1 105, which equals 15 × (2,016 + 672 + 168 + 42 + 7). **Not one close was dropped.**
- Failed passes **0**, `"level": "error"` lines **0**.
- Restarts of engine, ingest, worker and api: **0**. All four started at T0.
- Pass elapsed: p50 0.65 s, p95 0.86 s, max 1.93 s. The pre-deploy pass stage was p50 1.64 s.
- Stream at the end: pending 0, lag 0.
- Setups evaluated: 13,994, of which 47 passed the floor and became 27 signals after §10.3 dedup.
- Backfilled candles: **0**.
- WebSocket disconnects: **10**, and none of them lost a candle (0 backfilled, no ingest-watch finding).
  - 7 keepalive timeouts
  - 2 opening-handshake timeouts
  - 1 `1001 going away`
  - 6 of the 10 came on 10-20 between 12:23 and 13:13Z

## Latency (`latency_after_deploy_20261025T2029Z.txt`, `boundary_after_deploy_20261025T2029Z.txt`, against `*_before_deploy_20261018T2016Z.txt`)

| boundary | before p50 / p95 (s) | after p50 / p95 (s) |
|---|---|---|
| 15 (M5) | 22.10 / 25.46 | 10.73 / 11.72 |
| 30 (M5+M15) | 43.79 / 51.16 | 20.58 / 22.05 |
| 45 (+H1) | 66.36 / 77.65 | 30.58 / 32.52 |
| 60 (+H4) | 90.12 / 105.43 | 41.74 / 43.22 |
| 75 (+D1) | 116.79 / 118.62 | 46.56 / 56.59 |
| **all (2,017 boundaries)** | 23.92 / 66.51 (p99 92.28) | **11.13 / 30.69 (p99 42.03)** |

- **SLS §14 row 1** (≤ 30 s p95, ≤ 60 s p99): **p99 is met. p95 is 0.69 s over.**
- **Row 2** (close → all detectors evaluated, ≤ 2 s per symbol-TF): **not met.** Close → pass done is p95 25.3 s. The pass itself takes 0.87 s; the rest is queueing on one core (see the G2 scorecard).

## Signals (all grade B; Telegram pushes B since #336 on 2026-10-20)

| TF | signals | SUCCESS | FAILED | EXPIRED_ACTIVE | EXPIRED_UNTOUCHED | INVALIDATED_EARLY |
|---|---|---|---|---|---|---|
| M5 | 18 | 0 | 5 | 9 | 1 | 3 |
| M15 | 7 | 2 | 2 | 2 | 0 | 1 |
| H1 | 2 | 0 | 2 | 0 | 0 | 0 |
| **all** | **27** | **2** | **9** | **11** | **1** | **4** |

- **Archetype:** A3 24 · A4 2 · A2 1.
- **Direction:** **DOWN 21 · UP 6.** Every UP signal came on or after 10-21, once the higher timeframes stopped being bearish. The 0-UP stretch on 10-18 to 10-20 was F6 HTF alignment doing its job: 1,285 of 1,905 UP setups had an opposed HTF, and reading (b) of "CAUTION toward D" would have published none of them (`audit/F6_READING_B_20261021.md`).
- UP outcomes: 1 SUCCESS · 2 FAILED · 2 EXPIRED_ACTIVE · 1 INVALIDATED_EARLY.

### Against the v36 soak (36 signals, `SOAK_SUMMARY_T0_plus_7d.md`)

| | v36 | v38 |
|---|---|---|
| signals | 36 (5.1/day) | 27 (3.9/day) |
| SUCCESS / FAILED | 4 / 15 (21% of decided) | 2 / 9 (18% of decided) |
| EXPIRED_ACTIVE | 13 | 11 |
| M5 total R (incl. open R at expiry) | +2.12 over 17 | +2.10 over 14 |

**v38 was a correctness fix, not a quality fix, and the numbers say exactly that.** The share of decided signals that won is about the same (18% against 21%). Signal quality is unchanged and still weak. That is the open question for the next step.

## M5 R (only M5 records R; SLS §12.4)

| outcome | n | sum R | mean R |
|---|---|---|---|
| FAILED | 5 | **−5.00** | −1.00 |
| EXPIRED_ACTIVE (open R at expiry) | 9 | **+7.10** | +0.79 |
| **total** | 14 | **+2.10** | +0.15 |

- **All of the positive R is open R at expiry, as on v36.** Three M5 trades were at +2.0 to +2.2R when their 48-candle TTL ran out: LISTAUSDT 10-21 17:40, LITEBUSDT 10-22 01:20 and LINKUSDT 10-24 11:50. They count as EXPIRED_ACTIVE, not SUCCESS, because the ladder has no cap.
- These are engine touch/close semantics with no fees or slippage. **They are not a trading return.**

## Independent check against Binance (`audit/INDEPENDENT_CHECK_v38_soak.html`)
- 27 signals, 186/186 evidence records found, 19 series, 0 Binance gaps.
- **Claims: 679 PASS / 84 CANNOT / 0 FAIL.** **Outcomes: 23/23 match**, plus 4 INVALIDATED_EARLY that cannot be checked.
- **`displaced` (the v38 fix): 26/26 PASS.** v36 had 15 FAILs on the same check.
- **The first run said 5 FAILs, and all five were defects in the checker, not the engine.**
  - It searched for an OB's origin and a swing pool's swing backwards from the signal, which found a *later* candle at the same price.
  - It had no OTE logic, so OTE fell through to the OB check.
  - Fixed from the engine's own `created_at` definitions. The control, the v36 practice set, is unchanged at 817 / 104 / 15.
  - Details are in `audit/README.md`.
- The 84 CANNOT are mostly §4.4 pool class (53, which needs the dealing range) and §3.6 MSS condition 2 (23).

## Alarms (`alerts.log`, hourly invariants, ingest watch)
- Invariants runs in the window: **168, all clean.**
- `alerts.log`: **empty**. J and H2 never fired.
- `ingest_watch.alerts`: **no file**, meaning no BACKFILL NO-PASS and no INGEST GAP across the whole week.

## Telegram
- **Sent: 14 signals and 14 close-the-loop messages.** The send path is proven end to end, and the owner saw them on the phone.
- 7 `suppressed grade=B (§10.1)`, all before #336 went live on 2026-10-20 19:43Z.
- 6 `suppressed tier=INELIGIBLE`, all LISTAUSDT and LITEBUSDT, by design.
- 14 + 7 + 6 = 27 = every signal decided. `notify.cron.log` is 0 bytes.

## G2 criterion 5
- The hand-audit sheet is `audit/G2_AUDIT_v38_soak.html`: 27 signal cards, 223 evidence lines, and a suggested sample of 10 cards.
- **Criterion 5 asks for a human hand-audit.** The independent check is a machine check, and whether it counts is the owner's call.

## Open (owner)
1. **Signal quality** is the binding problem. 2 wins and 9 losses this week; v36 had 4 and 15.
2. M5's TTL ends winning ladders as EXPIRED_ACTIVE. Whether that is the intended scoring is a doctrine question.
3. §14 row 2 (2 s) needs parallel engines (#320 shards, off), probably on more cores. Row 1's p95 is 0.69 s over.
4. Criterion 5's human audit.
5. certifi 2026.10.19 reaches the containers only at the next deploy.
