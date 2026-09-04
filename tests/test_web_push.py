"""FEAT-083: the bell rings with the window closed.

Web Push has one part that cannot be tested here — the push itself, which needs
a browser, a real subscription and a vendor's push service. Everything *around*
it can be, and the three things that would actually hurt are all in that half:

* one user's devices reachable from another user's token,
* a dead endpoint that is never pruned, so it is retried for every notification
  forever, and
* a sink that lets its own failure escape into the producer that was only
  announcing a finished routine.

The end-to-end check (install the app, close every window, finish a routine) is
manual and is the one that proves the feature. These pin what a regression would
otherwise take quietly.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from condor import notifications, paths, push
from condor.web.models import WebUser
from condor.web.routes import push as push_routes

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "frontend" / "dist"
INDEX_HTML = DIST / "index.html"

ALICE = WebUser(id=1, role="user")
BOB = WebUser(id=2, role="user")

ALICE_ENDPOINT = "https://push.example.com/v1/alice"
BOB_ENDPOINT = "https://push.example.com/v1/bob"


def _sub(endpoint: str, label: str = "Chrome on macOS") -> push.Subscription:
    return push.new_subscription(
        endpoint=endpoint, p256dh="p256dh-key", auth="auth-secret", label=label
    )


def _note(user_id: int, text: str = "Routine finished") -> notifications.Notification:
    return notifications.Notification(
        id="abc123",
        user_id=user_id,
        ts=0.0,
        kind="routine",
        text=text,
        title="Arb check",
        link="/routines?tab=reports",
    )


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


# ── The store ──


def test_upsert_on_endpoint_does_not_duplicate_a_device():
    """A browser re-subscribing must replace its row, not add a second one.

    A permission reset, an app reinstall and a key rotation all hand back the
    same endpoint. Appending would mean one device buzzing twice per
    notification and a Settings list of rows nobody can tell apart.
    """
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT, "Chrome on macOS")))
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT, "Chrome on macOS (again)")))

    rows = push.list_for(ALICE.id)
    assert len(rows) == 1
    assert rows[0].label == "Chrome on macOS (again)"


def test_a_corrupt_store_reads_as_empty_rather_than_raising():
    """Junk on disk costs the feature, not the process that reads it."""
    path = paths.push_subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all", encoding="utf-8")

    assert push.list_for(ALICE.id) == []


def test_a_junk_row_costs_that_row_only():
    """A row written by an older build must not take the store down with it."""
    path = paths.push_subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "1": [
                    {"endpoint": ALICE_ENDPOINT, "p256dh": "k", "auth": "a"},
                    {"endpoint": "", "p256dh": "k", "auth": "a"},
                    {"nothing": "useful"},
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = push.list_for(ALICE.id)
    assert [r.endpoint for r in rows] == [ALICE_ENDPOINT]


def test_a_device_is_only_removable_by_the_user_holding_it():
    """`remove` is scoped to one bucket; another user's endpoint is not found."""
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))
    asyncio.run(push.save(BOB.id, _sub(BOB_ENDPOINT)))

    assert asyncio.run(push.remove(BOB.id, ALICE_ENDPOINT)) is False
    assert [r.endpoint for r in push.list_for(ALICE.id)] == [ALICE_ENDPOINT]
    assert asyncio.run(push.remove(ALICE.id, ALICE_ENDPOINT)) is True
    assert push.list_for(ALICE.id) == []
    assert [r.endpoint for r in push.list_for(BOB.id)] == [BOB_ENDPOINT]


# ── The VAPID keypair ──


def test_the_keypair_is_generated_once_and_never_rewritten():
    """Rotating it silently invalidates every subscription that exists.

    The failure mode is "push stopped working" with no error anywhere, so a
    second read must return the first write byte for byte.
    """
    first = push.vapid_keys()
    assert paths.vapid_key_path().is_file()
    assert push.vapid_keys() == first
    # The browser wants an uncompressed P-256 point: 65 bytes, so 0x04 first.
    assert push.public_key() == first["public_key"]
    assert push.private_key() == first["private_key"]


def test_the_key_directory_is_private_and_the_file_is_not_mode_0600():
    """Directory 0700, file 0644 — and that ordering is the whole point.

    A 0600 file is the tempting shape and it is the one that breaks: bind-mounted
    into a container it reads fine on macOS and fails with `Permission denied` on
    every Linux deploy, because the uid inside is not the uid that wrote it. The
    directory carries the protection so the file's own mode is not load-bearing.
    """
    push.vapid_keys()
    assert os.stat(paths.vapid_dir()).st_mode & 0o777 == 0o700
    assert os.stat(paths.vapid_key_path()).st_mode & 0o777 == 0o644


