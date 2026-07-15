"""Unit tests for the per-agent append-only executor transaction log.

Exercises ExecutorLog in isolation (no runtime, no gateway) against the same
read surface the SQLite ExecutorStore exposed.
"""

from decimal import Decimal

from condor.executors.base import ExecutorStatus
from condor.executors.log import ExecutorLog
from condor.executors.order import OrderExecutor, OrderSpotConfig, OrderStates


class _DummyGateway:
    async def close(self):
        pass


def _swap(eid, log, *, agent_slug="", agent_id="", strategy="", **over):
    kw = dict(
        chain_network="solana-mainnet-beta",
        wallet_address="Wallet111",
        base_token="SOL",
        quote_token="USDC",
        amount=Decimal("0.01"),
        side="SELL",
        agent_slug=agent_slug,
        agent_id=agent_id,
        strategy=strategy,
    )
    kw.update(over)
    return OrderExecutor(eid, OrderSpotConfig(**kw), _DummyGateway(), log)


def _lines(log, slug):
    path = log._path_for_slug(slug)
    return path.read_text().strip().splitlines() if path.exists() else []


# -- roundtrip + mark ----------------------------------------------------------


def test_save_roundtrip_and_mark(tmp_path):
    log = ExecutorLog(tmp_path)
    ex = _swap("swap_1", log, agent_slug="trender", agent_id="trender_3")
    log.save(ex)

    rec = log.load("swap_1")
    assert rec is not None
    assert rec.status == "PENDING"
    assert rec.type == "order_spot"
    assert rec.agent_slug == "trender"
    assert rec.config["base_token"] == "SOL"
    assert [r.id for r in log.load_non_terminal()] == ["swap_1"]

    log.mark("swap_1", "CLOSED", "done")
    rec = log.load("swap_1")
    assert rec.status == "CLOSED"
    assert rec.close_reason == "done"
    assert log.load_non_terminal() == []


def test_mark_unknown_id_raises(tmp_path):
    log = ExecutorLog(tmp_path)
    try:
        log.mark("nope", "CLOSED")
        assert False, "expected KeyError"
    except KeyError:
        pass


# -- fold semantics ------------------------------------------------------------


def test_open_then_close_folds_config_from_open_state_last_wins(tmp_path):
    log = ExecutorLog(tmp_path)
    ex = _swap("swap_2", log, agent_slug="trender", agent_id="trender_3")

    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.RESTING
    log.save(ex)  # opened

    ex.status = ExecutorStatus.CLOSED
    ex.close_reason = "swap done"
    ex.state.state = OrderStates.DONE
    log.save(ex)  # closed

    # append-only: two physical lines, one folded record
    assert len(_lines(log, "trender")) == 2
    rec = log.load("swap_2")
    assert rec.status == "CLOSED"
    assert rec.close_reason == "swap done"
    assert rec.state["state"] == "DONE"          # last-wins
    assert rec.config["quote_token"] == "USDC"   # from the opener
    assert rec.created_at <= rec.updated_at


def test_mark_keeps_prior_state(tmp_path):
    log = ExecutorLog(tmp_path)
    ex = _swap("swap_3", log, agent_slug="trender", agent_id="trender_3")
    ex.status = ExecutorStatus.ACTIVE
    ex.state.state = OrderStates.RESTING
    log.save(ex)

    log.mark("swap_3", "FAILED", "watchdog: dead task")
    rec = log.load("swap_3")
    assert rec.status == "FAILED"
    assert rec.close_reason == "watchdog: dead task"
    assert rec.state["state"] == "RESTING"       # state survived the status-only mark
    assert rec.type == "order_spot"              # attribution survived too


# -- per-agent scoping + fan-out ----------------------------------------------


def test_load_by_agent_filters_within_slug(tmp_path):
    log = ExecutorLog(tmp_path)
    log.save(_swap("swap_a", log, agent_slug="trender", agent_id="trender_3"))
    log.save(_swap("swap_b", log, agent_slug="trender", agent_id="trender_4"))
    log.save(_swap("swap_c", log, agent_slug="mm", agent_id="mm_1"))

    # same slug file, filtered by exact run id
    ids3 = {r.id for r in log.load_by_agent("trender_3")}
    assert ids3 == {"swap_a"}
    # whole slug
    assert {r.id for r in log.load_by_slug("trender")} == {"swap_a", "swap_b"}
    # different slug is a separate file
    assert log._path_for_slug("mm").exists()
    assert {r.id for r in log.load_by_slug("mm")} == {"swap_c"}


def test_non_terminal_and_list_all_fan_out_across_slugs(tmp_path):
    log = ExecutorLog(tmp_path)
    log.save(_swap("swap_a", log, agent_slug="trender", agent_id="trender_3"))
    log.save(_swap("swap_c", log, agent_slug="mm", agent_id="mm_1"))
    log.mark("swap_c", "CLOSED", "done")

    non_terminal = {r.id for r in log.load_non_terminal()}
    assert non_terminal == {"swap_a"}                 # swap_c closed, excluded
    all_ids = {r.id for r in log.list_all()}
    assert all_ids == {"swap_a", "swap_c"}            # list_all includes closed


def test_agentless_executor_routes_to_manual_slug(tmp_path):
    log = ExecutorLog(tmp_path)
    log.save(_swap("swap_manual", log))  # no agent_slug / agent_id
    assert (tmp_path / "_manual" / "executors.jsonl").exists()
    assert {r.id for r in log.load_by_slug("_manual")} == {"swap_manual"}
    assert log.load("swap_manual").agent_slug == ""


# -- append-only invariant -----------------------------------------------------


def test_append_only_never_rewrites(tmp_path):
    log = ExecutorLog(tmp_path)
    ex = _swap("swap_x", log, agent_slug="trender", agent_id="trender_3")
    for _ in range(5):
        log.save(ex)          # 5 snapshots
    log.mark("swap_x", "CLOSED", "done")  # + 1 mark
    assert len(_lines(log, "trender")) == 6
    # every physical line is independently valid JSON (whole-line writes)
    import json

    for line in _lines(log, "trender"):
        assert json.loads(line)["id"] == "swap_x"
