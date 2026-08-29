"""§15.2's sealed payload and §15.3's publication checks."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

from scanner.domain.confluence import (
    ZONE_DISTAL_EDGE,
    SignalLevels,
    TargetBand,
    entry_zone,
)
from scanner.domain.confluence.levels import Invalidation
from scanner.domain.lifecycle import (
    SignalPayload,
    SuppressionReason,
    publication_checks,
)


def levels(
    *,
    direction: str = "UP",
    invalidation_price: str = "98",
    target_low: str = "112",
) -> SignalLevels:
    return SignalLevels(
        direction=direction,
        entry=entry_zone(
            zone_id="z1",
            direction=direction,
            band_low=Decimal(100),
            band_high=Decimal(104),
        ),
        invalidation=Invalidation(Decimal(invalidation_price), ZONE_DISTAL_EDGE),
        primary_target=TargetBand(
            low=Decimal(target_low),
            high=Decimal(114),
            pool_id="p1",
            strength=Decimal(60),
        ),
    )


def payload(**overrides) -> SignalPayload:
    base = {
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "direction": "UP",
        "evidence_ids": ("ev-1", "ev-2"),
        "confidence": Decimal(82),
        "grade": "A",
        "factors": {"F1": "70"},
        "archetype": "A4",
        "reason": "Displacement FVG, first touch, HTF aligned.",
        "invalidation_distance_atr": Decimal("1.2"),
        "invalidation_distance_pct": Decimal("0.9"),
        "r_multiple": Decimal("2.5"),
        "condition_tags": ("exhaustion_watch",),
        "levels": levels(),
        "htf_chain": {"H4": "BULLISH"},
        "algo_version": "s8-confluence-v20",
        "param_set_version": "2026.08.24.2",
    }

    return SignalPayload(**{**base, **overrides})


def test_the_seal_is_stable_across_equal_payloads() -> None:
    """A hash that moved between two identical payloads would certify nothing.

    Canonical serialisation is what buys this: sorted keys, no whitespace, and
    every value already a string or a number rather than an object whose
    `repr` could drift between versions.
    """
    assert payload().seal() == payload().seal()


def test_the_seal_moves_when_any_priced_row_moves() -> None:
    """§12.1: "levels never mutate post-creation".

    The seal is what makes that checkable after the fact, so a changed
    invalidation must produce a different digest -- otherwise a tampered row
    still verifies.
    """
    moved = payload(levels=levels(invalidation_price="97"))

    assert moved.seal() != payload().seal()


def test_the_stored_form_is_the_hashed_form() -> None:
    """A row must be able to explain its own digest.

    Storing a differently-shaped payload from the one that was hashed leaves
    the only question anyone asks after a mismatch unanswerable.
    """
    p = payload()

    import hashlib

    raw = json.dumps(p.as_dict(), sort_keys=True, separators=(",", ":"))

    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == p.seal()


def test_the_dedup_key_is_10_3s_five_parts() -> None:
    """§10.3: `(symbol, TF, direction, archetype, zone_band_rounded)`.

    The band is rounded to significant figures and printed canonically, so
    `104.004` and `104.0040` both render `104` at four figures. A key that
    carried whatever scale the zone happened to be stored at would not match
    itself across two passes.
    """
    assert payload().dedup_key() == "BTCUSDT|H1|UP|A4|104:100"


def test_two_candidates_on_one_zone_share_a_dedup_key() -> None:
    """§10.3 merges them as a refresh -- "never a second alert".

    The band is rounded because a refined edge can differ in its last decimal
    between two passes on the same zone, and a key that distinguished them
    would alert twice for one opportunity.
    """
    a = payload()
    b = payload(
        levels=SignalLevels(
            direction="UP",
            entry=entry_zone(
                zone_id="z1",
                direction="UP",
                band_low=Decimal("100.001"),
                band_high=Decimal("104.004"),
            ),
            invalidation=Invalidation(Decimal(98), ZONE_DISTAL_EDGE),
            primary_target=TargetBand(low=Decimal(112), high=Decimal(114)),
        )
    )

    assert a.dedup_key() == b.dedup_key()


def test_a_complete_payload_with_fresh_feeds_publishes() -> None:
    decision = publication_checks(payload(), feeds_fresh=True, dedup_clear=True)

    assert decision.published
    assert decision.reasons == ()


def test_every_failing_check_is_reported_not_just_the_first() -> None:
    """§12.2 records the reason for §14's funnel.

    "It failed on freshness" when it also had no room to travel would send
    someone to fix the wrong thing -- and the funnel would attribute the
    suppression to the wrong cause for as long as the row lives.
    """
    tight = payload(levels=levels(target_low="103"))

    decision = publication_checks(tight, feeds_fresh=False, dedup_clear=False)

    assert not decision.published
    assert set(decision.reasons) == {
        SuppressionReason.STALE_FEEDS,
        SuppressionReason.BELOW_MIN_RR,
        SuppressionReason.DUPLICATE_KEY,
    }


def test_an_empty_evidence_chain_is_incomplete() -> None:
    """§15.3(1)'s "non-null" has to mean "not empty" too.

    The dataclass already refuses a missing field, so what is left is a value
    that constructs fine and means the same as absent: no evidence, a blank
    grade, a reason nobody wrote.
    """
    for field_name, empty in (
        ("evidence_ids", ()),
        ("grade", ""),
        ("reason", ""),
        ("factors", {}),
        ("htf_chain", {}),
    ):
        decision = publication_checks(
            replace(payload(), **{field_name: empty}),
            feeds_fresh=True,
            dedup_clear=True,
        )

        assert SuppressionReason.INCOMPLETE_PAYLOAD in decision.reasons, field_name


def test_incoherent_levels_are_caught_separately_from_the_rr_floor() -> None:
    """§15.3(1) and §15.3(3) are different failures.

    A target on the wrong side of the entry is a broken payload; a target too
    close is a real setup with no room. Collapsing them would hide a level bug
    inside a routine suppression.
    """
    backwards = payload(levels=levels(target_low="90"))

    decision = publication_checks(backwards, feeds_fresh=True, dedup_clear=True)

    assert SuppressionReason.INCOHERENT_LEVELS in decision.reasons


def test_the_dedup_key_survives_a_sub_cent_symbol() -> None:
    """The finding that forced significant-figure rounding.

    At two absolute decimals every sub-cent band rendered "0.00:0.00", so for
    a TTL window every distinct setup on one (symbol, TF, direction,
    archetype) was swallowed as a duplicate -- or merged as a refresh of a
    signal about a different zone. The universe's ~733 symbols are mostly
    sub-dollar; this was a launch-blocking silence, found before launch.
    """
    shib_a = payload(
        symbol="SHIBUSDT",
        levels=SignalLevels(
            direction="UP",
            entry=entry_zone(
                zone_id="z1",
                direction="UP",
                band_low=Decimal("0.00002810"),
                band_high=Decimal("0.00002850"),
            ),
            invalidation=Invalidation(Decimal("0.00002700"), ZONE_DISTAL_EDGE),
            primary_target=TargetBand(low=Decimal("0.00003100"), high=Decimal("0.00003100")),
        ),
    )
    shib_b = payload(
        symbol="SHIBUSDT",
        levels=SignalLevels(
            direction="UP",
            entry=entry_zone(
                zone_id="z2",
                direction="UP",
                band_low=Decimal("0.00003400"),
                band_high=Decimal("0.00003460"),
            ),
            invalidation=Invalidation(Decimal("0.00003300"), ZONE_DISTAL_EDGE),
            primary_target=TargetBand(low=Decimal("0.00003800"), high=Decimal("0.00003800")),
        ),
    )

    assert shib_a.dedup_key() != shib_b.dedup_key()
    assert "0.00:0.00" not in shib_a.dedup_key()


def test_two_spellings_of_one_price_share_a_key() -> None:
    """0.00002810 and 0.000028100 are one number; a dedup key that
    distinguished the spellings would alert twice for one opportunity."""

    a = payload(
        levels=SignalLevels(
            direction="UP",
            entry=entry_zone(
                zone_id="z1",
                direction="UP",
                band_low=Decimal("0.000028100"),
                band_high=Decimal("0.00002850"),
            ),
            invalidation=Invalidation(Decimal("0.00002700"), ZONE_DISTAL_EDGE),
            primary_target=TargetBand(low=Decimal("0.00003100"), high=Decimal("0.00003100")),
        )
    )
    b = payload(
        levels=SignalLevels(
            direction="UP",
            entry=entry_zone(
                zone_id="z1",
                direction="UP",
                band_low=Decimal("0.00002810"),
                band_high=Decimal("0.000028500"),
            ),
            invalidation=Invalidation(Decimal("0.00002700"), ZONE_DISTAL_EDGE),
            primary_target=TargetBand(low=Decimal("0.00003100"), high=Decimal("0.00003100")),
        )
    )

    assert a.dedup_key() == b.dedup_key()


def test_significant_rounding_prints_one_spelling_per_value() -> None:
    """The key's atoms: plain notation always, zero stable, scale-free."""

    from scanner.domain.lifecycle.payload import _significant

    # normalize() alone would render these 1E+2 and 6E+4.
    assert _significant(Decimal("100.00"), 4) == "100"
    assert _significant(Decimal("60000"), 4) == "60000"
    assert _significant(Decimal("0.000028104"), 4) == "0.0000281"
    # Zero has no significant digits; every stored spelling of it must key
    # identically.
    assert _significant(Decimal("0"), 4) == "0"
    assert _significant(Decimal("0.00"), 4) == "0"
