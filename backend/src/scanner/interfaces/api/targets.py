"""§15.2's Target Zone row as the API presents it, with SLS v1.0.15's ladder priced.

`target_bands` stores a laddered signal's exit as its RULE -- start and step in
R, no last rung -- because "no maximum" leaves no finite list to store. A
dashboard needs prices, and it must not compute them: that would be one more
implementation of R outside the domain, and part 1 of this change existed to
collapse three of those into one. So the first rungs are priced here, by the
domain's own `SignalLevels.rung_price`, from the signal's sealed record.

A pool-exit signal's targets are returned exactly as stored. Nothing is added
for them, so every response that predates the ladder is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from scanner.application.detection.signal_monitor import levels_from_record
from scanner.application.ports.signals import SignalRecord

# The owner's own rule named three -- "TP1 2R, TP2 3R, TP3 4R" -- with no cap
# after them. Three are priced; the payload says the rest continue.
RUNGS_PRICED = 3


def targets_view(signal: SignalRecord) -> dict[str, Any]:
    """The stored targets, plus the priced rungs when the exit is a ladder."""
    targets: dict[str, Any] = json.loads(signal.target_bands)

    if not targets.get("ladder"):
        return targets

    levels = levels_from_record(signal)
    ladder = levels.ladder

    # `levels_from_record` read the same `target_bands`, so a ladder there is a
    # ladder here; a mismatch would mean the reconstruction is broken.
    assert ladder is not None

    targets["ladder"] = {
        **targets["ladder"],
        "rungs": [
            {
                "n": n,
                "r": str(ladder.rung_r(n)),
                "price": str(levels.rung_price(n)),
            }
            for n in range(1, RUNGS_PRICED + 1)
        ],
        # §12.3 v1.0.15, stated where a reader of the prices will see it: the
        # rungs are not exits on their own, they move the stop.
        "trailing": "the invalidation trails one rung behind the highest reached",
    }

    return targets
