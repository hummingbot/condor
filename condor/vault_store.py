"""The one record of a vault Condor keeps (plan §4).

A vault is three on-chain accounts and nothing else; this file is a *pointer* to
them plus the two things the chain cannot hold — a label, and the private config
whose hash is on chain. It is deliberately the only local record: the prototype
kept a vault in four places and spent its time reconciling them.

`paths.user_dir(user_id)/vaults.json`, keyed by the Vault PDA address, which
is the vault's identity everywhere (D4). Per-user because the key in the record
is the creator's, and the creator is a person.

**A record is written before the signature and promoted after it.** Creating a
vault is one transaction — the treasury, the delegate and the strategy together — so
there is no ladder of steps to resume, only `pending`: what the browser was
asked to sign. `confirm` promotes it, and only if the chain carries the config's
hash. Until then the record exists so that a vault signed in a closed tab is
still findable, and reconcile drops it if the transaction never landed.

The lifecycle state — Running, Paused, WindingDown, Redeemable — is on chain and
is never mirrored here, because a mirror of a thing that can change without us
is a second answer waiting to be wrong. Tokenizing likewise: whether it has
happened is `token` being set, which is the same question the chain answers
with `mint`.

**Reconcile before anything else.** On every listing the accounts are read from
the chain; one the RPC says is *absent* rolls the stage back to the last piece
that does exist, or drops the record if nothing does. A timeout changes nothing
— "I could not reach the chain" is not "it is gone", and treating it as one is
how a fork restart deletes a user's work.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Iterable, Optional

from condor import paths
from condor.fsutil import atomic_write_json
from condor.solana_keys import is_pubkey

log = logging.getLogger(__name__)

# The on-chain lifecycle states, mirrored here only as names so routes can talk
# about them. The values live on chain.
STATE_RUNNING = "Running"
STATE_PAUSED = "Paused"
STATE_WINDING_DOWN = "WindingDown"
STATE_REDEEMABLE = "Redeemable"

_lock = threading.Lock()


def _path(user_id: int | str):
    return paths.user_dir(user_id) / "vaults.json"


def _read(user_id: int | str) -> dict[str, Any]:
    path = _path(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Loud, and empty rather than crashing every vault surface: the file is
        # a pointer, and the chain is the record.
        log.error("vaults.json for user %s is unreadable (%s)", user_id, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write(user_id: int | str, data: dict[str, Any]) -> None:
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


# ── reading ───────────────────────────────────────────────────────────────────


def all_vaults(user_id: int | str) -> dict[str, Any]:
    with _lock:
        return _read(user_id)


def get(user_id: int | str, account: str) -> Optional[dict[str, Any]]:
    return all_vaults(user_id).get(account)


def require(user_id: int | str, account: str) -> dict[str, Any]:
    record = get(user_id, account)
    if record is None:
        raise KeyError(account)
    return record


def for_server(user_id: int | str, server: str) -> dict[str, Any]:
    return {k: v for k, v in all_vaults(user_id).items() if v.get("server") == server}


def iter_all_users() -> Iterable[tuple[str, str, dict[str, Any]]]:
    """``(user_id, account, record)`` for every vault this install knows.

    The crank's entry point: it runs per server, not per person, and a vault's
    creator is not the one who cranks it.
    """
    for user_id in paths.iter_user_ids():
        for account, record in all_vaults(user_id).items():
            yield user_id, account, record


def live_vault_labels(user_id: int | str) -> list[str]:
    """Vaults that still hold this user's wallet as their creator on chain.

    What the detach gate asks. A draft with no delegate holds nothing (deleting
    it is a click), and a vault whose chain state is `Redeemable` is finished:
    holders redeem from the program, which does not consult the creator.
    """
    live = []
    for account, record in all_vaults(user_id).items():
        chain = record.get("chain") or {}
        state = chain.get("state")
        if state in (None, STATE_REDEEMABLE) and not record.get("delegate"):
            continue
        if state == STATE_REDEEMABLE:
            continue
        live.append(record.get("label") or account)
    return live


# ── writing ───────────────────────────────────────────────────────────────────


def create_record(
    user_id: int | str,
    *,
    account: str,
    label: str,
    server: str,
    network: str,
    vault_id: str,
    treasury_address: str,
    creator_address: str,
    pending: dict[str, Any],
) -> dict[str, Any]:
    """The record for a vault whose transaction has been built but not signed.

    Written *before* the signature, so a vault signed in a tab that then closed
    is still findable. ``pending`` is what the browser was asked to sign — the
    delegate, the strategy pin and its config — and stays pending until
    :func:`confirm` finds its hash on chain. Reconcile drops the record if the
    transaction never landed.
    """
    for name, value in (
        ("account", account),
        ("treasury_address", treasury_address),
        ("creator_address", creator_address),
    ):
        if not is_pubkey(value):
            raise ValueError(f"{name} is not a Solana address: {value!r}")
    record = {
        "label": label,
        "server": server,
        "network": network,
        # hex; both PDAs derive from it, and Gateway re-derives the wallet with it
        "vault_id": vault_id,
        "treasury_address": treasury_address,
        "creator_address": creator_address,
        "delegate": None,
        # Set by `tokenize`, never by the create flow. None means private.
        "token": None,
        "pin": None,
        "pending": pending,
        "created_at": int(time.time()),
    }
    with _lock:
        data = _read(user_id)
        if account in data:
            raise ValueError(f"vault {account} already exists")
        data[account] = record
        _write(user_id, data)
    return record


def update(
    user_id: int | str, account: str, mutate: Callable[[dict], None]
) -> dict[str, Any]:
    """Read-modify-write one record under the store lock.

    Every mutation goes through here so two browser tabs advancing the same
    draft cannot lose each other's step.
    """
    with _lock:
        data = _read(user_id)
        record = data.get(account)
        if record is None:
            raise KeyError(account)
        mutate(record)
        data[account] = record
        _write(user_id, data)
        return record


def is_live(record: dict[str, Any]) -> bool:
    """Has this vault's create transaction been confirmed?

    One question with one answer, where a stage ladder had three: a vault is
    live once its pin is promoted, and until then it is a build nobody has
    signed yet.
    """
    return bool((record.get("pin") or {}).get("config_hash"))


def confirm(
    user_id: int | str,
    account: str,
    *,
    pin: dict[str, Any],
    delegate: Optional[str],
    signature: str,
) -> dict[str, Any]:
    """Promote what was pending, now that the chain carries it.

    Idempotent: a confirm can arrive twice — a retried request, two tabs — and
    the second must find the same record rather than undo the first.
    """

    def mutate(record: dict) -> None:
        record["pin"] = pin
        record["create_signature"] = signature
        if delegate and not record.get("delegate"):
            record["delegate"] = {"address": delegate, "granted_at": int(time.time())}
        record.pop("pending", None)

    return update(user_id, account, mutate)


def delete(user_id: int | str, account: str) -> bool:
    with _lock:
        data = _read(user_id)
        if account not in data:
            return False
        data.pop(account)
        _write(user_id, data)
    return True


# ── reconcile ─────────────────────────────────────────────────────────────────


class ChainAbsent:
    """Sentinel: the RPC answered, and the account is not there.

    Distinct from ``None``, which a reader returns when it could not tell. The
    difference is the whole rule: absent rolls a record back, unknown changes
    nothing.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"


