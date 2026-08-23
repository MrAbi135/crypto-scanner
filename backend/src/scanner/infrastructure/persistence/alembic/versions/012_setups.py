"""T16 `detection.setups` — every candidate that passed gates, published or not.

DDD T16: *"Every confluence candidate that passed gates — published **and**
below-floor (SLS §8.6: floor rejects are recorded calibration data)."* Until
now the confluence engine wrote its candidates only as `SETUP_CANDIDATE_*`
rows in `engine_events`, which is an audit log rather than the modelled record
the rest of the product reads.

**Naming follows the detection schema, not the DDD's field list.** T16 names
`symbol_id` and `algo_version_id`, and no table in `detection` uses either
shape: `engine_events`, `ict_zones`, `ict_zone_interactions`,
`ict_zone_transitions`, `liquidity_pools` and `liquidity_transitions` all
carry `symbol` as a plain `varchar(32)` and the algo version as a plain
string. Introducing foreign keys for this one table would make it the odd one
out and would cross a schema boundary (`market.symbols`) that detection has
deliberately not crossed anywhere else. Worth a ruling if normalisation is
wanted; it should then be one migration for all seven, not a divergence here.

The JSON-bearing columns are `text` for the same reason — that is what
`evidence` already is on every detection table that has one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "012_setups"
down_revision = "011_interaction_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setups",
        sa.Column("setup_id", sa.String(length=160), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        # Null when a gate-passing candidate matched no §8.6 archetype. It
        # cannot then be published, and the row exists so calibration can ask
        # why the chain stopped there.
        sa.Column("archetype", sa.String(length=4), nullable=True),
        sa.Column("gate_results", sa.Text(), nullable=False),
        sa.Column("factor_scores", sa.Text(), nullable=False),
        sa.Column("adjustments", sa.Text(), nullable=False),
        sa.Column("base_confidence", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("final_confidence", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("floor_passed", sa.Boolean(), nullable=False),
        sa.Column("algo_version", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("setup_id", name="pk_setups"),
        sa.CheckConstraint(
            "direction IN ('UP','DOWN')",
            name="ck_setups_direction",
        ),
        sa.CheckConstraint(
            "archetype IS NULL OR archetype IN ('A1','A2','A3','A4','A5')",
            name="ck_setups_archetype",
        ),
        # A published setup is one that cleared its archetype's floor, and
        # §8.6 gives every archetype a floor -- so floor_passed without an
        # archetype is a contradiction the table refuses rather than stores.
        sa.CheckConstraint(
            "NOT floor_passed OR archetype IS NOT NULL",
            name="ck_setups_floor_needs_archetype",
        ),
        schema="detection",
    )

    # DDD T16: "(evaluated_at); (archetype, floor_passed, evaluated_at) for
    # calibration queries."
    op.create_index(
        "ix_setups_evaluated_at",
        "setups",
        ["evaluated_at"],
        schema="detection",
    )

    op.create_index(
        "ix_setups_calibration",
        "setups",
        ["archetype", "floor_passed", "evaluated_at"],
        schema="detection",
    )

    # One candidate per symbol, timeframe, direction and close, per algo
    # version. `setup_id` is a hash of exactly that tuple, so this index is
    # redundant today -- deliberately, the same way the §5.9 interaction index
    # is. It states the identity somewhere a future change to the hash cannot
    # walk past.
    op.create_index(
        "uq_setups_identity",
        "setups",
        ["symbol", "timeframe", "direction", "evaluated_at", "algo_version"],
        unique=True,
        schema="detection",
    )


def downgrade() -> None:
    op.drop_index("uq_setups_identity", table_name="setups", schema="detection")
    op.drop_index("ix_setups_calibration", table_name="setups", schema="detection")
    op.drop_index("ix_setups_evaluated_at", table_name="setups", schema="detection")
    op.drop_table("setups", schema="detection")
