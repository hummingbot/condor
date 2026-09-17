"""Condor Vault tools: the runner's buyback and the vault's state.

A Condor Vault is a strategy token on a Meteora Dynamic Bonding Curve (DBC).
The agent folder that drives it is public; the run config that tunes it is
private to its manager; and the runner's Swig agent wallet is paid **by the
token**: after
every close that realised LP fees, ``sweep_fees`` takes the runner's chosen share
of those fees (``buyback_bps`` of the vault block), converts it to SOL and buys
the vault token on its DBC pool, delivering the tokens to that same wallet.

Both tools read one thing this process was handed on argv, ``settings.vault``
(``--vault-json``): the public coordinates of the run — ``slug``, ``mint``,
``pool``, ``run_id``, ``swig_wallet``, ``buyback_bps`` and, when the run keeps a
session on disk, ``session_dir``. Nothing confidential travels that way, and
neither tool takes a wallet or an amount as a parameter: the wallet is the
block's, the amount is the executor's.

REALISED FEES. A hummingbot LP executor reports two fee figures and only one is
income. ``cum_fees_quote`` is the TRANSACTION cost the executor paid (gas, in
quote units — see ``LpExecutor.get_cum_fees_quote``), so sweeping it would buy
the vault token with money that was never earned. The income is
``custom_info.fees_earned_quote`` = ``base_fee × price + quote_fee``, the swap
fees the position collected valued in the pool's quote asset, with ``base_fee``
and ``quote_fee`` as its two legs. That is the figure ``sweep_fees`` sweeps, and
the one it reports.

IDEMPOTENCY. A sweep is one signature per executor, ever. Every attempt is
written to a per-run ledger BEFORE it signs — in memory for the life of this
process and, when the block names a ``session_dir``, appended to
``<session_dir>/vault_sweeps.jsonl`` so a restarted server refuses the same
executor too. A second call for an executor with any record refuses and names
the record's stage: a sweep that failed after submission may still have landed,
and the operator, not a retry, decides that.

Impl module: no MCP types, so it is testable with a stubbed client (see
``tests/test_vault_tools.py``). The registered tools in ``server.py`` carry the
docstrings the model reads.
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError

logger = logging.getLogger("hummingbot-mcp")

#: Every vault lives on Solana mainnet (or a fork of it that answers to the
#: same network id); the DBC program is mainnet-only code.
NETWORK = "solana-mainnet-beta"
#: Gateway's DBC connector, in the "name/type" form the unified swap route
#: takes. ``execute_swap`` on it from the Swig funds-owner address makes Gateway
#: wrap the swap in a Swig ``sign`` and sign with the stored delegate.
DBC_CONNECTOR = "meteora/launch"
#: The bare connector name, for Gateway's launch pool-info read (hummingbot-api
#: proxies it as GET /gateway/launch/pool-info; decimal fields come back as
#: strings — price, migrationProgress, quoteReserve, migrationQuoteThreshold).
# The Dynamic Bonding Curve is the `meteora` connector's `launch` trading type,
# not a connector of its own (Gateway, 2026-09-16). After migration the same
# connector trades the token on its `amm` surface.
DBC_CONNECTOR_NAME = "meteora"
#: Where a non-SOL fee asset is converted to SOL: an aggregator, so a fee asset
#: Gateway has no pool entry for (a fresh mint, a tokenised equity) still routes.
#: A vault whose strategy already quotes SOL never reaches this leg.
CONVERT_CONNECTOR = "jupiter/router"
#: The retry when the aggregator cannot deliver: one named SOL/USDC pool that
#: Gateway builds the transaction for itself. The aggregator signs server-side
#: and can fail for reasons that have nothing to do with the vault — no route
#: at a thin moment, or a fork that will not take its blockhash — and a fee
#: sweep that is already holding realised fees should not be lost to that.
#: Only the USDC leg has this route; any other fee asset raises as before.
CONVERT_RETRY_CONNECTOR = "orca/clmm"
CONVERT_RETRY_POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
CONVERT_RETRY_ASSET = "USDC"


SOL = "SOL"
#: The one slippage both legs of a sweep carry. A DBC curve is a single pool and
#: the conversion is an aggregator route; 1% is Gateway's own default for both.
SWEEP_SLIPPAGE_PCT = Decimal("1")
#: The append-only per-run ledger, beside the session's journal.
SWEEP_LEDGER_FILE = "vault_sweeps.jsonl"

#: How the DBC buy is expressed on Gateway's unified swap route, as agreed with
#: the connector: base = the vault mint, quote = SOL, side BUY, and ``amount``
#: is the SOL spent (exact-in). This is the M1 ``meteora`` launch-curve contract, not the
#: aggregator convention where a BUY's amount is the base received.
DBC_BUY_SIDE = "BUY"

#: executor_id -> the ledger record of its sweep attempt, for this process.
_swept: dict[str, dict[str, Any]] = {}
#: Session dirs whose on-disk ledger has been folded into ``_swept``.
_ledgers_loaded: set[str] = set()


class SweepRefused(ToolError):
    """``sweep_fees`` declined to sign, and says why."""


# ── the ledger ──────────────────────────────────────────────────────────────


def _ledger_path(vault: dict[str, Any]) -> Path | None:
    session_dir = vault.get("session_dir")
    return Path(session_dir) / SWEEP_LEDGER_FILE if session_dir else None


def _load_ledger(vault: dict[str, Any]) -> None:
    """Fold the on-disk ledger into memory, once per session dir per process."""
    path = _ledger_path(vault)
    if path is None or str(path) in _ledgers_loaded:
        return
    _ledgers_loaded.add(str(path))
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        # The last record for an executor wins: a `buy_submitted` followed by a
        # `done` reads as done.
        _swept[record["executor_id"]] = record


def _record(vault: dict[str, Any], record: dict[str, Any]) -> None:
    """Write one ledger record: memory first, then the file when there is one."""
    _swept[record["executor_id"]] = record
    path = _ledger_path(vault)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def swept_executors(vault: dict[str, Any]) -> list[dict[str, Any]]:
    """Every executor this run has a sweep record for, oldest first."""
    _load_ledger(vault)
    rows = [r for r in _swept.values() if r.get("run_id") == vault["run_id"]]
    return sorted(rows, key=lambda r: r.get("ts", 0))


def reset_ledger_for_tests() -> None:
    _swept.clear()
    _ledgers_loaded.clear()


# ── reading the executor ────────────────────────────────────────────────────


def _number(value: Any, name: str) -> float:
    if value is None or value == "":
        raise SweepRefused(f"the executor reports no {name}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SweepRefused(f"the executor's {name} is not a number: {value!r}") from exc
    return number


def realised_fees(executor: dict[str, Any]) -> tuple[float, str]:
    """``(fees_earned_quote, quote_asset)`` for a terminated LP executor.

    Reads ``custom_info.fees_earned_quote`` — the LP fee income the position
    collected, valued in the pool's quote asset — and the quote asset off the
    executor's ``trading_pair``. Refuses an executor that is not an LP executor:
    only an LP position realises swap fees, and the field does not exist on the
    other executor types.
    """
    executor_type = executor.get("executor_type") or (executor.get("config") or {}).get(
        "type"
    )
    if executor_type != "lp_executor":
        raise SweepRefused(
            f"executor {executor.get('executor_id')!r} is a {executor_type or 'unknown'}"
            " executor, not an LP executor — only an LP position realises fees"
        )
    custom = executor.get("custom_info") or {}
    if "fees_earned_quote" not in custom:
        raise SweepRefused(
            "the executor carries no custom_info.fees_earned_quote, so its realised"
            " fees cannot be read"
        )
    fees = _number(custom.get("fees_earned_quote"), "fees_earned_quote")
    pair = executor.get("trading_pair") or (executor.get("config") or {}).get(
        "trading_pair", ""
    )
    if "-" not in pair:
        raise SweepRefused(f"the executor's trading_pair {pair!r} names no quote asset")
    return fees, pair.split("-", 1)[1]


def _is_terminated(executor: dict[str, Any]) -> bool:
    return str(executor.get("status", "")).upper() == "TERMINATED"


# ── the tools ───────────────────────────────────────────────────────────────


def _require_vault(vault: dict[str, Any] | None) -> dict[str, Any]:
    if not vault:
        raise SweepRefused(
            "this run has no vault block: the tool is mounted, but the session was"
            " not started with a `vault` in its config, so there is no token to buy"
            " and no wallet to buy it from"
        )
    return vault


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def _quoted_out(quote: dict[str, Any], what: str) -> float:
    out = quote.get("amount_out")
    if out is None:
        out = quote.get("expected_amount")
    if out is None:
        raise ToolError(f"the {what} quote returned no amount_out: {quote}")
    return float(out)


def _tx_hash(result: dict[str, Any], what: str) -> str:
    tx = result.get("transaction_hash") or result.get("signature")
    if not tx:
        raise ToolError(f"the {what} swap returned no transaction hash: {result}")
    return str(tx)


async def sweep_fees(
    client: Any, executor_id: str, vault: dict[str, Any] | None
) -> dict[str, Any]:
    """Sweep the runner's share of one closed LP executor's realised fees.

    See the module docstring for the fee field, the leg order and the ledger.
    Returns a dict with ``formatted_output`` plus the numbers, for the tests.
    """
    vault = _require_vault(vault)
    if not executor_id:
        raise SweepRefused("sweep_fees needs the executor_id of the closed position")
    bps = int(vault["buyback_bps"])
    if bps == 0:
        raise SweepRefused(
            f"vault {vault['slug']} runs with buyback_bps = 0: the runner chose no"
            " buyback, so there is nothing to sweep"
        )

    _load_ledger(vault)
    prior = _swept.get(executor_id)
    if prior is not None:
        raise SweepRefused(
            f"executor {executor_id} already has a sweep record in run"
            f" {vault['run_id']} (stage={prior.get('stage')},"
            f" signature={prior.get('signature') or 'none'}) — a sweep is one"
            " signature per executor; check the chain before doing anything else"
        )

    executor = await client.executors.get_executor(executor_id)
    if not isinstance(executor, dict) or not executor:
        raise SweepRefused(f"executor {executor_id} was not found")
    if not _is_terminated(executor):
        raise SweepRefused(
            f"executor {executor_id} is {executor.get('status')!r}, not TERMINATED —"
            " fees are realised when the position closes; stop it first"
        )
    fees, fee_asset = realised_fees(executor)
    if fees <= 0:
        raise SweepRefused(
            f"executor {executor_id} realised no fees (fees_earned_quote ="
            f" {fees}), so there is nothing to sweep"
        )

    sweep_amount = fees * bps / 10_000
    base = {
        "executor_id": executor_id,
        "run_id": vault["run_id"],
        "slug": vault["slug"],
        "mint": vault["mint"],
        "swig_wallet": vault["swig_wallet"],
        "buyback_bps": bps,
        "fee_amount": fees,
        "fee_asset": fee_asset,
        "sweep_amount": sweep_amount,
    }
    convert_via: str | None = None

    # Leg 1: the fee asset to SOL, exact-in, unless the fees already are SOL.
    convert_tx: str | None = None
    if fee_asset == SOL:
        sol_amount = sweep_amount
    else:
        pair = f"{fee_asset}-{SOL}"

        async def _convert(connector: str, pool: str | None) -> tuple[float, str]:
            """Quote then execute one conversion route. Raises what it hits."""
            quoted = await client.gateway_swap.get_swap_quote(
                connector=connector,
                network=NETWORK,
                trading_pair=pair,
                side="SELL",
                amount=_dec(sweep_amount),
                slippage_pct=SWEEP_SLIPPAGE_PCT,
                extra_params={"pool_address": pool} if pool else None,
            )
            out = _quoted_out(quoted, f"{pair} conversion")
            if out <= 0:
                raise ToolError(
                    f"the {pair} conversion quote on {connector} came back with {out} SOL out"
                )
            _record(
                vault,
                {
                    **base,
                    "stage": "convert_submitted",
                    "connector": connector,
                    "sol_amount": out,
                    "ts": time.time(),
                },
            )
            done = await client.gateway_swap.execute_swap(
                connector=connector,
                network=NETWORK,
                trading_pair=pair,
                side="SELL",
                amount=_dec(sweep_amount),
                slippage_pct=SWEEP_SLIPPAGE_PCT,
                wallet_address=vault["swig_wallet"],
                extra_params={"pool_address": pool} if pool else None,
            )
            return out, _tx_hash(done, f"{pair} conversion")

        routes: list[tuple[str, str | None]] = [(CONVERT_CONNECTOR, None)]
        if fee_asset == CONVERT_RETRY_ASSET:
            routes.append((CONVERT_RETRY_CONNECTOR, CONVERT_RETRY_POOL))
        first_error: Exception | None = None
        for connector, pool in routes:
            try:
                sol_amount, convert_tx = await _convert(connector, pool)
                convert_via = connector
                break
            except Exception as exc:  # noqa: BLE001 — every route's failure is recorded
                _record(
                    vault,
                    {
                        **base,
                        "stage": "convert_failed",
                        "connector": connector,
                        "error": str(exc),
                        "ts": time.time(),
                    },
                )
                first_error = first_error or exc
        else:
            raise ToolError(
                f"the {pair} conversion failed on every route "
                f"({', '.join(c for c, _ in routes)}): {first_error}"
            ) from first_error
        _record(
            vault,
            {
                **base,
                "stage": "converted",
                "sol_amount": sol_amount,
                "convert_signature": convert_tx,
                "ts": time.time(),
            },
        )

    # Leg 2: SOL into the vault token on its DBC pool, from the Swig wallet.
    pair = f"{vault['mint']}-{SOL}"
    quote = await client.gateway_swap.get_swap_quote(
        connector=DBC_CONNECTOR,
        network=NETWORK,
        trading_pair=pair,
        side=DBC_BUY_SIDE,
        amount=_dec(sol_amount),
        slippage_pct=SWEEP_SLIPPAGE_PCT,
    )
    tokens_expected = _quoted_out(quote, "DBC buy")
    _record(
        vault,
        {
            **base,
            "stage": "buy_submitted",
            "sol_amount": sol_amount,
            "convert_signature": convert_tx,
            "tokens_expected": tokens_expected,
            "ts": time.time(),
        },
    )
    try:
        buy = await client.gateway_swap.execute_swap(
            connector=DBC_CONNECTOR,
            network=NETWORK,
            trading_pair=pair,
            side=DBC_BUY_SIDE,
            amount=_dec(sol_amount),
            slippage_pct=SWEEP_SLIPPAGE_PCT,
            wallet_address=vault["swig_wallet"],
        )
        signature = _tx_hash(buy, "DBC buy")
    except Exception as exc:
        _record(
            vault,
            {
                **base,
                "stage": "buy_failed",
                "sol_amount": sol_amount,
                "convert_signature": convert_tx,
                "error": str(exc),
                "ts": time.time(),
            },
        )
        raise
    record = {
        **base,
        "stage": "done",
        "sol_amount": sol_amount,
        "convert_signature": convert_tx,
        "tokens_expected": tokens_expected,
        "signature": signature,
        "ts": time.time(),
    }
    _record(vault, record)

    lines = [
        f"Swept executor {executor_id} for vault {vault['slug']} (run {vault['run_id']})",
        f"  Realised fees: {fees:.8g} {fee_asset} (custom_info.fees_earned_quote)",
        f"  Runner share: {bps} bps → {sweep_amount:.8g} {fee_asset}",
    ]
    if convert_tx:
        lines.append(
            f"  Converted to SOL via {convert_via}: {sol_amount:.8g} SOL"
            f" (tx {convert_tx})"
        )
    else:
        lines.append(f"  Fees were already SOL: {sol_amount:.8g} SOL")
    lines.append(
        f"  Bought {vault['mint']} on {DBC_CONNECTOR} for {sol_amount:.8g} SOL:"
        f" {tokens_expected:.8g} tokens expected (quoted)"
    )
    lines.append(f"  Delivered to Swig wallet {vault['swig_wallet']}")
    lines.append(f"  Signature: {signature}")
    lines.append(
        "Resolve the buy with get_swap_status(transaction_hash=...) if you need"
        " the confirmed token amount."
    )
    return {**record, "formatted_output": "\n".join(lines)}


async def _read(coro, what: str) -> tuple[Any, str | None]:
    """One Gateway read, as ``(result, error)``.

    The block is local knowledge and is always reported; each remote read is
    reported as its answer or as the reason it has none, so a Gateway that is
    down or a connector that is not mounted yet does not hide the block.
    """
    try:
        return await coro, None
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        logger.warning("vault_status: %s read failed: %s", what, exc)
        return None, f"{what} read failed: {exc}"


async def vault_status(client: Any, vault: dict[str, Any] | None) -> dict[str, Any]:
    """The vault block, the DBC pool's state and the Swig wallet's token balance.

    Two Gateway reads through hummingbot-api's proxies — the DBC pool-info and
    the wallet's balance of the mint — plus this run's sweep ledger. A read that
    fails is reported as failed, never as zero, and never hides the block.
    """
    vault = _require_vault(vault)
    public = {
        k: vault[k]
        for k in ("slug", "mint", "pool", "run_id", "swig_wallet", "buyback_bps")
    }

    pool, pool_error = await _read(
        client.gateway._get(
            "/gateway/launch/pool-info",
            params={
                "connector": DBC_CONNECTOR_NAME,
                "network": NETWORK,
                "pool_address": vault["pool"],
            },
        ),
        "DBC pool",
    )
    balances, balance_error = await _read(
        client.gateway._get(
            "/gateway/balances",
            params={
                "network": NETWORK,
                "address": vault["swig_wallet"],
                "tokens": vault["mint"],
            },
        ),
        "wallet balance",
    )
    token_balance: float | None = None
    if balance_error is None:
        raw_balances = balances.get("balances") if isinstance(balances, dict) else None
        if not isinstance(raw_balances, dict):
            balance_error = f"the balance read returned no balances map: {balances}"
        elif vault["mint"] in raw_balances:
            token_balance = float(raw_balances[vault["mint"]])
        elif len(raw_balances) == 1:
            # Gateway keys a balance by the symbol it resolved the mint to when
            # it knows one; a single answer to a single-token ask is that token.
            token_balance = float(next(iter(raw_balances.values())))
        else:
            balance_error = (
                f"the balance read did not answer for {vault['mint']}: {raw_balances}"
            )

    swept = swept_executors(vault)
    lines = [
        f"Vault {public['slug']} — run {public['run_id']}",
        f"  Mint: {public['mint']}",
        f"  Pool: {public['pool']} ({DBC_CONNECTOR_NAME})",
        f"  Swig wallet: {public['swig_wallet']}",
        f"  Buyback: {public['buyback_bps']} bps of realised fees",
        "",
        "DBC pool",
    ]
    if pool_error is None and isinstance(pool, dict):
        lines.append(f"  Price: {pool.get('price')} SOL per token")
        progress = f"  Migration progress: {pool.get('migrationProgress')}"
        if pool.get("quoteReserve") is not None:
            progress += f" ({pool.get('quoteReserve')} / {pool.get('migrationQuoteThreshold')} SOL)"
        lines.append(progress)
        lines.append(f"  Migrated: {pool.get('isMigrated')}")
    else:
        lines.append(
            f"  UNAVAILABLE — {pool_error or f'unexpected pool-info shape: {pool}'}"
        )
    lines.append("")
    if balance_error is None:
        lines.append(f"Swig wallet holds {token_balance:.8g} of the vault token")
    else:
        lines.append(f"Swig wallet balance UNAVAILABLE — {balance_error}")
    lines.append("")
    lines.append(f"Executors swept this run: {len(swept)}")
    for row in swept:
        lines.append(
            f"  - {row['executor_id']}: {row.get('stage')}"
            f" {row.get('sweep_amount')} {row.get('fee_asset')}"
            f" → {row.get('sol_amount')} SOL"
            + (f" (tx {row['signature']})" if row.get("signature") else "")
        )
    return {
        "vault": public,
        "pool": pool if pool_error is None else None,
        "pool_error": pool_error,
        "token_balance": token_balance,
        "balance_error": balance_error,
        "swept": swept,
        "formatted_output": "\n".join(lines),
    }
