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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pytest

from condor import botcamp_import, hub
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


@pytest.fixture(autouse=True)
def _no_leaked_pairing(monkeypatch):
    """Isolate ``hub._pending_device`` (device login, PR #111) between tests.

    It is module-level, in-memory state by design (a device login is a
    short-lived interactive act, not something ``config.yml`` should ever
    hold) — but that means two tests sharing one process would otherwise
    share one install's login attempt. ``monkeypatch.setattr`` restores the
    prior value after the test, same shape as ``no_sockets`` above guarding
    the real module state.
    """
    monkeypatch.setattr(hub, "_pending_device", None)


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
        #: The default ``POST /agents`` answer: ``202`` accepted-for-processing,
        #: truthfully pending (contract §5.2) — not an immediate listing.
        self.receipt = {
            "submission_id": "sub_test",
            "status": "pending",
            "slug": "traveller",
            "version": 1,
            "submitted_at": "2026-10-21T12:03:00Z",
        }

    def offer(self, slug: str, data: bytes, *, version: int = 1, **fields) -> dict:
        """Publish one agent version into the fake catalogue (PR #111's
        ``GET /api/agents/{slug}`` shape — a slug and its approved versions,
        never a listing id or a badge).

        ``data`` is the zip Botcamp would serve — see :func:`_zip_agent` —
        not Condor's own JSON bundle text.
        """
        row = {
            "version": version,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "fileCount": 0,
            "manifest": {},
            "sourceRef": "",
            "createdAt": "2026-10-18T09:00:00Z",
        }
        listing = {
            "slug": slug,
            "title": fields.pop("name", slug),
            "summary": fields.pop("description", ""),
            "latest": version,
            "versions": [row],
        }
        listing.update(fields)
        self.listings[slug] = listing
        self.bundles[(slug, version)] = data
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
        if method == "GET" and path.startswith("/agents/") and "/versions/" in path:
            slug, _, version_seg = path[len("/agents/") :].partition("/versions/")
            listing = self.listings.get(slug)
            if not listing:
                return _missing()
            resolved = (
                listing["latest"] if version_seg == "latest" else int(version_seg)
            )
            data = self.bundles.get((slug, resolved))
            if data is None:
                return _missing()
            row = next(v for v in listing["versions"] if v["version"] == resolved)
            return hub.Reply(
                200,
                data,
                {"X-Agent-Sha256": row["sha256"], "X-Agent-Version": str(resolved)},
            )
        if method == "GET" and path.startswith("/agents/"):
            listing = self.listings.get(path[len("/agents/") :])
            return _ok(listing) if listing else _missing()
        if method == "POST" and path == "/agents":
            return _ok(self.receipt)
        if method == "DELETE" and path.startswith("/agents/"):
            return hub.Reply(204, b"")
        # ── Submission lifecycle (FEAT-150, contract §5.3) — a sane default
        # that repeats "still pending"; a test wanting a transition sets an
        # ``overrides`` entry for the exact ``("GET", "/submissions/<id>")``.
        if method == "GET" and path.startswith("/submissions/"):
            return _ok(
                {
                    "submission_id": path[len("/submissions/") :],
                    "status": "pending",
                    "submitted_at": "2026-10-21T12:03:00Z",
                }
            )
        # ── Device login (PR #111) — sane defaults; a test wanting a specific
        # transition (200/authorization_pending/slow_down/access_denied/
        # expired_token/invalid_grant) sets an ``overrides`` entry for the
        # exact path instead.
        if method == "POST" and path == "/condor/device/code":
            return _ok(
                {
                    "device_code": "dev_test",
                    "user_code": "WXYZ-4821",
                    "verification_uri": "https://dev.botcamp.xyz/connect",
                    "verification_uri_complete": "https://dev.botcamp.xyz/connect?code=WXYZ-4821",
                    "expires_in": 600,
                    "interval": 5,
                }
            )
        if method == "POST" and path == "/condor/device/token":
            return hub.Reply(
                400, json.dumps({"error": "authorization_pending"}).encode()
            )
        # ── `GET`/`DELETE /condor/me` (PR #111) — sane defaults; a test
        # wanting a 401 (revoked token) or a refusal sets an ``overrides``
        # entry for the exact path instead.
        if method == "GET" and path == "/condor/me":
            return _ok(
                {
                    "data": {
                        "user": {
                            "id": "u_1",
                            "name": "cardosofede",
                            "slug": "cardosofede",
                        },
                        "scopes": ["agents:read", "agents:publish"],
                    }
                }
            )
        if method == "DELETE" and path == "/condor/me":
            return _ok({"data": {"revoked": True}})
        return _missing()


def _ok(payload) -> hub.Reply:
    return hub.Reply(200, json.dumps(payload).encode("utf-8"))


def _missing() -> hub.Reply:
    return hub.Reply(404, b'{"detail": "no such listing"}')


#: What every hub test addresses. Pinned rather than read from the module,
#: because an operator's `.env` may legitimately carry the
#: `CONDOR_HUB_DEV_MODE`/`CONDOR_HUB_DEV_URL` override (pointing this install
#: at a LAN hub), and a suite whose expected URLs move with it would pass or
#: fail on local config rather than on the code.
HUB_BASE = "https://dev.botcamp.xyz/api"


@pytest.fixture
def fake_hub(monkeypatch):
    stub = FakeHub()
    monkeypatch.setattr(hub, "HUB_URL", HUB_BASE)
    monkeypatch.setattr(hub, "_send", stub.send)
    return stub


def _bundle_text(slug: str = "traveller") -> str:
    return ab.render(ab.build(slug))


def _zip_agent(slug: str) -> bytes:
    """``slug``'s library files, zipped the way Botcamp serves one version
    (PR #111's wire shape) — never Condor's own JSON bundle.
    """
    import io
    import zipfile

    from condor.layering import publishable_files

    home = agent_home(slug)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel in publishable_files(home):
            zf.writestr(rel.as_posix(), (home / rel).read_bytes())
    return buf.getvalue()


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

    ``CONDOR_HUB_DEV_URL`` is in this list deliberately: it is the dev
    override's *target* variable, and setting it alone — without the knowing
    opt-in ``CONDOR_HUB_DEV_MODE`` — must be exactly as inert as any of the
    other plausible names. A bare env read would have redirected on this one
    alone; :func:`condor.hub._dev_override` must not.
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
        "CONDOR_HUB_DEV_URL",
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
    assert done.stdout.strip() == "https://dev.botcamp.xyz/api"
    assert "127.0.0.1" not in done.stdout


