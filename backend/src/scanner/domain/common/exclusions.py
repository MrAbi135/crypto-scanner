"""The hard universe exclusions of SLS §1.3, §1.6 and §1.7.

§1.3 evaluates these *before* liquidity tiers, and until this module nothing
evaluated them at all: `symbol_sync` kept every USDT pair, so on 2026-09-17
USDCUSDT and USD1USDT sat in the scanned universe as T1, RLUSDUSDT as T2 and
FDUSDUSDT and EURUSDT as T3. A peg has no market structure to read -- on a
volume-ranked list the first symbol would have been a flat line.

Three reasons, because §1.3 names three kinds:

* **STABLECOIN** (§1.6) -- a curated list of USD-pegged bases. §1.6 also
  describes an automatic classifier (30-day close stddev vs 1.00 USD < 1%),
  which only *flags* for manual confirmation and never removes a symbol, so it
  is not part of this hard gate.
* **FIAT_PEGGED** (§1.3) -- fiat currencies and fiat-pegged tokens that are not
  USD (EURUSDT is a foreign-exchange rate, not a crypto asset).
* **LEVERAGED_TOKEN** (§1.7) -- "naming **and** exchange metadata flag where
  available". Not configurable.

Deliberately NOT excluded, each checked against live Binance prices on
2026-09-17 rather than assumed:

* **PAXG, XAUT** -- gold-backed (~4,279 USD). §1.3 excludes *fiat*-pegged
  assets; gold is a commodity with its own market structure.
* **FRAX** (0.2556) and **USTC** (0.0051) -- names that once meant a USD peg
  and no longer do. A token that trades freely is a market, whatever it was
  called at launch. Excluding by history would hide real price action.
"""

from __future__ import annotations

from collections.abc import Collection
from enum import Enum


class ExclusionReason(str, Enum):
    STABLECOIN = "STABLECOIN"
    FIAT_PEGGED = "FIAT_PEGGED"
    LEVERAGED_TOKEN = "LEVERAGED_TOKEN"  # noqa: S105 - a reason code, not a secret


# §1.6's "USDC, DAI, FDUSD, TUSD, ..." completed from Binance's own registry.
# Listed while delisted too (BUSD, PAX, USDSB, SUSD): a relisting must not
# quietly bring a peg back into the universe.
STABLECOIN_BASES = frozenset(
    {
        "BFUSD",
        "BUSD",
        "DAI",
        "FDUSD",
        "GUSD",
        "LUSD",
        "PAX",
        "PYUSD",
        "RLUSD",
        "SUSD",
        "TUSD",
        "USD1",
        "USDC",
        "USDD",
        "USDE",
        "USDP",
        "USDS",
        "USDSB",
        "XUSD",
    }
)

FIAT_PEGGED_BASES = frozenset(
    {
        "AEUR",
        "ARS",
        "AUD",
        "BRL",
        "EUR",
        "EURC",
        "EURI",
        "GBP",
        "JPY",
        # Kyrgyz som (0.011387 USD on 2026-09-17; 30-day close spread 0.19%).
        # Found by the §1.6 classifier's first run over live data -- it is not
        # USD-pegged, so the classifier itself would never flag it.
        "KGST",
        "MXN",
        "PLN",
        "RON",
        "RUB",
        "TRY",
        "UAH",
        "ZAR",
    }
)

LEVERAGED_SUFFIXES = ("DOWN", "BULL", "BEAR", "UP", "3L", "3S")


def exclusion_reason(
    base_asset: str,
    *,
    leveraged_flag: bool,
    venue_assets: Collection[str],
) -> ExclusionReason | None:
    """Why `base_asset` may never be scanned, or None if it may.

    `venue_assets` is every asset the venue lists. It is what lets the naming
    rule of §1.7 tell a leveraged token from an ordinary one: BTCUP is BTC
    with a suffix, but JUP (Jupiter) and SYRUP (Maple) are not J and SYR --
    no such assets exist. A bare suffix match would have excluded both, and
    both trade. The metadata flag decides first; naming is the fallback for
    the older BULL/BEAR generation, which Binance never flagged.
    """
    if base_asset in STABLECOIN_BASES:
        return ExclusionReason.STABLECOIN

    if base_asset in FIAT_PEGGED_BASES:
        return ExclusionReason.FIAT_PEGGED

    if leveraged_flag or _named_like_a_leveraged_token(base_asset, venue_assets):
        return ExclusionReason.LEVERAGED_TOKEN

    return None


def _named_like_a_leveraged_token(base_asset: str, venue_assets: Collection[str]) -> bool:
    for suffix in LEVERAGED_SUFFIXES:
        if not base_asset.endswith(suffix):
            continue

        root = base_asset[: -len(suffix)]

        # A bare BULL or BEAR was the first generation's BTC 3x token.
        if root == "" or root in venue_assets:
            return True

    return False
