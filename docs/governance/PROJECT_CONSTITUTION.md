# PROJECT CONSTITUTION

## Institutional AI Crypto Scanner

**Document Status:** Supreme Governing Document
**Authority Level:** Highest — overrides all other documents, preferences, and ad-hoc decisions
**Applies To:** All contributors, all modules, all releases, all future expansions
**Version:** 1.0.0
**Ratified:** 2026-07-10

> Nothing shall be designed, written, merged, or deployed unless it complies with this Constitution. Where any other document, instruction, or convenience conflicts with this Constitution, this Constitution prevails. Amendments require an explicit, versioned revision of this document.

---

## 1. Vision Statement

The Institutional AI Crypto Scanner exists to give professional traders an institutional-grade market intelligence platform: a system that continuously scans the entire cryptocurrency market in real time, detects institutional order-flow behavior before it becomes visible to the retail crowd, and presents high-probability opportunities with transparent, explainable reasoning.

The platform is not a screener, not an indicator collection, and not a retail signal service. It is a professional trading intelligence system built on institutional methodology (ICT / Smart Money Concepts), rigorous data engineering, and disciplined AI — engineered from day one to operate as a commercial SaaS platform serving thousands of professional users concurrently.

## 2. Mission Statement

To design, build, and continuously evolve a real-time, AI-augmented market scanning platform that:

1. Ingests and processes the full breadth of exchange market data with verifiable accuracy and minimal latency.
2. Detects institutional market-structure events (BOS, CHoCH, MSS, liquidity sweeps, order blocks, fair value gaps, and related constructs) with precise, deterministic, testable logic.
3. Ranks, scores, and explains opportunities so that every signal is auditable and every recommendation is justified.
4. Maintains commercial-grade reliability, security, and scalability at all times — never sacrificing quality for speed of delivery.
5. Grows module by module into a complete institutional trading workspace: scanning, alerting, analysis, journaling, backtesting, and strategy construction.

## 3. Project Philosophy

1. **Institutional, not retail.** Every analytical feature must be grounded in institutional trading methodology. Retail-style indicator mashups are constitutionally prohibited as core signal logic.
2. **Design precedes code.** No module is implemented before its architecture, data flow, interfaces, and failure modes are designed and approved.
3. **Determinism before intelligence.** Detection logic (market structure, liquidity, zones) must be deterministic and testable. AI augments, ranks, and explains — it never replaces verifiable detection logic as the source of truth.
4. **Truth over comfort.** The system reports what the data shows. It must never fabricate, smooth over, or exaggerate signals to appear more useful.
5. **Every module is a product.** Each subsystem must be built to the standard of an independently shippable, commercially supportable component.
6. **Longevity over novelty.** Technology choices favor proven, maintainable, well-supported foundations over fashionable but unstable ones.
7. **The platform must explain itself.** Any output a trader sees must be traceable to the exact data, rules, and model reasoning that produced it.

## 4. Core Objectives

1. Real-time, full-market scanning of supported exchanges (beginning with Binance) with sustained accuracy under load.
2. A complete ICT / SMC detection engine covering market structure, liquidity, institutional zones, and premium/discount context.
3. Multi-timeframe analysis as a first-class capability, not an afterthought.
4. AI-driven coin ranking, momentum scoring, and natural-language trade explanation with full auditability.
5. A professional real-time dashboard meeting institutional UI standards.
6. Alerting infrastructure (Telegram first) with guaranteed delivery semantics and user-configurable filtering.
7. Commercial SaaS readiness: multi-tenancy, subscriptions, entitlements, usage metering, and operational monitoring.
8. An extensible platform architecture that can absorb every capability listed in the Long-Term Roadmap without structural rewrites.

## 5. Product Principles

1. **Signal quality over signal quantity.** Fewer, higher-conviction, well-explained opportunities always beat noisy volume.
2. **Latency is a feature.** Time-to-detection is a core product metric and must be measured, published internally, and defended.
3. **Explainability is mandatory.** No black-box outputs. Every score, rank, and alert carries its reasoning.
4. **The trader stays in control.** The platform informs decisions; it does not make them. No feature may auto-execute trades without an explicit, separately governed execution module and user consent framework.
5. **Progressive disclosure.** Interfaces present the essential first and the deep detail on demand.
6. **Zero tolerance for stale data presented as live.** Data freshness must be visible; degraded data must be labeled as degraded.
7. **Professional trust.** Reliability, honesty of presentation, and consistency of behavior are product features equal in rank to any analytical capability.

## 6. Long-Term Roadmap

The roadmap is expressed as capability phases. Phases are sequential in dependency, not necessarily in calendar; a later-phase capability may be prototyped early but may not ship before its dependencies meet constitutional standards.

**Phase 1 — Foundation (Market Intelligence Core)**
Live Binance market scanner; real-time data ingestion pipeline; multi-timeframe candle engine; relative volume detection; momentum scoring; core dashboard.

**Phase 2 — Institutional Detection**
ICT detection engine: BOS, CHoCH, MSS; liquidity sweep detection; order blocks; fair value gaps; breaker blocks; mitigation blocks; premium/discount zones; multi-timeframe structure alignment.

**Phase 3 — AI Intelligence Layer**
AI coin ranking; signal confluence scoring; AI trade explanations; adaptive signal-quality feedback.

**Phase 4 — Trader Workflow**
Telegram alerts; watchlists; custom filters; dashboard analytics; heatmaps.

**Phase 5 — Professional Toolkit**
Portfolio tracking; trade journal; risk calculator; performance analytics; backtesting engine.

**Phase 6 — Platform Expansion**
Strategy builder; AI assistant; news integration; economic calendar; whale activity monitoring; futures data integration; plugin system.

**Phase 7 — Commercial Scale**
Subscription tiers; multi-tenant hardening; mobile application; internationalization; public API program.

Every phase must ship at production quality. No phase may be declared complete with known constitutional violations outstanding.

## 7. Software Architecture Principles

1. **Modular monolith first, services when justified.** The system begins as a strictly modularized single deployment with hard internal boundaries, enabling extraction of services (scanner, AI, alerting) when scale demands — without rewrites.
2. **Clean Architecture layering is mandatory.** Dependencies point inward: UI → Application/Services → Domain → nothing. Infrastructure (exchanges, databases, message transports) is accessed only through interfaces owned by the domain/application layers.
3. **Strict separation of concerns.** UI, business logic, data access, API, configuration, models, services, and utilities are physically and logically separated. Mixing responsibilities in one module is a constitutional violation.
4. **Event-driven data flow.** Market data ingestion, detection, scoring, and alerting communicate through well-defined events/messages, never through shared mutable state.
5. **Every boundary has a contract.** All inter-module communication occurs through explicitly versioned interfaces, schemas, or events. No module may reach into another module's internals.
6. **Stateless where possible, explicit state where not.** Services must be horizontally scalable by default; any stateful component must document its state model, recovery behavior, and scaling strategy.
7. **Replaceability.** Every external dependency (exchange, database, LLM provider, message broker) sits behind an adapter so it can be replaced without touching domain logic.
8. **Failure is a first-class design input.** Every architectural design must specify behavior under: exchange outage, data gap, partial failure, backpressure, and restart.
9. **No speculative architecture.** Structures are built for the approved roadmap, not imagined futures (YAGNI at the architectural level) — but boundaries must be placed so roadmap items fit without demolition.