def test_the_environment_wins_over_the_file(monkeypatch):
    """A deployment holding its keys in a secret manager never touches disk."""
    monkeypatch.setenv(push.PRIVATE_KEY_ENV, "private-from-env")
    monkeypatch.setenv(push.PUBLIC_KEY_ENV, "public-from-env")

    assert push.public_key() == "public-from-env"
    assert push.private_key() == "private-from-env"
    assert not paths.vapid_key_path().exists()


def test_half_a_keypair_in_the_environment_is_ignored(monkeypatch):
    """A public key with no private half cannot sign; fall through to the file."""
    monkeypatch.setenv(push.PUBLIC_KEY_ENV, "public-from-env")

    assert push.public_key() != "public-from-env"
    assert paths.vapid_key_path().is_file()


def test_the_scrubber_knows_the_key_by_value_once_one_exists():
    """A pasted key file must not survive a share.

    Tier 1, not a tier-2 pattern: a raw P-256 scalar is 43 base64url characters,
    which is also an id and a digest, so the *shape* is not decidable. The value
    is one the install knows, and it reaches a transcript by one plausible
    route — an operator debugging "push stopped working" pastes the key file in.
    """
    from condor.sharing.scrub import install_values

    key = push.private_key()
    assert (key, "known_key") in install_values()


def test_reading_the_key_for_the_scrubber_does_not_mint_one():
    """An install that never turned push on must not get a keypair from a share.

    Generating one here would be worse than a missed value: it writes a durable
    secret as a side effect of an unrelated action, and (because the file is
    written once and never rewritten) fixes this install's push identity
    forever.
    """
    from condor.sharing.scrub import install_values

    assert push.configured_private_key() == ""
    install_values()
    assert not paths.vapid_key_path().exists()


# ── The routes ──


def test_subscribe_stores_under_the_token_and_never_a_body_id():
    """The endpoint in a body is a device to store, not an authorization."""
    body = push_routes.SubscribeRequest(
        endpoint=ALICE_ENDPOINT, p256dh="k", auth="a", label="Chrome on macOS"
    )
    result = asyncio.run(push_routes.subscribe(body, ALICE))

    assert result["subscribed"] is True
    assert [r.endpoint for r in push.list_for(ALICE.id)] == [ALICE_ENDPOINT]
    assert push.list_for(BOB.id) == []


def test_a_listing_never_leaks_another_users_devices_or_key_material():
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))
    asyncio.run(push.save(BOB.id, _sub(BOB_ENDPOINT)))

    rows = asyncio.run(push_routes.list_subscriptions(BOB))["items"]
    assert [r["endpoint"] for r in rows] == [BOB_ENDPOINT]
    # The browser's own key and auth secret have no reader out here.
    assert "p256dh" not in rows[0] and "auth" not in rows[0]


def test_unsubscribe_cannot_remove_someone_elses_endpoint():
    """Mirrors `test_agents_chat_id_ownership`: the token addresses the row."""
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))

    result = asyncio.run(
        push_routes.unsubscribe(
            push_routes.UnsubscribeRequest(endpoint=ALICE_ENDPOINT), BOB
        )
    )
    assert result["removed"] is False
    assert [r.endpoint for r in push.list_for(ALICE.id)] == [ALICE_ENDPOINT]


# ── The sink ──


def test_the_sink_pushes_one_payload_per_device(monkeypatch):
    sent: list[dict] = []

    async def fake_webpush(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(push_routes, "webpush_async", fake_webpush)
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT + "-2")))
    asyncio.run(push.save(BOB.id, _sub(BOB_ENDPOINT)))

    asyncio.run(push_routes._web_push(_note(ALICE.id)))

    assert len(sent) == 2
    endpoints = {call["subscription_info"]["endpoint"] for call in sent}
    assert endpoints == {ALICE_ENDPOINT, ALICE_ENDPOINT + "-2"}

    payload = json.loads(sent[0]["data"])
    assert payload["title"] == "Arb check"
    assert payload["link"] == "/routines?tab=reports"
    assert payload["id"] == "abc123"
    # Held by the push service while the machine is asleep — the whole point.
    assert sent[0]["ttl"] > 0


def test_a_user_with_no_devices_costs_no_push(monkeypatch):
    calls: list[dict] = []

    async def fake_webpush(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(push_routes, "webpush_async", fake_webpush)
    asyncio.run(push_routes._web_push(_note(BOB.id)))

    assert calls == []


def test_a_long_notification_is_cut_to_fit_the_protocol(monkeypatch):
    """A push payload is capped at ~4KB after encryption. The bell holds the rest."""
    sent: list[dict] = []

    async def fake_webpush(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(push_routes, "webpush_async", fake_webpush)
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))

    asyncio.run(push_routes._web_push(_note(ALICE.id, "x" * 5000)))

    body = json.loads(sent[0]["data"])["body"]
    assert len(body) < 500
    assert body.endswith("…")


