"""A bot deploy is bounded by the capital it trades, not only the cap it declares.

The gate bounded ``max_global_drawdown_quote`` — a figure the model states in the
call — and nothing about the position being opened, because a bot's size is not
in the call: it names saved controller configs and the money lives in those, on
the API server. Two findings fell out of that one gap.

**DT-2**: a $60 controller deployed cleanly under a $10 session position limit
and opened about $29.55 of exposure. The declared cap was within the limit, so
the gate approved; nothing ever compared the limit against the money.

**MM-4**: ``pmm_mister_operator`` shipped ``total_amount_quote: 500`` under
``max_position_size_quote: 600`` and passed the deploy its *limit* as the cap. A
kill switch at 600 against 500 of capital can only fire once the whole position
is gone — and it passed the cap-versus-limit check, because 600 was within 600.
No single figure catches that: it is wrong only relative to the capital.

So the deploy's capital is now resolved from the server before the pure check
judges it, the same shape ``_planned_amount_quote`` already had for executor
creates, and both comparisons are made against it.
"""

import asyncio

import pytest

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    _deployed_capital_quote,
    auto_approve_with_risk_check,
)

OPTIONS = [{"optionId": "allow", "kind": "allow_once"}]


class _Controllers:
    """The API's controller-config endpoint, as the gate uses it."""

    def __init__(self, sizes: dict, fail: bool = False):
        self.sizes = sizes
        self.fail = fail
        self.asked: list[str] = []

    async def get_controller_config(self, config_id: str):
        self.asked.append(config_id)
        if self.fail:
            raise RuntimeError("the API is unreachable")
        if config_id not in self.sizes:
            raise RuntimeError("404 config not found")
        return {"total_amount_quote": self.sizes[config_id]}


class _Client:
    def __init__(self, sizes: dict, fail: bool = False):
        self.controllers = _Controllers(sizes, fail)


def _deploy(limit: float, sizes: dict | None, fail: bool = False, **args) -> dict:
    """One deploy through the gate, with ``limit`` as the session's ceiling."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=limit))
    gate = auto_approve_with_risk_check(
        engine,
        RiskState(),
        execution_mode="loop",
        price_client=_Client(sizes, fail) if sizes is not None else None,
    )
    call = {
        "tool": "manage_bots",
        "input": {"action": "deploy", "bot_name": "b", **args},
    }
    return asyncio.run(gate(call, OPTIONS))


def _outcome(result: dict) -> str:
    return result["outcome"]["outcome"]


# ── DT-2: the capital, not the declared cap ──────────────────────────────────


def test_a_controller_larger_than_the_session_limit_is_refused():
    """The regression, at the numbers QA actually deployed."""
    result = _deploy(
        10, {"cfg": 60}, controllers_config=["cfg"], max_global_drawdown_quote=5
    )

    assert _outcome(result) == "cancelled"
    assert "$60.00" in result["reason"]
    assert "$10.00" in result["reason"]


def test_the_configs_of_one_deploy_are_summed():
    """A deploy takes a list, and all of them run in the same bot."""
    result = _deploy(
        500,
        {"a": 300, "b": 300},
        controllers_config=["a", "b"],
        max_global_drawdown_quote=100,
    )

    assert _outcome(result) == "cancelled"
    assert "$600.00" in result["reason"]


def test_a_deploy_inside_the_limit_goes_through():
    """The gate bounds the deploy; it does not forbid deploying."""
    result = _deploy(
        500, {"cfg": 500}, controllers_config=["cfg"], max_global_drawdown_quote=100
    )

    assert _outcome(result) == "selected"


# ── MM-4: a cap above the capital it guards ──────────────────────────────────


def test_a_cap_above_the_capital_is_refused():
    """The shipped pmm_mister_operator numbers: 600 against 500 of capital."""
    result = _deploy(
        600, {"cfg": 500}, controllers_config=["cfg"], max_global_drawdown_quote=600
    )

    assert _outcome(result) == "cancelled"
    assert "not below" in result["reason"]


def test_a_cap_equal_to_the_capital_is_refused():
    """The same degenerate switch, named rather than waved through for being
    arithmetically tidy: it fires once everything is gone."""
    result = _deploy(
        500, {"cfg": 500}, controllers_config=["cfg"], max_global_drawdown_quote=500
    )

    assert _outcome(result) == "cancelled"
    assert "not below" in result["reason"]


def test_the_declared_cap_check_still_runs_first():
    """A cap over the session limit keeps its own message — the capital checks
    are added to that rule, not substituted for it."""
    result = _deploy(
        500, {"cfg": 500}, controllers_config=["cfg"], max_global_drawdown_quote=5000
    )

    assert _outcome(result) == "cancelled"
    assert "position limit" in result["reason"]


def test_a_deploy_with_no_cap_at_all_is_still_refused():
    """MM-1's check, unchanged by any of this."""
    result = _deploy(500, {"cfg": 100}, controllers_config=["cfg"])

    assert _outcome(result) == "cancelled"
    assert "max_global_drawdown_quote" in result["reason"]


