"""§15.3(5)'s seals, recomputed."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.application.ports.signals import SignalRecord
from scanner.application.signal_audit import reseal, verify_seals
from scanner.shared import Timeframe

T0 = datetime(2026, 8, 24, tzinfo=UTC)


def signal(signal_id: str, payload: str) -> SignalRecord:
    return SignalRecord(
        signal_id=signal_id,
        setup_id=signal_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        direction="UP",
        archetype="A4",
        grade="A",
        final_confidence=Decimal(82),
        entry_proximal=Decimal(104),
        entry_distal=Decimal(100),
        invalidation_level=Decimal(98),
        target_bands="{}",
        published_at=T0,
        ttl_candles=24,
        algo_version="s8-test",
        param_set_version="2026.08.24.2",
        payload=payload,
        payload_hash=reseal(payload),
        dedup_key="BTCUSDT|H1|UP|A4|104.00:100.00",
    )


class FakeScanner:
    def __init__(self, rows: list[SignalRecord]) -> None:
        self.rows = rows
        self.batches: list[int] = []

    async def scan(self, *, batch: int = 500) -> list[SignalRecord]:
        self.batches.append(batch)

        return self.rows


@pytest.mark.asyncio
async def test_an_untouched_record_verifies() -> None:
    report = await verify_seals(FakeScanner([signal("sig-1", '{"symbol":"BTCUSDT"}')]))

    assert report.checked == 1
    assert report.intact


@pytest.mark.asyncio
async def test_a_payload_edited_without_resealing_is_caught() -> None:
    """The failure this exists for.

    Migration 018's triggers stop the rewrite from inside the database. This
    catches what gets past them -- a bad restore, a corrupted page, a
    hand-edit by someone who did not know the seal was there.
    """
    row = signal("sig-1", '{"symbol":"BTCUSDT"}')
    tampered = replace(row, payload='{"symbol":"ETHUSDT"}')

    report = await verify_seals(FakeScanner([tampered]))

    assert not report.intact
    assert report.failures[0].signal_id == "sig-1"
    assert report.failures[0].reason == "hash mismatch"
    assert report.failures[0].recorded != report.failures[0].recomputed


@pytest.mark.asyncio
async def test_a_coordinated_rewrite_is_not_caught_and_the_docstring_says_so() -> None:
    """A per-row hash cannot detect a payload and seal rewritten together.

    Pinned rather than left implicit: someone will eventually read
    "verify-hashes: failures=0" as proof the record was never touched, and it
    is not that. What makes the rewrite hard is the trigger guard, not this.
    """
    rewritten = signal("sig-1", '{"symbol":"ETHUSDT"}')

    assert (await verify_seals(FakeScanner([rewritten]))).intact


@pytest.mark.asyncio
async def test_a_sealed_payload_that_no_longer_parses_is_a_separate_failure() -> None:
    """A truncated write hashes cleanly to whatever survived.

    Reported apart from a mismatch because it is the worse state, not a
    lesser one: the seal agrees and the evidence is still gone.
    """
    row = signal("sig-1", '{"symbol":"BTC')

    report = await verify_seals(FakeScanner([row]))

    assert not report.intact
    assert "does not parse" in report.failures[0].reason


@pytest.mark.asyncio
async def test_every_failure_is_reported_not_only_the_first() -> None:
    rows = [
        replace(signal("sig-1", "{}"), payload='{"a":1}'),
        signal("sig-2", '{"ok":true}'),
        replace(signal("sig-3", "{}"), payload='{"b":2}'),
    ]

    report = await verify_seals(FakeScanner(rows))

    assert report.checked == 3
    assert [f.signal_id for f in report.failures] == ["sig-1", "sig-3"]


@pytest.mark.asyncio
async def test_an_empty_table_verifies_nothing_and_says_zero() -> None:
    """`checked=0 failures=0` is a clean line about nothing.

    The count travels with the verdict so a caller can tell the two apart --
    the CLI prints an explicit sentence for it.
    """
    report = await verify_seals(FakeScanner([]))

    assert (report.checked, report.intact) == (0, True)


def test_the_seal_is_over_the_stored_string_not_a_reconstruction() -> None:
    """Re-parsing and re-dumping would verify the round-trip, not the bytes.

    It would also start failing on untouched rows the day a field is added to
    the payload, which is the kind of alarm that gets switched off.
    """
    stored = '{"b":2,"a":1}'

    # A canonical re-dump would sort these keys and produce a different hash.
    assert reseal(stored) != reseal(json.dumps(json.loads(stored), sort_keys=True))