## 8. Engineering Standards

1. All work follows the sequence: design → review/approval → implementation → testing → review → release. Skipping stages is prohibited.
2. SOLID, DRY, KISS, and YAGNI are binding, not advisory.
3. Domain-Driven Design vocabulary is used for the trading domain: the terms used in code (e.g., `OrderBlock`, `LiquiditySweep`, `MarketStructureShift`) must match the ubiquitous language of this document.
4. Every function and module ships complete: no placeholders, no TODO comments, no stubbed logic, no pseudo-code, no unfinished branches.
5. Exceptions are never swallowed. Silent failure is a constitutional violation.
6. Global mutable state is prohibited. Dependency injection is the default composition mechanism.
7. Concurrency must be explicit and documented: every concurrent component declares its synchronization model and backpressure strategy.
8. All configuration is externalized; behavior may never depend on hardcoded environment-specific values.
9. Code that cannot be tested is considered incorrect by definition and must be restructured.

## 9. Coding Standards

1. Production-level code only. Demo-quality code may not be merged, even behind flags.
2. Every function documents: purpose, inputs, outputs, error behavior, and relevant edge cases.
3. All inputs are validated at boundaries (API, message, user input, exchange payloads). Internal layers may trust validated types, never raw data.
4. Type safety is mandatory: full type annotation/strict typing in every language used; untyped public interfaces are prohibited.
5. Naming must reveal intent; abbreviations are prohibited except industry-standard ones (e.g., OHLCV, BOS, FVG, ATR, API).
6. Functions do one thing. Modules own one responsibility. Duplicate logic must be extracted, never copied.
7. Magic numbers are prohibited; all thresholds, timeframes, and constants are named and centralized in configuration or constants modules.
8. Comments explain *why*, not *what*. Code that needs a comment to explain *what* must be rewritten.
9. Linting and formatting are enforced automatically in CI; style debates are settled by tooling, not opinion.
10. Every merged change compiles, passes all tests, and introduces zero new warnings.

## 10. Folder Structure Standards

1. The repository is organized by architectural layer and bounded context, never by file type alone.
2. The following top-level separations are mandatory and permanent: presentation/UI; application services; domain (trading logic, detection engines); infrastructure (exchange adapters, persistence, messaging); API layer; AI layer; configuration; shared utilities; tests; documentation; operations/deployment.
3. Each bounded context (e.g., scanner, detection, ranking, alerting, portfolio) owns its own directory subtree with its own models, services, and tests.
4. No file may live at a path that misrepresents its layer. A domain rule inside an infrastructure folder is a violation regardless of whether it works.
5. Shared code must be genuinely shared and dependency-free of higher layers; a "utils" dumping ground is prohibited — utilities are grouped by purpose.
6. Test structure mirrors source structure one-to-one.
7. The concrete folder tree for each phase is proposed as a design artifact and approved before implementation; once approved, structural changes require a documented refactoring decision (Section 41).

## 11. Naming Conventions

1. **Domain terms are canonical.** Institutional concepts use their full ICT/SMC names in types and modules: `BreakOfStructure`, `ChangeOfCharacter`, `MarketStructureShift`, `LiquiditySweep`, `OrderBlock`, `FairValueGap`, `BreakerBlock`, `MitigationBlock`, `PremiumDiscountZone`.
2. Classes/types: `PascalCase`. Functions/methods/variables: language-idiomatic (`snake_case` in Python, `camelCase` in JS/TS). Constants: `UPPER_SNAKE_CASE`. Files/folders: `snake_case` (backend), `kebab-case` (frontend assets/components per framework idiom).
3. Boolean names read as predicates: `is_`, `has_`, `should_`, `can_`.
4. Events are named in past tense (`LiquiditySwept`, `StructureBroken`); commands in imperative (`ScanMarket`, `EvaluateSymbol`).
5. Interfaces/adapters carry role-based names (`MarketDataProvider`, `AlertChannel`), never vendor names in domain code; vendor names appear only in infrastructure implementations (`BinanceMarketDataProvider`, `TelegramAlertChannel`).
6. Database objects: `snake_case`, plural table names, singular column names, no prefixes encoding type.
7. API routes: lowercase, hyphen-free resource nouns, versioned (Section 15).
8. No name may lie: a name describing behavior the code does not have must be corrected immediately.

## 12. Documentation Standards

1. Documentation is a deliverable, not an afterthought. A feature without documentation is incomplete.
2. Every module maintains a module document covering: purpose, responsibilities, dependencies, configuration, data contracts, failure behavior, and future improvements.
3. Every architectural decision of consequence is recorded as an Architecture Decision Record (ADR): context, options considered, decision, consequences. ADRs are immutable; reversals create new ADRs.
4. Every detection algorithm (BOS, CHoCH, sweeps, zones, etc.) has a specification document defining its exact rules, edge cases, and validation criteria — written before implementation and kept in sync with it.
5. Public APIs are documented from machine-readable schemas (single source of truth); prose docs are generated or verified against them.
6. Documentation lives in the repository, versioned with the code it describes.
7. Out-of-date documentation is treated as a defect with the same severity as a code defect.

## 13. Git Workflow

1. The default branch is permanently releasable. Broken states may never be merged to it.
2. All work happens on short-lived feature branches named `type/scope-description` (e.g., `feat/detection-order-blocks`, `fix/scanner-reconnect`).
3. Commits follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `perf:`, `chore:`) with imperative, meaningful messages. Cosmetic messages ("wip", "update") are prohibited on merged history.
4. Every change reaches the default branch through a reviewed merge request — no direct pushes, including by the project owner.
5. Every major change documents: what changed, why, files affected, migration notes, and breaking changes.
6. CI must pass (build, lint, type-check, tests) before any merge. A red pipeline blocks everything it covers.
7. History is kept clean: squash or rebase noisy branches before merge; force-pushing shared branches is prohibited.
8. Release states are tagged; tags are immutable.

## 14. Versioning Strategy

