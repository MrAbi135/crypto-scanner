"""SLS §2.15 battery: series-level checks incl. malformed cases."""

from scanner.application.marketdata import validate_batch, verify_aggregation
from scanner.shared import Timeframe, dec
from tests.support.builders import BASE_TIME, make_candle, make_series


def test_clean_series_passes() -> None:
    result = validate_batch(make_series(10))
    assert result.ok and not result.findings


def test_gap_is_recorded_not_fatal() -> None:
    series = make_series(10)
    dropped = series[:4] + series[6:]  # 2-candle hole
    result = validate_batch(dropped)
    assert result.ok  # gaps are ledger facts, not corruption
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.gap_candles == 2
    assert gap.open_time == series[3].open_time


def test_duplicate_is_fatal() -> None:
    series = make_series(5)
    result = validate_batch([*series[:3], series[2], *series[3:]])
    assert not result.ok
    assert any(f.check == "duplicate" for f in result.findings)


def test_disordered_batch_is_fatal() -> None:
    series = make_series(5)
    result = validate_batch([series[0], series[2], series[1]])
    assert not result.ok


def test_mixed_series_is_fatal() -> None:
    h1 = make_candle(timeframe=Timeframe.H1)
    h4 = make_candle(timeframe=Timeframe.H4)
    assert not validate_batch([h1, h4]).ok


def test_seam_gap_against_persisted_tail() -> None:
    series = make_series(5)
    # persisted tail two candles before the batch start ⇒ 1 missing at the seam
    result = validate_batch(series[2:], expected_prev_open=series[0].open_time)
    assert result.ok
    assert len(result.gaps) == 1 and result.gaps[0].gap_candles == 1


def test_batch_overlapping_tail_is_fatal() -> None:
    series = make_series(5)
    result = validate_batch(series, expected_prev_open=series[0].open_time)
    assert not result.ok


def test_aggregation_match() -> None:
    finer = make_series(12, timeframe=Timeframe.M5)  # one H1 of M5s
    native = make_candle(
        timeframe=Timeframe.H1,
        open_time=BASE_TIME,
        open_=finer[0].open,
        close=finer[-1].close,
        high=max(c.high for c in finer),
        low=min(c.low for c in finer),
        volume=sum((c.volume for c in finer), dec("0")),
    )
    assert verify_aggregation(native, finer) is None


def test_aggregation_mismatch_detected() -> None:
    finer = make_series(12, timeframe=Timeframe.M5)
    native = make_candle(
        timeframe=Timeframe.H1,
        open_time=BASE_TIME,
        open_=finer[0].open,
        close=finer[-1].close + dec("5"),  # corrupted close
        high=max(c.high for c in finer) + dec("5"),
        low=min(c.low for c in finer),
        volume=sum((c.volume for c in finer), dec("0")),
    )
    finding = verify_aggregation(native, finer)
    assert finding is not None and finding.check == "aggregation_mismatch"


def test_aggregation_span_mismatch_detected() -> None:
    finer = make_series(11, timeframe=Timeframe.M5)  # one M5 short
    native = make_candle(timeframe=Timeframe.H1, open_time=BASE_TIME)
    finding = verify_aggregation(native, finer)
    assert finding is not None and "span mismatch" in finding.message


def test_empty_batch_is_ok() -> None:
    assert validate_batch([]).ok
