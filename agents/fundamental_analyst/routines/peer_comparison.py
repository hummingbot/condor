import asyncio
import difflib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder

CATEGORY = "Analysis"

_INFRA_CATEGORIES = {"Bridge", "Chain", "Infrastructure", "Staking Pool", "RWA"}
# Slug suffixes that indicate a vault/pool variant rather than the main protocol
_VAULT_SUFFIXES = ("-hlp", "-pool", "-vault", "-lp", "-yield", "-amm", "-v1", "-v4")


class Config(BaseModel):
    """Side-by-side fundamental comparison of DeFi protocols and/or stock tickers."""

    assets: List[str] = Field(
        default=["uniswap", "COIN", "hyperliquid", "HOOD"],
        description="DeFi slugs or stock tickers (e.g. ['uniswap', 'COIN', 'hyperliquid', 'HOOD'])",
    )
    asset_types: Dict[str, str] = Field(
        default={},
        description="Optional type overrides: {'COIN': 'stock', 'uniswap': 'defi'}",
    )


# ── formatters ────────────────────────────────────────────────────────────────


def _fmt_usd(val) -> str:
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if abs(val) >= 1e12:
        return f"${val / 1e12:.2f}T"
    if abs(val) >= 1e9:
        return f"${val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.2f}M"
    if abs(val) >= 1e3:
        return f"${val / 1e3:.2f}K"
    return f"${val:.2f}"


def _fmt_mult(val) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.2f}x"
    except (TypeError, ValueError):
        return "N/A"


def _safe_div(num, denom):
    try:
        n, d = float(num), float(denom)
        return n / d if d != 0 else None
    except (TypeError, ValueError):
        return None


def _sum_last(chart: list, n: int) -> Optional[float]:
    if not chart:
        return None
    tail = chart[-n:]
    total = sum(v for _, v in tail if v is not None)
    return total or None


# ── classification helpers ────────────────────────────────────────────────────


def _looks_like_ticker(s: str) -> bool:
    """True for 1–5 letters, in either case (COIN, HOOD, AAPL, aapl).

    Case-insensitive since FA-3. Requiring uppercase meant a lowercase ticker
    skipped this test entirely and fell through to the fuzzy protocol matcher,
    which happily answered: ``aapl`` became Maple and ``nvda`` became Veda, each
    then reported with a DeFi protocol's TVL and fees.
    """
    return bool(re.match(r"^[A-Za-z]{1,5}$", s.strip()))


def _protocol_named_exactly(asset: str, protocols: list):
    """The protocol a ticker-shaped string *names*, or ``None``.

    Deliberately narrow, because the input is ambiguous by shape: an exact slug,
    or a versioned slug of the same name (``AAVE`` → ``aave-v3``), and nothing
    else. A prefix or substring search would read ``COIN`` as ``coinbase`` and a
    real ticker would disappear into the protocol list — which is the mirror of
    the bug this sits beside. Requiring an *exact* slug alone was too strict the
    other way: DefiLlama lists Aave only under versioned slugs, so ``AAVE`` was
    classified as a stock (FA-3).
    """
    low = asset.lower().strip()
    exact = next((p for p in protocols if p.get("slug", "").lower() == low), None)
    if exact:
        return exact
    versioned = [
        p for p in protocols if p.get("slug", "").lower().startswith(f"{low}-")
    ]
    return _pick_best(versioned) if versioned else None


_VERSION_SUFFIX_RE = re.compile(r"^-v\d+(\.\d+)?$")


def _brand(slug: str) -> str:
    """The first token of a slug — the brand a product sits under."""
    return (slug or "").lower().split("-", 1)[0]