1. The platform and all public contracts follow Semantic Versioning: `MAJOR.MINOR.PATCH`.
2. `MAJOR` — breaking changes to public APIs, data contracts, or user-visible behavior contracts. `MINOR` — backward-compatible capability additions. `PATCH` — backward-compatible fixes.
3. Internal module interfaces are versioned at their boundaries; breaking an internal contract requires coordinated versioning of all consumers within the same release.
4. Database schema versions are managed exclusively through ordered, reversible migrations; manual schema edits are prohibited in every environment.
5. Detection algorithms carry their own version identifiers; every stored signal records the algorithm version that produced it, so historical signals remain interpretable after logic changes.
6. AI models and prompts are versioned artifacts; every AI output records the model/prompt version that produced it.
7. A changelog is maintained for every release, derived from Conventional Commits.
8. Deprecations require: an announcement release, a migration path, and a stated removal version. Silent removal of functionality is prohibited.

## 15. API Standards

1. All external APIs are versioned in the path (`/api/v1/...`); breaking changes require a new version and a deprecation window for the old one.
2. REST semantics for resource operations; WebSocket (or equivalent streaming) for real-time market and signal streams. Streaming payloads carry schema versions.
3. Every endpoint defines: authentication requirements, authorization rules, request schema, response schema, error model, rate limits, and idempotency behavior — before implementation.
4. A single, uniform error envelope is used across all endpoints: machine-readable error code, human-readable message, correlation ID. Internal details (stack traces, queries) never leak through the API.
5. All list endpoints paginate; unbounded responses are prohibited.
6. All timestamps are UTC, ISO-8601, milliseconds precision. All monetary/price values use decimal-safe representations — floating-point money is prohibited at API boundaries.
7. Rate limiting, request validation, and payload size limits are enforced at the edge for every endpoint, no exceptions.
8. The API surface is a product: consistency of naming, shape, and behavior across endpoints is mandatory and reviewed.
9. Internal service-to-service communication obeys the same contract discipline as public APIs.

## 16. Database Standards

1. Storage is selected per workload: relational storage for transactional/user data; time-series-optimized storage for market data; in-memory caching for hot paths. Each choice is justified in an ADR.
2. The database is an infrastructure detail. Domain logic never depends on a specific database; all persistence flows through repository interfaces owned by the domain/application layer.
3. Schema changes occur only through versioned, ordered, reversible migrations reviewed like code.
4. Every table defines: primary key, ownership (which bounded context writes it), retention policy, and indexing rationale. Cross-context writes to another context's tables are prohibited.
5. Market data (candles, ticks, order-flow aggregates) is immutable once written; corrections are appended with provenance, never edited in place.
6. All prices and quantities are stored in decimal-safe types. Floating-point storage of monetary values is prohibited.
7. Time-series data defines explicit retention, downsampling, and archival policies from the moment the table exists — unbounded growth without a policy is a violation.
8. Backups are automated, encrypted, and restore-tested on a schedule. A backup that has never been restored is not considered a backup.
9. N+1 access patterns, unbounded queries, and missing-index full scans on hot paths are defects, not optimizations for later.
10. User data and market data are logically separated so tenant data can be exported or deleted without touching market history.

## 17. Security Standards

1. Security is a design constraint on every feature, not a hardening phase.
2. Secrets (API keys, tokens, credentials) never appear in source code, commit history, logs, error messages, or client-side payloads. Secrets live exclusively in secret-management facilities.
3. All external input — user, API, exchange, webhook, plugin — is untrusted and validated at the boundary. Injection classes (SQL, command, template, prompt) must be structurally prevented, not filtered case-by-case.
4. Authentication uses proven standards (industry-standard token/session frameworks); custom cryptography and custom auth protocols are prohibited.
5. Authorization is enforced server-side on every request; the UI hiding a control is never a security mechanism. Multi-tenant isolation is enforced at the data-access layer, not by convention.
6. All transport is encrypted (TLS everywhere, including internal service traffic in production). All sensitive data at rest is encrypted.
7. Principle of least privilege applies to every credential, service account, database role, and third-party scope. Exchange API keys used by users are stored encrypted, are never given withdrawal permissions by instruction and validation, and are never logged.
8. Dependency hygiene: automated vulnerability scanning on every build; known-critical vulnerabilities block release.
9. Rate limiting, brute-force protection, and abuse detection protect all authentication and expensive endpoints.
10. Security incidents follow a documented response procedure: contain, assess, notify affected users honestly, remediate, and record a post-mortem.

## 18. Error Handling Standards

1. Every error path is designed, not discovered. Design documents must enumerate failure modes for each module.
2. No exception is ever silently swallowed. Every caught exception is either handled meaningfully, enriched and rethrown, or escalated — always with logging and context.
3. Errors are typed and classified: validation errors, domain errors, infrastructure errors, and external-dependency errors are distinct and handled by distinct policies.
4. External dependencies (exchange APIs, AI providers, messaging) are wrapped with timeouts, bounded retries with backoff and jitter, and circuit breakers. Unbounded retries are prohibited.
5. Partial failure must degrade gracefully: if one symbol, one feed, or one detector fails, the scanner continues and the failure is quarantined and reported — one bad input may never halt the platform.
6. User-facing errors are honest, actionable, and free of internal detail. Internal errors carry full diagnostic context and a correlation ID linking API request → logs → traces.
7. Data-integrity errors (gaps, duplicates, out-of-order events) are first-class error types with defined reconciliation behavior, never ignored.
8. Fallback behavior must be explicit and visible: when the system serves degraded results, the degradation is labeled in the API and UI.
9. Crash-only discipline: every component must be safe to kill and restart at any moment without corrupting state.

## 19. Logging Standards

1. All logs are structured (machine-parseable), with consistent fields: timestamp (UTC), level, service, module, correlation ID, event name, and context payload.
2. Log levels have defined meanings and are enforced: `DEBUG` (development diagnostics), `INFO` (expected significant events), `WARNING` (abnormal but handled), `ERROR` (failed operations requiring attention), `CRITICAL` (platform integrity at risk). Level inflation and deflation are both violations.
3. Every signal, alert, and AI output is logged with full provenance: input data references, algorithm/model versions, and decision parameters. The audit trail is a product requirement.
4. Secrets, credentials, session tokens, and full personal data never appear in logs.
5. Logging is asynchronous and non-blocking on hot paths; logging may never degrade scanning latency materially.
6. Log retention, rotation, and storage cost policies are defined per environment before production launch.
7. Correlation IDs propagate across every boundary: request → service → event → alert. Any log line must be traceable to its originating trigger.
8. Logs are for machines and operators; user-facing messaging is never constructed from raw log content.

## 20. Performance Standards