def test_dev_mode_opt_in_overrides_the_hub_url():
    """The one deliberate crack: both env vars together, and only together.

    ``CONDOR_HUB_DEV_MODE`` is the knowing opt-in an operator sets on purpose;
    ``CONDOR_HUB_DEV_URL`` is where it then points. A plain ``http://`` target
    is accepted here — a production default never is, since it is never read
    from the environment at all — and the override logs loudly at import.
    """
    env = {
        "CONDOR_HUB_DEV_MODE": "1",
        "CONDOR_HUB_DEV_URL": "http://masiva.local:3000/api",
    }
    done = subprocess.run(
        [sys.executable, "-c", "import condor.hub as h; print(h.HUB_URL)"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={**_base_env(), **env},
    )
    assert done.stdout.strip() == "http://masiva.local:3000/api"


@pytest.mark.parametrize(
    "target",
    [
        "file:///etc/passwd",
        "ftp://masiva.local/api",
        "data:text/plain,hello",
        "masiva.local:3000/api",  # no scheme: urlsplit reads the host as one
        "http:///api",  # no host
    ],
)
def test_dev_mode_refuses_anything_but_an_http_url(target):
    """SEC review C5: the opener also serves file://, ftp:// and data:."""
    env = {"CONDOR_HUB_DEV_MODE": "1", "CONDOR_HUB_DEV_URL": target}
    done = subprocess.run(
        [sys.executable, "-c", "import condor.hub as h; print(h.HUB_URL)"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={**_base_env(), **env},
    )
    assert done.stdout.strip() == HUB_BASE


def test_send_refuses_a_non_http_url_before_opening_anything(monkeypatch):
    def dial(request):  # pragma: no cover - reaching it is the failure
        raise AssertionError("dialed")

    monkeypatch.setattr(hub, "_dial", dial)

    with pytest.raises(hub.HubError):
        hub._send("GET", "file:///etc/passwd")


def test_dev_mode_flag_without_a_url_changes_nothing():
    """The opt-in alone, with no target, must not blank out the real hub."""
    done = subprocess.run(
        [sys.executable, "-c", "import condor.hub as h; print(h.HUB_URL)"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={**_base_env(), "CONDOR_HUB_DEV_MODE": "1"},
    )
    assert done.stdout.strip() == "https://dev.botcamp.xyz/api"


def _base_env() -> dict:
    """This process's environment, with the dev override neutralized.

    These probes run with ``cwd=REPO_ROOT``, so the subprocess loads the
    repo's ``.env`` — and an operator testing against a LAN hub legitimately
    has ``CONDOR_HUB_DEV_MODE``/``CONDOR_HUB_DEV_URL`` in there. Without this
    the suite would assert the compiled-in rule against whatever that
    operator happens to be pointed at today. Empty strings rather than
    deletions, because ``load_dotenv`` never overrides a variable that is
    already set: the file's values are what has to be shut out, and only a
    present-but-empty value does that. A test wanting the override merges its
    own values over this.
    """
    import os

    return {
        **{k: v for k, v in os.environ.items()},
        "CONDOR_HUB_DEV_MODE": "",
        "CONDOR_HUB_DEV_URL": "",
    }


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


def test_get_reads_one_agent_and_sends_no_token_when_none_is_linked(fake_hub):
    fake_hub.offer("river_scalper", b"zip-bytes", name="River Scalper")

    listing = hub.get("river_scalper")

    assert listing.slug == "river_scalper"
    assert listing.name == "River Scalper"
    assert listing.versions[0].version == 1
    assert fake_hub.calls[0].token == ""


def test_get_sends_the_linked_token_so_a_gated_agent_can_resolve(fake_hub):
    fake_hub.offer("river_scalper", b"zip-bytes")

    hub.get("river_scalper", "a-token")

    assert fake_hub.calls[0].token == "a-token"


@pytest.mark.parametrize(
    "user_code",
    ["", "AB", "CALL +1 555 0100 TO VERIFY", "WXYZ-4821\nvisit evil.example"],
)
def test_a_user_code_that_is_not_a_code_is_refused(fake_hub, user_code):
    """The code is shown to the admin and put in the approval link, so a hub
    cannot use it to write its own words on Condor's screen (SEC-819)."""
    fake_hub.overrides[("POST", "/condor/device/code")] = hub.Reply(
        200,
        json.dumps(
            {
                "device_code": "dev_1",
                "user_code": user_code,
                "verification_uri": f"{hub.HUB_URL}/connect",
            }
        ).encode(),
    )

    with pytest.raises(hub.HubError):
        hub.device_start()


def test_get_reads_botcamps_data_envelope(fake_hub):
    """Botcamp wraps every answer in ``{"data": ...}`` — the bare §3.2 shape
    above and the enveloped one Botcamp actually sends both read the same."""
    fake_hub.overrides[("GET", "/agents/river_scalper")] = hub.Reply(
        200,
        json.dumps(
            {
                "data": {
                    "slug": "river_scalper",
                    "title": "River Scalper",
                    "latest": 1,
                    "versions": [{"version": 1, "sha256": "ab" * 32}],
                }
            }
        ).encode(),
    )

    listing = hub.get("river_scalper")

    assert listing.slug == "river_scalper"
    assert listing.name == "River Scalper"
    assert [v.version for v in listing.versions] == [1]
    assert hub.listing_status("river_scalper", 1).state == "ok"


def _catalogue(*entries: dict, envelope: bool = False) -> hub.Reply:
    body = {"listings": list(entries)}
    return hub.Reply(200, json.dumps({"data": body} if envelope else body).encode())


def _entry(slug: str, **fields) -> dict:
    return {
        "listing_id": slug,
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "version": 1,
        "author": {"name": "James", "handle": "james"},
        "badge": "validated",
        "free": True,
        **fields,
    }


def test_browse_reads_one_page_of_the_catalogue(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("grid-bot", description="Runs a grid."),
        _entry("paid-bot", free=False),
    )

    entries = hub.browse("grid", page=2, token="a-token")

    assert [e.slug for e in entries] == ["grid-bot", "paid-bot"]
    assert entries[0].author.handle == "james"
    assert entries[0].description == "Runs a grid."
    assert entries[1].free is False
    assert fake_hub.calls[0].url == f"{hub.HUB_URL}/agents?query=grid&page=2"
    assert fake_hub.calls[0].token == "a-token"


def test_a_null_column_reads_as_not_sent(fake_hub):
    """Botcamp answers ``null`` for an empty nullable column — a zip upload's
    ``sourceRef``, a page with no ``summary`` — and that must not refuse the
    whole agent."""
    fake_hub.overrides[("GET", "/agents/zip_bot")] = hub.Reply(
        200,
        json.dumps(
            {
                "data": {
                    "slug": "zip_bot",
                    "title": "Zip Bot",
                    "summary": None,
                    "latest": 1,
                    "versions": [
                        {
                            "version": 1,
                            "sha256": "ab" * 32,
                            "sourceRef": None,
                            "manifest": None,
                        }
                    ],
                }
            }
        ).encode(),
    )
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("zip_bot", description=None, author={"name": None, "handle": "z"})
    )

    listing = hub.get("zip_bot")
    assert listing.description == ""
    assert listing.versions[0].source_ref == ""
    assert listing.versions[0].manifest == {}
    [entry] = hub.browse()
    assert entry.description == "" and entry.author.name == ""


def test_browse_reads_botcamps_data_envelope_too(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("grid-bot"), envelope=True
    )

    assert [e.slug for e in hub.browse()] == ["grid-bot"]


def test_browse_drops_an_entry_whose_slug_a_pull_could_not_address(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("../../admin"), _entry("grid-bot")
    )

    assert [e.slug for e in hub.browse()] == ["grid-bot"]


def test_browse_refuses_a_page_longer_than_any_the_hub_sends(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        *[_entry(f"bot-{i}") for i in range(hub.MAX_CATALOGUE_PAGE + 1)]
    )

    with pytest.raises(hub.HubError):
        hub.browse()


@pytest.mark.parametrize("page", [0, -1, 1001, True])
def test_browse_holds_the_page_to_a_range_before_sending(fake_hub, page):
    with pytest.raises(IdentifierError):
        hub.browse(page=page)
    assert fake_hub.calls == []


def test_browse_encodes_the_query_rather_than_pathing_it(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue()

    assert hub.browse("a/..?x=1") == []
    assert fake_hub.calls[0].url == f"{hub.HUB_URL}/agents?query=a%2F..%3Fx%3D1&page=1"


def test_every_catalogue_read_addresses_botcamps_own_paths(fake_hub):
    """The three paths Botcamp actually serves, pinned as whole URLs.

    This install once composed a contract of its own invention — a ``/v1``
    segment and a ``hub/catalogue`` path neither of which Botcamp has ever
    answered — and the symptom was a 404 that read like a missing route. The
    paths below are botcamp-site's: ``/api/agents?query=&page=``,
    ``/api/agents/{slug}`` and ``/api/agents/{slug}/versions/{version}``, off
    whatever base :data:`hub.HUB_URL` carries.
    """
    fake_hub.overrides[("GET", "/agents")] = _catalogue(_entry("grid-bot"))
    fake_hub.offer("grid_bot", b"a zip Botcamp serves")

    hub.browse()
    hub.get("grid_bot")
    hub.fetch("grid_bot", "1")

    assert [call.url for call in fake_hub.calls] == [
        f"{hub.HUB_URL}/agents?query=&page=1",
        f"{hub.HUB_URL}/agents/grid_bot",
        f"{hub.HUB_URL}/agents/grid_bot/versions/1",
    ]
    for call in fake_hub.calls:
        assert "/v1/" not in call.url
        assert "/hub/" not in call.url


def test_a_malformed_catalogue_reply_is_refused_readably(fake_hub):
    fake_hub.overrides[("GET", "/agents/bad")] = hub.Reply(200, b"not json")

    with pytest.raises(hub.HubError) as exc:
        hub.get("bad")

    assert "not JSON" in str(exc.value)


def test_the_hubs_refusal_is_reported_with_its_status(fake_hub):
    fake_hub.overrides[("GET", "/agents/x")] = hub.Reply(
        503, b'{"detail": "the hub is down for maintenance"}'
    )

    with pytest.raises(hub.HubError) as exc:
        hub.get("x")

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
        hub.get("x")

    assert "Could not reach the agent hub" in str(exc.value)
    assert "connection refused" in str(exc.value)
    assert exc.value.status == 0


def test_an_error_status_is_an_answer_and_not_a_transport_failure(monkeypatch):
    """A 409's body carries the sentence the operator needs; it must survive."""
    import io
    import urllib.error

    def _conflict(_request):
        raise urllib.error.HTTPError(
            f"{hub.HUB_URL}/agents/x",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"detail": "that slug is already published"}'),
        )

    monkeypatch.setattr(hub, "_dial", _conflict)

    with pytest.raises(hub.HubError) as exc:
        hub.get("x")

    assert "already published" in str(exc.value)
    assert exc.value.status == 409


# ── The digest, which is the whole integrity story ───────────────────────


def test_fetch_returns_matching_bytes_verified_against_their_own_header(
    traveller, fake_hub
):
    data = _zip_agent("traveller")
    fake_hub.offer("traveller", data)

    fetched = hub.fetch("traveller")

    assert fetched.data == data
    assert fetched.version == 1
    bundle = botcamp_import.from_zip(fetched.data, agent_slug="traveller")
    assert bundle.agent.slug == "traveller"


def test_fetch_refuses_bytes_that_do_not_match_their_own_sha256_header(
    traveller, fake_hub
):
    """Named, and nothing is handed back — so no caller can parse them."""
    data = _zip_agent("traveller")
    fake_hub.offer("traveller", data)
    fake_hub.bundles[("traveller", 1)] = data + b"\x00"

    with pytest.raises(hub.HubError) as exc:
        hub.fetch("traveller")

    assert "traveller" in str(exc.value)
    assert "X-Agent-Sha256" in str(exc.value)


def test_a_reply_with_no_digest_header_is_refused_rather_than_trusted(
    traveller, fake_hub
):
    data = _zip_agent("traveller")
    fake_hub.offer("traveller", data)
    fake_hub.overrides[("GET", "/agents/traveller/versions/latest")] = hub.Reply(
        200, data, {}
    )

    with pytest.raises(hub.HubError) as exc:
        hub.fetch("traveller")

    assert "carries no X-Agent-Sha256" in str(exc.value)


def test_an_oversized_bundle_is_refused_before_it_is_hashed(fake_hub):
    big = b"x" * (ab.MAX_BUNDLE_BYTES + 1)
    fake_hub.overrides[("GET", "/agents/listed/versions/latest")] = hub.Reply(
        200,
        big,
        {
            "X-Agent-Sha256": hashlib.sha256(big).hexdigest(),
            "X-Agent-Version": "1",
        },
    )

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
    assert receipt.submission_id == "sub_test"
    assert receipt.status == "pending"
    assert receipt.listing_id == ""


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

    assert "Agent Hub" in str(exc.value)
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


# ── Submission lifecycle (FEAT-150, contract §5) ──────────────────────────


def test_a_deterministic_rejection_is_a_rejected_receipt_not_a_raise(
    traveller, fake_hub, cm
):
    """The hub's own secret-scan/structural refusal (§5.2's ``400``).

    Distinct from ``test_publish_refuses_a_certain_secret_and_sends_nothing``:
    that one is Condor's own FEAT-144 refusal before anything is sent. This is
    the hub, after receiving bytes, saying no — which is an answer about the
    submission, truthfully reported, not a transport failure.
    """
    fake_hub.overrides[("POST", "/agents")] = hub.Reply(
        400,
        json.dumps(
            {
                "status": "rejected",
                "reasons": [
                    {
                        "rule": "secret-scan",
                        "detail": "skills/s/SKILL.md: a key-like value",
                    }
                ],
            }
        ).encode("utf-8"),
    )

    receipt = hub.publish("traveller", "tok")

    assert receipt.status == "rejected"
    assert receipt.submission_id == ""
    assert receipt.reasons == [
        hub.SubmissionReason(
            rule="secret-scan", detail="skills/s/SKILL.md: a key-like value"
        )
    ]
    assert hub.local_submission("traveller").status == "rejected"


def test_submission_status_merges_the_poll_over_what_publish_stored(
    traveller, fake_hub, cm
):
    """``GET /submissions/{id}`` never repeats slug/version/digest (§5.3) —
    those have to survive the poll from the record ``publish`` already made.
    """
    hub.publish("traveller", "tok", notes="widened the grid")
    # The old, forward-looking ``listing_id``-keyed shape ``submission_status``
    # still reads for badge provenance (_published_listing_provenance) — not
    # the pull side's ``AgentListing``, which has no badge at all.
    fake_hub.listings["lst_8f3a9c2e1b"] = {"badge": hub.VALIDATED}
    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(
        200,
        json.dumps(
            {
                "submission_id": "sub_test",
                "status": "published",
                "submitted_at": "2026-10-21T12:03:00Z",
                "decided_at": "2026-10-21T14:40:00Z",
                "listing_id": "lst_8f3a9c2e1b",
            }
        ).encode("utf-8"),
    )

    receipt = hub.submission_status("traveller", "tok")

    assert receipt.status == "published"
    assert receipt.listing_id == "lst_8f3a9c2e1b"
    assert receipt.slug == "traveller"  # carried over, not repeated by the poll
    assert receipt.badge == hub.VALIDATED  # fetched via the listing (§3.1)
    assert hub.local_submission("traveller") == receipt


def test_submission_status_reports_a_rejection_with_reasons(traveller, fake_hub, cm):
    hub.publish("traveller", "tok")
    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(
        200,
        json.dumps(
            {
                "submission_id": "sub_test",
                "status": "rejected",
                "submitted_at": "2026-10-21T12:03:00Z",
                "decided_at": "2026-10-21T14:40:00Z",
                "reasons": [
                    {
                        "rule": "human-review",
                        "detail": "Tool-steering language in a skill.",
                    }
                ],
            }
        ).encode("utf-8"),
    )

    receipt = hub.submission_status("traveller", "tok")

    assert receipt.status == "rejected"
    assert receipt.reasons[0].rule == "human-review"
    assert receipt.listing_id == ""


def test_submission_status_refuses_without_a_submission_on_file(cm):
    with pytest.raises(hub.HubError) as exc:
        hub.submission_status("nobody-published-this", "tok")

    assert "No submission is on record" in str(exc.value)


def test_a_failed_poll_leaves_the_stored_submission_alone(traveller, fake_hub, cm):
    """A transient status-check failure is the caller's to retry — it must not
    corrupt the last known-good record (FEAT-150's "transient network error
    does not cause a re-upload").
    """
    hub.publish("traveller", "tok")
    before = hub.local_submission("traveller")

    fake_hub.overrides[("GET", "/submissions/sub_test")] = TimeoutError("timed out")
    with pytest.raises(hub.HubError):
        hub.submission_status("traveller", "tok")

    assert hub.local_submission("traveller") == before


def test_a_resubmission_replaces_the_prior_submission_record(traveller, fake_hub, cm):
    """A new upload is a new submission (§5.4) — its record must not inherit
    the previous attempt's ``listing_id``/``badge``/``submission_id``.
    """
    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(
        200,
        json.dumps(
            {
                "submission_id": "sub_test",
                "status": "published",
                "submitted_at": "2026-10-21T12:03:00Z",
                "decided_at": "2026-10-21T14:40:00Z",
                "listing_id": "lst_old",
            }
        ).encode("utf-8"),
    )
    hub.publish("traveller", "tok")
    hub.submission_status("traveller", "tok")
    assert hub.local_submission("traveller").status == "published"

    fake_hub.receipt = {
        "submission_id": "sub_new",
        "status": "pending",
        "slug": "traveller",
        "version": 2,
        "submitted_at": "2026-10-22T09:00:00Z",
    }
    receipt = hub.publish("traveller", "tok", notes="a new revision")

    assert receipt.submission_id == "sub_new"
    assert receipt.status == "pending"
    assert receipt.listing_id == ""
    assert receipt.badge == ""
    stored = hub.local_submission("traveller")
    assert stored.submission_id == "sub_new"
    assert stored.listing_id == ""


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
    assert reloaded.get_hub() == {
        "token": "botcamp-tok",
        "token_origin": "https://dev.botcamp.xyz",
    }


def test_a_token_is_only_sent_to_the_hub_that_issued_it(cm, monkeypatch):
    """SEC review C5: turning the dev override on must not hand a production
    token to a LAN or dev host."""
    hub.set_token("bc_prod_token")
    assert hub.token() == "bc_prod_token"

    monkeypatch.setattr(hub, "HUB_URL", "http://masiva.local:3000/api")
    assert hub.token() == ""
    assert hub.device_state().status != "linked"

    hub.set_token("bc_dev_token")  # connecting again, to the dev hub
    assert hub.token() == "bc_dev_token"


def test_a_token_stored_before_origins_counts_as_the_compiled_hubs(cm, monkeypatch):
    cm.update_hub(token="bc_legacy")  # no token_origin, as before the change
    assert hub.token() == "bc_legacy"

    monkeypatch.setattr(hub, "HUB_URL", "http://masiva.local:3000/api")
    assert hub.token() == ""


def test_the_config_file_and_its_backup_are_owner_only(cm):
    """SEC review C5: config.yml holds the hub token, at the default 0644."""
    import os
    import stat

    cm.config_path.write_text("{}\n")
    os.chmod(cm.config_path, 0o644)  # as an older install left it

    hub.set_token("botcamp-tok")
    hub.set_token("botcamp-tok-2")  # a second save makes the backup

    assert stat.S_IMODE(os.stat(cm.config_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cm._backup_path).st_mode) == 0o600


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


def _client(admin: bool = True):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.hub_router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=ADMIN_ID if admin else SEAT_ID,
        username="u",
        first_name="U",
        role="admin" if admin else "user",
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


# ── Device login: connect to a Botcamp profile, no pasted token (PR #111) ──


def _force_poll():
    """Make the next ``hub.device_state()`` hit the hub rather than the
    locally cached ``pending`` answer, regardless of ``interval``."""
    hub._pending_device["last_polled"] = 0.0


def test_device_start_sends_no_credential_and_tracks_the_attempt(fake_hub):
    code = hub.device_start()

    assert fake_hub.calls[-1].method == "POST"
    assert fake_hub.calls[-1].url == f"{hub.HUB_URL}/condor/device/code"
    assert fake_hub.calls[-1].token == ""
    assert fake_hub.calls[-1].body == {}
    assert code.user_code == "WXYZ-4821"
    assert code.approval_url == "https://dev.botcamp.xyz/connect"
    assert hub._pending_device["device_code"] == "dev_test"


def _hostile_code(**over) -> object:
    """A ``/condor/device/code`` reply aimed at somebody else's site.

    The default fake reply is same-origin, so nothing in the suite before
    SEC-819 ever exercised the branch where the hub asks Condor to open a
    URL it did not choose. ``over`` replaces individual fields so a test can
    poison one of the two URLs, or both.
    """
    return _ok(
        {
            "device_code": "dev_test",
            "user_code": "WXYZ-4821",
            "verification_uri": "https://evil.example/phish",
            "verification_uri_complete": "https://evil.example/phish?code=WXYZ-4821",
            "expires_in": 600,
            "interval": 5,
            **over,
        }
    )


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({}, id="both-hostile"),
        pytest.param(
            {"verification_uri": "https://dev.botcamp.xyz/connect"},
            id="only-complete-hostile",
        ),
        pytest.param(
            {"verification_uri_complete": ""},
            id="complete-absent-uri-hostile",
        ),
    ],
)
def test_a_hostile_device_code_reply_never_reaches_the_browser(
    fake_hub, caplog, override
):
    """SEC-819: the link Condor hands out is its own, whichever of the two
    URLs the hub poisoned — and the hostile one is not merely deprioritized,
    it is absent from every response."""
    fake_hub.overrides[("POST", "/condor/device/code")] = _hostile_code(**override)

    with caplog.at_level(logging.WARNING, logger="condor.hub"):
        started = hub.device_start()

    safe = "https://dev.botcamp.xyz/connect"
    assert started.approval_url == safe
    assert "evil.example" not in started.model_dump_json()

    pending = hub.device_state()
    assert pending.status == "pending"
    assert pending.approval_url == safe
    assert "evil.example" not in pending.model_dump_json()

    # Exactly one warning, naming the destination without its `?code=` query
    # and never the polling secret.
    mismatches = [r for r in caplog.records if "not this install's hub" in r.message]
    assert len(mismatches) == 1
    assert "https://evil.example/phish" in mismatches[0].getMessage()
    assert "WXYZ-4821" not in mismatches[0].getMessage()
    assert "dev_test" not in mismatches[0].getMessage()


