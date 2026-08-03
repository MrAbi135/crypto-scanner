# Test Fixtures — Provenance Rules

Recorded external payloads (e.g. Binance REST responses) used by adapter tests.
Fixtures live **with the tests that consume them**, never in a shared dumping
ground.

## Rules
- **No live-captured PII.** Market/exchange payloads only; never user data.
- **Bounded sizes.** Trim to the smallest payload that exercises the case; large
  captures are a repo-hygiene defect (§ assets law).
- **Record provenance.** Each fixture (or its directory README) states: the
  source endpoint, the capture date (UTC), and any trimming/redaction applied.
- **Deterministic.** Fixtures are frozen inputs — a fixture never changes to make
  a failing test pass; if the venue's shape changed, add a new dated fixture.

## Layout
```
fixtures/
  binance/         # recorded Binance REST payloads (S1 adapter tests)
    README.md      # per-source provenance
```
