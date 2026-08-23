"""The one place that knows how a §3.3 classification event is named.

§3.3 labels a swing *"relative to the previous confirmed swing of the same
type (per strength class)"*, so a pivot that is both an internal and an
external swing carries **two** labels, computed against two different
predecessors. On the soak VM, 695 pivots carry both and **152 of them carry
different labels** -- LL against HL 80 times, HH against LH 59 times. Neither
is wrong: the internal sequence has more and closer pivots, so a pullback low
under the last internal low can still sit above the last external one. They
are answers to two questions.

That is also why reading them by string prefix is a trap. `STRUCTURE_` alone
matches both series, and a consumer that used it would get a different answer
on roughly a fifth of all pivots while looking entirely healthy. The engine
has already been bitten once by a label reader matching the wrong prefix --
`_read_labels` originally matched `SWING_*`, which carries no label at all,
and pinned §7.4's trend maturity at zero.

So the format lives here, and it cannot be read without naming a strength.
"""

from __future__ import annotations

from scanner.domain.structure import StructureLabel, SwingStrength

_PREFIX = "STRUCTURE_"


def classification_event_type(
    strength: SwingStrength,
    label: StructureLabel,
) -> str:
    """The event type `_persist_classification` writes."""

    return f"{_PREFIX}{strength.value}_{label.value}"


def read_classification(
    event_type: str,
    *,
    strength: SwingStrength,
) -> StructureLabel | None:
    """The label this event carries, or None if it is not one of `strength`.

    `strength` is keyword-only and has no default on purpose: a caller that
    has not decided which of §3.3's two series it means cannot express the
    question, and 152 of 695 pivots would have answered it differently.

    An unrecognised label yields None rather than raising. It means version
    skew between a stored event and this build, and §7.4 counts what it
    recognises rather than abandoning the score.
    """
    head = f"{_PREFIX}{strength.value}_"

    if not event_type.startswith(head):
        return None

    try:
        return StructureLabel(event_type.removeprefix(head))
    except ValueError:
        return None


def is_classification(event_type: str, *, strength: SwingStrength) -> bool:
    """Whether this event is a §3.3 classification of the given strength."""

    return read_classification(event_type, strength=strength) is not None
