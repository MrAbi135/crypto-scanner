"""Stage 1 hard gates (SLS §8.2).

Binary, and no score can compensate. §8.2 is unambiguous: "failing any gate =>
no setup exists; nothing is scored, nothing is logged as a signal".

The battery takes evidence as flags rather than reaching into the engines that
produce them. Those engines are siblings under the acyclicity contract, and
composition belongs to the application layer -- the same shape as MssEvidence
and the stop-hunt composite.

Every gate result is returned, not just the first failure. §8.2 says gate
failures are "counted in diagnostics", and a battery that short-circuits can
only ever report one reason -- which makes the diagnostics useless for finding
out which gate is actually rejecting a market.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Gate(str, Enum):
    DATA = "G1"
    STRUCTURE = "G2"
    PD_CONTEXT = "G3"
    ZONE = "G4"
    NO_CONTRARY_FACT = "G5"
    VOLUME_INTEGRITY = "G6"


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """What the application layer must establish before anything is scored."""

    # G1 -- feeds fresh, not DEGRADED, warm-up complete, tier permits the TF.
    data_ready: bool

    # G2 -- trend state on this TF compatible with the direction.
    structure_compatible: bool

    # G3 -- §5.7 directional gate satisfied.
    pd_context_ok: bool

    # G3 qualifier: under PD_SUSPENDED only continuation archetypes are
    # eligible, so the gate can pass while narrowing what may follow.
    pd_suspended: bool = False

    # G4 -- at least one ACTIVE/FRESH zone of this polarity containing or
    # within 0.5 x ATR of price.
    zone_present: bool = True

    # G5 -- no unexpired opposing sweep-reclaim, failed break, or opposing
    # displacement in the last three candles.
    contrary_fact_present: bool = False

    # G6 -- symbol not wash_risk-capped below the archetype's minimum.
    volume_integrity_ok: bool = True


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    failed: tuple[Gate, ...]
    continuation_only: bool


def evaluate_gates(evidence: GateEvidence) -> GateResult:
    """Run all six. Returns every failure, not the first."""
    failed: list[Gate] = []

    if not evidence.data_ready:
        failed.append(Gate.DATA)

    if not evidence.structure_compatible:
        failed.append(Gate.STRUCTURE)

    if not evidence.pd_context_ok:
        failed.append(Gate.PD_CONTEXT)

    if not evidence.zone_present:
        failed.append(Gate.ZONE)

    if evidence.contrary_fact_present:
        failed.append(Gate.NO_CONTRARY_FACT)

    if not evidence.volume_integrity_ok:
        failed.append(Gate.VOLUME_INTEGRITY)

    return GateResult(
        passed=not failed,
        failed=tuple(failed),
        # Not a gate failure: PD_SUSPENDED still admits a setup, it just
        # restricts which archetypes may claim it (§8.2 G3).
        continuation_only=evidence.pd_suspended,
    )
