"""``condor uninstall`` — the opposite of ``condor init``.

Deregisters every GLOBAL trace of condor from the supported harnesses
(Claude Code, Codex, OpenClaw, Hermes) plus the ``~/.local/bin/condor`` PATH
link, so the box looks like a new user's: a fresh ``condor init`` (or the
curl installer) can then be tested end-to-end. Only condor entries are ever
touched; foreign symlinks/config are reported and left alone.

Repo-scoped wiring (.mcp.json, .codex/config.toml, in-repo skill mirrors) is
part of the checkout and is deliberately NOT removed — a new user gets it by
cloning. For a fully clean box, also delete the checkout itself.
"""

from pathlib import Path
from typing import Optional

import typer

from condor.cli.commands._common import REPO_ROOT
from condor.cli.output import ExitCode, echo, fail

EXTERNAL_HARNESSES = ("claude-code", "codex", "openclaw", "hermes")


def _path_link_lines() -> list[str]:
    """Remove ~/.local/bin/condor when it points at THIS install."""
    link = Path.home() / ".local" / "bin" / "condor"
    if not (link.is_symlink() or link.exists()):
        return []
    try:
        ours = link.resolve().is_relative_to(REPO_ROOT.resolve())
    except OSError:
        ours = False
    if not ours:
        return [f"{link} points at another install — left alone"]
    link.unlink()
    return [f"removed {link}"]


def uninstall(
    harness: Optional[str] = typer.Option(
        None,
        "--harness",
        help=f"Comma-separated selection from {list(EXTERNAL_HARNESSES)} "
        "(default: all).",
    ),
) -> None:
    """Deregister condor's skills + MCP servers from all harnesses (inverse of init)."""
    from condor.cli.commands import _wiring

    if harness:
        selected = [h.strip() for h in harness.split(",") if h.strip()]
        unknown = [h for h in selected if h not in EXTERNAL_HARNESSES]
        if unknown:
            fail(
                f"unknown harness(es) {unknown}; choose from {list(EXTERNAL_HARNESSES)}",
                ExitCode.CONFIG_ERROR,
            )
    else:
        selected = list(EXTERNAL_HARNESSES)

    echo(f"Condor uninstall — {REPO_ROOT}\n")
    removers = {
        "claude-code": _wiring.uninstall_claude_code,
        "codex": _wiring.uninstall_codex,
        "openclaw": _wiring.uninstall_openclaw,
        "hermes": _wiring.uninstall_hermes,
    }
    for h in selected:
        lines = removers[h]()
        echo(f"── {h} ──")
        for line in lines or ["  nothing registered"]:
            echo(f"  {line}" if not line.startswith("  ") else line)

    if not harness:  # full uninstall also drops the PATH link
        for line in _path_link_lines():
            echo(f"── path ──\n  {line}")

    echo(
        "\nGlobal wiring removed. Repo-scoped wiring (.mcp.json,\n"
        ".codex/config.toml, in-repo skill mirrors) is part of the checkout\n"
        "and stays. For a fully clean box, also delete the checkout:\n"
        f"  rm -rf {REPO_ROOT}\n"
        "Re-register anytime with `condor init` — and note `condor doctor\n"
        "--fix` re-links OpenClaw/Hermes wiring, so run it only when you\n"
        "want condor re-registered."
    )
