"""Build the detection pipeline (Sprint S4b).

One construction site for the nine services and their collaborators.
The CLI and the engine process both call this, so `engine run` and the live loop
cannot drift into detecting differently -- which is exactly what would have
happened had the engine copied the sixty lines this replaces.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.detection.confluence_replay import ConfluenceReplayService
from scanner.application.detection.ict_interaction_replay import (
    IctZoneInteractionReplayService,
)
from scanner.application.detection.ict_ob_replay import IctOrderBlockReplayService
from scanner.application.detection.ict_ote_replay import IctOteReplayService
from scanner.application.detection.ict_replay import IctReplayService
from scanner.application.detection.liquidity_replay import LiquidityReplayService
from scanner.application.detection.participation_replay import (
    ParticipationReplayService,
)
from scanner.application.detection.pipeline import DetectionPipeline
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
)
from scanner.application.detection.structure_replay import StructureReplayService
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
    StructureShiftReplayService,
)
from scanner.application.ports import Clock
from scanner.infrastructure.persistence.detection_repositories import (
    PgEngineEventRepository,
)
from scanner.infrastructure.persistence.ict_evidence_repository import (
    PgIctEvidenceRepository,
)
from scanner.infrastructure.persistence.ict_zone_interaction_repository import (
    PgIctZoneInteractionContextRepository,
    PgIctZoneInteractionRepository,
)
from scanner.infrastructure.persistence.ict_zone_repositories import (
    PgIctZoneRepository,
    PgIctZoneTransitionRepository,
)
from scanner.infrastructure.persistence.liquidity_detection_repositories import (
    PgLiquidityPoolRepository,
    PgLiquidityTransitionRepository,
)
from scanner.infrastructure.persistence.repositories import PgCandleRepository
from scanner.infrastructure.redis.engine_state import RedisEngineStateStore
from scanner.infrastructure.redis.ict_zone_state import RedisIctZoneStateStore
from scanner.infrastructure.redis.liquidity_state import RedisLiquidityStateStore


def build_detection_pipeline(
    sessions: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis,
    clock: Clock,
) -> DetectionPipeline:
    candles = PgCandleRepository(sessions, clock)
    events = PgEngineEventRepository(sessions)
    evidence = PgIctEvidenceRepository(sessions)

    zone_interactions = PgIctZoneInteractionRepository(sessions)

    zones = PgIctZoneRepository(sessions)
    zone_transitions = PgIctZoneTransitionRepository(sessions)
    zone_state = RedisIctZoneStateStore(redis_client)

    return DetectionPipeline(
        structure=StructureReplayService(
            candles,
            events,
            EngineStateManager(RedisEngineStateStore(redis_client)),
            clock,
        ),
        liquidity=LiquidityReplayService(
            candles,
            PgLiquidityPoolRepository(sessions),
            PgLiquidityTransitionRepository(sessions),
            events,
            RedisLiquidityStateStore(redis_client),
            clock,
        ),
        structure_shift=StructureShiftReplayService(
            candles,
            events,
            evidence,
            clock,
            EngineStateManager(
                RedisEngineStateStore(redis_client),
                namespace=SHIFT_NAMESPACE,
            ),
        ),
        ict=IctReplayService(
            candles,
            zones,
            zone_transitions,
            zone_state,
            clock,
        ),
        ict_ote=IctOteReplayService(
            candles,
            zones,
            zone_transitions,
            clock,
        ),
        ict_ob=IctOrderBlockReplayService(
            candles,
            zones,
            zone_transitions,
            zone_state,
            evidence,
            clock,
        ),
        ict_interaction=IctZoneInteractionReplayService(
            candles,
            PgIctZoneInteractionContextRepository(sessions),
            zone_interactions,
        ),
        participation=ParticipationReplayService(
            candles,
            events,
            clock,
        ),
        confluence=ConfluenceReplayService(
            candles,
            events,
            zones,
            evidence,
            zone_interactions,
            clock,
            EngineStateManager(
                RedisEngineStateStore(redis_client),
                namespace=SHIFT_NAMESPACE,
            ),
            shift_algo_version=STRUCTURE_SHIFT_ALGO_VERSION,
        ),
    )
