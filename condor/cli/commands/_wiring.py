"""Repo-scoped harness wiring: skill mirrors + MCP config files.

Shared by ``condor init`` (always repairs) and ``condor doctor`` (checks;
``--fix`` repairs), so the external-harness wiring cannot drift from the
repo's ``skills/`` library and MCP canon:

- ``.claude/skills`` and ``.agents/skills`` mirror ``skills/`` one symlink
  per skill (Claude Code / Codex+OpenClaw skill discovery).
- ``.mcp.json`` (Claude Code) and ``.codex/config.toml`` (Codex) carry the
  canonical ``condor`` + ``mcp-hummingbot`` stdio server entries.

Repo-owned files only — OpenClaw/Hermes global configs are never written
(doctor checks them read-only and prints the remedy command).

Every function returns a list of human-readable issue/change lines; empty
means already fresh. Lines containing "manually" mark issues a fix pass
could not (or refused to) repair.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from condor.cli.commands._common import REPO_ROOT

SKILLS_ROOT = REPO_ROOT / "skills"
SKILL_MIRROR_DIRS = (
    REPO_ROOT / ".claude" / "skills",
    REPO_ROOT / ".agents" / "skills",
)
MCP_JSON = REPO_ROOT / ".mcp.json"
CODEX_TOML = REPO_ROOT / ".codex" / "config.toml"

# The canon both config files must carry. Commands are cwd-relative on
# purpose: the harness runs from the repo (repo-scoped policy), so no
# --directory pinning.
EXPECTED_MCP = {
    "condor": {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.condor"],
        "env": {},
    },
    "mcp-hummingbot": {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.hummingbot_api"],
        "env": {},
    },
}

CODEX_CONFIG = """\
# Project-scoped Codex config — loaded only when Codex runs inside this repo
# (and the directory is trusted). The Codex analog of .mcp.json: Condor's MCP
# servers stay repo-scoped by design. Skills load from .agents/skills.
# Managed by `condor init` / `condor doctor --fix` — edits to the condor and
# mcp-hummingbot tables will be reverted.

[mcp_servers.condor]
command = "uv"
args = ["run", "python", "-m", "mcp_servers.condor"]

