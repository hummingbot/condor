"""``condor accounts`` — structured account store (§6.2b) lifecycle from the
terminal.

The bare verb lists accounts (redacted, last-modified first — collection-verb
grammar, docs/cli-plan.md principle 7). Actions hang off the same verb:

    accounts add <venue>        onboard (--fields lists what the venue needs;
                                key material via stdin JSON — NEVER argv)
    accounts remove <address>   delete an account (refuses while executors use it)
    accounts default <address>  set the venue default
    accounts import-env         explicit one-shot onboarding from CONDOR_* env vars

This is the highest-stakes dashboard migration: credential entry moves OFF
the no-auth HTTP surface entirely. Sealed store + read-only probe semantics
unchanged.
"""

import getpass
import os
import sys
from typing import Optional

import typer

from condor.cli.commands._common import read_json_object_from_stdin, try_control
from condor.cli.output import (
    ExitCode,
    echo,
    emit,
    fail,
    json_option,
    render_kv,
    render_table,
)

_OPEN_STATUSES = ("PENDING", "ACTIVE", "CLOSING")


def _store():
    from condor.executors.wallets import account_store

    return account_store()


def _list_rows() -> list[dict]:
    """Redacted listing, most recently modified first."""
    data = _store().load()
    rows = [
        {
            "venue": venue_id,
            "address": addr,
            "name": acct.get("name", ""),
            "default": addr == entry.get("default_account"),
            "updated_at": acct.get("updated_at", ""),
        }
        for venue_id, entry in sorted(data.items())
        if not venue_id.startswith("_")
        for addr, acct in entry.get("accounts", {}).items()
    ]
    # Accounts stored before updated_at stamping sort last, in venue order.
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return rows


def _resolve_across_venues(selector: str, venue: Optional[str]) -> tuple[str, str]:
    """(venue_id, custody_address) for an address/name selector, erroring on
    unknown or ambiguous matches."""
    from condor.accounts import AccountResolutionError

    store = _store()
    venues = [venue] if venue else [v for v in store.load() if not v.startswith("_")]
    hits = []
    for venue_id in venues:
        try:
            ref = store.resolve(venue_id, selector)
        except AccountResolutionError:
            continue
        except Exception:
            continue
        hits.append((venue_id, ref.custody_address))
    if not hits:
        scope = f" on {venue}" if venue else ""
        fail(f"no account matches {selector!r}{scope}", ExitCode.NOT_FOUND)
    if len(hits) > 1:
        fail(
            f"{selector!r} matches accounts on {[v for v, _ in hits]} — pass --venue",
            ExitCode.CONFIG_ERROR,
        )
    return hits[0]


def _executors_using(venue_id: str, address: str) -> list[str]:
    """IDs of non-terminal executors holding this account_ref, when the
    server is up to ask (best effort; server down means nothing can use it)."""
    listing = try_control("executor.list", {"limit": 500})
    if not listing:
        return []
    hits = []
    for record in listing.get("executors", []):
        if record.get("status") not in _OPEN_STATUSES:
            continue
        ref = (record.get("config") or {}).get("account_ref") or {}
        if (
            ref.get("venue_id") == venue_id
            and str(ref.get("custody_address", "")).lower() == address.lower()
        ):
            hits.append(record.get("id", "?"))
    return hits


# ── add ──


def _venue_or_fail(venue_id: str):
    from condor.venues.registry import (
        UnknownVenueError,
        load_venue_packages,
        venue_spec,
    )

    try:
        return venue_spec(venue_id)
    except UnknownVenueError:
        known = sorted(load_venue_packages())
        fail(
            f"unknown venue {venue_id!r} (known: {', '.join(known)})",
            ExitCode.CONFIG_ERROR,
        )


def _show_fields(venue_id: str, spec, as_json: bool) -> None:
    rows = [
        {"field": f.name, "secret": f.sealed, "description": f.description}
        for f in spec.credential_fields
    ]
    emit(
        {"venue": venue_id, "fields": rows},
        render_table(
            rows,
            columns=["field", "secret", "description"],
            title=f"credential fields for {venue_id}",
        ),
        as_json,
    )


