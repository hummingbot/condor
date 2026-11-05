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
import urllib.parse
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
def _real_hub_url(monkeypatch):
    """Pin :data:`hub.HUB_URL` back to the compiled-in origin for this file.

    ``hub._dev_override()`` runs once, at ``condor.hub``'s first import, off
    whatever ``os.environ`` holds at that moment. In a full-suite run some
    earlier-collected module can import ``utils.config`` — which calls
    ``load_dotenv()`` unconditionally at import time — before ``condor.hub``
    is first imported, so a developer's local ``.env`` (``CONDOR_HUB_DEV_MODE=1``,
    set exactly as CORR-929's own notes instruct, to test against
    ``dev.botcamp.xyz``) silently redirects ``HUB_URL`` for the rest of the
    process, regardless of this test file's own environment. Resetting it
    here, rather than only in ``fake_hub``, protects every assertion in this
    file that compares against the production origin without going through
    that fixture.
    """
    monkeypatch.setattr(hub, "HUB_URL", hub.COMPILED_HUB_URL)


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
        #: ``(slug, version)`` -> ``{"acceptedAt": ..., "termsVersion": ...}``,
        #: once accepted. Botcamp-site PR #137's own keying and idempotency:
        #: first acceptance wins, a second POST answers the same row.
        self.consents: dict[tuple[str, int], dict] = {}
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
            # The strategy id Condor now pins consent to (botcamp-site PR
            # #137, commit 2333769262) — stable across a slug rename, unlike
            # the slug itself. A caller wanting a specific one passes
            # ``agentId=...``.
            "agentId": fields.pop("agentId", f"id-{slug}"),
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
        route_path, _, query = path.partition("?")
        override = self.overrides.get((method, route_path))
        if isinstance(override, BaseException):
            raise hub.HubError(f"Could not reach the agent hub: {override}", status=0)
        if isinstance(override, hub.Reply):
            return override
        return self._route(method, route_path, payload, query)

    def _route(
        self, method: str, path: str, payload: bytes = b"", query: str = ""
    ) -> hub.Reply:
        # Consent (botcamp-site PR #137) — checked before the plain
        # bundle-download branch below, since both start with "/agents/"
        # and contain "/versions/" and this one has a "/consent" suffix the
        # other branch would otherwise misparse as part of the version.
        if (
            method in ("GET", "POST")
            and path.startswith("/agents/")
            and path.endswith("/consent")
        ):
            rest = path[len("/agents/") : -len("/consent")]
            slug, _, version_seg = rest.partition("/versions/")
            listing = self.listings.get(slug)
            if not listing:
                return _missing()
            resolved = (
                listing["latest"] if version_seg == "latest" else int(version_seg)
            )
            row = next(
                (v for v in listing["versions"] if v["version"] == resolved), None
            )
            if row is None:
                return _missing()
            # Pinned to the agent id and sha256 Condor sends (botcamp-site PR
            # #137, commit 2333769262): a GET carries them as query params, a
            # POST in its JSON body — both required, and checked against what
            # this slug/version resolve to *now*, same as the real route.
            sent = (
                dict(urllib.parse.parse_qsl(query))
                if method == "GET"
                else (json.loads(payload.decode()) if payload else {})
            )
            sent_agent_id = sent.get("agentId")
            sent_sha256 = sent.get("sha256")
            if not sent_agent_id or not sent_sha256:
                return hub.Reply(
                    400,
                    json.dumps(
                        {"error": "`agentId` and `sha256` are required"}
                    ).encode(),
                )
            if sent_agent_id != listing["agentId"] or (
                sent_sha256.lower() != row["sha256"].lower()
            ):
                return hub.Reply(
                    409,
                    json.dumps(
                        {
                            "error": "This version's content isn't the one "
                            "you reviewed — review it again"
                        }
                    ).encode(),
                )
            key = (slug, resolved)
            first_acceptance = method == "POST" and key not in self.consents
            if first_acceptance:
                self.consents[key] = {
                    "acceptedAt": "2026-10-21T12:03:00Z",
                    "termsVersion": sent.get("termsVersion"),
                }
            consent = self.consents.get(key)
            return hub.Reply(
                201 if first_acceptance else 200,
                json.dumps(
                    {
                        "data": {
                            "agentId": listing["agentId"],
                            "slug": slug,
                            "version": resolved,
                            "sha256": row["sha256"],
                            "accepted": consent is not None,
                            "acceptedAt": consent["acceptedAt"] if consent else None,
                            "termsVersion": (
                                consent["termsVersion"] if consent else None
                            ),
                        }
                    }
                ).encode("utf-8"),
            )
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
        # ── Connect Botcamp: redeem a pasted code (botcamp-site PR #137) —
        # a sane default that succeeds; a test wanting a specific error
        # (slow_down/too_many_connections/invalid_grant) sets an
        # ``overrides`` entry for the exact path instead.
        if method == "POST" and path == "/condor/connect-code/redeem":
            return _ok(
                {
                    "access_token": "bc_live_test_default",
                    "token_type": "bearer",
                    "scope": "agents:read agents:publish",
                    "expires_in": 7776000,
                    "user": {"id": "u_1", "name": "cardosofede"},
                }
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
HUB_BASE = "https://www.botcamp.xyz/api"


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
    assert done.stdout.strip() == "https://www.botcamp.xyz/api"
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
    assert done.stdout.strip() == "https://www.botcamp.xyz/api"


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


def test_a_zip_revision_with_no_stamped_uid_keeps_its_identity_across_content_changes(
    traveller,
):
    """A Botcamp zip that carries no ``agent_uid`` of its own must still
    resolve to the *same* agent on its next revision, even though that
    revision's files differ — the whole point of the field. Hashing the
    zip's own content (the way a local export without a prior stamp mints
    one) would instead mint a new identity every time anything in the
    archive changed, so a pull of the next revision would read as a
    different agent colliding on the installed slug rather than an update
    of it (Greptile's review of PR #111's integration)."""
    import io
    import zipfile

    from condor.layering import publishable_files

    def _zip_without_uid_stamp(home: Path) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for rel in publishable_files(home):
                zf.writestr(rel.as_posix(), (home / rel).read_bytes())
        return buf.getvalue()

    v1 = botcamp_import.from_zip(
        _zip_without_uid_stamp(traveller), agent_slug="traveller"
    )
    assert v1.agent.agent_uid

    _write(traveller / "skills" / "s" / "SKILL.md", SKILL_MD + "\nMore.\n")
    v2 = botcamp_import.from_zip(
        _zip_without_uid_stamp(traveller), agent_slug="traveller"
    )

    assert v2.agent.agent_uid == v1.agent.agent_uid


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


def test_publish_reviews_the_bundle_only_once(traveller, fake_hub, monkeypatch):
    """BUG-10: build() already runs bundle_review.review over these files;
    publish() used to run it again just to recompute acknowledged_secrets."""
    from condor.agents import bundle_review

    calls = []
    original = bundle_review.review

    def _counting(bundle):
        calls.append(1)
        return original(bundle)

    monkeypatch.setattr(bundle_review, "review", _counting)

    hub.publish("traveller", "tok")

    assert len(calls) == 1


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
    # BUG-8: badge provenance (_published_listing_provenance) reads the
    # catalogue (GET /agents?query=&page=), the only PR #111 shape that
    # actually carries a badge — the pull side's AgentListing has none.
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("lst_8f3a9c2e1b", badge=hub.VALIDATED)
    )
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