# ── what happens when the capital cannot be read ─────────────────────────────


@pytest.mark.parametrize(
    "sizes,fail,label",
    [
        ({}, False, "a config that does not exist"),
        ({"cfg": 100}, True, "an unreachable API"),
        ({"cfg": 0}, False, "a config declaring no size"),
    ],
)
def test_an_unreadable_capital_is_refused_not_assumed(sizes, fail, label):
    """An unreadable size is not a small one.

    The same rule the executor creates follow when they cannot be priced: the
    gate refuses rather than approving a number it does not have.
    """
    result = _deploy(
        500, sizes, fail, controllers_config=["cfg"], max_global_drawdown_quote=100
    )

    assert _outcome(result) == "cancelled", label
    assert "capital could not be read" in result["reason"]


def test_no_client_refuses_a_deploy_that_names_configs():
    result = _deploy(
        500, None, controllers_config=["cfg"], max_global_drawdown_quote=100
    )

    assert _outcome(result) == "cancelled"
    assert "no API client" in result["reason"]


def test_a_deploy_naming_no_configs_is_left_to_the_tool():
    """Not the same question as "could not be read".

    There is no capital to bound rather than capital that could not be read, and
    ``manage_bots`` answers such a call with "Error: 'controllers_config' is
    required" before reaching the API — so refusing it here would guard nothing.
    """
    assert (
        asyncio.run(
            _deployed_capital_quote({"action": "deploy", "bot_name": "b"}, None)
        )
        is None
    )

    result = _deploy(500, None, max_global_drawdown_quote=100)
    assert _outcome(result) == "selected"


# ── everything else is untouched ─────────────────────────────────────────────


@pytest.mark.parametrize("action", ["status", "logs", "stop_bot", "stop_controllers"])
def test_other_bot_actions_resolve_no_configs(action):
    """Only a deploy names controller configs; nothing else pays for a lookup."""
    engine = RiskEngine(RiskLimits())
    client = _Client({})
    gate = auto_approve_with_risk_check(
        engine, RiskState(), execution_mode="loop", price_client=client
    )
    call = {"tool": "manage_bots", "input": {"action": action, "bot_name": "b"}}

    assert _outcome(asyncio.run(gate(call, OPTIONS))) == "selected"
    assert client.controllers.asked == []


def test_the_pure_check_without_a_capital_is_unchanged():
    """Every existing caller passes no capital and keeps the call-only rule."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    call = {
        "tool": "manage_bots",
        "input": {
            "action": "deploy",
            "bot_name": "b",
            "controllers_config": ["cfg"],
            "max_global_drawdown_quote": 100,
        },
    }

    assert engine.check_bot_action(call, RiskState()) == (True, "")


# ── the shipped loops no longer ask for what the gate refuses ────────────────


@pytest.mark.parametrize("loop", ["pmm_mister_operator", "hip_3_mm_operator"])
def test_the_mm_loops_ceiling_is_not_above_the_capital_it_bounds(loop):
    """MM-4 at its source. Both loops shipped a 600 ceiling over 500 of capital,
    so the operator loop handed its deploy a cap the gate now refuses."""
    from pathlib import Path

    import yaml

    text = Path(f"agents/market_making_expert/loops/{loop}/loop.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    config = front["default_config"]

    assert (
        config["risk_limits"]["max_position_size_quote"] <= config["total_amount_quote"]
    )


def test_the_operator_loop_deploys_with_a_cap_below_its_capital():
    """The cap it passes has to be a fraction of the capital, not the ceiling."""
    from pathlib import Path

    import yaml

    path = Path("agents/market_making_expert/loops/pmm_mister_operator/loop.md")
    text = path.read_text()
    config = yaml.safe_load(text.split("---")[1])["default_config"]

    assert config["max_global_drawdown_quote"] < config["total_amount_quote"]
    # ...and the playbook tells the model to use it, rather than the limit it
    # used to pass.
    assert "max_global_drawdown_quote from [CURRENT CONFIG]" in text
    assert "<max_position_size_quote from risk_limits>" not in text
