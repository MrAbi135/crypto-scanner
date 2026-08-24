"""The running parameter set and its checksum (SLS Appendix A, TAD §14).

Appendix A lists the doctrine's parameters and their defaults. This is the
other half of that table: what the code actually runs on, gathered in one
place so the two can be compared by a machine instead of by eye.

**It imports; it does not re-declare.** Every value below comes from the
module that owns it. A registry that restated the numbers would be one more
copy to drift — and drift is the thing it exists to catch. That is not
hypothetical here: `TOLERANCE_ATR` had three declarations across the codebase
this week -- writing this registry is what turned up the fourth, and all
four now resolve to one constant.

**Absence is recorded, not defaulted.** A parameter the code does not
implement carries `implemented=None` and says why. §3.4's `idle_candles` is
the current example: the doctrine's second route into RANGING does not exist
in the source, and writing 100 here would assert that it does.

It lives in `application` rather than `domain` because it reads from every
engine, and `domain.common` is the leaf they all depend on -- import-linter
refuses that edge, correctly, even when the imports are deferred inside a
function. Assembling facts from every engine is a composition concern, and
TAD §14 puts parameter loading in the configuration layer above domain.

TAD §14: parameter sets are "loaded as versioned data via repository
(checksummed against `param_set_version`, never from env)", and a mismatch
means "the engine refuses to score". `checksum()` is what that verification
compares.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from scanner.domain.common.atr import ATR_PERIOD, DERIVED_DP, TOLERANCE_ATR
from scanner.domain.common.warmup import (
    DETECTION_MIN_CANDLES,
    VOLUME_MOMENTUM_MIN_CANDLES,
)
from scanner.domain.confluence.archetypes import FLOORS
from scanner.domain.confluence.confidence import MAX_PENALTY, MAX_SYNERGY
from scanner.domain.confluence.weights import (
    GRADE_A_FLOOR,
    GRADE_B_FLOOR,
    GRADE_S_FLOOR,
    WEIGHTS,
)
from scanner.domain.ict import MAX_ZONES
from scanner.domain.liquidity import (
    MAX_POOLS,
    POOL_MAX_AGE,
    SWEEP_SETUP_EXPIRY_CANDLES,
)
from scanner.domain.liquidity.clusters import EQ_MAX_GAP, EQ_MIN_GAP
from scanner.domain.momentum.legs import IMPULSE_MIN_ATR, MICRO_MAX_ATR
from scanner.domain.momentum.phases import (
    ACCEL_LOOKBACK,
    COMPRESSION_ENVELOPE_ATR,
    COMPRESSION_RANGE_ATR,
    COMPRESSION_WINDOW,
    EXPANSION_MEAN_RANGE_ATR,
)
from scanner.domain.momentum.score import WARMUP_CANDLES
from scanner.domain.ranking import TTL_CANDLES
from scanner.domain.structure import FAILED_BREAK_CANDLES, swing_window
from scanner.domain.structure.model import SwingStrength
from scanner.domain.volume import SPIKE_FLOOR_QUOTE

# Bumped by hand whenever any value below changes. SLS Appendix A: "Every
# parameter change increments `param_set_version` and requires golden-dataset
# re-validation." The checksum is what catches forgetting to.
PARAM_SET_VERSION = "2026.08.24.1"


@dataclass(frozen=True, slots=True)
class Parameter:
    """One Appendix A row, and what the code does about it."""

    name: str
    section: str
    doctrine: str
    implemented: str | None
    note: str | None = None

    @property
    def matches_doctrine(self) -> bool:
        return self.implemented == self.doctrine


def _p(name: str, section: str, doctrine: object, implemented: object) -> Parameter:
    return Parameter(name, section, str(doctrine), str(implemented))


def _absent(name: str, section: str, doctrine: object, note: str) -> Parameter:
    return Parameter(name, section, str(doctrine), None, note)


def _mapped() -> tuple[Parameter, ...]:
    return (
        _p("P.global.tolerance_atr", "0.4", "0.05", TOLERANCE_ATR),
        _p("P.global.derived_dp", "0.4", 4, DERIVED_DP),
        _p("ATR period", "2", 14, ATR_PERIOD),
        _p("warmup.structure", "1.9", 300, DETECTION_MIN_CANDLES),
        _p("warmup.volume_momentum", "1.9", 100, VOLUME_MOMENTUM_MIN_CANDLES),
        _p(
            "P.structure.k_internal",
            "3.1",
            2,
            swing_window(SwingStrength.INTERNAL),
        ),
        _p(
            "P.structure.k_external",
            "3.1",
            5,
            swing_window(SwingStrength.EXTERNAL),
        ),
        _p("P.structure.failed_break_candles", "3.5", 3, FAILED_BREAK_CANDLES),
        _absent(
            "P.structure.idle_candles",
            "3.4",
            100,
            "§3.4's second route into RANGING -- closed inside the range without an "
            "external BOS for 100 candles -- is not implemented. No constant and no "
            "check exists; the trend state machine only moves on CHoCH and MSS.",
        ),
        _p("P.liquidity.pool_max_age", "4.2", 500, POOL_MAX_AGE),
        _p("P.liquidity.eq_min_gap", "4.3", 3, EQ_MIN_GAP),
        _p("P.liquidity.eq_max_gap", "4.3", 100, EQ_MAX_GAP),
        _p("P.liquidity.max_pools", "4.2", 40, MAX_POOLS),
        _p("P.liquidity.sweep_expiry", "4.6", 15, SWEEP_SETUP_EXPIRY_CANDLES),
        _p("P.ict.max_zones", "5.1", 60, MAX_ZONES),
        _p("momentum.warmup", "7.1", 30, WARMUP_CANDLES),
        _p("accel.lookback", "7.2", 3, ACCEL_LOOKBACK),
        _p("expansion.mean_range_atr", "7.3", "1.4", EXPANSION_MEAN_RANGE_ATR),
        _p("compression.window", "7.3", 7, COMPRESSION_WINDOW),
        _p("compression.range_atr", "7.3", "0.7", COMPRESSION_RANGE_ATR),
        _p("compression.envelope_atr", "7.3", 2, COMPRESSION_ENVELOPE_ATR),
        _p("legs.impulse_min_atr", "7.5", "1.5", IMPULSE_MIN_ATR),
        _p("legs.micro_max_atr", "7.5", "0.75", MICRO_MAX_ATR),
        _p("P.volume.spike_floor", "6.2", 250000, SPIKE_FLOOR_QUOTE),
        _p("synergy cap", "8.5", 15, MAX_SYNERGY),
        _p("conflict cap", "8.5", 20, MAX_PENALTY),
        _p("grade.S", "9.4", 90, GRADE_S_FLOOR),
        _p("grade.A", "9.4", 80, GRADE_A_FLOOR),
        _p("grade.B", "9.4", 70, GRADE_B_FLOOR),
        _p(
            "archetype floors A1-A5",
            "8.6",
            "75/72/70/70/74",
            "/".join(str(FLOORS[a]) for a in sorted(FLOORS, key=lambda x: x.value)),
        ),
        _p(
            "P.rank.weights F1..F6",
            "9.1",
            "0.20/0.15/0.20/0.15/0.15/0.15",
            "/".join(str(WEIGHTS[f]) for f in sorted(WEIGHTS, key=lambda x: x.value)),
        ),
        _p(
            "P.lifecycle.ttl",
            "12.5",
            "M5:24/M15:24/H1:24/H4:18/D1:15",
            "/".join(
                f"{tf.value}:{n}"
                for tf, n in sorted(TTL_CANDLES.items(), key=lambda kv: kv[0].minutes)
            ),
        ),
    )


def parameters() -> tuple[Parameter, ...]:
    """The mapped parameter set, in a stable order."""

    return _mapped()


def payload() -> dict[str, object]:
    """What T10 stores, and what `checksum` is computed over."""

    return {
        "param_set_version": PARAM_SET_VERSION,
        "parameters": [
            {
                "name": p.name,
                "section": p.section,
                "doctrine": p.doctrine,
                "implemented": p.implemented,
            }
            for p in parameters()
        ],
    }


def checksum() -> str:
    """sha256 over the canonical payload.

    Sorted keys and no whitespace, so the digest depends on the values rather
    than on how they were serialised. Absence participates: implementing
    §3.4's idle rule changes this, which is correct -- it is a change to what
    the engine does, and Appendix A says every such change increments the
    version and re-validates the golden datasets.
    """
    raw = json.dumps(payload(), sort_keys=True, separators=(",", ":"))

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
