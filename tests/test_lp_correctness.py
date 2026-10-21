"""The LP operator's four correctness findings (LP-2, LP-4, LP-5, LP-6).

Each is a place where the playbook and the runtime disagreed about what was
possible, and the playbook was the one being followed.

**LP-2** — slot sizing came from the wallet balance ÷ slots, so
``total_amount_quote`` (the one number an operator sets to say how much this
strategy may trade) bounded nothing: a session declaring 1 SOL would commit
whatever the wallet held. And the gate's limits, which are quote-denominated,
were rendered with a "$" — so a 500 SOL ceiling was shown as ``$500``.

**LP-4** — ``lp_scanner`` queried GeckoTerminal's ``raydium``, which is the
constant-product AMM v4 and holds no CLMM positions, then built the connector
name by string concatenation. It found 11 SOL pools and none an ``lp_executor``
could open.

**LP-5** — the winddown policy mandated swapping leftover base tokens back, which
needs a MARKET create the shutdown gate refuses. The policy described a step that
could not happen.

**LP-6** — the orphan hint told every tick to close the position with
``manage_clmm``, which the tick seat does not mount.
"""

from pathlib import Path

import pytest
import yaml

from condor.agents.journal import render_risk_lines
from condor.agents.risk import RiskEngine, RiskLimits
from condor.pool_data import lp_provider_for_dex

LP_LOOP = "agents/solana_dex_lp_expert/loops/lp_slot_operator/loop.md"
LP_SHUTDOWN = "agents/solana_dex_lp_expert/shutdown.md"
LP_AGENT = "agents/solana_dex_lp_expert/AGENT.md"


def _front(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text().split("---")[1])["default_config"]


# ── LP-2: the budget bounds the slot, and the unit is named ──────────────────


def test_slot_sizing_is_bounded_by_the_declared_budget():
    """``total_amount_quote`` is the budget; the balance only bounds what is there."""
    body = Path(LP_LOOP).read_text()

    assert "min(total_amount_quote, usable quote_asset balance)" in body
    assert "never the wallet" in body


def test_a_dollar_quoted_strategy_still_reads_in_dollars():
    """No regression for the CEX strategies that quote in USDT."""
    state = RiskEngine(RiskLimits(max_position_size_quote=500.0)).get_state(None)

    assert "$500.00 limit" in render_risk_lines(state.to_dict())[0]


@pytest.mark.parametrize("quote", ["USD", "USDT", "USDC"])
def test_a_stablecoin_quote_reads_in_dollars_too(quote):
    state = RiskEngine(RiskLimits(), quote_asset=quote).get_state(None)

    assert "$" in render_risk_lines(state.to_dict())[0]


def test_a_sol_quoted_limit_is_not_rendered_as_dollars():
    """The LP-2 regression: 500 SOL shown as ``$500`` is three orders out."""
    state = RiskEngine(RiskLimits(max_position_size_quote=500.0), quote_asset="SOL")

    line = render_risk_lines(state.get_state(None).to_dict())[0]

    assert "SOL" in line
    assert "$" not in line


def test_the_unit_rides_on_the_risk_state():
    """Both renderers take only the state dict, so the unit has to be in it."""
    state = RiskEngine(RiskLimits(), quote_asset="sol").get_state(None)

    assert state.to_dict()["quote_asset"] == "SOL"


def test_a_risk_block_with_no_unit_still_renders():
    """``render_risk_lines`` keeps a default for every value: a failed dry run
    records ``risk_state={}`` and must still render."""
    assert render_risk_lines({})


# ── LP-4: the scanner asks for the concentrated venue ────────────────────────


def test_the_scanner_defaults_to_the_concentrated_raydium():
    from agents.solana_dex_lp_expert.routines.lp_scanner import Config

    assert "raydium-clmm" in Config().venues
    assert "raydium" not in Config().venues


@pytest.mark.parametrize(
    "dex,expected",
    [
        ("raydium-clmm", "raydium/clmm"),
        ("meteora", "meteora/clmm"),
        ("orca", "orca/clmm"),
        ("raydium", None),  # AMM v4 — no CLMM positions
        ("meteora-damm-v2", None),  # shares a brand, not the mechanics
    ],
)
def test_the_connector_comes_from_the_repos_own_vocabulary(dex, expected):
    """A dex id is not a connector name, and the API rejects a mismatch — so a
    guess here becomes a failed executor."""
    assert lp_provider_for_dex(dex, "solana") == expected


def test_the_scanner_uses_that_resolver_rather_than_string_building():
    source = Path("agents/solana_dex_lp_expert/routines/lp_scanner.py").read_text()

    assert "lp_provider_for_dex(" in source
    assert "f\"{c['dex']}/clmm\"" not in source


def test_a_pool_with_no_connector_is_dropped_not_returned():
    """A row the agent cannot act on is worse than no row."""
    source = Path("agents/solana_dex_lp_expert/routines/lp_scanner.py").read_text()

    assert "if not provider:" in source
    assert "no CLMM lp_provider" in source


@pytest.mark.parametrize("path", [LP_LOOP, LP_AGENT])
def test_the_playbooks_name_the_same_venue_as_the_scanner(path):
    assert "raydium-clmm" in Path(path).read_text()


