"""Core data provider: this session's on-chain executors and its Aomi wallet.

The ``onchain_executor`` runs on the Hummingbot API and executes through the
Aomi Pipeline, so its state lives in two places: the executor record (status,
close type, tx hashes, the error that ended it) and the wallet it signed from
(native balance, nonce). Both land here, in the same shape the other core
providers use, so the agent reads its DeFi footprint next to its CEX positions.

The durable lending ledger is checked independently of recent executor history.
Direct Aomi wallet reads remain optional when both histories are empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from condor.fetchers.executors import extract_executors_list

from . import register_provider
from .base import BaseProvider, ProviderResult

log = logging.getLogger(__name__)

MAX_LISTED = 10

# Native token symbol per chain, for the wallet line. Anything else prints
# ``native`` rather than guessing.
NATIVE_SYMBOL = {
    1: "ETH",
    10: "ETH",
    56: "BNB",
    137: "POL",
    8453: "ETH",
    42161: "ETH",
    59144: "ETH",
}


def _short_hash(value: Any) -> str:
    text = str(value or "")
    return f"{text[:10]}…{text[-4:]}" if len(text) > 16 else text


def _executor_line(ex: dict) -> str:
    config = ex.get("config") if isinstance(ex.get("config"), dict) else {}
    info = ex.get("custom_info") if isinstance(ex.get("custom_info"), dict) else {}
    parts = [
        f"  {str(ex.get('executor_id') or ex.get('id') or '')[:12]}",
        f"chain={config.get('chain_id', '?')}",
        f"mode={config.get('mode', '?')}",
        f"status={ex.get('status') or '?'}",
        f"close={ex.get('close_type') or '-'}",
    ]
    hashes = info.get("tx_hashes")
    if isinstance(hashes, list) and hashes:
        parts.append("tx=" + ",".join(_short_hash(h) for h in hashes))
    error = info.get("error")
    if isinstance(error, dict):
        reason = error.get("reason") or error.get("message")
        if reason:
            parts.append(f"err={reason}")
    return " ".join(parts)


class DefiPositionsProvider(BaseProvider):
    name = "defi_positions"
    is_core = True

    async def execute(
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        since: float = 0.0,
    ) -> ProviderResult:
        # bot_names is part of the provider contract but irrelevant here: on-chain
        # executors are attributed by controller_id, not by bot.
        label = f" [agent: {agent_id}]" if agent_id else ""
        try:
            result = await client.executors.search_executors(
                executor_types=["onchain_executor"],
                controller_ids=[agent_id] if agent_id else None,
                limit=50,
            )
        except Exception as e:  # noqa: BLE001 - a provider degrades, never raises
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"DeFi Positions{label}: failed to fetch ({e})",
            )

        executors = [
            ex for ex in extract_executors_list(result) if isinstance(ex, dict)
        ]
        lending = await self._lending_positions(client, agent_id)
        if (
            not executors
            and not config.get("defi_positions")
            and lending.get("status") == "available"
            and not lending["positions"]
        ):
            return ProviderResult(
                name=self.name,
                data={"executors": [], "wallet": None, "lending": lending},
                summary=f"DeFi Positions{label}: none",
            )

        lines = [f"DeFi Positions ({len(executors)} on-chain executors){label}:"]
        for ex in executors[:MAX_LISTED]:
            lines.append(_executor_line(ex))
        if len(executors) > MAX_LISTED:
            lines.append(f"  … {len(executors) - MAX_LISTED} more not shown")

        lines.extend(lending["lines"])
        wallet = await self._wallet(config, executors)
        lines.append(wallet["line"])

        return ProviderResult(
            name=self.name,
            data={"executors": executors, "wallet": wallet, "lending": lending},
            summary="\n".join(lines),
        )

    async def _lending_positions(self, client: Any, agent_id: str) -> dict:
        """Use full durable history, never infer remaining positions from recent txs."""
        try:
            response = await client.executors._get("/executors/lending/positions")
            if not isinstance(response, dict) or not isinstance(
                response.get("positions"), list
            ):
                raise ValueError("Invalid lending ledger response")
            rows = response["positions"]
            if any(
                not isinstance(row, dict)
                or not isinstance(row.get("controller_id"), str)
                or not row["controller_id"]
                for row in rows
            ):
                raise ValueError("Lending ledger is missing attribution")
            positions = [
                row for row in rows if not agent_id or row["controller_id"] == agent_id
            ]
            lines = [
                "Lending: controller contributions are not profit; receipt balances cover the entire wallet."
            ]
            for row in positions:

                def amount(field: str) -> str:
                    raw = row.get(field)
                    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw):
                        raise ValueError("Invalid lending amount")
                    decimals = row.get("decimals")
                    if decimals is None:
                        return f"{raw} raw units"
                    if type(decimals) is not int or not 0 <= decimals <= 255:
                        raise ValueError("Invalid lending precision")
                    digits = raw.lstrip("0") or "0"
                    if decimals:
                        digits = digits.zfill(decimals + 1)
                        digits = (
                            (digits[:-decimals] + "." + digits[-decimals:])
                            .rstrip("0")
                            .rstrip(".")
                        )
                    return digits

                unresolved = row.get("unresolved_executor_ids")
                if not isinstance(unresolved, list):
                    raise ValueError("Missing lending reconciliation state")
                balance = (
                    amount("wallet_receipt_balance_raw")
                    if row.get("balance_status") == "verified_wallet_balance"
                    and row.get("balance_scope") == "wallet"
                    else "unavailable"
                )
                lines.append(
                    f"  {row['controller_id']} account={row.get('account_name', '?')} "
                    f"chain={row.get('chain_id', '?')} asset={row.get('asset', '?')} "
                    f"wallet={row.get('wallet', '?')} pool={row.get('pool', '?')}: "
                    f"net contribution={amount('net_contributed_raw')}; "
                    f"wallet-wide receipt balance={balance}; "
                    f"pending supply={amount('pending_supply_raw')}; "
                    f"pending withdrawal={amount('pending_withdraw_raw')}; "
                    f"unresolved actions={len(unresolved)}"
                )
            if not positions:
                lines.append("  No recorded lending contributions for this controller.")
            lines.append(
                "Lending data is for reconciliation, not permission for automatic spending."
            )
            from ..lending import lending_exposure

            try:
                exposure = await lending_exposure(client, positions)
            except Exception:
                exposure = None
                lines.append(
                    "Lending quote exposure unavailable; automatic risk checks must pause."
                )
            return {
                "status": "available",
                "positions": positions,
                "lines": lines,
                "exposure_quote": exposure,
            }
        except Exception:
            # Old APIs and failed balance/history reads must never mean zero exposure.
            return {
                "status": "unavailable",
                "positions": None,
                "lines": [
                    "Lending history or balances unavailable. Do not assume zero exposure or allocate these funds."
                ],
            }

    async def _wallet(self, config: dict, executors: list[dict]) -> dict[str, Any]:
        """One line about the signing wallet, read through Aomi.

        Never fatal: Aomi being unconfigured, unreachable or unhappy about the
        address each become a line the agent can read, not a failed provider.
        """
        from condor.aomi_client import get_pipeline_client

        chain_id = int(config.get("chain_id") or 8453)
        address = str(config.get("wallet_address") or "").strip()
        if not address:
            for ex in executors:
                info = ex.get("custom_info")
                if isinstance(info, dict) and info.get("wallet_address"):
                    address = str(info["wallet_address"])
                    break

        wallet: dict[str, Any] = {"address": address or None, "chain_id": chain_id}
        pc = get_pipeline_client()
        if pc is None:
            wallet["line"] = "Wallet: Aomi not configured (AOMI_TOKEN unset)"
            return wallet
        try:
            if address:
                account = await pc.evm_account(address, chain_id)
                account = account if isinstance(account, dict) else {}
                symbol = NATIVE_SYMBOL.get(chain_id, "native")
                balance = account.get("balance_native", account.get("balance", "?"))
                nonce = account.get("nonce", "?")
                wallet.update(account=account)
                wallet["line"] = (
                    f"Wallet {address[:10]}… on chain {chain_id}: "
                    f"{balance} {symbol} (nonce {nonce})"
                )
            else:
                ctx = await pc.evm_context(chain_id)
                ctx = ctx if isinstance(ctx, dict) else {}
                block = ctx.get("block_number", ctx.get("block", "?"))
                gas = ctx.get("gas_price", ctx.get("base_fee", "?"))
                wallet.update(context=ctx)
                wallet["line"] = (
                    f"Wallet: none known; chain {chain_id} at block {block}, gas {gas}"
                )
        except Exception as e:  # noqa: BLE001 - a read failure is one line
            log.warning("defi_positions wallet read failed: %s", e)
            wallet["line"] = f"Wallet: Aomi read failed ({e})"
        finally:
            try:
                await pc.close()
            except Exception:  # noqa: BLE001
                pass
        return wallet


register_provider(DefiPositionsProvider())
