"""Pick the model Condor thinks with — at install time, and any time after.

``uv run python -m condor.setup_llm`` lists every model this machine can
actually run, says why each one is or isn't ready, and writes the pick to
``.env`` as ``CONDOR_DEFAULT_AGENT`` — the top of the precedence chain read by
``handlers.agents._shared._default_agent()``, so it survives restarts and reaches
every entry point. ``--status`` (and any run without a TTY: CI, ``curl | bash``)
prints the same report and changes nothing.

The wizard owns none of the detection: it renders
:mod:`condor.llm.readiness` over :data:`condor.llm.options.AGENT_OPTIONS`,
so a provider added to either shows up here for free and can never disagree with
what the ``get_available_models`` MCP tool reports.

It runs BEFORE the bot, so it reads ``.env`` itself rather than trusting
``os.environ`` — nothing has loaded that file yet. It never fails setup: an
interrupt, a rejected key or an unreachable API returns to the menu or exits, and
the exit code is always 0, because a model can always be picked later.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import shlex
import subprocess
import sys
from pathlib import Path

import httpx

from condor.acp import ACP_COMMANDS
from condor.acp.pydantic_ai_client import DEFAULT_BASE_URLS
from condor.llm import readiness
from condor.llm.openrouter_models import fetch_models
from condor.llm.options import (
    AGENT_OPTIONS,
    RECOMMENDED_AGENT,
    selectable_agent_options,
)
from condor.llm.readiness import MISSING, READY, UNVERIFIED, Readiness

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENV_VAR = "CONDOR_DEFAULT_AGENT"
FALLBACK_AGENT = "claude-code"  # same last resort as _default_agent()

# How many OpenRouter models the drill-down lists, cheapest input price first.
OPENROUTER_PAGE = 20

_BADGES = {READY: "✓", UNVERIFIED: "?", MISSING: "✗"}


# ── .env I/O ────────────────────────────────────────────────────────────────


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Parse ``KEY=VALUE`` from a .env, skipping comments and malformed lines.

    Surrounding quotes are stripped: the file may already carry a quoted
    ``TELEGRAM_TOKEN`` written by ``setup-environment.sh``.
    """
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def update_env_var(path: Path, key: str, value: str) -> None:
    """Replace the first live ``key=`` line, or append; every other line is kept.

    A commented ``# CONDOR_DEFAULT_AGENT=`` from ``.env.example`` is deliberately
    not matched — the live line is appended below it rather than uncommenting a
    line the user may have written a note next to. The file always ends with a
    newline so the next appender doesn't glue itself onto the last value.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    replacement = f"{key}={value}"
    for i, raw in enumerate(lines):
        stripped = raw.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
            lines[i] = replacement
            break
    else:
        lines.append(replacement)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── The menu, derived from AGENT_OPTIONS ────────────────────────────────────


def menu_options() -> dict[str, dict]:
    """Every startable key, plus the one picker this wizard can complete.

    ``RECOMMENDED_AGENT`` is listed first so the answer most installs want is
    option 1; the rest keep their ``AGENT_OPTIONS`` order.

    ``custom:`` is excluded on purpose: OpenAI-compatible endpoints live in
    per-user preferences, which do not exist at install time — the closing report
    points at ``/agent → Change LLM`` for those.
    """
    selectable = dict(selectable_agent_options())
    options: dict[str, dict] = {}
    if RECOMMENDED_AGENT in selectable:
        options[RECOMMENDED_AGENT] = selectable.pop(RECOMMENDED_AGENT)
    options.update(selectable)
    options["openrouter:"] = AGENT_OPTIONS["openrouter:"]
    return options


def base_of(agent_key: str) -> str:
    """The provider base of an agent key: ``claude-acp:opus`` → ``claude-acp``."""
    key = (agent_key or "").strip()
    if key.startswith("custom@"):
        return "custom"
    return key.split(":", 1)[0]


def is_writable_key(agent_key: str) -> bool:
    """Picker sentinels (``openrouter:``, ``custom:``) are not agent keys.

    Writing one as the default would fail at the user's first chat, which is the
    exact failure this feature exists to prevent.
    """
    key = (agent_key or "").strip()
    if not key:
        return False
    return not AGENT_OPTIONS.get(key, {}).get("picker")


def current_default(env: dict[str, str]) -> str:
    """The default in force today, by the same precedence ``_default_agent()`` uses.

    Resolved from the parsed ``.env`` rather than ``os.environ``: the wizard runs
    before anything has loaded that file.
    """
    from condor.agents.agent import AgentStore
    from condor.memory.paths import CHAT_SLUG

    if env.get(ENV_VAR):
        return env[ENV_VAR]
    try:
        agent = AgentStore().get(CHAT_SLUG)
    except Exception:
        agent = None
    return (agent.agent_key if agent else "") or FALLBACK_AGENT


def persist_choice(
    path: Path, agent_key: str, extra_env: dict[str, str] | None = None
) -> None:
    """Write the pick to .env, credentials first.

    Order matters: a half-write that saved ``CONDOR_DEFAULT_AGENT=openrouter:…``
    without its key would leave the machine defaulting to a provider it cannot
    reach.
    """
    if not is_writable_key(agent_key):
        raise ValueError(f"{agent_key!r} is a picker, not an agent key")
    for key, value in (extra_env or {}).items():
        update_env_var(path, key, value)
    update_env_var(path, ENV_VAR, agent_key)


# ── Rendering ───────────────────────────────────────────────────────────────


def render_menu(
    options: dict[str, dict], states: dict[str, Readiness], current: str
) -> str:
    """The numbered table: one row per option, badged with why it is/isn't ready."""

    def label_of(key: str) -> str:
        label = options[key]["label"]
        return f"{label} (recommended)" if key == RECOMMENDED_AGENT else label

    # Widened to the longest label so the badge column still lines up once the
    # recommended row carries its suffix.
    width = max([32] + [len(label_of(k)) for k in options])
    lines = []
    for n, key in enumerate(options, 1):
        state = states.get(base_of(key)) or Readiness(UNVERIFIED, "not checked")
        badge = _BADGES.get(state.state, "?")
        row = f"  {n}. {label_of(key):<{width}} [{badge} {state.detail}]"
        if key == current:
            row += "  ← current default"
        lines.append(row)
    lines.append(f"  s. Skip — keep the current default ({current})")
    return "\n".join(lines)


