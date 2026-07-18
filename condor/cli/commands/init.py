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

HARNESSES = ("claude-code", "codex", "openclaw", "hermes", "condor")

# Policy: Condor's skills and MCP servers are REPO-SCOPED — they load only when
# the harness runs from this directory (Claude Code: .mcp.json + .claude/skills;
# Codex: .codex/config.toml + .agents/skills; OpenClaw: workspace scan of
# skills/). init verifies/repairs that wiring; it never installs anything
# user-wide or into another product's global config.


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
        "codex": shutil.which("codex") is not None or (home / ".codex").exists(),
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


def _repair_wiring(host_dir, mcp_issues_fn) -> None:
    """Repair one harness's repo-scoped wiring (skills mirror + MCP config)."""
    from condor.cli.commands import _wiring

    for line in mcp_issues_fn(apply_fix=True):
        echo(f"  mcp: {line}")
    for line in _wiring.mirror_skills(host_dir, apply_fix=True):
        echo(f"  skills: {line}")


def _emit_claude_code() -> None:
    from condor.cli.commands import _wiring

    echo("\n── Claude Code ──")
    _repair_wiring(REPO_ROOT / ".claude" / "skills", _wiring.mcp_json_issues)
    echo(
        f"  Repo-scoped by design: run `claude` from {REPO_ROOT} —\n"
        "  .mcp.json (MCP servers) and .claude/skills (/condor and friends)\n"
        "  load automatically there, and ONLY there. Try: '/condor status'."
    )


def _emit_codex() -> None:
    from condor.cli.commands import _wiring

    echo("\n── Codex ──")
    _repair_wiring(REPO_ROOT / ".agents" / "skills", _wiring.codex_toml_issues)
    echo(
        f"  Repo-scoped by design: run `codex` from {REPO_ROOT} —\n"
        "  .codex/config.toml (MCP servers) and .agents/skills load there,\n"
        "  and ONLY there. Codex asks you to trust the directory on first\n"
        "  run — project MCP config requires it. Try: '$condor status'."
    )


def _emit_openclaw() -> None:
    from condor.cli.commands import _wiring

    echo("\n── OpenClaw ──")
    issues = _wiring.openclaw_issues(apply_fix=True)
    for line in issues:
        echo(f"  skills: {line}")
    if not issues:
        echo("  skills: /condor linked in ~/.openclaw/skills.")
    echo(
        "  (OpenClaw runs from its own workspace, so the /condor skill is\n"
        "  linked into ~/.openclaw/skills — condor entry only. A workspace\n"
        f"  opened at {REPO_ROOT} additionally scans the full skills/ set.)\n"
        "  MCP has no per-workspace scope in OpenClaw — add the server to\n"
        "  its config if doctor warns it is missing:\n"
        f"    openclaw mcp add condor -- uv run --directory {REPO_ROOT} python -m mcp_servers.condor"
    )


def _emit_hermes() -> None:
    from condor.cli.commands import _wiring

    echo("\n── Hermes ──")
    if not detect_harnesses()["hermes"]:
        echo(
            "  Hermes is not installed. Install it yourself (we never install\n"
            "  another project's software), then re-run init:\n"
            "    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
        )
        return
    issues = _wiring.hermes_issues(apply_fix=True)
    for line in issues:
        echo(f"  {line}")
    if not issues:
        echo("  condor MCP server registered + /condor skill linked.")
    echo(
        "  Hermes has no repo-scoped loading, so both are global: the MCP\n"
        "  server via `hermes mcp add` (condor entry only) and the skill as\n"
        f"  a ~/.hermes/skills/condor symlink into {REPO_ROOT}.\n"
        "  Try: '/skill condor status' (or just ask about your portfolio)."
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
        "codex": _emit_codex,
        "openclaw": _emit_openclaw,
        "hermes": _emit_hermes,
        "condor": _emit_condor,
    }
    for h in selected:
        emitters[h]()
    if not selected:
        echo("\nNo harness selected: Tier 2 + monitoring dashboard only.")

    echo("\nDone. `init` is idempotent — re-run it any time to add a harness.")