def test_submission_status_also_picks_up_the_published_date(traveller, fake_hub, cm):
    """BUG-8: the Publish panel wants ``published_at`` too, not just the
    badge — both live only on the catalogue entry."""
    hub.publish("traveller", "tok")
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("lst_1", badge=hub.VALIDATED, published_at="2026-10-20T00:00:00Z")
    )
    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(
        200,
        json.dumps(
            {
                "submission_id": "sub_test",
                "status": "published",
                "listing_id": "lst_1",
            }
        ).encode("utf-8"),
    )

    receipt = hub.submission_status("traveller", "tok")

    assert receipt.published_at == "2026-10-20T00:00:00Z"


def test_published_listing_provenance_sends_the_token_and_matches_the_exact_slug(
    fake_hub,
):
    """A members/purchase-gated listing needs the caller's token to resolve
    at all (SEC review C4), and a fuzzy catalogue search can plausibly
    return more than the one row — the match has to be on the slug, not on
    being first in the list."""
    fake_hub.overrides[("GET", "/agents")] = _catalogue(
        _entry("other-bot", badge=hub.VALIDATED, published_at="2026-01-01T00:00:00Z"),
        _entry("traveller", badge="", published_at="2026-10-20T00:00:00Z"),
    )

    badge, published_at = hub._published_listing_provenance(
        "traveller", token="a-token"
    )

    assert (badge, published_at) == ("", "2026-10-20T00:00:00Z")
    assert fake_hub.calls[-1].token == "a-token"


