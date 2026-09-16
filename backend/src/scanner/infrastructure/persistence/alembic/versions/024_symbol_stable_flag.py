"""SLS §1.6's automatic stablecoin classifier gets somewhere to record its flag.

§1.6: "30-day close-price standard deviation vs. 1.00 USD < 1% ⇒ flagged
stable, quarantined for manual confirmation. Auto-classification alone never
removes a symbol; it flags." Migration 023 gave the curated list its EXCLUDED
status; this gives the classifier its flag, which is a different thing -- a
question awaiting a person, not a decision.

* `stable_flag` -- NULL (not flagged), FLAGGED (awaiting review) or DISMISSED
  (a person ruled it is not a stablecoin; no measurement overrides that).
* `stable_deviation` -- the last measurement: RMS distance of the 30 daily
  closes from 1.00, as a fraction. NULL when there were not 30 closes.
* `stable_checked_at` -- when it was measured, so a reader can tell "never
  measured" from "measured and not pegged".

No data is changed; the worker's nightly pass fills these.
"""

from __future__ import annotations

from alembic import op

revision = "024_symbol_stable_flag"
down_revision = "023_symbol_exclusions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE market.symbols ADD COLUMN IF NOT EXISTS stable_flag varchar(16)")
    op.execute("ALTER TABLE market.symbols ADD COLUMN IF NOT EXISTS stable_deviation numeric")
    op.execute("ALTER TABLE market.symbols ADD COLUMN IF NOT EXISTS stable_checked_at timestamptz")
    op.execute(
        "ALTER TABLE market.symbols ADD CONSTRAINT ck_symbols_stable_flag "
        "CHECK (stable_flag IS NULL OR stable_flag IN ('FLAGGED','DISMISSED'))"
    )
    op.execute(
        "ALTER TABLE market.symbols ADD CONSTRAINT ck_symbols_stable_deviation "
        "CHECK (stable_deviation IS NULL OR stable_deviation >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE market.symbols DROP CONSTRAINT IF EXISTS ck_symbols_stable_deviation")
    op.execute("ALTER TABLE market.symbols DROP CONSTRAINT IF EXISTS ck_symbols_stable_flag")
    op.execute("ALTER TABLE market.symbols DROP COLUMN IF EXISTS stable_checked_at")
    op.execute("ALTER TABLE market.symbols DROP COLUMN IF EXISTS stable_deviation")
    op.execute("ALTER TABLE market.symbols DROP COLUMN IF EXISTS stable_flag")
