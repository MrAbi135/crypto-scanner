"""Which algo version is *current* for each zone type, in one place.

Exists because of what the s6-v2 -> s6-v3 bump did to the live table on
2026-08-29: zone ids carry their version, so every FVG re-derived as a new
v3 row while its v2 twin stayed live beside it -- the same physical gap,
twice, both inside `list_live`'s answer and therefore both inside §8's
scoring for as long as the old rows took to age out (~33 days on H4). Every
future bump repeats this unless the reads that *judge* zones are pinned to
the versions that are current.

The reads that *retire* zones must stay unpinned, and that is the whole
design: each replay's lifecycle iterates `list_live` without a version
filter, so superseded rows keep aging into their terminal states and leave
the table honestly. Filter those reads too and the old rows freeze live
forever, invisibly.

So: scoring and display pass `CURRENT_ZONE_VERSIONS`; lifecycles pass
nothing. A zone type missing from this map would be silently invisible to
scoring, which is why `assert_covers_all_zone_types` exists and is called
from the confluence module at import time -- a new zone type added without
deciding its version entry refuses to boot rather than quietly vanishing.
"""

from __future__ import annotations

from scanner.application.detection.ict_ob_replay import ICT_OB_ALGO_VERSION
from scanner.application.detection.ict_ote_replay import ICT_OTE_ALGO_VERSION
from scanner.application.detection.ict_replay import ICT_ALGO_VERSION

CURRENT_ZONE_VERSIONS: dict[str, str] = {
    "FVG": ICT_ALGO_VERSION,
    "IFVG": ICT_ALGO_VERSION,
    "BPR": ICT_ALGO_VERSION,
    "OB": ICT_OB_ALGO_VERSION,
    "BREAKER": ICT_OB_ALGO_VERSION,
    "MITIGATION": ICT_OB_ALGO_VERSION,
    "OTE": ICT_OTE_ALGO_VERSION,
}


def assert_covers_all_zone_types(zone_types: frozenset[str]) -> None:
    """Refuse to run with a zone type no version claims.

    Called with the set of types the zone writers can produce. A type left
    out of the map is not "unfiltered" -- with a filter in force it is
    *excluded*, which reads as a market without those zones.
    """
    missing = zone_types - CURRENT_ZONE_VERSIONS.keys()

    if missing:
        raise AssertionError(
            f"zone types with no current-version entry: {sorted(missing)} -- "
            "add them to CURRENT_ZONE_VERSIONS or they are invisible to scoring"
        )
