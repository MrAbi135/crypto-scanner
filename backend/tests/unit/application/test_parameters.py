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
    """§3.4's idle-RANGING rule is the current example, and it is real.

    The doctrine gives RANGING a second route: price closed inside the range
    without an external BOS for `P.structure.idle_candles = 100` candles. No
    constant and no check exists -- the trend state machine only moves on
    CHoCH and MSS.

    Writing 100 into the registry would assert an implementation that is not
    there, and the checksum would then certify it. Every absent entry must
    carry a note saying why, so nobody has to go looking to find out whether
    the gap is real or an oversight in the registry.
    """
    absent = [p for p in parameters() if p.implemented is None]

    assert [p.name for p in absent] == ["P.structure.idle_candles"]
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

    §3.4's idle route arriving is a change to what the engine does, and
    Appendix A says every parameter change increments the version and
    re-validates the golden datasets. A checksum that skipped absent entries
    would let that land silently.
    """
    import hashlib
    import json

    with_absent = payload()

    without = json.loads(json.dumps(with_absent))
    without["parameters"] = [p for p in without["parameters"] if p["implemented"] is not None]

    assert (
        hashlib.sha256(
            json.dumps(without, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
