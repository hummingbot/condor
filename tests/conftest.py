"""Shared test fixtures."""

import pytest

TEST_SOL_ACCOUNT = "7fEsqLYz3Zr7SNoBn9GnCTHU5V2Ta8DFXECfWQEmYVWk"
TEST_HL_ACCOUNT = "0x" + "a" * 40
TEST_PM_ACCOUNT = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _isolate_account_store(tmp_path_factory, monkeypatch):
    """Every test gets a structured multi-venue account store.

    Production executor creation now requires an exact persisted AccountRef;
    tests that exercise only adapters use these credential-free identities
    with injected connector overrides.
    """
    import json

    from condor.executors import wallets

    path = tmp_path_factory.mktemp("accounts") / "venues.json"
    path.write_text(
        json.dumps(
            {
                "solana": {
                    "default_account": TEST_SOL_ACCOUNT,
                    "accounts": {TEST_SOL_ACCOUNT: {"name": "test-solana"}},
                },
                "hyperliquid": {
                    "default_account": TEST_HL_ACCOUNT,
                    "accounts": {TEST_HL_ACCOUNT: {"name": "test-hl"}},
                },
                "polymarket": {
                    "default_account": TEST_PM_ACCOUNT,
                    "accounts": {TEST_PM_ACCOUNT: {"name": "test-pm"}},
                },
            }
        )
    )
    monkeypatch.setattr(wallets, "_VENUES_PATH", path)


@pytest.fixture(autouse=True)
def _isolate_notifications_outbox(tmp_path_factory, monkeypatch):
    """Redirect the notifications outbox to a tmp file for every test.

    Executors emit trade notifications via condor.notifications.notify(),
    which appends to store/notifications.jsonl. Without this, running the
    executor tests would pollute the real outbox. Tests that assert on the
    outbox (test_notifications_outbox) monkeypatch OUTBOX_PATH themselves and
    override this.
    """
    import condor.notifications as notifications

    path = tmp_path_factory.mktemp("outbox") / "notifications.jsonl"
    monkeypatch.setattr(notifications, "OUTBOX_PATH", path)


@pytest.fixture(autouse=True)
def _isolate_run_store(tmp_path_factory, monkeypatch):
    """Give every test a fresh RunStore rooted in a tmp dir.

    Engines/delegations/consults open run streams via get_run_store();
    without this, a test that constructs one would write into the real
    agents/ tree — and the singleton would pin whatever root the first
    test happened to resolve. Tests that need a specific root call
    set_run_store(RunStore(root=...)) themselves.
    """
    from condor.agents import runstore

    runstore.set_run_store(runstore.RunStore(root=tmp_path_factory.mktemp("runstore")))
    yield
    runstore.set_run_store(None)
