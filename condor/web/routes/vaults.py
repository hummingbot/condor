"""The Vaults tab's API (plan M4).

Every mutation here returns a **build** — an unsigned transaction for the
creator's browser — and a separate confirm call records the result once the
signature is on chain. Condor holds no key, so it cannot pretend a vault
changed; the only evidence that something happened is a confirmed signature,
and the store follows the chain rather than leading it.

Two phases, and most of this file is about keeping them straight:

* a **private** vault has no token and no outside holders. Its creator installs
  a delegate without anybody's co-signature, owes nothing to anyone, and moves
  assets in and out through that delegate — there is no withdraw *instruction*,
  because the delegate can already do it and the creator controls the delegate.
  Condor's part is a place to keep the label and the private config.
* a **tokenized** vault has holders. Withdrawals stop, the administrator
  co-signs delegate changes, and the numbers on the page stop being one
  person's business.

Reconcile runs before every listing: the chain is the record, and a local file
that disagrees with it is corrected or dropped (`vault_store`).
"""

from __future__ import annotations

import logging
import secrets

import aiohttp
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from condor import vault_store, wallet_store
from condor.market_rates import get_rates
from condor.gateway_client import VaultGateway
from condor.vault_config import NotCanonical, config_hash
from condor.web.auth import get_current_user, require_server_access_query
from condor.web.models import (
    CreateVaultRequest,
    VaultBuild,
    VaultCreateResponse,
    VaultInfo,
    VaultLabelRequest,
    VaultLaunchConfigRequest,
    VaultPublishRequest,
    VaultRedeemRequest,
    VaultScanResponse,
    VaultStageRequest,
    VaultTokenizeRequest,
    VaultWithdrawRequest,
    WebUser,
)
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vaults", tags=["vaults"])


# ── helpers ───────────────────────────────────────────────────────────────────


async def _gateway(server: str, network: str = "mainnet-beta") -> VaultGateway:
    return await VaultGateway.for_server(get_config_manager(), server, network)


def _creator(user: WebUser) -> str:
    """The wallet this user signs with, or a 409 telling them to attach one.

    A vault's creator is a key, not an account: everything the creator may do is
    checked on chain against `Vault.creator`, so Condor cannot act for a user who
    has not proved which key is theirs (plan D13).
    """
    record = wallet_store.get_wallet(user.id)
    if record is None:
        raise HTTPException(
            status_code=409,
            detail="Connect and attach a wallet first — a vault's creator is a key, and Condor holds none",
        )
    return record["address"]


async def _upstream(gw: VaultGateway, coro):
    """Call Gateway, and turn a failure into an error that names the server.

    The server is the whole address now: its Gateway is whichever one its
    hummingbot-api talks to, and there is no second one to tell it apart from.
    """
    try:
        return await coro
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"The Gateway on '{gw.server}' refused: {e}",
        )


def _as_build(payload: dict[str, Any]) -> VaultBuild:
    return VaultBuild(
        transaction=payload["transaction"],
        fee_payer=payload["feePayer"],
        recent_blockhash=payload["recentBlockhash"],
        last_valid_block_height=payload["lastValidBlockHeight"],
        current_block_height=payload["currentBlockHeight"],
        extra_signers=payload.get("extraSigners", []),
    )


def _to_info(account: str, record: dict[str, Any]) -> VaultInfo:
    return VaultInfo(
        account=account,
        live=vault_store.is_live(record),
        label=record.get("label", ""),
        server=record.get("server", ""),
        network=record.get("network", "mainnet-beta"),
        treasury_address=record.get("treasury_address", ""),
        creator_address=record.get("creator_address", ""),
        # Written by `tokenize` and read from the chain. A private vault has
        # none, which is why this is the chain's answer and not the record's.
        quote_mint=(record.get("chain") or {}).get("quote_mint"),
        delegate=record.get("delegate"),
        token=record.get("token"),
        pin=_public_pin(record.get("pin")),
        created_at=record.get("created_at", 0),
        chain=record.get("chain"),
        drift=record.get("drift"),
    )


