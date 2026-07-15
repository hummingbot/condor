"""Venue credential loading — keypair parsing + Gateway keystore decrypt."""

import json
from pathlib import Path

import pytest
from solders.keypair import Keypair

from condor.executors.wallets import (
    decrypt_gateway_keystore,
    keypair_from_secret,
    load_hyperliquid_creds,
)

GATEWAY_WALLET = Path.home() / "gateway/conf/wallets/solana/82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5.json"


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


def test_hyperliquid_creds_require_fields(monkeypatch):
    monkeypatch.setattr("condor.executors.wallets._venues_file", lambda: {})
    for var in ("CONDOR_HL_AGENT_KEY", "CONDOR_HL_ACCOUNT_ADDRESS", "CONDOR_HL_NETWORK"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError):
        load_hyperliquid_creds()


def test_hyperliquid_creds_from_env(monkeypatch):
    monkeypatch.setattr("condor.executors.wallets._venues_file", lambda: {})
    monkeypatch.setenv("CONDOR_HL_AGENT_KEY", "0xkey")
    monkeypatch.setenv("CONDOR_HL_ACCOUNT_ADDRESS", "0xacct")
    monkeypatch.setenv("CONDOR_HL_NETWORK", "testnet")
    creds = load_hyperliquid_creds()
    assert creds == {"agent_private_key": "0xkey", "account_address": "0xacct", "network": "testnet"}


# -- encrypted venue store (native wallet onboarding, #7) -----------------------

import base64  # noqa: E402

from condor.executors import secrets, wallets  # noqa: E402

_KEY = base64.b64encode(b"k" * 32).decode()


def test_secret_encrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    tok = secrets.encrypt_secret("s3cret")
    assert secrets.is_encrypted(tok) and tok != "s3cret"
    assert secrets.decrypt_secret(tok) == "s3cret"
    assert secrets.decrypt_secret("plaintext-passthrough") == "plaintext-passthrough"


def test_save_venue_seals_secrets_reader_decrypts(monkeypatch, tmp_path):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    wallets.save_venue("hyperliquid", {
        "agent_private_key": "0xSECRET", "account_address": "0xpublic", "network": "mainnet"})
    raw = json.loads((tmp_path / "venues.json").read_text())["hyperliquid"]
    assert raw["agent_private_key"].startswith("enc:v1:")  # secret sealed
    assert raw["account_address"] == "0xpublic"            # non-secret plaintext
    cfg = wallets.venue_config("hyperliquid")              # transparent decrypt
    assert cfg["agent_private_key"] == "0xSECRET"
    assert cfg["account_address"] == "0xpublic"
    assert "agent_private_key" in wallets.configured_venues()["hyperliquid"]  # names only


def test_save_venue_unknown_venue_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    with pytest.raises(ValueError):
        wallets.save_venue("nope", {"x": "y"})


def test_load_hyperliquid_from_sealed_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    monkeypatch.delenv("CONDOR_HL_AGENT_KEY", raising=False)
    monkeypatch.delenv("CONDOR_HL_ACCOUNT_ADDRESS", raising=False)
    wallets.save_venue("hyperliquid", {"agent_private_key": "0xk", "account_address": "0xa"})
    creds = load_hyperliquid_creds()
    assert creds["agent_private_key"] == "0xk" and creds["account_address"] == "0xa"


def test_import_env_to_store_seals(monkeypatch, tmp_path):
    monkeypatch.setenv("CONDOR_SECRETS_KEY", _KEY)
    monkeypatch.setattr(wallets, "_VENUES_PATH", tmp_path / "venues.json")
    monkeypatch.setenv("CONDOR_HL_AGENT_KEY", "0xk")
    monkeypatch.setenv("CONDOR_HL_ACCOUNT_ADDRESS", "0xa")
    monkeypatch.setenv("CONDOR_JUPITER_API_KEY", "jup_x")
    written = wallets.import_env_to_store()
    assert "hyperliquid" in written and "jupiter" in written
    raw = json.loads((tmp_path / "venues.json").read_text())
    assert raw["hyperliquid"]["agent_private_key"].startswith("enc:v1:")
    assert raw["jupiter"]["api_key"].startswith("enc:v1:")
