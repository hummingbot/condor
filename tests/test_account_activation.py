"""Phase 3 acceptance: account-store ACTIVATION (§6.2b, §12 operator cutover).

Onboarding derives custody FROM credentials (typed addresses are verified,
never trusted), the read-only probe gates enabling, loaders read ONLY the
structured store (env precedence deleted — not inverted), the flat pre-v1
file fails with the clear re-onboard error, and `condor accounts import-env`
is the explicit, idempotent env→store path. No network: probes are injected.
"""

import base64
import json

import pytest
from eth_account import Account
from solders.keypair import Keypair

import condor.cli as cli
import condor.executors.wallets as wallets
from condor.accounts import (
    AccountResolutionError,
    AccountStoreError,
    CustodyMismatchError,
    OnboardingError,
    PreV1FormatError,
    ProbeError,
    onboard_account,
    onboarding,
)

_KEY = base64.b64encode(b"t" * 32).decode()
HL_KEY = "0x" + "11" * 32
HL_ACCT = "0x" + "ab" * 20
HL_ACCT_2 = "0x" + "cd" * 20
PM_KEY = "0x" + "22" * 32
PM_SIGNER = Account.from_key(PM_KEY).address.lower()

_ENV_VARS = (
    "CONDOR_HL_AGENT_KEY",
    "CONDOR_HL_ACCOUNT_ADDRESS",
    "CONDOR_HL_NETWORK",
    "CONDOR_SOLANA_SECRET_KEY",
    "CONDOR_SOLANA_KEYSTORE",
    "CONDOR_SOLANA_PASSPHRASE",
    "CONDOR_SOLANA_RPC",
    "CONDOR_POLYMARKET_KEY",
    "CONDOR_POLYMARKET_FUNDER",
    "CONDOR_POLYMARKET_SIG_TYPE",
    "CONDOR_POLYMARKET_HOST",
    "CONDOR_POLYMARKET_API_KEY",
    "CONDOR_POLYMARKET_API_SECRET",
    "CONDOR_POLYMARKET_API_PASSPHRASE",
    "CONDOR_POLYMARKET_RELAYER_API_KEY",
    "CONDOR_POLYMARKET_RELAYER_ADDRESS",
    "CONDOR_JUPITER_API_KEY",
)


def _noop_probe(venue_id, ref, credentials):
    return None


@pytest.fixture()
def store_path(tmp_path, monkeypatch):
    """Sealed tmp store; the live store/venues.json is never touched."""
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "venues.json"


# -- custody derivation ----------------------------------------------------------


def test_onboard_solana_derives_custody_and_seals(store_path):
    kp = Keypair()
    ref = onboard_account(
        "solana", {"secret_key_b58": str(kp)}, name="hot", prober=_noop_probe
    )
    assert ref.custody_address == str(kp.pubkey())
    raw = json.loads(store_path.read_text())["solana"]["accounts"][str(kp.pubkey())]
    assert raw["secret_key_b58"].startswith("enc:v1:")  # sealed at rest
    assert raw["name"] == "hot"
    # loader round-trips through the store and re-verifies custody
    assert wallets.load_solana_keypair().pubkey() == kp.pubkey()
    assert wallets.load_solana_keypair("hot").pubkey() == kp.pubkey()


def test_onboard_rejects_mismatched_typed_address(store_path):
    kp, other = Keypair(), Keypair()
    with pytest.raises(CustodyMismatchError, match="never typed"):
        onboard_account(
            "solana",
            {"secret_key_b58": str(kp), "address": str(other.pubkey())},
            prober=_noop_probe,
        )
    assert not store_path.exists()  # nothing persisted


