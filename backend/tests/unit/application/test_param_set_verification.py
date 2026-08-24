"""TAD §14's boot check, in its three outcomes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from scanner.application.param_set_verification import (
    ParameterSetMismatchError,
    verify_parameter_set,
)
from scanner.application.parameters import PARAM_SET_VERSION, checksum, payload
from scanner.application.ports.param_sets import ParamSetRecord

NOW = datetime(2026, 8, 24, tzinfo=UTC)


class FakeParamSets:
    def __init__(self, rows: list[ParamSetRecord] | None = None) -> None:
        self.rows = list(rows or [])
        self.registered: list[ParamSetRecord] = []

    async def get(self, engine, algo_version, param_set_version):
        for row in self.rows:
            if (
                row.engine == engine
                and row.algo_version == algo_version
                and row.param_set_version == param_set_version
            ):
                return row

        return None

    async def register(self, record):
        self.registered.append(record)
        self.rows.append(record)


def recorded(*, digest: str) -> ParamSetRecord:
    return ParamSetRecord(
        engine="detection",
        algo_version="s8-test",
        param_set_version=PARAM_SET_VERSION,
        param_payload=json.dumps(payload(), sort_keys=True, separators=(",", ":")),
        checksum=digest,
        sls_reference="Appendix A",
        deployed_at=NOW,
    )


@pytest.mark.asyncio
async def test_a_triple_never_deployed_is_recorded_and_boot_continues() -> None:
    """Registering rather than refusing is what makes the check bind.

    A first deployment has nothing to compare against. Refusing would mean a
    manual seeding step before any new algo version could run, and a step
    nobody performs is a check nobody has -- so the set is recorded here and
    every boot after this one is verified against it.
    """
    repo = FakeParamSets()

    result = await verify_parameter_set(
        repo,
        engine="detection",
        algo_version="s8-test",
        now=NOW,
    )

    assert [r.param_set_version for r in repo.registered] == [PARAM_SET_VERSION]
    assert result.checksum == checksum()


@pytest.mark.asyncio
async def test_a_matching_checksum_registers_nothing() -> None:
    repo = FakeParamSets([recorded(digest=checksum())])

    result = await verify_parameter_set(
        repo,
        engine="detection",
        algo_version="s8-test",
        now=NOW,
    )

    assert repo.registered == []
    assert result.checksum == checksum()


@pytest.mark.asyncio
async def test_a_changed_parameter_under_an_unchanged_version_refuses_to_boot() -> None:
    """The state Appendix A forbids, and the whole reason this exists.

    "Every parameter change increments `param_set_version`." A build whose
    parameters moved while the version stayed put is scoring under a set
    nobody recorded, and months of signals afterwards cannot be attributed to
    the numbers that produced them.

    The error names both digests, because "mismatch" alone tells whoever is
    paged nothing about which side moved.
    """
    repo = FakeParamSets([recorded(digest="0" * 64)])

    with pytest.raises(ParameterSetMismatchError) as caught:
        await verify_parameter_set(
            repo,
            engine="detection",
            algo_version="s8-test",
            now=NOW,
        )

    message = str(caught.value)

    assert PARAM_SET_VERSION in message
    assert "0" * 64 in message
    assert checksum() in message
    assert repo.registered == []


@pytest.mark.asyncio
async def test_the_recorded_payload_is_the_canonical_one() -> None:
    """What T10 stores must be what the checksum was taken over.

    Storing a differently-serialised payload would leave the row unable to
    explain its own digest, which is the one question anyone reading it after
    a mismatch will have.
    """
    repo = FakeParamSets()

    result = await verify_parameter_set(
        repo,
        engine="detection",
        algo_version="s8-test",
        now=NOW,
    )

    assert json.loads(result.param_payload) == payload()
    assert result.param_payload == json.dumps(payload(), sort_keys=True, separators=(",", ":"))


@pytest.mark.asyncio
async def test_two_engines_share_a_parameter_set_without_colliding() -> None:
    """The set is global; the registry is keyed per engine.

    Structure and confluence carry different algo versions and the same
    parameters, so both register under the same `param_set_version`. A key
    without the engine would make the second one look like a mismatch.
    """
    repo = FakeParamSets()

    for algo in ("s4-v7", "s8-v20"):
        await verify_parameter_set(
            repo,
            engine="detection",
            algo_version=algo,
            now=NOW,
        )

    assert [r.algo_version for r in repo.registered] == ["s4-v7", "s8-v20"]
    assert {r.checksum for r in repo.registered} == {checksum()}
