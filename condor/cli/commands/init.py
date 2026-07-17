"""``condor init`` — harness onboarding (idempotent, multi-select).

Single operator, loopback-gated (§5.5) — no login/identity step. Product
questions live here, in Python, re-runnable forever — the bash installer
(install.sh) only bootstraps and then hands off to ``init``.
"""

import shutil
import sys
from typing import Optional

import typer

from condor.cli.commands._common import REPO_ROOT
from condor.cli.output import ExitCode, echo, fail

ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

HARNESSES = ("claude-code", "openclaw", "hermes", "condor")


def _interactive() -> bool:
    return sys.stdin.isatty()


def _ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


# ── .env handling ──


def _read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def _write_env_var(key: str, value: str) -> None:
    """Idempotently set KEY=value in .env (created from .env.example)."""
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
        else:
            ENV_FILE.touch()
    lines = ENV_FILE.read_text().splitlines()
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


# ── harness detection ──


def detect_harnesses(home=None) -> dict[str, bool]:
    """Evidence that each external harness exists on this box."""
    from pathlib import Path

    home = home or Path.home()
    return {
        "claude-code": shutil.which("claude") is not None,
        "openclaw": shutil.which("openclaw") is not None
        or (home / ".openclaw").exists(),
        "hermes": shutil.which("hermes") is not None or (home / ".hermes").exists(),
    }


def _select_harnesses(harness: Optional[str]) -> list[str]:
    detected = detect_harnesses()
    default = ["condor"] + [h for h, found in detected.items() if found]

    if harness:
        selected = [h.strip() for h in harness.split(",") if h.strip()]
        unknown = [h for h in selected if h not in HARNESSES and h != "none"]
        if unknown:
            fail(
                f"unknown harness(es) {unknown}; choose from {list(HARNESSES)} or 'none'",
                ExitCode.CONFIG_ERROR,
            )
        return [] if selected == ["none"] else selected

    if not _interactive():
        return default

    found = [h for h, ok in detected.items() if ok]
    if found:
        echo(f"• Detected on this box: {', '.join(found)}")
    echo(f"  Available harnesses: {', '.join(HARNESSES)}")
    raw = _ask(
        "Harnesses to set up (comma-separated, 'none' for Tier 2 only)",
        default=",".join(default),
    )
    selected = [h.strip() for h in raw.split(",") if h.strip()]
    unknown = [h for h in selected if h not in HARNESSES and h != "none"]
    if unknown:
        fail(
            f"unknown harness(es) {unknown}; choose from {list(HARNESSES)} or 'none'",
            ExitCode.CONFIG_ERROR,
        )
    return [] if selected == ["none"] else selected


def _mcp_stdio_snippet() -> str:
    return (
        '"condor": {"type": "stdio", "command": "uv", "args": '
        f'["run", "--directory", "{REPO_ROOT}", "python", "-m", "mcp_servers.condor"]}}'
    )


def _emit_claude_code() -> None:
    echo("\n── Claude Code ──")
    mcp_json = REPO_ROOT / ".mcp.json"
    skills = REPO_ROOT / ".claude" / "skills"
    if not mcp_json.exists() or not skills.exists():
        fail(
            f"repo is missing {mcp_json.name} or .claude/skills — checkout is incomplete",
            ExitCode.CONFIG_ERROR,
        )
    echo(
        f"  In-repo: open {REPO_ROOT} in Claude Code — .mcp.json and\n"
        "  .claude/skills are picked up automatically. Try: 'list my agents'.\n"
        "  From anywhere, register the server user-wide:\n"
        f"    claude mcp add --scope user condor -- uv run --directory {REPO_ROOT} python -m mcp_servers.condor"
    )


def _emit_openclaw() -> None:
    echo("\n── OpenClaw ──")
    echo(
        "  Add this MCP server to your OpenClaw config (see their MCP docs\n"
        "  for the exact file on your version):\n"
        f"    {_mcp_stdio_snippet()}\n"
        f"  Skills: OpenClaw discovers {REPO_ROOT / 'skills'} via workspace scan."
    )


def _emit_hermes() -> None:
    echo("\n── Hermes ──")
    installed = detect_harnesses()["hermes"]
    if not installed:
        echo(
            "  Hermes is not installed. Install it yourself (we never install\n"
            "  another project's software), then re-run init:\n"
            "    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
        )
    echo(
        "  Add this MCP server to your Hermes MCP config:\n"
        f"    {_mcp_stdio_snippet()}\n"
        "  Install the skills tap:\n"
        f"    hermes skills tap add {REPO_ROOT}"
    )


def _emit_condor() -> None:
    from utils.config import WEB_URL

    echo("\n── Condor harness (web) ──")
    echo(
        "  Start Condor:  condor serve   (or make run)\n"
        f"  Web chat:      {WEB_URL} — loopback-only (§5.5), no login."
    )


def init(
    harness: Optional[str] = typer.Option(
        None,
        "--harness",
        help=f"Comma-separated selection from {list(HARNESSES)}, or 'none'.",
    ),
) -> None:
    """Onboard a harness (idempotent — re-run any time to add one)."""
    echo(f"Condor init — {REPO_ROOT}\n")
    selected = _select_harnesses(harness)

    emitters = {
        "claude-code": _emit_claude_code,
        "openclaw": _emit_openclaw,
        "hermes": _emit_hermes,
        "condor": _emit_condor,
    }
    for h in selected:
        emitters[h]()
    if not selected:
        echo("\nNo harness selected: Tier 2 + monitoring dashboard only.")

    echo("\nDone. `init` is idempotent — re-run it any time to add a harness.")
