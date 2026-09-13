"""Derived names for a trading pair: the bot, its controller config, its book key.

Two pair shapes are accepted, both uppercase as Hummingbot writes them:

* ``BASE-QUOTE``            — any CLOB spot or perp market, e.g. ``SOL-USDT``
* ``ISSUER:TOKEN-QUOTE``    — a Hyperliquid HIP-3 market, e.g. ``XYZ:ORCL-USD``

The derived slug carries the **whole** pair, not just its base token. Two
markets on the same token are otherwise the same bot: ``BTC-USDT`` and
``BTC-USDC`` both quoting under one name would have the fly read one book's
P&L for the other and update the wrong controller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Docker container names must start alphanumeric and contain only
# [a-zA-Z0-9_.-]; the slug feeds one, so it is restricted to that.
_SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9.-]*")


@dataclass(frozen=True)
class Names:
    pair: str  # SOL-USDT           | XYZ:ORCL-USD
    base: str  # SOL                | ORCL
    quote: str  # USDT              | USD
    issuer: str  # ""               | xyz
    slug: str  # sol-usdt           | xyz-orcl-usd
    bot_name: str  # sol-usdt-fly   | xyz-orcl-usd-fly
    config_name: str  # sol_usdt_fly_mm | xyz_orcl_usd_fly_mm
    hl_coin: str  # ""              | xyz:ORCL  (Hyperliquid l2Book key)


def pair_names(pair: str) -> Names:
    """Parse a pair into everything derived from it, or refuse."""
    if not isinstance(pair, str) or not pair:
        raise ValueError("trading pair is required")
    if pair != pair.upper():
        raise ValueError(f"trading pair must be uppercase, got {pair!r}")
    if "-" not in pair:
        raise ValueError(
            f"trading pair must look like BASE-QUOTE or ISSUER:TOKEN-QUOTE, got {pair!r}"
        )
    head, quote = pair.rsplit("-", 1)
    issuer = ""
    base = head
    if ":" in head:
        issuer, base = head.split(":", 1)
        if ":" in base:
            raise ValueError(f"trading pair has more than one issuer prefix: {pair!r}")
    if not base or not quote or (":" in head and not issuer):
        raise ValueError(
            f"trading pair must look like BASE-QUOTE or ISSUER:TOKEN-QUOTE, got {pair!r}"
        )
    slug = pair.lower().replace(":", "-").replace("-", "-")
    if not _SAFE_SLUG.fullmatch(slug):
        raise ValueError(f"trading pair {pair!r} does not make a usable bot name")
    return Names(
        pair=pair,
        base=base,
        quote=quote,
        issuer=issuer.lower(),
        slug=slug,
        bot_name=f"{slug}-fly",
        config_name=f"{slug.replace('-', '_').replace('.', '_')}_fly_mm",
        # Hyperliquid keys a HIP-3 book by lowercase issuer + uppercase token,
        # with no quote suffix. A non-HIP-3 pair has no such key.
        hl_coin=f"{issuer.lower()}:{base}" if issuer else "",
    )


def parse_pairs(value: str, limit: int = 3) -> list[str]:
    pairs = [p.strip() for p in value.split(",") if p.strip()]
    if not pairs:
        raise ValueError("At least one pair is required")
    if len(pairs) > limit:
        raise ValueError(f"At most {limit} pairs, got {len(pairs)}")
    if len(set(pairs)) != len(pairs):
        raise ValueError("Duplicate pair")
    slugs = [pair_names(p).slug for p in pairs]
    if len(set(slugs)) != len(slugs):
        raise ValueError(f"Pairs collide on their derived bot name: {sorted(slugs)}")
    return pairs
