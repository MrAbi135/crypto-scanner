"""Trigger guards making T17/T18/T19 append-only in the database, not just in code.

DDD's immutability row asks for three layers: *"(a) no UPDATE grants to the
application role on these tables, (b) trigger-guard rejecting UPDATE/DELETE
(defense in depth), (c) hash chains/payload hashes for tamper evidence"*. Only
(c) was built. Until now "append-only" was a property of the repositories --
they simply had no update method -- which is a promise about the code that
happens to be true, not a property of the data. A psql session, a migration
with a typo, or any future code path could rewrite a published signal and
nothing would refuse.

This migration builds (b). It is the layer that binds today, because the
application connects as `scanner`, which owns the database: grants cannot
restrain an owner, and a superuser bypasses them outright. **Triggers fire for
everyone, owner and superuser alike.** So the layer the DDD lists second is in
practice the only one currently doing work, which is worth saying plainly
rather than shipping (a) as a no-op REVOKE that reads like protection.

**Which tables.** DDD's principle 1 names all three -- "`signals`,
`signal_transitions`, `signal_outcomes` are append-only, immutable" -- while
its enforcement row lists only T17/T19 (and T38/T34, which do not exist yet).
All three are guarded here. T18 holds the state machine, so a mutable T18
could rewrite what happened to a signal without touching T17 at all; guarding
the record while leaving its history editable would protect the noun and not
the story.

**Statement-level, not row-level.** A `DELETE ... WHERE false` matches nothing
and would slip past a row-level trigger. Statement-level also costs nothing
per row on the insert path, which these tables are entirely made of.

**Escape hatch, deliberately manual.** A future migration that genuinely must
rewrite one of these tables has to say so out loud:

    ALTER TABLE detection.signals DISABLE TRIGGER trg_signals_append_only;
    -- ... the rewrite, with the reason in the migration docstring ...
    ALTER TABLE detection.signals ENABLE TRIGGER trg_signals_append_only;

That is three lines and a paragraph of justification, which is the right
amount of friction for editing the crown jewel.
"""

from __future__ import annotations

from alembic import op

revision = "018_append_only_guards"
down_revision = "017_transition_refresh"
branch_labels = None
depends_on = None

_GUARDED = (
    ("signals", "trg_signals_append_only"),
    ("signal_transitions", "trg_signal_transitions_append_only"),
    ("signal_outcomes", "trg_signal_outcomes_append_only"),
)


def upgrade() -> None:
    # `42501` is insufficient_privilege. The alternative, a bare
    # raise_exception, arrives at the driver as a generic error and a caller
    # that wanted to distinguish "this is forbidden" from "the database is
    # down" would have to match on the message text.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION detection.reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'append_only_violation: % on %.% is forbidden',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = '42501';
        END;
        $$;
        """
    )

    for table, trigger in _GUARDED:
        op.execute(
            f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE OR DELETE ON detection.{table}
            FOR EACH STATEMENT
            EXECUTE FUNCTION detection.reject_mutation();
            """
        )

        # TRUNCATE is a separate event class and is not covered by the
        # trigger above -- without this, `TRUNCATE detection.signals` would
        # empty the crown jewel with every guard in place and reporting
        # green.
        op.execute(
            f"""
            CREATE TRIGGER {trigger}_truncate
            BEFORE TRUNCATE ON detection.{table}
            FOR EACH STATEMENT
            EXECUTE FUNCTION detection.reject_mutation();
            """
        )


def downgrade() -> None:
    for table, trigger in _GUARDED:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}_truncate ON detection.{table};")
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON detection.{table};")

    op.execute("DROP FUNCTION IF EXISTS detection.reject_mutation();")
