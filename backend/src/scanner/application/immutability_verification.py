"""Boot check: are the append-only guards actually installed?

DDD principle 1: *"No UPDATE/DELETE path exists at any privilege level used by
the application."* Migration 018 installs the triggers that make that true, but
a migration is a thing that ran once on some database — it is not evidence
about the database this process just connected to. A restore from an older
dump, a hand-repaired schema, or a `DISABLE TRIGGER` left on after an
emergency all leave a cluster that looks identical and enforces nothing.

So the guards are checked at boot the way TAD §14 checks the parameter set:
verified against the live catalog, and the process refuses to start rather
than run for months believing its record is immutable.

**The second half of the check reports rather than refuses.** DDD also asks
for "no UPDATE grants to the application role", and the application currently
connects as the database owner, for whom grants are not a restraint at all.
That gap cannot be closed from inside the process — it needs a second role and
a second secret, which is a deployment decision. Refusing to boot over it would
mean refusing to boot at all, so it surfaces as a warning carrying the exact
state, and the runbook carries the fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

GUARDED_TABLES: tuple[str, ...] = (
    "signals",
    "signal_transitions",
    "signal_outcomes",
)

# Two per table: one for UPDATE/DELETE, one for TRUNCATE. Counted rather than
# named so a rename in the migration surfaces here as a miscount instead of
# passing on a substring match.
_GUARDS_PER_TABLE = 2


class ImmutabilityGuardsMissingError(RuntimeError):
    """The append-only guards are not installed on a table that requires them.

    DDD: the signal record "cannot be edited by anyone — including us". A
    process that starts without the guards is one publishing signals into a
    table that can be quietly rewritten, and the whole track record rests on
    that not being possible.
    """


class ImmutabilityInspector(Protocol):
    async def enabled_guard_counts(self) -> dict[str, int]:
        """Enabled append-only triggers per guarded table in `detection`.

        Enabled, not merely present: a disabled trigger is still in the
        catalog and still shows up in a naive existence check, which would
        make this a check that cannot fail.
        """
        ...

    async def connection_role_bypasses_grants(self) -> bool:
        """Whether the connected role is a superuser or owns the guarded tables.

        Either answer means DDD's grant layer is decorative for this
        connection, which the caller reports rather than enforces.
        """
        ...


@dataclass(frozen=True, slots=True)
class ImmutabilityReport:
    """What the live database says about its own append-only guarantees."""

    guarded: tuple[str, ...]
    role_bypasses_grants: bool


async def verify_immutability_guards(
    inspector: ImmutabilityInspector,
) -> ImmutabilityReport:
    """Verify DDD's layer (b), and report on layer (a).

    Raises when any guarded table is missing an enabled trigger. The message
    names every table that failed, not the first: a schema that lost one
    trigger usually lost them the same way, and fixing them one boot at a time
    is a slow way to find that out.
    """
    counts = await inspector.enabled_guard_counts()

    missing = [table for table in GUARDED_TABLES if counts.get(table, 0) < _GUARDS_PER_TABLE]

    if missing:
        detail = ", ".join(
            f"detection.{t} ({counts.get(t, 0)}/{_GUARDS_PER_TABLE})" for t in missing
        )

        raise ImmutabilityGuardsMissingError(
            "append-only guards missing or disabled on: "
            f"{detail}. Migration 018 installs them; a restore from an older "
            "dump or a DISABLE TRIGGER left in place will produce exactly "
            "this. Do not start the engine against this database."
        )

    return ImmutabilityReport(
        guarded=GUARDED_TABLES,
        role_bypasses_grants=await inspector.connection_role_bypasses_grants(),
    )
