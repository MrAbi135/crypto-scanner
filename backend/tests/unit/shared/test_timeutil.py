"""Boundary-math laws (S0.3 §2 property table)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scanner.shared import Timeframe, ValidationError
from scanner.shared.timeutil import floor_to_boundary, is_boundary, next_boundary, span_boundaries

UTC = UTC
_ts = st.datetimes(
    min_value=datetime(2017, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(UTC),
)
_tf = st.sampled_from(list(Timeframe))


@given(_ts, _tf)
def test_floor_is_idempotent_and_bounds(ts: datetime, tf: Timeframe) -> None:
    floored = floor_to_boundary(ts, tf)
    assert floor_to_boundary(floored, tf) == floored
    assert floored <= ts < floored + tf.duration
    assert is_boundary(floored, tf)


@given(_ts)
def test_w1_floors_to_monday_midnight(ts: datetime) -> None:
    floored = floor_to_boundary(ts, Timeframe.W1)
    assert floored.weekday() == 0  # Monday
    assert (floored.hour, floored.minute, floored.second) == (0, 0, 0)


@given(_ts, _tf)
def test_next_boundary_is_strictly_ahead(ts: datetime, tf: Timeframe) -> None:
    nxt = next_boundary(ts, tf)
    assert nxt > ts
    assert is_boundary(nxt, tf)
    assert nxt - ts <= tf.duration


def test_span_boundaries_counts() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=10)
    spans = list(span_boundaries(start, end, Timeframe.H1))
    assert len(spans) == 10
    assert spans[0] == start and spans[-1] == end - timedelta(hours=1)


def test_span_rejects_misaligned_start() -> None:
    with pytest.raises(ValidationError):
        list(
            span_boundaries(
                datetime(2024, 1, 1, 0, 30, tzinfo=UTC),
                datetime(2024, 1, 2, tzinfo=UTC),
                Timeframe.H1,
            )
        )


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        floor_to_boundary(datetime(2024, 1, 1), Timeframe.H1)


def test_timeframe_parse() -> None:
    assert Timeframe.parse("h4") is Timeframe.H4
    with pytest.raises(ValidationError):
        Timeframe.parse("M1")  # M1 is deliberately not scanned (SLS §0.2)
