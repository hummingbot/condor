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
import textwrap
from dataclasses import dataclass
from pathlib import Path

from condor.setup_llm import ENV_PATH, base_of, current_default, read_env

OK, WARN, FAIL = "ok", "warn", "fail"
_BADGES = {OK: "✓", WARN: "!", FAIL: "✗"}

REPO_ROOT = Path(__file__).resolve().parent.parent

# Matches setup-environment.sh's own banner width, so the wizard's closing
# "Setup complete!" frame and this report's frame read as one design
# language when `make install` runs them back to back. It is a *minimum*:
# a check whose detail does not fit (a connection error, a remediation hint)
# widens the report up to _MAX_WIDTH / the terminal rather than spilling
# past the frame, and anything still too long wraps under the detail column.
_FRAME_WIDTH = 46
_MAX_WIDTH = 100

# Columns consumed before the detail text starts: the 4-space indent, the
# badge and its trailing space, the name column, and the two spaces after it.
_GUTTER = 4 + 2 + 2

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

    def render(self, width: int, report_width: int = _FRAME_WIDTH) -> str:
        color = _COLORS[self.state]
        indent = " " * (_GUTTER + width)
        head = f"    {color}{_BADGES[self.state]}{_RESET} {self.name:<{width}}  "

        body = report_width - _GUTTER - width
        # Below ~24 columns wrapping produces a ragged one-word-per-line
        # column that is harder to read than an overlong line -- leave it be.
        pieces = (
            textwrap.wrap(self.detail, width=body) or [""]
            if body >= 24
            else [self.detail]
        )
        lines = [f"{head}{_DIM}{pieces[0]}{_RESET}"]
        lines.extend(f"{indent}{_DIM}{piece}{_RESET}" for piece in pieces[1:])
        return "\n".join(lines)


def _name_width(checks: list[Check]) -> int:
    return max([28] + [len(c.name) for c in checks])


def _report_width(sections: list[tuple[str, list[Check]]]) -> int:
    """How wide the framed report should be.

    Starts at the wizard-matching :data:`_FRAME_WIDTH` and grows only as far
    as the longest single check line actually needs, capped by the terminal
    (and :data:`_MAX_WIDTH`, since a full-width report on an ultrawide
    terminal is worse to read, not better).
    """
    terminal = shutil.get_terminal_size(fallback=(80, 24)).columns
    cap = max(_FRAME_WIDTH, min(terminal - 2, _MAX_WIDTH))
    longest = _FRAME_WIDTH
    for _, checks in sections:
        width = _name_width(checks)
        for check in checks:
            longest = max(longest, _GUTTER + width + len(check.detail))
    return min(longest, cap)


def _frame(width: int, char: str = "═", color: str = _BOLD) -> str:
    return f"{color}{char * width}{_RESET}"


def _section(title: str, checks: list[Check], report_width: int) -> str:
    width = _name_width(checks)
    header = f"  {_BOLD}{title}{_RESET}"
    rule = f"  {_DIM}{'─' * (report_width - 2)}{_RESET}"
    lines = [header, rule]
    lines.extend(c.render(width, report_width) for c in checks)
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

    # `make run` builds the dashboard before starting anything, so a missing
    # node_modules is a startup failure waiting to happen -- and the fix
    # (`make install`) is not obvious from the npm error it produces.
    frontend = REPO_ROOT / "frontend"
    if not frontend.is_dir():
        checks.append(Check("frontend", FAIL, "frontend/ is missing from the repo"))
    elif (frontend / "node_modules").is_dir():
        checks.append(Check("frontend deps", OK, "frontend/node_modules present"))
    else:
        checks.append(
            Check(
                "frontend deps",
                WARN,
                "frontend/node_modules missing — `make run` will fail its build; "
                "run `make install`",
            )
        )
    return checks


# ── Config ───────────────────────────────────────────────────────────────────


