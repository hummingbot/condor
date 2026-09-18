"""Core data provider: positions summary (net position per connector/pair)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from condor.fetchers.executors import describe_executor_error
from condor.fetchers.tracked_positions import fetch_tracked_positions

from . import register_provider
from .base import BaseProvider, ProviderResult

if TYPE_CHECKING:
    from condor.agents.ownership import OwnedBot

log = logging.getLogger(__name__)


class PositionsProvider(BaseProvider):
    name = "positions"
    is_core = True

    async def execute(
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        owned: list[OwnedBot] | None = None,
    ) -> ProviderResult:
        # bot_names is part of the provider contract but irrelevant here: positions
        # are queried by controller_id, not by bot.
        try:
            positions = await fetch_tracked_positions(
                client, controller_id=agent_id or None, strict=True
            )
        except Exception as e:
            # The summary is embedded in the tick prompt and snapshot, and the
            # raw exception carries the backend URL: keep it in the server log.
            log.warning("positions provider fetch failed", exc_info=True)
            _, message = describe_executor_error(e)
            return ProviderResult(
                name=self.name,
                data={"error": message},
                summary=f"Positions Summary: failed to fetch ({message})",
            )

        if not positions:
            label = f" [agent: {agent_id}]" if agent_id else ""
            return ProviderResult(
                name=self.name,
                data={"positions": []},
                summary=f"Positions Summary{label}: no open positions",
            )

        lines = [
            f"Positions Summary ({len(positions)})"
            + (f" [agent: {agent_id}]" if agent_id else "")
            + ":"
        ]
        for pos in positions:
            connector = pos.get("connector_name", "?")
            pair = pos.get("trading_pair", "?")
            side = pos.get("position_side", pos.get("side", "?"))
            amount = pos.get("net_amount_base", pos.get("amount", 0))
            entry = pos.get("buy_breakeven_price", pos.get("entry_price", 0))
            current = pos.get("current_price", 0)
            pnl = pos.get("unrealized_pnl_quote", pos.get("unrealized_pnl", 0))
            try:
                amount_f = f"{float(amount):.6f}"
                entry_f = f"${float(entry):,.4f}" if entry else "?"
                current_f = f"${float(current):,.4f}" if current else "?"
                pnl_f = f"${float(pnl):+.2f}" if pnl else "$0.00"
            except (ValueError, TypeError):
                amount_f, entry_f, current_f, pnl_f = (
                    str(amount),
                    str(entry),
                    str(current),
                    str(pnl),
                )
            lines.append(
                f"  {connector} {pair} {side} | amt={amount_f} entry={entry_f} now={current_f} pnl={pnl_f}"
            )

        return ProviderResult(
            name=self.name,
            data={"positions": positions},
            summary="\n".join(lines),
        )


register_provider(PositionsProvider())
