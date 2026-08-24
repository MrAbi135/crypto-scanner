"""T20 `identity.tenants` and T21 `identity.users` — the account record.

S10 as the roadmap writes it is a large sprint: registration, verification
email, TOTP, tenancy, plans-as-data, an entitlement engine, GDPR workflows, an
audit hash chain and RLS. The scope for this build is **S10-minimal**: enough
identity for S11's endpoints to be authenticated and S13's terminal to log in,
and nothing that cannot yet be exercised end to end.

**What this migration builds:** the two tables the auth path reads on every
request. Columns follow DDD T20/T21 so the shape does not have to change when
the rest of S10 lands.

**Columns present but unused, deliberately.** `totp_secret_enc`,
`totp_enabled`, `email_verified_at` and `role` are named by T21 and carried
here. They are not written by anything in this build. Carrying them costs a
nullable column each and avoids a migration that would rewrite the hot auth
table later; leaving them out would mean this table does not match the
document that specifies it. Each one that pays no part in a decision today is
listed in the application layer's `UNUSED_IDENTITY_COLUMNS` so it cannot
quietly start reading as meaningful.

**`email` is stored case-folded**, per T21's "unique, case-folded". The unique
index is on the stored value rather than on `lower(email)`, because folding at
the boundary means every read path sees one spelling — an index expression
would let a caller look up `A@b.com`, miss, and create a second account.

**No `ON DELETE CASCADE` anywhere**, per DDD §545: deletion is the GDPR
workflow's job, and nothing cascades away a record an immutable table may
reference.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019_identity"
down_revision = "018_append_only_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")

    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenants"),
        sa.CheckConstraint("status IN ('ACTIVE','SUSPENDED')", name="ck_tenants_status"),
        schema="identity",
    )

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        # Case-folded at the application boundary; see the module docstring.
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        # T21's columns for flows this build does not implement. Nullable and
        # never written; see the module docstring.
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name="pk_users"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.tenant_id"],
            name="fk_users_tenant",
        ),
        sa.CheckConstraint(
            "role IN ('user','support','ops','superadmin')",
            name="ck_users_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','LOCKED','DISABLED')",
            name="ck_users_status",
        ),
        # The folding happens in Python, so the database should refuse a row
        # that did not go through it. Without this the uniqueness guarantee
        # rests entirely on every future caller remembering.
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_folded"),
        schema="identity",
    )

    op.create_index("uq_users_email", "users", ["email"], unique=True, schema="identity")
    op.create_index("ix_users_tenant", "users", ["tenant_id"], schema="identity")


def downgrade() -> None:
    op.drop_index("ix_users_tenant", table_name="users", schema="identity")
    op.drop_index("uq_users_email", table_name="users", schema="identity")
    op.drop_table("users", schema="identity")
    op.drop_table("tenants", schema="identity")
    op.execute("DROP SCHEMA IF EXISTS identity")