# The credentials setup-environment.sh used to seed config.yml with before it
# started requiring real ones. An install that still carries them either
# predates that change or took the "keep the API that is already running" path,
# which does not ask for credentials -- either way the API will answer 401 and
# the reason is worth naming before the connectivity check below just reports
# the failure.
_PLACEHOLDER_CREDENTIALS = {"admin", "password", "changeme", ""}


def _check_placeholder_credentials(data: object) -> list[Check]:
    if not isinstance(data, dict):
        return []
    servers = data.get("servers")
    if not isinstance(servers, dict):
        return []

    stale = [
        name
        for name, server in servers.items()
        if isinstance(server, dict)
        and str(server.get("password", "")).strip().lower() in _PLACEHOLDER_CREDENTIALS
    ]
    if not stale:
        return []
    return [
        Check(
            "API credentials",
            WARN,
            f"{', '.join(sorted(stale))} still uses a placeholder password — set "
            "the real hummingbot-api username/password via /servers (or in "
            "config.yml) or the API will answer 401",
        )
    ]


def check_config() -> list[Check]:
    from utils.config import MODE_LOCAL, resolve_mode

    checks = []
    env = read_env(ENV_PATH)

    if not ENV_PATH.exists():
        # A checkout that has never been set up: .env, config.yml, the mode and
        # both credentials are *all* missing at once. Reporting that as four
        # separate failures reads like four separate things went wrong, when
        # there is only one thing to do about it.
        checks.append(
            Check(
                "Setup",
                FAIL,
                "not set up yet — no .env. Run `make install` (or `make setup` "
                "if dependencies are already in place)",
            )
        )
    else:
        if resolve_mode(env) == MODE_LOCAL:
            # Local mode has no bot at all -- an empty TELEGRAM_TOKEN here is
            # the deliberate outcome of that choice, not a missing setup step.
            checks.append(
                Check("TELEGRAM_TOKEN", OK, "not used — local mode (no Telegram)")
            )
        else:
            token = env.get("TELEGRAM_TOKEN", "").strip()
            checks.append(
                Check(
                    "TELEGRAM_TOKEN",
                    OK if token else FAIL,
                    (
                        "configured"
                        if token
                        else "missing — run `make setup` to set it (or pick "
                        "local mode, which needs no bot)"
                    ),
                )
            )

        admin_id = env.get("ADMIN_USER_ID", "").strip()
        checks.append(
            Check(
                "ADMIN_USER_ID",
                OK if admin_id else FAIL,
                admin_id or "missing — run `make setup`",
            )
        )

    config_yml = REPO_ROOT / "config.yml"
    if not config_yml.exists():
        if ENV_PATH.exists():
            checks.append(Check("config.yml", FAIL, "not found — run `make setup`"))
    else:
        try:
            import yaml

            data = yaml.safe_load(config_yml.read_text(encoding="utf-8"))
            checks.append(Check("config.yml", OK, "present and parses"))
            checks.extend(_check_placeholder_credentials(data))
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


