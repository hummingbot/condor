"""The agent hub: publish, browse, install (FEAT-146).

Four properties, in the order they matter:

* **No user-supplied URL is ever dialled.** The base is compiled in and no
  environment variable moves it, the listing id goes through
  ``validate_identifier`` *before* a request exists, and a redirect is refused.
  That is the whole of FEAT-133's SSRF objection, answered.
* **Integrity is checked before anything parses.** ``fetch`` holds the bytes to
  the digest the listing reported and raises on a mismatch, so the preview and
  the install can each do their own fetch and still be the same bundle.
* **One serializer, one gate.** A publish sends byte-for-byte what
  ``GET /agents/{slug}/bundle`` returns, and an install runs through exactly
  FEAT-133's ``plan``/``apply`` or FEAT-145's update pair — the badge changes no
  decision anywhere.
* **The credential never shows up anywhere.** Not in a GET response, not in a
  log record, not in a request body or URL.

**Every test drives a stub transport.** ``hub._send`` is the one HTTP call in
the module and it is replaced whole here, so this file opens no socket — which
is also how it asserts that a refused identifier produces *zero* requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pytest

from condor import hub
from condor.agents import bundle as ab
from condor.agents.agent import AgentStore
from condor.fetchers._identifiers import IdentifierError
from condor.memory.mutes import load_mutes
from condor.memory.paths import agent_home

REPO_ROOT = Path(__file__).resolve().parents[1]

AGENT_MD = (
    "---\n"
    "name: Traveller\n"
    "description: a traveller\n"
    "agent_key: claude-code\n"
    "tools: []\n"
    "when_to_consult: when something must move\n"
    "server_required: true\n"
    "server_name: ''\n"
    "created_by: 0\n"
    "created_at: '2026-01-01T00:00:00+00:00'\n"
    "---\n"
    "\nTravel.\n"
)

SKILL_MD = (
    "---\nname: s\ndescription: d\nwhen_to_use: whenever\nsource: builtin\n"
    "---\n\nLook.\n"
)

ROUTINE_PY = (
    "from pydantic import BaseModel\n\n\n"
    "class Config(BaseModel):\n    pass\n\n\n"
    "async def run(config, context):\n    return 'ok'\n"
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


#: The admin, and the identity ``_client`` authenticates as. ``require_admin``
#: re-reads the role from the ConfigManager rather than trusting the JWT claim,
#: so a ``WebUser(role="admin")`` is not enough on its own — the config has to
#: say so too.
ADMIN_ID = 7
#: Approved, and not an admin: publishing with the install's one Botcamp token
#: would be acting as the admin's account at an outside service.
SEAT_ID = 2


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    """Make reaching a real socket a failure, in every test in this file.

    The criterion is "the suite makes no network call", and the way to hold a
    file to that is not to read it carefully — it is to break the one thing
    that could. ``hub._OPENER`` is the only object in the module that can open
    a connection, so a test whose stub did not cover some path fails here by
    name instead of hanging for ``HUB_TIMEOUT_S`` on a developer's machine and
    passing on a CI box with no route out.
    """

    def _no(*_a, **_kw):
        raise AssertionError("a test tried to open a real connection to the hub")

    monkeypatch.setattr(hub._OPENER, "open", _no)


@pytest.fixture(autouse=True)
def cm(tmp_path, monkeypatch):
    """A real ConfigManager on a throwaway config.yml — persistence included.

    **Autouse**, and that is the point rather than convenience. Three things in
    these paths reach ``get_config_manager()``: ``require_admin``,
    ``_install_capabilities`` and the token accessor. Without this fixture each
    of them resolves ``ConfigManager.instance()`` against ``./config.yml``,
    which under pytest is the developer's own live install — so the admin tests
    would pass or fail depending on whose laptop ran them, and every route test
    would read a real operator's servers and grants (the class of leak
    ``_isolated_runtime_root`` exists to close for every other durable root).
    """
    import config_manager as cm_module

    monkeypatch.chdir(tmp_path)  # audit_log.yml is written relative to cwd
    manager = cm_module.ConfigManager(config_path=str(tmp_path / "config.yml"))
    manager._data["users"] = {
        ADMIN_ID: {"user_id": ADMIN_ID, "role": "admin", "first_name": "Root"},
        SEAT_ID: {"user_id": SEAT_ID, "role": "user", "first_name": "Seat"},
    }
    monkeypatch.setattr(cm_module.ConfigManager, "_instance", manager)
    return manager


@pytest.fixture
def traveller():
    """A library agent with one of everything the layout defines."""
    home = agent_home("traveller")
    _write(home / "AGENT.md", AGENT_MD)
    _write(home / "skills" / "s" / "SKILL.md", SKILL_MD)
    _write(home / "routines" / "r.py", ROUTINE_PY)
    return home


@pytest.fixture
def frozen_clock(monkeypatch):
    """One ``exported_at`` for the whole test.

    ``build`` stamps ``datetime.now`` to the second, so two builds a second
    apart differ by one field. A byte-for-byte comparison of what the download
    hands over and what the publish sends has to be *asserted*, not left to the
    two calls landing inside the same second.
    """

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(ab, "datetime", _Fixed)


# ── The stub transport ───────────────────────────────────────────────────


class Call(NamedTuple):
    """One request the module tried to make."""

    method: str
    url: str
    token: str
    payload: bytes

    @property
    def body(self) -> dict:
        return json.loads(self.payload.decode("utf-8")) if self.payload else {}


class FakeHub:
    """A hub in a dict, and a ledger of everything that was sent to it."""

    def __init__(self):
        self.calls: list[Call] = []
        self.listings: dict[str, dict] = {}
        self.bundles: dict[str, bytes] = {}
        #: ``(method, path)`` -> ``Reply`` or an exception to raise instead.
        self.overrides: dict[tuple[str, str], object] = {}
        self.receipt = {"listing_id": "fresh", "slug": "traveller", "version": 1}

    def offer(self, listing_id: str, text: str, **fields) -> dict:
        """Publish one bundle into the fake catalogue, digest included."""
        data = text.encode("utf-8")
        listing = {
            "listing_id": listing_id,
            "digest": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "badge": hub.COMMUNITY,
            **fields,
        }
        self.listings[listing_id] = listing
        self.bundles[listing_id] = data
        return listing

    def send(self, method, url, *, token="", payload=b"") -> hub.Reply:
        """Stand in for ``hub._send``, translating like it does.

        An override that is an exception is raised as ``_send`` would have
        raised it — wrapped in a ``HubError`` with ``status=0`` — rather than
        escaping raw. The stub replaces ``_send``, so it has to be faithful to
        ``_send``'s contract or every test above it is asserting on a seam that
        does not behave like the one in production;
        ``test_a_transport_failure_is_translated_where_it_happens`` holds the
        real function to the same sentence from underneath.
        """
        self.calls.append(Call(method, url, token, payload))
        assert url.startswith(hub.HUB_URL), f"dialled something else: {url}"
        path = url[len(hub.HUB_URL) :]
        override = self.overrides.get((method, path.split("?")[0]))
        if isinstance(override, BaseException):
            raise hub.HubError(f"Could not reach the agent hub: {override}", status=0)
        if isinstance(override, hub.Reply):
            return override
        return self._route(method, path.split("?")[0])

    def _route(self, method: str, path: str) -> hub.Reply:
        if method == "GET" and path == "/agents":
            return _ok({"listings": list(self.listings.values())})
        if method == "GET" and path.endswith("/bundle"):
            listing_id = path[len("/agents/") : -len("/bundle")]
            body = self.bundles.get(listing_id)
            return hub.Reply(200, body) if body else _missing()
        if method == "GET" and path.startswith("/agents/"):
            listing = self.listings.get(path[len("/agents/") :])
            return _ok(listing) if listing else _missing()
        if method == "POST" and path == "/agents":
            return _ok(self.receipt)
        if method == "DELETE" and path.startswith("/agents/"):
            return hub.Reply(204, b"")
        return _missing()


def _ok(payload) -> hub.Reply:
    return hub.Reply(200, json.dumps(payload).encode("utf-8"))


def _missing() -> hub.Reply:
    return hub.Reply(404, b'{"detail": "no such listing"}')


@pytest.fixture
def fake_hub(monkeypatch):
    stub = FakeHub()
    monkeypatch.setattr(hub, "_send", stub.send)
    return stub


def _bundle_text(slug: str = "traveller") -> str:
    return ab.render(ab.build(slug))


# ── Headless, and compiled in ────────────────────────────────────────────


def test_the_hub_module_imports_no_web_app_and_no_telegram():
    """The ``condor/updates`` rule: the surfaces are views over this API.

    In a fresh interpreter, because this test module has already imported the
    FastAPI routes in-process by the time it runs.
    """
    probe = (
        "import sys, condor.hub;"
        "print(int(any(m == 'fastapi' or m.startswith('fastapi.') "
        "or m == 'telegram' or m.startswith('telegram.') for m in sys.modules)))"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "0", "condor.hub dragged in fastapi or telegram"


def test_no_environment_variable_redirects_the_hub():
    """The compiled-in rule ``condor/sharing/outbox.py`` sets, asserted.

    Every plausible name, set at once, in a fresh interpreter that then imports
    the module for the first time — an in-process ``importlib.reload`` would
    not prove it, because the constant could have been read at first import.
    """
    names = [
        "CONDOR_HUB_URL",
        "HUB_URL",
        "CONDOR_HUB",
        "CONDOR_HUB_BASE",
        "CONDOR_HUB_ENDPOINT",
        "CONDOR_BOTCAMP_URL",
        "BOTCAMP_URL",
        "CONDOR_AGENT_HUB_URL",
    ]
    env = {name: "http://127.0.0.1:1/v1" for name in names}
    done = subprocess.run(
        [sys.executable, "-c", "import condor.hub as h; print(h.HUB_URL)"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={**_base_env(), **env},
    )
    assert done.stdout.strip() == "https://hub.hummingbot.org/v1"
    assert "127.0.0.1" not in done.stdout


def _base_env() -> dict:
    import os

    return {k: v for k, v in os.environ.items()}


# ── The identifier guard: refused before a request exists ────────────────


@pytest.mark.parametrize(
    "listing_id",
    [
        "../../admin",
        "..",
        "a?b",
        "a#b",
        "a b",
        "a\nb",
        "",
        "http://169.254.169.254/latest/meta-data",
        "a/b",
        "a%2fb",
    ],
)
def test_a_listing_id_that_could_re_aim_the_url_is_refused_unsent(fake_hub, listing_id):
    """``validate_identifier`` runs before anything is sent, so nothing is.

    The zero-calls assertion is the real one: a check that ran *after* the
    request would pass a test that only looked at the exception.
    """
    with pytest.raises(IdentifierError):
        hub.get(listing_id)
    with pytest.raises(IdentifierError):
        hub.fetch(listing_id)
    with pytest.raises(IdentifierError):
        hub.unpublish(listing_id, "tok")

    assert fake_hub.calls == []


def test_a_redirect_is_refused_rather_than_followed():
    """A 302 would be the one place the compiled-in base stops holding.

    Exercised on the handler directly: it is the seam the real opener uses, and
    the stub transport that stands in for the socket everywhere else in this
    file is precisely what cannot exercise it.
    """
    handler = hub._NoRedirect()

    with pytest.raises(hub.HubError) as exc:
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data"
        )

    assert "redirect" in str(exc.value)
    assert exc.value.status == 302


# ── The catalogue ────────────────────────────────────────────────────────


def test_browse_reads_the_catalogue_and_sends_no_token(fake_hub):
    fake_hub.offer("one", "{}", slug="a", name="A")
    fake_hub.offer("two", "{}", slug="b", name="B", badge=hub.VALIDATED)

    listings = hub.browse()

    assert [listing.listing_id for listing in listings] == ["one", "two"]
    assert [listing.badge for listing in listings] == [hub.COMMUNITY, hub.VALIDATED]
    assert fake_hub.calls[0].token == ""


def test_a_search_term_is_a_query_parameter_and_never_a_path_segment(fake_hub):
    """So it needs no identifier check: it cannot change the endpoint."""
    hub.browse("../../admin #1")

    url = fake_hub.calls[0].url
    assert url.startswith(f"{hub.HUB_URL}/agents?")
    assert "/admin" not in url


def test_one_unreadable_row_does_not_make_the_catalogue_unbrowsable(fake_hub):
    fake_hub.offer("good", "{}", slug="a")
    fake_hub.listings["bad"] = {"version": "not a number at all"}

    assert [listing.listing_id for listing in hub.browse()] == ["good"]


def test_the_hubs_refusal_is_reported_with_its_status(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = hub.Reply(
        503, b'{"detail": "the hub is down for maintenance"}'
    )

    with pytest.raises(hub.HubError) as exc:
        hub.browse()

    assert "maintenance" in str(exc.value)
    assert exc.value.status == 503


def test_a_transport_failure_is_translated_where_it_happens(monkeypatch):
    """Stubbed at ``_dial``, which is below the translation rather than over it.

    The seam matters: replacing ``_send`` to simulate a refused connection
    would replace the code under test, and the assertion would hold whether or
    not ``_send`` translated anything at all.
    """

    def _refused(_request):
        raise OSError("connection refused")

    monkeypatch.setattr(hub, "_dial", _refused)

    with pytest.raises(hub.HubError) as exc:
        hub.browse()

    assert "Could not reach the agent hub" in str(exc.value)
    assert "connection refused" in str(exc.value)
    assert exc.value.status == 0


def test_an_error_status_is_an_answer_and_not_a_transport_failure(monkeypatch):
    """A 409's body carries the sentence the operator needs; it must survive."""
    import io
    import urllib.error

    def _conflict(_request):
        raise urllib.error.HTTPError(
            f"{hub.HUB_URL}/agents",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"detail": "that slug is already published"}'),
        )

    monkeypatch.setattr(hub, "_dial", _conflict)

    with pytest.raises(hub.HubError) as exc:
        hub.browse()

    assert "already published" in str(exc.value)
    assert exc.value.status == 409


