# Golden Datasets

Curated, hand-labelled SLS scenarios and the detector output doctrine requires
for them. Golden datasets are the detector-development workflow (Constitution
§32.3–§32.4): **a detector without a golden dataset may not ship**, and passing
datasets grow monotonically — they are never deleted or weakened to make a
release.

```bash
scripts/golden-verify.sh
```

The same suite runs in CI's `backend` job, so a change that alters doctrine
output cannot merge silently.

## What a golden run actually does

The harness wires the **real** detection services to in-memory ports and feeds
them a dataset's candles. It contains no doctrine of its own — every judgement
comes from `scanner.domain` / `scanner.application`, exactly as the engine
process executes it. A harness that re-implemented the rules would only ever
agree with itself.

| Piece | Role |
|---|---|
| `harness/dataset.py` | Load + validate dataset files; enforce provenance and gap-free series |
| `harness/memory.py` | In-memory `CandleRepository`, `EngineEventRepository`, state store, fixed clock |
| `harness/runner.py` | Wire the production service, run it, return canonical output |
| `harness/canonical.py` | The canonical form and its sha256 — what "byte-identical" means here |
| `test_golden.py` | The gate: dataset comparison, determinism ×3, provenance, harness self-test |

## The canonical form

Determinism (Constitution §29.3, §32.5; SLS §0) is only meaningful against a
canonical encoding, so: `Decimal` → exact string (never float — the no-float
law is worthless if the comparison rounds), `datetime` → ISO-8601 normalised to
UTC, keys sorted, compact separators, UTF-8 bytes.

Two fields are excluded from comparison:

- **`created_at`** — a clock reading at write time, not a detection fact.
  Including it would bake "when the harness ran" into doctrine's fingerprint.
- **`event_key`** — a sha256 fully derived from fields already compared, and
  not something a human labelling by hand could compute. Its real property,
  uniqueness, is asserted separately by the runner.

## Writing a dataset

One JSON file per case, under `datasets/<sprint>_<engine>/`. Prices are
strings. Volume fields default sensibly so a labeller only writes OHLC.

```json
{
  "dataset_id": "s4-swing-high-clean",
  "engine": "structure",
  "sls_sections": ["3.1", "3.3"],
  "description": "one line",
  "labelling_rationale": "why the SLS requires this exact output",
  "labelled_by": "name",
  "labelled_at": "2026-08-16",
  "algo_version": "s4-v2",
  "symbol": "GOLDENSTRUCT",
  "timeframe": "M5",
  "candles": [{ "open_time": "...", "open": "9.5", "high": "10", "low": "9", "close": "9.8" }],
  "expected": { "report": { }, "events": [] }
}
```

### Declared history (`filler`)

SLS §1.9 requires **≥ 300 closed candles** before structure, liquidity or ICT
detection may run at all, and names ATR baselines as one of the reasons. A
golden case that runs on eight candles is therefore exercising the engine in a
regime doctrine says cannot occur.

Writing 300 candles of OHLC by hand is not realistic, so history is **declared**
rather than enumerated:

```json
"filler": { "count": 293, "open": "100", "high": "103", "low": "100", "close": "100" },
"candles": [ ...the seven hand-written scenario candles... ]
```

The loader prepends `count` identical candles immediately before the first
scenario candle, at the timeframe's cadence, and the contiguity check runs over
the whole series.

Two properties make this sound, and `test_filler.py` asserts both:

- **Filler emits nothing.** Identical candles form a flat window, which confirms
  no swing under §3.1 and opens no gap under §5.4 — the same rule
  `s4-flat-window-emits-nothing` pins. 300 filler candles add history without
  adding one fact.
- **Filler is additive, never destructive.** Every detection the bare scenario
  produced still appears once history is declared, unchanged apart from its
  index offset.

**Filler is not verdict-neutral, and that is the point.** Declared history
genuinely *enables* detections a short series cannot reach: an external swing
needs `k_ext = 5` candles on its left, so a scenario candle near the start of a
bare series is unjudgeable and becomes judgeable once history exists. Migrating
`s4-classification-hh-hl-uptrend` to declared history surfaced exactly this — an
external swing low that the 15-candle version had no way to confirm. The short
version was under-reporting; the padded one shows what the engine would really
see. Expect migration to *add* expected output, and read every addition rather
than assuming it is noise.

**Indices stay absolute.** With 293 filler candles the third scenario candle is
index 295, and the label says 295. Translating indices to be scenario-relative
would put a layer between the engine's answer and the expectation, which is
precisely where an off-by-one hides.

