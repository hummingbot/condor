"""Structured account store (§6.2b) — schema, validation, resolution.

Schema (``store/venues.json`` once ACTIVATED in Phase 3; sealed fields stay
``enc:v1:`` encrypted):

.. code-block:: jsonc

    {
      "hyperliquid": {
        "default_account": "0xabc…",
        "accounts": {
          "0xabc…": { "name": "main", "agent_private_key": "enc:v1:…" },
          "0xdef…": { "name": "alt",  "agent_private_key": "enc:v1:…" }
        }
      }
    }

Rules implemented here (Phase 2 scope — DORMANT, fixture-tested only; the
live system still reads the flat file via ``condor/executors/wallets.py``):

- **No legacy compatibility:** a flat pre-v1 entry (credential fields
  directly on the venue object, no ``accounts`` map) fails validation with
  an "unsupported pre-v1 format" error. No compatibility reader, no
  automatic conversion.
- Accounts are keyed by **normalized custody address** within a registered
  ``venue_id`` — duplicate ``AccountRef``s are structurally impossible.
- **Display names are metadata**, unique within the venue (normalized
  comparison), accepted interchangeably with addresses in selection.
- **Selector resolution:** address or display name → canonical
  ``AccountRef``; no selector → the venue's ``default_account``; a venue
  with no default and no selector fails with a clear error.
- **Single serialized atomic writer** (tmp+rename+fsync, 0700 dir / 0600
  file).
- Top-level keys starting with ``_`` are reserved non-account blocks and are
  skipped by validation — today only ``"_services"`` (service-level API keys
  that are not account credentials, e.g. the Jupiter data-API key, written
  sealed by ``condor.executors.wallets.save_service``).

Phase 3 (ACTIVE): onboarding (custody derivation + read-only probe) lives in
``condor.accounts.onboarding``; the loaders in ``condor/executors/wallets.py``
read THIS store as the sole credential source (env precedence deleted).
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from condor.accounts.model import AccountRef
from condor.accounts.registry import VenueRegistry, default_registry


class AccountStoreError(ValueError):
    """Schema/validation failure."""


class PreV1FormatError(AccountStoreError):
    """A flat pre-v1 venues.json entry was found — unsupported."""


class AccountResolutionError(AccountStoreError):
    """A selector could not be resolved to exactly one account."""


# AccountStore is deliberately cheap to construct.  All instances targeting
# the same path therefore need the same process-wide transaction lock; an
# instance-local lock loses concurrent read/modify/write updates.
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = path.expanduser().resolve()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _default_store_path() -> Path:
    return Path(__file__).resolve().parents[2] / "store" / "venues.json"


class AccountStore:
    """Load/validate/resolve the structured account store."""

    def __init__(
        self,
        path: Path | None = None,
        registry: VenueRegistry | None = None,
    ):
        self._path = path or _default_store_path()
        self._registry = registry or default_registry()
        self._write_lock = _lock_for(self._path)

    # -- loading + validation --

    def load(self) -> dict:
        """Read + validate the store. Missing file → empty store."""
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text() or "{}")
        self.validate(raw)
        return raw

    def validate(self, data: dict) -> None:
        """Validate the full structured schema; raise on any violation."""
        if not isinstance(data, dict):
            raise AccountStoreError("venues.json root must be an object")
        for venue_id, entry in data.items():
            if venue_id.startswith("_"):
                # Reserved non-account block ("_services" et al) — not venue
                # accounts, not schema-validated here. See module docstring.
                if not isinstance(entry, dict):
                    raise AccountStoreError(f"{venue_id}: entry must be an object")
                continue
            venue = self._registry.get(venue_id)  # raises UnknownVenueError
            if not isinstance(entry, dict):
                raise AccountStoreError(f"{venue_id}: entry must be an object")
            if "accounts" not in entry:
                raise PreV1FormatError(
                    f"{venue_id}: unsupported pre-v1 format — flat venue "
                    "entries (credential fields with no 'accounts' map) are "
                    "not readable. Re-onboard the account via the dashboard "
                    "or `condor accounts import-env`."
                )
            accounts = entry["accounts"]
            if not isinstance(accounts, dict):
                raise AccountStoreError(f"{venue_id}: 'accounts' must be a map")
            unknown = set(entry) - {"default_account", "accounts"}
            if unknown:
                raise AccountStoreError(
                    f"{venue_id}: unknown venue-level fields {sorted(unknown)} "
                    "(credentials live per-account under 'accounts')"
                )
            seen_names: dict[str, str] = {}
            for addr, acct in accounts.items():
                canonical = self._registry.account_ref(venue_id, addr)
                if canonical.custody_address != addr:
                    raise AccountStoreError(
                        f"{venue_id}: account key {addr!r} is not the "
                        f"canonical custody address "
                        f"({canonical.custody_address!r}) — keys must be "
                        "stored normalized"
                    )
                if not isinstance(acct, dict):
                    raise AccountStoreError(
                        f"{venue_id}/{addr}: account must be an object"
                    )
                name = acct.get("name", "")
                if name:
                    norm = name.strip().lower()
                    if norm in seen_names:
                        raise AccountStoreError(
                            f"{venue_id}: duplicate display name {name!r} "
                            f"({seen_names[norm]} vs {addr}) — names must be "
                            "unique within a venue"
                        )
                    seen_names[norm] = addr
            default = entry.get("default_account")
            if default is not None and default not in accounts:
                raise AccountStoreError(
                    f"{venue_id}: default_account {default!r} is not a "
                    "configured account"
                )
            del venue  # only needed for registration check

    # -- resolution --

    def resolve(self, venue_id: str, selector: str | None = None) -> AccountRef:
        """Resolve an account selector (address or display name) to the
        canonical AccountRef. ``None`` → the venue's ``default_account``."""
        return self._resolve_in_data(self.load(), venue_id, selector)

    def _resolve_in_data(
        self, data: dict, venue_id: str, selector: str | None = None
    ) -> AccountRef:
        """Resolve against an already-loaded transaction snapshot."""
        entry = data.get(venue_id)
        if entry is None:
            raise AccountResolutionError(
                f"no accounts configured for venue {venue_id!r}"
            )
        accounts: dict = entry["accounts"]

        if selector is None or not str(selector).strip():
            default = entry.get("default_account")
            if not default:
                raise AccountResolutionError(
                    f"{venue_id}: no account selector given and the venue "
                    "has no default_account — pass `account:` in the spec "
                    "or set a default"
                )
            return AccountRef(venue_id=venue_id, custody_address=default)

        selector = str(selector).strip()

        # Try as address first (normalized), then as display name.
        try:
            ref = self._registry.account_ref(venue_id, selector)
            if ref.custody_address in accounts:
                return ref
        except ValueError:
            pass  # not address-shaped — try the name path

        norm = selector.lower()
        matches = [
            addr
            for addr, acct in accounts.items()
            if (acct.get("name", "") or "").strip().lower() == norm
        ]
        if len(matches) == 1:
            return AccountRef(venue_id=venue_id, custody_address=matches[0])
        if len(matches) > 1:
            # Structurally prevented by validate(); belt-and-braces.
            raise AccountResolutionError(
                f"{venue_id}: display name {selector!r} is ambiguous"
            )
        raise AccountResolutionError(
            f"{venue_id}: no account matches selector {selector!r} "
            f"(configured: {[a.get('name') or addr for addr, a in accounts.items()]})"
        )

    # -- writing (serialized + atomic) --

    def _write_locked(self, data: dict) -> None:
        """Persist a validated snapshot while ``_write_lock`` is held."""
        self.validate(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        tmp = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
            try:
                dir_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                # Some filesystems do not support directory fsync. The file
                # itself is still fsynced and atomically replaced.
                pass
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def save(self, data: dict) -> None:
        """Validate + atomically persist (tmp+rename+fsync, 0700/0600)."""
        with self._write_lock:
            self._write_locked(data)

    @contextmanager
    def transaction(self) -> Iterator[dict]:
        """Hold the shared lock across a complete read/modify/write.

        Normal exit commits atomically; an exception aborts. Executor create
        and account edits use this same guard so credentials cannot change
        between account resolution and durable creation.
        """
        with self._write_lock:
            data = self.load()
            yield data
            self._write_locked(data)

    @contextmanager
    def locked_snapshot(self) -> Iterator[dict]:
        """Yield a validated snapshot while holding the shared path lock.

        This is the account/executor transaction guard for operations that do
        not themselves mutate venues.json (credential binding + durable
        executor opener, and exact-account busy checks).
        """
        with self._write_lock:
            yield self.load()

    def upsert_account(
        self,
        venue_id: str,
        custody_address: str,
        fields: dict,
        *,
        make_default: bool = False,
        guard: Callable[[AccountRef, dict], None] | None = None,
    ) -> AccountRef:
        """Add or update one account entry (validated, atomic).

        Phase 3's onboarding path (custody derivation + venue probe) calls
        this AFTER validation; the store itself only enforces schema rules.
        """
        ref = self._registry.account_ref(venue_id, custody_address)
        stored = dict(fields)
        stored["updated_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        with self.transaction() as data:
            if guard is not None:
                guard(ref, data)
            entry = data.setdefault(venue_id, {"accounts": {}})
            entry.setdefault("accounts", {})
            entry["accounts"][ref.custody_address] = stored
            if make_default or "default_account" not in entry:
                entry["default_account"] = ref.custody_address
        return ref

    def account_fields(self, ref: AccountRef) -> dict:
        """The raw stored fields (sealed secrets untouched) for one account."""
        data = self.load()
        try:
            return dict(data[ref.venue_id]["accounts"][ref.custody_address])
        except KeyError:
            raise AccountResolutionError(
                f"{ref.venue_id}: no account {ref.custody_address!r}"
            ) from None

    def set_default(self, venue_id: str, selector: str) -> AccountRef:
        """Point the venue's ``default_account`` at the resolved selector."""
        with self.transaction() as data:
            ref = self._resolve_in_data(data, venue_id, selector)
            data[venue_id]["default_account"] = ref.custody_address
        return ref

    def remove_account(self, venue_id: str, custody_address: str) -> bool:
        """Remove one account entry. Removing the default just leaves the
        venue with no default. Returns True if the account existed."""
        ref = self._registry.account_ref(venue_id, custody_address)
        with self.transaction() as data:
            entry = data.get(venue_id)
            if not entry or ref.custody_address not in entry.get("accounts", {}):
                return False
            del entry["accounts"][ref.custody_address]
            if entry.get("default_account") == ref.custody_address:
                entry.pop("default_account", None)
            if not entry["accounts"]:
                del data[venue_id]
        return True