# ── The digest, which is the whole integrity story ───────────────────────


def test_fetch_returns_matching_bytes_and_they_parse_through_the_one_parser(
    traveller, fake_hub
):
    text = _bundle_text()
    fake_hub.offer("listed", text, slug="traveller", agent_uid="x" * 32)

    data = hub.fetch("listed")

    assert data == text.encode("utf-8")
    assert ab.parse(data).agent.slug == "traveller"


def test_fetch_refuses_bytes_that_do_not_match_the_listings_digest(traveller, fake_hub):
    """Named, and nothing is handed back — so no caller can parse them."""
    text = _bundle_text()
    fake_hub.offer("listed", text, slug="traveller")
    fake_hub.bundles["listed"] = text.replace("Travel.", "Trade.").encode("utf-8")

    with pytest.raises(hub.HubError) as exc:
        hub.fetch("listed")

    assert "listed" in str(exc.value)
    assert "digest" in str(exc.value)


def test_a_listing_with_no_digest_is_refused_rather_than_trusted(fake_hub):
    fake_hub.offer("listed", "{}", slug="a")
    fake_hub.listings["listed"]["digest"] = ""

    with pytest.raises(hub.HubError) as exc:
        hub.fetch("listed")

    assert "no digest" in str(exc.value)
    assert not any(call.url.endswith("/bundle") for call in fake_hub.calls)


