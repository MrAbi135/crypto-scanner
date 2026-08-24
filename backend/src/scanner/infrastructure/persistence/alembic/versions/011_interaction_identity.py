"""Re-key §5.9 interactions off the window-local index, and drop the copies.

`interaction_id` was `sha256(zone_id | kind | candle_index | observed_at)`, and
`candle_index` is the row's offset inside the 500-candle replay window. That
window slides forward one candle per close, so the same real interaction was
offset 136 on one pass and 135 on the next, the hash moved with it, and
`on_conflict_do_nothing` never had a conflict to do nothing about. Every pass
wrote the interaction again for as long as its candle stayed in the window.

Measured on the soak VM: 19,823,936 rows, and over a 300-zone sample 11,197 of
them stood for 544 distinct (zone, kind, candle) triples -- a factor of 20.6.
By the time the deploy was planned it had reached 24.7 million and 15 GB, which
is what forced the rebuild below.

This keeps the earliest row of each triple and re-keys it to the new hash. The
`min(interaction_id)` tie-break is arbitrary but deterministic: the duplicates
of a triple describe the same candle against the same zone, and differ only in
the window offset that recorded them and in nothing a consumer reads.

It rebuilds the table rather than deleting from it, for reasons of disk rather
than elegance -- see `upgrade`.

Recomputing the hash in SQL has to reproduce Python's `datetime.isoformat()`
byte for byte. No `observed_at` in the table carries microseconds (checked:
zero rows), so the format below is exact, and it was verified against
`_interaction_id` on two real rows before this migration was written.

Downgrade cannot restore the discarded duplicates, nor the old ids -- the window
offset they were hashed with is gone. It drops the index it added and says so
rather than pretending to reverse the rest.
"""

from __future__ import annotations

from alembic import op

revision = "011_interaction_identity"
down_revision = "010_wash_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rebuild the table rather than delete from it.

    The first version of this ran a self-join `DELETE` and then an `UPDATE`
    over every surviving row. Planned against the soak VM before deployment,
    that came out as two sequential scans of 24.7 million rows with two sorts
    and a materialise, cost ~10.5M -- and a `DELETE` reclaims nothing, so the
    15 GB table would have kept its 15 GB in dead tuples and needed a
    `VACUUM FULL` (another full copy) to give the space back. The VM has 23 GB
    free. That deploy could have filled the disk.

    The rebuild is one parallel scan and one sort, cost ~5.1M, and it writes
    about a twentieth of the rows because the duplicates never land. Dropping
    the old table returns all 15 GB instead of asking for 15 GB more.

    `LIKE ... INCLUDING CONSTRAINTS` carries the column types, the NOT NULLs
    and the kind check across, rather than my transcribing thirteen columns by
    hand and getting one nullability wrong. The indexes are created explicitly
    below so they keep their canonical names.
    """
    op.execute(
        """
        CREATE TABLE detection.ict_zone_interactions_rebuilt
        (LIKE detection.ict_zone_interactions INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
        """
    )

    # DISTINCT ON keeps the first row of each triple under the ORDER BY, so
    # the `min(interaction_id)` tie-break of the original is preserved: the
    # duplicates of a triple describe the same candle against the same zone
    # and differ only in the window offset that recorded them.
    #
    # The `to_char` format reproduces Python's `datetime.isoformat()` exactly
    # for a UTC timestamp with no microseconds, which is every row in this
    # table. Checked against `_interaction_id` on real rows.
    op.execute(
        """
        INSERT INTO detection.ict_zone_interactions_rebuilt
        SELECT DISTINCT ON (zone_id, kind, observed_at)
            encode(
                sha256(
                    convert_to(
                        zone_id || '|' || kind || '|'
                        || to_char(observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
                        || '+00:00',
                        'UTF8'
                    )
                )::bytea,
                'hex'
            ) AS interaction_id,
            zone_id,
            symbol,
            timeframe,
            zone_type,
            kind,
            observed_at,
            candle_index,
            penetration_depth,
            close_price,
            rejection_wick,
            close_through,
            evidence
        FROM detection.ict_zone_interactions
        ORDER BY zone_id, kind, observed_at, interaction_id
        """
    )

    op.execute("DROP TABLE detection.ict_zone_interactions")

    op.execute(
        "ALTER TABLE detection.ict_zone_interactions_rebuilt RENAME TO ict_zone_interactions"
    )

    op.create_primary_key(
        "pk_ict_zone_interactions",
        "ict_zone_interactions",
        ["interaction_id"],
        schema="detection",
    )

    op.create_index(
        "ix_ict_zone_interactions_context_time",
        "ict_zone_interactions",
        ["symbol", "timeframe", "observed_at"],
        schema="detection",
    )

    op.create_index(
        "ix_ict_zone_interactions_zone_time",
        "ict_zone_interactions",
        ["zone_id", "observed_at"],
        schema="detection",
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
