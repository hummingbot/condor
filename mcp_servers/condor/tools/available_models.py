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
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import httpx

from condor.acp import ACP_COMMANDS
from condor.acp.pydantic_ai_client import DEFAULT_BASE_URLS
from handlers.agents.openrouter_models import fetch_models

# Cloud providers Condor can address as "<prefix>:<model>", mapped to the env
# var holding each key. Presence of the key = the provider is usable. Sourced
# here (not hardcoded elsewhere) so the set stays in one place.
_CLOUD_KEY_ENVS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
}

# Local, keyless OpenAI-compatible servers. Base URLs come from the resolver's
# own defaults (DEFAULT_BASE_URLS) so they can never drift from what actually
# runs the model at request time.
_LOCAL_PREFIXES = ("ollama", "lmstudio")


def _npx_packages_installed() -> set[str]:
    """npm packages resolvable WITHOUT a network install: local, global, npx cache.

    Mirrors how npx resolves a package before falling back to downloading it.
    ``shutil.which("npx")`` says nothing about the package — npx exists whenever
    Node does — so probing the launcher would mark every ``npx``-run bridge
    available on any machine with Node.
    """
    roots = [Path.cwd() / "node_modules"]
    npm = shutil.which("npm")
    if npm:
        try:
            proc = subprocess.run(
                [npm, "root", "-g"], capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0 and proc.stdout.strip():
                roots.append(Path(proc.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            pass  # npm unusable = nothing installed via it; local + cache still count
    roots.extend((Path.home() / ".npm" / "_npx").glob("*/node_modules"))

    installed: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.name.startswith("@"):
                installed.update(f"{entry.name}/{sub.name}" for sub in entry.iterdir())
            else:
                installed.add(entry.name)
    return installed


def _acp_clis() -> list[dict]:
    """ACP CLI bridges and whether each one is actually installed.

    A bridge launched as ``npx <package> …`` is available only if the PACKAGE is
    already installed; a bare command is available if it is on PATH. Note that
    ``available`` means "launchable" — every ACP bridge additionally needs its
    own interactive login (Claude/Google/GitHub/OpenAI), which cannot be probed
    from here, so an available bridge is not necessarily a working one.
    """
    npx_packages = _npx_packages_installed()
    out: list[dict] = []
    for base, cmd in ACP_COMMANDS.items():
        parts = cmd.split()
        if not parts:
            available = False
        elif parts[0] == "npx":
            available = parts[1] in npx_packages if len(parts) > 1 else False
        else:
            available = shutil.which(parts[0]) is not None
        out.append({"agent_key": base, "command": cmd, "available": available})
    return out


async def _local_servers() -> dict:
    """Probe local OpenAI-compatible servers and list their loaded models.

    Unreachable servers are reported (``reachable: false``), not raised — the
    user just isn't running that server.
    """
    result: dict = {}
    timeout = httpx.Timeout(connect=1.5, read=3.0, write=1.5, pool=1.5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for prefix in _LOCAL_PREFIXES:
            base = DEFAULT_BASE_URLS.get(prefix)
            entry = {"base_url": base, "reachable": False, "models": []}
            if base:
                try:
                    resp = await client.get(f"{base.rstrip('/')}/models")
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        entry["reachable"] = True
                        entry["models"] = sorted(
                            m["id"] for m in data if isinstance(m, dict) and m.get("id")
                        )
                except Exception:
                    pass  # not running = normal, leave reachable=False
            result[prefix] = entry
    return result


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
        prefix: bool(os.environ.get(env)) for prefix, env in _CLOUD_KEY_ENVS.items()
    }
    return {
        "acp_clis": _acp_clis(),
        "cloud_keys": cloud_keys,
        "custom_endpoints": await _custom_endpoints(),
        "local": await _local_servers(),
        "openrouter": await _openrouter(openrouter_query, openrouter_limit),
    }