ABSENT = ChainAbsent()

#: Marks a reconciled record the chain says is not there. Set by
#: :func:`reconcile_record` and acted on by :func:`apply_reconcile`, so the
#: decision and the deletion stay one step apart.
GONE = "_gone"


def reconcile_record(
    record: dict[str, Any],
    *,
    chain: Any,
) -> tuple[dict[str, Any], bool]:
    """Fold the chain's answer into one record. Returns ``(record, changed)``.

    ``chain`` is the vault's decoded content, :data:`ABSENT`, or ``None`` for
    "could not tell". Only :data:`ABSENT` moves anything: a record is dropped
    when the chain has *said* the account is not there, never because an answer
    failed to arrive.
    """
    changed = False

    if chain is ABSENT:
        record[GONE] = True
        return record, True

    if isinstance(chain, dict):
        if record.get("chain") != chain:
            record["chain"] = chain
            changed = True
        # The record claims a pin; the chain is the arbiter of its numbers.
        pin = record.get("pin")
        if pin:
            record["drift"] = _drift(pin, chain)
        # The chain is the arbiter of whether it is still private.
        if chain.get("mint") and not record.get("token"):
            record["token"] = {"mint": chain["mint"], "dbc_pool": chain.get("dbc_pool")}
            changed = True

    return record, changed


def _drift(pin: dict[str, Any], chain: dict[str, Any]) -> Optional[str]:
    """What the store claims that the chain does not agree with.

    A vault does not run while this is set. The private config is served to the
    creator on the strength of its hash being the chain's, so a mismatch is not a
    cosmetic difference — it means the config on file is not the one signed.
    """
    mismatches = []
    for key, label in (
        ("version", "version"),
        ("config_hash", "config hash"),
    ):
        want, got = pin.get(key), chain.get(key)
        if want is not None and got is not None and want != got:
            mismatches.append(f"{label} (stored {want}, chain {got})")
    if not mismatches:
        return None
    return "the record disagrees with the chain on " + ", ".join(mismatches)


def apply_reconcile(
    user_id: int | str, reconciled: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Persist a reconciled view, dropping the records marked gone."""
    with _lock:
        data = _read(user_id)
        for account, record in reconciled.items():
            if record.get(GONE):
                data.pop(account, None)
            else:
                data[account] = record
        _write(user_id, data)
        return data
