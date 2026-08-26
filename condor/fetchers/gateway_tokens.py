"""Put a pool's tokens in Gateway's token list before anything tries to read them.

Gateway resolves a *swap* by mint address on its own — Solana's ``getToken``
falls back to reading the mint on chain, Token-2022 included — which is why a
pool discovered on GeckoTerminal can be traded at all without the user
maintaining anything by hand. It will not resolve a *balance* that way:
``fetchTokenAccounts`` skips every mint that is not in the network's token list,
so ``processSpecificTokens`` answers 0 even when the balance is asked for by
address. Everything that sizes an order off a balance — a sell, the percentage
presets, an LP amount, the portfolio row — therefore sees an empty wallet until
the token is listed. On EVM there is no on-chain fallback at all and the swap
itself fails with "token not found".

So the token list is not a convenience: it is a precondition of trading a pool,
and one the user should never have to satisfy by hand. Condor satisfies it the
moment a pool workspace opens, and again when an executor is created, for the
callers (agents, Telegram, MCP) that never open one.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from condor.dex_candles import ADDRESS_RE

logger = logging.getLogger(__name__)

# What one ensure call is allowed to register. A pool has two tokens; anything
# beyond a handful is a caller passing a list it did not mean to.
MAX_TOKENS_PER_CALL = 8

# Addresses this process has already seen on the list, so opening the same pool
# twice costs nothing. Only *confirmed* listings are remembered — never a failure,
# which must stay retryable — so the memo can never mask a missing token. Bounded
# because a browsing session mints a fresh entry per pool visited.
_MAX_MEMO = 4000
_listed: set[tuple[str, str]] = set()


def reset_listed_memo() -> None:
    """Forget what is known to be listed. For tests, and for a Gateway swap."""
    _listed.clear()


def forget_listed(network_id: str, address: str) -> None:
    """Drop one confirmed listing, after its token is deleted from the list.

    Without this, replacing a ticker's holder would leave the deleted address
    memoized as listed — and every pool that uses it would trust a balance
    Gateway can no longer read.
    """
    _listed.discard(_memo_key(network_id, address))


def _memo_key(network_id: str, address: str) -> tuple[str, str]:
    return (network_id, address.lower())


def _remember(network_id: str, address: str) -> None:
    if len(_listed) >= _MAX_MEMO:
        _listed.clear()
    _listed.add(_memo_key(network_id, address))


def token_addresses(*values: str | None) -> list[str]:
    """The address-shaped values among ``values``, deduped, order preserved.

    Takes anything a caller happens to hold — a trading pair's two sides, a
    pool's two mints — and keeps what can actually be registered. A ticker quote
    (``USDC``, or an XRPL pair whose base is a symbol) is not an address and is
    dropped rather than sent upstream to fail.
    """
    seen: set[str] = set()
    addresses: list[str] = []
    for value in values:
        for part in str(value or "").split("-"):
            candidate = part.strip()
            if not candidate or not ADDRESS_RE.match(candidate):
                continue
            if candidate.lower() in seen:
                continue
            seen.add(candidate.lower())
            addresses.append(candidate)
    return addresses


async def _network_tokens(client: Any, network_id: str) -> list[dict]:
    """Gateway's whole token list for ``network_id``.

    Read whole rather than filtered because Gateway's ``search`` only looks at
    symbol and name — a mint address matches nothing, and a ticker matches by
    rules this module should not have to guess at. The list is a curated file
    per network, so the whole of it is a small response.
    """
    response = await client.gateway.get_network_tokens(network_id)
    tokens = response.get("tokens", []) if isinstance(response, dict) else response
    return [token for token in (tokens or []) if isinstance(token, dict)]


class TokenListSnapshot:
    """One read of a network's token list, shared for the length of one operation.

    Every listedness question in a single call — both mints of a pool, the
    address being registered, the ticker's holder — interrogates the same
    curated file, and reading it whole is the only way to ask about an address
    at all (see ``_network_tokens``). So the read is taken once, lazily on first
    need, and reused.

    Reused *only* until something writes. ``invalidate`` is called after every
    save and must be called after every delete, because the one thing this must
    never do is answer a post-write check from a pre-write copy: that check
    exists precisely to see live state, and a snapshot serving it would report a
    token as unlisted after Gateway had just listed it — or, worse on the
    replace path, name a ticker holder that has already been deleted and invite
    the user to delete it again.

    Scoped to one operation on purpose, never to a process or a TTL. Nothing
    here outlives the call that created it, so it cannot go stale for a later
    request and it never spans two users or two permission checks.
    """

    def __init__(self, client: Any, network_id: str) -> None:
        self._client = client
        self._network_id = network_id
        self._tokens: list[dict] | None = None

    async def tokens(self) -> list[dict]:
        """The list, read once. A failed read is not cached, so it retries."""
        if self._tokens is None:
            self._tokens = await _network_tokens(self._client, self._network_id)
        return self._tokens

    def invalidate(self) -> None:
        """Forget the read. Call after anything writes to this network's list."""
        self._tokens = None