[mcp_servers.mcp-hummingbot]
command = "uv"
args = ["run", "python", "-m", "mcp_servers.hummingbot_api"]
"""


def mirror_skills(host_dir: Path, apply_fix: bool = True) -> list[str]:
    """Make ``host_dir`` mirror ``skills/``: one relative symlink per skill.

    Dangling symlinks (a renamed/removed skill) are dropped; a real directory
    shadowing a skill name is never touched, only reported ("manually").
    ``apply_fix=False`` reports what a fix pass would do without mutating.
    """
    changes: list[str] = []
    wanted = {p.name for p in SKILLS_ROOT.iterdir() if (p / "SKILL.md").exists()}

    if host_dir.exists():
        for entry in host_dir.iterdir():
            if entry.is_symlink() and not (entry / "SKILL.md").exists():
                if apply_fix:
                    entry.unlink()
                    changes.append(f"removed dangling link {entry.name}")
                else:
                    changes.append(f"dangling link {entry.name}")
            elif not entry.is_symlink() and entry.name in wanted:
                changes.append(
                    f"{entry} is a real dir shadowing a skill — resolve manually"
                )

    for name in sorted(wanted):
        link = host_dir / name
        if link.exists():
            continue
        if apply_fix:
            host_dir.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(SKILLS_ROOT / name, host_dir))
            changes.append(f"linked {name}")
        else:
            changes.append(f"missing link {name}")
    return changes


def _entry_drifted(entry: dict, expected: dict) -> bool:
    return entry.get("command") != expected["command"] or entry.get(
        "args"
    ) != expected["args"]


def mcp_json_issues(apply_fix: bool = True) -> list[str]:
    """Ensure .mcp.json carries the canonical condor/mcp-hummingbot entries.

    Other servers in the file (playwright, notification channels, …) are the
    user's and are preserved verbatim; only the two canonical entries are
    checked/patched. A file that does not parse is never rewritten.
    """
    try:
        data = json.loads(MCP_JSON.read_text()) if MCP_JSON.exists() else {}
    except json.JSONDecodeError as e:
        return [f".mcp.json does not parse ({e}) — fix manually"]

    servers = data.setdefault("mcpServers", {})
    drifted = [
        name
        for name, exp in EXPECTED_MCP.items()
        if _entry_drifted(servers.get(name) or {}, exp)
    ]
    if not drifted:
        return []
    if not apply_fix:
        return [f"{name} entry missing or drifted" for name in drifted]
    for name in drifted:
        servers[name] = EXPECTED_MCP[name]
    MCP_JSON.write_text(json.dumps(data, indent=2) + "\n")
    return [f"restored {name} entry" for name in drifted]


def codex_toml_issues(apply_fix: bool = True) -> list[str]:
    """Ensure .codex/config.toml carries the canonical MCP tables.

    Python ships no TOML writer, so a fix rewrites the whole file from
    :data:`CODEX_CONFIG` — but ONLY when the file contains nothing beyond the
    two canonical tables. Custom content (other servers, other settings)
    downgrades the fix to a "manually" report; the canonical version is one
    ``git checkout`` away.
    """
    if CODEX_TOML.exists():
        try:
            data = tomllib.loads(CODEX_TOML.read_text())
        except tomllib.TOMLDecodeError as e:
            return [f".codex/config.toml does not parse ({e}) — fix manually"]
    else:
        data = {}

    servers = data.get("mcp_servers") or {}
    drifted = [
        name
        for name, exp in EXPECTED_MCP.items()
        if _entry_drifted(servers.get(name) or {}, exp)
    ]
    if not drifted:
        return []
    if not apply_fix:
        return [f"{name} table missing or drifted" for name in drifted]

    has_custom = set(data) - {"mcp_servers"} or set(servers) - set(EXPECTED_MCP)
    if has_custom:
        return [
            "drifted but carries custom entries — restore the condor/"
            "mcp-hummingbot tables manually (canonical version is in git)"
        ]
    CODEX_TOML.parent.mkdir(parents=True, exist_ok=True)
    CODEX_TOML.write_text(CODEX_CONFIG)
    return ["rewrote canonical MCP tables"]


def external_harness_notes() -> list[tuple[str, str]]:
    """(harness, note) advisories for harnesses whose config is global.

    Read-only: parse failures or absence of the harness yield no note rather
    than a false alarm — these configs belong to other products. (Hermes is
    not here — it has a fixer, :func:`hermes_issues`, because its own CLI
    lets us add exactly the condor entries and nothing else.)
    """
    notes: list[tuple[str, str]] = []
    home = Path.home()

    oc = home / ".openclaw" / "openclaw.json"
    if oc.exists():
        try:
            servers = (json.loads(oc.read_text()).get("mcp") or {}).get(
                "servers"
            ) or {}
            if "condor" not in servers:
                notes.append(
                    (
                        "openclaw",
                        "condor MCP not in ~/.openclaw/openclaw.json — run "
                        f"`openclaw mcp add condor -- uv run --directory {REPO_ROOT} "
                        "python -m mcp_servers.condor`",
                    )
                )
        except Exception:
            pass
    return notes


def openclaw_issues(apply_fix: bool = True) -> list[str]:
    """Expose the /condor skill to OpenClaw via its managed skills dir.

    OpenClaw's workspace scan only sees repo skills when the workspace IS the
    repo — a gateway install runs from ~/.openclaw/workspace, so without this
    the skill never loads. The link goes into ``~/.openclaw/skills``
    (OpenClaw-only) and deliberately NOT ``~/.agents/skills``, which Codex
    reads user-wide — that would leak /condor outside the repo for Codex and
    break the repo-scoped policy. Only the ``condor`` entry is managed.
    Not an OpenClaw box → no issues.
    """
    import shutil

    home = Path.home()
    if not (home / ".openclaw").exists() and shutil.which("openclaw") is None:
        return []

    link = home / ".openclaw" / "skills" / "condor"
    target = SKILLS_ROOT / "condor"
    try:
        fresh = (link / "SKILL.md").exists() and link.resolve() == target.resolve()
    except OSError:
        fresh = False
    if fresh:
        return []
    if not apply_fix:
        return ["/condor skill not linked in ~/.openclaw/skills"]
    if link.exists() and not link.is_symlink():
        return [f"{link} exists and is not a symlink — resolve manually"]
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    return ["linked /condor skill into ~/.openclaw/skills"]


# ── uninstall (clean-install testing) ────────────────────────────────────────
#
# The inverse of the fixers above: strip every GLOBAL trace of condor from the
# harnesses so a fresh `condor init` (or the curl installer) behaves exactly
# like a new user's box. Repo-scoped wiring (.mcp.json, .codex/config.toml,
# in-repo skill mirrors) is part of the checkout and is left alone — a new
# user gets it by cloning. Only condor entries are ever removed.


def _unlink_if_ours(link: Path) -> str | None:
    """Remove ``link`` iff it is a symlink pointing into the repo's skills/.

    A real directory or a foreign symlink is reported and left alone; a
    dangling symlink whose target is inside the repo is ours and dropped.
    Returns a report line, or None when there is nothing at ``link``.
    """
    if not link.is_symlink():
        if link.exists():
            return f"{link} is not a symlink — left alone"
        return None
    try:
        ours = link.resolve().is_relative_to(SKILLS_ROOT.resolve())
    except OSError:
        ours = False
    if not ours:
        return f"{link} points outside this repo — left alone"
    link.unlink()
    return f"removed {link}"


def _run_cli(binary: str, args: list[str], timeout: float = 60) -> tuple[bool, str]:
    import shutil
    import subprocess

    path = shutil.which(binary)
    if path is None:
        return False, f"{binary} not on PATH"
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=timeout
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:160]
    return True, proc.stdout


def uninstall_claude_code() -> list[str]:
    """User-level Claude Code traces: skills symlink + user-scope MCP entries.

    The repo's own .mcp.json / .claude/skills stay — they are the checkout.
    """
    lines = []
    if (line := _unlink_if_ours(Path.home() / ".claude" / "skills" / "condor")):
        lines.append(line)
    cfg = Path.home() / ".claude.json"
    try:
        user_servers = json.loads(cfg.read_text()).get("mcpServers") or {}
    except Exception:
        user_servers = {}
    for name in EXPECTED_MCP:
        if name in user_servers:
            ok, err = _run_cli("claude", ["mcp", "remove", "--scope", "user", name])
            lines.append(
                f"deregistered user-scope MCP server {name}"
                if ok
                else f"could not remove user-scope MCP {name} ({err}) — "
                f"run `claude mcp remove --scope user {name}` manually"
            )
    return lines


def uninstall_codex() -> list[str]:
    """User-level Codex traces: ~/.agents/skills link + global config entries."""
    lines = []
    if (line := _unlink_if_ours(Path.home() / ".agents" / "skills" / "condor")):
        lines.append(line)
    cfg = Path.home() / ".codex" / "config.toml"

    def _global_servers() -> dict:
        try:
            return tomllib.loads(cfg.read_text()).get("mcp_servers") or {}
        except Exception:
            return {}

    if cfg.exists():
        for name in EXPECTED_MCP:
            if name not in _global_servers():
                continue
            _run_cli("codex", ["mcp", "remove", name])
            lines.append(
                f"deregistered global MCP server {name}"
                if name not in _global_servers()
                else f"{name} still in ~/.codex/config.toml — remove its "
                "[mcp_servers] table manually"
            )
    return lines


def uninstall_openclaw() -> list[str]:
    lines = []
    if (line := _unlink_if_ours(Path.home() / ".openclaw" / "skills" / "condor")):
        lines.append(line)
    oc = Path.home() / ".openclaw" / "openclaw.json"
    try:
        servers = (json.loads(oc.read_text()).get("mcp") or {}).get("servers") or {}
    except Exception:
        servers = {}
    for name in EXPECTED_MCP:
        if name not in servers:
            continue
        ok, err = _run_cli("openclaw", ["config", "unset", f"mcp.servers.{name}"])
        lines.append(
            f"deregistered MCP server {name}"
            if ok
            else f"could not unset mcp.servers.{name} ({err}) — run "
            f"`openclaw config unset mcp.servers.{name}` manually"
        )
    return lines


def uninstall_hermes() -> list[str]:
    lines = []
    if (line := _unlink_if_ours(Path.home() / ".hermes" / "skills" / "condor")):
        lines.append(line)
    if _hermes_mcp_registered():
        ok, err = _run_hermes(["mcp", "remove", "condor"])
        lines.append(
            "deregistered MCP server condor"
            if ok
            else f"could not remove condor MCP ({err}) — run "
            "`hermes mcp remove condor` manually"
        )
    ok, out = _run_hermes(["skills", "tap", "list"], timeout=30)
    if ok and str(REPO_ROOT) in out:
        ok, err = _run_hermes(["skills", "tap", "remove", str(REPO_ROOT)])
        lines.append(
            "removed skills tap"
            if ok
            else f"could not remove tap ({err}) — run "
            f"`hermes skills tap remove {REPO_ROOT}` manually"
        )
    return lines


def _run_hermes(
    args: list[str], timeout: float = 120, input: str | None = None
) -> tuple[bool, str]:
    import shutil
    import subprocess

    hermes = shutil.which("hermes")
    if hermes is None:
        return False, "hermes binary not on PATH"
    try:
        proc = subprocess.run(
            [hermes, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input,
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:160]
    return True, proc.stdout


def _hermes_mcp_registered() -> bool:
    ok, out = _run_hermes(["mcp", "list"], timeout=60)
    return ok and "condor" in out


def hermes_issues(apply_fix: bool = True) -> list[str]:
    """Register condor with Hermes: the MCP server + the /condor skill.

    Hermes has no repo-scoped loading, so this touches global state — the MCP
    server via Hermes' OWN CLI (`hermes mcp add`, condor entry only) and the
    skill as a symlink in `~/.hermes/skills/condor` (Hermes' primary skills
    dir; skills there are native slash commands). NOT `hermes skills tap` —
    a tap is a GitHub *source* to install from, and a local-path tap cannot
    be fetched, so it never surfaces the skill.
    Not a Hermes box (no ~/.hermes and no binary) → no issues.
    """
    home = Path.home()
    import shutil

    if not (home / ".hermes").exists() and shutil.which("hermes") is None:
        return []

    issues: list[str] = []
    mcp_registered = _hermes_mcp_registered()

    link = home / ".hermes" / "skills" / "condor"
    target = SKILLS_ROOT / "condor"
    try:
        skill_linked = (
            link / "SKILL.md"
        ).exists() and link.resolve() == target.resolve()
    except OSError:
        skill_linked = False

    if not mcp_registered:
        if apply_fix:
            # `hermes mcp add` discovers the server's tools, then PROMPTS
            # "Enable all N tools? [Y/n/select]" — and a cancelled prompt
            # still exits 0. Answer Y on stdin and trust only a re-listing.
            _, err = _run_hermes(
                [
                    "mcp",
                    "add",
                    "condor",
                    "--command",
                    "uv",
                    "--args",
                    "run",
                    "--directory",
                    str(REPO_ROOT),
                    "python",
                    "-m",
                    "mcp_servers.condor",
                ],
                input="Y\n",
            )
            issues.append(
                "registered condor MCP server (all tools enabled)"
                if _hermes_mcp_registered()
                else f"could not register condor MCP ({err.strip()[:120] or 'add was cancelled'}) "
                "— run `hermes mcp add condor --command uv --args run --directory "
                f"{REPO_ROOT} python -m mcp_servers.condor` manually"
            )
        else:
            issues.append("condor MCP not registered")
    if not skill_linked:
        if apply_fix:
            if link.exists() and not link.is_symlink():
                issues.append(
                    f"{link} exists and is not a symlink — resolve manually"
                )
            else:
                link.parent.mkdir(parents=True, exist_ok=True)
                link.unlink(missing_ok=True)
                link.symlink_to(target)
                issues.append("linked /condor skill into ~/.hermes/skills")
        else:
            issues.append("/condor skill not linked in ~/.hermes/skills")
    return issues