def _public_pin(pin: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A pin without its config. The values are the creator's; only the hash is
    anyone else's business, and a listing is read by more than the creator."""
    if not pin:
        return None
    return {k: v for k, v in pin.items() if k != "config"}


async def _reconcile(
    gw: VaultGateway, user_id: int, records: dict[str, Any]
) -> dict[str, Any]:
    """Fold the chain into the local records, then persist what moved.

    A vault Gateway lists but Condor has no record of is *not* added here: this
    store is per-user and a listing is not proof of whose it is. What the chain
    corrects is Condor's own rows.

    A record missing from the listing is **asked about directly** before it is
    dropped. The listing is one call through a proxy, and an answer that is
    empty or short looks exactly like a chain with no vaults on it — so
    believing it cost a real vault its record once. Only a direct read that
    comes back 404 is absence (plan §4).
    """
    try:
        on_chain = {v["account"]: v for v in await gw.list_vaults()}
    except Exception as e:
        # "I could not reach the chain" is not "it is gone" (plan §4). The
        # records are shown as they stand and nothing is written.
        logger.warning("vault reconcile skipped: %s", e)
        return records

    reconciled: dict[str, Any] = {}
    for account, record in records.items():
        chain = on_chain.get(account)
        answer = (
            _chain_fields(chain) if chain else await _absent_or_unknown(gw, account)
        )
        updated, changed = vault_store.reconcile_record(dict(record), chain=answer)
        if changed:
            reconciled[account] = updated
        records[account] = updated
    if reconciled:
        vault_store.apply_reconcile(user_id, reconciled)
    return {k: v for k, v in records.items() if not v.get(vault_store.GONE)}


async def _absent_or_unknown(gw: VaultGateway, account: str) -> Any:
    """Ask the chain about one account: its fields, ABSENT, or ``None``.

    ``None`` is "could not tell" and changes nothing. Anything other than a 404
    is could-not-tell, including a proxy error and a timeout: "I did not hear"
    must never be recorded as "it is gone".
    """
    try:
        return _chain_fields(await gw.vault(account))
    except aiohttp.ClientResponseError as e:
        if e.status == 404:
            return vault_store.ABSENT
        logger.warning("vault %s could not be read: %s", account, e)
        return None
    except Exception as e:
        logger.warning("vault %s could not be read: %s", account, e)
        return None


def _chain_fields(chain: dict[str, Any]) -> dict[str, Any]:
    """Gateway's camelCase decode, as the store keeps it."""
    return {
        "creator": chain["creator"],
        "treasury": chain["treasury"],
        "mint": chain.get("mint"),
        "dbc_pool": chain.get("dbcPool"),
        "config_hash": chain.get("configHash"),
        "quote_mint": chain.get("quoteMint"),
        "version": chain.get("version", 0),
        "circulating_supply": chain.get("circulatingSupply", "0"),
        "total_supply": chain.get("totalSupply", "0"),
        # The launch terms this vault chose. The threshold is how much the
        # curve raises before it becomes a pool, and so how large the vault a
        # holder is buying into will be.
        "graduation_quote_threshold": chain.get("graduationQuoteThreshold", "0"),
        "creator_trading_fee_pct": chain.get("creatorTradingFeePct", 0),
        "pool_fee_option": chain.get("poolFeeOption", 0),
        "tokenized": bool(chain.get("tokenized")),
        "state": chain.get("state"),
        "delegate": chain.get("delegate"),
        "created_ts": chain.get("createdTs", 0),
        "tokenized_ts": chain.get("tokenizedTs", 0),
        "wind_down_ts": chain.get("windDownTs", 0),
        # Where the token trades once the curve graduated. Derived by Gateway
        # from terms the program already fixes, so nobody has to be told which
        # fee config a vault graduated into.
        "damm_pool": chain.get("dammPool"),
    }


def _require(user: WebUser, account: str) -> dict[str, Any]:
    record = vault_store.get(user.id, account)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no vault {account}")
    return record


# ── listing and creation ──────────────────────────────────────────────────────


@router.get("", response_model=list[VaultInfo])
async def list_vaults(
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    """Every vault on this network, with this user's own filled in.

    A vault is a public account: its creator, its state, its phase and its terms
    are on chain for anyone to read, and a listing that showed only the ones
    Condor happens to hold a record of would be hiding chain data behind a local
    file. So the chain is the list, and a record adds what only its owner has —
    the label, the pinned strategy, and whether the two disagree.

    What a record never adds to somebody else's row is the private config: that
    is served by `/config`, to its creator alone.
    """
    gw = await _gateway(server)
    records = vault_store.for_server(user.id, server)
    records = await _reconcile(gw, user.id, records)

    try:
        on_chain = {v["account"]: v for v in await gw.list_vaults()}
    except Exception as e:
        # The chain is unreachable; the records are still worth showing, and
        # each already carries the last chain state reconcile saw.
        logger.warning("vault listing fell back to records alone: %s", e)
        on_chain = {}

    rows = [_to_info(account, record) for account, record in sorted(records.items())]
    rows += [
        _chain_only_info(account, chain, server)
        for account, chain in sorted(on_chain.items())
        if account not in records
    ]
    return rows


def _chain_only_info(account: str, chain: dict[str, Any], server: str) -> VaultInfo:
    """A vault this user has no record of — everything from the chain, nothing else.

    `live` is true because the account exists: the flag asks whether the create
    transaction landed, and for a vault read *off* the chain that question is
    already answered.
    """
    fields = _chain_fields(chain)
    return VaultInfo(
        account=account,
        live=True,
        label="",
        server=server,
        network=chain.get("network", "mainnet-beta"),
        treasury_address=fields["treasury"],
        creator_address=fields["creator"],
        quote_mint=fields.get("quote_mint"),
        delegate=None,
        token=(
            {"mint": fields["mint"], "dbc_pool": fields.get("dbc_pool")}
            if fields.get("mint")
            else None
        ),
        pin=None,
        created_at=fields.get("created_ts", 0),
        chain=fields,
        drift=None,
    )


@router.post("", response_model=VaultCreateResponse)
async def create_vault(
    req: CreateVaultRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    """A vault, in one signature: the treasury, its delegate, and its strategy.

    Three instructions in one transaction, because there is no useful moment
    between them — a vault with no delegate cannot trade and a vault with no
    strategy has nothing to trade. Signing them separately would have needed a
    resumable draft to survive a closed tab, which is machinery for a problem
    that only existed because there were three.

    The record is written before the signature so that a vault signed in a tab
    that then closed is still findable; `confirm` promotes it once the chain
    carries the config's hash, and reconcile drops it if the transaction never
    landed.
    """
    creator = _creator(user)
    try:
        digest = config_hash(req.config)
    except NotCanonical as e:
        raise HTTPException(status_code=400, detail=str(e))
    agent_ref = {
        "repoHash": "00" * 32,
        "commit": "00" * 20,
        "agentSlug": req.agent_slug,
        "strategySlug": req.strategy_slug,
    }
    gw = await _gateway(server, req.network)
    build = await _upstream(
        gw,
        gw.build(
            "build-create-vault",
            {
                "walletAddress": creator,
                "fundLamports": str(req.fund_lamports),
                "agentRef": agent_ref,
                "configHash": digest,
            },
        ),
    )
    vault_store.create_record(
        user.id,
        account=build["vaultAccount"],
        label=req.label,
        server=server,
        network=req.network,
        vault_id=build["id"],
        treasury_address=build["treasury"],
        creator_address=creator,
        pending={
            "delegate": build["delegate"],
            "agent_ref": agent_ref,
            "config": req.config,
            "config_hash": digest,
        },
    )
    logger.info("user %s built vault %s on %s", user.id, build["vaultAccount"], server)
    return VaultCreateResponse(account=build["vaultAccount"], build=_as_build(build))


@router.post("/{account}/confirm")
async def confirm_created(
    account: str, req: VaultStageRequest, user: WebUser = Depends(get_current_user)
):
    """The signature landed — promote what was pending, if the chain agrees.

    The check is the point: Condor stores the config's values, the chain stores
    its hash, and the values are only ever believed because they hash to it.
    Storing them on the browser's say-so would let a failed transaction leave
    Condor holding parameters no vault ever agreed to.
    """
    record = _require(user, account)
    pending = record.get("pending")
    if not pending:
        if vault_store.is_live(record):
            return {"live": True, "version": (record["pin"] or {}).get("version", 1)}
        raise HTTPException(status_code=409, detail="nothing is staged for this vault")
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    chain = await _upstream(gw, gw.vault(account))
    _require_hash(chain, pending["config_hash"])
    record = vault_store.confirm(
        user.id,
        account,
        pin={
            "agent_ref": pending["agent_ref"],
            "version": chain.get("version", 1),
            "config_hash": chain.get("configHash"),
            "config": pending["config"],
            "scan": None,
        },
        delegate=pending.get("delegate"),
        signature=req.signature,
    )
    return {"live": True, "version": (record["pin"] or {}).get("version", 1)}


def _require_hash(chain: dict[str, Any], expected: str) -> None:
    """Refuse unless the chain carries the hash that was staged."""
    if chain.get("configHash") != expected:
        raise HTTPException(
            status_code=409,
            detail=(
                "the chain does not carry the staged config's hash "
                f"(chain {chain.get('configHash')}, staged {expected}) — "
                "the transaction may not have landed"
            ),
        )


@router.post("/{account}/build-install-delegate", response_model=VaultBuild)
async def build_install_delegate(
    account: str, user: WebUser = Depends(get_current_user)
):
    """Replace the key that trades this vault.

    Not part of creating one any more — the create transaction installs the
    first delegate. This is how an administrator is changed afterwards: Gateway
    mints the new key and, for a tokenized vault, adds the administrator's
    co-signature. A private vault gets neither asked for nor given one.
    """
    record = _require(user, account)
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    build = await _upstream(
        gw,
        gw.build(
            "build-install-delegate",
            {"walletAddress": record["creator_address"], "vaultAccount": account},
        ),
    )
    vault_store.update(
        user.id,
        account,
        lambda r: r.update(pending_delegate=build.get("mint")),
    )
    return _as_build(build)


@router.post("/{account}/delegated")
async def confirm_delegated(
    account: str, req: VaultStageRequest, user: WebUser = Depends(get_current_user)
):
    import time

    record = _require(user, account)
    delegate = record.get("pending_delegate")
    if not delegate:
        raise HTTPException(status_code=409, detail="no delegate is staged")
    vault_store.update(
        user.id,
        account,
        lambda r: r.update(
            delegate={"address": delegate, "granted_at": int(time.time())},
            pending_delegate=None,
        ),
    )
    return {"delegate": delegate}


# ── the strategy ──────────────────────────────────────────────────────────────


@router.post("/{account}/build-publish", response_model=VaultBuild)
async def build_publish(
    account: str, req: VaultPublishRequest, user: WebUser = Depends(get_current_user)
):
    """A new version: a new agent pin, a new private config, or both.

    The config is staged as `pending` and only promoted once `/published`
    proves the chain carries its hash. Storing it first would let a failed
    transaction leave Condor holding parameters no vault ever agreed to.

    There is no `build-pin`: a vault is pinned by the transaction that creates
    it, so version 1 never needs one and every later version is this.
    """
    record = _require(user, account)
    if not vault_store.is_live(record):
        raise HTTPException(
            status_code=409, detail="this vault's create transaction has not landed"
        )
    try:
        digest = config_hash(req.config)
    except NotCanonical as e:
        raise HTTPException(status_code=400, detail=str(e))
    agent_ref = _agent_ref(record, req)
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    build = await _upstream(
        gw,
        gw.build(
            "build-publish",
            {
                "walletAddress": record["creator_address"],
                "vaultAccount": account,
                "agentRef": agent_ref,
                "configHash": digest,
            },
        ),
    )
    vault_store.update(
        user.id,
        account,
        lambda r: r.__setitem__(
            "pin",
            {
                **(r.get("pin") or {}),
                "pending": {
                    "agent_ref": agent_ref,
                    "config": req.config,
                    "config_hash": digest,
                },
            },
        ),
    )
    return _as_build(build)


def _agent_ref(record: dict[str, Any], req: VaultPublishRequest) -> dict[str, str]:
    """What the pin points at. Nothing on chain validates it — Condor's own scan
    is what gates whether Condor will run it (plan D17)."""
    pin = record.get("pin") or {}
    previous = pin.get("agent_ref") or {}
    agent = req.agent_slug or previous.get("agentSlug")
    strategy = req.strategy_slug or previous.get("strategySlug")
    if not agent or not strategy:
        raise HTTPException(
            status_code=400, detail="agent_slug and strategy_slug are required"
        )
    return {
        "repoHash": previous.get("repoHash") or "00" * 32,
        "commit": previous.get("commit") or "00" * 20,
        "agentSlug": agent,
        "strategySlug": strategy,
    }


@router.post("/{account}/published")
async def confirm_published(
    account: str, req: VaultStageRequest, user: WebUser = Depends(get_current_user)
):
    """Promote the pending config — but only if the chain carries its hash."""
    record = _require(user, account)
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    chain = await _upstream(gw, gw.vault(account))
    pending = (record.get("pin") or {}).get("pending")
    if not pending:
        raise HTTPException(status_code=409, detail="nothing is staged for this vault")
    _require_hash(chain, pending["config_hash"])
    promoted = {
        "agent_ref": pending["agent_ref"],
        "version": chain.get("version", 1),
        "config_hash": chain.get("configHash"),
        "config": pending["config"],
        "signature": req.signature,
        # A new version is a new thing to check: the old verdict was about the
        # old config, and carrying it over would let an unscanned version run.
        "scan": None,
    }
    vault_store.update(user.id, account, lambda r: r.__setitem__("pin", promoted))
    return {"version": promoted["version"]}


@router.get("/{account}/config")
async def read_config(account: str, user: WebUser = Depends(get_current_user)):
    """The private config, to its creator alone.

    Not in any listing, not in the vault object, not to an admin. Anyone else
    gets the hash, which is what the chain gives them anyway.
    """
    record = _require(user, account)
    pin = record.get("pin") or {}
    if not pin.get("config"):
        raise HTTPException(status_code=404, detail="this vault has no config stored")
    return {
        "config": pin["config"],
        "config_hash": pin.get("config_hash"),
        "version": pin.get("version"),
    }


# ── running, and the one-way door ─────────────────────────────────────────────

#: The creator-signed builds that are nothing but a forward: this layer checks
#: who is asking and which vault, and Gateway's schema checks the rest. One
#: route rather than one per instruction, because a handler whose whole body is
#: "pass it on" is a handler that will be copied wrong.
CREATOR_BUILDS = {
    # Creator-driven trading: a transaction any Gateway `/trading/*/build-*`
    # route built for the treasury, wrapped into `execute*` for the creator
    # to sign. Everything a delegate can do, the creator can do by hand.
    "execute": "build-execute",
    "set-active": "build-set-active",
    "wind-down": "build-wind-down",
    "claim-income": "build-claim-income",
    # Funding the vault. Not a program instruction — a vault has no say in who
    # sends it money — but it belongs here because the *destination* is derived
    # from the vault rather than typed, and a vault has two addresses that look
    # equally plausible to paste.
    "deposit": "build-deposit",
}


@router.post("/{account}/build/{name}", response_model=VaultBuild)
async def build_for_creator(
    account: str,
    name: str,
    body: dict[str, Any] = Body(default=None),
    user: WebUser = Depends(get_current_user),
):
    """One of the creator's plain builds, forwarded.

    The allowlist is what keeps this from being an open proxy into Gateway: a
    name that is not in it is a 404 here, not a request sent onward.
    """
    route = CREATOR_BUILDS.get(name)
    if route is None:
        raise HTTPException(status_code=404, detail=f"no such build: {name}")
    return await _simple_build(user, account, route, body or {})


async def _simple_build(
    user: WebUser, account: str, route: str, body: dict[str, Any]
) -> VaultBuild:
    record = _require(user, account)
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    build = await _upstream(
        gw,
        gw.build(
            route,
            {
                "walletAddress": record["creator_address"],
                "vaultAccount": account,
                **body,
            },
        ),
    )
    return _as_build(build)


# ── tokenizing: the launch config, then the launch ────────────────────────────


@router.post("/{account}/build-launch-config", response_model=VaultBuild)
async def build_launch_config(
    account: str,
    req: VaultLaunchConfigRequest,
    user: WebUser = Depends(get_current_user),
):
    """The vault's own DBC config — the terms it will launch on.

    One per vault, because a vault prices its launch off the assets it already
    holds, which is why the program checks a config's *terms* rather than its
    address. The graduation market cap is the one that matters most: it fixes
    how much quote the curve raises before it becomes a pool, and so how large
    the vault a holder is buying into will be. Half of that raise is
    permanently locked as the market they exit through; the rest, less the
    protocol's 2 % graduation fee, is the strategy's capital.

    Its address is remembered here on confirmation, so the launch form does not
    ask anyone to paste one.
    """
    record = _require(user, account)
    if record.get("token"):
        raise HTTPException(status_code=409, detail="this vault is already tokenized")
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    body = {
        "walletAddress": record["creator_address"],
        "vaultAccount": account,
        # The quote asset is chosen here, not at creation: this config is what
        # fixes it, the program writes it onto the vault at `tokenize`, and it
        # can never change afterwards.
        "quoteMint": req.quote_mint,
        "initialMarketCap": req.initial_market_cap,
        "graduationMarketCap": req.graduation_market_cap,
    }
    for key, value in (
        ("creatorTradingFeePercentage", req.creator_trading_fee_percentage),
        ("poolFeeOption", req.pool_fee_option),
        ("circulatingSupply", req.circulating_supply),
        ("baseFeeBps", req.base_fee_bps),
    ):
        if value is not None:
            body[key] = value
    build = await _upstream(gw, gw.build("build-launch-config", body))
    vault_store.update(
        user.id,
        account,
        lambda r: r.__setitem__(
            "launch_config", {"address": build["config"], "confirmed": False}
        ),
    )
    return _as_build(build)


@router.post("/{account}/launch-config-created")
async def confirm_launch_config(
    account: str, req: VaultStageRequest, user: WebUser = Depends(get_current_user)
):
    """The config transaction landed, so the launch may use its address."""
    record = _require(user, account)
    config = record.get("launch_config")
    if not config:
        raise HTTPException(status_code=409, detail="no launch config is staged")
    vault_store.update(
        user.id,
        account,
        lambda r: r.__setitem__(
            "launch_config",
            {**config, "confirmed": True, "signature": req.signature},
        ),
    )
    return {"config": config["address"]}


@router.post("/{account}/build-tokenize", response_model=VaultBuild)
async def build_tokenize(
    account: str, req: VaultTokenizeRequest, user: WebUser = Depends(get_current_user)
):
    """Launch the token. One way: from here nobody withdraws, ever."""
    record = _require(user, account)
    if not vault_store.is_live(record):
        raise HTTPException(
            status_code=409, detail="this vault's create transaction has not landed"
        )
    if record.get("token"):
        raise HTTPException(status_code=409, detail="this vault is already tokenized")
    config = record.get("launch_config") or {}
    if not config.get("confirmed"):
        raise HTTPException(
            status_code=409,
            detail="build and sign this vault's launch config first — its terms are what the program checks",
        )
    return await _simple_build(
        user,
        account,
        "build-tokenize",
        {
            "dbcConfig": config["address"],
            "name": req.name,
            "symbol": req.symbol,
            "uri": req.uri,
        },
    )


@router.post("/{account}/withdraw")
async def withdraw(
    account: str, req: VaultWithdrawRequest, user: WebUser = Depends(get_current_user)
):
    """Take assets out of a private vault.

    There is no `withdraw` instruction in the program and this is not one: the
    delegate the creator installed can already move anything in this treasury, and
    this asks it to. That is also why it stops at `tokenize` — not because a key
    stops being able to, but because from there the vault's assets are other
    people's too, and the only way they leave is a redemption after a wind-down.
    """
    record = _require(user, account)
    if record.get("token"):
        raise HTTPException(
            status_code=409,
            detail="this vault has holders — nothing leaves it but a redemption after a wind-down",
        )
    gw = await _gateway(record["server"], record.get("network", "mainnet-beta"))
    return await _upstream(
        gw, gw.withdraw(account, req.destination, req.amount, req.mint)
    )


# ── redemption: the one thing a holder does here ──────────────────────────────


@router.post("/{account}/build-redeem", response_model=VaultBuild)
async def build_redeem(
    account: str,
    req: VaultRedeemRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    """Burn tokens, take the quote asset pro-rata.

    The only route here that is not the creator's: a holder is anyone with the
    token, so this asks for no record and checks no ownership — Condor may well
    have never heard of this vault. What it needs is an attached wallet, because
    the burn comes out of that wallet's account.

    After a wind-down the payment comes from an account the *program* owns, so
    nothing in this path — not Condor, not the administrator, not the delegate —
    is able to refuse it. This route only builds the transaction that asks.
    """
    holder = _creator(user)
    gw = await _gateway(server)
    build = await _upstream(
        gw,
        gw.build(
            "build-redeem",
            {"walletAddress": holder, "vaultAccount": account, "amount": req.amount},
        ),
    )
    return _as_build(build)


# ── the run gate ──────────────────────────────────────────────────────────────


@router.post("/{account}/scan", response_model=VaultScanResponse)
async def scan(account: str, user: WebUser = Depends(get_current_user)):
    """Condor's own decision about whether it will run this version.

    Not an attestation and not on chain (plan D17). It gates what *Condor's*
    crank starts, and its verdict is shown to the creator so a refusal is not a
    mystery. A creator who self-hosts is not bound by it at all.
    """
    import time

    record = _require(user, account)
    pin = record.get("pin") or {}
    config = pin.get("config")
    if config is None:
        raise HTTPException(status_code=409, detail="nothing pinned to scan")
    findings = _scan_config(config)
    verdict = {"passed": not findings, "findings": findings, "at": int(time.time())}
    vault_store.update(
        user.id, account, lambda r: r.setdefault("pin", {}).__setitem__("scan", verdict)
    )
    return VaultScanResponse(**verdict)


def _scan_config(config: dict[str, Any]) -> list[str]:
    """The checks Condor makes before its crank runs somebody's strategy.

    Deliberately shallow and deliberately explicit: it looks for the shapes that
    have actually cost money — an unbounded size, a slippage that is not a
    bound, a pair the vault cannot trade. It is not a safety proof and does not
    pretend to be one.
    """
    findings: list[str] = []
    if not isinstance(config, dict):
        return ["the config is not an object"]
    amount = config.get("amount_lamports")
    if isinstance(amount, int) and amount <= 0:
        findings.append("amount_lamports is not positive, so a run would open nothing")
    slippage = config.get("slippage_bps")
    if isinstance(slippage, int) and slippage > 1000:
        findings.append(
            f"slippage_bps is {slippage} (over 10%), which is a bound in name only"
        )
    if not config.get("pair"):
        findings.append("no pair: the crank would not know what to trade")
    return findings


# ── housekeeping ──────────────────────────────────────────────────────────────


@router.patch("/{account}", response_model=VaultInfo)
async def rename(
    account: str, req: VaultLabelRequest, user: WebUser = Depends(get_current_user)
):
    record = vault_store.update(
        user.id, account, lambda r: r.__setitem__("label", req.label)
    )
    return _to_info(account, record)


@router.delete("/{account}")
async def delete_draft(account: str, user: WebUser = Depends(get_current_user)):
    """Drafts only, and only while no delegate is installed.

    Forgetting a vault that exists on chain would not remove it; it would only
    cost this user the ability to reach it. So the rows that can be deleted are
    the ones that never became anything.
    """
    record = _require(user, account)
    if vault_store.is_live(record):
        raise HTTPException(
            status_code=409,
            detail="this vault is running on chain — wind it down instead; deleting the row would only hide it",
        )
    if record.get("delegate"):
        raise HTTPException(
            status_code=409, detail="a delegate is installed on this vault's treasury"
        )
    vault_store.delete(user.id, account)
    return {"deleted": True}


# ── what the vault holds ──────────────────────────────────────────────────────


@router.get("/{account}/holdings")
async def holdings(
    account: str,
    server: str = Query(...),
    quote: str = Query(
        "USDC",
        description=(
            "Symbol to price the treasury in. The launch form asks for the "
            "asset the vault will sell its token for, because a price in one "
            "currency and a market cap in another is how a launch ends up two "
            "orders of magnitude off."
        ),
    ),
    user: WebUser = Depends(require_server_access_query),
):
    """Everything in the vault's treasury, and where its own token trades.

    Read straight from the chain through Gateway rather than from any local
    store: a vault's balances move every time its strategy does, and a cached
    copy would be a second answer to a question the chain already answers.

    The treasury comes from the chain too, not from a record, which is what makes
    this readable for *any* vault on the server rather than only the ones this
    user happens to run. A vault is a public account — the listing already says
    so — and what it holds is the most public thing about it.

    The vault's **own token** appears here like any other balance, because that
    is what it is. After graduation the unsold supply sits in this same treasury,
    so the creator can market-make their own token — an LP position against its
    own pool, earning fees for the vault and deepening the market its holders
    exit through. `treasury` names that balance separately only because it is
    the one that must be left out of a redemption's denominator.
    """
    network = _network_of(user, account, server)
    gw = await _gateway(server, network)
    chain = await _upstream(gw, gw.vault(account))
    treasury = chain.get("treasury")
    if not treasury:
        raise HTTPException(status_code=404, detail=f"no vault {account} on {server}")
    balances = await _upstream(gw, gw.balances(treasury))
    return {
        "account": account,
        "treasury_address": treasury,
        "quote_mint": chain.get("quoteMint"),
        "balances": balances.get("balances", balances),
        # What those balances are worth, which Gateway cannot say: it answers
        # amounts and holds no prices. Any token the ticker pool could not
        # price is named in `unpriced`, so a caller can say the total is
        # partial rather than showing it as if it were complete.
        **await _treasury_value(server, balances.get("balances", balances), quote),
        # The treasury's own balance of its own token: unsold supply, not
        # circulating, excluded from what a redemption divides by.
        "retained_supply": chain.get("retainedSupply"),
        # The estate, once a wind-down has fixed it. Not the launch
        # `circulating_supply` on the `Vault`, which is what the curve offered.
        "redeemable_supply": chain.get("redeemableSupply"),
        "mint": chain.get("mint"),
        # The graduated pool: what the DEX browser lists and an LP executor trades.
        "damm_pool": chain.get("dammPool"),
    }


async def _treasury_value(
    server: str, balances: dict[str, Any], quote: str
) -> dict[str, Any]:
    """What the treasury holds, priced in `quote`, from the cached ticker pool.

    This is what a creator is tokenizing, and so what the launch price is a
    judgement about: pricing the token above this says the vault is worth more
    than the assets in it, which is the whole proposition of a managed vault,
    and pricing it below says the opposite.

    A token the pool cannot price is **named, not skipped**. Quietly dropping it
    would under-report the assets by an unknown amount, and the number this
    feeds is the one a creator uses to decide what their vault is worth.
    """
    amounts = {
        symbol: float(amount)
        for symbol, amount in (balances or {}).items()
        if float(amount or 0) > 0
    }
    wanted = [f"{symbol}-{quote}" for symbol in amounts if symbol != quote]
    try:
        rates = await get_rates(server, wanted) if wanted else {}
    except Exception as e:
        logger.warning("could not price a treasury on %s: %s", server, e)
        return {"value": None, "unpriced": sorted(amounts), "quote": quote}

    value = amounts.get(quote, 0.0)
    unpriced = []
    for symbol, amount in amounts.items():
        if symbol == quote:
            continue
        rate = rates.get(f"{symbol}-{quote}")
        if rate is None:
            unpriced.append(symbol)
            continue
        value += amount * float(rate)
    return {"value": value, "unpriced": sorted(unpriced), "quote": quote}


def _network_of(user: WebUser, account: str, server: str) -> str:
    """The network a vault is on — its record's, or the server's default.

    A vault this user has no record of is read on the network the rest of the
    page is already using; there is only one per server in practice, and the
    chain read below fails loudly if that is ever wrong.
    """
    record = vault_store.get(user.id, account)
    return (record or {}).get("network", "mainnet-beta")


@router.get("/{account}/lp-positions")
async def lp_positions(
    account: str,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    """Every liquidity position the vault's treasury still holds.

    Fanned out across the protocols this Gateway actually reports, rather than
    a list written here: a vault's strategy may LP anywhere, and a hard-coded
    set of connectors is one that stops being true the first time somebody adds
    one.

    Two kinds of no-answer, kept apart because they mean different things.
    ``unsupported`` is a *capability*: a fungible-LP AMM has no positions to
    enumerate, Gateway says so every time, and reporting that as a failure would
    put a warning on every vault page forever. ``errors`` is everything else — a
    protocol that should have answered and did not — and it stays loud, because
    "no positions" and "nobody could tell you" are different answers and only
    one of them is good news.
    """
    network = _network_of(user, account, server)
    gw = await _gateway(server, network)
    chain = await _upstream(gw, gw.vault(account))
    treasury = chain.get("treasury")
    if not treasury:
        raise HTTPException(status_code=404, detail=f"no vault {account} on {server}")

    client = gw.client
    network_id = f"solana-{network}"
    try:
        connectors = await client.connectors()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"the Gateway on '{server}' did not list its connectors ({type(e).__name__})",
        )

    positions: list[dict[str, Any]] = []
    errors: list[str] = []
    unsupported: list[str] = []
    for connector in connectors:
        if connector.get("chain") != "solana":
            continue
        name = connector.get("name", "")
        types = connector.get("trading_types", [])
        for kind, read in (
            ("clmm", client.clmm_positions_owned),
            ("amm", client.amm_positions_owned),
        ):
            if kind not in types:
                continue
            try:
                rows = await read(name, network_id, treasury)
            except Exception as e:
                detail = _detail(e)
                bucket = unsupported if _is_unsupported(detail) else errors
                bucket.append(f"{name} {kind}: {detail}")
                continue
            for row in rows or []:
                positions.append({**row, "protocol": name, "kind": kind})

    return {
        "account": account,
        "treasury_address": treasury,
        "positions": positions,
        "errors": errors,
        "unsupported": unsupported,
    }


#: How Gateway words a protocol that cannot enumerate a treasury's positions at
#: all. Matched on its sentence rather than on a list of connector names here,
#: which is the same list this route exists to avoid keeping.
_UNSUPPORTED = "not supported"


def _is_unsupported(detail: str) -> bool:
    return _UNSUPPORTED in detail.lower()


def _detail(error: Exception) -> str:
    """The upstream's own words where it gave any — a 400 from Gateway says
    exactly why it will not enumerate a fungible-LP AMM, and that sentence is
    more use than the exception class."""
    message = getattr(error, "message", None) or str(error)
    return message or type(error).__name__