1. Performance targets are defined per component before implementation and verified by measurement, not assumption. Every real-time path has an explicit latency budget.
2. Binding baseline targets (tightened, never loosened, as the platform matures): market event ingestion-to-detection latency measured in low seconds or better for scan cycles and sub-second for stream processing; dashboard interactive updates without perceptible lag; API p95 response times defined and monitored per endpoint.
3. The full-market scan cycle time is a first-class published internal metric with a defined maximum; regressions beyond budget block release.
4. Memory and CPU envelopes are defined per service; unbounded memory growth (caches without eviction, queues without limits) is prohibited.
5. Exchange API rate limits are treated as hard physical constraints: budgeted centrally, monitored continuously, with backpressure — never handled by ad-hoc sleeps scattered through code.
6. Caching is deliberate: every cache declares its key model, TTL/invalidation strategy, and staleness tolerance. Caches without invalidation strategies are prohibited.
7. Hot paths (ingestion, detection, scoring) are profiled before and after significant change; optimization is evidence-driven — premature micro-optimization and ignored macro-inefficiency are both violations.
8. Concurrency is used deliberately: async I/O for network-bound work, controlled parallelism for CPU-bound detection, with documented backpressure at every queue.
9. Load and soak testing at realistic full-market scale is required before any capacity-affecting release.

## 21. Scalability Principles

1. Scale is designed for thousands of concurrent professional users and full-market data breadth from the first architecture, even while early deployments run small.
2. Horizontal scalability is the default: any component that cannot scale horizontally must document why and define its vertical limits and failover story.
3. Workloads are separable: ingestion, detection, AI scoring, alert delivery, and user-facing serving must be independently scalable as load profiles diverge.
4. Multi-tenancy is structural: tenant context flows through every request and query; per-tenant limits, metering, and isolation are built into the platform layer, not bolted on.
5. Data volume strategy is mandatory: partitioning, downsampling, and archival for time-series data are designed alongside the schema, not after the first storage crisis.
6. Statelessness at the serving layer; shared state lives in purpose-built stores, never in process memory relied upon across requests.
7. Every queue, buffer, and subscription has bounded capacity and defined overflow behavior (shed, spill, or backpressure) — chosen explicitly per flow.
8. Scaling events (adding symbols, exchanges, timeframes, tenants) must be configuration and capacity operations, never code rewrites.

## 22. UI/UX Design Principles

1. The interface must look and behave like professional trading software: modern, minimal, fast, and dense with meaning — never cluttered, never childish, never gimmicky.
2. Dark mode is the primary design target; every visual decision is made dark-first. Light mode, if offered, must meet the same standard.
3. Visual hierarchy is engineered: excellent spacing, disciplined typography, restrained color used semantically (state, direction, severity) — color is information, not decoration.
4. Glassmorphism and depth effects are permitted where they aid hierarchy, prohibited where they reduce legibility or performance.
5. Animations are professional and purposeful: they communicate state change and continuity. Decorative animation on data-critical surfaces is prohibited.
6. Real-time surfaces must never jump, flicker, or reflow in ways that cause misreads; data updates preserve user context (scroll, selection, sort).
7. Responsiveness is mandatory across desktop breakpoints from launch; the design system must not preclude mobile (Section 38).
8. Every interactive element provides immediate feedback; every loading state, empty state, and error state is designed — no blank panels, no spinners without context.
9. Accessibility is a standard: sufficient contrast, keyboard operability, and readable type scales are required, not optional polish.
10. A single design system governs all surfaces: shared tokens, components, and interaction patterns. One-off UI inventions per feature are prohibited.

## 23. Dashboard Design Philosophy

1. The dashboard is a decision instrument, not a data dump. Its purpose is to answer, at a glance: *where is institutional activity happening right now, and why does it matter?*
2. Information architecture follows the trader's workflow: market overview → ranked opportunities → instrument deep-dive → action (alert, watchlist, journal). Navigation depth follows that order.
3. Progressive disclosure is the organizing rule: headline state first (rank, score, structure state), full evidence (events, zones, timeframes, AI explanation) on demand.
4. Density with clarity: professional users get high information density, achieved through hierarchy and alignment — never through shrinking text or cramming.
5. Every number on the dashboard displays or links to its provenance: timeframe, data freshness, and the logic version that produced it.
6. Real-time truthfulness: connection state, data staleness, and degraded feeds are always visible on the surface they affect.
7. Customization within a coherent frame: users may configure watchlists, filters, columns, and layouts, but customization may never let the interface misrepresent data.
8. Heatmaps, analytics views, and future modules plug into the same layout grid, token system, and interaction language — the dashboard is a platform surface, not a page.

## 24. Trading Engine Philosophy

1. The trading engine is the analytical core that interprets markets through institutional logic: market structure, liquidity, and displacement — not oscillator crossovers.
2. Analysis is hierarchical: higher-timeframe structure establishes context; lower-timeframe events acquire meaning only within that context. Context-free signals are constitutionally prohibited.
3. All engine logic is deterministic and reproducible: the same market data and configuration must always produce the same analysis, byte for byte. Nondeterminism belongs only to the clearly-bounded AI layer.
4. The engine is exchange-agnostic and instrument-agnostic in design: it consumes normalized market data and emits normalized events, enabling futures data and additional exchanges without core changes.
5. Every analytical concept is a first-class domain object with a specification, a version, and tests — never an inline calculation buried in pipeline code.
6. The engine emits events and evidence, not opinions: raw detections flow to scoring and AI layers, which are separate and independently versioned.
7. The engine must be honest about uncertainty: incomplete data, forming candles, and ambiguous structure are represented explicitly in its outputs, never guessed away.
8. Execution (order placement) is outside the engine's mandate. If ever built, execution is a separate, separately-governed module with its own constitutional amendment.

## 25. Scanner Engine Philosophy

1. The scanner's mandate is breadth with rigor: evaluate the entire supported market continuously, applying the full detection stack to every eligible symbol — no silent sampling, no hidden shortcuts.
2. Real-time first: streaming data drives detection wherever the exchange provides it; polling exists only as explicit, budgeted fallback.
3. The scan pipeline is staged and observable: ingest → normalize → detect → score → rank → publish. Every stage reports throughput, latency, and error rates.
4. Symbol universe management is explicit: listing rules, delisting handling, minimum-liquidity eligibility, and quote-asset scope are configuration with documented defaults — never hardcoded assumptions.
5. Fairness of attention: no symbol's analysis may silently starve because of load; scheduling guarantees bounded staleness for every symbol in the universe, with priority tiers as explicit configuration.
6. The scanner is resilient by quarantine: malformed data or a failing detector affects only the symbol/detector involved; the failure is logged, surfaced, and retried under policy.
7. Every scan result is timestamped, versioned, and reproducible against stored input data.
8. Scanner capacity limits (symbols × timeframes × detectors) are known, measured numbers — expansion is a capacity decision, never a surprise.

## 26. AI Engine Philosophy