_SS_PROCESS = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def _listening_process(port: int) -> str:
    """``"name (pid N)"`` for whatever holds ``port``, or ``""`` if unknown.

    Worth naming in the report: doctor cannot otherwise tell Condor's own
    dashboard apart from an unrelated service that happens to have taken the
    port, and "your dashboard is on every interface" is a confusing thing to
    read when the listener is somebody else's process entirely.
    """
    if shutil.which("ss"):
        try:
            proc = subprocess.run(
                ["ss", "-H", "-ltnp", f"( sport = :{port} )"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if match := _SS_PROCESS.search(proc.stdout):
                return f"{match.group(1)}, pid {match.group(2)}"
        except Exception:
            pass
    return ""


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

    owner = _listening_process(WEB_PORT)
    held_by = f" (held by {owner})" if owner else ""

    public = [b for b in binds if _is_public_bind(b)]
    if USE_TAILSCALE:
        if public:
            return [
                Check(
                    "Dashboard port",
                    FAIL,
                    f"USE_TAILSCALE=true but {WEB_PORT} is bound to "
                    f"{public[0]} (all interfaces){held_by} — should be "
                    "127.0.0.1-only",
                )
            ]
        return [
            Check(
                "Dashboard port", OK, f"127.0.0.1:{WEB_PORT} only (Tailscale){held_by}"
            )
        ]

    if public:
        return [
            Check(
                "Dashboard port",
                WARN,
                f"{WEB_PORT} is reachable on all interfaces{held_by} — fine on a "
                "trusted LAN, risky on a public VPS. Enable Tailscale "
                "(`make setup`) or firewall it.",
            )
        ]
    return [Check("Dashboard port", OK, f"127.0.0.1:{WEB_PORT} only{held_by}")]


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


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

_HB_CONTAINER = "hummingbot-api"


def _local_stack_diagnosis() -> list[Check]:
    """Why a *co-located* hummingbot-api isn't answering.

    Only consulted after a localhost server has already failed to connect:
    on the happy path this would be pure noise, and on a remote-API install
    Docker's state on this machine says nothing about the API at all. The
    connectivity failure above tells you it is down; this tells you which of
    the three usual reasons it is.
    """
    if not shutil.which("docker"):
        return [
            Check(
                "Docker",
                FAIL,
                "not installed — the local hummingbot-api stack runs on it: "
                "https://docs.docker.com/get-docker/",
            )
        ]
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return [Check("Docker", WARN, f"installed; could not run `docker ps`: {e}")]

    if proc.returncode != 0:
        return [
            Check(
                "Docker",
                FAIL,
                "installed but the daemon is not responding — start Docker "
                "Desktop, or `sudo systemctl start docker`",
            )
        ]

    for line in proc.stdout.splitlines():
        name, _, status = line.partition("\t")
        if name.strip() == _HB_CONTAINER:
            return [
                Check(
                    f"{_HB_CONTAINER} container",
                    OK,
                    status.strip() or "running — but not answering; check its logs",
                )
            ]
    return [
        Check(
            f"{_HB_CONTAINER} container",
            FAIL,
            "not running — start it with `cd ../hummingbot-api && make deploy`",
        )
    ]


def check_hummingbot_api() -> list[Check]:
    # Constructing a ConfigManager against a missing config.yml *creates* one
    # (ConfigManager._init_default_config writes its defaults straight to
    # disk). Doctor promises to be read-only, and a doctor run that silently
    # produced an empty config.yml also made the "not found -- run `make
    # setup`" check above unreachable on every later run. So on an
    # unconfigured install, say so and touch nothing.
    if not (REPO_ROOT / "config.yml").exists():
        # On a checkout that was never set up, the Setup check above already
        # says this and names the same command -- repeating it as a second
        # failure just inflates the count. It is a real failure only for a
        # half-configured install: .env exists, config.yml somehow does not.
        return [
            Check(
                "Hummingbot API",
                FAIL if ENV_PATH.exists() else WARN,
                "no config.yml yet — run `make setup`",
            )
        ]

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
    local_failure = False
    for name, server in servers.items():
        host, port = server.get("host"), server.get("port")
        label = f"API '{name}' ({host}:{port})"
        try:
            asyncio.run(_probe_and_close(cm, name))
            checks.append(Check(label, OK, "reachable, authenticated"))
        except Exception as e:
            checks.append(Check(label, FAIL, f"{e} — {_connection_hint(host, e)}"))
            local_failure = local_failure or host in _LOCAL_HOSTS

    if local_failure:
        checks.extend(_local_stack_diagnosis())
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

    width = _report_width(sections)

    print()
    print(_frame(width))
    print(f"  {_BOLD}Condor Doctor{_RESET}")
    print(_frame(width))
    print()

    all_checks: list[Check] = []
    for title, checks in sections:
        print(_section(title, checks, width))
        print()
        all_checks.extend(checks)

    summary, exit_code = _tally(all_checks)
    if exit_code:
        border_color = _COLORS[FAIL]
    elif any(c.state == WARN for c in all_checks):
        border_color = _COLORS[WARN]
    else:
        border_color = _COLORS[OK]

    print(_frame(width, color=border_color))
    print(f"  {summary}")
    print(_frame(width, color=border_color))
    print()
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
