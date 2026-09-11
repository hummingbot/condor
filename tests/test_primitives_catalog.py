"""The catalog is derived from the code, so it cannot lie (FEAT-052).

The point of these tests is the gate at the bottom: every callable the catalog
advertises must carry a non-empty docstring and an annotated signature. That is
what makes a derived index trustworthy — a fetcher that lands undocumented fails
CI here instead of silently reaching a model as a nameless line.

Sync tests: ``pytest-asyncio`` is a dev dependency but is not installed in this
venv, so anything async is driven with ``asyncio.run`` (same as
``test_code_runner.py``).
"""

import inspect

import pytest

from condor import primitives
from condor.primitives import _CATALOG, UNTYPED_PARAMS, catalog, describe


def _catalogued():
    """(ref, object) for every callable the catalog advertises."""
    for group in _CATALOG:
        mod = primitives._load(group)
        for name, obj in primitives._public_callables(mod):
            yield f"{group}.{name}", obj


# ── the index ──


def test_every_catalogued_module_imports():
    for group, path in _CATALOG.items():
        mod = primitives._load(group)
        assert mod.__name__ == path, group


def test_catalog_groups_the_whole_surface():
    text = catalog()

    for group in _CATALOG:
        assert f"\n{group}  (" in text, group
    assert "market_data.fetch_candles" not in text  # grouped, not flattened
    assert "  fetch_candles(client, connector_name, trading_pair" in text
    assert "Fetch candle data for a trading pair." in text


def test_catalog_lists_routines_with_their_call_form():
    text = catalog()

    assert "await call_routine(name, config)" in text
    # Every discoverable routine is in there, agent-prefixed ones included.
    names = {r["name"] for r in primitives._routine_entries()}
    assert names, "no routines discovered — the fixture repo should ship some"
    for name in names:
        assert f"\n  {name}" in text, name


def test_a_group_narrows_the_catalog():
    text = catalog("market_data")

    assert "fetch_candles(" in text
    assert "\nportfolio  (" not in text
    assert "\nroutines  (" not in text


def test_routines_is_a_group_of_its_own():
    text = catalog("routines")

    assert "await call_routine(name, config)" in text
    assert "\nmarket_data  (" not in text


def test_an_unknown_group_names_the_real_ones():
    text = catalog("nope")

    assert "Unknown group 'nope'" in text
    assert "market_data" in text and "routines" in text


def test_the_signature_in_the_catalog_carries_defaults_not_types():
    line = next(
        line
        for line in catalog("market_data").splitlines()
        if line.strip().startswith("fetch_current_price(")
    )

    assert "trading_pair=''" in line
    assert "strict=False" in line
    assert ": str" not in line  # types belong to describe(), not the index


def test_a_wide_signature_is_elided_rather_than_wrapped():
    line = next(
        line
        for line in catalog("market_data").splitlines()
        if line.strip().startswith("fetch_historical_candles(")
    )

    assert ", ...)" in line
    assert len(line) < 200


# ── describe ──


def test_describe_returns_the_live_signature_and_full_docstring():
    text = describe("market_data.fetch_historical_candles")

    from condor.fetchers.market_data import fetch_historical_candles

    assert str(inspect.signature(fetch_historical_candles)) in text
    assert inspect.getdoc(fetch_historical_candles) in text
    assert "from condor.fetchers.market_data import fetch_historical_candles" in text
    assert text.startswith("condor.fetchers.market_data.fetch_historical_candles")


def test_describe_marks_a_coroutine_as_async():
    assert "async def fetch_candles(" in describe("market_data.fetch_candles")
    assert "def find_rate(" in describe("rates.find_rate")
    assert "async def find_rate(" not in describe("rates.find_rate")


def test_describe_reaches_a_re_exporting_package():
    text = describe("reports.ReportBuilder")

    assert "class ReportBuilder(" in text
    assert "from condor.reports import ReportBuilder" in text


def test_describe_a_routine_returns_its_config_fields():
    text = describe("routine:arb_check")

    assert "routine:arb_check" in text and "one-shot" in text
    assert "trading_pair: str = 'SOL-USDC'" in text
    assert "Trading pair" in text
    assert 'await call_routine("arb_check"' in text


def test_describe_a_routine_accepts_the_bare_name_of_an_agent_routine(tmp_path):
    """Seeded rather than borrowed from the checkout.

    This used to read whichever agent routines the developer happened to have on
    disk, which is exactly the isolation FEAT-115 closed: with both agent roots
    repointed at tmp dirs there is nothing to borrow, so the fixture writes the
    one routine the assertion needs.
    """
    from condor.memory.paths import agent_home
    from condor.routine_store import get_routine_store

    routines_dir = agent_home("scout") / "routines"
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "pulse_check.py").write_text(
        "from pydantic import BaseModel\n\n\n"
        "class Config(BaseModel):\n"
        '    """Check the pulse."""\n\n\n'
        "async def run(config, context):\n"
        "    return 'ok'\n",
        "utf-8",
    )
    get_routine_store()._routines_cache = None

    entry = next(
        (r for r in primitives._routine_entries() if "/" in r["name"]),
        None,
    )
    assert entry is not None, "the seeded agent routine did not reach the catalog"

    bare = entry["name"].split("/")[-1]

    assert f"routine:{entry['name']}" in describe(f"routine:{bare}")


def test_an_unknown_ref_suggests_instead_of_raising():
    text = describe("market_data.fetch_candle")

    assert "Unknown ref" in text
    assert "market_data.fetch_candles" in text
    assert "catalog()" in text


def test_an_unknown_group_or_a_bare_word_is_not_an_exception():
    assert "Unknown ref" in describe("nonsense.thing")
    assert "Unknown ref" in describe("fetch_candles")
    assert "Unknown ref" in describe("")
    assert "Unknown ref" in describe("routine:no_such_routine")


# ── the gate that keeps the derived index honest ──


def test_every_catalogued_callable_is_documented():
    missing = [
        ref for ref, obj in _catalogued() if not (inspect.getdoc(obj) or "").strip()
    ]

    assert not missing, (
        "These are advertised by condor.primitives.catalog() with no docstring, "
        f"so they reach a caller as a bare name: {missing}"
    )


def test_every_catalogued_callable_has_a_typed_signature():
    untyped = {}
    for ref, obj in _catalogued():
        sig = inspect.signature(obj)
        bad = [
            p.name
            for p in sig.parameters.values()
            if p.annotation is p.empty
            and p.name not in UNTYPED_PARAMS
            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
        if bad:
            untyped[ref] = bad

    assert not untyped, (
        "Catalogued parameters with no annotation. `client` is exempt by "
        f"convention (see UNTYPED_PARAMS); these are not: {untyped}"
    )


def test_the_full_catalog_stays_cheap_enough_to_read_on_impulse():
    # ~4 chars per token. The whole trade of this feature is a read-on-demand
    # index instead of resident tool schema, and it stops being a trade if the
    # index costs what the schemas did (~6.6k tokens).
    approx_tokens = len(catalog()) / 4

    assert approx_tokens < 4000, f"catalog() is ~{approx_tokens:.0f} tokens"
    assert len(catalog("market_data")) / 4 < 500