def _collect_credentials(venue_id: str, spec, keys_stdin: bool) -> dict:
    declared = {f.name: f for f in spec.credential_fields}
    if keys_stdin or not sys.stdin.isatty():
        payload = read_json_object_from_stdin()
        unknown = sorted(set(payload) - set(declared))
        if unknown:
            fail(
                f"unknown credential field(s) for {venue_id}: {', '.join(unknown)} "
                f"(run `condor accounts add {venue_id} --fields`)",
                ExitCode.CONFIG_ERROR,
            )
        return {k: str(v) for k, v in payload.items() if v not in (None, "")}
    # Interactive: hidden prompts for sealed fields; empty input skips a field.
    values: dict = {}
    for field in declared.values():
        prompt = f"{field.name} ({field.description}): "
        raw = getpass.getpass(prompt) if field.sealed else input(prompt)
        if raw.strip():
            values[field.name] = raw.strip()
    return values


def _add(
    venue_id: str,
    name: str,
    keys_stdin: bool,
    no_probe: bool,
    make_default: bool,
    show_fields: bool,
    as_json: bool,
) -> None:
    from condor.accounts import (
        CustodyMismatchError,
        OnboardingError,
        ProbeError,
        onboard_account,
    )

    spec = _venue_or_fail(venue_id)
    if show_fields:
        _show_fields(venue_id, spec, as_json)
        return

    credentials = _collect_credentials(venue_id, spec, keys_stdin)
    if not credentials:
        fail(
            f"no credentials provided (run `condor accounts add {venue_id} --fields` "
            "to see what the venue needs)",
            ExitCode.CONFIG_ERROR,
        )
    try:
        ref = onboard_account(
            venue_id,
            credentials,
            name=name,
            probe=not no_probe,
            make_default=make_default,
        )
    except (OnboardingError, CustodyMismatchError, ProbeError) as e:
        fail(str(e), ExitCode.CONFIG_ERROR)
    record = {
        "venue": ref.venue_id,
        "address": ref.custody_address,
        "name": name,
        "probed": not no_probe,
    }
    emit(record, render_kv(record, title="account onboarded"), as_json)


# ── import-env ──


def _env_credential_plan() -> list[tuple[str, dict]]:
    """(venue_id, credentials) for each venue with legacy env creds present —
    the SAME variables the pre-activation loaders read implicitly."""
    env = os.environ.get
    plan: list[tuple[str, dict]] = []
    if env("CONDOR_HL_AGENT_KEY") or env("CONDOR_HL_ACCOUNT_ADDRESS"):
        venue_id = (
            "hyperliquid-testnet"
            if env("CONDOR_HL_NETWORK") == "testnet"
            else "hyperliquid"
        )
        plan.append(
            (
                venue_id,
                {
                    "agent_private_key": env("CONDOR_HL_AGENT_KEY"),
                    "account_address": env("CONDOR_HL_ACCOUNT_ADDRESS"),
                },
            )
        )
    if env("CONDOR_SOLANA_SECRET_KEY") or env("CONDOR_SOLANA_KEYSTORE"):
        plan.append(
            (
                "solana",
                {
                    "secret_key_b58": env("CONDOR_SOLANA_SECRET_KEY"),
                    "keystore_path": env("CONDOR_SOLANA_KEYSTORE"),
                    "keystore_passphrase": env("CONDOR_SOLANA_PASSPHRASE"),
                    "rpc_url": env("CONDOR_SOLANA_RPC"),
                },
            )
        )
    if env("CONDOR_POLYMARKET_KEY"):
        plan.append(
            (
                "polymarket",
                {
                    "private_key": env("CONDOR_POLYMARKET_KEY"),
                    "funder": env("CONDOR_POLYMARKET_FUNDER"),
                    "signature_type": env("CONDOR_POLYMARKET_SIG_TYPE"),
                    "host": env("CONDOR_POLYMARKET_HOST"),
                    "api_key": env("CONDOR_POLYMARKET_API_KEY"),
                    "api_secret": env("CONDOR_POLYMARKET_API_SECRET"),
                    "api_passphrase": env("CONDOR_POLYMARKET_API_PASSPHRASE"),
                    "relayer_api_key": env("CONDOR_POLYMARKET_RELAYER_API_KEY"),
                    "relayer_address": env("CONDOR_POLYMARKET_RELAYER_ADDRESS"),
                },
            )
        )
    return plan


