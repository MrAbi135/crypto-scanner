"""Load the golden datasets into the dev database so the chart can show them.

Constitution §5 makes golden labels the developer's personal responsibility, and
Roadmap §8.2 names S13a the instrument for discharging it. But the datasets are
hand-written JSON, and the chart reads Postgres -- so until now the instrument
could not reach the thing it was built to inspect.

This closes that gap. Each dataset already carries its own synthetic symbol
(GOLDENFVG, GOLDENBSL, ...), so loading them creates contexts the chart can
select like any other.

**It reuses the harness loader deliberately.** Re-parsing the JSON here would
mean the chart could show a series subtly different from the one the tests
assert against -- and then a label verified on screen would be a label verified
against the wrong candles. One parser, one set of candles.

Run: scripts/golden-load.sh   (or scripts/golden-load.ps1)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import redis.asyncio as aioredis  # noqa: E402

from scanner.config import get_settings  # noqa: E402
from scanner.infrastructure.clock import SystemClock  # noqa: E402
from scanner.infrastructure.persistence.database import (  # noqa: E402
    build_engine,
    build_session_factory,
)
from scanner.infrastructure.persistence.repositories import PgCandleRepository  # noqa: E402
from scanner.runtime.wiring.detection import build_detection_pipeline  # noqa: E402
from tests.golden.harness.dataset import discover_datasets  # noqa: E402


async def main() -> int:
    settings = get_settings("engine")

    db = build_engine(settings.db_dsn)
    redis_client = aioredis.from_url(settings.redis_url)
    clock = SystemClock()

    sessions = build_session_factory(db)
    candles = PgCandleRepository(sessions, clock)
    pipeline = build_detection_pipeline(sessions, redis_client, clock)

    datasets = discover_datasets()

    print(f"{len(datasets)} golden datasets\n")

    for dataset in sorted(datasets, key=lambda d: d.dataset_id):
        # emit_outbox stays off: these are historical facts being seeded, not
        # closes that just happened. Announcing them would have the live engine
        # replay a synthetic market as though it were real.
        inserted = await candles.bulk_insert(dataset.candles)

        report = await pipeline.run(
            dataset.symbol,
            dataset.timeframe,
            dataset.start,
            dataset.end,
        )

        print(
            f"  {dataset.symbol:<12} {dataset.timeframe.value:<4} "
            f"candles={len(dataset.candles):<4} new={inserted:<4} "
            f"swings={report.structure.internal_swings + report.structure.external_swings:<4} "
            f"pools={report.liquidity.pools_upserted:<3} "
            f"sweeps={report.liquidity.sweeps:<3} "
            f"{dataset.dataset_id}"
        )

    print("\nOpen the chart and pick any GOLDEN* symbol.")

    await redis_client.aclose()
    await db.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