def test_onboard_hyperliquid_normalizes_and_validates(store_path):
    ref = onboard_account(
        "hyperliquid",
        {
            "agent_private_key": HL_KEY,
            "account_address": HL_ACCT.upper().replace("0X", "0x"),
        },
        name="main",
        prober=_noop_probe,
    )
    assert ref.custody_address == HL_ACCT  # normalized lowercase
    creds = wallets.load_hyperliquid_creds()
    assert creds == {
        "agent_private_key": HL_KEY,
        "account_address": HL_ACCT,
        "network": "mainnet",  # derived from venue_id, never stored
    }
    with pytest.raises(OnboardingError, match="does not parse"):
        onboard_account(
            "hyperliquid",
            {"agent_private_key": "0xnope", "account_address": HL_ACCT},
            prober=_noop_probe,
        )
    with pytest.raises(OnboardingError, match="not an EVM address"):
        onboard_account(
            "hyperliquid",
            {"agent_private_key": HL_KEY, "account_address": "banana"},
            prober=_noop_probe,
        )


def test_onboard_polymarket_custody_signer_vs_funder(store_path):
    funder = "0x" + "ef" * 20
    # signature_type 1/2: custody = the funder/proxy, not the signer
    ref = onboard_account(
        "polymarket",
        {"private_key": PM_KEY, "signature_type": 1, "funder": funder},
        prober=_noop_probe,
    )
    assert ref.custody_address == funder
    # signature_type 0: custody = signer; a different funder is a rebind → rejected
    with pytest.raises(CustodyMismatchError):
        onboard_account(
            "polymarket",
            {"private_key": PM_KEY, "signature_type": 0, "funder": funder},
            prober=_noop_probe,
        )
    wallets.account_store().remove_account("polymarket", funder)
    ref = onboard_account("polymarket", {"private_key": PM_KEY}, prober=_noop_probe)
    assert ref.custody_address == PM_SIGNER
    creds = wallets.load_polymarket_creds()
    assert creds["private_key"] == PM_KEY and creds["signature_type"] == 0


# -- probe gating ----------------------------------------------------------------


