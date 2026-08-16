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
  "algo_version": "s4-v1",
  "symbol": "GOLDENSTRUCT",
  "timeframe": "M5",
  "candles": [{ "open_time": "...", "open": "9.5", "high": "10", "low": "9", "close": "9.8" }],
  "expected": { "report": { }, "events": [] }
}
```

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
| structure (S4) | 3 | ≥ 60 | clean swing, equal-extreme tie, flat-window absence |
| liquidity (S5) | 3 | ≥ 50 | single-candle sweep, close-through break, two-candle sweep |
| ict zones (S6) | 1 | ≥ 80 | FVG wick-fill progression. **Only the FVG/IFVG/BPR pass is wired** — the order-block, OTE and interaction services have their own ports and land later, so a dataset expecting their output would silently see nothing. |

This is the beginning of that debt being paid, not the end of it.
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
