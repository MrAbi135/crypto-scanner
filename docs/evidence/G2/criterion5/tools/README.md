# G2 criterion 5: hand-audit sheet

**Owner ruling, 2026-10-18:** criterion 5 counts the **new 7 days**, i.e. the soak that starts at the next deploy's T0, on v37 code. The file here, `G2_AUDIT_PRACTICE_old_soak.html`, is **practice only**: the old soak, v36, 2026-10-10 14:04Z → 10-17 14:04Z, 36 signals.

## Making the real sheet (after the deploy + 7 days)
From this folder on the laptop:
```
ssh -i "C:/Users/My PC/Downloads/ssh-key-2026-08-20.asc" ubuntu@141.148.205.213 "python3 -" < audit_data.py > audit_new_soak.json
python make_sheet.py audit_new_soak.json G2_AUDIT_new_soak.html "G2 audit sheet — naya soak (v37)"
```
- With no arguments, `audit_data.py` reads `~/soak-logs/T0` and takes T0 → T0 + 7 days. For any other window, pass two ISO UTC times after `python3 -`.
- Every query is read-only (`default_transaction_read_only = on`). It takes about 5 s on the VM.
- Run it only after T0 + 7 days. Before that, the window is still filling up.

## What the sheet does
- One card per signal: coin, timeframe, direction, archetype reason in plain words, entry/stop/target, outcome, and TradingView links (Binance spot, own and next-higher timeframe).
- One line per evidence record the engine scored (BOS, MSS, external swing, sweep pool, stop hunt, target pool, zone). Each line gives the claim and where to look; the owner marks ✓ / ✗ / ? and can add a note. A last line holds the whole-signal verdict.
- A record the lookup cannot find is shown as "record nahi mila", never dropped. On the practice set 216/216 were found.
- "★ pehle ye" marks a suggested sample: at least one per (timeframe, outcome). How many to audit is the owner's call.
- Marks stay in the browser (localStorage, best effort) and leave as `g2_audit_marks.csv` via "CSV nikalo".

## Checked 2026-10-18 on the practice set
- 36 signals. Outcomes match `SOAK_SUMMARY_T0_plus_7d.md`.
- 0 of 170 evidence events are dated after their signal.
- 171/171 UTC↔PKT pairs are correct.
- Buttons and counter work. The CSV has 275 rows (header + 274).
- No horizontal scroll at 375 px.

## Known limits
- A record's time is its `created_at`, and what that means depends on the record. For an OB it is the **first origin candle** (`domain/ict/order_blocks.py`). For a breaker it is the parent OB's invalidation. For an FVG it is the third candle's close. For a swing pool it is after the swing confirms. Corrected 2026-10-26: the earlier "write time" note was wrong.
- Swing indexes (window-local) are deliberately not shown.

## Independent check (machine, against Binance), added 2026-10-18
The owner asked me to do the verification myself ("tum khud kar lo"). This is a **machine check, not the human audit** that criterion 5 names; whether it counts is the owner's call.
```
python independent_check.py audit_new_soak.json check_new_soak.json
python make_report.py check_new_soak.json audit_new_soak.json INDEPENDENT_CHECK_new_soak.html "…"
```
- Candles come from Binance's public spot API, cached in `binance_cache/`, not from our DB. No engine code is imported; ATR, swings, displacement, FVG, OB and the outcome walk are rewritten from the SLS text.
- Practice result (old soak, 36 signals): 817 PASS / 15 FAIL / 104 CANNOT claim checks; outcomes 33/33 exact (3 INVALIDATED_EARLY not checkable).
- All 15 FAILs are one finding. The engine pays "displaced" (+18 F1) by the §7.5 impulse leg, while §3.5 defines it by the break candle or the one before passing §5.10. 15 signals are affected, 12 of them A3; 11 fail regardless of my ATR seeding.
- Two outcome rules the engine follows and SLS §12.3 does not state: the entry-touch candle is excluded from stop/target, and on M5 a moved stop applies from the next candle. With both, 33/33 match; without them, 30/33. The flags are `ACT_EXCL` and `MOVED_NEXT` (default 1).
- My first run said 166 FAIL. Nearly all of those were my own misreadings: event dating (swings are dated at the swing candle, stop hunts one candle after the displacement), the two-candle sweep, §3.1's tie rule, §4.4's positional external class, a BOS before the trend allowed it, and too little history for a zone. Each was resolved from the SLS text or the engine's own records before being changed here.

## Clauses and the displaced what-if (2026-10-18)
- Every check in `independent_check.py` names its SLS clause in a `clause` field. A check without one raises an error; on the practice set all 972 checks have one. Two are labelled "engine convention (measured), not an SLS clause": the event dating of stop hunts and swings.
- `whatif_displaced.py audit.json setups.jsonl` is read-only. It applies SLS 3.5's candle definition of `displaced` to the BOS each signal's F1 attributes, then recomputes `floor(base ± 3.6 + synergy − penalty)` from the stored values. The model reproduces the stored final for 36/36 setups.
- Practice result: 15 signals lose +18, 1 gains +18. 7 fall below their archetype floor, 6 of them A3. 12 of the 29 A3 signals lose A3. In total 13 would not publish as published.
- Limits: whether a lost A3 would fall back to A4/A5 cannot be read, because `archetype_unmet` records only the archetypes tried before the chosen one. Setups that were not published and might gain +18 are not covered.
- `setups_practice.jsonl` came read-only from the VM (factor scores, adjustments, gates, archetype_unmet).

## v38 soak run, and a checker fix (2026-10-26)
- Window 2026-10-18T20:20:39Z → 10-25T20:20:39Z. 27 signals, 186/186 records found.
- **The first run reported 5 FAILs, and all five were defects in this checker, not the engine:**
  1. OB origin runs were searched back from the signal, so a *later* run at the same band was found (2h, 14h and 4h after the true origin).
  2. A swing pool's swing was searched the same way, and was found 75 min after the pool.
  3. An OTE fell through to the OB check, because there is no OTE re-derivation.
- **Fix:**
  - An OB run must start at `created_at`.
  - A breaker's run ends by its `created_at`.
  - An FVG's c3 closes by its `created_at`.
  - A SWING pool's swing is at or before `created_at`.
  - An OTE is CANNOT.
- **Control:** the practice set (v36 soak) is byte-for-byte the same verdict count before and after (817 / 104 / 15, the 15 being the known `displaced` finding).
- **v38 result:** 679 PASS / 84 CANNOT / **0 FAIL**. Outcomes 23/23, plus 4 INVALIDATED_EARLY that are not checkable. `displaced` 26/26 PASS, where v36 had 15 FAIL.
- A first, wrong fix (bounding every zone by `created_at` as a write time) took the practice FAILs from 15 to 26. It was measured, reverted and replaced. The pre-fix checker is kept as `independent_check_pre_20261026.py`.