def test_an_ordinary_same_origin_reply_logs_no_mismatch(fake_hub, caplog):
    with caplog.at_level(logging.WARNING, logger="condor.hub"):
        started = hub.device_start()

    assert started.approval_url == "https://dev.botcamp.xyz/connect"
    assert not [r for r in caplog.records if "not this install's hub" in r.message]


@pytest.mark.parametrize(
    "hub_url, expected",
    [
        ("https://dev.botcamp.xyz/api", "https://dev.botcamp.xyz/connect"),
        # A dev hub: scheme, host *and port* survive; `/api` does not.
        ("http://masiva.local:3000/api", "http://masiva.local:3000/connect"),
    ],
)
def test_approval_url_is_built_from_the_configured_origin(
    monkeypatch, hub_url, expected
):
    monkeypatch.setattr(hub, "HUB_URL", hub_url)

    assert hub.approval_url() == expected


def test_the_approval_link_never_carries_the_code(fake_hub):
    """Botcamp has the person type the code (RFC 8628 §5.4) and ignores a
    ``?code=``: a prefilled link is what a phishing message would send."""
    started = hub.device_start()

    assert "WXYZ-4821" not in started.approval_url
    assert "?" not in started.approval_url
    assert started.user_code == "WXYZ-4821"  # still shown, to be typed


