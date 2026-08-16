"""Golden dataset format: load, validate, and expose curated cases.

A dataset is one JSON file pairing a curated candle series with the output
the SLS says the engine must produce for it. The format is deliberately
hand-writable: prices are strings, candles are terse, and nothing in the
`expected` block requires the labeller to compute a hash or an id.

Provenance is mandatory (Constitution §32.3-§32.4): every case records who
labelled it, when, against which SLS sections, and — in `labelling_rationale`
— *why* the expectation follows from doctrine. A dataset whose expectation was
produced by running the detector and pasting the result proves nothing; it
can only ever agree with the code it was copied from. The rationale field is
where that distinction is made visible to a reviewer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe

DATASET_ROOT = Path(__file__).resolve().parent.parent / "datasets"

_REQUIRED_TOP_LEVEL = (
    "dataset_id",
    "engine",
    "sls_sections",
    "description",
    "labelling_rationale",
    "labelled_by",
    "labelled_at",
    "algo_version",
    "symbol",
    "timeframe",
    "candles",
    "expected",
)


@dataclass(frozen=True, slots=True)
class GoldenDataset:
    dataset_id: str
    engine: str
    sls_sections: tuple[str, ...]
    description: str
    labelling_rationale: str
    labelled_by: str
    labelled_at: str
    algo_version: str
    symbol: str
    timeframe: Timeframe
    candles: tuple[Candle, ...]
    expected: dict[str, Any]
    path: Path

    @property
    def start(self) -> datetime:
        return self.candles[0].open_time

    @property
    def end(self) -> datetime:
        """Exclusive upper bound covering the final candle."""

        return self.candles[-1].open_time + self.timeframe.duration


def load_dataset(path: Path) -> GoldenDataset:
    raw = json.loads(path.read_text(encoding="utf-8"))

    missing = [field for field in _REQUIRED_TOP_LEVEL if field not in raw]
    if missing:
        raise ValueError(f"{path.name}: dataset is missing required fields: {missing}")

    if not raw["labelling_rationale"].strip():
        raise ValueError(
            f"{path.name}: labelling_rationale must explain why the expectation "
            "follows from the SLS. An unexplained expectation is not a label."
        )

    if not raw["sls_sections"]:
        raise ValueError(f"{path.name}: a dataset must cite at least one SLS section")

    symbol = raw["symbol"]
    timeframe = Timeframe(raw["timeframe"])

    candles = tuple(
        _build_candle(entry, symbol=symbol, timeframe=timeframe, path=path)
        for entry in raw["candles"]
    )

    if not candles:
        raise ValueError(f"{path.name}: a dataset needs at least one candle")

    _assert_contiguous(candles, path=path)

    return GoldenDataset(
        dataset_id=raw["dataset_id"],
        engine=raw["engine"],
        sls_sections=tuple(raw["sls_sections"]),
        description=raw["description"],
        labelling_rationale=raw["labelling_rationale"],
        labelled_by=raw["labelled_by"],
        labelled_at=raw["labelled_at"],
        algo_version=raw["algo_version"],
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        expected=raw["expected"],
        path=path,
    )


def discover_datasets(root: Path | None = None) -> tuple[GoldenDataset, ...]:
    """Load every committed dataset, sorted by id for stable test ordering."""

    base = root or DATASET_ROOT
    found = tuple(
        load_dataset(path) for path in sorted(base.rglob("*.json")) if "raw" not in path.parts
    )

    ids = [dataset.dataset_id for dataset in found]
    duplicates = {item for item in ids if ids.count(item) > 1}
    if duplicates:
        raise ValueError(f"duplicate dataset_id values: {sorted(duplicates)}")

    return tuple(sorted(found, key=lambda dataset: dataset.dataset_id))


def _build_candle(
    entry: dict[str, Any],
    *,
    symbol: str,
    timeframe: Timeframe,
    path: Path,
) -> Candle:
    try:
        volume = Decimal(entry.get("volume", "100"))

        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=datetime.fromisoformat(entry["open_time"]),
            open=Decimal(entry["open"]),
            high=Decimal(entry["high"]),
            low=Decimal(entry["low"]),
            close=Decimal(entry["close"]),
            volume=volume,
            quote_volume=Decimal(entry.get("quote_volume", str(volume * Decimal("100")))),
            taker_buy_volume=Decimal(entry.get("taker_buy_volume", str(volume / Decimal("2")))),
            trade_count=int(entry.get("trade_count", 10)),
            source=CandleSource.BACKFILL,
        )
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{path.name}: candle entry missing field {exc}") from exc


def _assert_contiguous(candles: Sequence[Candle], *, path: Path) -> None:
    """Golden series must be gap-free.

    SLS §2.15.4 forbids confirming structure across a data hole, so a dataset
    containing an accidental gap would be testing gap handling while claiming
    to test doctrine. Gap behaviour gets its own explicit datasets.
    """

    step = candles[0].timeframe.duration

    for previous, current in pairwise(candles):
        if current.open_time - previous.open_time != step:
            raise ValueError(
                f"{path.name}: non-contiguous candles at "
                f"{previous.open_time.isoformat()} -> {current.open_time.isoformat()}"
            )