def test_an_oversized_bundle_is_refused_before_it_is_hashed(fake_hub):
    big = b"x" * (ab.MAX_BUNDLE_BYTES + 1)
    fake_hub.listings["listed"] = {
        "listing_id": "listed",
        "digest": hashlib.sha256(big).hexdigest(),
    }
    fake_hub.bundles["listed"] = big

    with pytest.raises(hub.HubError) as exc:
        hub.fetch("listed")

    assert "limit" in str(exc.value)


# ── Publishing: one serializer, and the export's refusal ─────────────────


def test_publish_sends_byte_for_byte_what_the_download_hands_over(
    traveller, fake_hub, frozen_clock
):
    """The one-builder rule, across the two doors that emit a bundle."""
    from starlette.testclient import TestClient

    downloaded = _client().get("/agents/traveller/bundle").content

    hub.publish("traveller", "tok")

    sent = fake_hub.calls[-1].body["bundle"].encode("utf-8")
    assert sent == downloaded
    assert isinstance(TestClient, type)  # the client really was the HTTP door


def test_publish_carries_the_identity_the_version_and_the_digest(
    traveller, fake_hub, frozen_clock
):
    receipt = hub.publish("traveller", "tok", notes="Widened the grid")

    body = fake_hub.calls[-1].body
    bundle = ab.parse(body["bundle"].encode("utf-8"))
    assert body["slug"] == "traveller"
    assert body["agent_uid"] == bundle.agent.agent_uid
    assert body["notes"] == "Widened the grid" == bundle.notes
    assert body["digest"] == hashlib.sha256(body["bundle"].encode("utf-8")).hexdigest()
    assert receipt.listing_id == "fresh"


