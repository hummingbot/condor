"""Venue credential loading — keypair parsing, Gateway keystore decrypt, and
the ACTIVATED structured-store loaders (§6.2b): store-only resolution (env
precedence deleted), sealed-field decryption, custody verification, and the
``_services`` shim for non-account config (Jupiter data-API key)."""

import base64
import json
from pathlib import Path

import pytest
from solders.keypair import Keypair

import condor.executors.wallets as wallets
from condor.accounts import (
    AccountResolutionError,
    PreV1FormatError,
    onboard_account,
)
from condor.executors import secrets
from condor.executors.wallets import (
    decrypt_gateway_keystore,
    keypair_from_secret,
    load_hyperliquid_creds,
)

GATEWAY_WALLET = Path.home() / "gateway/conf/wallets/solana/82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5.json"

_KEY = base64.b64encode(b"k" * 32).decode()
HL_KEY = "0x" + "11" * 32
HL_ACCT = "0x" + "ab" * 20


@pytest.fixture()
def store_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    for var in ("CONDOR_HL_AGENT_KEY", "CONDOR_HL_ACCOUNT_ADDRESS", "CONDOR_HL_NETWORK",
                "CONDOR_SOLANA_SECRET_KEY", "CONDOR_SOLANA_RPC", "CONDOR_JUPITER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "venues.json"


# -- key material parsing --


def test_keypair_from_base58_and_array_agree():
    kp = Keypair()
    b58 = str(kp)  # base58 secret
    arr = json.dumps(list(bytes(kp)))
    assert keypair_from_secret(b58).pubkey() == kp.pubkey()
    assert keypair_from_secret(arr).pubkey() == kp.pubkey()


@pytest.mark.skipif(not GATEWAY_WALLET.exists(), reason="local gateway wallet not present")
def test_decrypt_gateway_keystore_matches_pubkey():
    ks = json.loads(GATEWAY_WALLET.read_text())
    secret = decrypt_gateway_keystore(ks, "a")
    kp = keypair_from_secret(secret)
    assert str(kp.pubkey()) == "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"


def test_secret_encrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    tok = secrets.encrypt_secret("s3cret")
    assert secrets.is_encrypted(tok) and tok != "s3cret"
    assert secrets.decrypt_secret(tok) == "s3cret"
    assert secrets.decrypt_secret("plaintext-passthrough") == "plaintext-passthrough"


# -- structured-store loaders (env precedence DELETED) --


def test_loaders_ignore_env_entirely(store_path, monkeypatch):
    monkeypatch.setenv("CONDOR_HL_AGENT_KEY", "0xenvkey")
    monkeypatch.setenv("CONDOR_HL_ACCOUNT_ADDRESS", "0x" + "99" * 20)
    with pytest.raises(AccountResolutionError, match="no longer read"):
        load_hyperliquid_creds()
    # and with a configured account, env can never override it
    onboard_account(
        "hyperliquid", {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        prober=lambda *a: None,
    )
    creds = load_hyperliquid_creds()
    assert creds["agent_private_key"] == HL_KEY  # not "0xenvkey"
    assert creds["account_address"] == HL_ACCT


def test_load_hyperliquid_from_sealed_store(store_path):
    onboard_account(
        "hyperliquid", {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        prober=lambda *a: None,
    )
    raw = json.loads(store_path.read_text())["hyperliquid"]["accounts"][HL_ACCT]
    assert raw["agent_private_key"].startswith("enc:v1:")  # sealed on disk
    creds = load_hyperliquid_creds()
    assert creds == {"agent_private_key": HL_KEY, "account_address": HL_ACCT,
                     "network": "mainnet"}


def test_flat_pre_v1_store_rejected(store_path):
    store_path.write_text(json.dumps(
        {"hyperliquid": {"agent_private_key": "0xk", "account_address": HL_ACCT}}
    ))
    with pytest.raises(PreV1FormatError, match="Re-onboard"):
        load_hyperliquid_creds()


def test_solana_custody_verified_on_load(store_path):
    """A hand-edited entry whose secret derives a different pubkey than its
    map key is refused — never trade the wrong wallet."""
    kp = Keypair()
    other = Keypair()
    wallets.account_store().upsert_account(
        "solana", str(other.pubkey()), {"secret_key_b58": str(kp), "name": ""}
    )
    with pytest.raises(RuntimeError, match="custody mismatch"):
        wallets.load_solana_keypair()


def test_solana_rpc_url_defaults_public(store_path):
    from condor.executors.solana import DEFAULT_RPC

    assert wallets.solana_rpc_url() == DEFAULT_RPC  # no account, no selector
    with pytest.raises(AccountResolutionError):
        wallets.solana_rpc_url("named")  # explicit selector must resolve
    kp = Keypair()
    onboard_account("solana", {"secret_key_b58": str(kp), "rpc_url": "https://rpc.example"},
                    prober=lambda *a: None)
    assert wallets.solana_rpc_url() == "https://rpc.example"


# -- _services shim (non-account config) --


def test_service_config_seals_and_decrypts(store_path):
    entry = wallets.save_service("jupiter", {"api_key": "jup_secret"})
    assert entry["api_key"].startswith("enc:v1:")
    raw = json.loads(store_path.read_text())
    assert raw["_services"]["jupiter"]["api_key"].startswith("enc:v1:")
    assert wallets.venue_config("jupiter") == {"api_key": "jup_secret"}
    assert wallets.venue_config("absent") == {}  # nothing stored → empty


def test_service_config_coexists_with_accounts(store_path):
    wallets.save_service("jupiter", {"api_key": "jup_secret"})
    onboard_account(
        "hyperliquid", {"agent_private_key": HL_KEY, "account_address": HL_ACCT},
        prober=lambda *a: None,
    )
    assert wallets.venue_config("jupiter")["api_key"] == "jup_secret"
    assert load_hyperliquid_creds()["account_address"] == HL_ACCT


def test_save_service_unknown_raises(store_path):
    with pytest.raises(ValueError, match="unknown service"):
        wallets.save_service("nope", {"x": "y"})
