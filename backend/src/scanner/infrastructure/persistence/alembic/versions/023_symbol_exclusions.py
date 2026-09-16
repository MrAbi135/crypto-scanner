"""SLS §1.3's hard exclusions get a status and a reason in the registry.

§1.3 excludes stablecoin bases (§1.6), leveraged tokens (§1.7) and fiat-pegged
assets *before* liquidity tiers. Nothing implemented that, and nothing in the
registry could have recorded it: `market.symbols.status` allowed only the §1
lifecycle, so the 2026-09-17 registry carried USDCUSDT and USD1USDT as ACTIVE
T1 symbols.

This adds:

* `EXCLUDED` to `ck_symbols_status`;
* `exclusion_reason` (STABLECOIN / FIAT_PEGGED / LEVERAGED_TOKEN), required
  exactly when the status is EXCLUDED, so a row can never be excluded without
  saying why, or carry a reason while still being scanned.

**No data is changed here.** The next `symbol_sync` (worker boot, then every
UTC midnight) applies the exclusions, because the rule lives in the domain
and a migration that re-implemented it in SQL would be a second copy to keep
in step. Until that sync runs, every row simply has a NULL reason and a
lifecycle status, which satisfies both constraints.

The table holds ~750 rows, so the constraints are added and checked in one
step; the lock lasts milliseconds and needs no NOT VALID / VALIDATE split.
"""

from __future__ import annotations

from alembic import op

revision = "023_symbol_exclusions"
down_revision = "022_recorded_at"
branch_labels = None
depends_on = None

_STATUSES = "'QUARANTINE','ACTIVE','DELISTING','DELISTED','EXCLUDED'"
_PREVIOUS_STATUSES = "'QUARANTINE','ACTIVE','DELISTING','DELISTED'"
_REASONS = "'STABLECOIN','FIAT_PEGGED','LEVERAGED_TOKEN'"


def upgrade() -> None:
    op.execute("ALTER TABLE market.symbols ADD COLUMN IF NOT EXISTS exclusion_reason varchar(32)")

    op.execute("ALTER TABLE market.symbols DROP CONSTRAINT ck_symbols_status")
    op.execute(
        "ALTER TABLE market.symbols ADD CONSTRAINT ck_symbols_status "
        f"CHECK (status IN ({_STATUSES}))"
    )

    op.execute(
        "ALTER TABLE market.symbols ADD CONSTRAINT ck_symbols_exclusion_reason "
        f"CHECK ((status = 'EXCLUDED') = (exclusion_reason IS NOT NULL) "
        f"AND (exclusion_reason IS NULL OR exclusion_reason IN ({_REASONS})))"
    )


def downgrade() -> None:
    # An EXCLUDED row has no lifecycle status to fall back to that would be
    # true, so the downgrade refuses rather than inventing one.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM market.symbols WHERE status = 'EXCLUDED') THEN
                RAISE EXCEPTION 'market.symbols has EXCLUDED rows; resolve them before downgrading';
            END IF;
        END
        $$
        """
    )
    op.execute("ALTER TABLE market.symbols DROP CONSTRAINT ck_symbols_exclusion_reason")
    op.execute("ALTER TABLE market.symbols DROP CONSTRAINT ck_symbols_status")
    op.execute(
        "ALTER TABLE market.symbols ADD CONSTRAINT ck_symbols_status "
        f"CHECK (status IN ({_PREVIOUS_STATUSES}))"
    )
    op.execute("ALTER TABLE market.symbols DROP COLUMN IF EXISTS exclusion_reason")
