"""One answer to "can Condor actually run this model right now?".

Every surface that offers a model — the ``get_available_models`` MCP tool behind
the agent_builder skill, and the setup wizard (``condor/setup_llm.py``) — asks
this module, so they can never disagree about what a machine can run. The probes
source their commands from :data:`condor.acp.ACP_COMMANDS` and their base URLs
from :data:`condor.acp.pydantic_ai_client.DEFAULT_BASE_URLS`, so adding a
provider there lights it up here for free.

Three states, never two: a CLI bridge that is installed but whose login cannot be
proven is ``UNVERIFIED``, not ready and not missing. The login markers below are
undocumented paths that upstream CLIs may move, so they only ever downgrade a row
from *ready* to *unverified* — never to *missing*, and never enough to refuse a
pick.

Read-only and side-effect-free. Never returns a key value, and an unreachable
local server is a normal state (the user simply isn't running it), reported as
``MISSING`` with the command that starts it, never raised.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

from condor.acp import ACP_COMMANDS
from condor.acp.pydantic_ai_client import DEFAULT_BASE_URLS

READY, UNVERIFIED, MISSING = "ready", "unverified", "missing"

# Cloud providers Condor can address as "<prefix>:<model>", mapped to the env
# var holding each key. Presence of the key = the provider is usable. Sourced
# here (not hardcoded per surface) so the set stays in one place.
CLOUD_KEY_ENVS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
}

# Local, keyless OpenAI-compatible servers. Base URLs come from the resolver's
# own defaults (DEFAULT_BASE_URLS) so they can never drift from what actually
# runs the model at request time.
LOCAL_PREFIXES = ("ollama", "lmstudio")

# What to run once, interactively, to log a bridge in. Not run from here: the
# wizard reports and instructs, it never drives someone else's OAuth flow.
_LOGIN_COMMANDS = {
    "claude-code": "claude",
    "claude-acp": "claude",
    "gemini": "npx @google/gemini-cli",
    "copilot": "npx @github/copilot",
    "codex": "codex",
}

# Bridges launched as a bare command still come from npm; name the package so a
# missing one is actionable instead of just absent.
_BARE_INSTALL = {
    "claude-agent-acp": "npm install -g @agentclientprotocol/claude-agent-acp",
}

_START_COMMANDS = {
    "ollama": "start it with `ollama serve`",
    "lmstudio": "start LM Studio and enable its local server",
}


@dataclass(frozen=True)
class Readiness:
    """What a menu row needs: a state, a sentence, and any models behind it."""

    state: str  # READY | UNVERIFIED | MISSING
    detail: str  # human sentence; when not READY it names the fix
    models: list[str] = field(default_factory=list)  # local server models; [] elsewhere

    @property
    def usable(self) -> bool:
        """False only for MISSING — an unproven login must never block a pick."""
        return self.state != MISSING


def npx_packages_installed() -> set[str]:
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


def _keychain_has(service: str) -> bool | None:
    """macOS keychain lookup: True found, False absent, None cannot tell."""
    security = shutil.which("security")
    if not security:
        return None
    try:
        proc = subprocess.run(
            [security, "find-generic-password", "-s", service],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode == 0


def acp_login_state(base: str) -> bool | None:
    """Has this bridge's interactive login already happened?

    ``True`` a credential exists, ``False`` none was found where that CLI keeps
    one, ``None`` this bridge has no marker we can read (GitHub Copilot) or the
    machine could not be asked. Heuristic by construction: a credentials file
    proves a login happened, not that the token is still live, and its absence
    proves nothing on a machine using another auth path — which is why callers
    treat False and None alike as *unverified*, never as *not installed*.
    """
    home = Path.home()
    if base in ("claude-code", "claude-acp"):
        if (home / ".claude" / ".credentials.json").exists():
            return True
        if sys.platform == "darwin":  # Claude Code stores it in the login keychain
            return _keychain_has("Claude Code-credentials")
        return False
    if base == "gemini":
        if (home / ".gemini" / "oauth_creds.json").exists():
            return True
        return bool(os.environ.get("GEMINI_API_KEY")) or False
    if base == "codex":
        return (home / ".codex" / "auth.json").exists()
    return None  # copilot and anything new: no documented on-disk marker


def install_command(cmd: str) -> str:
    """How to install the CLI behind an ACP command line."""
    parts = cmd.split()
    if len(parts) > 1 and parts[0] == "npx":
        return f"npm install -g {parts[1]}"
    if parts and parts[0] in _BARE_INSTALL:
        return _BARE_INSTALL[parts[0]]
    return f"install `{cmd.split()[0] if parts else cmd}` and put it on PATH"


def acp_bridges() -> list[dict]:
    """ACP CLI bridges: whether each is installed, and whether it is logged in.

    A bridge launched as ``npx <package> …`` is available only if the PACKAGE is
    already installed; a bare command is available if it is on PATH. ``available``
    means "launchable" — a bridge additionally needs its own interactive login,
    reported separately as ``logged_in`` (``None`` = no marker to read).
    """
    npx_packages = npx_packages_installed()
    out: list[dict] = []
    for base, cmd in ACP_COMMANDS.items():
        parts = cmd.split()
        if not parts:
            available = False
        elif parts[0] == "npx":
            available = parts[1] in npx_packages if len(parts) > 1 else False
        else:
            available = shutil.which(parts[0]) is not None
        out.append(
            {
                "agent_key": base,
                "command": cmd,
                "available": available,
                "logged_in": acp_login_state(base) if available else None,
            }
        )
    return out


async def local_servers() -> dict:
    """Probe local OpenAI-compatible servers and list their loaded models.

    Unreachable servers are reported (``reachable: false``), not raised — the
    user just isn't running that server.
    """
    result: dict = {}
    timeout = httpx.Timeout(connect=1.5, read=3.0, write=1.5, pool=1.5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for prefix in LOCAL_PREFIXES:
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


def _bridge_readiness(entry: dict) -> Readiness:
    base, cmd = entry["agent_key"], entry["command"]
    if not entry["available"]:
        return Readiness(MISSING, f"not installed — {install_command(cmd)}")
    login = _LOGIN_COMMANDS.get(base, cmd)
    if entry.get("logged_in") is True:
        return Readiness(READY, "installed and logged in")
    if entry.get("logged_in") is False:
        return Readiness(UNVERIFIED, f"installed; run `{login}` once and log in")
    return Readiness(
        UNVERIFIED,
        f"installed; login could not be verified — if your first chat "
        f"fails, run `{login}` once and log in",
    )


def _local_readiness(prefix: str, entry: dict) -> Readiness:
    if not entry["reachable"]:
        hint = _START_COMMANDS.get(prefix, "start the server")
        return Readiness(MISSING, f"not reachable at {entry['base_url']} — {hint}")
    models = list(entry["models"])
    return Readiness(READY, f"{len(models)} model(s) available", models)


def _key_readiness(base: str, env: dict[str, str]) -> Readiness:
    var = CLOUD_KEY_ENVS[base]
    if env.get(var) or os.environ.get(var):
        return Readiness(READY, f"{var} is set")
    if base == "openrouter":
        return Readiness(MISSING, "needs an API key (you can enter one here)")
    return Readiness(MISSING, f"needs {var} in .env")


async def probe_all(
    bases: Iterable[str], env: dict[str, str] | None = None
) -> dict[str, Readiness]:
    """Readiness for several provider bases, probing each source exactly once.

    ``env`` is passed in rather than read from the process so the setup wizard
    can probe against a ``.env`` the bot has not loaded yet; ``os.environ`` is
    still consulted as a fallback.
    """
    env = env or {}
    wanted = list(dict.fromkeys(bases))
    bridges: dict[str, dict] = {}
    if any(b in ACP_COMMANDS for b in wanted):
        bridges = {e["agent_key"]: e for e in acp_bridges()}
    servers: dict = {}
    if any(b in LOCAL_PREFIXES for b in wanted):
        servers = await local_servers()

    out: dict[str, Readiness] = {}
    for base in wanted:
        if base in bridges:
            out[base] = _bridge_readiness(bridges[base])
        elif base in servers:
            out[base] = _local_readiness(base, servers[base])
        elif base in CLOUD_KEY_ENVS:
            out[base] = _key_readiness(base, env)
        else:
            out[base] = Readiness(UNVERIFIED, "cannot be checked from here")
    return out


async def probe(base: str, env: dict[str, str] | None = None) -> Readiness:
    """Readiness for a single provider base (see :func:`probe_all`)."""
    return (await probe_all([base], env))[base]
