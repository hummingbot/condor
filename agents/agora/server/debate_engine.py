"""Self-contained Agora debate engine — the 'parliament'.

Runs the multi-agent debate that the Condor `agora_debate` routine calls over
HTTP. Twelve specialised LLM roles deliberate every asset:

  Phase 1  Intelligence   market / social / news / fundamentals analysts (parallel)
  Phase 2  Investment     bull researcher  <->  bear researcher  ->  research judge
  Phase 3  Risk           aggressive / neutral / conservative  ->  risk judge
  Phase 4  Verdict        final BUY / SELL / HOLD  (direction + confidence)

The engine talks to an OpenAI-compatible chat endpoint (opencode-go /
deepseek-v4-flash by default), so there is no heavy framework dependency and
nothing to GPU. A mock mode returns a deterministic verdict with a full transcript,
so the entire Condor pipeline can be exercised with no API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("agora.engine")

CONFIG_PATH = Path(__file__).resolve().parent / "agora_config.yaml"
_ASSET_CONTEXT: dict[str, str] = {
    "BTC": "BTC is crypto's reference market — driven by macro liquidity, ETF flows and risk sentiment.",
    "XAU": "XAU is spot gold quoted in USDT — driven by real yields, the US dollar and safe-haven flow, NOT crypto sentiment.",
    "CL": "CL is WTI crude oil quoted in USDT — driven by OPEC+ supply policy, inventories and geopolitics, NOT crypto sentiment.",
    "XAG": "XAG is spot silver — monetary drivers like gold plus higher industrial beta.",
}


@dataclass
class EngineConfig:
    pairs: list[str] = field(default_factory=lambda: ["BTC-USDT", "XAU-USDT", "CL-USDT"])
    aliases: dict[str, str] = field(default_factory=dict)
    model: str = "deepseek-v4-pro"
    backend_url: str = "https://opencode.ai/zen/go/v1"
    api_key: str = ""
    temperature: float = 0.3
    timeout: int = 90
    max_tokens: int = 2000  # generous: reasoning models spend tokens on CoT first
    max_debate_rounds: int = 1
    max_risk_rounds: int = 1
    mock_mode: bool = True  # safe default: deterministic verdicts, no key required
    rate_limit_seconds: int = 180
    confidence: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key_set(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "EngineConfig":
        try:
            import yaml  # type: ignore
        except Exception:  # pyyaml may be absent; fall back to sane defaults.
            logger.warning("PyYAML not found — using default engine config.")
            return cls()

        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}

        llm = raw.get("llm", {})
        # Resolve the same way Condor resolves custom@opencode-go:...:
        #   base_url -> AGORA_BACKEND_URL env, else config, else CUSTOM_LLM_BASE_URL
        #   api_key  -> AGORA_API_KEY env, else config api_key_env, else OPENCODE_GO_API_KEY
        backend_url = (
            os.getenv("AGORA_BACKEND_URL")
            or llm.get("backend_url")
            or os.getenv("CUSTOM_LLM_BASE_URL", "")
        )
        api_key = (
            os.getenv("AGORA_API_KEY")
            or os.getenv(llm.get("api_key_env", "CUSTOM_LLM_API_KEY"))
            or os.getenv("OPENCODE_GO_API_KEY", "")
        )
        return cls(
            pairs=raw.get("pairs", cls().pairs),
            aliases=raw.get("pair_aliases", {}),
            model=llm.get("model", cls().model),
            backend_url=backend_url,
            api_key=api_key,
            temperature=float(llm.get("temperature", cls().temperature)),
            timeout=int(llm.get("request_timeout_seconds", cls().timeout)),
            max_tokens=int(llm.get("max_tokens", cls().max_tokens)),
            max_debate_rounds=int(raw.get("debate", {}).get("max_debate_rounds", 1)),
            max_risk_rounds=int(raw.get("debate", {}).get("max_risk_rounds", 1)),
            mock_mode=bool(raw.get("debate", {}).get("mock_mode", False)),
            rate_limit_seconds=int(raw.get("server", {}).get("rate_limit_seconds", 180)),
            confidence=raw.get("confidence", {}),
        )


def _resolve_pair(pair: str, aliases: dict[str, str]) -> str:
    """'BTC'/'GOLD' -> 'BTC-USDT'."""
    return aliases.get(pair.upper(), pair.upper())


def _base_of(pair: str) -> str:
    return pair.split("-")[0]


# ── LLM call ──────────────────────────────────────────────────────────────
async def _chat(system: str, user: str, cfg: EngineConfig) -> str:
    """Single chat completion via the OpenAI-compatible endpoint.

    Handles reasoning models (deepseek-v4-pro/flash): they emit their chain of
    thought in ``reasoning_content`` and the actual answer in ``content``. We
    budget enough tokens for the reasoning step and prefer ``content``, falling
    back to ``reasoning_content`` (clipped) when the answer is empty.
    """
    if cfg.mock_mode:
        return _mock_reply(system, user, cfg)

    from openai import AsyncOpenAI  # imported lazily so mock mode needs nothing

    client = AsyncOpenAI(api_key=cfg.api_key or "empty", base_url=cfg.backend_url)
    try:
        resp = await client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
        )
        msg = resp.choices[0].message
        answer = (msg.content or "").strip()
        if not answer:
            reason = (getattr(msg, "reasoning_content", None) or "").strip()
            answer = reason[:1600] if reason else ""
        return answer
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM call failed: %s", exc)
        raise


def _mock_reply(system: str, user: str, cfg: EngineConfig) -> str:
    """Deterministic, asset-aware transcript for key-free demos.

    Parses the asset from the user brief so each role's mock argument reads as a
    real debate, not a placeholder. The final verdict is still computed from the
    judge text by the normal parser, so the confidence/decision path is real.
    """
    sys_l = system.lower()
    pair = "the asset"
    for tok in ("btc", "xau", "cl", "xag", "gold", "oil"):
        if tok in user.lower():
            pair = {"btc": "BTC", "xau": "gold (XAU)", "cl": "WTI crude (CL)",
                    "xag": "silver (XAG)", "gold": "gold (XAU)", "oil": "WTI crude (CL)"}.get(tok, pair)
            break

    if "market analyst" in sys_l:
        return (f"[{pair}] Technicals: price below the 50-period mean, RSI near 50, "
                f"volume unremarkable. No strong directional momentum; range-bound bias.")
    if "social" in sys_l:
        return (f"[{pair}] Crowd positioning is balanced. "
                f"(Note: for commodities, crypto-native social signal is not a primary driver.)")
    if "news" in sys_l:
        return (f"[{pair}] No dominant headline. Macro calendar light; "
                f"watching upcoming policy cues that could shift the regime.")
    if "fundamentals" in sys_l:
        return (f"[{pair}] Structural drivers intact. Supply/demand balanced; "
                f"no acute catalyst that forces a directional view here.")
    if "bull" in sys_l:
        return (f"[{pair}] The case FOR long: downside looks contained, any positive "
                f"macro surprise skews risk to the upside. Conviction 62/100.")
    if "bear" in sys_l:
        return (f"[{pair}] The case FOR short / caution: momentum is absent and the "
                f"range can resolve lower on risk-off flows. Conviction 58/100.")
    if "research manager" in sys_l:
        return (f"INVESTMENT PLAN: NEUTRAL. The bull and bear cases are close; "
                f"absent a catalyst, stand aside rather than force a directional bet. "
                f"Conviction 60/100.")
    if "aggressive" in sys_l:
        return (f"[{pair}] Size toward the upper bound of the risk budget; the payoff "
                f"asymmetry favours participation at these levels.")
    if "neutral" in sys_l:
        return (f"[{pair}] Moderate sizing, standard risk budget; no reason to deviate.")
    if "conservative" in sys_l:
        return (f"[{pair}] Preserve capital: small size, tight invalidation; if wrong, "
                f"losses stay trivial.")
    if "risk judge" in sys_l:
        return (f"FINAL DECISION: HOLD. Research leans neutral and no edge clears the "
                f"bar; do not deploy until a catalyst resolves the range.")
    return "(mock) reasoned analysis"


# ── Prompts ───────────────────────────────────────────────────────────────
_ANALYST_ROLE = {
    "market": (
        "You are the Market Analyst for an AI trading debate. You argue only from "
        "technical price action: trend, momentum (RSI), support/resistance, volume. "
        "Be specific and concise (under 180 words)."
    ),
    "social": (
        "You are the Social Sentiment Analyst. You assess crowd positioning, social "
        "buzz and retail sentiment. If the asset is a commodity (gold/oil), state "
        "explicitly that crypto-native social sentiment is NOT relevant. Concise."
    ),
    "news": (
        "You are the News Analyst. You assess macro and asset-specific headlines and "
        "their directional bias. Concise (under 180 words)."
    ),
    "fundamentals": (
        "You are the Fundamentals Analyst. You assess valuation, flows and structural "
        "drivers. For commodities, focus on supply/demand and policy. Concise."
    ),
}

_DEBATER_ROLE = {
    "bull": (
        "You are the Bull Researcher in a trading debate. Using the analyst reports, "
        "make the strongest case FOR a long position. Cite specific evidence. "
        "State your conviction 0-100. Concise (under 200 words)."
    ),
    "bear": (
        "You are the Bear Researcher in a trading debate. Using the analyst reports, "
        "make the strongest case FOR a short position (or why not to be long). "
        "Cite specific evidence. State your conviction 0-100. Concise (under 200 words)."
    ),
}

_RISK_ROLE = {
    "aggressive": (
        "You are the Aggressive Risk Analyst. You argue for maximum warranted "
        "position size and conviction. Concise (under 120 words)."
    ),
    "neutral": (
        "You are the Neutral Risk Analyst. You argue for balanced, moderate sizing. "
        "Concise (under 120 words)."
    ),
    "conservative": (
        "You are the Conservative Risk Analyst. You argue for capital preservation and "
        "what could go wrong. Concise (under 120 words)."
    ),
}


def _investment_brief(pair: str, packet: dict) -> str:
    base = _base_of(pair)
    ctx = _ASSET_CONTEXT.get(base, "")
    return (
        f"ASSET: {pair} ({base}).\n{ctx}\n\n"
        f"BRIEFING PACKET (analyst inputs):\n{packet.get('market_data', '')}\n\n"
        f"{packet.get('fundamentals', '')}\n\n"
        f"News: {packet.get('news_data', '') or '(none)'}\n"
        f"Social: {packet.get('social_data', '') or '(none)'}\n\n"
        f"Decide the directional case. No hedging: pick a side."
    )


# ── Debate orchestration ───────────────────────────────────────────────────
async def run_debate(pair: str, packet: dict, cfg: EngineConfig) -> dict[str, Any]:
    """Run the full 4-phase debate and return a structured transcript + verdict.

    `packet` keys: market_data, fundamentals, news_data, social_data.
    """
    pair = _resolve_pair(pair, cfg.aliases)
    base = _base_of(pair)
    brief = _investment_brief(pair, packet)

    # Phase 1 — analysts in parallel
    analyst_keys = ["market", "social", "news", "fundamentals"]
    analyst_reports = {}
    async def _analyst(k):
        return k, await _chat(_ANALYST_ROLE[k], brief, cfg)
    for k, report in await asyncio.gather(*(_analyst(k) for k in analyst_keys)):
        analyst_reports[k] = report

    analyst_block = "\n\n".join(
        f"[{k.upper()}]\n{analyst_reports[k]}" for k in analyst_keys
    )

    # Phase 2 — bull vs bear (rounds)
    bull_hist, bear_hist = [], []
    bull_arg = bear_arg = ""
    for r in range(max(1, cfg.max_debate_rounds)):
        round_ctx = (
            f"{brief}\n\nANALYST REPORTS:\n{analyst_block}\n\n"
            f"BULL SO FAR:\n{bull_arg}\n\nBEAR SO FAR:\n{bear_arg}"
        )
        bull_arg, bear_arg = await asyncio.gather(
            _chat(_DEBATER_ROLE["bull"], round_ctx + "\n\nPresent your case.", cfg),
            _chat(_DEBATER_ROLE["bear"], round_ctx + "\n\nPresent your case.", cfg),
        )
        bull_hist.append(bull_arg)
        bear_hist.append(bear_arg)

    invest_ctx = (
        f"{brief}\n\nANALYST REPORTS:\n{analyst_block}\n\n"
        f"BULL CASE:\n{bull_arg}\n\nBEAR CASE:\n{bear_arg}"
    )
    research_judge = await _chat(
        "You are the Research Manager (judge). Weigh the bull and bear cases and "
        "issue an INVESTMENT PLAN: a single directional lean (LONG/SHORT/NEUTRAL), "
        "target rationale, and conviction 0-100. Concise (under 200 words).",
        invest_ctx,
        cfg,
    )

    # Phase 3 — risk debate
    risk_ctx = f"{invest_ctx}\n\nRESEARCH JUDGE:\n{research_judge}"
    agg, neu, cons = await asyncio.gather(
        _chat(_RISK_ROLE["aggressive"], risk_ctx, cfg),
        _chat(_RISK_ROLE["neutral"], risk_ctx, cfg),
        _chat(_RISK_ROLE["conservative"], risk_ctx, cfg),
    )
    risk_judge = await _chat(
        "You are the Risk Judge. Given the investment plan and the three risk "
        "viewpoints, issue the FINAL DECISION as the first word: BUY, SELL or HOLD, "
        "then justify it briefly. Concise (under 150 words).",
        f"{risk_ctx}\n\nAGGRESSIVE: {agg}\n\nNEUTRAL: {neu}\n\nCONSERVATIVE: {cons}",
        cfg,
    )

    # Phase 4 — verdict + confidence
    decision, direction, confidence = _parse_verdict(
        research_judge, risk_judge, bull_hist, bear_hist, cfg
    )

    return {
        "pair": pair,
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        "rationale": research_judge,
        "risk_assessment": risk_judge,
        "bull_case": "\n\n".join(bull_hist),
        "bear_case": "\n\n".join(bear_hist),
        "analyst_reports": analyst_reports,
        "reports": analyst_reports,  # alias used by agora_debate normaliser
    }


def _parse_verdict(
    research_judge: str,
    risk_judge: str,
    bull_hist: list[str],
    bear_hist: list[str],
    cfg: EngineConfig,
) -> tuple[str, str, int]:
    text = f"{research_judge}\n{risk_judge}".upper()
    if "BUY" in text and "SELL" not in text:
        decision, direction = "BUY", "LONG"
    elif "SELL" in text and "BUY" not in text:
        decision, direction = "SELL", "SHORT"
    elif "BUY" in text and "SELL" in text:
        decision = "BUY" if text.rfind("BUY") > text.rfind("SELL") else "SELL"
        direction = "LONG" if decision == "BUY" else "SHORT"
    else:
        decision, direction = "HOLD", "NONE"

    c = cfg.confidence
    conf = int(c.get("baseline", 60))
    invest_history = "\n".join(bull_hist + bear_hist)
    if len(invest_history) > 2000:
        conf += int(c.get("thorough_debate_bonus", 10))
    risk_history = f"{research_judge}\n{risk_judge}"
    if len(risk_history) > 1000:
        conf += int(c.get("substantive_risk_bonus", 5))
    hedges = ("might", "could", "perhaps", "uncertain", "maybe", "possibly")
    if not any(h in research_judge.lower() for h in hedges):
        conf += int(c.get("decisive_judge_bonus", 10))
    if not any(h in risk_judge.lower() for h in hedges):
        conf += int(c.get("decisive_risk_judge_bonus", 5))

    return decision, direction, min(conf, int(c.get("max", 95)))