1. AI's role is defined and bounded: ranking, scoring, confluence assessment, natural-language explanation, and assistant capabilities. AI never fabricates detections and never overrides deterministic detection logic.
2. Layered truth: deterministic engines produce facts; AI produces interpretation of those facts. Every AI output must be grounded in, and cite, the deterministic evidence it interprets.
3. Explainability is mandatory: every AI ranking or explanation is traceable to its inputs, model version, and prompt/feature version. Unattributable AI output may not be shown to users.
4. AI failure is a handled state: if the AI layer is unavailable, the platform continues operating on deterministic outputs, clearly labeled — AI is an enhancement layer, never a single point of failure.
5. Hallucination defense is structural: AI outputs referencing market facts are validated against the evidence store before display; contradictions are rejected, logged, and studied.
6. Model/provider independence: all AI capability sits behind internal interfaces; providers and models are swappable infrastructure.
7. Cost and latency discipline: AI usage is budgeted, cached where sound, and tiered by value — expensive inference is spent on high-value analysis, not on every tick.
8. Feedback loops are engineered, not implied: signal outcomes feed evaluation datasets so ranking quality is measured against reality, with documented methodology.
9. User data privacy: user portfolios, journals, and behavior are never sent to third-party model providers without explicit consent and contractual protection.

## 27. Market Data Philosophy

1. Market data is the platform's foundation of truth; its integrity outranks every feature. Bad data rendered beautifully is a constitutional failure.
2. Single source of truth per data type: one normalized internal representation for candles, trades, and derived aggregates; all consumers read the normalized form, never raw vendor payloads.
3. Completeness is verified, not assumed: gap detection, duplicate detection, and out-of-order handling run continuously; every gap is either healed through backfill or explicitly marked.
4. Provenance is preserved: every stored datum records its source, receipt time, and normalization version.
5. Freshness is a visible property: every consumer — engine, API, or UI — can always determine data age, and staleness thresholds trigger degradation labeling automatically.
6. The forming candle is sacred ground: incomplete intervals are structurally distinguished from closed intervals everywhere; detection logic declares explicitly whether it operates on closed data only. Repainting caused by conflating the two is a critical defect class.
7. Exchange connectivity is engineered for failure: reconnection with resume, sequence validation, heartbeat monitoring, and automatic backfill after outage are core requirements, not resilience extras.
8. Multi-source readiness: the data layer is designed so additional exchanges and data types (futures, funding, open interest, whale flows, news) join as new providers behind existing interfaces.
9. Historical data is a strategic asset: it is retained, versioned, and quality-controlled to serve backtesting and AI evaluation — the same data discipline applies to history as to live feeds.

## 28. Signal Quality Philosophy

1. A signal is a claim, and every claim carries a burden of proof: evidence (detections, timeframes, data references), confidence basis, and logic version.
2. Precision outranks recall: the platform's reputation rests on the quality of what it surfaces. Thresholds are tuned to suppress noise even at the cost of missing marginal opportunities.
3. Confluence is the core quality mechanism: signals strengthen through multi-timeframe agreement and multi-detector alignment; single-factor signals are ranked accordingly and never disguised as high-conviction.
4. Every signal has a lifecycle: created → active → validated/invalidated/expired. Signals age, expire under defined rules, and are never silently mutated after emission.
5. Signal outcomes are measured: the platform records what happened after every signal against defined evaluation criteria; signal quality metrics (hit rates, adverse excursion, decay profiles) are computed per algorithm version.
6. No survivorship editing: historical signals are immutable — including the wrong ones. Deleting or rewriting past signals is a constitutional violation of the highest order.
7. Honesty in presentation: scores and ranks display their basis; the UI may never imply certainty the underlying logic does not possess.
8. Quality regressions block release: a detection/ranking change that measurably degrades signal quality metrics may not ship, regardless of what else it improves.

## 29. Risk Management Philosophy

1. The platform's stance is fixed: it provides analysis and risk tooling — it does not provide financial advice, and its language must never promise outcomes.
2. Risk context is part of every opportunity: signals present structure-derived context (invalidation levels, premium/discount position, distance to liquidity) so users can assess risk, not just direction.
3. The risk calculator and portfolio tools follow professional conventions: position sizing from account risk percentage, R-multiple framing, and exposure aggregation — implemented with decimal-safe precision.
4. Conservative defaults: every configurable risk-related setting defaults to the conservative choice; aggressive configurations require deliberate user action.
5. The platform must never gamify losses or wins: no streak mechanics, no urgency manipulation, no engagement patterns that reward overtrading.
6. Educational integrity: explanations teach the institutional reasoning behind signals; the platform makes users better analysts, not more dependent clickers.
7. Data-driven self-awareness: performance analytics and the trade journal exist to confront users with their real statistics — accuracy of that accounting is a hard requirement.
8. Regulatory posture: user-facing analytical outputs carry appropriate disclaimers; jurisdictions' requirements are tracked as the commercial footprint grows.

## 30. ICT / SMC Principles

1. ICT / Smart Money Concepts constitute the platform's canonical analytical doctrine. Implementations must be faithful to the methodology's structural logic, not loose approximations.
2. Every ICT/SMC construct is precisely specified before implementation. The canonical construct set includes: Break of Structure (BOS), Change of Character (CHoCH), Market Structure Shift (MSS), liquidity pools and liquidity sweeps (buy-side/sell-side), order blocks, fair value gaps (FVG), breaker blocks, mitigation blocks, and premium/discount zones — each with exact, testable definitions covering formation, validation, mitigation/invalidation, and expiry.
3. Swing structure is the skeleton: swing point identification is a single, shared, versioned implementation; every structure-dependent detector consumes it — parallel, inconsistent swing logic is prohibited.
4. Timeframe hierarchy is doctrine: higher-timeframe bias governs lower-timeframe interpretation; discount zones matter in bullish context, premium zones in bearish context; the engine encodes this hierarchy explicitly.
5. Liquidity narrative first: the analytical frame treats price as moving toward liquidity and reacting at institutional zones; detectors exist to evidence that narrative, and their outputs compose into it.
6. State correctness over signal excitement: every zone and structure object maintains rigorous state (fresh, tested, mitigated, invalidated, expired, swept) with exact transition rules — stale zones presented as fresh are critical defects.
7. No repainting, ever: all ICT/SMC detections are computed on closed-candle data unless a detector explicitly and visibly operates intrabar; a detection, once confirmed, never disappears retroactively.
8. Doctrine evolves by specification: refinements to ICT/SMC logic go through spec revision, versioning, and quality re-measurement (Section 28) — never through quiet parameter drift.

## 31. Future AI Modules