def test_device_state_is_none_with_nothing_pending_or_linked(cm):
    assert hub.device_state() == hub.DeviceLinkState(status="none")


def test_is_linked_never_polls_and_agrees_with_device_state(fake_hub, cm):
    """``is_linked()`` is the config-only read `/settings/hub-device/linked`
    uses so a non-admin seat can never drive the pending poll (BUG-3 case
    A). It must track whatever ``device_state`` would call "linked", off
    local config alone.
    """
    assert hub.is_linked() is False

    hub.device_start()
    calls_before = len(fake_hub.calls)
    assert hub.is_linked() is False
    assert len(fake_hub.calls) == calls_before  # no poll spent

    fake_hub.overrides[("POST", "/condor/device/token")] = _ok(
        {"access_token": "bc_live_x", "user": {"name": "cardosofede"}}
    )
    _force_poll()
    hub.device_state()

    assert hub.is_linked() is True


def test_device_state_reports_pending_with_the_user_code_and_link(fake_hub):
    hub.device_start()

    state = hub.device_state()

    assert state.status == "pending"
    assert state.user_code == "WXYZ-4821"
    assert state.approval_url == "https://dev.botcamp.xyz/connect"


def test_device_state_does_not_poll_the_hub_again_within_the_interval(fake_hub):
    hub.device_start()
    hub.device_state()
    calls_before = len(fake_hub.calls)

    hub.device_state()

    assert len(fake_hub.calls) == calls_before


def test_authorization_pending_keeps_polling(fake_hub):
    """The fake hub's default ``/condor/device/token`` answer — a plain 400
    ``authorization_pending`` — must read as still-``pending``, not an error."""
    hub.device_start()
    _force_poll()

    state = hub.device_state()

    assert state.status == "pending"
    assert fake_hub.calls[-1].url == f"{hub.HUB_URL}/condor/device/token"
    assert fake_hub.calls[-1].body == {"device_code": "dev_test"}


def test_slow_down_increases_the_poll_interval(fake_hub):
    fake_hub.overrides[("POST", "/condor/device/token")] = hub.Reply(
        400, json.dumps({"error": "slow_down"}).encode()
    )
    hub.device_start()
    interval_before = hub._pending_device["interval"]
    _force_poll()

    state = hub.device_state()

    assert state.status == "pending"
    assert hub._pending_device["interval"] > interval_before


def test_a_successful_poll_stores_the_credential_scope_and_user(fake_hub, cm):
    hub.device_start()
    fake_hub.overrides[("POST", "/condor/device/token")] = _ok(
        {
            "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
            "token_type": "bearer",
            "scope": "agents:read agents:publish",
            "user": {"id": "u_1", "name": "cardosofede"},
        }
    )
    _force_poll()

    state = hub.device_state()

    assert state.status == "linked"
    assert state.profile_handle == "cardosofede"
    assert state.scope == ["agents:read", "agents:publish"]
    assert "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60" not in state.model_dump_json()
    assert hub.token() == "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60"
    assert cm.get_hub()["profile_handle"] == "cardosofede"
    assert cm.get_hub()["scope"] == ["agents:read", "agents:publish"]
    assert cm.get_hub()["user"] == {"id": "u_1", "name": "cardosofede"}
    # The attempt is forgotten: a later read comes straight off config, with
    # no second poll for the now-consumed device_code.
    assert hub._pending_device is None
    calls_before = len(fake_hub.calls)
    assert hub.device_state() == hub.DeviceLinkState(
        status="linked",
        profile_handle="cardosofede",
        scope=["agents:read", "agents:publish"],
    )
    assert len(fake_hub.calls) == calls_before


def test_a_sign_in_with_no_user_identity_still_reads_as_linked(fake_hub, cm):
    """BUG-6: a reply whose ``user`` carries neither ``name`` nor ``id`` still
    stores a real, usable token — judging "linked" on a non-empty
    ``profile_handle`` read that install back as signed out forever even
    though every later call still sent the stored token.
    """
    hub.device_start()
    fake_hub.overrides[("POST", "/condor/device/token")] = _ok(
        {
            "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
            "scope": "agents:read agents:publish",
            "user": {},
        }
    )
    _force_poll()

    state = hub.device_state()

    assert state.status == "linked"
    assert state.profile_handle == ""
    assert hub.token() == "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60"
    assert hub.is_linked() is True
    # A later read, straight off config, must agree — not fall back to
    # "none" because the stored handle is empty.
    calls_before = len(fake_hub.calls)
    assert hub.device_state().status == "linked"
    assert len(fake_hub.calls) == calls_before


def test_a_linked_profile_survives_an_unrelated_save_and_a_fresh_load(fake_hub, cm):
    """ARCH-738's own criterion, now for the keys the device-login flow adds.

    ``CONFIG_SECTIONS["hub"]`` merges whatever keys ``update_hub`` is given,
    so this is really asserting that ``device_state`` wrote all of them
    (rather than only ``token``) and that an unrelated save does not drop
    any.
    """
    import config_manager as cm_module

    hub.device_start()
    fake_hub.overrides[("POST", "/condor/device/token")] = _ok(
        {
            "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
            "scope": "agents:read agents:publish",
            "user": {"id": "u_1", "name": "cardosofede"},
        }
    )
    _force_poll()
    hub.device_state()

    cm.update_hub(**cm.get_hub())  # an unrelated save of the same section

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.get_hub() == {
        "token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
        "token_origin": "https://dev.botcamp.xyz",
        "profile_handle": "cardosofede",
        "scope": ["agents:read", "agents:publish"],
        "user": {"id": "u_1", "name": "cardosofede"},
    }


def test_access_denied_is_reported_once_then_forgotten(fake_hub):
    fake_hub.overrides[("POST", "/condor/device/token")] = hub.Reply(
        400, json.dumps({"error": "access_denied"}).encode()
    )
    hub.device_start()
    _force_poll()

    assert hub.device_state().status == "denied"
    assert hub.device_state().status == "none"


def test_expired_token_is_reported_once_then_forgotten(fake_hub):
    fake_hub.overrides[("POST", "/condor/device/token")] = hub.Reply(
        400, json.dumps({"error": "expired_token"}).encode()
    )
    hub.device_start()
    _force_poll()

    assert hub.device_state().status == "expired"
    assert hub.device_state().status == "none"


def test_invalid_grant_is_reported_as_its_own_distinct_status(fake_hub):
    """``invalid_grant`` is a device code the hub does not recognize — a
    different, actionable state from a denial or a plain expiry, so it gets
    its own status rather than being folded into one of those."""
    fake_hub.overrides[("POST", "/condor/device/token")] = hub.Reply(
        400, json.dumps({"error": "invalid_grant"}).encode()
    )
    hub.device_start()
    _force_poll()

    assert hub.device_state().status == "invalid"
    assert hub.device_state().status == "none"


