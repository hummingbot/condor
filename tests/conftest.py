"""Shared test fixtures."""

import pytest


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