def test_published_listing_provenance_degrades_to_no_badge_when_absent(fake_hub):
    """Not (yet) indexed, or gated behind a membership the token does not
    carry — this must not raise, only answer with nothing."""
    fake_hub.overrides[("GET", "/agents")] = _catalogue()

    assert hub._published_listing_provenance("traveller") == ("", "")


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


def test_unpublish_clears_the_stored_submission_so_it_stops_reading_published(
    traveller, fake_hub, cm
):
    """``GET /{slug}/submission`` only re-polls the hub while a record reads
    ``pending`` (§5.4's own cost-saving) — a ``published`` record never
    changes on the hub's side on its own. Without clearing this install's own
    copy, a successful withdrawal would leave the dashboard reading
    "Published" and offering another withdrawal forever (Greptile's review of
    PR #111's integration)."""
    hub.publish("traveller", "tok")
    fake_hub.overrides[("GET", "/submissions/sub_test")] = hub.Reply(
        200,
        json.dumps(
            {
                "submission_id": "sub_test",
                "status": "published",
                "submitted_at": "2026-10-21T12:03:00Z",
                "decided_at": "2026-10-21T14:40:00Z",
                "listing_id": "traveller",
            }
        ).encode("utf-8"),
    )
    hub.submission_status("traveller", "tok")
    assert hub.local_submission("traveller").status == "published"

    hub.unpublish("traveller", "tok")

    assert hub.local_submission("traveller") is None


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
        "token_origin": "https://www.botcamp.xyz",
    }


def test_a_token_is_only_sent_to_the_hub_that_issued_it(cm, monkeypatch):
    """SEC review C5: turning the dev override on must not hand a production
    token to a LAN or dev host."""
    hub.set_token("bc_prod_token")
    assert hub.token() == "bc_prod_token"

    monkeypatch.setattr(hub, "HUB_URL", "http://masiva.local:3000/api")
    assert hub.token() == ""
    assert hub.connection_state().status != "linked"

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


# ── Connect Botcamp: redeem a pasted code (botcamp-site PR #137) ─────────


def test_redeeming_sends_no_credential_and_links_on_success(fake_hub, cm):
    state = hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert fake_hub.calls[-1].method == "POST"
    assert fake_hub.calls[-1].url == f"{hub.HUB_URL}/condor/connect-code/redeem"
    assert fake_hub.calls[-1].token == ""
    assert fake_hub.calls[-1].body == {
        "code": "WXYZ-WXYZ-WXYZ",
        "client_name": "Condor",
    }
    assert state.status == "linked"
    assert state.profile_handle == "cardosofede"
    assert state.scope == ["agents:read", "agents:publish"]
    assert hub.token() == "bc_live_test_default"


def test_the_code_is_stripped_but_otherwise_sent_as_typed(fake_hub):
    hub.redeem_connect_code("  wxyz-wxyz-wxyz  ")

    assert fake_hub.calls[-1].body["code"] == "wxyz-wxyz-wxyz"


def test_an_empty_code_is_refused_without_a_request(fake_hub):
    with pytest.raises(hub.HubError):
        hub.redeem_connect_code("   ")

    assert fake_hub.calls == []


def test_a_successful_redeem_stores_the_credential_scope_and_user(fake_hub, cm):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = _ok(
        {
            "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
            "token_type": "bearer",
            "scope": "agents:read agents:publish",
            "user": {"id": "u_1", "name": "cardosofede"},
        }
    )

    state = hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert state.status == "linked"
    assert state.profile_handle == "cardosofede"
    assert state.scope == ["agents:read", "agents:publish"]
    assert "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60" not in state.model_dump_json()
    assert hub.token() == "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60"
    assert cm.get_hub()["profile_handle"] == "cardosofede"
    assert cm.get_hub()["scope"] == ["agents:read", "agents:publish"]
    assert cm.get_hub()["user"] == {"id": "u_1", "name": "cardosofede"}


