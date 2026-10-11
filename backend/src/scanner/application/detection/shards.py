"""Which engine process runs which symbol (TAD §4: "sharded by symbol-hash consumer groups").

One engine process uses one core: Python runs one thread of bytecode at a time,
and on the 2-core VM a close burst left the second core idle while ~60 passes
queued behind each other (measured 2026-10-11: close -> pass done p95 54 s,
engine at ~100% of one core, Postgres at ~17%). Speed step 2 lets several
processes share the work.

Two rules must survive the split, and both do because a symbol is owned by
exactly one process:

* **One context at a time** (TAD §10): a symbol-timeframe's passes run in
  order, never concurrently -- every pass reads and writes that context's
  state.
* **Higher timeframe first** within a boundary (`order_for_detection`): a
  lower timeframe reads the rung above it, written by the same process
  earlier in the same batch.

Each shard has its own consumer group on the one candle stream, so every
process sees every close; a process runs the closes it owns and acknowledges
the rest unprocessed in its own group. Ingest does not change.

The hash is SHA-256 of the symbol, so the owner of a symbol is the same in
every process and across restarts -- Python's own `hash()` is salted per
process and would hand one symbol to two engines.

With one shard -- the default -- the group is `engine` as it always was and
every symbol is owned, so nothing changes until a deploy sets a count.

**Balance is the hash's, not ours.** Over the 15 symbols scanned today, two
shards split 6/9, so a close burst finishes when the nine-symbol shard does:
about 1.67x rather than 2x. A round-robin over the configured symbol list would
split 8/7, but it would make ownership depend on every engine holding the same
list; one engine restarted against an edited list could then share a symbol
with another, and one context would run twice at once. The hash cannot
disagree with itself, and it evens out as the universe grows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from scanner.application.ports.event_consumer import CANDLE_GROUP


def shard_of(symbol: str, count: int) -> int:
    """The shard that owns `symbol` when there are `count` of them."""
    if count < 1:
        raise ValueError("a shard count must be at least 1")

    digest = hashlib.sha256(symbol.encode("utf-8")).digest()

    return int.from_bytes(digest, "big") % count


@dataclass(frozen=True, slots=True)
class Shard:
    index: int = 0
    count: int = 1

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("a shard count must be at least 1")

        if not 0 <= self.index < self.count:
            raise ValueError(f"shard index {self.index} is outside 0..{self.count - 1}")

    def owns(self, symbol: str) -> bool:
        return self.count == 1 or shard_of(symbol, self.count) == self.index

    @property
    def group(self) -> str:
        """This shard's consumer group.

        The single-shard name is the one every deployment so far has used, so
        a one-shard engine keeps its pending list and its place in the stream.
        A sharded group's name carries the count as well as the index: a
        group from a 2-way split must never be resumed by a 3-way one, which
        would own a different set of symbols.
        """
        if self.count == 1:
            return CANDLE_GROUP

        return f"{CANDLE_GROUP}-shard-{self.index}-of-{self.count}"