@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_endpoint_is_pruned_and_no_other_row_is(monkeypatch, status):
    """The only place a subscription dies without the user asking.

    It has to exist: a browser that threw its registration away answers 404/410
    forever, and an unpruned row is retried on every single notification.
    """
    from pywebpush import WebPushException

    async def fake_webpush(**kwargs):
        if kwargs["subscription_info"]["endpoint"] == ALICE_ENDPOINT:
            raise WebPushException("gone", response=_FakeResponse(status))

    monkeypatch.setattr(push_routes, "webpush_async", fake_webpush)
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT + "-alive")))
    asyncio.run(push.save(BOB.id, _sub(BOB_ENDPOINT)))

    asyncio.run(push_routes._web_push(_note(ALICE.id)))

    assert [r.endpoint for r in push.list_for(ALICE.id)] == [ALICE_ENDPOINT + "-alive"]
    assert [r.endpoint for r in push.list_for(BOB.id)] == [BOB_ENDPOINT]


def test_a_transient_failure_keeps_the_subscription(monkeypatch):
    """A 500 from a push service is the service's problem, not the device's."""
    from pywebpush import WebPushException

    async def fake_webpush(**kwargs):
        raise WebPushException("boom", response=_FakeResponse(500))

    monkeypatch.setattr(push_routes, "webpush_async", fake_webpush)
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))

    asyncio.run(push_routes._web_push(_note(ALICE.id)))

    assert [r.endpoint for r in push.list_for(ALICE.id)] == [ALICE_ENDPOINT]


def test_a_raising_sink_never_reaches_the_producer(monkeypatch):
    """`record()` completes and the bell entry is written regardless.

    The bus contract (`notifications._push`) is that a dead sink is not a dead
    notification, and this sink must not be the one that tests it.
    """

    async def exploding_webpush(**kwargs):
        raise RuntimeError("the push service melted")

    monkeypatch.setattr(push_routes, "webpush_async", exploding_webpush)
    asyncio.run(push.save(ALICE.id, _sub(ALICE_ENDPOINT)))

    stored = asyncio.run(
        notifications.record(ALICE.id, "Routine finished", kind="routine")
    )

    assert stored is not None
    assert [n.text for n in notifications.list_for(ALICE.id)] == ["Routine finished"]


# ── The service worker, as it is actually served ──


@pytest.mark.skipif(
    not INDEX_HTML.is_file(),
    reason="frontend/dist not built; the static file route is not mounted",
)
class TestServiceWorkerIsServed:
    """FEAT-082 shipped no service worker; this is the first one.

    It is served by the same file arm of the SPA catch-all the manifest uses —
    `public/` is copied verbatim into `dist/` — so there is no route to add and
    correspondingly nothing to stop it silently falling through to `index.html`,
    which is why these assert on bytes.
    """

    @pytest.fixture(scope="class")
    def client(self):
        from starlette.testclient import TestClient

        from condor.web.app import create_app

        with TestClient(create_app()) as test_client:
            yield test_client

    def test_it_is_served_as_javascript_from_the_root(self, client):
        """Scope `/` depends on it being at the root, not on a header."""
        resp = client.get("/sw.js")
        assert resp.status_code == 200
        assert resp.content != INDEX_HTML.read_bytes()
        assert "javascript" in resp.headers["content-type"]

    def test_it_revalidates_like_every_other_unhashed_file(self, client):
        """An updated worker has to be picked up on the next load."""
        resp = client.get("/sw.js")
        assert "no-cache" in resp.headers["cache-control"]

    def test_it_registers_a_push_handler_and_no_fetch_handler(self, client):
        """The absence is the design, not an omission.

        The moment this worker caches a response it becomes a second, silently
        disagreeing answer to "which build is installed" — the exact failure
        `app.py`'s `_NO_CACHE`/`_HashedAssets` pair already has a story about.
        """
        body = client.get("/sw.js").text
        assert 'addEventListener("push"' in body
        assert 'addEventListener("notificationclick"' in body
        assert 'addEventListener("fetch"' not in body
        assert "caches" not in body

    def test_clicking_a_notification_reuses_the_open_window(self, client):
        """`focus` + `postMessage`, never `openWindow` when a window exists.

        The runtime twin of the manifest's `launch_handler: navigate-existing`
        (FEAT-082), and for the same reason: `ChatProvider` opens a chat
        WebSocket on every route, so a second window is a second subscription.
        """
        body = client.get("/sw.js").text
        assert "matchAll" in body
        assert "client.focus()" in body
        assert "condor:navigate" in body
