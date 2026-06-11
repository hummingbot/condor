"""Regression tests for journal tick resolution and position_executor notional sizing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_servers.condor.tools.trading_agent import _resolve_journal_tick, journal_write
from mcp_servers.hummingbot_api.tools.executors import (
    _apply_notional_usd_to_amount,
    _apply_position_amount_from_trading_rules,
)


class TestJournalTickResolution:
    def test_explicit_tick_param(self):
        assert _resolve_journal_tick(7, "tick=7 entry_class=hold", "agent_1") == 7

    def test_parse_tick_from_text_when_param_zero(self):
        assert _resolve_journal_tick(
            0, "tick=7 entry_class=regime_adaptive_half_size pair=XRP-USD", "agent_1"
        ) == 7

    def test_journal_write_uses_text_tick(self, tmp_path: Path, monkeypatch):
        session_dir = tmp_path / "sessions" / "session_99"
        session_dir.mkdir(parents=True)
        (session_dir / "journal.md").write_text(
            "# Journal - test_agent_99\n\n## Summary\n\n## Decisions\n\n## Ticks\n"
            "- tick#6 | 2026-06-09 09:11 | actions=0 | hold\n\n## Executors\n\n## Snapshots\n"
        )
        agent_dir = tmp_path

        monkeypatch.setattr(
            "condor.trading_agent.journal.resolve_agent_dirs",
            lambda agent_id: (session_dir, agent_dir),
        )
        monkeypatch.setattr(
            "condor.trading_agent.engine.get_engine",
            lambda agent_id: None,
        )

        result = journal_write(
            "test_agent_99",
            "action",
            "tick=7 entry_class=hold pair=none",
            tick=0,
        )
        assert result == {"written": True}
        journal_text = (session_dir / "journal.md").read_text()
        assert "**#7**" in journal_text
        assert "**#0**" not in journal_text


class TestPositionExecutorSizing:
    def test_notional_usd_converts_with_live_price(self):
        client = MagicMock()
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"XRP-USD": 2.0}}
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "XRP-USD",
            "notional_usd": 200,
            "amount": 0.1735,
        }
        err, note = asyncio.run(_apply_notional_usd_to_amount(client, config))
        assert err is None
        assert config["amount"] == pytest.approx(100.0)
        assert "notional_usd=200" in note
        assert "notional_usd" not in config

    def test_wrong_manual_amount_rejected(self):
        client = MagicMock()
        client.connectors.get_trading_rules = AsyncMock(
            return_value={
                "XRP-USD": {
                    "min_base_amount_increment": 1,
                    "min_order_size": 1,
                    "min_notional_size": 10,
                }
            }
        )
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"XRP-USD": 2.0}}
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "XRP-USD",
            "amount": 0.1735,
        }
        err, note = asyncio.run(
            _apply_position_amount_from_trading_rules(client, config)
        )
        assert err is not None
        assert "notional_usd" in err
        assert "0.1735" in err

    def test_correct_amount_passes_rules(self):
        client = MagicMock()
        client.connectors.get_trading_rules = AsyncMock(
            return_value={
                "XRP-USD": {
                    "min_base_amount_increment": 1,
                    "min_order_size": 1,
                    "min_notional_size": 10,
                }
            }
        )
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"XRP-USD": 2.0}}
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "XRP-USD",
            "amount": 100,
        }
        err, note = asyncio.run(
            _apply_position_amount_from_trading_rules(client, config)
        )
        assert err is None
        assert config["amount"] == 100
