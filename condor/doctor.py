"""Verify Condor's install and runtime state.

``uv run python -m condor.doctor`` re-checks everything the install wizard
only confirms once at setup time: required dependencies, `.env`/`config.yml`
sanity, the AI model's actual readiness, whether the web dashboard is
sitting on a public interface it shouldn't be, and whether every configured
Hummingbot API server is actually reachable and authenticating.

Read-only — nothing here mutates `.env`, `config.yml`, or any running
process. Unlike :mod:`condor.setup_llm` (which always exits 0 because a
model can be picked later), this exits non-zero when a check actually
``fail``s, so `make install`/CI can act on the result.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from condor.setup_llm import ENV_PATH, base_of, current_default, read_env

OK, WARN, FAIL = "ok", "warn", "fail"
_BADGES = {OK: "✓", WARN: "!", FAIL: "✗"}

REPO_ROOT = Path(__file__).resolve().parent.parent

# Matches setup-environment.sh's own banner width, so the wizard's closing
# "Setup complete!" frame and this report's frame read as one design
# language when `make install` runs them back to back.
_FRAME_WIDTH = 46

# Colored to match setup-environment.sh's msg_ok/msg_warn/msg_error palette.
# Off for a non-tty (piped/redirected output, CI logs) or NO_COLOR -- see
# https://no-color.org -- so scripts parsing this report never see escapes.
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_BOLD = "\033[1m" if _USE_COLOR else ""
_DIM = "\033[2m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""
_COLORS = (
    {OK: "\033[0;32m", WARN: "\033[1;33m", FAIL: "\033[0;31m"}
    if _USE_COLOR
    else {OK: "", WARN: "", FAIL: ""}
)


@dataclass
class Check:
    name: str
    state: str
    detail: str

    def render(self, width: int) -> str:
        color = _COLORS[self.state]
        return (
            f"    {color}{_BADGES[self.state]}{_RESET} {self.name:<{width}}  "
            f"{_DIM}{self.detail}{_RESET}"
        )


def _frame(char: str = "═", color: str = _BOLD) -> str:
    return f"{color}{char * _FRAME_WIDTH}{_RESET}"


def _section(title: str, checks: list[Check]) -> str:
    width = max([28] + [len(c.name) for c in checks])
    header = f"  {_BOLD}{title}{_RESET}"
    rule = f"  {_DIM}{'─' * (_FRAME_WIDTH - 2)}{_RESET}"
    lines = [header, rule]
    lines.extend(c.render(width) for c in checks)
    return "\n".join(lines)


# ── Dependencies ─────────────────────────────────────────────────────────────

# (label, command, severity if missing — uv is load-bearing, the rest only
# matter for `make run`/frontend builds)
_DEPS = [
    ("uv", ["uv", "--version"], FAIL),
    ("tmux", ["tmux", "-V"], WARN),
    ("node", ["node", "--version"], WARN),
    ("npm", ["npm", "--version"], WARN),
    ("typescript (tsc)", ["tsc", "--version"], WARN),
]


def check_dependencies() -> list[Check]:
    checks = []
    for label, cmd, missing_severity in _DEPS:
        if not shutil.which(cmd[0]):
            checks.append(Check(label, missing_severity, "not found"))
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            version = (proc.stdout or proc.stderr).strip().splitlines()
            detail = version[0] if version else "installed"
        except Exception:
            detail = "installed"
        checks.append(Check(label, OK, detail))
    return checks


# ── Config ───────────────────────────────────────────────────────────────────


def check_config() -> list[Check]:
    from utils.config import MODE_LOCAL, resolve_mode

    checks = []
    env = read_env(ENV_PATH)

    if resolve_mode(env) == MODE_LOCAL:
        # Local mode has no bot at all -- an empty TELEGRAM_TOKEN here is the
        # deliberate outcome of that choice, not a missing setup step.
        checks.append(
            Check("TELEGRAM_TOKEN", OK, "not used — local mode (no Telegram)")
        )
    else:
        token = env.get("TELEGRAM_TOKEN", "").strip()
        checks.append(
            Check(
                "TELEGRAM_TOKEN",
                OK if token else FAIL,
                "configured" if token else "missing — set in .env",
            )
        )

    admin_id = env.get("ADMIN_USER_ID", "").strip()
    checks.append(
        Check(
            "ADMIN_USER_ID",
            OK if admin_id else FAIL,
            admin_id or "missing — set in .env",
        )
    )

    config_yml = REPO_ROOT / "config.yml"
    if not config_yml.exists():
        checks.append(Check("config.yml", FAIL, "not found — run `make setup`"))
    else:
        try:
            import yaml

            yaml.safe_load(config_yml.read_text(encoding="utf-8"))
            checks.append(Check("config.yml", OK, "present and parses"))
        except Exception as e:
            checks.append(Check("config.yml", FAIL, f"failed to parse: {e}"))

    # Same readiness check `condor/setup_llm.py --status` reports for the
    # default agent — reused rather than reimplemented so the two can never
    # disagree about whether the current default actually works.
    from condor.llm import readiness

    agent_key = current_default(env)
    try:
        state = asyncio.run(readiness.probe(base_of(agent_key), env))
        badge = {"ready": OK, "unverified": WARN, "missing": FAIL}.get(
            state.state, WARN
        )
        checks.append(Check("AI model", badge, f"{agent_key} — {state.detail}"))
    except Exception as e:
        checks.append(Check("AI model", WARN, f"{agent_key} — could not check: {e}"))

    return checks


# ── Port exposure ────────────────────────────────────────────────────────────


def _listening_binds(port: int) -> list[str]:
    """Raw local bind addresses (e.g. ``0.0.0.0:8088``, ``127.0.0.1:8088``)
    currently LISTEN-ing on ``port``. Empty if nothing is listening or
    neither ``ss`` nor ``lsof`` is available."""
    if shutil.which("ss"):
        try:
            proc = subprocess.run(
                ["ss", "-H", "-ltn", f"( sport = :{port} )"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return [
                cols[3]
                for line in proc.stdout.splitlines()
                if len(cols := line.split()) >= 4
            ]
        except Exception:
            pass
    if shutil.which("lsof"):
        try:
            proc = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            binds = []
            for line in proc.stdout.splitlines()[1:]:  # skip header row
                cols = line.split()
                if cols:
                    binds.append(cols[-1].split("(")[0])
            return binds
        except Exception:
            pass
    return []


def _is_public_bind(addr: str) -> bool:
    host = addr.rsplit(":", 1)[0] if re.search(r":\d+$", addr) else addr
    return host in ("0.0.0.0", "*", "::", "[::]", "")


def check_dashboard_port() -> list[Check]:
    from utils.config import USE_TAILSCALE, WEB_PORT

    binds = _listening_binds(WEB_PORT)
    if not binds:
        # Normal right after `make install` -- Condor has never been started
        # yet, so there is nothing to bind-check. Not a warning: it would fire
        # on every fresh install's automatic doctor run for no real reason.
        return [
            Check(
                "Dashboard port",
                OK,
                f"not running yet — start with `make run` to check the real "
                f"{WEB_PORT} bind",
            )
        ]

    public = [b for b in binds if _is_public_bind(b)]
    if USE_TAILSCALE:
        if public:
            return [
                Check(
                    "Dashboard port",
                    FAIL,
                    f"USE_TAILSCALE=true but {WEB_PORT} is bound to "
                    f"{public[0]} (all interfaces) — should be 127.0.0.1-only",
                )
            ]
        return [Check("Dashboard port", OK, f"127.0.0.1:{WEB_PORT} only (Tailscale)")]

    if public:
        return [
            Check(
                "Dashboard port",
                WARN,
                f"{WEB_PORT} is reachable on all interfaces — fine on a "
                "trusted LAN, risky on a public VPS. Enable Tailscale "
                "(`make setup`) or firewall it.",
            )
        ]
    return [Check("Dashboard port", OK, f"127.0.0.1:{WEB_PORT} only")]


# ── Tailscale ────────────────────────────────────────────────────────────────

_TAILSCALE_INSTALL = "curl -fsSL https://tailscale.com/install.sh | sh"


def check_tailscale() -> list[Check]:
    from utils.config import USE_TAILSCALE

    if not USE_TAILSCALE:
        return [Check("Tailscale", OK, "not enabled (USE_TAILSCALE is not set)")]

    if not shutil.which("tailscale"):
        return [
            Check(
                "Tailscale",
                FAIL,
                f"USE_TAILSCALE=true but tailscale isn't installed — {_TAILSCALE_INSTALL}",
            )
        ]

    try:
        proc = subprocess.run(
            ["tailscale", "status"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as e:
        return [Check("Tailscale", WARN, f"installed; could not check status: {e}")]

    if proc.returncode != 0:
        return [
            Check("Tailscale", FAIL, "installed but not connected — run `tailscale up`")
        ]

    # The first non-blank line of `tailscale status` is this node's own tailnet
    # identity (hostname, IP, account) -- confirms *which* tailnet, not just
    # that some daemon answered.
    first_line = next(
        (line.strip() for line in proc.stdout.splitlines() if line.strip()), ""
    )
    return [Check("Tailscale", OK, first_line or "connected")]


# ── Hummingbot API connectivity ──────────────────────────────────────────────


def _connection_hint(host: str, exc: Exception) -> str:
    from utils.config import USE_TAILSCALE

    msg = str(exc).lower()
    if "401" in msg or "unauthor" in msg or "auth" in msg:
        return "check the username/password in /servers"
    if host in ("localhost", "127.0.0.1"):
        return "is it running? `cd ../hummingbot-api && docker compose ps`"
    if USE_TAILSCALE:
        return "see the Tailscale check above"
    return "check the host is reachable — firewall, Tailscale status, or the server is down"


async def _probe_and_close(cm, name: str) -> None:
    """Confirm ``name`` connects, then close the client's session immediately.

    ``get_client()`` is written for the long-running bot process, which keeps
    the client (and its aiohttp session) cached for reuse across requests.
    Doctor is a one-shot script that exits right after this check, so nothing
    would otherwise ever close that session -- aiohttp then complains about an
    "Unclosed client session" during interpreter shutdown. Safe to close here:
    this check's :class:`ConfigManager` instance lives only for this process.
    """
    client = await cm.get_client(name)
    await client.close()


def check_hummingbot_api() -> list[Check]:
    from config_manager import get_config_manager

    cm = get_config_manager()
    servers = cm.list_servers()
    if not servers:
        return [
            Check(
                "Hummingbot API",
                WARN,
                "no servers configured — add one via /servers or `make setup`",
            )
        ]

    checks = []
    for name, server in servers.items():
        host, port = server.get("host"), server.get("port")
        label = f"API '{name}' ({host}:{port})"
        try:
            asyncio.run(_probe_and_close(cm, name))
            checks.append(Check(label, OK, "reachable, authenticated"))
        except Exception as e:
            checks.append(Check(label, FAIL, f"{e} — {_connection_hint(host, e)}"))
    return checks


# ── Entry point ──────────────────────────────────────────────────────────────


def _tally(all_checks: list[Check]) -> tuple[str, int]:
    """The closing pass/warn/fail counts, and the process exit code they imply."""
    failed = sum(1 for c in all_checks if c.state == FAIL)
    warned = sum(1 for c in all_checks if c.state == WARN)
    passed = len(all_checks) - failed - warned

    line = "   ".join(
        [
            f"{_COLORS[OK]}{_BADGES[OK]} {passed} passed{_RESET}",
            f"{_COLORS[WARN]}{_BADGES[WARN]} {warned} warning(s){_RESET}",
            f"{_COLORS[FAIL]}{_BADGES[FAIL]} {failed} failed{_RESET}",
        ]
    )
    return line, (1 if failed else 0)


def main(argv: list[str] | None = None) -> int:
    sections = [
        ("Dependencies", check_dependencies()),
        ("Configuration", check_config()),
        ("Dashboard", check_dashboard_port()),
        ("Tailscale", check_tailscale()),
        ("Hummingbot API", check_hummingbot_api()),
    ]

    print()
    print(_frame())
    print(f"  {_BOLD}Condor Doctor{_RESET}")
    print(_frame())
    print()

    all_checks: list[Check] = []
    for title, checks in sections:
        print(_section(title, checks))
        print()
        all_checks.extend(checks)

    summary, exit_code = _tally(all_checks)
    if exit_code:
        border_color = _COLORS[FAIL]
    elif any(c.state == WARN for c in all_checks):
        border_color = _COLORS[WARN]
    else:
        border_color = _COLORS[OK]

    print(_frame(color=border_color))
    print(f"  {summary}")
    print(_frame(color=border_color))
    print()
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
