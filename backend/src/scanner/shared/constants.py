"""Environment-blind invariants (S0.3 §2)."""

from datetime import UTC
from decimal import ROUND_HALF_EVEN, Context

# Constitution §45.8: one decimal context for all money/price math.
# Precision 28 covers every quote/base asset step Binance lists with headroom;
# banker's rounding avoids systematic drift in aggregation.
DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

# UTC is re-exported (from datetime) so it is the single UTC source platform-wide.
__all__ = ["DECIMAL_CONTEXT", "UTC"]