def test_a_sign_in_with_no_user_identity_still_links(fake_hub, cm):
    """BUG-6, carried over from the device flow: a reply whose ``user``
    carries neither ``name`` nor ``id`` still stores a real, usable token,
    and ``is_linked()`` must not judge "linked" on a non-empty
    ``profile_handle``.
    """
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = _ok(
        {
            "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
            "scope": "agents:read agents:publish",
            "user": {},
        }
    )

    state = hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert state.status == "linked"
    assert state.profile_handle == ""
    assert hub.token() == "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60"
    assert hub.is_linked() is True


def test_a_linked_profile_survives_an_unrelated_save_and_a_fresh_load(fake_hub, cm):
    """ARCH-738's own criterion, now for the keys this flow adds.
    ``CONFIG_SECTIONS["hub"]`` merges whatever keys ``update_hub`` is given,
    so this is really asserting that ``redeem_connect_code`` wrote all of
    them (rather than only ``token``) and that an unrelated save does not
    drop any.
    """
    import config_manager as cm_module

    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = _ok(
        {
            "access_token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
            "scope": "agents:read agents:publish",
            "user": {"id": "u_1", "name": "cardosofede"},
        }
    )
    hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    cm.update_hub(**cm.get_hub())  # an unrelated save of the same section

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert reloaded.get_hub() == {
        "token": "bc_live_8f2e9c1a3b7d4f605e8a9c2b1d3f7e60",
        "token_origin": "https://www.botcamp.xyz",
        "profile_handle": "cardosofede",
        "scope": ["agents:read", "agents:publish"],
        "user": {"id": "u_1", "name": "cardosofede"},
    }


def test_a_successful_redeem_with_no_token_raises(fake_hub):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = _ok(
        {"scope": "agents:read", "user": {"id": "u_1"}}
    )

    with pytest.raises(hub.HubError):
        hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert hub.token() == ""


@pytest.mark.parametrize(
    "error, message_fragment",
    [
        ("invalid_grant", "wrong, expired or already used"),
        ("too_many_connections", "too many Condor"),
    ],
)
def test_the_known_400_errors_get_their_own_sentence(fake_hub, error, message_fragment):
    """The hub's error bodies are deliberately uniform (a wrong, expired or
    already-used code all answer ``invalid_grant``), so Condor writes its own
    sentence per *distinguishable* case rather than passing the hub's text
    through.
    """
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = hub.Reply(
        400, json.dumps({"error": error, "error_description": "..."}).encode()
    )

    with pytest.raises(hub.HubError, match=message_fragment):
        hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert hub.token() == ""


def test_an_unrecognized_400_falls_back_to_the_hubs_own_refusal(fake_hub):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = hub.Reply(
        400, json.dumps({"error": "invalid_request"}).encode()
    )

    with pytest.raises(hub.HubError) as excinfo:
        hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert excinfo.value.status == 400


def test_rate_limiting_raises_429_with_a_specific_sentence(fake_hub):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = hub.Reply(
        429, json.dumps({"error": "slow_down"}).encode()
    )

    with pytest.raises(hub.HubError, match="Too many attempts") as excinfo:
        hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert excinfo.value.status == 429


def test_a_server_error_raises_with_its_own_status(fake_hub):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = hub.Reply(500, b"{}")

    with pytest.raises(hub.HubError) as excinfo:
        hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert excinfo.value.status == 500
    assert hub.token() == ""


def test_connection_state_is_none_with_nothing_linked(cm):
    state = hub.connection_state()

    assert state.status == "none"
    assert state.connect_url == "https://www.botcamp.xyz/connect"


def test_connection_state_agrees_with_is_linked_once_connected(fake_hub, cm):
    assert hub.is_linked() is False
    assert hub.connection_state().status == "none"

    hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")

    assert hub.is_linked() is True
    assert hub.connection_state().status == "linked"


def test_connection_state_makes_no_hub_call(fake_hub, cm):
    hub.redeem_connect_code("WXYZ-WXYZ-WXYZ")
    calls_before = len(fake_hub.calls)

    hub.connection_state()

    assert len(fake_hub.calls) == calls_before


def test_connect_url_is_built_from_the_configured_origin_whether_linked_or_not(
    monkeypatch, cm
):
    monkeypatch.setattr(hub, "HUB_URL", "http://masiva.local:3000/api")

    assert hub.connection_state().connect_url == "http://masiva.local:3000/connect"


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

    result = hub.disconnect()

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
    result = hub.disconnect()

    assert result == hub.DisconnectResult(revoked=None)
    assert hub.token() == ""


