<!-- One PR = one squash commit = one story (S0.1 guide §16). -->

## Implements
<!-- MANDATORY governance-reference line. Cite the roadmap unit and the governing
     spec section(s), e.g. "Implements: Roadmap S1 / SLS §2.13 / DDD T3". -->
Implements:

## What & why
<!-- The story: what this change does and why it is correct against the cited spec. -->

## Governance checklist (Constitution §40)
- [ ] Cites the governing document section(s) above; no scope beyond them
- [ ] Production-level code only — no stubs, TODOs, placeholders, or dead code (§8.4, §45.1)
- [ ] Strict typing; no untyped public interfaces (§9.4)
- [ ] No floating-point for money/prices/quantities at storage or API boundaries (§45.8)
- [ ] Tests land with the change; source files have their mirror test files (§32)
- [ ] Layer boundaries respected (import-linter / eslint-boundaries green)
- [ ] Lockfiles updated if dependencies changed, with justification (§23)
- [ ] Docs/ADR updated if a decision was made

## Validation evidence
<!-- Paste or link the lint / type-check / test / import-linter results. -->
