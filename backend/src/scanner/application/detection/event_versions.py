"""Which engine-event generations are *current*, in one place (audit class C).

The zones' reason in `zone_versions.py`, applied to `detection.engine_events`.
A version bump makes the new engine re-derive its window's facts under its own
label while the old generation's rows stay in the table, so for a window after
every deploy one BOS, one MSS or one volume fact exists twice. The readers that
*judge* with them -- confluence, order-block grading, the signal monitor, the
chart, §6.6's suspect-volume count -- read both generations unless they are
pinned to the running versions.

One set rather than a per-type map: every engine's version string is its own
(`s4-...`, `s6-structure-shift-...`, `s5-...`, `s7-participation-...`), so a
row's `algo_version` alone says which generation it belongs to, and no reader
has to know which engine writes a given type -- `STRUCTURE_MSS_INVALIDATED_*`
carries a structure prefix and comes from the shift engine.

Confluence's own rows (SETUP_CANDIDATE_*, SIGNAL_SUPPRESSED_*) are not here: no
reader takes them from the event log, and confluence importing this module is
what keeps it from importing confluence.
"""

from __future__ import annotations

from scanner.application.detection.liquidity_replay import LIQUIDITY_ALGO_VERSION
from scanner.application.detection.participation_replay import PARTICIPATION_ALGO_VERSION
from scanner.application.detection.structure_replay import STRUCTURE_ALGO_VERSION
from scanner.application.detection.structure_shift_replay import STRUCTURE_SHIFT_ALGO_VERSION

CURRENT_EVENT_VERSIONS: frozenset[str] = frozenset(
    {
        STRUCTURE_ALGO_VERSION,
        STRUCTURE_SHIFT_ALGO_VERSION,
        LIQUIDITY_ALGO_VERSION,
        PARTICIPATION_ALGO_VERSION,
    }
)
