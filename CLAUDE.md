# CLAUDE.md

Repo-level context for Claude Code sessions working in this repository.

**This file does not govern anything.** Authority lives in
`docs/governance/` — the Constitution, then the SLS, then the DDD, roadmap and
API spec. Where this file and those disagree, they win. Where the code and the
SLS disagree, the SLS says the code is wrong.

## Future Integration: the Hybrid Trading Bot

There is a second, **separate** project on this account — the **Hybrid Trading
Bot** (`Downloads\HYBRID TRADING BOT`). It is an independent codebase with its
own repository, its own governance and its own history. Nothing in this
repository is built for it, and no work here should be justified by it.

What it is, in the owner's words:

- A hybrid rule-based + RL crypto trading bot, for Binance Spot and Futures.
- Strategy grounded in ICT/SMC — supply and demand, fair value gaps, order
  blocks.
- **Phase 1:** rule-based signals plus LightGBM scoring (in progress).
- **Phase 2:** reinforcement-learning agent integration (later).

**The plan, already decided by the owner:** once this Scanner is ready and
stable, the Bot will reuse the Scanner's data pipeline, its ICT/SMC zone
detection engine, and its confidence scoring engine — so that the Bot does not
rebuild those components, and signal quality stays consistent across both
projects.

### What that means for work inside this repository

Three things follow, and they are worth stating because a future session could
easily get them backwards:

1. **The direction of the dependency is one-way.** The Bot consumes the
   Scanner. The Scanner does not know the Bot exists and must not grow toward
   it: the Constitution forbids auto-execution here, and §13 already frames
   downstream consumers as read-only (Portfolio reads immutable outcomes;
   Backtesting must execute the specification byte-for-byte). Reuse means the
   Bot reads what this engine publishes — not that this engine acquires
   execution hooks, order routing, or Bot-shaped configuration.

2. **The contract is the migrations, not the DDD.** The design document and the
   real schema have drifted: ids are `VARCHAR(160)`, JSON-bearing columns are
   `TEXT` rather than `JSONB`, detection tables carry plain `symbol` /
   `algo_version` with no foreign keys to the registry (deliberate — sealed
   evidence must never be re-labelled retroactively), and zones live in
   `detection.ict_zones` with `band_low`/`band_high`, not the DDD's
   `detection.zones`. Anyone wiring a consumer should read
   `backend/src/scanner/infrastructure/persistence/alembic/versions/`.

3. **This engine's success statistics are not a trader's returns.** §12.4's
   semantics are asymmetric on purpose: SUCCESS is the target being *touched*,
   FAILED is the invalidation being *closed* through, and a wick through the
   stop records only `stress_test`. A bot holding a real stop exits on that
   wick. So a consumer must re-simulate outcomes with execution-realistic fills
   plus fees and slippage, and must never present this engine's published hit
   rate as its own expected performance. This is not hypothetical: the same
   signals scored PF 1.121 under close-based stops and PF 0.697 under
   touch-based stops when it was measured.

There is no export, webhook or public-API surface for scan results today, and
the public API programme is deferred. The two integration paths that exist are
the authenticated REST/WS client and a direct read of the `detection` schema;
the first honours the freshness, version and entitlement contract, the second
bypasses it.
