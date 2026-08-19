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


async def _is_listed(client: Any, network_id: str, address: str) -> bool:
    """Whether Gateway's token list for ``network_id`` already holds ``address``.

    Searched rather than listed in full: Gateway matches ``search`` against symbol,
    name *and* address, so this is one small response instead of the whole list.
    An API that narrowed the search to symbols would only cost a redundant save,
    which is itself a no-op — so a false negative here is safe.
    """
    response = await client.gateway.get_network_tokens(network_id, search=address)
    tokens = response.get("tokens", []) if isinstance(response, dict) else response
    wanted = address.lower()
    return any(
        str((token or {}).get("address") or "").lower() == wanted
        for token in (tokens or [])
    )


async def ensure_tokens_listed(
    client: Any, network_id: str, addresses: Iterable[str]
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
    """
    verdicts: dict[str, str] = {}
    for address in list(addresses)[:MAX_TOKENS_PER_CALL]:
        if _memo_key(network_id, address) in _listed:
            verdicts[address] = "listed"
            continue

        try:
            if await _is_listed(client, network_id, address):
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

        # A save whose symbol collides with a token already on the list answers
        # 200 with "already exists" and writes nothing, so the response cannot be
        # trusted to mean the token is there. Re-reading the list can.
        try:
            saved = await _is_listed(client, network_id, address)
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
