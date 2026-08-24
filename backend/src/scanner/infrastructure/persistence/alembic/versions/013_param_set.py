"""T10's parameter-set columns on `detection.algo_versions`.

DDD T10: *"One row per version pair: parameter-set content (JSONB), checksum,
spec reference, deployed/retired timestamps. Engine boot verifies checksum
against this table (TAD §14)."*

The table shipped as a stub — `id, engine, version, created_at` — and nothing
ever wrote to it: zero rows on the soak VM after two days. Without the
parameter set and its checksum there was nothing for boot to verify against,
so TAD §14's *"param-set checksum mismatch ⇒ engine refuses to score"* had no
mechanism at all.

**The unique key widens rather than moves.** It was `(engine, version)`; a
parameter change keeps the algo version and increments `param_set_version`, so
that pair is no longer unique and `(engine, version, param_set_version)` is.
Keeping `engine` in the key is a departure from T10's `(algo_version,
param_set_version)`, and a deliberate one: this registry is per-engine here
(`s4-v7`, `s8-v20`, …) and always has been, so a key without it would collide
the moment two engines share a param set — which is the normal case, since the
set is global.

Existing rows are backfilled with the current version and a null checksum
rather than a fabricated one. A checksum invented at migration time would
certify a parameter set nobody computed; a null says "never verified", and the
boot check treats it as a row to fill in rather than one to compare against.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "013_param_set"
down_revision = "012_setups"
branch_labels = None
depends_on = None

_UNVERIFIED = "unverified"


def upgrade() -> None:
    op.add_column(
        "algo_versions",
        sa.Column(
            "param_set_version",
            sa.String(length=64),
            nullable=False,
            server_default=_UNVERIFIED,
        ),
        schema="detection",
    )

    op.add_column(
        "algo_versions",
        sa.Column("param_payload", sa.Text(), nullable=True),
        schema="detection",
    )

    # Null means "this row predates verification", not "the checksum is
    # empty". Boot fills it in; it never compares against a null.
    op.add_column(
        "algo_versions",
        sa.Column("checksum", sa.String(length=64), nullable=True),
        schema="detection",
    )

    op.add_column(
        "algo_versions",
        sa.Column("sls_reference", sa.String(length=32), nullable=True),
        schema="detection",
    )

    op.add_column(
        "algo_versions",
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        schema="detection",
    )

    op.add_column(
        "algo_versions",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        schema="detection",
    )

    op.drop_constraint(
        "uq_algo_versions_engine_version",
        "algo_versions",
        schema="detection",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_algo_versions_engine_version_param_set",
        "algo_versions",
        ["engine", "version", "param_set_version"],
        schema="detection",
    )

    # The default did its job for the existing rows; leaving it would let a
    # future insert omit the column and land as "unverified" silently.
    op.alter_column(
        "algo_versions",
        "param_set_version",
        server_default=None,
        schema="detection",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_algo_versions_engine_version_param_set",
        "algo_versions",
        schema="detection",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_algo_versions_engine_version",
        "algo_versions",
        ["engine", "version"],
        schema="detection",
    )

    for column in (
        "retired_at",
        "deployed_at",
        "sls_reference",
        "checksum",
        "param_payload",
        "param_set_version",
    ):
        op.drop_column("algo_versions", column, schema="detection")
