"""SLS §1.6's automatic stablecoin classifier.

§1.6 runs two detectors side by side: the curated list in `exclusions.py`,
which removes a symbol, and this classifier, which only raises a flag --
"30-day close-price standard deviation vs. 1.00 USD < 1% ⇒ flagged stable,
quarantined for manual confirmation. Auto-classification alone never removes
a symbol; it flags."

**"Standard deviation vs. 1.00 USD"** is read as the root-mean-square distance
of the 30 daily closes *from 1.00*, not their spread around their own mean.
The spread around the mean cannot be what is meant: it is small for any quiet
market at any price, and a peg is a claim about *where* the price sits, not
only about how still it is. Against 1.00 the measure is small only for a
token that both sits at a dollar and stays there.

The flag has three states, and the transitions are the whole contract:

* no flag + deviation below the limit -> FLAGGED (a new candidate for review);
* FLAGGED + deviation at or above the limit -> no flag (the price left the peg,
  so there is nothing left to confirm);
* DISMISSED is a person's decision that the symbol is not a stablecoin. No
  measurement changes it, in either direction -- a classifier that re-raised a
  dismissed flag every night would make the review meaningless.

Confirming the other way is not a state here: a confirmed stablecoin belongs
on the curated list, which excludes it permanently (§1.3).

Fewer than 30 closes measures nothing and changes nothing. A token listed last
week has no 30-day record to judge, and inventing one from a shorter window
would flag every new listing that opens flat near a dollar for the wrong reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import Enum

PEG_WINDOW_DAYS = 30
PEG_DEVIATION_LIMIT = Decimal("0.01")
PEG_TARGET = Decimal("1")


class StableFlag(str, Enum):
    FLAGGED = "FLAGGED"
    DISMISSED = "DISMISSED"


def peg_deviation(closes: Sequence[Decimal]) -> Decimal | None:
    """RMS distance of the closes from 1.00 USD, as a fraction of 1.00.

    None unless there are exactly `PEG_WINDOW_DAYS` closes: the window is the
    rule, not a maximum.
    """
    if len(closes) != PEG_WINDOW_DAYS:
        return None

    mean_square = sum(((close - PEG_TARGET) ** 2 for close in closes), Decimal(0)) / len(closes)

    return mean_square.sqrt() / PEG_TARGET


def next_stable_flag(current: StableFlag | None, deviation: Decimal | None) -> StableFlag | None:
    """The flag after tonight's measurement."""
    if current is StableFlag.DISMISSED or deviation is None:
        return current

    if deviation < PEG_DEVIATION_LIMIT:
        return StableFlag.FLAGGED

    return None
