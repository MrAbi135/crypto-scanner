"""DDD layer (c), finally used: verify the payload seals on T17.

DDD's immutability row asks for "hash chains/payload hashes for tamper
evidence". §15.3(5) puts the hash on every signal at publication and the
column has carried one since T17 existed — but nothing has ever recomputed
one. A seal nobody checks is a column, not evidence.

**What this detects, exactly.** The stored hash is sha256 over the canonical
JSON of the payload, so a payload edited without recomputing its hash is
caught. A coordinated rewrite of both is not — a per-row hash cannot detect
that, and pretending otherwise is worse than the gap. The thing that stops the
coordinated rewrite is migration 018's triggers; this catches what gets past
them, which is corruption, a bad restore, and a hand-edit by someone who did
not know the hash was there.

The two failures are reported apart because they mean different things. A
mismatch is the payload and its seal disagreeing. An *unparseable* payload is
a row whose JSON no longer loads at all, which no hash comparison would flag
as long as the bytes still hash to the recorded value — a truncated write
lands there, and it is a worse state than a mismatch, not a lesser one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

from scanner.application.ports.signals import SignalRecord


class SignalScanner(Protocol):
    async def scan(self, *, batch: int = 500) -> list[SignalRecord]:
        """Every signal, oldest first. See `PgSignalRepository.scan`."""
        ...


@dataclass(frozen=True, slots=True)
class SealFailure:
    signal_id: str
    reason: str
    recorded: str
    recomputed: str


@dataclass(frozen=True, slots=True)
class SealReport:
    checked: int = 0
    failures: tuple[SealFailure, ...] = field(default_factory=tuple)

    @property
    def intact(self) -> bool:
        return not self.failures


def reseal(payload: str) -> str:
    """The seal §15.3(5) computes, over the payload exactly as stored.

    `SignalPayload.seal()` hashes the canonical dump of `as_dict()`, and T17
    stores that same dump verbatim, so the stored string can be rehashed
    directly. Rebuilding a `SignalPayload` from the JSON and re-sealing that
    would verify the *reconstruction* instead -- and would start failing the
    day a field is added to the payload, on rows that were never touched.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def verify_seals(signals: SignalScanner, *, batch: int = 500) -> SealReport:
    """Recompute every payload hash on T17 and report what disagrees."""

    checked = 0
    failures: list[SealFailure] = []

    for row in await signals.scan(batch=batch):
        checked += 1

        recomputed = reseal(row.payload)

        if recomputed != row.payload_hash:
            failures.append(
                SealFailure(row.signal_id, "hash mismatch", row.payload_hash, recomputed)
            )

            continue

        try:
            json.loads(row.payload)
        except ValueError:
            failures.append(
                SealFailure(
                    row.signal_id,
                    "payload is sealed but does not parse",
                    row.payload_hash,
                    recomputed,
                )
            )

    return SealReport(checked=checked, failures=tuple(failures))
