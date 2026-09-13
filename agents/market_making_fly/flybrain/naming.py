"""Derived names for a HIP-3 pair: the bot, its controller config, the l2Book coin."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Names:
    pair: str  # XYZ:DRAM-USD  (Hummingbot trading_pair, uppercase)
    issuer: str  # xyz
    token: str  # DRAM
    coin: str  # xyz:DRAM       (Hyperliquid l2Book coin: lowercase issuer, uppercase token)
    bot_name: str  # dram-fly
    config_name: str  # dram_fly_mm


def pair_names(pair: str) -> Names:
    if ":" not in pair or not pair.endswith("-USD"):
        raise ValueError(f"HIP-3 pair must look like ISSUER:TOKEN-USD, got {pair!r}")
    if pair != pair.upper():
        raise ValueError(f"HIP-3 pair must be uppercase, got {pair!r}")
    issuer, rest = pair.split(":", 1)
    token = rest[: -len("-USD")]
    if not issuer or not token:
        raise ValueError(f"HIP-3 pair must look like ISSUER:TOKEN-USD, got {pair!r}")
    base = token.lower()
    return Names(
        pair=pair,
        issuer=issuer.lower(),
        token=token,
        coin=f"{issuer.lower()}:{token}",
        bot_name=f"{base}-fly",
        config_name=f"{base}_fly_mm",
    )


def parse_pairs(value: str, limit: int = 3) -> list[str]:
    pairs = [p.strip() for p in value.split(",") if p.strip()]
    if not pairs:
        raise ValueError("At least one pair is required")
    if len(pairs) > limit:
        raise ValueError(f"At most {limit} pairs, got {len(pairs)}")
    if len(set(pairs)) != len(pairs):
        raise ValueError("Duplicate pair")
    for pair in pairs:
        pair_names(pair)
    return pairs