def _product_variants(inp: str, protocols: list) -> list:
    """Distinct PRODUCTS under the brand ``inp`` names, by slug.

    A brand can carry several products that are not the same instrument:
    Hyperliquid lists a perps venue, an HLP vault and a spot order book, and
    they have different TVL, different fees and different meaning. Picking the
    largest is how a request for "hyperliquid" was answered with its vault in
    one routine and its spot book in the other (FA-2).

    Versions are NOT products: ``uniswap-v2`` and ``uniswap-v3`` are the same
    instrument at two ages, and taking the largest of those is a sensible
    default rather than a wrong answer. So a ``-v<n>`` suffix is folded away and
    anything else counts as its own product.

    A ``_VAULT_SUFFIXES`` blacklist cannot do this job: it has to enumerate every
    product name anyone might ship, and the spot order book that produced this
    finding was not on it.
    """
    low = inp.lower().strip()
    if not low:
        return []
    kin = [p for p in protocols if _brand(p.get("slug", "")) == _brand(low)]
    products: dict[str, dict] = {}
    for p in kin:
        slug = p.get("slug", "").lower()
        suffix = slug[len(_brand(slug)) :]
        if _VERSION_SUFFIX_RE.match(suffix):
            suffix = ""  # a version of the base product
        # Keep the largest of each product so versions collapse by TVL.
        kept = products.get(suffix)
        if kept is None or (p.get("tvl") or 0) > (kept.get("tvl") or 0):
            products[suffix] = p
    return list(products.values())


def _pick_best(candidates: list):
    """From a list of protocols, prefer non-infra non-vault, then highest TVL."""
    if not candidates:
        return None
    # Exclude infrastructure categories
    trading = [p for p in candidates if p.get("category") not in _INFRA_CATEGORIES]
    pool = trading if trading else candidates
    # Prefer the main protocol over vault/pool variants (e.g. prefer hyperliquid over hyperliquid-hlp)
    core = [
        p
        for p in pool
        if not any(p.get("slug", "").endswith(s) for s in _VAULT_SUFFIXES)
    ]
    preferred = core if core else pool
    return max(preferred, key=lambda p: p.get("tvl") or 0)


def _find_slug(slug_input: str, protocols: list, fuzzy: bool = True) -> tuple:
    """Return (protocol_dict, matched_slug) for a given input. ``(None, input)`` on miss.

    ``fuzzy=False`` stops before the ``difflib`` fallback. Classification passes
    it: deciding *whether a string is a protocol at all* must not rest on a
    near-miss, or every unrecognised stock ticker becomes whichever protocol it
    happens to resemble (FA-3). Fetching a protocol the caller has already been
    classified as DeFi may still use it — that is a typo in a known-kind input,
    which is FA-5's problem and is reported rather than silenced.
    """
    inp = slug_input.lower().strip()

    exact = next((p for p in protocols if p.get("slug", "") == inp), None)
    if exact:
        return exact, inp

    prefix = [p for p in protocols if p.get("slug", "").startswith(inp)]
    if prefix:
        best = _pick_best(prefix)
        return best, best["slug"]

    contains = [
        p
        for p in protocols
        if inp in p.get("slug", "") and not p.get("slug", "").startswith(inp)
    ]
    if contains:
        best = _pick_best(contains)
        return best, best["slug"]

    name_c = [p for p in protocols if inp in p.get("name", "").lower()]
    if name_c:
        best = _pick_best(name_c)
        return best, best["slug"]

    if not fuzzy:
        return None, slug_input

    slugs = [p.get("slug", "") for p in protocols]
    close = difflib.get_close_matches(inp, slugs, n=3, cutoff=0.5)
    if close:
        best_slug = max(
            close,
            key=lambda s: next(
                (p.get("tvl") or 0 for p in protocols if p["slug"] == s), 0
            ),
        )
        proto = next((p for p in protocols if p["slug"] == best_slug), None)
        return proto, best_slug

    return None, slug_input


async def _fetch_parent_index(client: httpx.AsyncClient) -> Dict[str, str]:
    """``parentProtocol id -> gecko_id`` from ``/lite/protocols2``, or ``{}``.

    ``{}`` on any failure, and cached by :class:`_ParentIndex` either way: a
    read that did not answer must not be retried once per asset in the table.
    """
    try:
        r = await client.get("https://api.llama.fi/lite/protocols2")
        if r.status_code != 200:
            return {}
        parents = r.json().get("parentProtocols") or []
    except Exception:
        return {}
    return {
        str(x.get("id")): str(x.get("gecko_id"))
        for x in parents
        if x.get("id") and x.get("gecko_id")
    }


