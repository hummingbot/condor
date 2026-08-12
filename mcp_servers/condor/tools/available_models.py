"""Detect which models/providers are actually usable right now.

Backs the agent_builder skill's model recommendation: instead of guessing or
hardcoding a default, the assistant asks what the operator has actually
configured — installed ACP CLI bridges, cloud API keys present in the
environment, reachable local model servers (Ollama / LM Studio) with the models
they have loaded, their own saved OpenAI-compatible endpoints (Venice, Together,
a self-hosted vLLM…), and (only when an OpenRouter key is set) the tool-capable
OpenRouter catalog, cheapest first.

Read-only and side-effect-free. NEVER returns a key value — only whether each
key's env var is set. An unreachable local server is a normal state (the user
simply isn't running it), reported as ``reachable: false``, never raised.

The detection itself lives in :mod:`handlers.agents.readiness`, shared with the
setup wizard (``condor/setup_llm.py``) so the two can never disagree about what
this machine can run.
"""

from __future__ import annotations

import os

from handlers.agents.openrouter_models import fetch_models
from handlers.agents.readiness import CLOUD_KEY_ENVS, acp_bridges, local_servers


async def _openrouter(query: str, limit: int) -> dict:
    """Tool-capable OpenRouter catalog, cheapest first.

    The catalog is public, so recommendations work WITHOUT a key. ``key_present``
    reports whether OPENROUTER_API_KEY is set — i.e. whether these models can be
    RUN now, or are shown as options the operator would need to enable a key for.
    """
    key_present = bool(os.environ.get("OPENROUTER_API_KEY"))
    models = await fetch_models()  # already filtered to tool-calling support
    q = query.strip().lower()
    matched = [m for m in models if not q or q in m.slug.lower() or q in m.name.lower()]
    matched.sort(key=lambda m: (m.prompt_price, m.slug))  # cheapest input price first
    shown = matched[:limit]
    return {
        "key_present": key_present,
        "total_matching": len(matched),
        "shown": len(shown),
        "models": [
            {
                "agent_key": f"openrouter:{m.slug}",
                "name": m.name,
                "context_length": m.context_length,
                "usd_per_mtok_in": round(m.prompt_price, 4),
                "usd_per_mtok_out": round(m.completion_price, 4),
            }
            for m in shown
        ],
    }


async def _custom_endpoints() -> list[dict]:
    """The user's own saved OpenAI-compatible endpoints and the models they serve.

    These are the strongest signal in the whole report: the operator added each
    one deliberately and Condor validated it reachable at save time. Models are
    re-fetched here so a stale endpoint shows up as ``reachable: false`` rather
    than being recommended and failing on the first consult.
    """
    from condor.preferences import get_custom_providers, load_user_data_for
    from handlers.agents.custom_models import CustomProviderError, fetch_models
    from mcp_servers.condor.settings import settings

    try:
        providers = get_custom_providers(load_user_data_for(settings.user_id))
    except Exception:
        return []

    out: list[dict] = []
    for provider in providers:
        entry = {
            "name": provider["name"],
            "base_url": provider["base_url"],
            "reachable": False,
            "models": [],
        }
        try:
            _, models = await fetch_models(
                provider["base_url"], provider.get("api_key", "")
            )
            entry["reachable"] = True
            entry["models"] = [
                {"agent_key": f"custom@{provider['name']}:{m}", "model": m}
                for m in models
            ]
        except CustomProviderError:
            pass  # endpoint down or key rejected — report it, don't raise
        out.append(entry)
    return out


async def get_available_models(
    openrouter_query: str = "", openrouter_limit: int = 20
) -> dict:
    """Report every model/provider currently usable for an agent's ``agent_key``."""
    cloud_keys = {
        prefix: bool(os.environ.get(env)) for prefix, env in CLOUD_KEY_ENVS.items()
    }
    return {
        "acp_clis": acp_bridges(),
        "cloud_keys": cloud_keys,
        "custom_endpoints": await _custom_endpoints(),
        "local": await local_servers(),
        "openrouter": await _openrouter(openrouter_query, openrouter_limit),
    }
