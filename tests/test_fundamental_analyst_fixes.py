"""The Fundamental Analyst's six findings (FA-1 .. FA-6).

Every one of them is a report that answered confidently and wrongly, which in a
fundamentals tool is worse than not answering.

**FA-1** — market cap and FDV read N/A for most protocols, including Uniswap V3
and Curve, and so did every multiple built on them. The brand scan took
``candidates[0]`` — whatever DefiLlama listed first — rather than the parent that
actually owns the token, and every failure collapsed into ``{}`` so the report
could not say whether a protocol has no token or the lookup was rate limited.

**FA-2** — ``hyperliquid`` resolved to the HLP vault in one routine and the spot
order book in the other. A brand's products are different instruments; picking
the largest is a guess wearing an answer's clothes.

**FA-3** — ``aapl`` became Maple and ``nvda`` became Veda, because the ticker
test required uppercase and a lowercase ticker fell through to a fuzzy protocol
match. ``AAVE`` went the other way and was treated as a stock.

**FA-4** — the AGENT.md samples called ``httpx.get`` synchronously inside the
bot's event loop; a bounded 2-second call delayed a concurrent dashboard request
by 1.82s.

**FA-5** — a typo resolved silently to whatever it resembled, and ``_ttm_sum``
summed however many quarters existed while calling the result TTM.

**FA-6** — ``dividendYield`` arrives in percentage points and was multiplied by
100 again: CSCO showed 146.0%, PYPL 100.0%. Two peer tickers were also dead.
"""

import importlib.util
import re
from pathlib import Path

import pandas as pd
import pytest

FA = Path("agents/fundamental_analyst")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, FA / f"routines/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def snapshot():
    return _load("traditional_company_snapshot")


@pytest.fixture(scope="module")
def peers():
    return _load("peer_comparison")


@pytest.fixture(scope="module")
def defi():
    return _load("defi_protocol_snapshot")


# ── FA-6: the dividend yield is a fraction like its neighbours ──────────────


@pytest.mark.parametrize(
    "raw,shown", [(1.46, "1.5%"), (1.0, "1.0%"), (0.5, "0.5%"), (0.0, "0.0%")]
)
def test_a_percentage_point_yield_is_normalised(snapshot, raw, shown):
    """The live figures: 1.46 was shown as 146.0%."""
    assert snapshot._fmt_pct(snapshot._pct_points_to_fraction(raw)) == shown


@pytest.mark.parametrize("raw", [None, "", "n/a", float("inf")])
def test_an_unreadable_yield_is_not_invented(snapshot, raw):
    value = snapshot._pct_points_to_fraction(raw)
    assert value is None or value == value  # never NaN


def test_the_neighbouring_rates_are_left_alone(snapshot):
    """``payoutRatio`` and the margins really are fractions, so the shared
    formatter stays correct for them — the fix belongs at the one field that
    arrived pre-scaled, not in the formatter."""
    source = (FA / "routines/traditional_company_snapshot.py").read_text()

    assert '"payout_ratio": info.get("payoutRatio")' in source
    assert '"div_yield": _pct_points_to_fraction(' in source


def test_the_dead_peer_tickers_are_gone(snapshot):
    """VMW was delisted in 2023 and SQ renamed in 2025; Yahoo returns empty
    records for both, so those peer rows were blank."""
    flat = {t for peers in snapshot._INDUSTRY_PEERS.values() for t in peers}

    assert "VMW" not in flat
    assert "SQ" not in flat


# ── FA-5: a partial period is not a TTM ─────────────────────────────────────


def test_four_quarters_sum(snapshot):
    df = pd.DataFrame([[10, 10, 10, 10]], index=["Total Revenue"])
    assert snapshot._ttm_sum(df, "Total Revenue") == 40.0


@pytest.mark.parametrize("quarters", [1, 2, 3])
def test_fewer_than_four_is_not_reported_as_twelve_months(snapshot, quarters):
    """Annualising from a short history assumes the quarters that are missing,
    and every multiple built on it is wrong by the same factor."""
    df = pd.DataFrame([[10] * quarters], index=["Total Revenue"])

    assert snapshot._ttm_sum(df, "Total Revenue") is None


def test_nan_quarters_do_not_count_toward_the_window(snapshot):
    df = pd.DataFrame([[10, 10, float("nan"), float("nan")]], index=["Total Revenue"])

    assert snapshot._ttm_sum(df, "Total Revenue") is None


def test_an_absent_row_is_still_none(snapshot):
    df = pd.DataFrame([[10, 10, 10, 10]], index=["Total Revenue"])

    assert snapshot._ttm_sum(df, "Nope") is None


# ── FA-3: a ticker is not a protocol that resembles it ──────────────────────

PROTOCOLS = [
    {"slug": "maple", "name": "Maple", "tvl": 5e8},
    {"slug": "veda", "name": "Veda", "tvl": 1e8},
    {"slug": "aave-v3", "name": "Aave V3", "tvl": 2e10},
    {"slug": "aave-v2", "name": "Aave V2", "tvl": 1e9},
    {"slug": "uniswap-v3", "name": "Uniswap V3", "tvl": 3e9},
    {"slug": "coinbase-wrapped", "name": "cbBTC", "tvl": 1e9},
    {"slug": "lido", "name": "Lido", "tvl": 2e10},
]


def _classify(peers, asset: str) -> str:
    if peers._looks_like_ticker(asset):
        return "defi" if peers._protocol_named_exactly(asset, PROTOCOLS) else "stock"
    return "defi" if peers._find_slug(asset, PROTOCOLS, fuzzy=False)[0] else "stock"