class _ParentIndex:
    """One lazy ``/lite/protocols2`` read, shared by every row in a run.

    The payload is some 7 MB and the rows are fetched concurrently, so the lock
    matters: without it a five-protocol comparison downloads it five times.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: Optional[Dict[str, str]] = None

    async def gecko_id(self, client: httpx.AsyncClient, parent_id: str):
        if not parent_id:
            return None
        async with self._lock:
            if self._by_id is None:
                self._by_id = await _fetch_parent_index(client)
        return self._by_id.get(parent_id) or None


async def _find_gecko_id(
    client: httpx.AsyncClient,
    matched_slug: str,
    user_input: str,
    all_protocols: list,
    parents: _ParentIndex,
) -> Optional[str]:
    """Find gecko_id via DefiLlama data. Falls back to user_input only as a last resort."""
    p = next((x for x in all_protocols if x.get("slug") == matched_slug), None)
    if p and p.get("gecko_id"):
        return p["gecko_id"]

    # The brand's own record, which is where a versioned protocol's token id
    # actually lives (FA-1). ``/protocols`` files no ``gecko_id`` on a version
    # -- the token belongs to Uniswap, not to uniswap-v3 -- and there is no
    # plain ``uniswap`` record for the scan below to find either, so every
    # versioned protocol fell through to the raw input and CoinGecko 404'd.
    # ``mcap`` and the ``mcap_rev`` multiple built on it then read N/A for the
    # whole column.
    #
    # Deliberately the same shape as ``defi_protocol_snapshot``'s copy rather
    # than a shared import: these routines are standalone files and already keep
    # their own ``_find_slug``, ``_pick_best`` and ``_sum_last``. Changing one
    # lookup means changing both -- which is exactly how this one was left
    # behind when the snapshot's was fixed.
    parent_gecko = await parents.gecko_id(
        client, str((p or {}).get("parentProtocol") or "")
    )
    if parent_gecko:
        return parent_gecko

    # Brand prefix scan (strip common version/variant suffixes)
    brand = (
        matched_slug.split("-v")[0]
        if "-v" in matched_slug
        else matched_slug.split("-")[0]
    )
    candidates = [
        x
        for x in all_protocols
        if x.get("slug", "").startswith(brand) and x.get("gecko_id")
    ]
    if candidates:
        # The PARENT first, then the largest sibling -- never ``candidates[0]``,
        # which is whatever DefiLlama happened to list first and can be a dead
        # id that 404s into a silent N/A. Same rule as the snapshot routine's.
        parent = next(
            (x for x in candidates if x.get("slug", "").lower() == brand), None
        )
        if parent:
            return parent["gecko_id"]
        return max(candidates, key=lambda x: x.get("tvl") or 0)["gecko_id"]

    # Only use raw user_input if it looks like a clean coin slug (no spaces, no suffixes)
    inp = user_input.lower().strip()
    if inp and " " not in inp and "-" not in inp:
        return inp
    return None


# ── DeFi fetch ────────────────────────────────────────────────────────────────


async def _fetch_fees_chart(
    client: httpx.AsyncClient, slug: str, data_type: str
) -> list:
    try:
        r = await client.get(
            f"https://api.llama.fi/summary/fees/{slug}",
            params={"dataType": data_type},
        )
        if r.status_code == 200:
            return r.json().get("totalDataChart", [])
    except Exception:
        pass
    return []


async def _fetch_coingecko_mcap(
    client: httpx.AsyncClient, gecko_id: str
) -> Optional[float]:
    if not gecko_id:
        return None
    try:
        r = await client.get(
            f"https://api.coingecko.com/api/v3/coins/{gecko_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
            },
        )
        if r.status_code == 200:
            md = r.json().get("market_data", {})
            return md.get("market_cap", {}).get("usd")
    except Exception:
        pass
    return None


async def _fetch_defi_row(
    client: httpx.AsyncClient,
    slug_input: str,
    all_protocols: list,
    parents: _ParentIndex,
) -> dict:
    """Fetch one DeFi protocol → normalized row dict. Never raises."""
    try:
        # A brand with several PRODUCTS is a question, not a default (FA-2).
        # Only when the caller named the brand alone: an explicit slug such as
        # "hyperliquid-hlp" has already answered it.
        variants = _product_variants(slug_input, all_protocols)
        # Against ALL protocols, not the collapsed variant list: grouping keeps
        # only the largest of each product, so a caller naming a smaller version
        # exactly (``uniswap-v2`` where v3 is bigger) would be told to "pick one"
        # about a slug that is already a precise answer.
        named_exactly = any(
            p.get("slug", "").lower() == slug_input.lower().strip()
            for p in all_protocols
        )
        if len(variants) > 1 and not named_exactly:
            choices = ", ".join(
                sorted(p.get("slug", "") for p in variants if p.get("slug"))
            )
            return {
                "asset": slug_input,
                "type": "defi",
                "error": (
                    f"'{slug_input}' names several products, which are different "
                    f"instruments with different TVL and fees — pick one: {choices}"
                ),
            }

        proto, matched_slug = _find_slug(slug_input, all_protocols)
        if proto is None:
            return {
                "asset": slug_input,
                "type": "defi",
                "error": f"Protocol '{slug_input}' not found on DefiLlama",
            }
        # A match the caller did not ask for is reported, never silently
        # substituted: a typo used to resolve to whatever it resembled and the
        # report named the wrong protocol with no indication (FA-5).
        inexact = matched_slug.lower() != slug_input.lower().strip()

        # Fetch detail page, fees, and revenue in parallel
        r_detail, fees_chart, rev_chart = await asyncio.gather(
            client.get(f"https://api.llama.fi/protocol/{matched_slug}"),
            _fetch_fees_chart(client, matched_slug, "dailyFees"),
            _fetch_fees_chart(client, matched_slug, "dailyRevenue"),
        )
        detail = r_detail.json() if r_detail.status_code == 200 else {}

        tvl = proto.get("tvl")
        # mcap: protocols list → detail page → CoinGecko fallback
        mcap = proto.get("mcap") or detail.get("mcap")
        if not mcap:
            gecko_id = await _find_gecko_id(
                client, matched_slug, slug_input, all_protocols, parents
            )
            mcap = await _fetch_coingecko_mcap(client, gecko_id)

        fees_ttm = _sum_last(fees_chart, min(365, len(fees_chart)))
        rev_ttm = _sum_last(rev_chart, min(365, len(rev_chart)))
        # MCap/Rev uses protocol revenue; fall back to fees if no separate revenue stream
        mcap_rev_base = rev_ttm if rev_ttm else fees_ttm
        mcap_rev = _safe_div(mcap, mcap_rev_base)

        return {
            # The name carries the substitution when there was one, so a typo
            # cannot pass for the protocol the caller meant (FA-5).
            "asset": (
                f"{proto.get('name', matched_slug)} (matched from " f"'{slug_input}')"
                if inexact
                else proto.get("name", matched_slug)
            ),
            "type": "defi",
            "mcap": mcap,
            "rev_ttm": fees_ttm,  # gross fees (user-paid) → "Rev TTM" column
            "net_or_prot": rev_ttm,  # fees kept by protocol → "Net Inc / Protocol Rev" column
            "mcap_rev": mcap_rev,
            "pe": None,  # N/A for DeFi
            "fcf_or_tvl": tvl,
            "fcf_label": "TVL",
            "error": None,
        }
    except Exception as e:
        return {"asset": slug_input, "type": "defi", "error": str(e)}


# ── Stock fetch ───────────────────────────────────────────────────────────────


def _ttm_sum(df, *labels) -> Optional[float]:
    """Sum 4 most recent quarterly values for the first matching label."""
    if df is None or df.empty:
        return None
    for label in labels:
        if label in df.index:
            row = df.loc[label]
            vals = []
            for v in row.iloc[:4]:
                try:
                    f = float(v)
                    if f == f:  # not NaN
                        vals.append(f)
                except (TypeError, ValueError):
                    pass
            if vals:
                return sum(vals)
    return None


def _fetch_stock_row(ticker: str) -> dict:
    """Sync yfinance fetch → normalized row dict. Call via run_in_executor."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        try:
            q_fin = t.quarterly_financials
        except Exception:
            q_fin = None
        try:
            q_cf = t.quarterly_cashflow
        except Exception:
            q_cf = None

        rev_ttm = _ttm_sum(q_fin, "Total Revenue")
        net_inc_ttm = _ttm_sum(q_fin, "Net Income", "Net Income Common Stockholders")
        fcf_ttm = _ttm_sum(q_cf, "Free Cash Flow")
        if fcf_ttm is None:
            op_cf = _ttm_sum(
                q_cf,
                "Operating Cash Flow",
                "Cash Flow From Continuing Operating Activities",
            )
            capex = _ttm_sum(q_cf, "Capital Expenditure", "Capital Expenditures")
            if op_cf is not None and capex is not None:
                fcf_ttm = op_cf + capex  # capex stored as negative
            elif op_cf is not None:
                fcf_ttm = op_cf

        mcap = info.get("marketCap")
        pe = info.get("trailingPE")
        ps = info.get("priceToSalesTrailing12Months")
        mcap_rev = (
            _safe_div(mcap, rev_ttm) if (mcap and rev_ttm) else (ps if ps else None)
        )

        return {
            "asset": info.get("longName") or info.get("shortName") or ticker.upper(),
            "type": "stock",
            "mcap": mcap,
            "rev_ttm": rev_ttm,
            "net_or_prot": net_inc_ttm,
            "mcap_rev": mcap_rev,
            "pe": pe,
            "fcf_or_tvl": fcf_ttm,
            "fcf_label": "FCF",
            "error": None,
        }
    except Exception as e:
        return {"asset": ticker.upper(), "type": "stock", "error": str(e)}


