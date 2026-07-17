"""Condor command-line entry point (`python -m condor.cli`, or `make init`).

Subcommands:

    init         harness multi-select; emits per-harness config.
                 Idempotent — re-run any time to add a harness. Single
                 operator, loopback-gated (§5.5) — no login/identity step.
    update       pull the latest Condor from the current branch and
                 sync dependencies (restart `condor serve` after).
    accounts     structured account store (§6.2b) management. The bare
                 verb lists accounts (redacted: venue, custody address,
                 display name, default marker), most recently modified
                 first — the standard collection-verb grammar
                 (docs/cli-plan.md, principle 7).
                   import-env  EXPLICIT one-shot onboarding from the legacy
                               CONDOR_* env vars (custody derived from the
                               credentials, read-only probe unless
                               --no-probe, sealed at rest). Idempotent —
                               accounts are keyed by custody address. The
                               loaders themselves never read env vars.

Product questions live here, in Python, re-runnable forever — the bash
installer (install.sh) only bootstraps and then hands off to `init`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

HARNESSES = ("claude-code", "openclaw", "hermes", "condor")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


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


def detect_harnesses(home: Path | None = None) -> dict[str, bool]:
    """Evidence that each external harness exists on this box."""
    home = home or Path.home()
    return {
        "claude-code": shutil.which("claude") is not None,
        "openclaw": shutil.which("openclaw") is not None
        or (home / ".openclaw").exists(),
        "hermes": shutil.which("hermes") is not None or (home / ".hermes").exists(),
    }


# ── probes ──


# ── init steps ──


def _select_harnesses(args) -> list[str]:
    detected = detect_harnesses()
    default = ["condor"] + [h for h, found in detected.items() if found]

    if args.harness:
        selected = [h.strip() for h in args.harness.split(",") if h.strip()]
        unknown = [h for h in selected if h not in HARNESSES and h != "none"]
        if unknown:
            _die(f"unknown harness(es) {unknown}; choose from {list(HARNESSES)} or 'none'")
        return [] if selected == ["none"] else selected

    if not _interactive():
        return default

    found = [h for h, ok in detected.items() if ok]
    if found:
        print(f"• Detected on this box: {', '.join(found)}")
    print(f"  Available harnesses: {', '.join(HARNESSES)}")
    raw = _ask(
        "Harnesses to set up (comma-separated, 'none' for Tier 2 only)",
        default=",".join(default),
    )
    selected = [h.strip() for h in raw.split(",") if h.strip()]
    unknown = [h for h in selected if h not in HARNESSES and h != "none"]
    if unknown:
        _die(f"unknown harness(es) {unknown}; choose from {list(HARNESSES)} or 'none'")
    return [] if selected == ["none"] else selected


def _mcp_stdio_snippet() -> str:
    return (
        '"condor": {"type": "stdio", "command": "uv", "args": '
        f'["run", "--directory", "{REPO_ROOT}", "python", "-m", "mcp_servers.condor"]}}'
    )


def _emit_claude_code() -> None:
    print("\n── Claude Code ──")
    mcp_json = REPO_ROOT / ".mcp.json"
    skills = REPO_ROOT / ".claude" / "skills"
    if not mcp_json.exists() or not skills.exists():
        _die(f"repo is missing {mcp_json.name} or .claude/skills — checkout is incomplete")
    print(
        f"  In-repo: open {REPO_ROOT} in Claude Code — .mcp.json and\n"
        "  .claude/skills are picked up automatically. Try: 'list my agents'.\n"
        "  From anywhere, register the server user-wide:\n"
        f"    claude mcp add --scope user condor -- uv run --directory {REPO_ROOT} python -m mcp_servers.condor"
    )


def _emit_openclaw() -> None:
    print("\n── OpenClaw ──")
    print(
        "  Add this MCP server to your OpenClaw config (see their MCP docs\n"
        "  for the exact file on your version):\n"
        f"    {_mcp_stdio_snippet()}\n"
        f"  Skills: OpenClaw discovers {REPO_ROOT / 'skills'} via workspace scan."
    )


def _emit_hermes() -> None:
    print("\n── Hermes ──")
    installed = detect_harnesses()["hermes"]
    if not installed:
        print(
            "  Hermes is not installed. Install it yourself (we never install\n"
            "  another project's software), then re-run init:\n"
            "    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
        )
    print(
        "  Add this MCP server to your Hermes MCP config:\n"
        f"    {_mcp_stdio_snippet()}\n"
        "  Install the skills tap:\n"
        f"    hermes skills tap add {REPO_ROOT}"
    )


def _emit_condor() -> None:
    from utils.config import WEB_URL

    print("\n── Condor harness (web) ──")
    print(
        f"  Start Condor:  uv run python -m condor.cli serve   (or make run)\n"
        f"  Web chat:      {WEB_URL} — loopback-only (§5.5), no login."
    )


def cmd_init(args) -> None:
    print(f"Condor init — {REPO_ROOT}\n")
    selected = _select_harnesses(args)

    emitters = {
        "claude-code": _emit_claude_code,
        "openclaw": _emit_openclaw,
        "hermes": _emit_hermes,
        "condor": _emit_condor,
    }
    for harness in selected:
        emitters[harness]()
    if not selected:
        print("\nNo harness selected: Tier 2 + monitoring dashboard only.")

    print("\nDone. `init` is idempotent — re-run it any time to add a harness.")


# ── account store (§6.2b activation) ──


def _env_credential_plan() -> list[tuple[str, dict]]:
    """(venue_id, credentials) for each venue with legacy env creds present —
    the SAME variables the pre-activation loaders read implicitly."""
    env = os.environ.get
    plan: list[tuple[str, dict]] = []
    if env("CONDOR_HL_AGENT_KEY") or env("CONDOR_HL_ACCOUNT_ADDRESS"):
        venue_id = (
            "hyperliquid-testnet" if env("CONDOR_HL_NETWORK") == "testnet" else "hyperliquid"
        )
        plan.append((venue_id, {
            "agent_private_key": env("CONDOR_HL_AGENT_KEY"),
            "account_address": env("CONDOR_HL_ACCOUNT_ADDRESS"),
        }))
    if env("CONDOR_SOLANA_SECRET_KEY") or env("CONDOR_SOLANA_KEYSTORE"):
        plan.append(("solana", {
            "secret_key_b58": env("CONDOR_SOLANA_SECRET_KEY"),
            "keystore_path": env("CONDOR_SOLANA_KEYSTORE"),
            "keystore_passphrase": env("CONDOR_SOLANA_PASSPHRASE"),
            "rpc_url": env("CONDOR_SOLANA_RPC"),
        }))
    if env("CONDOR_POLYMARKET_KEY"):
        plan.append(("polymarket", {
            "private_key": env("CONDOR_POLYMARKET_KEY"),
            "funder": env("CONDOR_POLYMARKET_FUNDER"),
            "signature_type": env("CONDOR_POLYMARKET_SIG_TYPE"),
            "host": env("CONDOR_POLYMARKET_HOST"),
            "api_key": env("CONDOR_POLYMARKET_API_KEY"),
            "api_secret": env("CONDOR_POLYMARKET_API_SECRET"),
            "api_passphrase": env("CONDOR_POLYMARKET_API_PASSPHRASE"),
            "relayer_api_key": env("CONDOR_POLYMARKET_RELAYER_API_KEY"),
            "relayer_address": env("CONDOR_POLYMARKET_RELAYER_ADDRESS"),
        }))
    return plan


def cmd_account_import_env(args) -> None:
    """Explicit one-shot env→store onboarding. There is NO implicit seeding:
    this command is the only path from env credentials into the store."""
    from condor.accounts.onboarding import onboard_account

    probe = not args.no_probe
    plan = _env_credential_plan()
    jupiter_key = os.environ.get("CONDOR_JUPITER_API_KEY")
    if not plan and not jupiter_key:
        print("no CONDOR_* venue credentials found in the environment — nothing to import")
        return

    failures: list[str] = []
    for venue_id, credentials in plan:
        try:
            ref = onboard_account(venue_id, credentials, probe=probe)
            print(f"• {venue_id}: account {ref.custody_address} onboarded"
                  + ("" if probe else " (probe skipped)"))
        except Exception as e:
            failures.append(venue_id)
            print(f"! {venue_id}: NOT onboarded — {e}", file=sys.stderr)
    if jupiter_key:
        from condor.executors.wallets import save_service

        save_service("jupiter", {"api_key": jupiter_key})
        print("• jupiter: data-API key sealed into the _services block")

    if failures:
        _die(f"import-env failed for: {', '.join(failures)}")
    if plan:
        print(
            "Done. The matching CONDOR_* lines can be removed from .env — "
            "loaders read ONLY the store now."
        )


def cmd_accounts_list(args) -> None:
    """Redacted listing, most recently modified first (venue, custody
    address, name, default marker)."""
    from condor.executors.wallets import account_store

    data = account_store().load()
    rows = [
        (
            acct.get("updated_at", ""),
            venue_id,
            addr,
            acct.get("name", ""),
            addr == entry.get("default_account"),
        )
        for venue_id, entry in sorted(data.items())
        if not venue_id.startswith("_")
        for addr, acct in entry.get("accounts", {}).items()
    ]
    if not rows:
        print("no accounts configured — onboard via the dashboard or "
              "`python -m condor.cli accounts import-env`")
        return
    # Last modified first; accounts stored before updated_at stamping sort
    # to the bottom in venue order (stable sort).
    rows.sort(key=lambda r: r[0], reverse=True)
    for _, venue_id, addr, name, is_default in rows:
        marker = "  (default)" if is_default else ""
        label = f"  {name}" if name else ""
        print(f"{venue_id}  {addr}{label}{marker}")


def cmd_update(args) -> None:
    """Update Condor from the remote branch: fetch/compare, pull, uv sync.

    The CLI replacement for the retired bot-admin /update flow (wraps
    utils.updater). Restart `condor serve` afterwards to pick up the code.
    """
    import asyncio

    from utils.updater import check_for_updates, install_dependencies, pull_updates

    async def run() -> None:
        info = await check_for_updates()
        if info["error"]:
            _die(info["error"])
        print(f"branch {info['branch']}: local {info['local_commit']}, "
              f"remote {info['remote_commit']}")
        if info["up_to_date"]:
            print("Already up to date.")
            return
        print(f"{info['commits_behind']} commit(s) behind:\n{info['commit_log']}")
        if args.check_only:
            return
        ok, msg = await pull_updates()
        if not ok:
            _die(msg)
        print(msg)
        ok, msg = await install_dependencies()
        if not ok:
            _die(msg)
        print("Dependencies synced. Restart Condor to apply: "
              "uv run python -m condor.cli serve")

    asyncio.run(run())


def cmd_serve(args) -> None:
    """The ONE process (§9.3): control socket + scheduler + execution runtime
    + web app, in the web app's lifespan. ``--headless`` skips the frontend
    assets. The bind is mechanically loopback-only (§5.5)."""
    import uvicorn

    from condor.web.security import validate_bind_host

    validate_bind_host(args.host)
    if args.headless:
        os.environ["CONDOR_HEADLESS"] = "1"
    uvicorn.run(
        "condor.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )


def cmd_stop(args) -> None:
    """Harness-independent stop — talks to the control socket directly.

    The escape hatch for a wedged chat client (docs/agent-history-comparison.md
    §9.3): when the harness UI hangs, the engine keeps trading; this reaches it
    from any terminal. Takes a run ULID, an agent slug (stops all its live
    runs), or nothing (stops every live run)."""
    import asyncio
    import json as _json

    from condor.agents.runstore import is_run_id
    from condor.control.client import ControlError, call_control

    async def _stop() -> None:
        if args.target and is_run_id(args.target):
            targets = [(args.target, "")]
        else:
            live = await call_control("agent.list")
            targets = [
                (i["agent_id"], i.get("agent_slug", ""))
                for i in live.get("agents", [])
                if not args.target or i.get("agent_slug") == args.target
            ]
            if not targets:
                scope = f" for {args.target!r}" if args.target else ""
                print(f"no live runs{scope}")
                return
        for run_id, slug in targets:
            result = await call_control(
                "agent.verb",
                {"slug": slug, "verb": "stop", "agent_id": run_id,
                 "close": args.close},
                timeout=120,
            )
            print(f"{run_id}: {_json.dumps(result)}")

    try:
        asyncio.run(_stop())
    except ControlError as e:
        raise SystemExit(f"control socket: {e} — is `condor serve` running?")


def main(argv: list[str] | None = None) -> None:
    # The whole app resolves config.yml, .env and store/ relative to the
    # repo root — anchor there no matter where the CLI is invoked from.
    os.chdir(REPO_ROOT)

    parser = argparse.ArgumentParser(prog="condor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="onboarding by harness selection")
    p_init.add_argument("--harness", help=f"comma-separated selection from {list(HARNESSES)}, or 'none'")
    p_init.set_defaults(func=cmd_init)

    p_update = sub.add_parser("update", help="git pull the current branch + uv sync")
    p_update.add_argument(
        "--check-only", action="store_true",
        help="only report whether updates are available; don't pull",
    )
    p_update.set_defaults(func=cmd_update)

    p_serve = sub.add_parser(
        "serve",
        help="run Condor: control socket + scheduler + executor runtime + web app",
    )
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="bind host — loopback only; non-loopback is rejected (§5.5)",
    )
    p_serve.add_argument("--port", type=int, default=3000)
    p_serve.add_argument(
        "--headless", action="store_true",
        help="disable HTTP assets (no dashboard frontend; API + socket only)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_stop = sub.add_parser(
        "stop",
        help="stop live runs directly over the control socket (works even "
        "when the chat harness is wedged)",
    )
    p_stop.add_argument(
        "target", nargs="?", default="",
        help="run ULID or agent slug; omit to stop ALL live runs",
    )
    p_stop.add_argument(
        "--close", action="store_true",
        help="also close the runs' remaining owned inventory (default: detach)",
    )
    p_stop.set_defaults(func=cmd_stop)

    p_accounts = sub.add_parser(
        "accounts",
        help="structured account store (§6.2b): bare verb lists accounts "
        "(last-modified first, redacted)",
    )
    p_accounts.set_defaults(func=cmd_accounts_list)
    accounts_sub = p_accounts.add_subparsers(dest="accounts_command")
    p_import = accounts_sub.add_parser(
        "import-env",
        help="explicit one-shot onboarding from CONDOR_* env credentials",
    )
    p_import.add_argument(
        "--no-probe", action="store_true",
        help="skip the read-only venue probe (offline import)",
    )
    p_import.set_defaults(func=cmd_account_import_env)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