@pytest.mark.parametrize("ticker", ["aapl", "AAPL", "nvda", "NVDA", "HOOD", "COIN"])
def test_a_stock_ticker_is_a_stock(peers, ticker):
    """``aapl`` became Maple and ``nvda`` became Veda. ``COIN`` must also not be
    swallowed by ``coinbase-wrapped``, which is the mirror failure."""
    assert _classify(peers, ticker) == "stock"


@pytest.mark.parametrize("protocol", ["AAVE", "aave", "lido", "uniswap-v3"])
def test_a_protocol_is_a_protocol(peers, protocol):
    """``AAVE`` was classified as a stock: DefiLlama lists Aave only under
    versioned slugs, and an exact-slug test alone could not see it."""
    assert _classify(peers, protocol) == "defi"


def test_the_ticker_test_is_case_insensitive(peers):
    assert peers._looks_like_ticker("aapl")
    assert peers._looks_like_ticker("AAPL")
    assert not peers._looks_like_ticker("uniswap-v3")


def test_classification_never_uses_the_fuzzy_fallback(peers):
    """A near-miss may resolve a protocol the caller already said is a protocol;
    it must not decide what KIND of thing an input is."""
    assert peers._find_slug("aapl", PROTOCOLS, fuzzy=False)[0] is None
    # ...while the fuzzy route still exists for the fetch path.
    assert peers._find_slug("aapl", PROTOCOLS, fuzzy=True)[0] is not None


def test_a_versioned_slug_is_reachable_but_a_substring_is_not(peers):
    assert peers._protocol_named_exactly("AAVE", PROTOCOLS)["slug"].startswith("aave-")
    assert peers._protocol_named_exactly("COIN", PROTOCOLS) is None


# ── FA-2: a brand with several products is a question ───────────────────────

VENUES = [
    {
        "slug": "hyperliquid",
        "name": "Hyperliquid",
        "tvl": 3e9,
        "category": "Derivatives",
    },
    {"slug": "hyperliquid-hlp", "name": "HLP", "tvl": 5e8, "category": "Yield"},
    {
        "slug": "hyperliquid-spot-orderbook",
        "name": "Spot",
        "tvl": 2e9,
        "category": "Dexs",
    },
    {"slug": "uniswap-v2", "name": "Uniswap V2", "tvl": 1e9, "category": "Dexs"},
    {"slug": "uniswap-v3", "name": "Uniswap V3", "tvl": 4e9, "category": "Dexs"},
    {"slug": "lido", "name": "Lido", "tvl": 2e10, "category": "Liquid Staking"},
]


def test_a_multi_product_brand_is_ambiguous(peers):
    """Perps venue, vault and spot book are different instruments."""
    variants = peers._product_variants("hyperliquid", VENUES)

    assert len(variants) == 3


def test_versions_are_not_products(peers):
    """``uniswap-v2`` and ``uniswap-v3`` are one instrument at two ages, so
    taking the largest is a default rather than a wrong answer."""
    variants = peers._product_variants("uniswap", VENUES)

    assert [p["slug"] for p in variants] == ["uniswap-v3"]


def test_a_single_product_brand_is_not_ambiguous(peers):
    assert len(peers._product_variants("lido", VENUES)) == 1


def test_the_suffix_blacklist_is_no_longer_what_decides(peers):
    """It had to enumerate every product name anyone might ship, and the spot
    order book that produced this finding was not on it."""
    assert "-spot-orderbook" not in peers._VAULT_SUFFIXES
    assert len(peers._product_variants("hyperliquid", VENUES)) > 1


# ── FA-1: the token belongs to the brand, and absence has a reason ──────────

LLAMA = [
    {"slug": "uniswap", "gecko_id": "uniswap", "tvl": 1e8},
    {"slug": "uniswap-v2", "gecko_id": "uni-v2-stale", "tvl": 1e9},
    {"slug": "uniswap-v3", "tvl": 4e9},
    {"slug": "curve-dex", "tvl": 2e9},
    {"slug": "curve", "gecko_id": "curve-dao-token", "tvl": 1e8},
]


@pytest.mark.parametrize(
    "slug,expected",
    [("uniswap-v3", "uniswap"), ("curve-dex", "curve-dao-token")],
)
def test_the_parent_token_is_preferred_over_an_arbitrary_sibling(defi, slug, expected):
    """``candidates[0]`` was whatever DefiLlama listed first — which could be a
    stale id that 404s into a silent N/A."""
    assert defi._find_gecko_id(slug, slug, LLAMA) == expected


def test_a_missing_market_cap_says_why(defi):
    source = (FA / "routines/defi_protocol_snapshot.py").read_text()

    assert "no token identity on record" in source
    assert "rate limited" in source
    assert 'cg_data.get("error")' in source


def test_coingecko_failures_are_not_swallowed(defi):
    source = (FA / "routines/defi_protocol_snapshot.py").read_text()
    fetch = source[source.index("async def _fetch_coingecko") :][:1600]

    assert "except Exception:\n        pass" not in fetch
    assert '"error"' in fetch


# ── FA-4: a snippet runs inside the bot's event loop ────────────────────────


def test_the_agent_samples_never_block_the_loop():
    body = (FA / "AGENT.md").read_text()

    assert "httpx.get(" not in body
    assert "AsyncClient" in body
    assert "await client.get(" in body


def test_the_samples_say_why():
    """An agent told only "use async" will reach for the synchronous call the
    next time it is in a hurry."""
    body = (FA / "AGENT.md").read_text()

    assert "blocks EVERYTHING" in body
    assert "1.82s" in body


def test_the_shipped_routines_already_do_this():
    """The samples were the only blocking thing — pinned so a future edit to a
    routine cannot quietly reintroduce it."""
    for path in (FA / "routines").glob("*.py"):
        source = re.sub(r"#.*", "", path.read_text())
        assert "httpx.get(" not in source, path
