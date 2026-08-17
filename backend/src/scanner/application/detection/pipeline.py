"""The detection pipeline: all seven replay services, in doctrine order.

Extracted from `cli.py`, where this sequence was inline in a 250-line function
and therefore reachable only by typing a command. The engine now runs the same
object, so there is exactly one definition of what "run detection" means.

**The order is not arbitrary and must not be reordered casually.** It is the
module dependency graph from Roadmap §6:

    structure ─→ liquidity ─→ structure_shift ─→ ict ─→ ote / ob ─→ interaction

`structure_shift` (CHoCH/MSS, SLS §3.6) reads liquidity sweep evidence to decide
whether a break is an MSS, so it cannot precede liquidity. The ICT engines read
structure and liquidity facts through `IctEvidenceRepository` for their
qualification flags, so they come last. Running these out of order does not
crash -- each service simply finds less evidence than exists and quietly grades
its output lower.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from scanner.application.detection.ict_interaction_replay import (
    IctZoneInteractionReplayService,
)
from scanner.application.detection.ict_ob_replay import IctOrderBlockReplayService
from scanner.application.detection.ict_ote_replay import IctOteReplayService
from scanner.application.detection.ict_replay import IctReplayService
from scanner.application.detection.liquidity_replay import LiquidityReplayService
from scanner.application.detection.structure_replay import StructureReplayService
from scanner.application.detection.structure_shift_replay import (
    StructureShiftReplayService,
)
from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class DetectionPipelineReport:
    structure: Any
    liquidity: Any
    structure_shift: Any
    ict: Any
    ict_ote: Any
    ict_ob: Any
    ict_interaction: Any


class DetectionPipeline:
    """Run every detector for one context over one window."""

    def __init__(
        self,
        structure: StructureReplayService,
        liquidity: LiquidityReplayService,
        structure_shift: StructureShiftReplayService,
        ict: IctReplayService,
        ict_ote: IctOteReplayService,
        ict_ob: IctOrderBlockReplayService,
        ict_interaction: IctZoneInteractionReplayService,
    ) -> None:
        self._structure = structure
        self._liquidity = liquidity
        self._structure_shift = structure_shift
        self._ict = ict
        self._ict_ote = ict_ote
        self._ict_ob = ict_ob
        self._ict_interaction = ict_interaction

    async def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        rebuild_state: bool = False,
    ) -> DetectionPipelineReport:
        structure = await self._structure.run(
            symbol,
            timeframe,
            start,
            end,
            rebuild_state=rebuild_state,
        )

        liquidity = await self._liquidity.run(symbol, timeframe, start, end)

        structure_shift = await self._structure_shift.run(symbol, timeframe, start, end)

        ict = await self._ict.run(symbol, timeframe, start, end)

        ict_ote = await self._ict_ote.run(symbol, timeframe, start, end)

        ict_ob = await self._ict_ob.run(symbol, timeframe, start, end)

        ict_interaction = await self._ict_interaction.run(symbol, timeframe, start, end)

        return DetectionPipelineReport(
            structure=structure,
            liquidity=liquidity,
            structure_shift=structure_shift,
            ict=ict,
            ict_ote=ict_ote,
            ict_ob=ict_ob,
            ict_interaction=ict_interaction,
        )
