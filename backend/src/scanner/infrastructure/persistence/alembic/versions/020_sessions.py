"""T22 `identity.sessions` — refresh-token families with rotation and reuse detection.

DDD T22: *"One row per session family: current refresh hash, rotation counter,
device/user-agent info, revocation state."* TAD §20 draws the rule this table
exists for:

> alt reuse detected (old refresh presented) → revoke entire family → 401 →
> full re-auth (possible theft)

**One row per family, not per token.** The rotation replaces `refresh_hash` in
place; the superseded token leaves no row behind. That is what makes reuse
detectable at all — a presented token either hashes to the row's current value
or it does not, and "does not, but the family exists" is the theft signal. A
table of individual tokens would have to decide how long to keep spent ones,
and a spent token that has been pruned is indistinguishable from a forgery.

**`refresh_hash` is sha256, not Argon2.** The secret is 256 bits of
`secrets.token_urlsafe`, so there is nothing to stretch — stretching defends
low-entropy inputs. This is on the hot refresh path and Argon2 would put 50 ms
on every token rotation to protect against a preimage attack on a random
256-bit value.

**Not built here, and named so it is not assumed:** TAD §20's Redis revocation
bitmap ("effective ≤ 30 s"). It is a cache in front of this table, and this
table is the record. Without it, a revoked family is refused on its next
refresh rather than mid-access-token — which is the access token's ≤15 minute
lifetime, not thirty seconds. That gap is real and belongs to the piece that
adds the cache.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "020_sessions"
down_revision = "019_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        # The family id. It travels in the refresh token so a presented token
        # can be located without a table scan over hashes.
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("refresh_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotation_count", sa.Integer(), nullable=False),
        sa.Column("device_label", sa.String(length=200), nullable=True),
        sa.Column("ip_created", sa.String(length=45), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("session_id", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.user_id"],
            name="fk_sessions_user",
        ),
        sa.CheckConstraint("rotation_count >= 0", name="ck_sessions_rotation_count"),
        # A revoked row must say why. `revoke_reason` is what separates a
        # logout from a detected theft in the session list and in the audit
        # trail, and a null one turns both into "gone".
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)",
            name="ck_sessions_revocation_paired",
        ),
        sa.CheckConstraint(
            "revoke_reason IS NULL OR revoke_reason IN "
            "('logout','reuse_detected','user_revoked','password_changed')",
            name="ck_sessions_revoke_reason",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_sessions_lifetime"),
        schema="identity",
    )

    # T22's "unique refresh_hash". Two families holding one hash would make
    # the reuse check ambiguous, and it is the only check that stands between
    # a stolen token and an indefinite session.
    op.create_index(
        "uq_sessions_refresh_hash",
        "sessions",
        ["refresh_hash"],
        unique=True,
        schema="identity",
    )

    # T22's "(user_id, revoked_at) partial on active". The session list and
    # the revoke-all paths both ask only for live families, and on a table
    # that keeps revoked rows for ninety days most of it is not that.
    op.create_index(
        "ix_sessions_user_active",
        "sessions",
        ["user_id", "rotated_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_user_active", table_name="sessions", schema="identity")
    op.drop_index("uq_sessions_refresh_hash", table_name="sessions", schema="identity")
    op.drop_table("sessions", schema="identity")
