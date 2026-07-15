"""World Cup winner odds + token resolver for the World Cup Bettor.

Reads the Polymarket **public gamma API** (no creds, not geo-blocked — only CLOB
order *placement* is) for the winner event, then resolves each requested target
(``Team:Side``) to its ERC-1155 outcome ``token_id`` and current book so the
agent can place attractive limit buys. Buying "Spain No" means holding Spain's
**No** token; buying "Argentina Yes" means holding Argentina's **Yes** token.
"""

import json
import logging
import urllib.request

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

_GAMMA = "https://gamma-api.polymarket.com/events?slug="


class Config(BaseModel):
    """Resolve Polymarket winner-market token ids + live prices for the targets."""

    event_slug: str = Field(default="world-cup-winner", description="Polymarket event slug")
    targets: list[str] = Field(
        default=["Argentina:Yes", "Spain:No", "England:No"],
        description="Team:Side entries to resolve (Side is Yes or No)",
    )


def _fetch_event(slug: str) -> dict:
    req = urllib.request.Request(_GAMMA + slug, headers={"User-Agent": "condor-worldcup/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode())
    if not data:
        raise RuntimeError(f"gamma returned no event for slug={slug!r}")
    return data[0] if isinstance(data, list) else data


def _markets_by_team(event: dict) -> dict:
    """team (lowercased) -> {yes_token, no_token, yes_price, no_price, best_bid,
    best_ask, closed}. Skips resolved/closed markets."""
    out = {}
    for m in event.get("markets", []):
        team = (m.get("groupItemTitle") or "").strip()
        if not team:
            continue
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            token_ids = json.loads(m.get("clobTokenIds") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if len(outcomes) != 2 or len(token_ids) != 2 or len(prices) != 2:
            continue
        # outcomes align with token_ids and prices index-for-index (Yes, No).
        idx = {o.lower(): i for i, o in enumerate(outcomes)}
        yi, ni = idx.get("yes", 0), idx.get("no", 1)
        out[team.lower()] = {
            "team": team,
            "yes_token": token_ids[yi],
            "no_token": token_ids[ni],
            "yes_price": float(prices[yi]),
            "no_price": float(prices[ni]),
            "best_bid": _f(m.get("bestBid")),
            "best_ask": _f(m.get("bestAsk")),
            "closed": bool(m.get("closed")),
        }
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    import asyncio

    try:
        event = await asyncio.to_thread(_fetch_event, config.event_slug)
    except Exception as e:
        return f"Error fetching Polymarket event {config.event_slug!r}: {e}"

    teams = _markets_by_team(event)
    lines = [f"**Polymarket: {event.get('title', config.event_slug).strip()}**"]
    resolved = []
    for target in config.targets:
        if ":" not in target:
            lines.append(f"- {target}: bad target (use Team:Side)")
            continue
        team_name, side = target.split(":", 1)
        m = teams.get(team_name.strip().lower())
        if m is None:
            lines.append(f"- {target}: team not found in event")
            continue
        if m["closed"]:
            lines.append(f"- {target}: market CLOSED/resolved — skip")
            continue
        is_yes = side.strip().lower() == "yes"
        token = m["yes_token"] if is_yes else m["no_token"]
        # Price of the token you'd HOLD. For No, that's (1 - Yes side prices).
        token_bid = m["best_bid"] if is_yes else (1 - m["best_ask"]) if m["best_ask"] is not None else None
        token_ask = m["best_ask"] if is_yes else (1 - m["best_bid"]) if m["best_bid"] is not None else None
        token_mid = m["yes_price"] if is_yes else m["no_price"]
        resolved.append({
            "target": target, "token_id": token,
            "bid": token_bid, "ask": token_ask, "mid": token_mid,
        })
        lines.append(
            f"- **{m['team']} {side.strip().title()}**: token={token} | "
            f"bid={_r(token_bid)} ask={_r(token_ask)} mid={_r(token_mid)} "
            f"(implied {token_mid * 100:.1f}%)"
        )

    lines.append(
        "\nTo accumulate a target, place an order_pred LIMIT BUY with market=token_id, "
        "venue=polymarket, order_type=limit, limit_px BELOW ask (attractive), amount_quote>=10 (Polymarket min)."
    )
    return "\n".join(lines)


def _r(v):
    return round(v, 4) if isinstance(v, (int, float)) else "n/a"