1. All future AI capability — AI assistant, trade explanation, strategy suggestion, news interpretation, whale-flow analysis, adaptive ranking — is governed by the AI Engine Philosophy (Section 26) without exception. New AI modules inherit its grounding, explainability, versioning, and fallback requirements.
2. Each future AI module is chartered before construction: purpose, inputs, evidence sources, output contract, failure behavior, evaluation methodology, and cost model — approved as a design artifact.
3. The AI assistant operates with bounded authority: it may query platform data, explain analysis, and configure user-scoped settings on request; it may never alter detection logic, global configuration, or other users' data, and it may never present speculation as platform analysis.
4. News and economic-calendar interpretation modules must separate three layers visibly: the fact (event), the market context (data), and the interpretation (AI) — collapsed presentations that blur fact and interpretation are prohibited.
5. Learning systems (adaptive ranking, feedback-trained scoring) require: immutable training data lineage, offline evaluation gates before deployment, versioned rollout with rollback, and monitoring for drift and degradation.
6. No AI module may create a feedback loop that grades its own homework: evaluation data and criteria are owned outside the module being evaluated.
7. AI compute cost is a managed budget per module and per tenant tier; a module whose cost model breaks the subscription economics must be redesigned, not subsidized silently.

## 32. Testing Philosophy

1. Untested logic is unfinished logic. Test coverage is a completion criterion for every feature, with detection and financial logic held to the strictest standard.
2. The testing pyramid is enforced: extensive unit tests on domain and detection logic; integration tests on module boundaries, persistence, and exchange adapters; end-to-end tests on critical user journeys; load/soak tests on real-time infrastructure.
3. Detection algorithms are validated against curated golden datasets: hand-verified market scenarios encoding each construct's specification, including edge cases (equal highs/lows, gaps, wicks-only sweeps, overlapping zones). A detector without a golden dataset may not ship.
4. Regression protection is permanent: every fixed defect gains a test reproducing it; golden datasets grow monotonically; a passing test may never be deleted to make a change ship.
5. Determinism testing is explicit: engines are verified to produce identical output for identical input across runs and versions (unless the version change is the tested subject).
6. Failure is tested, not just success: disconnections, malformed payloads, gaps, timeouts, and partial outages have dedicated tests at every resilience boundary.
7. Tests are production-quality code: readable, maintainable, fast, isolated, and free of hidden interdependence. Flaky tests are defects with priority equal to the code they cover.
8. CI runs the full relevant suite on every merge candidate; no human may waive a red pipeline.
9. Backtesting infrastructure doubles as a validation instrument: strategy and signal logic must produce consistent results between live and historical execution paths on identical data.

## 33. Deployment Standards

1. All environments — development, staging, production — are defined as code and reproducible from the repository. Hand-configured environments are prohibited.
2. The pipeline is the only road to production: build → automated tests → staging deployment → verification → production. Manual artifact uploads and hotfixes outside the pipeline are prohibited.
3. Deployments are zero-downtime by design for user-facing services; real-time data continuity across deployments (stream resume, state recovery) is a tested requirement.
4. Every deployment is reversible: rollback procedures are automated, tested, and executable within minutes. A release without a rollback path may not proceed.
5. Configuration and secrets are injected per environment; a build artifact is environment-agnostic and promoted, not rebuilt, between stages.
6. Database migrations deploy with explicit ordering relative to code (expand → migrate → contract) so that rolling deployments never encounter incompatible schemas.
7. Feature flags gate risky or phased capabilities; flags are inventoried, owned, and removed after full rollout — permanent accidental flags are prohibited.
8. Every release is tagged, changelogged, and traceable: which commit, which migrations, which algorithm/model versions went live, and when.

## 34. Monitoring Standards

1. The platform is observable by construction: metrics, structured logs, and traces are built into every service from its first version — observability is never retrofitted.
2. The four golden dimensions are monitored for every service: latency, traffic, errors, and saturation — plus domain-critical metrics: data freshness per feed, scan cycle time, detection throughput, signal emission rates, alert delivery latency, and AI layer health.
3. Business truth is monitored alongside system truth: anomalies in signal volume, ranking distributions, or detection rates trigger investigation — silence from a detector is a symptom, not a comfort.
4. Alerting has discipline: every alert is actionable, owned, and documented with a response runbook. Noisy alerts are tuned or removed; alert fatigue is treated as an operational defect.
5. Health is externally verifiable: liveness, readiness, and dependency-health endpoints exist for every service and drive orchestration and status reporting.
6. User-impacting incidents are measured from the user's perspective: uptime and degradation SLOs are defined per capability (data freshness, alert delivery, dashboard availability) before commercial launch.
7. Every incident of consequence receives a blameless post-mortem: timeline, root cause, corrective actions with owners — and corrective actions are tracked to completion.
8. Cost observability is mandatory: infrastructure, data, and AI spend are monitored per component and per tenant tier so the economics of the platform are always known.

## 35. Commercial Product Standards

1. The platform is engineered as a commercial product at all times: every feature must be supportable, documentable, meterable, and sellable — hobby-grade shortcuts are prohibited even pre-revenue.
2. Reliability is a contractual posture: public SLOs, honest status communication during incidents, and no silent degradation of paid capability.
3. Tenant data is sacred: strict isolation, export capability, and deletion capability (right to be forgotten) are structural requirements ahead of the first paying user.
4. The product surface is professional end-to-end: onboarding, documentation, error messages, and emails meet the same quality standard as the dashboard.
5. Support is designed for: diagnostic tooling, correlation IDs surfaced to users, and admin tooling for account/entitlement management exist as product features.
6. Legal integrity: terms of service, privacy policy, data-processing transparency, and analytical disclaimers are versioned artifacts maintained with the product.
7. Pricing and packaging decisions are engineering inputs: capability boundaries (symbols, timeframes, alert volume, AI usage, API access) must be enforceable in the platform layer, not by honor system.
8. The brand promise is conservative: marketing claims may never exceed what the measured system delivers. Overpromising is a constitutional violation, not a growth tactic.

## 36. Subscription Readiness

1. Multi-tenant subscription architecture is prepared from the foundation: every user-scoped resource carries tenant identity; every capability check flows through a central entitlement service.
2. Entitlements are declarative: plans are defined as capability sets (feature flags, quotas, rate limits, data depth, AI budget) in configuration — introducing or changing a tier is a configuration act, never a code scavenger hunt.
3. Usage metering is built in: scan access, alerts, API calls, and AI consumption are metered per tenant with auditable accuracy, because billing disputes are resolved by records, not recollection.
4. Billing integration is abstracted: payment providers sit behind an internal billing interface; subscription state (active, trialing, past-due, canceled, grandfathered) is a first-class domain model with defined transitions.
5. Graceful commercial states: expiry and downgrade degrade capability precisely and predictably — never data loss, never abrupt lockout from owned data, always a clear path back.
6. Trials and grandfathering are designed states, not hacks: time-boxed trials, legacy plan preservation, and promotional entitlements all flow through the same entitlement model.
7. Revenue-critical paths (checkout, entitlement checks, renewal) receive the platform's highest testing and monitoring tier.
8. Tax, invoicing, and jurisdiction handling are delegated to proven providers — custom tax logic is prohibited.