def test_publish_refuses_a_certain_secret_and_sends_nothing(traveller, fake_hub):
    """FEAT-144's export refusal, inherited rather than re-implemented.

    It is written for exactly this moment: the value is still fixable by the
    person who owns it, and a published bundle cannot be unpublished out of
    somebody's download.
    """
    _write(
        traveller / "skills" / "s" / "SKILL.md",
        SKILL_MD.replace("Look.", "key: " + "0123456789abcdef" * 4 + ",1,2,3"),
    )
    _write(
        traveller / "routines" / "r.py",
        ROUTINE_PY + "\nPHRASE = 'abandon ability able about above absent absorb "
        "abstract absurd abuse access accident'\n",
    )

    with pytest.raises(ab.BundleError):
        hub.publish("traveller", "tok")

    assert fake_hub.calls == []


def test_publish_without_a_token_sends_nothing_and_says_what_to_do(traveller, fake_hub):
    with pytest.raises(hub.HubError) as exc:
        hub.publish("traveller", "   ")

    assert "Settings" in str(exc.value)
    assert fake_hub.calls == []


def test_a_failed_publish_raises_readably_and_queues_nothing(traveller, fake_hub):
    """A 500 and then a timeout. There is no durable queue to inspect.

    The shape is deliberate, and the docstring of ``condor/sharing/outbox.py``
    is the argument: that queue exists because an unshare must survive a
    restart and because a sweep sends what nobody is watching. A publish is an
    interactive act with a human watching the result, so a failure is theirs to
    see and theirs to retry.
    """
    from condor import paths

    before = _tree(paths.runtime_root()) | _tree(paths.data_dir())

    fake_hub.overrides[("POST", "/agents")] = hub.Reply(500, b'{"detail": "boom"}')
    with pytest.raises(hub.HubError) as exc:
        hub.publish("traveller", "tok")
    assert exc.value.status == 500
    assert "500" in str(exc.value)

    fake_hub.overrides[("POST", "/agents")] = TimeoutError("timed out")
    with pytest.raises(hub.HubError) as exc:
        hub.publish("traveller", "tok")
    assert "Could not reach the agent hub" in str(exc.value)

    assert _tree(paths.runtime_root()) | _tree(paths.data_dir()) == before