**Choosing the filler's height.** Filler contributes true range, so it moves
ATR. Set the filler's high−low to the scenario's *mean* true range and the
blended ATR lands exactly on the value the scenario was designed around — which
is what keeps thresholds hand-checkable.

### The rule that matters most

**Derive the expectation from the SLS. Never paste the detector's output.**

A dataset built by running the engine and recording whatever came back can
only ever agree with the code it was copied from. It catches future
regressions and is blind to the bug sitting in front of you today. The
`labelling_rationale` field is where a reviewer checks which kind you wrote —
it must reason from doctrine to the expected numbers, not restate the
description. The loader rejects an empty rationale and the suite rejects a
short one.

Constitution §5 puts detector correctness on the developer personally. AI may
draft a dataset; the developer owns the label.

### Other rules

- **Curated input + expected output, versioned together.** Each case pins the
  `algo_version` it was labelled against.
- **Gap-free series.** The loader rejects non-contiguous candles: SLS §2.15.4
  forbids confirming structure across a data hole, so an accidental gap would
  silently test gap handling while claiming to test doctrine. Gap behaviour
  gets its own explicit datasets.
- **Raw captures stay local.** `golden/**/raw/` is gitignored; only curated,
  trimmed, labelled datasets are committed.
- **Ids are unique** and drive test ids, so a failure names the case.

## Coverage status

| Engine | Datasets | Roadmap DoD | State |
|---|---|---|---|
| structure (S4) | 4 | ≥ 60 | clean swing, equal-extreme tie, flat-window absence, HH/HL classification |
| liquidity (S5) | 3 | ≥ 50 | single-candle sweep, close-through break, two-candle sweep |
| ict zones (S6) | 5 | ≥ 80 | FVG wick-fill progression, sub-filter rejection, bearish mirror, IFVG born UNPROVEN, BPR from live parents. **Only the FVG/IFVG/BPR pass is wired** — the order-block, OTE and interaction services have their own ports and land later, so a dataset expecting their output would silently see nothing. |

### Detectors with no golden case yet

Breadth before depth: a detector with *zero* cases is more dangerous than one
with three instead of sixty, because Constitution §32.3 says it may not ship at
all. Current gaps, and what each needs:

| Detector | Blocked on |
|---|---|
| BOS / CHoCH / MSS (§3.5–§3.6) | A series long enough to confirm three external swings per side (`k_ext = 5`), since the trend gate that arms BOS needs two consecutive HH/HL pairs — roughly 50+ hand-built candles |
| Trend state machine (§3.4) | Same: no external swing has ever confirmed in a golden series |
| EQH/EQL clusters (§4.3), stop hunts (§4.7) | **Unreachable, not merely unwritten.** `detect_equal_level_clusters`, `detect_stop_hunt` and `mark_stop_hunt_failed` are implemented and unit-tested but have **no caller** anywhere outside `domain/liquidity/`, and no table exists for their output. A golden case cannot observe a detector the engine never runs. See `docs/evidence/S5/CHECKLIST.md`. |
| BPR §5.6 parent-liveness | The rule itself is **not enforced** — see `tests/unit/application/detection/test_bpr_parent_state_defect.py`, an `xfail(strict=True)` repro. A golden case cannot encode it until the ordering is fixed. |
| Order blocks (§5.1), Breaker (§5.2), Mitigation (§5.3), OTE (§5.7), PD arrays | Their services (`ict_ob_replay`, `ict_ote_replay`, `ict_interaction_replay`) are not wired into the harness yet |

**All 12 datasets now declare 300 candles**, so every case satisfies the §1.9 warm-up count rather than running in a regime doctrine excludes. This is the beginning of that debt being paid, not the end of it.
`run_dataset` raises on an unsupported `engine` value rather than skipping, so
a dataset for an unwired engine fails loudly instead of passing vacuously.

### Opaque identifiers

`pool_id`, `transition_id` and `event_key` are sha256 digests — deterministic,
but not something a human labelling a case could write. Rather than drop the
cross-references that make evidence readable, the liquidity runner **aliases**
pool ids to their natural key (`pool:BSL:4` = side plus creation index) and
applies the substitution recursively through every payload. `transition_id`
is dropped outright, being derived from fields already compared.

One thing the liquidity canonical form deliberately omits: the pool-creation
`evidence` blob, which restates the strength component breakdown. Its total is
compared, its components are unit-tested, and reproducing a nested JSON blob by
hand would make datasets unwritable — which costs more coverage than it buys.