def test_disconnecting_survives_an_unreachable_hub(cm):
    """The revoke attempt reaches `_send`, which the autouse ``no_sockets``
    fixture turns into a transport failure — disconnect must still clear the
    local credential, and must report that the hub was *not* told rather
    than claiming a revoke that did not happen."""
    cm.update_hub(token="bc_live_x", profile_handle="cardosofede")

    result = hub.disconnect()  # must not raise

    assert result.revoked is False
    assert result.error
    assert hub.token() == ""


def test_disconnecting_when_the_hub_refuses_the_revoke_still_clears_locally(
    fake_hub, cm
):
    cm.update_hub(token="bc_live_x", profile_handle="cardosofede")
    fake_hub.overrides[("DELETE", "/condor/me")] = hub.Reply(500, b"{}")

    result = hub.disconnect()

    assert result.revoked is False
    assert result.error
    assert hub.token() == ""


def test_connecting_a_hub_device_is_admin_only(cm, fake_hub):
    res = _settings_client(admin=False).post(
        "/settings/hub-device/connect", json={"code": "WXYZ-WXYZ-WXYZ"}
    )

    assert res.status_code == 403
    assert hub.token() == ""


def test_reading_the_full_hub_device_state_is_admin_only(cm, fake_hub):
    res = _settings_client(admin=False).get("/settings/hub-device")

    assert res.status_code == 403


def test_disconnecting_the_hub_device_is_admin_only(cm):
    res = _settings_client(admin=False).delete("/settings/hub-device")

    assert res.status_code == 403


def test_the_dashboard_can_connect_through_to_linked(cm, fake_hub):
    client = _settings_client(admin=True)

    none = client.get("/settings/hub-device")
    assert none.json()["status"] == "none"
    assert none.json()["connect_url"] == "https://www.botcamp.xyz/connect"

    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = _ok(
        {
            "access_token": "bc_live_super_secret",
            "scope": "agents:read agents:publish",
            "user": {"id": "u_1", "name": "cardosofede"},
        }
    )

    connected = client.post(
        "/settings/hub-device/connect", json={"code": "WXYZ-WXYZ-WXYZ"}
    )
    assert connected.status_code == 200
    assert connected.json() == {
        "status": "linked",
        "profile_handle": "cardosofede",
        "scope": ["agents:read", "agents:publish"],
        "connect_url": "https://www.botcamp.xyz/connect",
    }
    assert "bc_live_super_secret" not in connected.text

    linked = client.get("/settings/hub-device")
    assert linked.json()["status"] == "linked"
    assert linked.json()["profile_handle"] == "cardosofede"


def test_the_dashboard_reports_a_wrong_or_expired_code_with_a_specific_sentence(
    cm, fake_hub
):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = hub.Reply(
        400, json.dumps({"error": "invalid_grant"}).encode()
    )
    client = _settings_client(admin=True)

    res = client.post("/settings/hub-device/connect", json={"code": "WXYZ-WXYZ-WXYZ"})

    assert res.status_code == 400
    assert "wrong, expired or already used" in res.json()["detail"]
    assert hub.token() == ""


