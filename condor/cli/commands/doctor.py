"""``condor doctor`` — the checks whose failures otherwise surface one at a
time as confusing runtime errors (hbot's charter, adopted verbatim).

Every check is a row: ``ok`` / ``warn`` / ``fail`` / ``skip`` plus a one-line
detail with the remedy. Any ``fail`` exits 1; warns and skips are advisories
and exit 0. Checks never crash the command: an unexpected exception becomes
that check's ``fail`` row.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional

import typer

from condor.cli.commands._common import REPO_ROOT, try_control
from condor.cli.output import ExitCode, emit, fail, json_option, render_table

SOCKET_PATH = REPO_ROOT / "store" / "condor-control.sock"
DIRECT_TOKEN_PATH = REPO_ROOT / "store" / ".direct-token"

NETWORK_TIMEOUT = 5.0
# Signed venue APIs (Hyperliquid signatures, Solana blockhashes) reject
# drifted clocks; thresholds are forgiving of our ~1s time-source granularity.
CLOCK_WARN_S = 2.0
CLOCK_FAIL_S = 10.0
DISK_WARN_BYTES = 1 << 30  # 1 GiB free
DISK_FAIL_BYTES = 100 << 20  # 100 MiB free
RUNS_WARN_BYTES = (
    1 << 30
)  # 1 GiB of run streams/artifacts (§9.4.4 retention still open)
LEARNINGS_NEAR_CAP = 35


def _row(check: str, status: str, detail: str) -> dict:
    return {"check": check, "status": status, "detail": detail}


def _server_up() -> bool:
    ping = try_control("ping", timeout=NETWORK_TIMEOUT)
    return bool(ping and ping.get("ok"))


# ── individual checks ────────────────────────────────────────────────────────


def _install_row() -> dict:
    import platform

    rev = "unknown"
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        pass
    return _row(
        "install", "ok", f"{REPO_ROOT} @ {rev}, python {platform.python_version()}"
    )


def _path_row(apply_fix: bool) -> dict:
    """The ~/.local/bin/condor symlink points at THIS install's venv script
    (install.sh creates it; --fix re-creates it). `shutil.which` is useless
    here — under `uv run` the venv bin is prepended to PATH, masking a
    missing user-shell link."""
    script = REPO_ROOT / ".venv" / "bin" / "condor"
    if not script.exists():
        return _row(
            "path",
            "warn",
            f"{script} missing — run `uv sync` to install the entry point",
        )
    link = Path.home() / ".local" / "bin" / "condor"
    on_path = str(link.parent) in os.environ.get("PATH", "").split(":")
    if link.is_symlink() or link.exists():
        if link.exists() and link.resolve() == script.resolve():
            detail = f"{link} -> {script}"
            if not on_path:
                detail += " — but ~/.local/bin is not on PATH"
            return _row("path", "ok" if on_path else "warn", detail)
        if not apply_fix:
            target = link.resolve() if link.exists() else "a missing target"
            return _row(
                "path",
                "warn",
                f"{link} points at {target}, not this install — relink with "
                f"`uv run --directory {REPO_ROOT} condor doctor --fix`",
            )
    elif not apply_fix:
        # `condor` itself is unrunnable in this state — the remedy must be an
        # invocation that works without the link.
        return _row(
            "path",
            "warn",
            f"`condor` is not linked onto PATH — fix with "
            f"`uv run --directory {REPO_ROOT} condor doctor --fix` "
            f"(links {link})",
        )
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(script)
    detail = f"linked {link} -> {script}"
    if not on_path:
        detail += " — add ~/.local/bin to your PATH"
    return _row("path", "ok" if on_path else "warn", detail)


def _socket_row() -> dict:
    from condor.cli.commands._common import probe_control

    if not SOCKET_PATH.exists():
        return _row(
            "control socket", "warn", "server not running — start with `condor serve`"
        )
    t0 = time.monotonic()
    state = probe_control(timeout=NETWORK_TIMEOUT)
    ms = round((time.monotonic() - t0) * 1000)
    if state == "up":
        return _row("control socket", "ok", f"answers in {ms}ms")
    if state == "blocked":
        return _row(
            "control socket",
            "warn",
            "connect DENIED by this shell's sandbox (e.g. Codex) — server "
            "state unknown, may well be up; re-run unsandboxed or check via "
            "the MCP tools",
        )
    return _row(
        "control socket",
        "fail",
        "socket exists but does not answer — server wedged? restart `condor serve`",
    )


def _direct_token_row() -> dict:
    if not SOCKET_PATH.exists():
        return _row("direct token", "skip", "server not running")
    if not DIRECT_TOKEN_PATH.exists():
        return _row(
            "direct token",
            "fail",
            "store/.direct-token missing — restart `condor serve` (it mints one at startup)",
        )
    # Both files are written at startup, token AFTER the socket; an older
    # token means it belongs to a previous server process.
    if DIRECT_TOKEN_PATH.stat().st_mtime < SOCKET_PATH.stat().st_mtime - 5:
        return _row(
            "direct token",
            "fail",
            "store/.direct-token predates the server start — stale; restart `condor serve`",
        )
    return _row("direct token", "ok", "fresh for this server process")


def _stale_clients_row() -> dict:
    """MCP client processes older than the server survive server restarts
    holding dead sockets (incident 3's openclaw-gateway wedge)."""
    if not SOCKET_PATH.exists():
        return _row("stale clients", "skip", "server not running")
    server_start = SOCKET_PATH.stat().st_mtime
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,lstart=,command="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception as e:
        return _row("stale clients", "warn", f"could not scan processes: {e}")
    stale = []
    for line in out.splitlines():
        if "mcp_servers.condor" not in line or "grep" in line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        pid, lstart = parts[0], " ".join(parts[1:6])
        try:
            started = time.mktime(time.strptime(lstart, "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            continue
        if started < server_start - 5:
            stale.append(pid)
    if stale:
        return _row(
            "stale clients",
            "warn",
            f"{len(stale)} condor MCP client(s) predate the server "
            f"(pids {', '.join(stale)}) — restart your harness/gateway",
        )
    return _row("stale clients", "ok", "no MCP clients older than the server")


def _remote_unix_time() -> Optional[float]:
    """Best-effort current UTC time from a public source, RTT-compensated;
    None if offline."""
    import urllib.request

    try:
        t0 = time.time()
        with urllib.request.urlopen(
            "https://api.kraken.com/0/public/Time", timeout=NETWORK_TIMEOUT
        ) as resp:
            data = json.loads(resp.read())
        return float(data["result"]["unixtime"]) + (time.time() - t0) / 2
    except Exception:
        pass
    try:
        from email.utils import parsedate_to_datetime

        req = urllib.request.Request("https://www.cloudflare.com", method="HEAD")
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            date_header = resp.headers.get("Date")
        if date_header:
            return (
                parsedate_to_datetime(date_header).timestamp() + (time.time() - t0) / 2
            )
    except Exception:
        pass
    return None


def _clock_row() -> dict:
    remote = _remote_unix_time()
    if remote is None:
        return _row(
            "clock",
            "warn",
            "could not reach a time source — offline? (trading needs network access)",
        )
    skew = abs(time.time() - remote)
    detail = f"skew vs internet time ~{skew:.1f}s"
    if skew <= CLOCK_WARN_S:
        return _row("clock", "ok", detail)
    if skew <= CLOCK_FAIL_S:
        return _row(
            "clock",
            "warn",
            f"{detail} — signed venue requests may start failing; sync NTP",
        )
    return _row(
        "clock",
        "fail",
        f"{detail} — signed venue requests WILL fail; sync your clock (NTP)",
    )


def _tail_event(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            lines = f.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


def _unswept_runs(live_ids: set[str]) -> list[Path]:
    from condor.agents.runstore import get_run_store

    store = get_run_store()
    pending = []
    for slug in store.agent_slugs_with_runs():
        for path in store.all_run_paths(slug):
            if path.stem in live_ids:
                continue
            tail = _tail_event(path)
            if tail is not None and tail.get("type") != "run_ended":
                pending.append(path)
    return pending


def _interrupted_runs_row(apply_fix: bool) -> dict:
    server_up = _server_up()
    live = (try_control("agent.list") or {}).get("agents", []) if server_up else []
    live_ids = {i.get("agent_id", "") for i in live}
    pending = _unswept_runs(live_ids)
    if not pending:
        return _row("run streams", "ok", "every ended stream has run_ended")
    names = ", ".join(p.stem for p in pending[:3]) + ("…" if len(pending) > 3 else "")
    if apply_fix:
        if server_up:
            return _row(
                "run streams",
                "warn",
                f"{len(pending)} interrupted stream(s) ({names}) — the server sweeps "
                "at startup; not sweeping under a live server (restart `condor serve`)",
            )
        from condor.agents.runstore import get_run_store

        voided = get_run_store().sweep_interrupted()
        return _row(
            "run streams",
            "ok",
            f"swept {len(pending)} interrupted stream(s), voided "
            f"{len(voided)} pending approval(s)",
        )
    remedy = (
        "restart `condor serve` to sweep"
        if server_up
        else "run `condor doctor --fix` to sweep"
    )
    return _row(
        "run streams",
        "warn",
        f"{len(pending)} stream(s) awaiting sweep ({names}) — {remedy}",
    )


def _account_probe_rows() -> list[dict]:
    from condor.accounts.onboarding import ProbeWarning, default_probe
    from condor.executors.wallets import account_credentials, account_store

    store = account_store()
    try:
        data = store.load()
    except Exception as e:
        return [_row("accounts", "fail", f"store unreadable: {e}")]
    rows = []
    for venue_id, entry in sorted(data.items()):
        if venue_id.startswith("_"):
            continue
        for addr in entry.get("accounts", {}):
            check = f"probe {venue_id} {addr[:10]}…"
            try:
                ref = store.resolve(venue_id, addr)
                default_probe(venue_id, ref, account_credentials(venue_id, addr))
                rows.append(_row(check, "ok", "read-only probe passed"))
            except ProbeWarning as w:
                rows.append(_row(check, "warn", str(w)[:240]))
            except Exception as e:
                rows.append(_row(check, "fail", str(e)[:180]))
    if not rows:
        rows.append(_row("accounts", "ok", "none configured"))
    return rows


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _disk_row() -> dict:
    from condor.agents.agent import agents_data_root

    from condor.agents.runstore import _RUN_DIRS

    usage = shutil.disk_usage(REPO_ROOT)
    free_gb = usage.free / (1 << 30)
    root = agents_data_root()
    runs_bytes = sum(
        _dir_size(d)
        for name in _RUN_DIRS
        for d in root.glob(f"*/{name}")
        if d.is_dir()
    )
    reports_dir = REPO_ROOT / "condor" / "reports"
    reports_bytes = _dir_size(reports_dir) if reports_dir.is_dir() else 0
    detail = (
        f"{free_gb:.1f} GiB free; runs/artifacts "
        f"{runs_bytes / (1 << 20):.0f} MiB, reports {reports_bytes / (1 << 20):.0f} MiB"
    )
    if usage.free < DISK_FAIL_BYTES:
        return _row("disk", "fail", f"{detail} — run streams will fail to write")
    if usage.free < DISK_WARN_BYTES:
        return _row("disk", "warn", f"{detail} — running low")
    if runs_bytes + reports_bytes > RUNS_WARN_BYTES:
        return _row(
            "disk",
            "warn",
            f"{detail} — no retention policy yet (§9.4.4); prune manually",
        )
    return _row("disk", "ok", detail)


def _learnings_row() -> dict:
    from condor.agents.agent import AgentStore, agents_data_root
    from condor.agents.learnings import MAX_LEARNINGS, read_learnings

    hot = []
    for agent in AgentStore().list_all():
        text = read_learnings(agents_data_root() / agent.slug)
        n = sum(1 for line in text.splitlines() if line.startswith("- "))
        if n >= LEARNINGS_NEAR_CAP:
            hot.append(f"{agent.slug} {n}/{MAX_LEARNINGS}")
    if hot:
        return _row(
            "learnings",
            "warn",
            f"at/near the {MAX_LEARNINGS} cap: {', '.join(hot)} — "
            "consolidation pressure (curate agents/<slug>/learnings.md)",
        )
    return _row("learnings", "ok", f"all agents under the {MAX_LEARNINGS} cap")


def _harness_wiring_rows(apply_fix: bool) -> list[dict]:
    """Repo-scoped harness wiring must match the repo canon (skills mirrors
    for Claude Code/Codex/OpenClaw, .mcp.json, .codex/config.toml) — drift
    here means a harness silently runs stale skills or no MCP server.
    Global harness configs (OpenClaw/Hermes) are checked read-only."""
    from condor.cli.commands import _wiring

    rows = []
    targets = [
        (f"skills {d.relative_to(REPO_ROOT)}", lambda fix, d=d: _wiring.mirror_skills(d, fix))
        for d in _wiring.SKILL_MIRROR_DIRS
    ] + [
        ("mcp .mcp.json", _wiring.mcp_json_issues),
        ("mcp .codex/config.toml", _wiring.codex_toml_issues),
        ("skills openclaw", _wiring.openclaw_issues),
        ("mcp+skills hermes", _wiring.hermes_issues),
    ]
    for name, fn in targets:
        issues = fn(apply_fix)
        if not issues:
            rows.append(_row(name, "ok", "matches repo canon"))
            continue
        detail = "; ".join(issues)
        unfixed = not apply_fix or any("manually" in i for i in issues)
        if not apply_fix:
            detail += " — run `condor doctor --fix`"
        rows.append(_row(name, "warn" if unfixed else "ok", detail))

    for harness, note in _wiring.external_harness_notes():
        rows.append(_row(f"mcp {harness}", "warn", note))
    return rows


def _frontend_row() -> dict:
    dist = REPO_ROOT / "frontend" / "dist" / "index.html"
    if dist.exists():
        return _row("frontend", "ok", "assets built")
    return _row(
        "frontend",
        "warn",
        "frontend/dist not built — non-headless `condor serve` has no "
        "dashboard (run `make build-frontend`)",
    )


def _spec_rows() -> list[dict]:
    """AGENT.md validation, including entry_guards names — caught HERE
    instead of at trade time."""
    from condor.agents.agent import AgentStore
    from condor.agents.spec import validate_agent_spec
    from condor.executors.guards import _GUARDS

    problems = []
    agents = AgentStore().list_all()
    for agent in agents:
        try:
            validate_agent_spec(agent)
        except Exception as e:
            problems.append(f"{agent.slug}: {e}")
            continue
        guards = (agent.default_config or {}).get("entry_guards") or {}
        unknown = [g for g in guards if g not in _GUARDS]
        if unknown:
            problems.append(
                f"{agent.slug}: unknown entry_guards {unknown} (known: {sorted(_GUARDS)})"
            )
    if problems:
        return [_row("agent specs", "fail", p) for p in problems]
    return [_row("agent specs", "ok", f"{len(agents)} spec(s) valid")]


def doctor(
    apply_fix: bool = typer.Option(
        False,
        "--fix",
        help="Apply safe remedies (sweep interrupted run streams while the server is down).",
    ),
    as_json: bool = json_option(),
) -> None:
    """Check the install's health; exit 0 = healthy, 1 = something needs fixing."""
    checks: List[Callable[[], object]] = [
        _install_row,
        lambda: _path_row(apply_fix),
        _socket_row,
        _direct_token_row,
        _stale_clients_row,
        _clock_row,
        lambda: _interrupted_runs_row(apply_fix),
        lambda: _harness_wiring_rows(apply_fix),
        _account_probe_rows,
        _disk_row,
        _learnings_row,
        _frontend_row,
        _spec_rows,
    ]
    rows: list[dict] = []
    for check in checks:
        try:
            result = check()
            rows.extend(result if isinstance(result, list) else [result])
        except Exception as e:  # a check may never crash the command
            name = getattr(check, "__name__", "check").strip("_").removesuffix("_row")
            rows.append(_row(name, "fail", f"check crashed: {e!r}"))
    healthy = all(r["status"] != "fail" for r in rows)
    payload = {"healthy": healthy, "checks": rows}
    emit(
        payload,
        render_table(
            rows,
            columns=["check", "status", "detail"],
            title="doctor",
            max_widths={"detail": 120},
        ),
        as_json,
    )
    if not healthy:
        fail("doctor found problems (see failed checks above)", ExitCode.ERROR)