def test_overlapping_polls_claim_the_slot_before_dialling_out(fake_hub, monkeypatch):
    """BUG-3 case B: two ``device_state()`` calls landing close together must
    never both reach the hub with the same one-time ``device_code`` — the
    loser would see ``invalid_grant`` and misreport a working sign-in as
    failed. The second call here starts while the first is still inside its
    (slowed-down) exchange, and must answer "pending" from the cache rather
    than dialling out a second time.
    """
    hub.device_start()
    _force_poll()

    entered = []
    original_send = hub._send

    def _slow_send(method, url, **kwargs):
        if method == "POST" and url.endswith("/condor/device/token"):
            entered.append(1)
            time.sleep(0.1)
        return original_send(method, url, **kwargs)

    monkeypatch.setattr(hub, "_send", _slow_send)

    results: list[hub.DeviceLinkState] = []

    def _poll():
        results.append(hub.device_state())

    first = threading.Thread(target=_poll)
    first.start()
    time.sleep(0.02)  # let the first call claim the slot and start dialling
    second = threading.Thread(target=_poll)
    second.start()
    first.join()
    second.join()

    assert len(entered) == 1
    assert [r.status for r in results] == ["pending", "pending"]


def test_the_600s_deadline_expires_locally_without_a_further_hub_call(fake_hub):
    """Stop polling at the device-code deadline rather than forever: once the
    deadline has passed, reading the state must not spend a request on an
    attempt the hub has already retired on its own side."""
    hub.device_start()
    hub._pending_device["deadline"] = time.monotonic() - 1
    calls_before = len(fake_hub.calls)

    state = hub.device_state()

    assert state.status == "expired"
    assert hub._pending_device is None
    assert len(fake_hub.calls) == calls_before


def test_restarting_a_device_login_forgets_the_previous_attempt(fake_hub):
    hub.device_start()
    first_device_code = hub._pending_device["device_code"]
    fake_hub.overrides[("POST", "/condor/device/code")] = _ok(
        {
            "device_code": "dev_second",
            "user_code": "ABCD-1234",
            "verification_uri": "https://dev.botcamp.xyz/connect",
            "verification_uri_complete": "https://dev.botcamp.xyz/connect?code=ABCD-1234",
            "expires_in": 600,
            "interval": 5,
        }
    )

    second = hub.device_start()

    # `device_start` hands back a `DeviceStart`, which carries no
    # `device_code` at all — the polling secret stays inside this module
    # (SEC-819), so the swap is read off the tracked attempt.
    assert second.user_code == "ABCD-1234"
    assert second.approval_url == "https://dev.botcamp.xyz/connect"
    assert hub._pending_device["device_code"] == "dev_second" != first_device_code


def test_cancelling_a_pending_device_login_forgets_it_with_no_hub_call(fake_hub):
    """The device-flow contract has no cancel endpoint: an unpolled device
    code just expires at the hub on its own, so cancelling is purely local."""
    hub.device_start()
    calls_before = len(fake_hub.calls)

    hub.device_cancel()

    assert len(fake_hub.calls) == calls_before
    assert hub._pending_device is None
    assert hub.device_state().status == "none"


def test_disconnecting_a_linked_profile_revokes_the_token_then_clears_it_locally(
    fake_hub, cm
):
    """PR #111 gave disconnect something to tell the hub: it must ask first,
    with the presented token, and only then clear the local copy."""
    cm.update_hub(
        token="bc_live_x",
        profile_handle="cardosofede",
        scope=["agents:read"],
        user={"id": "u_1", "name": "cardosofede"},
    )

    result = hub.device_cancel()

    assert fake_hub.calls[-1] == (
        "DELETE",
        f"{hub.HUB_URL}/condor/me",
        "bc_live_x",
        b"",
    )
    assert result == hub.DisconnectResult(revoked=True)
    assert hub.token() == ""
    assert cm.get_hub().get("profile_handle", "") == ""
    assert cm.get_hub().get("scope") == []
    assert cm.get_hub().get("user") == {}


def test_disconnecting_with_nothing_linked_makes_no_hub_call(cm):
    """No token means nothing to revoke — the honest answer is ``None``, not
    a claimed success, and nothing is sent."""
    result = hub.device_cancel()

    assert result == hub.DisconnectResult(revoked=None)
    assert hub.token() == ""


def test_disconnecting_survives_an_unreachable_hub(cm):
    """The revoke attempt reaches `_send`, which the autouse ``no_sockets``
    fixture turns into a transport failure — disconnect must still clear the
    local credential, and must report that the hub was *not* told rather
    than claiming a revoke that did not happen."""
    cm.update_hub(token="bc_live_x", profile_handle="cardosofede")

    result = hub.device_cancel()  # must not raise

    assert result.revoked is False
    assert result.error
    assert hub.token() == ""


def test_disconnecting_when_the_hub_refuses_the_revoke_still_clears_locally(
    fake_hub, cm
):
    cm.update_hub(token="bc_live_x", profile_handle="cardosofede")
    fake_hub.overrides[("DELETE", "/condor/me")] = hub.Reply(500, b"{}")

    result = hub.device_cancel()

    assert result.revoked is False
    assert result.error
    assert hub.token() == ""


def test_a_device_login_start_that_the_hub_refuses_raises_and_tracks_nothing(fake_hub):
    fake_hub.overrides[("POST", "/condor/device/code")] = hub.Reply(500, b"{}")

    with pytest.raises(hub.HubError):
        hub.device_start()

    assert hub._pending_device is None


def test_starting_a_hub_device_login_is_admin_only(cm, fake_hub):
    res = _settings_client(admin=False).post("/settings/hub-device/start")

    assert res.status_code == 403
    assert hub._pending_device is None


def test_polling_the_hub_device_login_is_admin_only(cm, fake_hub):
    res = _settings_client(admin=False).get("/settings/hub-device")

    assert res.status_code == 403


def test_disconnecting_the_hub_device_login_is_admin_only(cm):
    res = _settings_client(admin=False).delete("/settings/hub-device")

    assert res.status_code == 403


# ── Revocation: a 401 from any token-bearing call clears it locally ──────


def test_a_401_during_publish_clears_the_stored_token(traveller, fake_hub, cm):
    hub.set_token("bc_live_revoked")
    fake_hub.overrides[("POST", "/agents")] = hub.Reply(401, b"{}")

    with pytest.raises(hub.HubError):
        hub.publish("traveller", hub.token())

    assert hub.token() == ""


def test_a_401_during_unpublish_clears_the_stored_token(fake_hub, cm):
    hub.set_token("bc_live_revoked")
    fake_hub.overrides[("DELETE", "/agents/listed")] = hub.Reply(401, b"{}")

    with pytest.raises(hub.HubError):
        hub.unpublish("listed", hub.token())

    assert hub.token() == ""


# ── Who the linked token identifies, read live (PR #111's `GET /me`) ─────


def test_me_reads_the_signed_in_account(fake_hub, cm):
    hub.set_token("bc_live_x")

    info = hub.me(hub.token())

    assert fake_hub.calls[-1] == ("GET", f"{hub.HUB_URL}/condor/me", "bc_live_x", b"")
    assert info.user.name == "cardosofede"
    assert info.scopes == ["agents:read", "agents:publish"]


def test_me_without_a_token_raises_without_a_request(fake_hub, cm):
    with pytest.raises(hub.NoTokenError):
        hub.me("")

    assert fake_hub.calls == []


def test_a_401_from_me_clears_the_stored_token(fake_hub, cm):
    hub.set_token("bc_live_revoked")
    fake_hub.overrides[("GET", "/condor/me")] = hub.Reply(401, b"{}")

    with pytest.raises(hub.HubError):
        hub.me(hub.token())

    assert hub.token() == ""


def test_the_hub_me_route_answers_the_live_account(cm, fake_hub):
    hub.set_token("bc_live_x")

    res = _settings_client(admin=True).get("/settings/hub-me")

    assert res.status_code == 200
    body = res.json()
    assert body["user"]["name"] == "cardosofede"
    assert body["scopes"] == ["agents:read", "agents:publish"]
    assert "bc_live_x" not in res.text


def test_the_hub_me_route_says_400_with_no_token(cm, fake_hub):
    res = _settings_client(admin=True).get("/settings/hub-me")

    assert res.status_code == 400
    assert fake_hub.calls == []


def test_the_hub_me_route_propagates_a_401_and_clears_the_token(cm, fake_hub):
    hub.set_token("bc_live_revoked")
    fake_hub.overrides[("GET", "/condor/me")] = hub.Reply(401, b"{}")

    res = _settings_client(admin=True).get("/settings/hub-me")

    assert res.status_code == 401
    assert hub.token() == ""


def test_the_hub_me_route_is_admin_only(cm, fake_hub):
    res = _settings_client(admin=False).get("/settings/hub-me")

    assert res.status_code == 403
    assert fake_hub.calls == []


def test_a_401_during_submission_status_clears_the_stored_token(fake_hub, cm):
    hub.set_token("bc_live_revoked")
    hub._store_submission(
        "traveller", hub.Receipt(submission_id="sub_test", status="pending")
    )
    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(401, b"{}")

    with pytest.raises(hub.HubError):
        hub.submission_status("traveller", hub.token())

    assert hub.token() == ""


