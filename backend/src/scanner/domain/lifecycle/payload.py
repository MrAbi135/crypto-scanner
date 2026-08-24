"""§15.2's mandatory payload, sealed, and §15.3's checks on it (SLS §15).

§12.1 creates a signal with "the complete §15.2 payload", and §15.3 then
decides once, atomically, whether it may be published. Both are here: the
payload's nine rows as a snapshot, the seal that makes it auditable, and the
five checks — each returning *which* one failed, because §12.2 requires a
suppression to carry "a recorded reason (auditable funnel: candidates →
published is a monitored ratio, §14)".

**The payload is a snapshot and the hash is taken over it.** §12.1: "immutable
core: evidence, zones, levels never mutate post-creation (refresh events
append)". A hash over a structure that can still change certifies nothing, so
`seal` serialises canonically — sorted keys, no whitespace — and every value
that reaches it is already a string or a number, never an object whose
`repr` might drift between versions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from scanner.domain.confluence import SignalLevels


class SuppressionReason(str, Enum):
    """Why §15.3 refused, in the vocabulary §12.2 wants recorded."""

    INCOMPLETE_PAYLOAD = "INCOMPLETE_PAYLOAD"
    INCOHERENT_LEVELS = "INCOHERENT_LEVELS"
    STALE_FEEDS = "STALE_FEEDS"
    BELOW_MIN_RR = "BELOW_MIN_RR"
    DUPLICATE_KEY = "DUPLICATE_KEY"


@dataclass(frozen=True, slots=True)
class SignalPayload:
    """§15.2's nine rows, as a snapshot taken at creation.

    Every field is required. §15.3(1) is "payload complete — every field above
    non-null", and a dataclass with defaults everywhere would let an
    incomplete payload construct itself and then fail a check that exists to
    catch exactly that.
    """

    symbol: str
    timeframe: str
    direction: str

    # §15.2 "Evidence": the complete event-id chain.
    evidence_ids: tuple[str, ...]

    # §15.2 "Confidence": FinalConfidence + grade + the F1-F6 breakdown.
    confidence: Decimal
    grade: str
    factors: dict[str, str]

    # §15.2 "Reason": archetype + a deterministic human-readable string. The
    # AI thesis is explicitly "when available" and is not part of the seal --
    # it is written later against a different version pair (§11), and hashing
    # it here would make the seal depend on whether the model had run.
    archetype: str
    reason: str

    # §15.2 "Risk": invalidation distance, R-multiple, market-condition tags.
    invalidation_distance_atr: Decimal
    invalidation_distance_pct: Decimal
    r_multiple: Decimal
    condition_tags: tuple[str, ...]

    # §15.2 "Invalidation", "Entry Zone", "Target Zone" -- all three from §15.2's
    # priced rows, computed in `domain.confluence.levels`.
    levels: SignalLevels

    # §15.2 "Supported Timeframes": signal TF plus the HTF bias chain as it
    # stood at creation. A snapshot, because the chain moves and the record
    # must say what was true when the call was made.
    htf_chain: dict[str, str]

    # §15.2 "Versions".
    algo_version: str
    param_set_version: str

    def as_dict(self) -> dict[str, object]:
        """The canonical form: strings and numbers, no domain objects.

        What gets hashed and what gets stored are the same structure, so a row
        can always explain its own digest.
        """
        entry = self.levels.entry
        primary = self.levels.primary_target
        secondary = self.levels.secondary_target

        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "evidence_ids": list(self.evidence_ids),
            "confidence": str(self.confidence),
            "grade": self.grade,
            "factors": dict(self.factors),
            "archetype": self.archetype,
            "reason": self.reason,
            "risk": {
                "invalidation_distance_atr": str(self.invalidation_distance_atr),
                "invalidation_distance_pct": str(self.invalidation_distance_pct),
                "r_multiple": str(self.r_multiple),
                "condition_tags": list(self.condition_tags),
            },
            "invalidation": {
                "price": str(self.levels.invalidation.price),
                "rule": self.levels.invalidation.rule,
            },
            "entry_zone": {
                "zone_id": entry.zone_id,
                "proximal": str(entry.proximal),
                "distal": str(entry.distal),
                "refined_proximal": (
                    str(entry.refined_proximal) if entry.refined_proximal is not None else None
                ),
                "refined_distal": (
                    str(entry.refined_distal) if entry.refined_distal is not None else None
                ),
            },
            "targets": {
                "primary": _target_dict(primary),
                "secondary": _target_dict(secondary) if secondary is not None else None,
            },
            "htf_chain": dict(self.htf_chain),
            "versions": {
                "algo_version": self.algo_version,
                "param_set_version": self.param_set_version,
            },
        }

    def seal(self) -> str:
        """§15.3(5): "payload hashed; the hash accompanies the signal for audit"."""

        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def dedup_key(self, *, band_dp: int = 2) -> str:
        """§10.3: `(symbol, TF, direction, archetype, zone_band_rounded)`.

        The band is rounded because two candidates on the same zone can differ
        in the last decimal of a refined edge and are the same opportunity.
        §10.3's whole purpose is that such a pair "is merged as a refresh
        event on the existing signal — never a second alert".
        """
        entry = self.levels.entry

        band = f"{round(entry.proximal, band_dp)}:{round(entry.distal, band_dp)}"

        return "|".join((self.symbol, self.timeframe, self.direction, self.archetype, band))


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    """§12.2's single atomic verdict, and why."""

    published: bool
    reasons: tuple[SuppressionReason, ...] = field(default_factory=tuple)


