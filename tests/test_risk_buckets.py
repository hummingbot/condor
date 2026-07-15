"""Phase 3 acceptance (§6.1): disjoint exposure buckets, denomination
fail-closed conversion, and the risk-reducing exemption."""

from types import SimpleNamespace

import pytest

from condor.executors import ops


def _record(status="ACTIVE", orders=None, config=None, agent_id="acme_1"):
    cfg = {
        "type": "position_spot",
        "base_token": "WIF",
        "quote_token": "SOL",
        "amount_quote": "10",
        **(config or {}),
    }
    return SimpleNamespace(
        id="e1",
        type=cfg["type"],
        status=status,
        agent_slug="acme",
        agent_id=agent_id,
        config=cfg,
        state={"orders": orders or []},
    )


def _order(role, side, req, unit="quote", fb="0", fq="0", status="OPEN", fees=None):
    return {
        "venue_order_id": f"v-{role}-{side}-{req}-{fb}-{fq}-{status}",
        "role": role,
        "side": side,
        "requested_qty": req,
        "requested_unit": unit,
        "cumulative_filled_base_qty": fb,
        "cumulative_filled_quote_qty": fq,
        "fees_by_asset": fees or {},
        "status": status,
    }


# -- disjoint buckets: exposure must not jump across the order lifecycle --


def test_exposure_stable_across_order_lifecycle():
    # SUBMITTING (no landed orders): reservation at declared size (10 quote)
    r_submitting = _record(orders=[])
    e0 = ops._record_exposure(r_submitting)
    assert e0 == pytest.approx(10.0)

    # OPEN, nothing filled: bucket 3 remainder = full requested (10 quote)
    r_open = _record(orders=[_order("entry", "buy", "10", fq="0")])
    e1 = ops._record_exposure(r_open)
    assert e1 == pytest.approx(10.0)

    # Partially filled 4/10: bucket 2 = 4, bucket 3 = 6 → total still 10
    r_partial = _record(orders=[_order("entry", "buy", "10", fq="4")])
    e2 = ops._record_exposure(r_partial)
    assert e2 == pytest.approx(10.0)

    # FILLED: all bucket 2
    r_filled = _record(orders=[_order("entry", "buy", "10", fq="10", status="FILLED")])
    e3 = ops._record_exposure(r_filled)
    assert e3 == pytest.approx(10.0)

    # Exit fills reduce inventory
    r_exited = _record(
        orders=[
            _order("entry", "buy", "10", fq="10", status="FILLED"),
            _order("exit", "sell", "10", fq="6", status="FILLED"),
        ]
    )
    assert ops._record_exposure(r_exited) == pytest.approx(4.0)


# -- denomination (§6.1): convert at 1 when quoted in it; else fail closed --


def _agent(denom):
    return SimpleNamespace(denomination=denom, risk_limits={"max_open_executors": 5})


def test_sol_denominated_agent_sol_quoted_converts_at_1():
    ops._assert_denomination_convertible(
        _agent("SOL"), {"base_token": "WIF", "quote_token": "SOL"}
    )


def test_usd_family_equivalence():
    ops._assert_denomination_convertible(_agent("USD"), {"quote_token": "USDC"})
    ops._assert_denomination_convertible(_agent("USDC"), {})  # perp default USDC


def test_cross_denomination_fails_closed():
    with pytest.raises(ops.ExecutorOpError) as ei:
        ops._assert_denomination_convertible(
            _agent("USDC"), {"base_token": "WIF", "quote_token": "SOL"}
        )
    assert ei.value.status == 422
    assert "fails closed" in ei.value.message


# -- risk-reducing exemption (§6.1) --


def _long_10(extra_orders=None):
    """A scope holding +10 WIF (long), optionally with live opposite orders."""
    orders = [_order("entry", "buy", "10", unit="base", fb="10", fq="100",
                     status="FILLED")]
    orders += extra_orders or []
    return [_record(orders=orders)]


def _sell_cfg(amount):
    return {
        "base_token": "WIF",
        "quote_token": "SOL",
        "side": "SELL",
        "amount": str(amount),
    }


def test_reducing_close_is_exempt():
    scope = _long_10()
    assert ops._is_risk_reducing(scope, _sell_cfg(10), "order_spot") is True
    assert ops._is_risk_reducing(scope, _sell_cfg(6), "order_spot") is True


def test_over_close_crossing_zero_not_exempt():
    scope = _long_10()
    assert ops._is_risk_reducing(scope, _sell_cfg(12), "order_spot") is False


def test_live_tp_consumes_reducing_capacity():
    """A long +10 with a resting TP sell-10 has ZERO remaining reducing
    capacity — a watchdog sell-10 must be rejected (cancel the TP first)."""
    tp = _order("protection", "sell", "10", unit="base", status="OPEN")
    scope = _long_10([tp])
    assert ops._is_risk_reducing(scope, _sell_cfg(10), "order_spot") is False
    # After the TP is cancelled, capacity returns.
    tp_cancelled = _order("protection", "sell", "10", unit="base", status="CANCELED")
    scope2 = _long_10([tp_cancelled])
    assert ops._is_risk_reducing(scope2, _sell_cfg(10), "order_spot") is True


def test_two_partial_closes_cannot_combine_into_short():
    """Two individually valid sell-6 closes against a long +10 cannot combine
    into a short −2: the first's live remainder reserves capacity."""
    live_sell6 = _order("exit", "sell", "6", unit="base", status="OPEN")
    scope = _long_10([live_sell6])
    assert ops._is_risk_reducing(scope, _sell_cfg(6), "order_spot") is False
    assert ops._is_risk_reducing(scope, _sell_cfg(4), "order_spot") is True


def test_risk_increasing_order_not_exempt():
    scope = _long_10()
    buy_more = {
        "base_token": "WIF", "quote_token": "SOL", "side": "BUY", "amount": "5",
    }
    assert ops._is_risk_reducing(scope, buy_more, "order_spot") is False


def test_over_cap_agent_can_still_reduce(monkeypatch):
    """§11: stop --close style reduction succeeds while the agent is already
    over its cap — the exemption bypasses both caps."""
    from types import SimpleNamespace as NS

    agent = NS(
        denomination="SOL",
        risk_limits={"max_open_executors": 1, "max_position_size_quote": 5},
    )
    monkeypatch.setattr(
        "condor.agents.agent.AgentStore",
        lambda: NS(get=lambda slug: agent),
    )
    scope = _long_10()  # exposure 100 quote — way over the 5 cap
    runtime = NS(store=NS(load_by_slug=lambda slug: scope))
    declaration = NS(max_notional_quote=100, max_loss_quote=100)

    # A reducing sell passes despite both caps being blown.
    ops._enforce_agent_caps(
        runtime, "acme", declaration, cfg=_sell_cfg(10), type_="order_spot"
    )

    # A risk-INCREASING order while over cap is still rejected.
    with pytest.raises(ops.ExecutorOpError) as ei:
        ops._enforce_agent_caps(
            runtime,
            "acme",
            declaration,
            cfg={"base_token": "WIF", "quote_token": "SOL", "side": "BUY",
                 "amount": "5"},
            type_="order_spot",
        )
    assert ei.value.status == 409
