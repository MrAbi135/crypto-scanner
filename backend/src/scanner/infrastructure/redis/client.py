"""One place that builds the Redis client, with the settings a long run needs.

Every process was calling `aioredis.from_url(url)` bare. That is fine while
work is short, and wrong the moment a caller holds a connection across
something slow: the engine processes a batch of candle closes that can take
minutes, and on the first G1b resume proof the connection had timed out by the
time it went back for more work. The consumer task died with
`network:TimeoutError`, and only the crash callback added in the same change
made that visible rather than leaving a live process consuming nothing.

The three settings below are what make an idle-then-reused connection survive:

* `health_check_interval` — redis-py pings a connection that has been idle
  this long before handing it out, and reconnects instead of failing.
* `socket_keepalive` — the OS keeps the TCP connection from being reaped
  underneath us while a batch runs.
* `retry_on_timeout` — a single timed-out command is retried rather than
  raised, which is the difference between a blip and an outage for a caller
  that has no other way to tell them apart.
"""

from __future__ import annotations

import redis.asyncio as aioredis

# Comfortably shorter than any idle timeout a broker or proxy is likely to
# impose, and cheap: one PING on a connection that has been unused this long.
HEALTH_CHECK_INTERVAL_S = 30


def build_redis(url: str) -> aioredis.Redis:
    """The client every process should use."""
    return aioredis.from_url(
        url,
        health_check_interval=HEALTH_CHECK_INTERVAL_S,
        socket_keepalive=True,
        retry_on_timeout=True,
    )
