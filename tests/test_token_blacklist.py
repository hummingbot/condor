"""Code-enforced post-stop-loss token cooldown (memecoin falling-knife fix)."""

import time
from decimal import Decimal
from types import SimpleNamespace

from condor.executors.base import ExecutorStatus
from condor.executors.log import ExecutorLog
from condor.executors.position import PositionExecutor, PositionSpotConfig, PositionStates
from condor.agents.token_blacklist import stopped_out_reason

WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"


def _closed(store, slug, mint, opened_at, close_type="stop_loss"):
    ex = PositionExecutor(
        f"pos_{mint}_{int(opened_at)}",
        PositionSpotConfig(
            chain_network="solana-mainnet-beta", wallet_address=WALLET,
            base_token=mint, quote_token="SOL", amount_quote=Decimal("0.03"),
            agent_slug=slug, agent_id=f"{slug}_1"),
        SimpleNamespace(), store)
    ex.status = ExecutorStatus.CLOSED
    ex.state.state = PositionStates.COMPLETE
    ex.state.close_type = close_type
    ex.state.opened_at = opened_at
    store.save(ex)


def test_recent_stop_blocks_reentry(tmp_path):
    store = ExecutorLog(tmp_path)
    _closed(store, "memecoin_trender", "MINT_A", time.time() - 60)  # 1 min ago
    assert stopped_out_reason("memecoin_trender", "MINT_A", 4, store=store) is not None


def test_old_stop_expires(tmp_path):
    store = ExecutorLog(tmp_path)
    _closed(store, "memecoin_trender", "MINT_A", time.time() - 5 * 3600)  # 5h ago
    assert stopped_out_reason("memecoin_trender", "MINT_A", 4, store=store) is None


def test_take_profit_is_not_blacklisted(tmp_path):
    store = ExecutorLog(tmp_path)
    _closed(store, "memecoin_trender", "MINT_A", time.time() - 60, close_type="take_profit")
    assert stopped_out_reason("memecoin_trender", "MINT_A", 4, store=store) is None


def test_untouched_token_allowed(tmp_path):
    store = ExecutorLog(tmp_path)
    _closed(store, "memecoin_trender", "MINT_A", time.time() - 60)
    assert stopped_out_reason("memecoin_trender", "MINT_B", 4, store=store) is None


def test_other_agents_stop_does_not_block(tmp_path):
    store = ExecutorLog(tmp_path)
    _closed(store, "other_agent", "MINT_A", time.time() - 60)
    assert stopped_out_reason("memecoin_trender", "MINT_A", 4, store=store) is None