def render_report(agent_key: str, state: Readiness) -> str:
    """The closing verdict on the default in force — including a warning today's setup never gives."""
    badge = _BADGES.get(state.state, "?")
    head = f"  {badge} Default model: {agent_key} — {state.detail}"
    tail = "    Switch any time: `make pick-model`, or /agent → Change LLM in the bot."
    return f"{head}\n{tail}"


# ── Drill-downs ─────────────────────────────────────────────────────────────


async def validate_openrouter_key(key: str) -> bool | None:
    """Is this OpenRouter key real? ``None`` when the API could not be reached.

    Hits ``GET /api/v1/key``, which 401s. The catalog endpoint (``/models``) is
    public, so sending a key at it validates nothing — a bogus key still gets a
    200.
    """
    url = f"{DEFAULT_BASE_URLS['openrouter'].rstrip('/')}/key"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except Exception:
        return None
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        return False
    return None


def _pick_local(key: str, state: Readiness, ask, say) -> str | None:
    """Choose one of a local server's loaded models, or its own default."""
    prefix = base_of(key)
    say(f"\n  Models loaded on {prefix}:")
    for n, model in enumerate(state.models, 1):
        say(f"    {n}. {model}")
    say(f"    Enter — let {prefix} pick the model at session start")
    answer = ask("  Model number (or Enter): ").strip()
    if not answer:
        return key  # the bare "ollama:"/"lmstudio:" key resolves at session start
    if answer.isdigit() and 1 <= int(answer) <= len(state.models):
        return f"{prefix}:{state.models[int(answer) - 1]}"
    say("  ! Not one of the models.")
    return None


def _pick_openrouter(env, ask, say, ask_secret) -> tuple[str, dict[str, str]] | None:
    """Validate a key, then pick a tool-capable slug. Nothing is written on refusal."""
    saved = env.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    suffix = " (Enter keeps the saved one)" if saved else ""
    entered = ask_secret(f"  OpenRouter API key{suffix}: ").strip()
    key = entered or saved
    if not key:
        say("  ! No key entered — nothing was written.")
        return None

    verdict = asyncio.run(validate_openrouter_key(key))
    if verdict is False:
        say("  ✗ OpenRouter rejected that key — nothing was written.")
        return None
    if verdict is None:
        say("  ✗ Could not reach OpenRouter to check that key — nothing was written.")
        return None

    models = asyncio.run(fetch_models())  # already filtered to tool-calling support
    if not models:
        say("  ✗ Could not fetch the OpenRouter catalog — nothing was written.")
        return None
    models = sorted(models, key=lambda m: (m.prompt_price, m.slug))[:OPENROUTER_PAGE]

    say("\n  Tool-capable models, cheapest first:")
    for n, model in enumerate(models, 1):
        say(f"    {n}. {model.slug:<44} ${model.prompt_price:.2f}/Mtok in")
    answer = ask("  Model number: ").strip()
    if not (answer.isdigit() and 1 <= int(answer) <= len(models)):
        say("  ! Not one of the models.")
        return None

    extra = {"OPENROUTER_API_KEY": key} if entered else {}
    return f"openrouter:{models[int(answer) - 1].slug}", extra


