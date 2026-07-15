"""Condor command-line entry point (`python -m condor.cli`, or `make init`).

Subcommands:

    init         identity anchor + hummingbot-api probe + harness
                 multi-select; emits per-harness config. Idempotent —
                 re-run any time to add a harness or the Telegram token.
    login-token  mint a short-lived web login URL from the terminal
                 (same trust model as Tier-A auto-bind: whoever runs
                 commands on this box already owns config.yml and the
                 JWT signing secret).
    account      structured account store (§6.2b) management:
                   import-env  EXPLICIT one-shot onboarding from the legacy
                               CONDOR_* env vars (custody derived from the
                               credentials, read-only probe unless
                               --no-probe, sealed at rest). Idempotent —
                               accounts are keyed by custody address. The
                               loaders themselves never read env vars.
                   list        redacted listing (venue, custody address,
                               display name, default marker).

Product questions live here, in Python, re-runnable forever — the bash
installer (install.sh) only bootstraps and then hands off to `init`.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

HARNESSES = ("claude-code", "openclaw", "hermes", "condor")
API_INSTALL_CMD = (
    "curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh"
    " | bash -s -- --hummingbot-api"
)


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


def _ask_yes_no(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


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


def _probe_http(url: str, timeout: float = 5.0) -> bool:
    """True when *anything* HTTP answers at url (401/404 counts: the
    service is up; credentials are a /servers concern, not init's)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _validate_telegram_token(token: str) -> str:
    """Return the bot username for a valid token; die loudly otherwise."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        _die(f"Telegram rejected the bot token (HTTP {exc.code}). Check it in @BotFather.")
    except Exception as exc:
        _die(f"Could not reach api.telegram.org to validate the token: {exc}")
    if not data.get("ok"):
        _die(f"Telegram rejected the bot token: {data}")
    return data["result"].get("username", "")


# ── init steps ──


def _step_identity(args) -> int:
    """Anchor the sole approved user (or admin of a multi-user install)."""
    env = _read_env_file()
    existing = env.get("ADMIN_USER_ID", "")
    if args.user_id:
        user_id = args.user_id
    elif existing:
        user_id = int(existing)
        print(f"• Identity: using existing admin user id {user_id} (.env)")
    elif not _interactive():
        _die(
            "no ADMIN_USER_ID in .env and no --user-id given; "
            "non-interactive init needs --user-id"
        )
    elif _ask_yes_no("Do you use Telegram?", default=True):
        print("  Get your numeric id from https://t.me/userinfobot (send /start).")
        raw = _ask("Your Telegram user id")
        if not raw.isdigit():
            _die(f"a Telegram user id is numeric, got {raw!r}")
        user_id = int(raw)
    else:
        user_id = 1_000_000 + secrets.randbelow(2_000_000_000)
        print(
            f"• Minted user id {user_id} (an identifier, not a credential).\n"
            "  NOTE: enabling the Telegram surface later requires the id to BE\n"
            "  your Telegram id — that is an .env + config.yml edit, so decide\n"
            "  early if you can."
        )

    _write_env_var("ADMIN_USER_ID", str(user_id))
    os.environ["ADMIN_USER_ID"] = str(user_id)

    # Materialize config.yml with this admin (creates it on first run).
    from config_manager import get_config_manager

    get_config_manager()

    # Multi-user: extra approved users disable Tier-A auto-bind by design.
    extra = args.extra_users or ""
    if not extra and _interactive() and not args.harness:
        if not _ask_yes_no("Single-user install?", default=True):
            extra = _ask("Additional approved Telegram user ids (comma-separated)")
    if extra:
        cm = get_config_manager()
        ids = [int(x) for x in extra.replace(" ", "").split(",") if x]
        for uid in ids:
            cm.register_pending(uid)
            cm.approve_user(uid, user_id)
        print(
            f"• Approved {len(ids)} extra user(s). Tier-A auto-bind is now OFF\n"
            "  (it needs exactly one approved user): each user's MCP server\n"
            "  registration must pass explicit identity, e.g.\n"
            "    env CONDOR_USER_ID=<id> uv run python -m mcp_servers.condor"
        )
    return user_id


def _step_api(args, user_id: int) -> None:
    """Register the same-box hummingbot-api and probe it, loudly."""
    from config_manager import get_config_manager

    cm = get_config_manager()
    if cm.get_server("local") is None:
        # admin/admin are hummingbot-api's own install defaults; change
        # via /servers (Telegram) or the dashboard if you hardened them.
        cm.add_server("local", "localhost", 8000, "admin", "admin", owner_id=user_id)
        if not cm.get_default_server():
            cm.set_default_server("local")
        print("• Registered hummingbot-api server 'local' (localhost:8000)")

    api_url = args.api_url or "http://localhost:8000"
    if _probe_http(api_url):
        print(f"• hummingbot-api reachable at {api_url}")
    else:
        print(
            f"\n!! hummingbot-api is NOT reachable at {api_url}.\n"
            "!! Nothing can trade until it is running. Install/start it with:\n"
            f"!!   {API_INSTALL_CMD}\n",
            file=sys.stderr,
        )


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


def _emit_condor(args) -> None:
    from utils.config import WEB_URL

    print("\n── Condor harness (Telegram + web) ──")
    env = _read_env_file()
    token = args.telegram_token or env.get("TELEGRAM_TOKEN", "")
    if args.telegram_token:
        username = _validate_telegram_token(token)
        _write_env_var("TELEGRAM_TOKEN", token)
        print(f"  Telegram bot @{username} configured.")
    elif token:
        print("  Telegram: token already in .env.")
    elif _interactive() and _ask_yes_no(
        "Set up the Telegram surface now (needs a bot token from @BotFather)?",
        default=False,
    ):
        print("  Create a bot with https://t.me/BotFather (/newbot), paste the token.")
        token = _ask("Bot token")
        username = _validate_telegram_token(token)
        _write_env_var("TELEGRAM_TOKEN", token)
        print(f"  Telegram bot @{username} configured.")
    else:
        print("  Telegram: skipped — the web surface works without it; re-run init to add.")
    print(
        f"  Start Condor:  make run   (tmux session 'condor')\n"
        f"  Web chat:      {WEB_URL}/login — mint a login URL with:\n"
        "                 uv run python -m condor.cli login-token"
    )


def cmd_init(args) -> None:
    print(f"Condor init — {REPO_ROOT}\n")
    user_id = _step_identity(args)
    _step_api(args, user_id)
    selected = _select_harnesses(args)

    emitters = {
        "claude-code": _emit_claude_code,
        "openclaw": _emit_openclaw,
        "hermes": _emit_hermes,
        "condor": lambda: _emit_condor(args),
    }
    for harness in selected:
        emitters[harness]()
    if not selected:
        print("\nNo harness selected: Tier 2 + monitoring dashboard only.")
        print("Mint a dashboard login with: uv run python -m condor.cli login-token")

    print("\nDone. `init` is idempotent — re-run it any time to add a harness.")


def cmd_login_token(args) -> None:
    from config_manager import get_config_manager

    cm = get_config_manager()
    user_id = args.user_id
    if not user_id:
        approved = cm.get_approved_users()
        if len(approved) != 1:
            _die(
                f"{len(approved)} approved users in config.yml — pass --user-id "
                "(implicit identity needs exactly one, like Tier-A auto-bind)"
            )
        user_id = approved[0]

    from condor.web.auth import create_cli_login_token
    from utils.config import WEB_URL

    token = create_cli_login_token(user_id)
    print(f"{WEB_URL}/login?token={token}")
    print("Valid for 5 minutes. Requires the Condor web server to be running.")


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


def cmd_account_list(args) -> None:
    """Redacted listing: venue, custody address, name, default marker."""
    from condor.executors.wallets import account_store

    data = account_store().load()
    rows = [
        (venue_id, addr, acct.get("name", ""), addr == entry.get("default_account"))
        for venue_id, entry in sorted(data.items())
        if not venue_id.startswith("_")
        for addr, acct in entry.get("accounts", {}).items()
    ]
    if not rows:
        print("no accounts configured — onboard via the dashboard or "
              "`python -m condor.cli account import-env`")
        return
    for venue_id, addr, name, is_default in rows:
        marker = "  (default)" if is_default else ""
        label = f"  {name}" if name else ""
        print(f"{venue_id}  {addr}{label}{marker}")


def main(argv: list[str] | None = None) -> None:
    # The whole app resolves config.yml, .env and store/ relative to the
    # repo root — anchor there no matter where the CLI is invoked from.
    os.chdir(REPO_ROOT)

    parser = argparse.ArgumentParser(prog="condor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="onboarding by harness selection")
    p_init.add_argument("--user-id", type=int, help="approved user id (skips the identity question)")
    p_init.add_argument("--extra-users", help="comma-separated additional approved user ids")
    p_init.add_argument("--harness", help=f"comma-separated selection from {list(HARNESSES)}, or 'none'")
    p_init.add_argument("--telegram-token", help="bot token for the Condor Telegram surface")
    p_init.add_argument("--api-url", help="hummingbot-api URL to probe (default http://localhost:8000)")
    p_init.set_defaults(func=cmd_init)

    p_token = sub.add_parser("login-token", help="mint a web dashboard login URL")
    p_token.add_argument("--user-id", type=int, help="user to log in as (default: sole approved user)")
    p_token.set_defaults(func=cmd_login_token)

    p_account = sub.add_parser("account", help="structured account store management (§6.2b)")
    account_sub = p_account.add_subparsers(dest="account_command", required=True)
    p_import = account_sub.add_parser(
        "import-env",
        help="explicit one-shot onboarding from CONDOR_* env credentials",
    )
    p_import.add_argument(
        "--no-probe", action="store_true",
        help="skip the read-only venue probe (offline import)",
    )
    p_import.set_defaults(func=cmd_account_import_env)
    p_list = account_sub.add_parser("list", help="redacted account listing")
    p_list.set_defaults(func=cmd_account_list)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