## 37. Plugin System Philosophy

1. The plugin system's purpose is controlled extensibility: allowing detection modules, data providers, alert channels, and dashboard widgets to be added without touching the core — first for internal velocity, later for a vetted ecosystem.
2. Plugins are contract-bound: every plugin type has a versioned interface, a declared capability manifest, and a defined lifecycle (install, enable, configure, disable, uninstall).
3. Plugins are untrusted by default: sandboxed execution boundaries, explicit permission grants, resource quotas (CPU, memory, API budget), and no direct database or secret access — a plugin may only touch what its manifest declares.
4. Core integrity is inviolable: no plugin may modify core detection logic, other plugins, or platform data outside its scope; plugin failure is quarantined and may never destabilize the platform.
5. Plugin outputs are labeled: users always see which analysis came from core doctrine and which from a plugin, with the plugin's identity and version.
6. The core team eats the plugin API first: internal optional modules are built on the public plugin interfaces, guaranteeing the extension surface stays honest and capable.
7. A future marketplace requires its own governance amendment: review standards, security audit requirements, and revenue mechanics are defined before any third-party code runs in production.

## 38. Future Mobile App Standards

1. Mobile is a first-class future surface, not a shrunken dashboard: its charter is monitoring, alerting, and quick decision support — the workflows a professional needs away from the desk.
2. API-first discipline makes mobile possible: every capability is exposed through the same versioned APIs the web client uses; no web-only backdoors may accumulate.
3. Feature parity is deliberate, not assumed: each capability is classified as mobile-core, mobile-adapted, or desktop-only, by workflow logic rather than implementation convenience.
4. Push notification infrastructure inherits alerting standards: delivery tracking, user-controlled filtering, and honest latency — a missed critical alert on mobile is a severity-one defect.
5. Mobile UI obeys the same design system doctrine (Section 22): dark-first, professional, dense-but-clear, with interaction patterns adapted to touch.
6. Offline and degraded-network behavior is designed: cached last-known state clearly timestamped, graceful reconnection, and never stale data presented as live.
7. Battery, bandwidth, and background execution budgets are engineering requirements from the first mobile design document.
8. Mobile security matches platform standards: secure credential storage, biometric unlock support, and certificate pinning for API traffic.

## 39. Internationalization Standards

1. The platform is built i18n-ready from the start: no user-facing string is hardcoded; all copy flows through a localization layer even while English is the only shipped language.
2. Formatting is locale-aware by architecture: numbers, currencies, dates, and times render through centralized formatting services — with the constitutional exception that market timestamps remain UTC-anchored and unambiguous everywhere.
3. Layouts tolerate translation: text expansion, RTL readiness at the design-system level, and no meaning carried by string concatenation.
4. Trading terminology is curated per locale: ICT/SMC terms are localized by glossary decision (translate vs. keep canonical English), never by machine translation of domain vocabulary.
5. Time-zone correctness is absolute: users select display time zones; all storage and computation remain UTC; session-based concepts (killzones, market opens) are defined in their canonical market time zones and converted for display.
6. Localization assets are versioned with releases; an untranslated string in a shipped locale is a defect.
7. Regulatory and disclaimer text is localized with legal review, not literal translation.

## 40. Code Review Rules

1. Every change is reviewed before merge — no exceptions for seniority, urgency, or size. Self-merge without review is prohibited.
2. Review scope is constitutional compliance, not taste: architecture boundaries, layer separation, error handling, validation, tests, documentation, naming, performance implications, and security posture are checked against this document.
3. Detection and financial logic require specification review: the reviewer verifies the implementation against the written algorithm spec, not just against the code's internal consistency.
4. Reviewers are accountable for what they approve: an approval asserts the change is production-worthy under this Constitution.
5. Review feedback is specific and actionable: cite the violated principle or the concrete risk; "I would do it differently" is not a blocking argument — constitutional violation is.
6. Small, focused changes are the standard: a change too large to review responsibly must be split. "Too big to review" is grounds for rejection alone.
7. A change touching security, tenancy, billing, or data integrity requires heightened review rigor and explicit verification of the relevant constitutional sections.
8. No change merges with unresolved blocking comments; disagreements escalate through the Decision-Making Framework (Section 42), never through wearing the reviewer down.

## 41. Refactoring Rules

1. Refactoring is continuous maintenance, not a special event: code quality debt is paid as it is encountered, within the boundaries of the current change's scope.
2. Behavior preservation is the definition: a refactoring changes structure, never observable behavior. Structural change and behavioral change never ship in the same commit.
3. Refactoring is test-protected: no significant restructuring proceeds without tests covering current behavior first. Refactoring untested code begins by testing it.
4. Large refactorings are designed: anything crossing module boundaries requires a written plan — motivation, target structure, migration steps, and risk assessment — approved before work begins.
5. Detection logic refactoring carries extra duty: golden datasets must pass identically before and after; any output difference reclassifies the work as a logic change requiring versioning and spec revision.
6. The Boy Scout Rule applies with discipline: leave touched code better than found, but scope creep disguised as cleanup is prohibited — large discoveries become their own planned work.
7. Deprecated code is removed, not abandoned: dead code, commented-out blocks, and orphaned modules are deleted; the version control system is the archive.
8. Rewrites are a last resort requiring CTO-level decision: a rewrite proposal must prove refactoring is infeasible, not merely tedious.

## 42. Decision-Making Framework

1. Decision authority follows a defined hierarchy: (1) this Constitution; (2) approved architectural designs and ADRs; (3) module specifications; (4) written team conventions; (5) individual judgment. A lower level may never override a higher one.
2. Decisions of consequence are written: any choice affecting architecture, data contracts, detection doctrine, security posture, or commercial mechanics is recorded as an ADR with context, options, and rationale.
3. The evaluation order for competing options is fixed: correctness → security → data integrity → reliability → maintainability → performance → delivery speed → convenience. A tie at a higher criterion is broken at the next, never by preference for the easier path.
4. Reversibility calibrates rigor: easily reversible decisions are made quickly and revisited freely; hard-to-reverse decisions (storage engines, public API contracts, tenancy model) demand full analysis and explicit approval.
5. Evidence beats opinion: performance claims require measurements, quality claims require tests, market-logic claims require the specification and data. "It should be fine" is not evidence.
6. Disagreement resolution is structured: state positions against the evaluation order, identify the actual criterion in dispute, decide at the appropriate authority level, record the outcome — then commit fully, even those who disagreed.
7. Constitutional amendment is deliberate: changes to this document require an explicit proposal, impact review against all dependent sections, version increment, and a recorded rationale. The Constitution is never amended retroactively to excuse a violation.
8. In the absence of a covering rule, decide by the Project Philosophy (Section 3) and record the decision so the gap can be closed.