def test_probe_failure_refuses_to_enable(store_path, monkeypatch):
    def _boom(venue_id, ref, credentials):
        raise RuntimeError("venue unreachable")

    monkeypatch.setitem(onboarding._PROBERS, "hyperliquid", _boom)
    with pytest.raises(ProbeError, match="NOT enabled"):
        onboard_account(
            "hyperliquid",
            {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        )
    assert not store_path.exists()
    # and the loader confirms nothing became selectable
    with pytest.raises(AccountResolutionError):
        wallets.load_hyperliquid_creds()


def test_probe_skippable_only_explicitly(store_path):
    ref = onboard_account(
        "hyperliquid",
        {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        probe=False,
    )
    assert ref.custody_address == HL_ACCT


# -- display names ---------------------------------------------------------------


def test_duplicate_display_name_rejected(store_path):
    onboard_account(
        "hyperliquid",
        {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        name="Main",
        prober=_noop_probe,
    )
    with pytest.raises(AccountStoreError, match="duplicate display name"):
        onboard_account(
            "hyperliquid",
            {"agent_private_key": HL_KEY, "account_address": HL_ACCT_2},
            name="main",  # normalized comparison
            prober=_noop_probe,
        )


def test_account_selector_picks_named_account(store_path):
    onboard_account(
        "hyperliquid",
        {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        name="main",
        prober=_noop_probe,
    )
    onboard_account(
        "hyperliquid",
        {"agent_private_key": HL_KEY, "account_address": HL_ACCT_2},
        name="alt",
        prober=_noop_probe,
    )
    assert wallets.load_hyperliquid_creds("alt")["account_address"] == HL_ACCT_2
    assert wallets.load_hyperliquid_creds(HL_ACCT_2)["account_address"] == HL_ACCT_2
    assert wallets.load_hyperliquid_creds()["account_address"] == HL_ACCT  # default


# -- env precedence is DELETED -----------------------------------------------------


def test_env_vars_alone_no_longer_trade(store_path, monkeypatch):
    """The old env-over-config precedence is deleted, not inverted: with only
    env credentials and an empty store, the loader fails with the clear
    import-env pointer."""
    monkeypatch.setenv("CONDOR_HL_AGENT_KEY", HL_KEY)
    monkeypatch.setenv("CONDOR_HL_ACCOUNT_ADDRESS", HL_ACCT)
    with pytest.raises(AccountResolutionError, match="import-env"):
        wallets.load_hyperliquid_creds()


def test_flat_pre_v1_store_raises_from_loader(store_path):
    store_path.write_text(
        json.dumps(
            {
                "hyperliquid": {
                    "agent_private_key": "enc:v1:X",
                    "account_address": HL_ACCT,
                }
            }
        )
    )
    with pytest.raises(PreV1FormatError, match="unsupported pre-v1 format"):
        wallets.load_hyperliquid_creds()
    with pytest.raises(PreV1FormatError):
        wallets.venue_config("jupiter")  # even the services shim refuses it


# -- CLI: explicit import-env -------------------------------------------------------


def test_import_env_creates_accounts_explicitly(store_path, monkeypatch, capsys):
    kp = Keypair()
    monkeypatch.setenv("CONDOR_HL_AGENT_KEY", HL_KEY)
    monkeypatch.setenv("CONDOR_HL_ACCOUNT_ADDRESS", HL_ACCT)
    monkeypatch.setenv("CONDOR_SOLANA_SECRET_KEY", str(kp))
    monkeypatch.setenv("CONDOR_SOLANA_RPC", "https://rpc.example")
    monkeypatch.setenv("CONDOR_JUPITER_API_KEY", "jup_x")

    cli.main(["accounts", "import-env", "--no-probe"])
    out = capsys.readouterr().out
    assert HL_ACCT in out and str(kp.pubkey()) in out

    # loaders (which never read env) now work from the store
    assert wallets.load_hyperliquid_creds()["agent_private_key"] == HL_KEY
    assert wallets.load_solana_keypair().pubkey() == kp.pubkey()
    assert wallets.solana_rpc_url() == "https://rpc.example"
    assert wallets.venue_config("jupiter")["api_key"] == "jup_x"
    raw = json.loads(store_path.read_text())
    assert raw["_services"]["jupiter"]["api_key"].startswith("enc:v1:")

    # idempotent: address-keyed upsert, never a duplicate
    cli.main(["accounts", "import-env", "--no-probe"])
    raw = json.loads(store_path.read_text())
    assert list(raw["hyperliquid"]["accounts"]) == [HL_ACCT]
    assert list(raw["solana"]["accounts"]) == [str(kp.pubkey())]


def test_import_env_probe_failure_is_loud(store_path, monkeypatch):
    monkeypatch.setenv("CONDOR_HL_AGENT_KEY", HL_KEY)
    monkeypatch.setenv("CONDOR_HL_ACCOUNT_ADDRESS", HL_ACCT)
    monkeypatch.setitem(
        onboarding._PROBERS,
        "hyperliquid",
        lambda *a: (_ for _ in ()).throw(RuntimeError("down")),
    )
    with pytest.raises(SystemExit):
        cli.main(["accounts", "import-env"])
    assert not store_path.exists()


def test_import_env_nothing_to_import(store_path, capsys):
    cli.main(["accounts", "import-env"])
    assert "nothing to import" in capsys.readouterr().out


def test_account_list_redacted(store_path, capsys):
    onboard_account(
        "hyperliquid",
        {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        name="main",
        prober=_noop_probe,
    )
    cli.main(["accounts"])
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if HL_ACCT in ln)
    assert "hyperliquid" in line and "main" in line and "yes" in line  # default marker
    assert HL_KEY not in out  # never the secret
    assert "enc:v1:" not in out


def test_account_list_orders_by_last_modified(store_path, capsys):
    onboard_account(
        "hyperliquid",
        {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        name="hl",
        prober=_noop_probe,
    )
    kp = Keypair()
    sol = onboard_account(
        "solana",
        {"secret_key_b58": str(kp)},
        name="sol",
        prober=_noop_probe,
    )
    store = wallets.account_store()
    with store.transaction() as data:
        data["hyperliquid"]["accounts"][HL_ACCT][
            "updated_at"
        ] = "2026-01-01T00:00:00+00:00"
        data["solana"]["accounts"][sol.custody_address][
            "updated_at"
        ] = "2026-07-01T00:00:00+00:00"
    cli.main(["accounts"])
    out = capsys.readouterr().out
    assert out.index("solana") < out.index("hyperliquid")  # newest first