def _import_env(no_probe: bool) -> None:
    """Explicit one-shot env→store onboarding. There is NO implicit seeding:
    this command is the only path from env credentials into the store."""
    from condor.accounts.onboarding import onboard_account

    probe = not no_probe
    plan = _env_credential_plan()
    jupiter_key = os.environ.get("CONDOR_JUPITER_API_KEY")
    if not plan and not jupiter_key:
        echo(
            "no CONDOR_* venue credentials found in the environment — nothing to import"
        )
        return

    failures: list[str] = []
    for venue_id, credentials in plan:
        try:
            ref = onboard_account(venue_id, credentials, probe=probe)
            echo(
                f"• {venue_id}: account {ref.custody_address} onboarded"
                + ("" if probe else " (probe skipped)")
            )
        except Exception as e:
            failures.append(venue_id)
            typer.echo(f"! {venue_id}: NOT onboarded — {e}", err=True)
    if jupiter_key:
        from condor.executors.wallets import save_service

        save_service("jupiter", {"api_key": jupiter_key})
        echo("• jupiter: data-API key sealed into the _services block")

    if failures:
        fail(f"import-env failed for: {', '.join(failures)}", ExitCode.ERROR)
    if plan:
        echo(
            "Done. The matching CONDOR_* lines can be removed from .env — "
            "loaders read ONLY the store now."
        )


# ── the command ──


def accounts(
    action: Optional[str] = typer.Argument(
        None, help="add | remove | default | import-env; omit to list accounts."
    ),
    target: Optional[str] = typer.Argument(
        None,
        help="Venue id for `add`; custody address (or display name) for "
        "`remove`/`default`.",
    ),
    name: str = typer.Option("", "--name", help="Display name for `add`."),
    venue: Optional[str] = typer.Option(
        None,
        "--venue",
        help="Disambiguate `remove`/`default` when an address "
        "matches on several venues.",
    ),
    show_fields: bool = typer.Option(
        False,
        "--fields",
        help="For `add`: list the venue's credential fields and exit.",
    ),
    keys_stdin: bool = typer.Option(
        False,
        "--keys-stdin",
        help="For `add`: read credentials as a JSON object from stdin (never argv).",
    ),
    no_probe: bool = typer.Option(
        False, "--no-probe", help="Skip the read-only venue probe (offline import)."
    ),
    make_default: bool = typer.Option(
        False, "--make-default", help="For `add`: make this the venue default."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="For `remove`: proceed even if open executors hold the account.",
    ),
    as_json: bool = json_option(),
) -> None:
    """List venue accounts (redacted) or manage them: add/remove/default/import-env."""
    if action is None:
        rows = _list_rows()
        if not rows and not as_json:
            echo(
                "no accounts configured — `condor accounts add <venue>` or "
                "`condor accounts import-env`"
            )
            return
        emit(
            {"accounts": rows},
            render_table(
                rows,
                columns=["venue", "address", "name", "default", "updated_at"],
                title="accounts",
            ),
            as_json,
        )
        return

    if action == "import-env":
        _import_env(no_probe)
        return

    if action == "add":
        if not target:
            fail("usage: condor accounts add <venue>", ExitCode.CONFIG_ERROR)
        _add(target, name, keys_stdin, no_probe, make_default, show_fields, as_json)
        return

    if action in ("remove", "default"):
        if not target:
            fail(f"usage: condor accounts {action} <address>", ExitCode.CONFIG_ERROR)
        venue_id, address = _resolve_across_venues(target, venue)
        if action == "default":
            _store().set_default(venue_id, address)
            record = {"venue": venue_id, "default": address}
            emit(record, render_kv(record, title="default set"), as_json)
            return
        busy = _executors_using(venue_id, address)
        if busy and not force:
            fail(
                f"account {address} on {venue_id} is held by open executor(s) "
                f"{', '.join(busy)} — stop them first or pass --force",
                ExitCode.ERROR,
            )
        if not _store().remove_account(venue_id, address):
            fail(f"no account {address} on {venue_id}", ExitCode.NOT_FOUND)
        record = {"venue": venue_id, "removed": address}
        emit(record, render_kv(record, title="account removed"), as_json)
        return

    fail(
        f"unknown action {action!r} (add | remove | default | import-env)",
        ExitCode.CONFIG_ERROR,
    )
