# G2 criterion 5: the 7-day signal soak, audited

**Status: MET (2026-10-26), by an independent machine check against Binance's own candles, under the owner's ruling of that day.** The ruling is recorded in Roadmap v2.2.0 §17.

## The ruling
Roadmap §9 named the criterion "7-day signal soak hand-audited", and S9's DoD said "manually spot-audited by the developer against charts".

On 2026-10-26 the owner was offered two routes:
- (a) a person checks the sheet's ten starred cards in TradingView;
- (b) the independent machine check below counts.

The owner chose **(b)**: "(b) machine jaanch kaafi hai".

## The soak
- Window: **2026-10-18T20:20:39Z → 2026-10-25T20:20:39Z**.
- Build: release `p1b-531cbfc`, `s8-confluence-v38`, `s6-ob-v9`.
- During the window the engine had 0 restarts, 0 failed passes and 0 dropped closes (43,575 passes, which is 15 symbols × 2,905 closes).
- The full summary is in `SOAK_SUMMARY_v38_T0_plus_7d.md`.

## What the check is
`tools/independent_check.py` re-derives every claim a published signal's evidence makes, from **Binance's public spot klines**, and **imports nothing from the engine**. ATR, swings, BOS, displacement, FVG, order blocks, sweeps and the outcome walk are all rewritten from the SLS text. Every check names the SLS clause it tests, and a check without one raises.

Each check returns one of three verdicts:
- **PASS**
- **FAIL**
- **CANNOT**: the script cannot decide the claim. This is never a silent pass.

## Result
| | count |
|---|---|
| signals | 27 |
| evidence records found (of those the signals cite) | 186 / 186 |
| claim checks PASS | **679** |
| claim checks FAIL | **0** |
| claim checks CANNOT | 84 |
| outcomes matched candle by candle | **23 / 23** |
| outcomes not checkable (INVALIDATED_EARLY, a premise check) | 4 |
| `displaced`, the v38 fix | **26 / 26 PASS** (the v36 soak had 15 FAILs on this check) |

**The 84 CANNOT, by clause:**
- SLS §4.4, pool external/internal class (53). This needs the dealing range.
- SLS §3.6, MSS condition 2, sweep or failure swing (23).
- SLS §3.5, first close beyond the level plus trend agreement (5).
- SLS §3.6, CHoCH (2).
- SLS §5.8, OTE band (1). This is a Fibonacci band of a leg and is not re-derived.

None of these is a hidden FAIL. They are claims this tool does not decide.

## The checker's own defects, found on this soak and fixed with a control
The first run reported **5 FAILs, and all five were defects in the checker:**
- It searched for an order block's origin run, and a swing pool's swing, backwards from the *signal*. That found a later candle at the same price: 2h, 14h, 4h and 75 min late.
- It had no OTE logic, so an OTE fell through to the order-block check.

The fix uses each record's `created_at` as the engine defines it:
- an OB: its first origin candle (`domain/ict/order_blocks.py`);
- a breaker: the parent's invalidation;
- an FVG: c3's close.

An OTE is now CANNOT.

**Control:** the practice set, the v36 soak with 36 signals, gives byte-for-byte the same verdict counts before and after the fix: 817 PASS, 104 CANNOT and 15 FAIL, the 15 being the known `displaced` finding v38 fixed.

A first, wrong fix (bounding every zone by `created_at` read as a write time) moved the practice FAILs from 15 to 26. It was measured, reverted and replaced. Details are in `tools/README.md`.

## What this does not establish
- **Not signal quality.** The same soak's signals won 2 and lost 9, about the same as v36, and they lose to trading costs (`deploy-baseline/audit/QUALITY_FINDINGS_20261026.md`). Criterion 5 asks whether the engine did what the SLS says, not whether that makes money.
- **Not a human reading.** The chart-reading judgement the original wording asked for was waived by the owner's ruling, not performed. The hand-audit sheet `G2_AUDIT_v38_soak.html` is kept here for anyone who wants to do it.

## Reproduce
Read-only. Run from `tools/` with the laptop's SSH key. Candles are fetched from Binance and cached.
```
ssh -i <key> ubuntu@<vm> "python3 - 2026-10-18T20:20:39Z 2026-10-25T20:20:39Z" < audit_data.py > audit_v38_soak.json
python independent_check.py audit_v38_soak.json check_v38_soak.json
python make_report.py check_v38_soak.json audit_v38_soak.json INDEPENDENT_CHECK_v38_soak.html "Independent check — v38 soak"
python make_sheet.py audit_v38_soak.json G2_AUDIT_v38_soak.html "G2 audit sheet — v38 soak"
```
`SHA256SUMS` pins the files committed here.
