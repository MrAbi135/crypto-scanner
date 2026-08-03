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
| _(none yet)_ | — | — | — | — | — |

The first entries arrive with the read API (S11) and the live feed (S2).