def test_the_dashboard_can_start_and_poll_a_device_login_through_to_linked(
    cm, fake_hub
):
    client = _settings_client(admin=True)

    started = client.post("/settings/hub-device/start")
    assert started.status_code == 200
    body = started.json()
    assert body["user_code"] == "WXYZ-4821"
    assert body["approval_url"] == "https://dev.botcamp.xyz/connect"
    assert "access_token" not in body

    pending = client.get("/settings/hub-device")
    assert pending.json()["status"] == "pending"

    fake_hub.overrides[("POST", "/condor/device/token")] = _ok(
        {
            "access_token": "bc_live_super_secret",
            "scope": "agents:read agents:publish",
            "user": {"id": "u_1", "name": "cardosofede"},
        }
    )
    _force_poll()

    linked = client.get("/settings/hub-device")
    assert linked.json() == {
        "status": "linked",
        "user_code": "",
        "approval_url": "",
        "expires_at": 0.0,
        "profile_handle": "cardosofede",
        "scope": ["agents:read", "agents:publish"],
    }
    assert "bc_live_super_secret" not in linked.text


def test_the_dashboard_routes_never_hand_back_a_hostile_approval_url(
    cm, fake_hub, caplog
):
    """SEC-819 at the wire, not just in the module: neither the start route
    nor a subsequent pending poll may carry a URL the hub chose."""
    fake_hub.overrides[("POST", "/condor/device/code")] = _hostile_code()
    client = _settings_client(admin=True)

    with caplog.at_level(logging.WARNING, logger="condor.hub"):
        started = client.post("/settings/hub-device/start")
    assert started.status_code == 200
    assert started.json() == {
        "user_code": "WXYZ-4821",
        "approval_url": "https://dev.botcamp.xyz/connect",
        "expires_in": 600,
        "interval": 5,
    }
    assert "evil.example" not in started.text

    pending = client.get("/settings/hub-device")
    assert pending.json()["status"] == "pending"
    assert pending.json()["approval_url"] == ("https://dev.botcamp.xyz/connect")
    assert "evil.example" not in pending.text

    assert (
        len([r for r in caplog.records if "not this install's hub" in r.message]) == 1
    )


def test_the_dashboard_can_disconnect_a_linked_profile(cm, fake_hub):
    hub.set_token("bc_live_x")
    from config_manager import get_config_manager

    get_config_manager().update_hub(profile_handle="cardosofede")
    client = _settings_client(admin=True)

    res = client.delete("/settings/hub-device")

    assert res.status_code == 200
    assert res.json() == {"status": "none", "revoked": True, "error": ""}
    assert hub.token() == ""


def test_the_dashboard_reports_when_the_hub_could_not_be_told(cm, fake_hub):
    """A disconnect must clear the local credential either way, but it must
    never tell the dashboard the hub was revoked when it was not."""
    hub.set_token("bc_live_x")
    fake_hub.overrides[("DELETE", "/condor/me")] = hub.Reply(500, b"{}")
    client = _settings_client(admin=True)

    res = client.delete("/settings/hub-device")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "none"
    assert body["revoked"] is False
    assert body["error"]
    assert hub.token() == ""


@pytest.fixture
def offered(traveller, fake_hub):
    """``traveller`` published to the fake hub, under its own slug, and the
    agent gone from disk.

    Removed so the install is a *create*: the test that wants the update verb
    leaves it in place instead.
    """
    import shutil

    data = _zip_agent("traveller")
    listing = fake_hub.offer("traveller", data, name="Traveller")
    shutil.rmtree(traveller)
    return listing


def test_a_listing_this_install_does_not_have_is_an_install(offered):
    preview = _client().post("/hub/agents/traveller/install/preview")

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
    ab.build("traveller")  # mints and stamps this agent's agent_uid on disk
    fake_hub.offer("traveller", _zip_agent("traveller"))

    body = _client().post("/hub/agents/traveller/install/preview").json()

    assert body["verb"] == "update"
    # FEAT-145's ``UpdatePlan`` shape, which the import plan does not have.
    assert "blockers" in body["plan"] and "counts" in body["plan"]
    installed_uid = AgentStore().get("traveller").agent_uid
    assert installed_uid
    assert body["plan"]["installed_agent_uid"] == installed_uid


def test_the_preview_and_the_install_carry_the_same_plan(offered, fake_hub):
    client = _client()

    preview = client.post("/hub/agents/traveller/install/preview").json()
    installed = client.post("/hub/agents/traveller/install").json()

    for field, value in preview["plan"].items():
        assert installed["plan"][field] == value, field
    assert installed["plan"]["imported"] is True
    # Two fetches, not one cached preview: the digest check is what makes that
    # safe, and it ran on both.
    assert sum(1 for call in fake_hub.calls if "/versions/" in call.url) == 2
    # And one listing GET apiece, not two: both the preview and the install
    # ask the hub fresh for the catalogue entry.
    assert (
        sum(1 for call in fake_hub.calls if call.url.endswith("/agents/traveller")) == 2
    )


def test_the_preview_returns_what_version_and_digest_it_reviewed(offered, fake_hub):
    body = _client().post("/hub/agents/traveller/install/preview").json()

    row = fake_hub.listings["traveller"]["versions"][0]
    assert body["fetched"] == {"version": 1, "sha256": row["sha256"]}


def test_install_refuses_when_botcamp_approved_a_newer_version_since_preview(
    offered, fake_hub
):
    """BUG-2: a frontend that always asks for "latest" can have Botcamp
    approve v2 between the preview and the install call. Pinning install to
    the preview's own digest turns that race into a refusal, not a silent
    upgrade to code nobody reviewed.
    """
    client = _client()
    preview = client.post("/hub/agents/traveller/install/preview").json()
    reviewed_sha256 = preview["fetched"]["sha256"]

    # ``offered`` already removed the agent's library from disk (so the
    # install is a *create*), so a v2 bundle has to be built from scratch —
    # the same files plus the one a reviewer never saw.
    home = agent_home("traveller")
    _write(home / "AGENT.md", AGENT_MD)
    _write(home / "skills" / "s" / "SKILL.md", SKILL_MD)
    _write(home / "routines" / "r.py", ROUTINE_PY)
    _write(home / "routines" / "added_in_v2.py", ROUTINE_PY)
    fake_hub.offer("traveller", _zip_agent("traveller"), version=2)
    import shutil

    shutil.rmtree(home)

    res = client.post(
        "/hub/agents/traveller/install",
        json={"version": "latest", "expected_sha256": reviewed_sha256},
    )

    assert res.status_code == 409
    assert "changed since it was reviewed" in res.json()["detail"]
    assert not agent_home("traveller").exists()


def test_install_succeeds_when_the_pinned_digest_still_matches(offered, fake_hub):
    client = _client()
    preview = client.post("/hub/agents/traveller/install/preview").json()
    reviewed_version = preview["fetched"]["version"]
    reviewed_sha256 = preview["fetched"]["sha256"]

    res = client.post(
        "/hub/agents/traveller/install",
        json={"version": str(reviewed_version), "expected_sha256": reviewed_sha256},
    )

    assert res.status_code == 200
    assert res.json()["plan"]["imported"] is True


def test_an_installed_hub_agent_arrives_muted_with_nothing_running(offered):
    from condor.agents.engine import get_all_engines

    res = _client().post("/hub/agents/traveller/install")

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

    _client().post("/hub/agents/traveller/install")

    source = agent_home("traveller") / "routines" / "r.py"
    assert not code_trust.allowed("traveller", "r", source.read_text())


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/hub/catalogue"),
        ("get", "/hub/agents/traveller"),
        ("post", "/hub/agents/traveller/install/preview"),
        ("post", "/hub/agents/traveller/install"),
    ],
)
def test_a_seat_reads_the_hub_without_the_admins_token(
    offered, fake_hub, cm, method, path
):
    """SEC review C4: the one token carries whatever the admin's Botcamp
    account bought or joined, so only the admin's reads send it."""
    hub.set_token("botcamp-tok")
    before = len(fake_hub.calls)

    getattr(_client(admin=False), method)(path)

    sent = fake_hub.calls[before:]
    assert sent, "the route reached the hub"
    assert {call.token for call in sent} == {""}


def test_the_admins_reads_still_carry_the_token(offered, fake_hub, cm):
    hub.set_token("botcamp-tok")
    before = len(fake_hub.calls)

    _client(admin=True).post("/hub/agents/traveller/install/preview")

    assert {call.token for call in fake_hub.calls[before:]} == {"botcamp-tok"}


def test_a_seat_installs_from_chat_without_the_admins_token(
    offered, fake_hub, cm, monkeypatch
):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", SEAT_ID, raising=False)
    hub.set_token("botcamp-tok")
    before = len(fake_hub.calls)

    trading_agent.manage_agents(action="hub_install", path="traveller")

    sent = fake_hub.calls[before:]
    assert sent and {call.token for call in sent} == {""}


def test_every_hub_surface_carries_the_one_trust_sentence(offered):
    body = _client().get("/hub/agents/traveller").json()

    assert "not a security boundary" in body["trust"]
    assert "muted and unstarted" in body["trust"]


# ── Hub-listing status for an installed agent (CORR-812) ─────────────────
#
# An install that downloaded a listing has no local record of it at all
# before this item — these tests cover both halves: that installing from the
# hub actually writes the record, and that a later check of it tells
# "the hub confirmed this is gone" apart from "the hub could not be reached,"
# never claiming the second as the first.


