"""Re-key §5.9 interactions off the window-local index, and drop the copies.

`interaction_id` was `sha256(zone_id | kind | candle_index | observed_at)`, and
`candle_index` is the row's offset inside the 500-candle replay window. That
window slides forward one candle per close, so the same real interaction was
offset 136 on one pass and 135 on the next, the hash moved with it, and
`on_conflict_do_nothing` never had a conflict to do nothing about. Every pass
wrote the interaction again for as long as its candle stayed in the window.

Measured on the soak VM: 19,823,936 rows, and over a 300-zone sample 11,197 of
them stood for 544 distinct (zone, kind, candle) triples -- a factor of 20.6.

This keeps the earliest row of each triple and re-keys it to the new hash. The
`min(interaction_id)` tie-break is arbitrary but deterministic: the duplicates
of a triple describe the same candle against the same zone, and differ only in
the window offset that recorded them and in nothing a consumer reads.

Recomputing the hash in SQL has to reproduce Python's `datetime.isoformat()`
byte for byte. No `observed_at` in the table carries microseconds (checked:
zero rows), so the format below is exact, and it was verified against
`_interaction_id` on two real rows before this migration was written.

Downgrade cannot restore what it deleted, nor the old ids -- the window offset
they were hashed with is gone. It is a no-op rather than a lie.
"""

from __future__ import annotations

from alembic import op

revision = "011_interaction_identity"
down_revision = "010_wash_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM detection.ict_zone_interactions a
        USING detection.ict_zone_interactions b
        WHERE a.zone_id = b.zone_id
          AND a.kind = b.kind
          AND a.observed_at = b.observed_at
          AND a.interaction_id > b.interaction_id
        """
    )

    # The `to_char` format reproduces Python's `datetime.isoformat()` exactly
    # for a UTC timestamp with no microseconds, which is every row in this
    # table. Checked against `_interaction_id` on real rows before writing it.
    op.execute(
        """
        UPDATE detection.ict_zone_interactions
        SET interaction_id = encode(
            sha256(
                convert_to(
                    zone_id || '|' || kind || '|'
                    || to_char(observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
                    || '+00:00',
                    'UTF8'
                )
            )::bytea,
            'hex'
        )
        """
    )

    # The new primary key is a hash of exactly this triple, so this index is
    # redundant today and that is the point: it states the invariant somewhere
    # a future change to the hash cannot quietly walk past, and it is what
    # would have turned twenty million rows into a failed insert on day one.
    op.create_index(
        "uq_ict_zone_interactions_identity",
        "ict_zone_interactions",
        ["zone_id", "kind", "observed_at"],
        unique=True,
        schema="detection",
    )


def downgrade() -> None:
    """The index goes; the deleted rows and the old ids cannot come back.

    See the module docstring -- the window offset they were hashed with is not
    recoverable, so restoring them is not something this can honestly offer.
    """
    op.drop_index(
        "uq_ict_zone_interactions_identity",
        table_name="ict_zone_interactions",
        schema="detection",
    )
