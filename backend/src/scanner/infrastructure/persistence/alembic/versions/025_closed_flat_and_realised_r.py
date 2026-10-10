"""SLS v1.0.15 §12.4: the CLOSED_FLAT state, and `realised_r` on the outcome.

A laddered M5 signal exits on its trailing invalidation, so its result is no
longer "target touched" or "invalidation closed through" but the R it booked.
Two things follow for the database:

* **CLOSED_FLAT** -- a signal that reached TP1, had its invalidation moved to
  the entry, and gave the move back: `realised_r` exactly 0. T18's two state
  constraints and T19's outcome constraint all enumerate the states they accept,
  so without this the first CLOSED_FLAT the monitor writes is refused on insert.
* **`realised_r`** on T19 -- nullable, because every pool-exit outcome (all of
  them before v1.0.15) has none, and a zero there would claim a result nobody
  measured. Signed, so it stays outside `ck_signal_outcomes_excursions`.

No row is read or rewritten. `ADD COLUMN` without a default does not touch
existing rows, which also keeps it clear of 018's append-only triggers: those
fire on UPDATE, DELETE and TRUNCATE, and this migration issues none of them.
The grants in `ops/db/least-privilege-role.sql` are table-level, so the new
column needs no grant of its own.

**Downgrade refuses while any CLOSED_FLAT row exists.** Re-adding the old
constraints over such a row would fail half way; refusing first says why. The
rows are append-only evidence and are not deleted to make a downgrade fit.
"""

from __future__ import annotations

from alembic import op

revision = "025_closed_flat_and_realised_r"
down_revision = "024_symbol_stable_flag"
branch_labels = None
depends_on = None

_OLD_STATES = (
    "'DETECTED','PUBLISHED','SUPPRESSED','ACTIVE','SUCCESS','FAILED',"
    "'EXPIRED_UNTOUCHED','EXPIRED_ACTIVE','INVALIDATED_EARLY'"
)
_NEW_STATES = _OLD_STATES + ",'CLOSED_FLAT'"

_OLD_OUTCOMES = "'SUCCESS','FAILED','EXPIRED_UNTOUCHED','EXPIRED_ACTIVE','INVALIDATED_EARLY'"
_NEW_OUTCOMES = _OLD_OUTCOMES + ",'CLOSED_FLAT'"


def _replace_checks(states: str, outcomes: str) -> None:
    for column, name in (
        ("from_state", "ck_signal_transitions_from"),
        ("to_state", "ck_signal_transitions_to"),
    ):
        op.execute(f"ALTER TABLE detection.signal_transitions DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE detection.signal_transitions ADD CONSTRAINT {name} "
            f"CHECK ({column} IN ({states}))"
        )

    op.execute("ALTER TABLE detection.signal_outcomes DROP CONSTRAINT ck_signal_outcomes_outcome")
    op.execute(
        "ALTER TABLE detection.signal_outcomes ADD CONSTRAINT ck_signal_outcomes_outcome "
        f"CHECK (outcome IN ({outcomes}))"
    )


def upgrade() -> None:
    _replace_checks(_NEW_STATES, _NEW_OUTCOMES)

    op.execute(
        "ALTER TABLE detection.signal_outcomes ADD COLUMN IF NOT EXISTS realised_r numeric(38, 18)"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM detection.signal_outcomes WHERE outcome = 'CLOSED_FLAT')
               OR EXISTS (
                   SELECT 1 FROM detection.signal_transitions
                    WHERE to_state = 'CLOSED_FLAT' OR from_state = 'CLOSED_FLAT'
               )
            THEN
                RAISE EXCEPTION
                    'CLOSED_FLAT rows exist; they are append-only evidence and the old '
                    'constraints cannot hold them. Not downgrading.';
            END IF;
        END $$;
        """
    )

    op.execute("ALTER TABLE detection.signal_outcomes DROP COLUMN IF EXISTS realised_r")

    _replace_checks(_OLD_STATES, _OLD_OUTCOMES)
