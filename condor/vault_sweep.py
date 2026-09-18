"""The sweep: a share of realised LP fees, spent buying the vault's token and burning it.

This is the whole investment interface. A holder's running return is not a
dividend, a share of profits, or a claim on the vault's assets — it is that the
supply shrinks every time the strategy closes a position in profit. `fee_bps` on
the vault says how much of each realised fee goes into that, and the number is
read from the chain rather than from Condor's copy, because it is the one
promise the token makes.

**Income, never gas.** The number swept is `fees_earned_quote` — what an LP
position actually earned — and never `cum_fees_quote`, which is what the
executor *spent* on transactions. Sweeping the second would burn the token in
proportion to how much gas Condor wasted, which is not a return.

**One signature per executor, ever.** The ledger is written *before* anything is
signed, so a crash between the two leaves an executor marked swept and its share
unspent — which loses a few basis points of burn. The other order loses real
money: a crank that restarts and re-reads the same terminal executor would buy
and burn again out of capital that was never earned. Idempotency wins.

**Buy, then burn, in two transactions.** Both go to Gateway as the vault's
wallet — this file decides *how much*, and Gateway knows how to say it on the
chain. Nothing here encodes an instruction: a second implementation of a wire
format, in another language, in the one path that spends a holder's assets, is a
second thing to get wrong. A burn
that never lands is not a loss and not an inconsistency: tokens sitting in the
vault's own wallet are treasury, which `redeem` already excludes from the
circulating supply, so they are out of circulation either way and the next pass
burns them. That is why this does not need the atomicity a single transaction
would buy.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from condor import paths
from condor.fsutil import atomic_write_json


log = logging.getLogger(__name__)

#: Below this the sweep waits and accumulates. A 0.0001 SOL buy pays more in
#: fees and priority than it burns, and it is the kind of transaction that makes
#: a vault's history unreadable.
DEFAULT_MIN_SWEEP_LAMPORTS = 10_000_000  # 0.01 SOL

_lock = threading.Lock()


def _ledger_path(swig_account: str):
    return paths.state_dir("vaults") / f"{swig_account}.json"


def read_ledger(swig_account: str) -> dict[str, Any]:
    path = _ledger_path(swig_account)
    if not path.exists():
        return {"swept": {}, "pending_lamports": 0, "sweeps": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Loud, and *not* an empty ledger: reading as empty would let every
        # executor be swept a second time, which is the one failure this file
        # exists to prevent.
        raise RuntimeError(
            f"the sweep ledger {path} is unreadable ({exc}). Fix or remove it "
            "deliberately — treating it as empty would double-sweep every "
            "executor this vault has ever closed."
        ) from exc
    data.setdefault("swept", {})
    data.setdefault("pending_lamports", 0)
    data.setdefault("sweeps", [])
    return data


def _write_ledger(swig_account: str, data: dict[str, Any]) -> None:
    path = _ledger_path(swig_account)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def share_of(fees_earned_quote: float, fee_bps: int) -> int:
    """`fee_bps` of a realised fee, in lamports, rounded down.

    Down, always: the remainder stays in the vault. Rounding up would spend a
    lamport of capital that no fee earned, and over a thousand closes that is a
    thousand lamports of holders' assets bought with other holders' assets.
    """
    if fees_earned_quote <= 0 or fee_bps <= 0:
        return 0
    lamports = int(fees_earned_quote * 1_000_000_000)
    return max(0, lamports * fee_bps // 10_000)


def record_close(
    swig_account: str,
    executor_id: str,
    fees_earned_quote: float,
    fee_bps: int,
) -> dict[str, Any]:
    """Accrue one terminated executor's share. Idempotent by executor id.

    Returns the ledger. `pending_lamports` is what has accrued and not yet been
    spent; :func:`due` says whether that is enough to be worth a transaction.
    """
    with _lock:
        ledger = read_ledger(swig_account)
        if executor_id in ledger["swept"]:
            return ledger
        share = share_of(fees_earned_quote, fee_bps)
        ledger["swept"][executor_id] = {
            "fees_earned_quote": fees_earned_quote,
            "fee_bps": fee_bps,
            "share_lamports": share,
            "at": int(time.time()),
        }
        ledger["pending_lamports"] = int(ledger["pending_lamports"]) + share
        _write_ledger(swig_account, ledger)
        return ledger


def due(swig_account: str, min_lamports: int = DEFAULT_MIN_SWEEP_LAMPORTS) -> int:
    """How much is waiting to be swept, or 0 if it is not yet worth a transaction."""
    pending = int(read_ledger(swig_account)["pending_lamports"])
    return pending if pending >= min_lamports else 0


def take_pending(swig_account: str, lamports: int) -> None:
    """Deduct what a sweep is about to spend, *before* it is spent.

    Same argument as the executor ledger: a crash after this costs the burn, a
    crash before it costs the capital twice.
    """
    with _lock:
        ledger = read_ledger(swig_account)
        ledger["pending_lamports"] = max(0, int(ledger["pending_lamports"]) - lamports)
        _write_ledger(swig_account, ledger)


def record_sweep(
    swig_account: str, signature: str, lamports: int, burned: Optional[str]
) -> None:
    """Note a landed sweep. History only — the chain is the record."""
    with _lock:
        ledger = read_ledger(swig_account)
        ledger["sweeps"].append(
            {
                "signature": signature,
                "lamports": lamports,
                "burned": burned,
                "at": int(time.time()),
            }
        )
        # A vault that runs for a year should not carry a megabyte of this; the
        # signatures are on chain and the page reads them from there.
        ledger["sweeps"] = ledger["sweeps"][-200:]
        _write_ledger(swig_account, ledger)