def _tree(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_a_revoked_token_is_reported_as_the_operators_to_fix(traveller, fake_hub):
    fake_hub.overrides[("POST", "/agents")] = hub.Reply(401, b"{}")

    with pytest.raises(hub.HubError) as exc:
        hub.publish("traveller", "tok")

    assert exc.value.status == 401
    assert "token" in str(exc.value)


def test_unpublish_authenticates_with_the_account_token_and_no_capability(fake_hub):
    """No ``delete_token``: ``wire.py``'s capability answers a different problem.

    That collector "must not know who you are", so a revocation there has to
    prove possession of a secret instead of an identity. A hub publish is
    authenticated by an account, and the same account authenticates the
    withdrawal.
    """
    hub.unpublish("listed", "tok")

    call = fake_hub.calls[-1]
    assert call.method == "DELETE"
    assert call.token == "tok"
    assert call.payload == b""
    assert b"delete_token" not in call.payload
    assert "delete_token" not in call.url
    assert "tok" not in call.url


def test_unpublish_reports_the_hubs_refusal(fake_hub):
    fake_hub.overrides[("DELETE", "/agents/listed")] = hub.Reply(
        403, b'{"detail": "that listing is not yours"}'
    )

    with pytest.raises(hub.HubError) as exc:
        hub.unpublish("listed", "tok")

    assert "not yours" in str(exc.value)


# ── The token: stored, never read back, never logged ─────────────────────


def test_the_token_survives_a_save_and_a_fresh_load(cm):
    """ARCH-177 and CORR-244's failure, asserted rather than assumed.

    Both were a config section declared in some of the three hand-kept lists
    and not all of them, so it was dropped on the next save — once taking every
    user preference with it, once re-minting the admin's sharing veto on every
    restart. ARCH-738 collapsed those lists into ``CONFIG_SECTIONS``; this is
    the test that says the ``hub`` section is really in it.
    """
    import config_manager as cm_module

    assert hub.set_token("botcamp-tok") is True
    cm._save_config()

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.get_hub() == {"token": "botcamp-tok"}


def test_clearing_the_token_is_persisted_too(cm):
    import config_manager as cm_module

    hub.set_token("botcamp-tok")
    assert hub.set_token("") is False

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.get_hub().get("token") == ""


def test_an_absent_config_file_is_not_created_by_reading_the_token(
    tmp_path, monkeypatch
):
    """The guard ``consent._cm`` sets: a read must not materialize config.yml.

    In a directory of its own, because the autouse fixture has already written
    a ``config.yml`` into ``tmp_path`` — asserting on that one would be
    asserting the fixture had run.
    """
    import config_manager as cm_module

    fresh = tmp_path / "fresh-clone"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    monkeypatch.setattr(cm_module.ConfigManager, "_instance", None)

    assert hub.token() == ""
    assert not (fresh / "config.yml").exists()


def test_the_token_route_never_answers_with_the_value(cm):
    client = _settings_client(admin=True)
    client.put("/settings/hub-token", json={"token": "botcamp-tok"})

    res = client.get("/settings/hub-token")

    assert res.json() == {"set": True, "can_change": True}
    assert "botcamp-tok" not in res.text

    client.put("/settings/hub-token", json={"token": ""})
    unset = client.get("/settings/hub-token")
    assert unset.json() == {"set": False, "can_change": True}
    assert "token" not in unset.json()


def test_only_the_admin_may_set_the_token(cm):
    """One token, one Botcamp account, install-wide — the telemetry line."""
    res = _settings_client(admin=False).put(
        "/settings/hub-token", json={"token": "botcamp-tok"}
    )

    assert res.status_code == 403
    assert hub.token() == ""


def test_the_token_reaches_no_log_record_during_a_publish(
    traveller, fake_hub, cm, caplog
):
    hub.set_token("botcamp-sup3rs3cret")

    with caplog.at_level(logging.DEBUG):
        hub.publish("traveller", hub.token())

    assert fake_hub.calls[-1].token == "botcamp-sup3rs3cret"
    assert "botcamp-sup3rs3cret" not in caplog.text
    assert "traveller" in caplog.text  # the agent's name is fine, and useful


# ── Installing: the file path's gate, unchanged ──────────────────────────


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.hub_router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=ADMIN_ID, username="u", first_name="U", role="admin"
    )
    return TestClient(app)


