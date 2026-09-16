"""SLS §1.3 / §1.6 / §1.7 hard exclusions.

Every case below is a real Binance USDT pair as `exchangeInfo` and the ticker
reported it on 2026-09-17, so each test is a decision about a symbol that
exists rather than about a made-up name.
"""

from __future__ import annotations

import pytest

from scanner.domain.common import ExclusionReason, exclusion_reason

# Assets the venue lists, enough for the naming rule's "is the root an asset".
VENUE = frozenset({"BTC", "ETH", "BNB", "XRP", "EOS", "LINK", "USDT", "JUP", "SYRUP"})


def _reason(base: str, *, flag: bool = False) -> ExclusionReason | None:
    return exclusion_reason(base, leveraged_flag=flag, venue_assets=VENUE)


@pytest.mark.parametrize(
    "base",
    # USDC and USD1 were ACTIVE at T1, RLUSD at T2 and FDUSD at T3 in the live
    # registry the day this was written.
    ["USDC", "USD1", "RLUSD", "FDUSD", "TUSD", "USDP", "USDE", "USDS", "BFUSD", "XUSD", "DAI"],
)
def test_usd_stablecoins_are_excluded(base: str) -> None:
    assert _reason(base) is ExclusionReason.STABLECOIN


@pytest.mark.parametrize("base", ["EUR", "EURI", "AEUR", "GBP", "AUD", "KGST"])
def test_fiat_pegged_assets_are_excluded(base: str) -> None:
    """EURUSDT was ACTIVE at T3: a currency rate, not a crypto market."""
    assert _reason(base) is ExclusionReason.FIAT_PEGGED


@pytest.mark.parametrize(
    "base",
    [
        "PAXG",  # gold-backed, ~4,279 USD: a commodity, not a fiat peg
        "XAUT",
        "FRAX",  # 0.2556 -- no longer pegged, trades freely
        "USTC",  # 0.0051 -- the collapsed peg is a market now
        "BTC",
    ],
)
def test_assets_that_only_sound_pegged_stay_in(base: str) -> None:
    assert _reason(base) is None


@pytest.mark.parametrize("base", ["BTCUP", "BTCDOWN", "LINKUP", "ETHDOWN"])
def test_flagged_leveraged_tokens_are_excluded(base: str) -> None:
    assert _reason(base, flag=True) is ExclusionReason.LEVERAGED_TOKEN


@pytest.mark.parametrize("base", ["ETHBULL", "ETHBEAR", "XRPBULL", "BNBBEAR", "EOSBULL"])
def test_the_unflagged_generation_is_caught_by_name(base: str) -> None:
    """Binance never flagged the BULL/BEAR generation; only the name says it."""
    assert _reason(base) is ExclusionReason.LEVERAGED_TOKEN


@pytest.mark.parametrize("base", ["BULL", "BEAR"])
def test_the_bare_first_generation_names_are_excluded(base: str) -> None:
    assert _reason(base) is ExclusionReason.LEVERAGED_TOKEN


@pytest.mark.parametrize("base", ["JUP", "SYRUP"])
def test_real_tokens_that_end_in_a_suffix_stay_in(base: str) -> None:
    """Jupiter and Maple both trade. A bare `(UP|DOWN|...)$` match excluded
    them; there is no J or SYR asset for them to be leveraged versions of."""
    assert _reason(base) is None


def test_the_flag_excludes_even_without_the_name() -> None:
    """§1.7 names both; the venue's own metadata is the stronger evidence."""
    assert _reason("NEWTOKEN", flag=True) is ExclusionReason.LEVERAGED_TOKEN