## 43. Development Lifecycle

1. Every unit of work follows the constitutional sequence, without stage-skipping: **Define** (problem, success criteria) → **Design** (architecture, contracts, data flow, failure modes) → **Approve** (design review against this Constitution) → **Implement** (production-quality, complete) → **Test** (per Section 32) → **Review** (per Section 40) → **Release** (per Section 33) → **Observe** (per Section 34) → **Learn** (metrics, post-mortems, spec refinement).
2. Design artifacts are mandatory deliverables for every module: architecture description, module breakdown, data flow, interface contracts, and failure-mode analysis — approved before implementation begins.
3. Work is decomposed into shippable, testable increments; a branch living longer than a short iteration signals decomposition failure, not dedication.
4. Definition of Done is uniform and non-negotiable: designed, implemented completely, tested, documented, reviewed, observable, and constitutionally compliant. Partial done is not done.
5. Quality gates cannot be traded against deadlines: when time is short, scope shrinks — quality never does.
6. Priorities follow the roadmap's dependency order (Section 6); foundation work is never deferred in favor of visible features built on sand.
7. Each phase concludes with a review: constitutional compliance audit, quality metrics assessment, and documented lessons feeding the next phase's designs.

## 44. Project Success Metrics

1. Success is measured, never asserted. Every metric below has a defined measurement method, an owner, and a review cadence.
2. **Data integrity metrics:** feed uptime, gap frequency and heal time, data freshness distributions, normalization error rates.
3. **Engine metrics:** full-market scan cycle time, ingestion-to-detection latency, detection throughput, determinism verification pass rate.
4. **Signal quality metrics:** per-algorithm-version hit rates against defined evaluation criteria, false-positive rates, signal decay profiles, confluence distribution — trending upward or explained.
5. **Platform reliability metrics:** SLO attainment per capability, incident count and severity trend, mean time to detection and recovery, rollback frequency.
6. **Engineering health metrics:** test coverage on domain logic, pipeline pass stability, review turnaround, defect escape rate, refactoring debt register size.
7. **Product metrics:** activation and retention of professional users, alert engagement quality (acted-upon rate, not raw clicks), feature adoption depth, support ticket rates per capability.
8. **Commercial metrics (post-launch):** conversion, churn, revenue per tier, cost per tenant, AI cost ratio, margin per subscription tier.
9. Vanity metrics are prohibited as decision inputs: raw signal counts, feature counts, and lines of code measure nothing this Constitution values.
10. Metric regressions are governance events: a sustained regression in any category triggers documented investigation, not silent acceptance of a new baseline.

## 45. Things That Must Never Be Done

The following are absolute prohibitions. No deadline, demo, investor meeting, or convenience justifies them.

1. **Never** ship placeholder logic, stubbed functions, TODO-marked code, or pseudo-code to any shared branch.
2. **Never** swallow an exception, fail silently, or hide a degraded state from operators or users.
3. **Never** present stale, gap-ridden, or estimated data as live and complete.
4. **Never** allow detection logic to repaint: a confirmed detection may not retroactively vanish or mutate.
5. **Never** delete, rewrite, or selectively hide historical signals or performance records.
6. **Never** let AI output override, fabricate, or contradict deterministic detection evidence — and never show users unattributable AI claims.
7. **Never** hardcode secrets, credentials, or API keys anywhere: not in code, config files under version control, logs, or client payloads.
8. **Never** use floating-point arithmetic for money, prices, or position sizes at storage or API boundaries.
9. **Never** bypass the pipeline: no unreviewed merges, no red-pipeline releases, no manual production changes, no migration by hand.
10. **Never** mix architectural layers: no business logic in UI, no database access from domain rules, no vendor payloads leaking past adapters.
11. **Never** duplicate domain logic instead of extracting it — especially swing/structure logic, which must remain singular.
12. **Never** trade security, data integrity, or tenant isolation for delivery speed.
13. **Never** build retail-indicator signal logic into the core analytical doctrine.
14. **Never** auto-execute trades, or blur the line between analysis and financial advice.
15. **Never** promise — in UI, docs, or marketing — capability or certainty the measured system does not deliver.
16. **Never** amend this Constitution retroactively to legitimize a violation that already occurred.

---

## Final Constitutional Principles

These are the non-negotiable rules that every future development decision must satisfy. They summarize the supreme obligations of this Constitution; where doubt exists, these principles govern.

1. **The Constitution is supreme.** No feature, deadline, or preference overrides this document. Changes to the rules happen by amendment, never by exception.
2. **Design before code, always.** Architecture, contracts, and failure modes are designed and approved before implementation begins — for every module, at every phase.
3. **Production quality is the only quality.** Complete implementations, full error handling, validated inputs, and tests are the minimum bar for any merged code. There is no "for now."
4. **Determinism is the source of truth; AI interprets, never invents.** Market analysis rests on deterministic, versioned, reproducible detection logic. AI ranks, scores, and explains — grounded in evidence, always attributable, never authoritative over facts.
5. **Data integrity outranks every feature.** Gaps are healed or labeled, freshness is visible, history is immutable, and nothing stale is ever dressed up as live.
6. **Institutional doctrine only.** ICT / Smart Money Concepts, precisely specified, versioned, and validated against golden datasets, define the analytical core. No repainting. No retail indicator mashups. No quiet parameter drift.
7. **Boundaries are law.** Clean Architecture layering, bounded contexts, versioned contracts, and adapter-isolated dependencies are structural requirements — violated by no module, ever.
8. **Everything is observable and auditable.** Every signal, score, alert, and AI output carries provenance: the data, the logic version, and the reasoning that produced it.
9. **Failure is designed for.** Every component defines its behavior under outage, gap, overload, and restart — degrades gracefully, quarantines faults, and recovers without corruption.
10. **Security and tenant isolation are absolute.** Least privilege, validated boundaries, encrypted transport and storage, no secrets in code, and structural multi-tenant isolation — from the first commit, not the first customer.
11. **The platform tells the truth.** To users, operators, and stakeholders: honest data, honest confidence, honest incidents, honest marketing. Signal quality metrics are measured against reality and regressions block release.
12. **Scale is architected, not hoped for.** Thousands of professional users and full-market breadth are design inputs today; scaling is configuration and capacity, never rewrite.
13. **Quality never yields to speed.** When constraints bind, scope shrinks. Skipping design, review, testing, or documentation is not an available trade.
14. **Every decision is accountable.** Consequential choices are written, evidence-based, evaluated in the fixed order — correctness, security, integrity, reliability, maintainability, performance, speed — and recorded for those who come after.

*Ratified as the supreme governing document of the Institutional AI Crypto Scanner. All development, present and future, proceeds under its authority.*

**— End of Constitution —**