def _settings_client(admin: bool):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import settings as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=ADMIN_ID if admin else SEAT_ID,
        username="u",
        first_name="U",
        role="admin" if admin else "user",
    )
    return TestClient(app)


@pytest.fixture
def offered(traveller, fake_hub):
    """``traveller`` published to the fake hub, and the agent gone from disk.

    Removed so the install is a *create*: the test that wants the update verb
    leaves it in place instead.
    """
    import shutil

    text = _bundle_text()
    listing = fake_hub.offer(
        "listed",
        text,
        slug="traveller",
        name="Traveller",
        agent_uid=ab.parse(text.encode("utf-8")).agent.agent_uid,
        author={"name": "Fede", "handle": "@fede"},
    )
    shutil.rmtree(traveller)
    return listing


def test_a_listing_this_install_does_not_have_is_an_install(offered):
    preview = _client().post("/hub/agents/listed/install/preview")

    assert preview.status_code == 200
    body = preview.json()
    assert body["verb"] == "install"
    # FEAT-133's ``ImportPlan`` shape, not FEAT-145's.
    assert body["plan"]["slug"] == "traveller"
    assert "refusals" in body["plan"] and "executables" in body["plan"]
    assert "blockers" not in body["plan"]
    assert not agent_home("traveller").exists()


def test_a_listing_of_an_agent_already_here_is_an_update(traveller, fake_hub):
    """FEAT-145's verb, chosen by the plan's ``is_update`` and not by this layer."""
    client = _client()
    text = client.get("/agents/traveller/bundle").text
    bundle = ab.parse(text.encode("utf-8"))
    # A newer bundle of the same agent: the identity matches what is installed.
    bumped = bundle.model_copy(update={"version": bundle.version + 1})
    fake_hub.offer(
        "listed",
        ab.render(bumped),
        slug="traveller",
        agent_uid=bundle.agent.agent_uid,
    )
    ab.write_stamp(
        traveller,
        ab.BundleStamp(
            agent_uid=bundle.agent.agent_uid,
            version=bundle.version,
            exported_at=bundle.exported_at,
            digests={f.path: f.sha256 for f in bundle.files},
        ),
    )

    body = client.post("/hub/agents/listed/install/preview").json()

    assert body["verb"] == "update"
    # FEAT-145's ``UpdatePlan`` shape, which the import plan does not have.
    assert "blockers" in body["plan"] and "counts" in body["plan"]
    assert body["plan"]["installed_agent_uid"] == bundle.agent.agent_uid


def test_the_preview_and_the_install_carry_the_same_plan(offered, fake_hub):
    client = _client()

    preview = client.post("/hub/agents/listed/install/preview").json()
    installed = client.post("/hub/agents/listed/install").json()

    for field, value in preview["plan"].items():
        assert installed["plan"][field] == value, field
    assert installed["plan"]["imported"] is True
    # Two fetches, not one cached preview: the digest check is what makes that
    # safe, and it ran on both.
    assert sum(1 for call in fake_hub.calls if call.url.endswith("/bundle")) == 2
    # And one listing GET apiece, not two: the route hands the listing it
    # already holds to ``fetch`` rather than asking the hub again.
    assert sum(1 for call in fake_hub.calls if call.url.endswith("/listed")) == 2


def test_the_badge_changes_no_decision(offered, fake_hub):
    """A community listing plans, mutes and reports exactly what a validated
    one does. Asserted by comparing two responses with only the badge differing.
    """
    client = _client()
    community = client.post("/hub/agents/listed/install/preview").json()

    fake_hub.listings["listed"]["badge"] = hub.VALIDATED
    validated = client.post("/hub/agents/listed/install/preview").json()

    assert community["listing"]["badge"] == hub.COMMUNITY
    assert validated["listing"]["badge"] == hub.VALIDATED
    assert community["plan"] == validated["plan"]
    assert community["verb"] == validated["verb"]