def test_installing_from_the_hub_records_its_provenance_outside_the_bundle(
    offered, fake_hub
):
    _client().post("/hub/agents/traveller/install")

    home = agent_home("traveller")
    stamp = ab.read_stamp(home)
    assert stamp.hub_slug == "traveller"
    assert stamp.hub_version == 1
    assert stamp.hub_digest == fake_hub.listings["traveller"]["versions"][0]["sha256"]
    assert stamp.hub_status == "ok"
    assert stamp.hub_checked_at

    # Outside publishable_files, so a re-export of this very agent cannot
    # carry somebody else's listing as its own provenance.
    bundle = ab.build("traveller")
    assert "hub_slug" not in ab.render(bundle)


def test_a_plain_file_update_does_not_invent_hub_provenance(traveller):
    """``apply_update`` is shared with the plain file-upload path, which has
    no hub agent at all — it must not fabricate one."""
    original = ab.parse(_bundle_text().encode("utf-8"))
    bumped = original.model_copy(update={"version": original.version + 1})
    plan = ab.plan_update(bumped, "traveller")

    ab.apply_update(plan, user_id=7)

    stamp = ab.read_stamp(agent_home("traveller"))
    assert stamp.hub_slug == ""
    assert stamp.hub_status == ""


def test_listing_status_reads_unchanged_as_ok(offered, fake_hub):
    result = hub.listing_status("traveller", 1)

    assert result.state == "ok"


def test_listing_status_reads_a_superseded_version_as_delisted(offered, fake_hub):
    """The installed version no longer among the hub's approved ones."""
    fake_hub.listings["traveller"]["versions"][0]["version"] = 2
    fake_hub.listings["traveller"]["latest"] = 2

    result = hub.listing_status("traveller", 1)

    assert result.state == "delisted"


def test_listing_status_reads_a_404_as_delisted(offered, fake_hub):
    fake_hub.overrides[("GET", "/agents/traveller")] = hub.Reply(
        404, b'{"detail": "no approved version"}'
    )

    result = hub.listing_status("traveller", 1)

    assert result.state == "delisted"


def test_listing_status_reads_a_network_failure_as_unknown(offered, fake_hub):
    fake_hub.overrides[("GET", "/agents/traveller")] = ConnectionError("down")

    result = hub.listing_status("traveller", 1)

    assert result.state == "unknown"


def test_listing_status_reads_a_5xx_as_unknown_not_delisted(offered, fake_hub):
    fake_hub.overrides[("GET", "/agents/traveller")] = hub.Reply(
        503, b'{"detail": "maintenance"}'
    )

    result = hub.listing_status("traveller", 1)

    assert result.state == "unknown"


def test_listing_status_reads_an_access_refusal_as_unknown_not_delisted(
    offered, fake_hub
):
    """401/402/403 say this install cannot currently read the agent, never
    that the hub confirmed it is gone."""
    fake_hub.overrides[("GET", "/agents/traveller")] = hub.Reply(
        402, b'{"detail": "membership required"}'
    )

    result = hub.listing_status("traveller", 1)

    assert result.state == "unknown"


def test_listing_status_refuses_a_malicious_id_before_any_request(fake_hub):
    with pytest.raises(IdentifierError):
        hub.listing_status("../agents", 1)

    assert fake_hub.calls == []


def test_listing_status_reads_a_malformed_reply_as_unknown(offered, fake_hub):
    fake_hub.overrides[("GET", "/agents/traveller")] = hub.Reply(200, b"not json")

    result = hub.listing_status("traveller", 1)

    assert result.state == "unknown"


def test_installed_agent_status_is_untracked_without_hub_provenance(traveller):
    assert hub.installed_agent_status("traveller") == {"tracked": False}


def test_installed_agent_status_confirms_and_persists_delisted(offered, fake_hub):
    _client().post("/hub/agents/traveller/install")
    fake_hub.overrides[("GET", "/agents/traveller")] = hub.Reply(
        404, b'{"detail": "gone"}'
    )

    status = hub.installed_agent_status("traveller", force=True)

    assert status["tracked"] is True
    assert status["slug"] == "traveller"
    assert status["status"] == "delisted"
    assert status["live_check_failed"] is False
    assert status["checked_at"]
    # Persisted, not just returned — a reload must see it without a new call.
    assert ab.read_stamp(agent_home("traveller")).hub_status == "delisted"


def test_installed_agent_status_does_not_overwrite_on_an_unknown_check(
    offered, fake_hub
):
    """A transient failure must not erase the last confirmed answer, and must
    not touch any installed file or execution state."""
    _client().post("/hub/agents/traveller/install")
    confirmed_at = ab.read_stamp(agent_home("traveller")).hub_checked_at
    fake_hub.overrides[("GET", "/agents/traveller")] = ConnectionError("down")

    status = hub.installed_agent_status("traveller", force=True)

    assert status["status"] == "ok"
    assert status["live_check_failed"] is True
    assert status["checked_at"] == confirmed_at
    stamp = ab.read_stamp(agent_home("traveller"))
    assert stamp.hub_status == "ok"
    assert stamp.hub_checked_at == confirmed_at


def test_installed_agent_status_does_not_recheck_within_the_cache_window(
    offered, fake_hub
):
    _client().post("/hub/agents/traveller/install")
    calls_before = len(fake_hub.calls)

    hub.installed_agent_status("traveller")

    assert len(fake_hub.calls) == calls_before


def test_the_hub_status_route_answers_untracked_for_a_file_imported_agent(traveller):
    res = _client().get("/agents/traveller/hub-status")

    assert res.status_code == 200
    assert res.json() == {"tracked": False}


def test_the_hub_status_route_is_open_to_any_authenticated_user(offered, fake_hub):
    """Unlike publish/submission, this reads no credential — it is a fact
    about one installed agent, the same gate get/preview/install use."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    _client().post("/hub/agents/traveller/install")

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.hub_router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=SEAT_ID, username="u", first_name="U", role="user"
    )
    res = TestClient(app).get("/agents/traveller/hub-status")

    assert res.status_code == 200
    assert res.json()["tracked"] is True


def test_the_hub_status_route_answers_404_for_an_agent_that_does_not_exist():
    res = _client().get("/agents/nobody/hub-status")

    assert res.status_code == 404


# ── The routes' own refusals ─────────────────────────────────────────────


def test_the_catalogue_route_lists_one_page_with_the_trust_sentence(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue(_entry("grid-bot"))

    body = _client().get("/hub/catalogue", params={"query": "grid", "page": 2}).json()

    assert [entry["slug"] for entry in body["listings"]] == ["grid-bot"]
    assert body["page"] == 2
    # Links a browser follows are built from the configured hub, not the reply.
    origin = hub.site_url("")
    assert body["listings"][0]["page_url"] == f"{origin}/strategies/grid-bot"
    assert body["botcamp_url"] == f"{origin}/strategies?type=AGENT"
    assert "not a security boundary" in body["trust"]
    assert fake_hub.calls[0].url == f"{hub.HUB_URL}/agents?query=grid&page=2"


def test_a_catalogue_link_never_comes_from_the_reply(fake_hub):
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("grid-bot", page_url="https://evil.example/phish")
    )

    body = _client().get("/hub/catalogue").json()

    assert body["listings"][0]["page_url"] == f"{hub.site_url('')}/strategies/grid-bot"


def test_a_catalogue_page_out_of_range_is_a_400_and_nothing_is_sent(fake_hub):
    res = _client().get("/hub/catalogue", params={"page": 0})

    assert res.status_code == 400
    assert fake_hub.calls == []


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

    res = _client().get("/hub/agents/x")
    assert res.status_code == 502
    assert "Could not reach the agent hub" in res.json()["detail"]


def test_a_missing_listing_keeps_the_hubs_404(fake_hub):
    assert _client().get("/hub/agents/nothing").status_code == 404


def test_the_hub_cannot_serve_something_that_is_not_a_bundle(fake_hub):
    """A wrong-kind-of-file refusal (``NotJsonError``) is 415, like the file
    import door's own ``_bundle_refusal`` convention."""
    fake_hub.offer("listed", b"not a zip at all")

    res = _client().post("/hub/agents/listed/install")

    assert res.status_code == 415
    assert "zip archive" in res.json()["detail"]


def test_a_tampered_bundle_never_reaches_the_importer(traveller, fake_hub):
    """The digest check, through the route — a 502, and nothing written."""
    data = _zip_agent("traveller")
    fake_hub.offer("listed", data)
    fake_hub.bundles[("listed", 1)] = data + b"\x00"
    import shutil

    shutil.rmtree(traveller)

    res = _client().post("/hub/agents/listed/install")

    assert res.status_code == 502
    assert "X-Agent-Sha256" in res.json()["detail"]
    assert not agent_home("traveller").exists()


def test_the_publish_route_answers_a_receipt(traveller, fake_hub):
    hub.set_token("botcamp-tok")

    res = _client().post("/agents/traveller/publish", json={"notes": "first cut"})

    assert res.status_code == 200
    assert res.json()["published"] is False  # pending is not published (FEAT-150)
    assert res.json()["receipt"]["submission_id"] == "sub_test"
    assert res.json()["receipt"]["status"] == "pending"
    assert fake_hub.calls[-1].body["notes"] == "first cut"
    assert "botcamp-tok" not in res.text


def test_the_submission_route_answers_null_before_any_publish(traveller, fake_hub):
    assert _client().get("/agents/traveller/submission").json() == {"receipt": None}
    assert fake_hub.calls == []  # nothing on file, nothing to poll