async def _is_listed(
    client: Any,
    network_id: str,
    address: str,
    snapshot: "TokenListSnapshot | None" = None,
) -> bool:
    """Whether Gateway's token list for ``network_id`` already holds ``address``.

    The whole list is read, not ``search=<address>``: Gateway matches ``search``
    against symbol and name only, so asking for a mint always answered zero
    tokens. Every token then looked unlisted, the save that followed was refused
    with "already exists" (a *symbol* check, which the listed token's own ticker
    trips), and the re-read said unlisted again — so a correctly listed token
    like Solana USDC reported ``symbol_taken`` forever, the pool banner asked the
    user to add a token that was already there, and the conflict lookup could
    name no holder but the token itself.

    A network's list is Gateway's curated file — tens of entries, not the whole
    chain — so reading it whole is one small response, and confirmed hits are
    memoized by the caller anyway.

    ``snapshot`` lets a caller that is asking several of these in a row share
    one read; without it the list is read fresh, which is what a standalone call
    wants.
    """
    wanted = address.lower()
    tokens = await (snapshot or TokenListSnapshot(client, network_id)).tokens()
    return any(
        str((token or {}).get("address") or "").lower() == wanted for token in tokens
    )


async def find_symbol_holder(
    client: Any,
    network_id: str,
    symbol: str,
    exclude: str | None = None,
    snapshot: TokenListSnapshot | None = None,
) -> dict[str, Any] | None:
    """The token on ``network_id``'s list that holds ``symbol``, if any.

    This is the other half of a ``symbol_taken`` verdict: the verdict says the
    ticker is in use, this says by *whom* — which is what the user needs to
    decide whether the holder should be replaced. ``exclude`` skips the token
    being registered itself, so a half-written state cannot name it as its own
    conflict.

    ``snapshot`` shares a read the caller has already paid for — but only one
    taken after the last write, or this would name a holder that is no longer
    there.
    """
    wanted = symbol.lower()
    excluded = (exclude or "").lower()
    for entry in await (snapshot or TokenListSnapshot(client, network_id)).tokens():
        if str(entry.get("symbol") or "").lower() != wanted:
            continue
        address = str(entry.get("address") or "")
        if address.lower() == excluded:
            continue
        return {
            "symbol": entry.get("symbol"),
            "address": address,
            "name": entry.get("name"),
        }
    return None


async def ensure_tokens_listed(
    client: Any,
    network_id: str,
    addresses: Iterable[str],
    snapshot: TokenListSnapshot | None = None,
) -> dict[str, str]:
    """Register every address that Gateway does not already know, idempotently.

    Returns one verdict per address:

    - ``listed`` — already there, nothing was written.
    - ``added`` — fetched from GeckoTerminal by Gateway and saved.
    - ``symbol_taken`` — another token on the list already holds this one's
      ticker, so Gateway refused to save it. Balances for it will keep reading 0,
      and a *quote* in that state resolves to the other token's address, which is
      a wrong-token trade rather than a failed one. Reported, never forced: the
      fix is a human deciding which token owns the ticker.
    - ``failed`` — the lookup or the save errored; retryable, so nothing is
      remembered.

    Registration is sequential on purpose. Gateway's ``addToken`` is a
    read-modify-write of one JSON file, so two concurrent saves for the same
    network can drop one of the two tokens.

    A pool's two mints ask the same list the same question, so one read answers
    both: the addresses share a `TokenListSnapshot`, taken lazily *after* the
    memo has had its say — a fully memoized re-visit still costs zero requests,
    which an eager read at the top would spend for nothing. Every save drops the
    snapshot, so the verification below it reads live state and the next address
    sees the token this loop just registered. A caller running several of these
    around its own writes passes its own ``snapshot`` and owns invalidating it.
    """
    snapshot = snapshot or TokenListSnapshot(client, network_id)
    verdicts: dict[str, str] = {}
    for address in list(addresses)[:MAX_TOKENS_PER_CALL]:
        if _memo_key(network_id, address) in _listed:
            verdicts[address] = "listed"
            continue

        try:
            if await _is_listed(client, network_id, address, snapshot):
                _remember(network_id, address)
                verdicts[address] = "listed"
                continue
        except Exception as e:
            logger.warning("token lookup failed %s on %s: %s", address, network_id, e)
            verdicts[address] = "failed"
            continue

        try:
            # Address in, token out: Gateway resolves symbol, name and decimals
            # from GeckoTerminal itself, so Condor spends none of its own budget
            # and stores no metadata it would then have to keep fresh.
            await client.gateway.save_network_token(
                network_id=network_id, token_address=address
            )
        except Exception as e:
            logger.warning("token save failed %s on %s: %s", address, network_id, e)
            verdicts[address] = "failed"
            continue
        finally:
            # Whatever the save did — wrote, refused, or errored halfway — the
            # copy of the list in hand is worthless from here: for the check
            # below, and for every address after this one.
            snapshot.invalidate()

        # A save whose symbol collides with a token already on the list answers
        # 200 with "already exists" and writes nothing, so the response cannot be
        # trusted to mean the token is there. Re-reading the list can.
        try:
            saved = await _is_listed(client, network_id, address, snapshot)
        except Exception as e:
            logger.warning("token re-check failed %s on %s: %s", address, network_id, e)
            verdicts[address] = "failed"
            continue

        if saved:
            _remember(network_id, address)
            verdicts[address] = "added"
        else:
            logger.warning(
                "token %s on %s not saved: its symbol is held by another address; "
                "balances for it will read 0",
                address,
                network_id,
            )
            verdicts[address] = "symbol_taken"

    return verdicts