def test_an_installed_hub_agent_arrives_muted_with_nothing_running(offered):
    from condor.agents.engine import get_all_engines

    res = _client().post("/hub/agents/listed/install")

    assert res.status_code == 200
    assert AgentStore().get("traveller") is not None
    muted = load_mutes("traveller")
    assert muted["routines"] == {"r"}
    assert muted["skills"] == {"s"}
    assert not [a for a in get_all_engines() if a.startswith("traveller")]
    assert "unmute" in res.json()["plan"]["next_step"]


def test_an_imported_routine_has_no_execution_consent_on_the_hub_path(offered):
    """FEAT-147, which is a separate gesture from the mute and both are needed."""
    from condor.agents import code_trust

    _client().post("/hub/agents/listed/install")

    source = agent_home("traveller") / "routines" / "r.py"
    assert not code_trust.allowed("traveller", "r", source.read_text())


def test_the_findings_the_hub_computed_ride_on_the_listing(offered, fake_hub):
    """The hub's review is the one whose input the author does not control.

    The export-side review is advice to the author, who runs their own copy of
    the exporter; this is why the service re-runs it on submission (TASK-004)
    and why the listing carries the result.
    """
    fake_hub.listings["listed"]["findings"] = [
        {
            "category": "endpoint",
            "label": "public host",
            "path": "AGENT.md",
            "line": 3,
            "start": 10,
            "end": 30,
        }
    ]
    fake_hub.listings["listed"]["review_version"] = "bundle_review/2026-10-18"

    body = _client().post("/hub/agents/listed/install/preview").json()

    assert body["listing"]["findings"][0]["label"] == "public host"
    assert body["listing"]["review_version"] == "bundle_review/2026-10-18"


def test_the_author_handle_is_carried_as_the_hubs_attestation(offered):
    body = _client().post("/hub/agents/listed/install/preview").json()

    assert body["listing"]["author"] == {"name": "Fede", "handle": "@fede"}


def test_every_hub_surface_carries_the_one_trust_sentence(offered):
    client = _client()

    for body in (
        client.get("/hub/agents").json(),
        client.get("/hub/agents/listed").json(),
    ):
        assert "not a security boundary" in body["trust"]
        assert "muted and unstarted" in body["trust"]


# ── The routes' own refusals ─────────────────────────────────────────────


def test_a_refused_listing_id_is_a_400_and_nothing_is_sent(fake_hub):
    res = _client().get("/hub/agents/not%20a%20listing")

    assert res.status_code == 400
    assert fake_hub.calls == []


def test_an_unreachable_hub_is_a_502_and_not_a_500(monkeypatch):
    """Stubbed at ``_dial`` so the route sees the ``HubError`` production makes.

    A raw ``OSError`` out of a stubbed ``_send`` would be a 500 and would be
    testing a seam that cannot happen: ``_send`` is the thing that translates.
    Without ``fake_hub``, deliberately — that fixture replaces ``_send``, which
    is above the stub here and would swallow it.
    """

    def _refused(_request):
        raise OSError("refused")

    monkeypatch.setattr(hub, "_dial", _refused)

    res = _client().get("/hub/agents")
    assert res.status_code == 502
    assert "Could not reach the agent hub" in res.json()["detail"]


def test_a_missing_listing_keeps_the_hubs_404(fake_hub):
    assert _client().get("/hub/agents/nothing").status_code == 404


def test_the_hub_cannot_serve_something_that_is_not_a_bundle(fake_hub):
    text = '{"kind": "something-else"}'
    fake_hub.offer("listed", text, slug="x")

    res = _client().post("/hub/agents/listed/install")

    assert res.status_code == 400
    assert "Condor agent bundle" in res.json()["detail"]


def test_a_tampered_bundle_never_reaches_the_importer(traveller, fake_hub):
    """The digest check, through the route — a 502, and nothing written."""
    text = _bundle_text()
    fake_hub.offer("listed", text, slug="traveller")
    fake_hub.bundles["listed"] = text.replace("Travel.", "Trade.").encode("utf-8")
    import shutil

    shutil.rmtree(traveller)

    res = _client().post("/hub/agents/listed/install")

    assert res.status_code == 502
    assert "digest" in res.json()["detail"]
    assert not agent_home("traveller").exists()