def test_the_submission_route_polls_while_pending_and_stops_once_decided(
    traveller, fake_hub
):
    """The route's own half of "stop on a terminal state": once the stored
    record is a verdict, a second read costs no hub call at all.
    """
    hub.set_token("botcamp-tok")
    _client().post("/agents/traveller/publish")

    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(
        200,
        json.dumps(
            {
                "submission_id": "sub_test",
                "status": "published",
                "submitted_at": "2026-10-21T12:03:00Z",
                "decided_at": "2026-10-21T14:40:00Z",
                "listing_id": "lst_8f3a9c2e1b",
            }
        ).encode("utf-8"),
    )
    first = _client().get("/agents/traveller/submission")
    assert first.json()["receipt"]["status"] == "published"

    calls_after_first_poll = len(fake_hub.calls)
    second = _client().get("/agents/traveller/submission")
    assert second.json()["receipt"]["status"] == "published"
    assert len(fake_hub.calls) == calls_after_first_poll  # no re-poll


def test_the_submission_route_is_admin_only(traveller, fake_hub):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    hub.set_token("botcamp-tok")
    _client().post("/agents/traveller/publish")

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=SEAT_ID, username="u", first_name="U", role="user"
    )

    assert TestClient(app).get("/agents/traveller/submission").status_code == 403


def test_the_publish_route_says_400_when_no_token_is_set(traveller, fake_hub):
    """Not 502: nothing was sent, so there is no upstream to blame.

    Reported as the hub being down is how an operator spends an afternoon
    checking a service that is fine.
    """
    res = _client().post("/agents/traveller/publish")

    assert res.status_code == 400
    assert "Agent Hub" in res.json()["detail"]
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


def test_manage_agents_gets_one_hub_agent(fake_hub):
    from mcp_servers.condor.tools import trading_agent

    fake_hub.offer("listed", b"zip-bytes", name="A")

    result = trading_agent.manage_agents(action="hub_get", path="listed")

    assert result["listing"]["slug"] == "listed"
    assert result["listing"]["name"] == "A"
    assert "not a security boundary" in result["trust"]


def test_manage_agents_installs_from_the_hub(offered, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)

    result = trading_agent.manage_agents(action="hub_install", path="traveller")

    assert result["verb"] == "install"
    assert result["imported"] is True
    assert result["listing"]["slug"] == "traveller"
    assert load_mutes("traveller")["routines"] == {"r"}


def test_manage_agents_hub_install_records_provenance(offered, fake_hub, monkeypatch):
    """BUG-4: the chat door's install must stamp the same provenance the web
    route does, or ``installed_agent_status`` never learns to watch this
    agent for delisting."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)

    trading_agent.manage_agents(action="hub_install", path="traveller")

    stamp = ab.read_stamp(agent_home("traveller"))
    assert stamp.hub_slug == "traveller"
    assert stamp.hub_version == 1
    assert stamp.hub_status == "ok"


def test_manage_agents_hub_update_records_provenance(traveller, fake_hub, monkeypatch):
    """Same stamp, taken on the update verb (the agent already installed)."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    ab.build("traveller")  # mints and stamps this agent's agent_uid on disk
    fake_hub.offer("traveller", _zip_agent("traveller"), version=2)
    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)

    result = trading_agent.manage_agents(action="hub_install", path="traveller")

    assert result["verb"] == "update"
    stamp = ab.read_stamp(agent_home("traveller"))
    assert stamp.hub_slug == "traveller"
    assert stamp.hub_version == 2


def test_manage_agents_hub_install_needs_a_slug(fake_hub):
    from mcp_servers.condor.tools import trading_agent

    assert "required" in trading_agent.manage_agents(action="hub_install")["error"]
    assert fake_hub.calls == []


def test_manage_agents_publishes_to_the_hub(traveller, fake_hub, cm, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)
    hub.set_token("botcamp-tok")

    result = trading_agent.manage_agents(
        action="publish_hub", agent_slug="traveller", notes="first cut"
    )

    assert result["published"] is False  # pending is not published (FEAT-150)
    assert result["status"] == "pending"
    assert result["submission_id"] == "sub_test"
    assert fake_hub.calls[-1].token == "botcamp-tok"


def test_manage_agents_publish_without_a_token_is_an_error_not_a_raise(
    traveller, fake_hub, monkeypatch
):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)
    result = trading_agent.manage_agents(action="publish_hub", agent_slug="traveller")

    assert "Agent Hub" in result["error"]
    assert fake_hub.calls == []


def test_manage_agents_publish_hub_is_admin_only(traveller, fake_hub, cm, monkeypatch):
    """SEC review C1: the tool's "ADMIN ONLY" was read only by the model."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", SEAT_ID, raising=False)
    hub.set_token("botcamp-tok")

    result = trading_agent.manage_agents(action="publish_hub", agent_slug="traveller")

    assert "admin-only" in result["error"]
    assert fake_hub.calls == []


def test_the_hub_actions_are_routed_and_not_misfiled(fake_hub):
    """A sibling family's action answers with the call that would have worked."""
    from mcp_servers.condor.tools import trading_agent

    misrouted = trading_agent.manage_loops(action="hub_get")

    assert "manage_agents" in misrouted["error"]


def test_fetch_is_addressed_only_by_what_the_caller_gave(traveller, fake_hub):
    """``fetch`` never consults ``get``'s reply at all.

    Unlike the abandoned ``listing_id`` contract, there is no second value in
    PR #111's wire shape a hub answer could redirect a download with:
    ``fetch``'s URL is built from the caller's own ``slug``/``version`` alone,
    and the integrity check is the *same* response's own header, never a
    field read out of an earlier call.
    """
    data = _zip_agent("traveller")
    fake_hub.offer("listed", data)
    fake_hub.listings["listed"]["slug"] = "../../admin"

    fetched = hub.fetch("listed")

    assert fetched.data == data
    assert not any("/admin" in call.url for call in fake_hub.calls)


# ── Possible keys wait for a person, on every door (SEC review C2) ───────
#
# A bare 64-hex is an EVM key as often as a tx hash. Export and publish stop on
# it until someone acknowledges it by fingerprint; the acknowledgement rides to
# Botcamp, which runs the same check, as ``acknowledged_secrets``.

_MAYBE_KEY = "4c0883a69102937d6231471b5dbb6204fe512961708279f1b7e0f4e6a6b01f4c"


@pytest.fixture
def keyish(traveller):
    _write(traveller / "routines" / "r.py", ROUTINE_PY + f'\nTX = "{_MAYBE_KEY}"\n')
    from condor.agents import bundle_review

    return bundle_review.fingerprint(_MAYBE_KEY)


def test_chat_export_lists_possible_keys_and_takes_an_acknowledgement(keyish):
    from mcp_servers.condor.tools import trading_agent

    stopped = trading_agent.manage_agents(action="export", agent_slug="traveller")

    assert "may be a private key" in stopped["error"]
    assert [k["fingerprint"] for k in stopped["possible_keys"]] == [keyish]
    assert _MAYBE_KEY not in json.dumps(stopped)
    assert "never acknowledge one yourself" in stopped["next"]

    done = trading_agent.manage_agents(
        action="export", agent_slug="traveller", acknowledge=[keyish]
    )
    assert done["exported"] is True


def test_chat_publish_stops_before_sending_then_carries_the_acknowledgement(
    keyish, fake_hub, cm, monkeypatch
):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)
    hub.set_token("botcamp-tok")

    stopped = trading_agent.manage_agents(action="publish_hub", agent_slug="traveller")
    assert stopped["possible_keys"][0]["fingerprint"] == keyish
    assert fake_hub.calls == []

    trading_agent.manage_agents(
        action="publish_hub", agent_slug="traveller", acknowledge=[keyish]
    )
    body = fake_hub.calls[-1].body
    assert body["acknowledged_secrets"] == [keyish]


def test_publish_sends_only_acknowledgements_the_bundle_carries(keyish, fake_hub, cm):
    hub.set_token("botcamp-tok")

    hub.publish(
        "traveller", "botcamp-tok", acknowledged=[keyish, "sha256:ffffffffffffffff"]
    )

    assert fake_hub.calls[-1].body["acknowledged_secrets"] == [keyish]


def test_a_clean_publish_acknowledges_nothing(traveller, fake_hub, cm):
    hub.publish("traveller", "botcamp-tok")

    assert fake_hub.calls[-1].body["acknowledged_secrets"] == []


def test_the_export_route_answers_409_with_the_list_then_takes_it(keyish):
    client = _client()

    stopped = client.get("/agents/traveller/bundle")
    assert stopped.status_code == 409
    assert "may be a private key" in stopped.json()["detail"]
    assert stopped.json()["possible_keys"][0]["fingerprint"] == keyish
    assert _MAYBE_KEY not in stopped.text

    assert (
        client.get(f"/agents/traveller/bundle?acknowledge={keyish}").status_code == 200
    )


def test_the_publish_route_answers_409_with_the_list_then_takes_it(
    keyish, fake_hub, cm
):
    hub.set_token("botcamp-tok")
    client = _client()

    stopped = client.post("/agents/traveller/publish", json={})
    assert stopped.status_code == 409
    assert fake_hub.calls == []

    sent = client.post("/agents/traveller/publish", json={"acknowledge": [keyish]})
    assert sent.status_code == 200
    assert fake_hub.calls[-1].body["acknowledged_secrets"] == [keyish]
