"""Interactive AI-brain (LLM) selection for Condor setup.

Step 2 of ``setup-environment.sh`` runs this (``uv run python -m
condor.setup_llm``); it is equally runnable standalone any time later. The
chosen agent key is written to ``.env`` as ``CONDOR_DEFAULT_AGENT`` — the top
of the default-model precedence in ``handlers.agents._shared`` — plus
``OPENROUTER_API_KEY`` when OpenRouter is picked, since ``openrouter:*``
models refuse to start without it.

The wizard ends with a readiness report for the chosen brain instead of
letting setup exit green around an unusable default: an unauthenticated CLI
is reported with its login command, a stopped local server with its start
command. Without a TTY (CI, ``curl | bash`` with no terminal) it prints the
same report for the current default and changes nothing.

Custom OpenAI-compatible endpoints are deliberately not offered here: they
live in per-user preferences (``condor/preferences.py``), which do not exist
at install time. The wizard points users at ``/agent → Change LLM`` instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from getpass import getpass
from pathlib import Path
from shutil import which

ENV_FILE = Path(".env")  # setup runs from the project root, like setup-environment.sh

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
LOCAL_BASES = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}
LOCAL_START_HINTS = {
    "ollama": "start it with `ollama serve` (https://ollama.com)",
    "lmstudio": "open LM Studio and enable its local server (Developer tab)",
}
PROBE_TIMEOUT_S = 3

READY = "ready"
UNVERIFIED = "unverified"
MISSING = "missing"

_STATE_BADGE = {READY: "✓", UNVERIFIED: "?", MISSING: "✗"}


# ── .env access ──────────────────────────────────────


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse simple KEY=VALUE lines; comments and malformed lines are skipped."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def update_env_var(path: Path, key: str, value: str) -> None:
    """Replace ``key=`` in place or append it, preserving every other line."""
    lines = path.read_text().splitlines() if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


# ── detection ────────────────────────────────────────


def _http_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode())


def parse_openai_models(payload: dict) -> list[str]:
    """Model ids from an OpenAI-compatible ``/models`` response."""
    return [
        m["id"] for m in payload.get("data", []) if isinstance(m, dict) and m.get("id")
    ]


def probe_local_models(provider: str) -> list[str] | None:
    """Models served by a local ollama/lmstudio instance, or None if down."""
    try:
        return parse_openai_models(_http_json(f"{LOCAL_BASES[provider]}/models"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _claude_login_found() -> bool:
    if (Path.home() / ".claude" / ".credentials.json").exists():
        return True
    if sys.platform == "darwin":
        probe = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True,
        )
        return probe.returncode == 0
    return False


def detect(base: str, env: dict[str, str]) -> tuple[str, str]:
    """Readiness of one provider base → (state, human detail/fix)."""
    import os

    if base in ("claude-code", "claude-acp"):
        if not which("claude-agent-acp"):
            return (
                MISSING,
                "ACP bridge not on PATH — npm install -g @agentclientprotocol/claude-agent-acp",
            )
        if _claude_login_found():
            return READY, "bridge installed, Claude login found"
        return (
            UNVERIFIED,
            "bridge installed; if first chat fails, run `claude` once and log in",
        )

    if base in ("gemini", "copilot", "codex"):
        if not which("npx"):
            return MISSING, "needs Node.js (npx not on PATH)"
        if base == "gemini":
            if (
                Path.home() / ".gemini" / "oauth_creds.json"
            ).exists() or os.environ.get("GEMINI_API_KEY"):
                return READY, "login found"
            return UNVERIFIED, "run `npx @google/gemini-cli` once and authenticate"
        if base == "codex":
            if (Path.home() / ".codex" / "auth.json").exists():
                return READY, "login found"
            return UNVERIFIED, "log in with the Codex CLI (`codex login`)"
        return UNVERIFIED, "run `npx @github/copilot` once and /login"

    if base in LOCAL_BASES:
        models = probe_local_models(base)
        if models is None:
            return (
                MISSING,
                f"no server at {LOCAL_BASES[base]} — {LOCAL_START_HINTS[base]}",
            )
        return READY, f"{len(models)} model(s) available"

    if base == "openrouter":
        if env.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY"):
            return READY, "API key set"
        return MISSING, "needs an API key (entered here)"

    return UNVERIFIED, "unknown provider"


# ── choices ──────────────────────────────────────────


def menu_options() -> list[tuple[str, str]]:
    """(agent_key, label) pairs offered at setup, in AGENT_OPTIONS order.

    Picker sentinels are excluded except ``openrouter:``, which this wizard
    can complete (key + model). ``custom:`` needs the per-user preference
    store and is offered at runtime instead.
    """
    from handlers.agents._shared import AGENT_OPTIONS

    options = []
    for key, meta in AGENT_OPTIONS.items():
        if meta.get("picker") and key != "openrouter:":
            continue
        options.append((key, meta["label"]))
    return options


def current_default(env: dict[str, str]) -> str:
    from handlers.agents._shared import DEFAULT_AGENT

    return env.get("CONDOR_DEFAULT_AGENT") or DEFAULT_AGENT


def persist_choice(
    agent_key: str, extra_env: dict[str, str] | None = None, path: Path = ENV_FILE
) -> None:
    for key, value in (extra_env or {}).items():
        update_env_var(path, key, value)
    update_env_var(path, "CONDOR_DEFAULT_AGENT", agent_key)


# ── interactive flows ────────────────────────────────


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _pick_local_model(provider: str) -> str | None:
    """Resolve ``ollama:``/``lmstudio:`` to a concrete or bare key; None to go back."""
    models = probe_local_models(provider)
    if models is None:
        print(
            f"  ✗ No server at {LOCAL_BASES[provider]} — {LOCAL_START_HINTS[provider]}"
        )
        return None
    print(f"  Models on the local {provider} server:")
    for i, model in enumerate(models, 1):
        print(f"    {i}. {model}")
    raw = _ask(f"  Model [1-{len(models)}, Enter = pick automatically at chat start]: ")
    if not raw:
        return f"{provider}:"
    if raw.isdigit() and 1 <= int(raw) <= len(models):
        return f"{provider}:{models[int(raw) - 1]}"
    print("  ✗ Not a listed model number.")
    return None


def _pick_openrouter(env: dict[str, str]) -> tuple[str, dict[str, str]] | None:
    """Validate an OpenRouter key and model → (agent_key, env-to-write); None to go back."""
    existing = env.get("OPENROUTER_API_KEY", "")
    if existing:
        key = _ask("  OpenRouter API key [Enter = keep the one in .env]: ") or existing
    else:
        key = getpass("  OpenRouter API key (hidden): ").strip()
    if not key:
        print("  ✗ OpenRouter needs an API key.")
        return None
    try:
        models = parse_openai_models(
            _http_json(
                f"{OPENROUTER_BASE}/models", headers={"Authorization": f"Bearer {key}"}
            )
        )
    except urllib.error.HTTPError as e:
        print(f"  ✗ OpenRouter rejected the key (HTTP {e.code}).")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ✗ Could not reach OpenRouter: {e}")
        return None
    model = _ask("  Model id (e.g. anthropic/claude-sonnet-4-5): ")
    if model not in models:
        print(f"  ✗ '{model}' is not in OpenRouter's model list.")
        return None
    extra = {"OPENROUTER_API_KEY": key} if key != existing or not existing else {}
    return f"openrouter:{model}", extra


def choose(env: dict[str, str]) -> tuple[str, dict[str, str]] | None:
    """Run the menu until a choice is confirmed; None means keep the default."""
    options = menu_options()
    default = current_default(env)
    while True:
        print("\nWhich AI brain should Condor use by default?\n")
        for i, (key, label) in enumerate(options, 1):
            state, detail = detect(key.partition(":")[0], env)
            marker = "  ← current default" if key == default else ""
            print(f"  {i}. {label:<32} [{_STATE_BADGE[state]} {detail}]{marker}")
        print(f"  s. Skip — keep current default ({default})")
        raw = _ask(f"\nChoice [1-{len(options)}, s, Enter = keep default]: ").lower()
        if raw in ("s", "skip", ""):
            return None
        if not raw.isdigit() or not 1 <= int(raw) <= len(options):
            print("  ✗ Not an option.")
            continue
        key, _label = options[int(raw) - 1]
        if key in ("ollama:", "lmstudio:"):
            picked = _pick_local_model(key[:-1])
            if picked is None:
                continue
            return picked, {}
        if key == "openrouter:":
            picked = _pick_openrouter(env)
            if picked is None:
                continue
            return picked
        return key, {}


# ── reporting ────────────────────────────────────────


def report(agent_key: str, env: dict[str, str]) -> None:
    state, detail = detect(agent_key.partition(":")[0], env)
    if state == READY:
        print(f"\n  ✓ Default brain: {agent_key} — {detail}")
    elif state == UNVERIFIED:
        print(f"\n  ? Default brain: {agent_key} — {detail}")
    else:
        print(f"\n  ✗ Default brain: {agent_key} — NOT READY: {detail}")
        print("    Condor cannot chat until this is fixed.")


def status() -> None:
    env = read_env()
    default = current_default(env)
    print("AI brain options:")
    for key, label in menu_options():
        state, detail = detect(key.partition(":")[0], env)
        marker = "  ← current default" if key == default else ""
        print(f"  [{_STATE_BADGE[state]}] {label:<32} {detail}{marker}")
    report(default, env)


def main(argv: list[str]) -> int:
    if "--status" in argv or not sys.stdin.isatty():
        status()
        return 0
    env = read_env()
    try:
        choice = choose(env)
    except (KeyboardInterrupt, EOFError):
        print("\n  Skipped — keeping the current default.")
        report(current_default(env), env)
        return 0
    if choice is None:
        report(current_default(env), env)
        return 0
    agent_key, extra = choice
    persist_choice(agent_key, extra)
    print(f"  ✓ Wrote CONDOR_DEFAULT_AGENT={agent_key} to {ENV_FILE}")
    report(agent_key, read_env())
    print("    Switch any time: rerun this wizard, or /agent → Change LLM in Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
