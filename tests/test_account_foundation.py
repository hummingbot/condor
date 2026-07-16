"""Phase 2 acceptance: dormant account foundation (§6.2b phasing, §11).

The structured store validates and rejects flat pre-v1 FIXTURES, selector and
default_account resolution produce the canonical AccountRef, duplicate
custody keys are structurally impossible, and the writer is serialized +
atomic — all against fixtures. The LIVE system still boots and trades on the
flat ``venues.json`` path (nothing here touches it)."""

import json
import stat

import pytest

from condor.accounts import (
    AccountRef,
    AccountResolutionError,
    AccountStore,
    AccountStoreError,
    PreV1FormatError,
    default_registry,
)
from condor.accounts.registry import UnknownVenueError

ADDR_A = "0x" + "a" * 40
ADDR_B = "0x" + "b" * 40
SOL_ADDR = "7fEsqLYz3Zr7SNoBn9GnCTHU5V2Ta8DFXECfWQEmYVWk"


def _store(tmp_path, data=None):
    store = AccountStore(path=tmp_path / "venues.json")
    if data is not None:
        store.save(data)
    return store


def _structured(default=ADDR_A):
    return {
        "hyperliquid": {
            "default_account": default,
            "accounts": {
                ADDR_A: {"name": "main", "agent_private_key": "enc:v1:AAAA"},
                ADDR_B: {"name": "alt", "agent_private_key": "enc:v1:BBBB"},
            },
        }
    }


# -- schema validation --


def test_structured_schema_validates(tmp_path):
    store = _store(tmp_path, _structured())
    assert store.load()["hyperliquid"]["default_account"] == ADDR_A


def test_flat_pre_v1_fixture_rejected(tmp_path):
    """A flat pre-v1 entry (credentials directly on the venue) fails with the
    'unsupported pre-v1 format' error — never auto-converted."""
    store = _store(tmp_path)
    flat = {"hyperliquid": {"agent_private_key": "enc:v1:X", "account_address": ADDR_A}}
    (tmp_path / "venues.json").write_text(json.dumps(flat))
    with pytest.raises(PreV1FormatError, match="unsupported pre-v1 format"):
        store.load()


def test_unregistered_venue_id_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(UnknownVenueError, match="unregistered venue_id"):
        store.validate({"binance": {"accounts": {}}})


def test_non_canonical_account_key_rejected(tmp_path):
    """Keys must be stored normalized (EVM lowercased) — a checksummed key is
    a schema error, not silently accepted as a second identity."""
    store = _store(tmp_path)
    mixed = ADDR_A.replace("0xaaaa", "0xAAaa")
    with pytest.raises(AccountStoreError, match="canonical custody address"):
        store.validate({"hyperliquid": {"accounts": {mixed: {"name": "x"}}}})


def test_duplicate_display_name_rejected(tmp_path):
    store = _store(tmp_path)
    dup = {
        "hyperliquid": {
            "accounts": {
                ADDR_A: {"name": "main"},
                ADDR_B: {"name": "MAIN"},  # normalized comparison
            }
        }
    }
    with pytest.raises(AccountStoreError, match="duplicate display name"):
        store.validate(dup)


def test_default_account_must_exist(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(AccountStoreError, match="default_account"):
        store.validate(
            {"hyperliquid": {"default_account": ADDR_B, "accounts": {ADDR_A: {}}}}
        )


# -- resolution --


def test_resolve_by_name_and_address_and_default(tmp_path):
    store = _store(tmp_path, _structured())
    expected_alt = AccountRef("hyperliquid", ADDR_B)
    assert store.resolve("hyperliquid", "alt") == expected_alt
    assert store.resolve("hyperliquid", ADDR_B) == expected_alt
    # Mixed-case EVM address input normalizes to the canonical key
    assert (
        store.resolve("hyperliquid", ADDR_B.upper().replace("0X", "0x")) == expected_alt
    )
    # No selector → default_account
    assert store.resolve("hyperliquid") == AccountRef("hyperliquid", ADDR_A)


def test_resolve_no_default_fails_clearly(tmp_path):
    data = _structured()
    del data["hyperliquid"]["default_account"]
    store = _store(tmp_path, data)
    with pytest.raises(AccountResolutionError, match="no default_account"):
        store.resolve("hyperliquid")


def test_resolve_unknown_selector_fails(tmp_path):
    store = _store(tmp_path, _structured())
    with pytest.raises(AccountResolutionError, match="no account matches"):
        store.resolve("hyperliquid", "nope")


def test_solana_addresses_stay_case_sensitive(tmp_path):
    store = _store(
        tmp_path,
        {"solana": {"accounts": {SOL_ADDR: {"name": "hot"}}}},
    )
    assert store.resolve("solana", SOL_ADDR).custody_address == SOL_ADDR
    with pytest.raises(AccountResolutionError):
        store.resolve("solana", SOL_ADDR.lower())


# -- structural uniqueness + writer --


def test_duplicate_custody_structurally_impossible(tmp_path):
    """Accounts are a map keyed by custody address: upserting the same address
    twice (any case) yields ONE entry, never a duplicate AccountRef."""
    store = _store(tmp_path, _structured())
    store.upsert_account(
        "hyperliquid", ADDR_A.upper().replace("0X", "0x"), {"name": "main2"}
    )
    data = store.load()
    assert list(data["hyperliquid"]["accounts"]).count(ADDR_A) == 1
    assert data["hyperliquid"]["accounts"][ADDR_A]["name"] == "main2"


def test_writer_atomic_permissions(tmp_path):
    store = _store(tmp_path, _structured())
    mode = stat.S_IMODE((tmp_path / "venues.json").stat().st_mode)
    assert mode == 0o600
    assert not (tmp_path / "venues.json.tmp").exists()


def test_remove_default_leaves_venue_without_default(tmp_path):
    store = _store(tmp_path, _structured())
    assert store.remove_account("hyperliquid", ADDR_A)
    data = store.load()
    assert "default_account" not in data["hyperliquid"]
    with pytest.raises(AccountResolutionError):
        store.resolve("hyperliquid")


def test_registry_account_ref_normalizes():
    reg = default_registry()
    ref = reg.account_ref("hyperliquid", ADDR_A.upper().replace("0X", "0x"))
    assert ref == AccountRef("hyperliquid", ADDR_A)
    with pytest.raises(UnknownVenueError):
        reg.account_ref("hyperliquid-devnet", ADDR_A)


def test_store_activated_as_loader_source():
    """Phase 3 ACTIVATION: the wallets loaders resolve credentials through
    this store (one path knob, ``wallets._VENUES_PATH``); the flat reader is
    gone."""
    import condor.executors.wallets as wallets
    from condor.accounts import store as store_mod

    assert store_mod._default_store_path().name == "venues.json"
    assert wallets.account_store()._path == wallets._VENUES_PATH
    assert not hasattr(wallets, "save_venue")  # flat writer deleted
    assert not hasattr(wallets, "import_env_to_store")  # replaced by the CLI


def test_concurrent_store_instances_do_not_lose_updates(tmp_path):
    import threading

    path = tmp_path / "venues.json"
    first = AccountStore(path=path)
    second = AccountStore(path=path)
    barrier = threading.Barrier(2)

    def write(store, address, name):
        barrier.wait()
        store.upsert_account("hyperliquid", address, {"name": name})

    t1 = threading.Thread(target=write, args=(first, ADDR_A, "main"))
    t2 = threading.Thread(target=write, args=(second, ADDR_B, "alt"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert set(first.load()["hyperliquid"]["accounts"]) == {ADDR_A, ADDR_B}
