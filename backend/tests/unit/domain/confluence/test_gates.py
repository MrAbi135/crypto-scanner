"""The hard-gate battery against SLS §8.2."""

from __future__ import annotations

import pytest

from scanner.domain.confluence import Gate, GateEvidence, evaluate_gates


def evidence(**overrides):
    base = {
        "data_ready": True,
        "structure_compatible": True,
        "pd_context_ok": True,
    }

    return GateEvidence(**{**base, **overrides})


def test_a_clean_candidate_passes_every_gate() -> None:
    result = evaluate_gates(evidence())

    assert result.passed is True
    assert result.failed == ()


@pytest.mark.parametrize(
    ("override", "gate"),
    [
        ({"data_ready": False}, Gate.DATA),
        ({"structure_compatible": False}, Gate.STRUCTURE),
        ({"pd_context_ok": False}, Gate.PD_CONTEXT),
        ({"zone_present": False}, Gate.ZONE),
        ({"contrary_fact_present": True}, Gate.NO_CONTRARY_FACT),
        ({"volume_integrity_ok": False}, Gate.VOLUME_INTEGRITY),
    ],
)
def test_any_single_failure_rejects_the_candidate(override: dict, gate: Gate) -> None:
    """§8.2: "no score can compensate". Each gate is independently fatal."""
    result = evaluate_gates(evidence(**override))

    assert result.passed is False
    assert result.failed == (gate,)


def test_every_failing_gate_is_reported_not_just_the_first() -> None:
    """§8.2 counts gate failures in diagnostics.

    A battery that short-circuits can only ever name one reason, so the
    diagnostics could never answer which gate is actually rejecting a market --
    the only question they exist to answer.
    """
    result = evaluate_gates(
        evidence(
            data_ready=False,
            zone_present=False,
            volume_integrity_ok=False,
        )
    )

    assert result.failed == (Gate.DATA, Gate.ZONE, Gate.VOLUME_INTEGRITY)


def test_suspended_pd_narrows_the_archetypes_without_failing_the_gate() -> None:
    """§8.2 G3: PD_SUSPENDED means "only continuation archetypes eligible".

    That is a restriction on what may follow, not a rejection. Treating it as a
    gate failure would silently drop every continuation setup during a
    suspension -- exactly the setups the clause is written to preserve.
    """
    result = evaluate_gates(evidence(pd_suspended=True))

    assert result.passed is True
    assert result.continuation_only is True


def test_an_ordinary_candidate_is_not_continuation_only() -> None:
    assert evaluate_gates(evidence()).continuation_only is False
