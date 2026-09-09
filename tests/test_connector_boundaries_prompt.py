"""Agents must not go looking in Gateway for a native connector's problem.

Asked why a token showed $0.00 on xrpl, an agent called the Gateway config and container
tools five times, found Gateway returning 503, and concluded "the issue is that Gateway
is down, which is why FUZZY isn't showing a proper value in your portfolio" — plausible,
confidently stated, and wrong. xrpl is a native connector and never touches Gateway.

Nothing in the shared prompt distinguished the two kinds of connector, while the tool
surface pushes the other way: manage_gateway_config describes itself as covering
"chains, networks, tokens, connectors, pools, wallets", and nearly every other
DEX-flavoured tool really is Gateway-mediated. So the model generalises "xrpl is a DEX"
into "DEXs go through Gateway". The only place the boundary was written down was one
specialist agent's own AGENT.md, which no other persona ever sees.
"""
from condor.agents.prompts import BASE_PROMPT_COMMON


def test_the_boundary_is_stated_in_the_shared_prompt():
    assert "CONNECTOR BOUNDARIES:" in BASE_PROMPT_COMMON


def test_it_names_the_gateway_tools_not_to_reach_for():
    """A rule the model cannot map onto a tool name is a rule it will not follow."""
    assert "manage_gateway_config" in BASE_PROMPT_COMMON
    assert "manage_gateway_container" in BASE_PROMPT_COMMON


def test_it_names_native_connectors_explicitly():
    boundaries = BASE_PROMPT_COMMON.split("CONNECTOR BOUNDARIES:")[1]
    for connector in ("xrpl", "binance", "kraken"):
        assert connector in boundaries


def test_the_gateway_venues_named_are_not_a_short_exhaustive_list():
    """Naming only some supported venues steers an agent away from Gateway for the rest,
    so the ones listed are followed by an explicit catch-all."""
    boundaries = BASE_PROMPT_COMMON.split("CONNECTOR BOUNDARIES:")[1]
    for venue in ("Meteora", "Orca", "Raydium", "Uniswap", "PancakeSwap"):
        assert venue in boundaries
    assert "any other pool-based venue" in boundaries


def test_it_gives_a_rule_for_telling_them_apart():
    boundaries = BASE_PROMPT_COMMON.split("CONNECTOR BOUNDARIES:")[1]
    assert "<chain>-<network>" in boundaries
    assert "list_connectors" in boundaries


def test_it_carries_the_xrpl_caveat_that_was_stranded_in_one_agent():
    """No candles endpoint — previously only xrpl_market_maker knew this."""
    boundaries = BASE_PROMPT_COMMON.split("CONNECTOR BOUNDARIES:")[1]
    assert "candles" in boundaries
    assert "explore_geckoterminal" in boundaries


def test_the_rest_of_the_shared_prompt_is_intact():
    for section in ("GENERAL:", "SKILLS & ROUTINES:", "MEMORY", "NOTIFICATIONS:"):
        assert section in BASE_PROMPT_COMMON
