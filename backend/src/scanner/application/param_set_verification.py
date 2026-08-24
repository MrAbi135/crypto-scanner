"""TAD §14's boot check: the running parameters against what was recorded."""

from __future__ import annotations

import json
from datetime import datetime

from scanner.application.parameters import PARAM_SET_VERSION, checksum, payload
from scanner.application.ports.param_sets import ParamSetRecord, ParamSetRepository


class ParameterSetMismatchError(RuntimeError):
    """The running parameters are not the ones recorded under this version.

    TAD §14: *"param-set checksum mismatch ⇒ engine refuses to score (§6
    failure rule)."* Raised at boot so the process dies with a precise reason
    rather than scoring under a parameter set nobody recorded — which is the
    failure that leaves months of signals unattributable after the fact.
    """


async def verify_parameter_set(
    repository: ParamSetRepository,
    *,
    engine: str,
    algo_version: str,
    now: datetime,
    sls_reference: str | None = None,
) -> ParamSetRecord:
    """Verify the running parameter set, or record it the first time.

    Three outcomes, and the middle one is the whole point:

    * **No row.** This triple has never been deployed, so the set is recorded
      and boot continues. Registering rather than refusing is what makes the
      check bind from the first deployment onward without a manual seeding
      step nobody would remember.
    * **A row whose checksum matches.** Nothing to do.
    * **A row whose checksum differs.** The parameters moved while
      `param_set_version` stayed put. Appendix A requires every parameter
      change to increment the version, so this is precisely the state the
      doctrine forbids, and it raises.

    The third case cannot be recovered from here. Bumping the version is a
    deliberate act with golden re-validation attached, and guessing which side
    is right — the code or the record — is not something a boot sequence gets
    to decide.
    """
    running = checksum()

    recorded = await repository.get(engine, algo_version, PARAM_SET_VERSION)

    if recorded is None:
        fresh = ParamSetRecord(
            engine=engine,
            algo_version=algo_version,
            param_set_version=PARAM_SET_VERSION,
            param_payload=json.dumps(payload(), sort_keys=True, separators=(",", ":")),
            checksum=running,
            sls_reference=sls_reference,
            deployed_at=now,
        )

        await repository.register(fresh)

        return fresh

    if recorded.checksum != running:
        raise ParameterSetMismatchError(
            f"{engine}: parameter set {PARAM_SET_VERSION} was recorded with checksum "
            f"{recorded.checksum} and this build computes {running}. A parameter "
            "changed without incrementing param_set_version (SLS Appendix A), so the "
            "engine refuses to score (TAD §14)."
        )

    return recorded
