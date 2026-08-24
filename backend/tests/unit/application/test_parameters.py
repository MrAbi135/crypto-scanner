"""The registry against Appendix A, and the checksum that identifies it."""

from __future__ import annotations

from scanner.application.parameters import (
    PARAM_SET_VERSION,
    Parameter,
    checksum,
    parameters,
    payload,
)


def test_no_implemented_parameter_departs_from_appendix_a() -> None:
    """The drift alarm, and the only reason the registry earns its keep.

    Appendix A is the doctrine's parameter table; the registry reads the
    constants the code runs on. A value changed in the source without an
    amendment fails here, which is the check that did not exist when
    `P.global.tolerance_atr` sat unimplemented while the table said 0.05.

    Absence is a separate matter and is not drift -- see the test below.
    """
    drifted = [
        (p.name, p.doctrine, p.implemented)
        for p in parameters()
        if p.implemented is not None and not p.matches_doctrine
    ]

    assert not drifted, f"parameters departing from Appendix A: {drifted}"


def test_an_unimplemented_parameter_is_recorded_as_absent_not_defaulted() -> None:
    """Absence is written down, and every absent entry says why.

    §3.4's `idle_candles` was the entry that made this rule matter: the
    registry reported it absent, which is how the missing rule was found and
    then written. There are none left today, and the assertion is on the
    invariant rather than on the list -- an entry that appears here later must
    still explain itself, and a registry that silently defaulted it to the
    doctrine value would have the checksum certify an implementation that is
    not there.
    """
    absent = [p for p in parameters() if p.implemented is None]

    assert all(p.note for p in absent)


def test_every_entry_names_its_section() -> None:
    """A parameter without its clause cannot be re-checked against the spec."""

    assert all(p.section for p in parameters())
    assert all(p.name for p in parameters())


def test_the_checksum_is_stable_across_calls() -> None:
    assert checksum() == checksum()


def test_the_checksum_moves_when_a_value_does() -> None:
    """Serialisation-independent, value-dependent.

    Sorted keys and no whitespace, so the digest tracks the parameter set
    rather than how it happened to be written out. This is what TAD §14's
    boot verification compares, and a digest that ignored a changed value
    would let the engine score under a parameter set nobody recorded.
    """
    import hashlib
    import json

    baseline = payload()

    assert (
        checksum()
        == hashlib.sha256(
            json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )

    moved = json.loads(json.dumps(baseline))
    moved["parameters"][0]["implemented"] = "0.06"

    assert (
        hashlib.sha256(
            json.dumps(moved, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        != checksum()
    )


def test_absence_participates_in_the_checksum() -> None:
    """Implementing a missing rule must change the digest.

    That is not hypothetical: §3.4's idle route into RANGING was recorded
    absent here, and writing it moved this checksum -- which is correct, and
    is why `PARAM_SET_VERSION` moved with it. Appendix A requires every
    parameter change to increment the version and re-validate the golden
    datasets, and a digest blind to absence would let a rule arrive silently.

    Written against a constructed payload rather than the live one, because
    the live set has no absent entries today and a test that filtered zero
    rows would pass while asserting nothing.
    """
    import hashlib
    import json

    live = payload()

    assert (
        hashlib.sha256(
            json.dumps(live, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        == checksum()
    )

    unimplemented = json.loads(json.dumps(live))
    unimplemented["parameters"][0]["implemented"] = None

    assert (
        hashlib.sha256(
            json.dumps(unimplemented, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        != checksum()
    )


def test_the_payload_carries_the_version_t10_records_against() -> None:
    assert payload()["param_set_version"] == PARAM_SET_VERSION
    assert PARAM_SET_VERSION


def test_matches_doctrine_is_false_for_an_absent_parameter() -> None:
    """Absent is not "matches" -- `None` never equals a doctrine string.

    Worth pinning because the drift test filters absences out before asking,
    and a `matches_doctrine` that returned True for them would make that
    filter look unnecessary and invite its removal.
    """
    absent = Parameter("P.x", "1.1", "100", None, note="not implemented")

    assert not absent.matches_doctrine