def test_the_dashboard_reports_rate_limiting_as_429(cm, fake_hub):
    fake_hub.overrides[("POST", "/condor/connect-code/redeem")] = hub.Reply(
        429, json.dumps({"error": "slow_down"}).encode()
    )
    client = _settings_client(admin=True)

    res = client.post("/settings/hub-device/connect", json={"code": "WXYZ-WXYZ-WXYZ"})

    assert res.status_code == 429


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

    Sets a token, the same precondition the real admin-only Pull tab already
    enforces (``AgentHub.tsx`` mounts ``HubBrowser`` only once ``linked`` is
    true): install now records consent for the exact version it fetches
    (botcamp-site PR #137), and consent needs the Condor connection's Bearer
    token — the hub refuses a browser session (403) and refuses no token at
    all (401) the same as any other read that needs one.
    """
    import shutil

    data = _zip_agent("traveller")
    listing = fake_hub.offer("traveller", data, name="Traveller")
    shutil.rmtree(traveller)
    hub.set_token("botcamp-tok")
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


def test_a_hub_update_also_lands_a_newly_added_skill_enabled(traveller, fake_hub):
    """FEAT-146's exception threads through ``plan_update`` too, not only a
    fresh install: a skill a newer hub version adds is never muted either."""
    from condor.memory.skills import SkillStore

    ab.build("traveller")  # mints and stamps this agent's agent_uid on disk
    _write(
        traveller / "skills" / "new_skill" / "SKILL.md",
        "---\nname: new_skill\ndescription: d\nwhen_to_use: whenever\n"
        "source: builtin\n---\n\nLook again.\n",
    )
    fake_hub.offer("traveller", _zip_agent("traveller"), version=2)
    hub.set_token("botcamp-tok")  # install now records consent (PR #137)

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    assert res.json()["verb"] == "update"
    assert load_mutes("traveller").get("skills", set()) == set()
    assert "new_skill" in {row["slug"] for row in SkillStore("traveller").catalog()}


def test_a_hub_update_grants_execution_consent_for_the_routines_it_writes(
    traveller, fake_hub
):
    """The same trust-boundary exception as a fresh install, threaded through
    ``plan_update``/``apply_update`` too: a routine this update adopts needs
    no further per-file allow, keyed to the exact bytes it just wrote."""
    from condor.agents import code_trust

    ab.build("traveller")  # mints and stamps this agent's agent_uid on disk
    _write(traveller / "routines" / "r.py", ROUTINE_PY + "\n# v2\n")
    _write(traveller / "routines" / "added_in_v2.py", ROUTINE_PY)
    fake_hub.offer("traveller", _zip_agent("traveller"), version=2)
    hub.set_token("botcamp-tok")  # install now records consent (PR #137)

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    assert res.json()["verb"] == "update"
    assert code_trust.allowed(
        "traveller", "r", (traveller / "routines" / "r.py").read_text()
    )
    assert code_trust.allowed(
        "traveller",
        "added_in_v2",
        (traveller / "routines" / "added_in_v2.py").read_text(),
    )


def test_a_hub_update_grants_execution_consent_for_a_crlf_routine_too(
    traveller, fake_hub
):
    """The update path's own ``code_trust.allow`` call (``apply_update``)
    has the same CRLF-digest bug as the fresh-install one: covered
    separately because it reads its text from ``update_plan.files`` rather
    than from ``import_plan.bundle.files`` directly."""
    from condor.agents import code_trust
    from routines.base import pending_routines

    ab.build("traveller")  # mints and stamps this agent's agent_uid on disk
    _write(
        traveller / "routines" / "r.py",
        (ROUTINE_PY + "\n# v2\n").replace("\n", "\r\n"),
    )
    fake_hub.offer("traveller", _zip_agent("traveller"), version=2)
    hub.set_token("botcamp-tok")  # install now records consent (PR #137)

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    assert res.json()["verb"] == "update"
    assert code_trust.allowed(
        "traveller", "r", (traveller / "routines" / "r.py").read_text()
    )
    # The surface fengtality actually clicked (PR #314 round 2): a routine
    # ``pending_routines`` still lists is one the Routines tab renders an
    # "allow" chip for, no matter what ``code_trust.allowed`` says elsewhere.
    assert pending_routines("traveller") == []


def test_a_hub_update_conflict_does_not_grant_consent_for_the_kept_local_file(
    offered, fake_hub
):
    """Boundary: consent follows the exact reviewed artifact, never the hub as
    a source. A routine the operator edited, where the hub's next version also
    changed it, is a ``conflict`` — the local bytes are kept on disk — and
    neither the local edit nor the hub's newer bytes (never written here) may
    come out allowed. Granting the newer bytes anyway would be consent for
    content that was never installed, let alone read by the operator."""
    from condor.agents import code_trust

    client = _client()
    client.post("/hub/agents/traveller/install")
    home = agent_home("traveller")
    reviewed_r_py = (home / "routines" / "r.py").read_text()
    assert code_trust.allowed("traveller", "r", reviewed_r_py)

    local_edit = reviewed_r_py + "\n# the operator's own change\n"
    (home / "routines" / "r.py").write_text(local_edit)
    assert not code_trust.allowed("traveller", "r", local_edit)

    # A newer hub version that changed this same file too, independently of
    # the operator's edit: written to disk just long enough to zip it, then
    # the operator's edit is put straight back — ``_zip_agent`` only ever
    # reads the live home, so this is the one way to offer content that
    # never actually lands here.
    new_upstream_r_py = reviewed_r_py + "\n# the hub's own v2 change\n"
    (home / "routines" / "r.py").write_text(new_upstream_r_py)
    zf_bytes = _zip_agent("traveller")
    (home / "routines" / "r.py").write_text(local_edit)
    fake_hub.offer("traveller", zf_bytes, version=2)

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    assert res.json()["verb"] == "update"
    files = {f["path"]: f["verdict"] for f in res.json()["plan"]["files"]}
    assert files["routines/r.py"] == "conflict"
    # The operator's edit is still on disk, untouched by the update.
    assert (home / "routines" / "r.py").read_text() == local_edit
    assert not code_trust.allowed("traveller", "r", local_edit)
    # The hub's newer bytes were never written here and must not come out
    # allowed either — that would be consent for content nobody has read.
    assert not code_trust.allowed("traveller", "r", new_upstream_r_py)


def test_the_preview_and_the_install_carry_the_same_plan(offered, fake_hub):
    client = _client()

    preview = client.post("/hub/agents/traveller/install/preview").json()
    installed = client.post("/hub/agents/traveller/install").json()

    for field, value in preview["plan"].items():
        assert installed["plan"][field] == value, field
    assert installed["plan"]["imported"] is True
    # Two fetches, not one cached preview: the digest check is what makes that
    # safe, and it ran on both. Excludes the consent calls (PR #137), which
    # also carry "/versions/" in their path but are not a bundle download —
    # the GET carries its agentId/sha256 pin as a query string, so the path
    # is compared without it.
    assert (
        sum(
            1
            for call in fake_hub.calls
            if "/versions/" in call.url
            and not call.url.split("?")[0].endswith("/consent")
        )
        == 2
    )
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


def test_an_installed_hub_agent_arrives_unmuted_with_nothing_running(offered):
    """FEAT-150: a hub install's routines arrive unmuted, same as its skills
    and tools — the hub already reviewed this exact content. Nothing is
    started regardless: muting is a visibility gate, not an execution one."""
    from condor.agents.engine import get_all_engines

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    assert AgentStore().get("traveller") is not None
    muted = load_mutes("traveller")
    assert muted["routines"] == set()
    assert not [a for a in get_all_engines() if a.startswith("traveller")]
    assert "allow" in res.json()["plan"]["next_step"]
    assert "unmute" not in res.json()["plan"]["next_step"]


def test_a_hub_installed_skill_lands_enabled_with_no_second_approval(offered):
    """FEAT-146: the hub already reviewed this exact content before serving
    it, so a skill — prose, never executed — needs no unmuting here, unlike
    the plain file import path (``test_imported_code_arrives_muted_and_the_
    agent_is_registered`` in ``tests/test_agent_bundle.py``)."""
    from condor.memory.skills import SkillStore

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    muted = load_mutes("traveller")
    assert muted["skills"] == set()
    assert "s" in {row["slug"] for row in SkillStore("traveller").catalog()}
    # Neither needs unmuting, and the wording says so with no mention of it.
    assert "unmute" not in res.json()["plan"]["next_step"]


def test_a_hub_installed_routine_has_execution_consent_for_the_reviewed_bytes(
    offered,
):
    """The owner's trust-boundary call: the hub's review stands in for the
    operator's per-file ``code_trust`` allow, tied to the exact bytes just
    installed — the one gate FEAT-147 used to hold unconditional."""
    from condor.agents import code_trust

    _client().post("/hub/agents/traveller/install")

    source = agent_home("traveller") / "routines" / "r.py"
    assert code_trust.allowed("traveller", "r", source.read_text())
    # Still armed — a hub install is still a gated agent, just one the hub's
    # own review already allowed every routine on.
    assert code_trust.is_armed("traveller")


def test_a_hub_installed_routine_with_crlf_line_endings_still_has_execution_consent(
    traveller, fake_hub
):
    """Greptile, PR #314 round 2: a CRLF zip entry survives ``decode``
    untouched, so the digest ``code_trust.allow`` stored at import time
    has to be computed over the same folding the runtime applies when it
    re-hashes the file off disk (``Path.read_text()`` always turns CRLF
    into ``\\n``) — otherwise a hub-reviewed CRLF routine reads as
    "changed" forever and the operator has to allow it by hand anyway.

    That "by hand anyway" is exactly what the human reviewer hit by
    screenshot after the earlier consent commits landed: the Routines tab's
    "allow" chip is driven by ``pending_routines``, not by
    ``code_trust.allowed`` directly, so this also pins that list empty.
    """
    import shutil

    from condor.agents import code_trust
    from routines.base import pending_routines

    _write(traveller / "routines" / "r.py", ROUTINE_PY.replace("\n", "\r\n"))
    fake_hub.offer("traveller", _zip_agent("traveller"), name="Traveller")
    shutil.rmtree(traveller)
    hub.set_token("botcamp-tok")  # install now records consent (PR #137)

    res = _client().post("/hub/agents/traveller/install")

    assert res.status_code == 200
    source = agent_home("traveller") / "routines" / "r.py"
    assert code_trust.allowed("traveller", "r", source.read_text())
    assert pending_routines("traveller") == []


def test_editing_a_hub_installed_routine_does_not_inherit_its_consent(offered):
    """Consent is keyed to the exact reviewed artifact, never to the hub as a
    source: change the bytes and the file is pending again, like any other."""
    from condor.agents import code_trust

    _client().post("/hub/agents/traveller/install")

    source = agent_home("traveller") / "routines" / "r.py"
    source.write_text(source.read_text() + "\n# a local edit\n")

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

    assert "not a security boundary for code" in body["trust"]
    assert "land enabled" in body["trust"]
    assert "grants execution consent" in body["trust"]


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


def test_the_hub_status_route_never_sends_a_non_admins_check_with_the_admins_token(
    offered, fake_hub, cm
):
    """BUG-5: ``installed_agent_status`` always called ``token()``
    unconditionally, bypassing SEC review C4's rule that only the admin's
    own reads carry the admin's Botcamp account."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    _client().post("/hub/agents/traveller/install")
    hub.set_token("botcamp-tok")

    # Force a live re-check rather than the cached confirmed answer.
    home = agent_home("traveller")
    stamp = ab.read_stamp(home)
    ab.write_stamp(
        home, stamp.model_copy(update={"hub_checked_at": "2000-01-01T00:00:00Z"})
    )

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.hub_router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=SEAT_ID, username="u", first_name="U", role="user"
    )
    before = len(fake_hub.calls)

    res = TestClient(app).get("/agents/traveller/hub-status")

    assert res.status_code == 200
    sent = fake_hub.calls[before:]
    assert sent, "the route reached the hub"
    assert {call.token for call in sent} == {""}


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