def _install_bridge(base: str, say=print) -> bool:
    """Run the install command for a MISSING ACP bridge, right where it's needed.

    Reuses :func:`handlers.agents.readiness.install_command` against
    ``ACP_COMMANDS[base]`` -- the exact command already shown in that row's
    "not installed" detail, so this can never run something different from
    what the menu displayed. Streams the real install output (npm's progress,
    not a silent hang) rather than capturing it. Returns ``False`` without
    attempting anything when there's no known package-manager install for
    this bridge (or the command fails), leaving the caller to report why.
    """
    cmd = ACP_COMMANDS.get(base, "")
    install_cmd = readiness.install_command(cmd) if cmd else ""
    if not install_cmd or install_cmd.startswith("install `"):
        return False
    say(f"\n  → {install_cmd}")
    try:
        result = subprocess.run(shlex.split(install_cmd))
    except (OSError, subprocess.SubprocessError) as e:
        say(f"  ✗ Install failed: {e}")
        return False
    if result.returncode != 0:
        say(
            f"  ✗ Install failed (exit {result.returncode}) — try `{install_cmd}` manually."
        )
        return False
    say("  ✓ Installed.")
    return True


# ── The prompt loop ─────────────────────────────────────────────────────────


def choose(
    options: dict[str, dict],
    states: dict[str, Readiness],
    current: str,
    env: dict[str, str],
    ask=input,
    say=print,
    ask_secret=None,
) -> tuple[str, dict[str, str]] | None:
    """Run the menu until something is picked. ``None`` = keep the current default."""
    ask_secret = ask_secret or getpass.getpass
    while True:
        say(render_menu(options, states, current))
        answer = ask("\n  Pick a number (or `s` to keep the current default): ")
        answer = answer.strip().lower()
        if answer in ("", "s", "skip"):
            return None
        if not (answer.isdigit() and 1 <= int(answer) <= len(options)):
            say("  ! Not one of the options.\n")
            continue

        key = list(options)[int(answer) - 1]
        state = states.get(base_of(key)) or Readiness(UNVERIFIED, "not checked")
        if key == "openrouter:":
            # The only MISSING row that is still pickable: the key is entered here.
            picked = _pick_openrouter(env, ask, say, ask_secret)
            if picked is None:
                say("")
                continue
            return picked
        if state.state == MISSING:
            base = base_of(key)
            if base in ACP_COMMANDS and _install_bridge(base, say):
                states.pop(base, None)
                state = asyncio.run(readiness.probe(base, env))
                states[base] = state
            if state.state == MISSING:
                say(f"  ✗ {options[key]['label']} isn't ready — {state.detail}\n")
                continue
            say(
                f"  {_BADGES.get(state.state, '?')} {options[key]['label']} — {state.detail}\n"
            )
        if key in ("ollama:", "lmstudio:"):
            picked_key = _pick_local(key, state, ask, say)
            if picked_key is None:
                say("")
                continue
            return picked_key, {}
        return key, {}


def _state_for(
    agent_key: str, states: dict[str, Readiness], env: dict[str, str]
) -> Readiness:
    base = base_of(agent_key)
    if base in states:
        return states[base]
    return asyncio.run(readiness.probe(base, env))


def main(argv: list[str] | None = None) -> int:
    """Always returns 0: setup must not fail because a model could not be picked."""
    args = sys.argv[1:] if argv is None else argv
    status_only = "--status" in args

    env = read_env(ENV_PATH)
    options = menu_options()
    states = asyncio.run(readiness.probe_all([base_of(k) for k in options], env))
    current = current_default(env)

    print("\n  Which AI model should Condor use by default?\n")

    if status_only or not sys.stdin.isatty():
        print(render_menu(options, states, current))
        print("")
        print(render_report(current, _state_for(current, states, env)))
        return 0

    try:
        picked = choose(options, states, current, env)
    except (KeyboardInterrupt, EOFError):
        print("\n  ! Skipped — run `make pick-model` any time.")
        picked = None

    chosen = current
    if picked is not None:
        chosen, extra = picked
        try:
            persist_choice(ENV_PATH, chosen, extra)
        except (ValueError, OSError) as e:
            print(f"  ✗ Could not save the choice: {e}")
            chosen = current
        else:
            print(f"\n  ✓ Saved {ENV_VAR}={chosen} to .env")
            # Re-probe against the .env just written: an OpenRouter key entered a
            # moment ago makes a row that was MISSING at render time READY now.
            states.pop(base_of(chosen), None)

    # Make sure whatever ends up being the effective default -- just picked,
    # kept from a previous run, or the untouched recommended fallback -- has
    # its CLI bridge actually installed. `choose()` already installs one the
    # moment a MISSING row is selected; this only still fires for the "picker
    # was skipped but the default needs it" case, in the same run rather than
    # a separate pass tacked on afterward. Interactive-only: `--status`/no-tty
    # promises to change nothing (see the module docstring), so this never
    # runs there.
    base = base_of(chosen)
    state = _state_for(chosen, states, read_env(ENV_PATH))
    if state.state == MISSING and base in ACP_COMMANDS and _install_bridge(base):
        states.pop(base, None)
        state = _state_for(chosen, states, read_env(ENV_PATH))

    print("")
    print(render_report(chosen, state))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    try:
        sys.exit(main())
    except Exception as e:  # a broken wizard must not break `make setup`
        print(f"  ! Model selection could not run ({e}) — try `make pick-model` later.")
        sys.exit(0)
