# Cache Registry (TAD §18)

Every cached value in the platform is registered here before it is written. An
unregistered cache is a defect: caching without a documented key, TTL, and
invalidation trigger is how stale data silently becomes "live" (Constitution
§45.3 — never present stale data as live).

## Rules
- **One row per logical cache entry.** Register it in the PR that introduces it.
- **TTL is mandatory and bounded.** No unbounded caches.
- **Invalidation is explicit.** State exactly what event evicts or refreshes the
  key. "Eventually consistent" is not an invalidation policy.
- **Freshness-sensitive reads bypass cache.** Anything gated by SLS §2.12
  freshness reads the source of truth, never a cache.
- **Redis is disposable.** Cache is derived state only; PostgreSQL is the system
  of record (DDD §A1).

## Registry
| Key pattern | Layer | Value | TTL | Invalidation trigger | Owning sprint |
|---|---|---|---|---|---|
| `scanner:stream:candle-closed` | Redis Stream | Relayed `market.candle.closed` events awaiting engine consumption | **none** — capped at ~100k entries (`MAXLEN ~`) | Trimmed by the cap; consumed via consumer group. Not a cache: T39 is the record, this is delivery | S4b |
| `scanner:engine-state:{context}` | Redis string | Structure engine state snapshot | **none — see note** | Rebuilt by `engine rebuild-state`; overwritten each run | S4 |
| `scanner:liquidity-state:{symbol}:{tf}` | Redis string | Liquidity pool working set | **none — see note** | Overwritten each run | S5 |
| `scanner:ict-zones:{symbol}:{tf}` | Redis string | Live ICT zone working set | **none — see note** | Overwritten each run | S6 |

### Note on the three missing TTLs

The rule above says TTL is mandatory and bounded. The three state stores from
S4/S5/S6 set none, and were never registered here at all — found on 2026-08-17
while registering the S4b stream. Recording the gap rather than quietly writing
"n/a" in the column.

They are not dangerous in the way an unbounded *cache* is: each key is
overwritten on every run for its context, so the set is bounded by the universe
size rather than growing without limit, and all three hold derived state that
`rebuild-state` can reconstruct from Postgres. What is missing is eviction for a
context that leaves the universe — those keys are never written again and never
expire.

Fixing it means choosing a TTL long enough not to evict live working state
mid-session, which is a behaviour change to three detection paths and belongs in
its own change, not in the PR that noticed it. Tracked as S4b follow-up.

The stream row is different: `MAXLEN ~ 100000` is a real bound, and the stream
is delivery rather than cache — Postgres (T39) remains the record of what
happened.