def publication_checks(
    payload: SignalPayload,
    *,
    feeds_fresh: bool,
    dedup_clear: bool,
) -> PublicationDecision:
    """§15.3, evaluated exactly once.

    `feeds_fresh` and `dedup_clear` are facts the caller establishes, not
    questions answered here: the first needs the ingest layer's freshness view
    and the second needs the signals table. Passing them in keeps this a pure
    function of the payload plus two stated facts, which is what makes the
    verdict reproducible from a stored row months later.

    **Every failing check is reported, not just the first.** §12.2 records a
    suppression reason for the funnel in §14, and "it failed on freshness"
    when it also had no room to travel would send someone to fix the wrong
    thing.
    """
    reasons: list[SuppressionReason] = []

    if not _complete(payload):
        reasons.append(SuppressionReason.INCOMPLETE_PAYLOAD)

    if not payload.levels.coherent:
        reasons.append(SuppressionReason.INCOHERENT_LEVELS)

    if not feeds_fresh:
        reasons.append(SuppressionReason.STALE_FEEDS)

    if not payload.levels.meets_rr:
        reasons.append(SuppressionReason.BELOW_MIN_RR)

    if not dedup_clear:
        reasons.append(SuppressionReason.DUPLICATE_KEY)

    return PublicationDecision(published=not reasons, reasons=tuple(reasons))


def _complete(payload: SignalPayload) -> bool:
    """§15.3(1)'s "every field above non-null".

    The dataclass already refuses a missing field, so what is left to check is
    emptiness: an evidence chain of nothing, a blank grade, a reason string
    nobody wrote. Those construct fine and mean the same as absent.
    """
    return bool(
        payload.evidence_ids
        and payload.grade
        and payload.archetype
        and payload.reason
        and payload.factors
        and payload.htf_chain
        and payload.algo_version
        and payload.param_set_version
    )


def _target_dict(target: object) -> dict[str, object]:
    from scanner.domain.confluence import TargetBand

    assert isinstance(target, TargetBand)

    return {
        "low": str(target.low),
        "high": str(target.high),
        "pool_id": target.pool_id,
        "strength": str(target.strength) if target.strength is not None else None,
    }


__all__ = [
    "PublicationDecision",
    "SignalPayload",
    "SuppressionReason",
    "publication_checks",
]
