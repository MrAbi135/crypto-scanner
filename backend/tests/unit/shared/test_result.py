"""Unit + property tests for the Result exemplar (S0.2 §5)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from scanner.shared.result import Err, Ok


def test_ok_predicates() -> None:
    assert Ok(1).is_ok()
    assert not Ok(1).is_err()


def test_err_predicates() -> None:
    assert Err("boom").is_err()
    assert not Err("boom").is_ok()


def test_map_transforms_ok() -> None:
    assert Ok(2).map(lambda x: x + 1) == Ok(3)


def test_map_is_noop_on_err() -> None:
    assert Err("boom").map(lambda x: x + 1) == Err("boom")


def test_map_err_transforms_err() -> None:
    assert Err("boom").map_err(lambda e: f"{e}!") == Err("boom!")


def test_map_err_is_noop_on_ok() -> None:
    assert Ok(2).map_err(lambda e: "unused") == Ok(2)


def test_and_then_chains_on_ok() -> None:
    assert Ok(2).and_then(lambda x: Ok(x * 3)) == Ok(6)


def test_and_then_can_produce_err() -> None:
    assert Ok(2).and_then(lambda x: Err("rejected")) == Err("rejected")


def test_and_then_short_circuits_on_err() -> None:
    assert Err("boom").and_then(lambda x: Ok(x * 3)) == Err("boom")


def test_unwrap_or() -> None:
    assert Ok(2).unwrap_or(9) == 2
    assert Err("boom").unwrap_or(9) == 9


@given(st.integers())
def test_functor_identity_law(x: int) -> None:
    # map(id) == identity
    assert Ok(x).map(lambda v: v) == Ok(x)


@given(st.integers())
def test_functor_composition_law(x: int) -> None:
    def f(v: int) -> int:
        return v + 1

    def g(v: int) -> int:
        return v * 2

    assert Ok(x).map(f).map(g) == Ok(x).map(lambda v: g(f(v)))


@given(st.integers())
def test_left_identity_monad_law(x: int) -> None:
    # Ok(x).and_then(f) == f(x)
    def f(v: int) -> Ok[int]:
        return Ok(v + 1)

    assert Ok(x).and_then(f) == f(x)


@given(st.integers())
def test_right_identity_monad_law(x: int) -> None:
    # m.and_then(Ok) == m
    assert Ok(x).and_then(Ok) == Ok(x)
