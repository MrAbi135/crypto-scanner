"""The boot check over DDD's append-only guards."""

from __future__ import annotations

import pytest

from scanner.application.immutability_verification import (
    GUARDED_TABLES,
    ImmutabilityGuardsMissingError,
    verify_immutability_guards,
)


class FakeInspector:
    def __init__(self, counts: dict[str, int], *, bypasses: bool = True) -> None:
        self.counts = counts
        self.bypasses = bypasses

    async def enabled_guard_counts(self) -> dict[str, int]:
        return self.counts

    async def connection_role_bypasses_grants(self) -> bool:
        return self.bypasses


def fully_guarded() -> dict[str, int]:
    return dict.fromkeys(GUARDED_TABLES, 2)


@pytest.mark.asyncio
async def test_a_fully_guarded_schema_passes() -> None:
    report = await verify_immutability_guards(FakeInspector(fully_guarded()))

    assert report.guarded == GUARDED_TABLES


@pytest.mark.asyncio
async def test_a_missing_guard_refuses_the_boot() -> None:
    """DDD: the signal record "cannot be edited by anyone -- including us".

    A process that starts without the guards publishes into a table that can
    be quietly rewritten, and the entire track record rests on that being
    impossible. Refusing at boot is the only point at which the operator is
    still looking.
    """
    counts = fully_guarded()

    del counts["signal_outcomes"]

    with pytest.raises(ImmutabilityGuardsMissingError, match="signal_outcomes"):
        await verify_immutability_guards(FakeInspector(counts))


@pytest.mark.asyncio
async def test_a_table_guarded_against_update_but_not_truncate_still_fails() -> None:
    """Two triggers per table, and the second one is the easy one to forget.

    `TRUNCATE` is a separate event class in PostgreSQL: a table carrying only
    the UPDATE/DELETE guard can still be emptied in one statement, with the
    guard present and every existence check reporting green.
    """
    counts = fully_guarded() | {"signals": 1}

    with pytest.raises(ImmutabilityGuardsMissingError, match=r"signals \(1/2\)"):
        await verify_immutability_guards(FakeInspector(counts))


@pytest.mark.asyncio
async def test_every_unguarded_table_is_named_at_once() -> None:
    """A schema that lost one trigger usually lost them the same way.

    Reporting the first would have the operator fix and restart three times to
    discover a single cause.
    """
    with pytest.raises(ImmutabilityGuardsMissingError) as raised:
        await verify_immutability_guards(FakeInspector({}))

    for table in GUARDED_TABLES:
        assert table in str(raised.value)


@pytest.mark.asyncio
async def test_an_owner_connection_is_reported_rather_than_refused() -> None:
    """DDD's layer (a) cannot be satisfied from inside the process.

    "No UPDATE grants to the application role" needs a second role and a
    second secret -- a deployment decision. Refusing to boot over it would
    mean refusing to boot at all, so the state is reported and the runbook
    carries the fix.
    """
    report = await verify_immutability_guards(FakeInspector(fully_guarded(), bypasses=True))

    assert report.role_bypasses_grants

    restrained = await verify_immutability_guards(FakeInspector(fully_guarded(), bypasses=False))

    assert not restrained.role_bypasses_grants