# ── main ──────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    now = datetime.now(timezone.utc)
    loop = asyncio.get_event_loop()

    # 1. Load DefiLlama protocol list (used for classification + slug resolution)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://api.llama.fi/protocols")
        r.raise_for_status()
        all_protocols = r.json()

    # 2. Classify each asset: defi vs stock
    #    - uppercase 1-5 letter strings default to stock unless there is an exact slug match
    #    - everything else tries _find_slug; if a protocol matches, it's defi
    #    - asset_types overrides both rules
    classified: Dict[str, str] = {}
    for asset in config.assets:
        override = (
            config.asset_types.get(asset)
            or config.asset_types.get(asset.upper())
            or config.asset_types.get(asset.lower())
        )
        if override:
            classified[asset] = override.lower()
        elif _looks_like_ticker(asset):
            # Ambiguous by shape, so only a name it matches exactly (or a
            # versioned slug of that name) makes it a protocol.
            named = _protocol_named_exactly(asset, all_protocols)
            classified[asset] = "defi" if named is not None else "stock"
        else:
            # Longer or punctuated input: the fuller search, but never the fuzzy
            # fallback — a near-miss cannot decide what KIND of thing this is.
            proto, _ = _find_slug(asset, all_protocols, fuzzy=False)
            classified[asset] = "defi" if proto is not None else "stock"

    defi_assets = [a for a in config.assets if classified[a] == "defi"]
    stock_assets = [a for a in config.assets if classified[a] == "stock"]

    # 3. Fetch all in parallel: async httpx for DeFi, run_in_executor for yfinance
    async with httpx.AsyncClient(timeout=60) as client:
        # One parent-record read for the whole table, lazily (FA-1).
        parents = _ParentIndex()
        all_coros = [
            _fetch_defi_row(client, a, all_protocols, parents) for a in defi_assets
        ] + [loop.run_in_executor(None, _fetch_stock_row, a) for a in stock_assets]
        raw_results = await asyncio.gather(*all_coros, return_exceptions=True)

    rows = []
    for res in raw_results:
        if isinstance(res, Exception):
            rows.append({"asset": "?", "type": "?", "mcap": None, "error": str(res)})
        else:
            rows.append(res)

    # 4. Sort by mcap descending (None → end)
    rows.sort(key=lambda x: (x.get("mcap") or -1), reverse=True)

    # 5. Build table rows
    columns = [
        "Asset",
        "Type",
        "MCap",
        "Rev TTM",
        "Net Inc / Protocol Rev",
        "MCap/Rev",
        "P/E",
        "FCF / TVL",
    ]
    table_data = []
    for row in rows:
        if row.get("error"):
            table_data.append(
                {
                    "Asset": row.get("asset", "?"),
                    "Type": (row.get("type") or "?").upper(),
                    "MCap": "N/A",
                    "Rev TTM": "N/A",
                    "Net Inc / Protocol Rev": "N/A",
                    "MCap/Rev": "N/A",
                    "P/E": "N/A",
                    "FCF / TVL": "N/A",
                }
            )
        else:
            table_data.append(
                {
                    "Asset": row["asset"],
                    "Type": row["type"].upper(),
                    "MCap": _fmt_usd(row.get("mcap")),
                    "Rev TTM": _fmt_usd(row.get("rev_ttm")),
                    "Net Inc / Protocol Rev": _fmt_usd(row.get("net_or_prot")),
                    "MCap/Rev": _fmt_mult(row.get("mcap_rev")),
                    "P/E": _fmt_mult(row.get("pe")),
                    "FCF / TVL": _fmt_usd(row.get("fcf_or_tvl")),
                }
            )

    # 6. Commentary: cheapest on MCap/Rev + highest stock net margin
    valid = [r for r in rows if not r.get("error")]
    with_mcap_rev = [r for r in valid if r.get("mcap_rev") is not None]
    cheapest = (
        min(with_mcap_rev, key=lambda x: float(x["mcap_rev"]))
        if with_mcap_rev
        else None
    )

    stock_with_margin = [
        r
        for r in valid
        if r.get("type") == "stock"
        and r.get("net_or_prot") is not None
        and r.get("rev_ttm")
        and r["rev_ttm"] != 0
    ]
    for r in stock_with_margin:
        r["_net_margin"] = r["net_or_prot"] / r["rev_ttm"]
    highest_margin = (
        max(stock_with_margin, key=lambda x: x["_net_margin"])
        if stock_with_margin
        else None
    )

    commentary_parts = []
    if cheapest:
        commentary_parts.append(
            f"**Cheapest on MCap/Rev:** {cheapest['asset']} ({_fmt_mult(cheapest['mcap_rev'])})"
        )
    if highest_margin:
        pct = highest_margin["_net_margin"] * 100
        commentary_parts.append(
            f"**Highest net margin (stocks):** {highest_margin['asset']} ({pct:.1f}%)"
        )
    commentary = (
        " · ".join(commentary_parts)
        if commentary_parts
        else "_Insufficient data for summary._"
    )

    # 7. Report
    errors = [r for r in rows if r.get("error")]

    builder = ReportBuilder("Peer Comparison")
    builder.source("routine", "peer_comparison")
    builder.tags(["fundamentals", "comparison", "defi", "equity"])

    builder.section(
        "01 / COMPARISON TABLE",
        f"{now.strftime('%Y-%m-%d %H:%M UTC')} · {len(rows)} assets "
        f"({len(defi_assets)} DeFi, {len(stock_assets)} stocks)",
    )
    builder.table(table_data, columns)
    builder.markdown(commentary)

    builder.section("02 / COLUMN GUIDE", "")
    builder.markdown(
        "- **Rev TTM** — DeFi: gross fees paid by users (TTM); Stock: total revenue (TTM)\n"
        "- **Net Inc / Protocol Rev** — DeFi: fees retained by the protocol (TTM); Stock: net income (TTM)\n"
        "- **MCap/Rev** — DeFi: MCap ÷ protocol revenue (falls back to fees if unavailable); Stock: MCap ÷ revenue ≈ P/S\n"
        "- **P/E** — trailing P/E for stocks; N/A for DeFi protocols\n"
        "- **FCF / TVL** — DeFi: total value locked; Stock: free cash flow (TTM)\n"
        "- DeFi MCap sources: DefiLlama protocols list → protocol detail page → CoinGecko"
    )

    if errors:
        builder.section("03 / FETCH ERRORS", "Assets that could not be retrieved")
        for err in errors:
            builder.markdown(f"- **{err.get('asset', '?')}**: {err['error']}")

    builder.section("04 / META", "")
    builder.kpi("Assets", ", ".join(config.assets))
    builder.kpi("DeFi Data", "DefiLlama + CoinGecko (mcap fallback)")
    builder.kpi("Stock Data", "Yahoo Finance (yfinance)")
    builder.kpi("Fetched At", now.strftime("%Y-%m-%d %H:%M UTC"))

    builder.manual_order()
    report_id = await builder.save()

    lines = [f"**Peer Comparison** — {now.strftime('%Y-%m-%d')}"]
    for row in rows:
        if row.get("error"):
            lines.append(f"• {row.get('asset', '?')} — ERROR: {row['error']}")
        else:
            lines.append(
                f"• {row['asset']} ({row['type'].upper()}) | "
                f"MCap: {_fmt_usd(row.get('mcap'))} | "
                f"Rev TTM: {_fmt_usd(row.get('rev_ttm'))} | "
                f"MCap/Rev: {_fmt_mult(row.get('mcap_rev'))}"
            )
    lines.append("")
    lines.append(commentary)
    lines.append(f"Report → {report_id}")
    return "\n".join(lines)