def test_manage_agents_browses_the_hub_catalogue(fake_hub):
    """BUG-7: the chat door had no catalogue browse at all — only a lookup
    by a slug the caller had to already know — even though PR #111's
    catalogue endpoint (``condor.hub.browse``) existed and the web dashboard
    already read it.
    """
    from mcp_servers.condor.tools import trading_agent

    fake_hub.overrides[("GET", "/agents")] = _catalogue(_entry("grid-bot"))

    result = trading_agent.manage_agents(action="hub_browse")

    assert result["listings"][0]["slug"] == "grid-bot"
    assert result["page"] == 1
    assert "not a security boundary" in result["trust"]


def test_manage_agents_gets_one_hub_agent(fake_hub):
    from mcp_servers.condor.tools import trading_agent

    fake_hub.offer("listed", b"zip-bytes", name="A")

    result = trading_agent.manage_agents(action="hub_get", path="listed")

    assert result["listing"]["slug"] == "listed"
    assert result["listing"]["name"] == "A"
    assert "not a security boundary" in result["trust"]


def test_manage_agents_installs_from_the_hub(offered, monkeypatch):
    """The chat door takes the same hub exception the web route does
    (FEAT-146, widened to routines in FEAT-150, and to execution consent by
    the owner's later trust-boundary call) — a Greptile finding on this PR
    caught this door lagging behind with ``mute_routines`` left at its
    default; both doors must land a hub install identically."""
    from condor.agents import code_trust
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", ADMIN_ID, raising=False)

    result = trading_agent.manage_agents(action="hub_install", path="traveller")

    assert result["verb"] == "install"
    assert result["imported"] is True
    assert result["listing"]["slug"] == "traveller"
    muted = load_mutes("traveller")
    assert muted["routines"] == set()
    # FEAT-146: the hub already reviewed this content, so a skill — unlike a
    # routine, which is code — needs no second approval on this door either.
    assert muted["skills"] == set()
    source = agent_home("traveller") / "routines" / "r.py"
    assert code_trust.allowed("traveller", "r", source.read_text())


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