# ── LP-5: the winddown stops describing an impossible step ───────────────────


def test_the_winddown_no_longer_mandates_a_swap_it_cannot_make():
    body = Path(LP_SHUTDOWN).read_text()

    assert "mandatory, not a dust sweep" not in body
    assert "Do not try to swap it back" in body


def test_it_says_why_rather_than_just_forbidding_it():
    """An agent told only "don't" will try once to find out."""
    body = Path(LP_SHUTDOWN).read_text()

    assert "winddown refuses every create" in body
    assert "no `execute_swap`" in body


def test_the_leftovers_are_still_reported():
    """The deliverable becomes an accurate list, not a zero balance — the
    liquidity removal, which is the safety-critical half, already happened."""
    body = Path(LP_SHUTDOWN).read_text()

    assert "send_notification" in body
    assert "still held as spot" in body


def test_the_winddown_gate_is_unchanged():
    """Deliberate: the policy was made honest rather than the gate widened.

    Allowing a create during the one mode that exists to stop exposure is a
    product decision with live-fire consequences, not a docs fix.
    """
    from condor.agents.risk import _shutdown_refusal

    refusal = _shutdown_refusal(
        "create_order_executor", {"side": 2, "execution_strategy": "MARKET"}
    )

    assert refusal
    assert "not a winddown action" in refusal


# ── LP-6: the tick escalates instead of calling a tool it lacks ──────────────


def test_the_tick_seat_still_does_not_mount_the_liquidity_tools():
    """The premise. If this ever changes, the hint below should change with it."""
    from mcp_servers.hummingbot_api.profiles import PROFILE_TOOLS

    assert "manage_clmm" not in PROFILE_TOOLS["tick"]
    assert "manage_amm" not in PROFILE_TOOLS["tick"]


def test_the_orphan_hint_no_longer_names_an_unmounted_tool():
    source = Path("condor/agents/providers/executors.py").read_text()
    hint = source[source.index("ORPHANED POSITION") :][:1400]

    assert 'manage_clmm(action="close"' not in hint


def test_the_orphan_hint_escalates():
    """What the tick CAN do: read the details, notify, journal."""
    source = Path("condor/agents/providers/executors.py").read_text()
    hint = source[source.index("ORPHANED POSITION") :][:1400]

    assert "ESCALATE" in hint
    assert "list_orphaned_positions()" in hint
    assert "send_notification" in hint


def test_the_hint_warns_against_marking_it_resolved():
    """``resolve_orphaned_position`` IS mounted on the tick, and calling it on a
    position still open on-chain would drop live exposure out of the warning —
    the one way a tick could make this worse rather than merely not better."""
    source = Path("condor/agents/providers/executors.py").read_text()
    hint = source[source.index("ORPHANED POSITION") :][:1400]

    assert "Do NOT" in hint and "resolve_orphaned_position" in hint
    assert "hides live exposure" in hint


# ── the connector is resolved before Gateway is asked (review follow-up) ────


def test_pool_info_is_asked_with_a_connector_gateway_knows():
    """The half that made the LP-4 fix worse than the bug.

    ``_enrich`` passed the GeckoTerminal dex id straight to Gateway as the
    ``connector``. With the old ``raydium`` default that happened to work; with
    the corrected ``raydium-clmm`` it does not — pool-info fails, the candidate
    is dropped, and a Raydium scan reports nothing usable even when it found
    eligible pools. The connector is now resolved first, and the dex id is kept
    for discovery only.
    """
    source = Path("agents/solana_dex_lp_expert/routines/lp_scanner.py").read_text()
    enrich = source[source.index("async def _enrich") :]
    enrich = enrich[: enrich.index("enriched = [")]

    assert 'connector=c["dex"]' not in enrich
    assert "connector = provider.split" in enrich
    # ...and resolved BEFORE the request, not after it.
    assert enrich.index("lp_provider_for_dex(") < enrich.index("get_pool_info(")


@pytest.mark.parametrize(
    "dex,connector", [("raydium-clmm", "raydium"), ("meteora", "meteora")]
)
def test_the_gateway_connector_is_the_providers_brand(dex, connector):
    assert lp_provider_for_dex(dex, "solana").split("/", 1)[0] == connector


def test_an_unusable_venue_costs_no_request():
    """Dropped before paying for a pool-info call that cannot succeed."""
    source = Path("agents/solana_dex_lp_expert/routines/lp_scanner.py").read_text()
    enrich = source[source.index("async def _enrich") :]

    assert enrich.index("if not provider:") < enrich.index("get_pool_info(")


# ── the autonomous limits are described honestly (review follow-up) ─────────


def test_the_grace_window_is_not_promised():
    """The limits fire on a crossing, which can happen inside
    ``out_of_range_max_sec`` and while the loop is still running — telling an
    operator their configured wait still governs exits would be false."""
    body = Path(LP_LOOP).read_text()

    assert "cannot pre-empt" not in body
    assert "can happen inside your `out_of_range_max_sec` grace window" in body
    assert "while this loop is still running" in body