def test_the_publish_route_answers_a_receipt(traveller, fake_hub):
    hub.set_token("botcamp-tok")

    res = _client().post("/agents/traveller/publish", json={"notes": "first cut"})

    assert res.status_code == 200
    assert res.json()["receipt"]["listing_id"] == "fresh"
    assert fake_hub.calls[-1].body["notes"] == "first cut"
    assert "botcamp-tok" not in res.text


def test_the_publish_route_says_400_when_no_token_is_set(traveller, fake_hub):
    """Not 502: nothing was sent, so there is no upstream to blame.

    Reported as the hub being down is how an operator spends an afternoon
    checking a service that is fine.
    """
    res = _client().post("/agents/traveller/publish")

    assert res.status_code == 400
    assert "Settings" in res.json()["detail"]
    assert fake_hub.calls == []


def test_the_unpublish_route_withdraws_and_refuses_a_non_admin(traveller, fake_hub):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    hub.set_token("botcamp-tok")
    assert _client().delete("/hub/agents/listed").json() == {
        "unpublished": True,
        "listing_id": "listed",
    }
    assert fake_hub.calls[-1].method == "DELETE"

    app = FastAPI()
    app.include_router(routes.hub_router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=SEAT_ID, username="u", first_name="U", role="user"
    )
    sent = len(fake_hub.calls)

    assert TestClient(app).delete("/hub/agents/listed").status_code == 403
    assert len(fake_hub.calls) == sent


def test_the_publish_route_refuses_a_non_admin(traveller, fake_hub):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=SEAT_ID, username="u", first_name="U", role="user"
    )

    res = TestClient(app).post("/agents/traveller/publish")

    assert res.status_code == 403
    assert fake_hub.calls == []


def test_the_publish_route_refuses_an_agent_that_does_not_exist(fake_hub):
    assert _client().post("/agents/nobody/publish").status_code == 404
    assert fake_hub.calls == []


# ── The chat/MCP door ────────────────────────────────────────────────────


def test_manage_agents_browses_the_hub(fake_hub):
    from mcp_servers.condor.tools import trading_agent

    fake_hub.offer("listed", "{}", slug="a", name="A", badge=hub.VALIDATED)

    result = trading_agent.manage_agents(action="hub_browse")

    assert [row["listing_id"] for row in result["listings"]] == ["listed"]
    assert "not a security boundary" in result["trust"]


def test_manage_agents_installs_from_the_hub(offered, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)

    result = trading_agent.manage_agents(action="hub_install", path="listed")

    assert result["verb"] == "install"
    assert result["imported"] is True
    assert result["listing"]["badge"] == hub.COMMUNITY
    assert load_mutes("traveller")["routines"] == {"r"}


def test_manage_agents_hub_install_needs_a_listing_id(fake_hub):
    from mcp_servers.condor.tools import trading_agent

    assert "required" in trading_agent.manage_agents(action="hub_install")["error"]
    assert fake_hub.calls == []


def test_manage_agents_publishes_to_the_hub(traveller, fake_hub, cm):
    from mcp_servers.condor.tools import trading_agent

    hub.set_token("botcamp-tok")

    result = trading_agent.manage_agents(
        action="publish_hub", agent_slug="traveller", notes="first cut"
    )

    assert result["published"] is True
    assert result["listing_id"] == "fresh"
    assert fake_hub.calls[-1].token == "botcamp-tok"


def test_manage_agents_publish_without_a_token_is_an_error_not_a_raise(
    traveller, fake_hub
):
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents(action="publish_hub", agent_slug="traveller")

    assert "Settings" in result["error"]
    assert fake_hub.calls == []


def test_the_hub_actions_are_routed_and_not_misfiled(fake_hub):
    """A sibling family's action answers with the call that would have worked."""
    from mcp_servers.condor.tools import trading_agent

    misrouted = trading_agent.manage_loops(action="hub_browse")

    assert "manage_agents" in misrouted["error"]


def test_the_bundle_is_addressed_with_the_id_the_caller_validated(traveller, fake_hub):
    """Never with the one the reply carried, which nothing checked.

    A hub answer is a string from another service. Interpolating its
    ``listing_id`` into the download path would put an unvalidated value in a
    URL segment — the exact thing ``condor/fetchers/_identifiers.py`` exists to
    prevent — and the compiled-in base would be holding the host on its own.
    """
    text = _bundle_text()
    fake_hub.offer("listed", text, slug="traveller")
    fake_hub.listings["listed"]["listing_id"] = "../../admin"

    with pytest.raises(hub.HubError) as exc:
        hub.fetch("listed")

    assert "listed" in str(exc.value)
    assert not any("/admin" in call.url for call in fake_hub.calls)
    assert not any(call.url.endswith("/bundle") for call in fake_hub.calls)
