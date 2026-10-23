"""The agent hub: publish one of yours, browse, install somebody else's (FEAT-146).

FEAT-133 makes an agent one portable file and stops deliberately at the file,
so moving one still meant emailing it. This is the distribution half: a
catalogue Condor can read, an install that runs through the very same
``plan``/``apply`` a file import uses, and a publish that uploads the identical
bytes the operator could have downloaded themselves.

**The trust model, in three sentences.** It is the decision the rest of this
module hangs off, and every surface that renders a listing repeats it.

1. *A bundle is untrusted code wherever it came from, and the gate is on the
   receiving install.* Condor installs any well-formed bundle — from a file,
   from a friend, from the hub — and never requires a hub signature. The gate
   is what already exists: every executable file named before anything is
   written (FEAT-133), the review's findings shown beside that list
   (FEAT-144), routines and skills muted on arrival, no execution consent for
   any imported ``.py`` (FEAT-147), nothing started, and the operator unmuting
   item by item after reading each one.
2. *The hub is a discovery and moderation service, not a security boundary.*
   Only an ``APPROVED`` version is ever served at all (PR #111's
   ``AgentVersionStatus``) — a human or an AI reviewer passed it before
   :func:`get` can even see it — but that is a gate on what reaches the
   catalogue, not a claim read by anything in this module that decides
   whether to write. An approved bundle is not the bundle the operator will
   run after editing it here.
3. *Integrity between hub and client is a content digest over HTTPS, not a
   signature.* :func:`fetch` verifies the downloaded bytes against the
   ``X-Agent-Sha256`` header the same response carries, before any caller
   parses them; FEAT-133's per-file digests then catch any later edit. There
   is no author claim on this read path at all (PR #111 attaches no account
   to a catalogue read) — :data:`VALIDATED`/:data:`COMMUNITY` and the
   ``author`` field that renders one still exist for :func:`publish`'s own
   :class:`Receipt`, unrelated to anything a pull reads.

**Why the base URL is compiled in, and why no environment variable moves it.**
The rule is ``condor/sharing/outbox.py``'s, and here it is not tidiness but the
SSRF answer. FEAT-133 refused to build an import-from-URL for exactly one
reason: "the Condor process can reach ``localhost`` and cloud metadata
endpoints, so a server-side fetch of a user-supplied URL is an SSRF surface
that needs its own design". This is that design. A compiled-in base, plus a
listing id checked by :func:`condor.fetchers._identifiers.validate_identifier`
before a single request is made, plus an opener that refuses redirects, means
the server-side fetch is no longer a fetch of a user-supplied URL at all — the
caller contributes one path segment drawn from ``[A-Za-z0-9._-]`` and nothing
else. Making the base env-overridable, or following a 302, would hand that
property straight back. :func:`_dev_override` is the one narrow exception,
gated on an explicit two-variable opt-in rather than a bare env read — see
its docstring — for testing against a hub that is not yet live in production.

**Why there is no durable outbox.** ``condor/sharing/outbox.py`` exists because
its sweep "sends what nobody has read" from a worker thread, and because "a
user who pressed Unshare and then lost the network has still revoked" — a
revocation has to survive a restart to be worth promising. A publish is neither:
it is an interactive act with a human watching the result, and a failure is
reported to that human and retried by pressing the button again. Queueing it
would mean a bundle leaving the box minutes later, from bytes nobody was still
looking at.

**Synchronous on purpose.** Every function here blocks, like
:mod:`condor.sharing.share`, and for the same reason: the two callers are a web
route and a synchronous MCP tool. The routes send these through
``asyncio.to_thread`` (PERF-235's rule, stated in
``condor/web/routes/sharing.py``), which keeps the bot's single event loop free
while letting the chat door call them directly with no event-loop gymnastics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from condor.agents.bundle import MAX_BUNDLE_BYTES
from condor.fetchers._identifiers import IdentifierError, validate_identifier

log = logging.getLogger(__name__)

#: Where the hub lives. **Compiled in, like the sharing collector's address,
#: and no environment variable redirects it in production** — see the module
#: docstring: this constant is half of the SSRF answer, and
#: ``tests/test_hub.py`` asserts that setting every plausible env name and
#: re-importing changes nothing. The one deliberate exception is
#: :func:`_dev_override`, gated on an explicit dev-mode opt-in rather than a
#: bare env read — see that function.
#:
#: The hostname itself is not Condor's decision to make. The service lives in
#: a codebase that is not this one (PR #111 of ``hummingbot/botcamp-site``
#: puts these routes on the botcamp.xyz app itself, under ``/api/condor/*``
#: and ``/api/agents/*`` — there is no ``hub.`` subdomain and no ``/v1``
#: segment), and standing it up — who reviews a submission for the
#: ``validated`` tier, how a publish token is issued, whether the hub runs
#: FEAT-144's review as a Python dependency or a reimplementation — is
#: TASK-004. Changing it is a code change by design.
#:
#: Pointed at the **dev** deployment while the hub is being exercised end to
#: end: ``dev.botcamp.xyz`` is the instance that currently answers these
#: routes. Bare ``botcamp.xyz`` is not a drop-in replacement — it answers
#: ``307`` to ``www.botcamp.xyz``, and :class:`_NoRedirect` refuses that, so
#: moving this to production is a deliberate change made against whichever
#: host serves the API without a redirect, not a one-token edit. The path
#: prefix is the same on both: ``/api``, verified on dev for
#: ``GET /api/agents``, ``GET /api/agents/{slug}``,
#: ``GET /api/agents/{slug}/versions/{version}``, ``GET /api/submissions/{id}``
#: and ``POST /api/condor/device/code``.
HUB_URL = "https://dev.botcamp.xyz/api"


def _dev_override() -> str:
    """The dev-only override of :data:`HUB_URL`, applied once at import.

    Testing against a hub other than the compiled-in one (a LAN instance,
    say) needs *some* way to retarget this module — but FEAT-146
    made the base non-overridable on purpose, as the SSRF answer the module
    docstring describes, and :func:`test_no_environment_variable_redirects_the_hub`
    in ``tests/test_hub.py`` holds every plausible env name to that. This is
    the one deliberate crack in that rule, and it is kept narrow on purpose:

    * It takes **two** env vars, not one. ``CONDOR_HUB_DEV_MODE`` is the
      knowing opt-in — an operator sets it because they mean to point this
      install somewhere other than the real hub, not because a deploy script
      happened to export a URL — and ``CONDOR_HUB_DEV_URL`` is the target.
      Setting the URL alone, the shape a bare env read would have, changes
      nothing: :data:`HUB_URL` stays the compiled-in production address.
    * A plain ``http://`` target is accepted here and nowhere else — the
      production constant above stays ``https://`` unconditionally, since it
      is never read from the environment at all.
    * Lights up the override loudly, once, at import — never silently.
    """
    import os

    if os.environ.get("CONDOR_HUB_DEV_MODE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return ""
    return os.environ.get("CONDOR_HUB_DEV_URL", "").strip()


_dev_hub_url = _dev_override()
if _dev_hub_url:
    log.warning(
        "hub: CONDOR_HUB_DEV_MODE is set — talking to %r instead of the real "
        "hub. This must never be set on an operator's production install.",
        _dev_hub_url,
    )
    HUB_URL = _dev_hub_url

#: One call's budget. Longer than the collector's 10s because a bundle download
#: is up to :data:`condor.agents.bundle.MAX_BUNDLE_BYTES`, not one transcript.
HUB_TIMEOUT_S = 20

#: What a listing page may weigh. A catalogue of metadata, so this is generous
#: by two orders of magnitude; it exists so a hub that answers with a
#: terabyte cannot be a memory exhaustion.
MAX_LISTING_BYTES = 1_000_000

#: A human reviewed one submitted revision of this listing, once.
VALIDATED = "validated"
#: Nobody reviewed it. The default, and **installable on identical terms**.
COMMUNITY = "community"


class HubError(RuntimeError):
    """Anything that stopped a hub call, with the status that said so.

    ``status`` is the hub's own HTTP status when the hub answered, and ``0``
    when nothing arrived — a transport failure, a refused redirect, a reply
    this reader could not parse, or a local refusal that never left the box. A
    caller mapping this to its own protocol needs that distinction: a 401 is
    the operator's token, a 0 is the network.
    """

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class NoTokenError(HubError):
    """This install has no Botcamp token, so the request never left the box.

    Its own class because the two callers have to tell it apart from every
    other ``HubError``: it is neither the hub refusing nor the network failing,
    it is a local configuration gap with a local fix, and the web layer answers
    it 400 rather than 502 on exactly that basis. ``status`` stays ``0``,
    because nothing answered.
    """


class _HubRead(BaseModel):
    """A shape the hub describes, where ``null`` means "not sent".

    Botcamp's columns are nullable — a zip upload has no ``sourceRef``, a page
    with no description has no ``summary`` — and it answers ``null`` for them
    rather than leaving them out. Every field here already has a default for
    "not sent", so a ``null`` is dropped and the default applies, instead of
    one empty column refusing a whole listing.
    """

    @model_validator(mode="before")
    @classmethod
    def _null_is_not_sent(cls, data):
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data


class AgentVersion(_HubRead):
    """One ``APPROVED`` version of a Botcamp agent (PR #111's own shape,
    ``GET /api/agents/{slug}``'s ``versions[]``).

    ``sha256`` is carried here for the version picker to show, but it is
    **not** what :func:`fetch` verifies a download against — that is the
    ``X-Agent-Sha256`` header the *download itself* answers with, a value
    this install read off the response it is checking, never a field from an
    earlier, separate call. ``manifest`` is Botcamp's own coarse summary
    (name/agent_key/tools/loops) and is rendered as a hint only; the bundle
    :func:`condor.botcamp_import.from_zip` builds from the downloaded files
    is the one this install actually acts on.
    """

    model_config = ConfigDict(populate_by_name=True)

    version: int
    sha256: str = ""
    size: int = 0
    file_count: int = Field(0, alias="fileCount")
    source_ref: str = Field("", alias="sourceRef")
    created_at: str = Field("", alias="createdAt")
    manifest: dict = Field(default_factory=dict)


class AgentListing(_HubRead):
    """One Botcamp agent, as ``GET /api/agents/{slug}`` describes it.

    PR #111 carries no listing id, no badge, no author claim and no
    FEAT-144-shaped findings on this read path at all — the catalogue
    (:func:`browse`) has some of those, but nothing here joins the two. ``slug`` is therefore the only identity there is, and it is this
    install's own charge to put through :func:`condor.fetchers._identifiers.validate_identifier`
    before it reaches a URL, the same as every other caller-controlled path
    segment in this module. ``versions`` lists only ``APPROVED`` revisions,
    newest first; ``latest`` is Botcamp's own pick among them, never computed
    here.
    """

    model_config = ConfigDict(populate_by_name=True)

    slug: str
    name: str = Field("", alias="title")
    description: str = Field("", alias="summary")
    latest: int = 1
    versions: list[AgentVersion] = Field(default_factory=list)


class CatalogueAuthor(_HubRead):
    name: str = ""
    handle: str = ""


class CatalogueEntry(_HubRead):
    """One published agent, as ``GET /api/agents?query=&page=`` lists it.

    Botcamp's own words, rendered rather than re-derived — the same trust
    :class:`AgentListing` gets. ``slug`` is what a pull addresses, and it goes
    through :func:`get`/:func:`fetch`'s identifier guard like any slug typed
    by hand; :func:`browse` drops an entry whose slug would not. ``free`` is
    whether any signed-in account can pull it — ``False`` means a membership
    or unlock (402).
    """

    listing_id: str = ""
    slug: str
    name: str = ""
    description: str = ""
    version: int = 1
    author: CatalogueAuthor = Field(default_factory=CatalogueAuthor)
    badge: str = ""
    digest: str = ""
    bytes: int = 0
    published_at: str = ""
    free: bool = True


#: Botcamp pages the catalogue 20 at a time; a reply longer than this is
#: refused rather than rendered, whatever the hub claims.
MAX_CATALOGUE_PAGE = 100
MAX_CATALOGUE_QUERY = 100


class SubmissionReason(BaseModel):
    """One line of why a submission is ``rejected`` (contract §5.2, §6.3).

    ``rule`` is ``"secret-scan"``, ``"structural"`` or ``"human-review"``;
    ``detail`` is the hub's own sentence and never carries the secret value
    itself (§6.2) — this module trusts that promise the same way it trusts
    the hub's listing fields, by rendering it rather than re-deriving it.
    """

    rule: str = ""
    detail: str = ""


class Receipt(BaseModel):
    """What the hub said about one submission (FEAT-150, contract §5).

    A submission is not a listing (§5.1): ``status`` is ``"pending"``,
    ``"rejected"`` or ``"published"``, and nothing here may read a 2xx
    response status as a claim of ``"published"`` on its own — that claim is
    the hub's own word in the body, via this field. A ``pending`` or
    ``rejected`` receipt need not carry ``listing_id``/``badge``/
    ``published_at``; those exist only once a human verdict publishes it.
    ``GET /submissions/{id}`` (§5.3) answers with only ``submission_id``,
    ``status``, ``submitted_at``, ``decided_at``, ``reasons`` and
    ``listing_id`` — never ``slug``/``agent_uid``/``version``/``digest``/
    ``bytes`` again — which is why :func:`submission_status` merges its
    answer over the record :func:`publish` already stored rather than
    replacing it outright.
    """

    submission_id: str = ""
    status: str = "pending"
    agent_uid: str = ""
    slug: str = ""
    version: int = 1
    digest: str = ""
    bytes: int = 0
    submitted_at: str = ""
    decided_at: str = ""
    #: Present only on ``"rejected"`` (§6.3, §11.3).
    reasons: list[SubmissionReason] = Field(default_factory=list)
    #: Present only on ``"published"``.
    listing_id: str = ""
    #: :data:`VALIDATED` or :data:`COMMUNITY`, filled in once ``published`` —
    #: the submission answer itself does not repeat it, so this is carried
    #: over from a follow-up :func:`get` when a poll first observes the
    #: transition (see :func:`submission_status`).
    badge: str = ""
    published_at: str = ""


class DeviceCode(BaseModel):
    """What ``POST /api/condor/device/code`` handed back (PR #111's contract).

    The faithful parse of an **untrusted** reply, and internal to this module
    on purpose. ``verification_uri``/``verification_uri_complete`` are the
    hub's suggestion of where to send the admin's browser, and nothing before
    this point vetted them — :func:`device_start` derives the link it actually
    hands out from :data:`HUB_URL` instead and returns a :class:`DeviceStart`,
    so neither field ever reaches a response, a template or a browser
    (SEC-819). ``device_code`` is used only to poll — it is never rendered,
    logged, or returned past :func:`device_start`'s own caller.
    """

    device_code: str
    #: Shown to the admin as-is and put in the approval link, so it is held to
    #: the shape a code has (Botcamp's are ``XXXX-XXXX``) — a hub cannot use
    #: it to put a sentence of its own on Condor's screen.
    user_code: str = Field(pattern=r"^[A-Za-z0-9-]{4,16}$")
    verification_uri: str
    verification_uri_complete: str = ""
    expires_in: int = 600
    interval: int = 5


class DeviceStart(BaseModel):
    """A device login, as everything outside this module may see it.

    :class:`DeviceCode` minus the two fields that came from the hub's mouth:
    ``approval_url`` is built here from :data:`HUB_URL`, so a caller that
    opens it is going to the address this install was configured with and
    nowhere else.
    """

    user_code: str
    approval_url: str
    expires_in: int = 600
    interval: int = 5


class DeviceLinkState(BaseModel):
    """What a caller may learn about this install's Botcamp link.

    Never the credential — see :func:`device_state`. ``status`` is one of
    ``"none"`` (nothing pending, nothing linked), ``"pending"`` (a device
    login is in flight; ``user_code``/``approval_url``/``expires_at`` are
    set), ``"denied"``, ``"expired"``, or ``"invalid"`` (a device login just
    ended that way — reported once, to the poll that observed it), or
    ``"linked"`` (``profile_handle`` and ``scope`` are set).

    ``approval_url`` is derived from :data:`HUB_URL`, never echoed from the
    hub's reply — see :func:`approval_url`.
    """

    status: str = "none"
    user_code: str = ""
    approval_url: str = ""
    expires_at: float = 0.0
    profile_handle: str = ""
    scope: list[str] = Field(default_factory=list)


#: The one device-login attempt this install is tracking, or ``None``.
#: Deliberately in-memory and not in ``config.yml``: a device login is a
#: short-lived, interactive act with an admin watching the result, the same
#: reasoning the module docstring gives for there being no durable publish
#: outbox. A restart mid-login simply loses the attempt — the hub's own
#: ``expires_in`` deadline is what ends it either way. Guarded by
#: :data:`_device_lock` because the web routes dispatch these calls through
#: ``asyncio.to_thread``, i.e. a worker thread pool rather than the event
#: loop.
_pending_device: dict | None = None
_device_lock = threading.Lock()


class Reply(NamedTuple):
    """One HTTP exchange's outcome. The seam the tests drive instead of a socket."""

    status: int
    body: bytes
    #: Response headers, by name, case preserved as sent. ``None`` (the
    #: default — every construction site that predates this field keeps
    #: working unchanged) reads the same as ``{}`` to :func:`_header`.
    #: :func:`fetch`'s ``X-Agent-Sha256`` check is the one reader that needs
    #: this; every error-reporting path that came before it only ever needed
    #: ``body``.
    headers: dict | None = None


def _segment(value: str, what: str) -> str:
    """``value`` if it is safe to interpolate into the hub's URL path.

    ``validate_identifier`` is the charset guard and it is not sufficient on
    its own here: its charset includes ``.``, so ``".."`` and ``"."`` *match*
    it — they contain nothing outside letters, digits, dot, dash and
    underscore. One ``..`` segment is still a traversal
    (``{HUB_URL}/agents/..`` addresses ``{HUB_URL}``), which is why
    ``validate_db_path`` in that same module refuses those two segments
    separately rather than leaning on its own regex. This is the single-segment
    version of that refusal.

    The charset check is left where it is rather than tightened: it is shared
    with every connector and account name the Hummingbot client interpolates,
    and an identifier that is literally ``.`` is pathological there but not
    this module's call to outlaw.

    Used for a listing id *and* for a ``link_id`` (:data:`_pending_link`):
    the latter is minted by the hub, not typed by a human, but it still rides
    in a URL path this process dials, and "no unchecked value reaches a path
    segment, whoever wrote it" (module docstring) does not carve out an
    exception for values this install did not choose itself.
    """
    validate_identifier(value, what)
    if value in (".", ".."):
        raise IdentifierError(
            f"Invalid {what} {value!r}: a {what} may not be '.' or '..'."
        )
    return value


def _listing_id(value: str) -> str:
    return _segment(value, "listing id")


def _agent_slug(value: str) -> str:
    return _segment(value, "agent slug")


def _version_segment(value: str) -> str:
    """``value`` if it is safe as a version path segment: ``latest`` or a
    positive integer, verbatim. Never a value the hub's reply echoed back —
    see :func:`fetch`.
    """
    if value == "latest":
        return value
    if value.isdigit() and value != "0" and not value.startswith("0"):
        return value
    raise IdentifierError(
        f"Invalid version {value!r}: must be 'latest' or a positive integer"
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a hub reply cannot re-aim the fetch.

    The compiled-in base and the validated identifier together promise that
    this process only ever dials :data:`HUB_URL`. A followed 302 would break
    that promise at the one point nothing is checking: the hub (or anything
    able to answer as it) could redirect the fetch to ``169.254.169.254`` or to
    a loopback port and Condor would dial it with its own network position.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HubError(
            f"The hub answered {code} with a redirect to another address; "
            "Condor only talks to the hub it was built with",
            status=code,
        )


_OPENER = urllib.request.build_opener(_NoRedirect)


def _dial(request: urllib.request.Request) -> Reply:
    """The actual socket, and the only thing in this module that opens one.

    Separate from :func:`_send` so that the translation above it — "an error
    status is an answer, a transport failure is not" — has something underneath
    it to fail. A test that replaced ``_send`` wholesale to simulate a refused
    connection would be replacing the very code it meant to exercise, and would
    pass whether or not that translation existed.
    """
    with _OPENER.open(request, timeout=HUB_TIMEOUT_S) as response:
        return Reply(
            response.status,
            response.read(MAX_BUNDLE_BYTES + 1),
            dict(response.headers),
        )


def _send(method: str, url: str, *, token: str = "", payload: bytes = b"") -> Reply:
    """The one HTTP call in this module. Everything else is a view over it.

    A single seam on purpose: ``tests/test_hub.py`` replaces this whole
    function with a stub that records its calls, which is how the suite asserts
    that an invalid identifier produces *zero* requests and how it makes no
    network call of its own.

    ``token`` rides in ``Authorization``, never in the body and never in the
    URL: a credential in a JSON body is captured by every proxy that logs
    bodies, and one in a query string is captured by every access log there is.
    Nothing here logs the header either — see :func:`_log_safe`.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "condor-hub/1",
    }
    if payload:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=payload or None, headers=headers, method=method
    )
    try:
        return _dial(request)
    except urllib.error.HTTPError as exc:
        # An error status is an answer, not a failure to reach the hub: the body
        # usually carries the sentence the operator needs ("that slug is
        # already published", "this token was revoked").
        return Reply(
            exc.code, exc.read(MAX_LISTING_BYTES) or b"", dict(exc.headers or {})
        )
    except HubError:
        raise
    except Exception as exc:  # noqa: BLE001 - every transport failure reads alike
        raise HubError(f"Could not reach the agent hub: {exc}", status=0) from exc


def _log_safe(url: str) -> str:
    """A hub URL fit for a log line. Path only — no query, no credential.

    The token never reaches a URL in the first place, so this is belt and
    braces; what it actually removes is a ``?query=`` an operator typed, which
    is their search and nobody else's business in a shared log.
    """
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def approval_url(user_code: str) -> str:
    """Where to send an admin to approve a device login, built locally.

    The hub tells Condor where it would like the browser sent
    (``verification_uri``/``verification_uri_complete``). Condor does not
    take it up on that. Those strings arrive from a service this install does
    not control, over a call that carries no credential and so proves nothing
    about who answered; opening one is a navigation Condor performs on behalf
    of the one person on the box who can publish, behind a button that says
    "Sign in to Botcamp". A hostile or compromised hub that could choose that
    destination would have a phishing page with Condor's own endorsement on
    it. So the destination is the caller's to decide, exactly as FEAT-146
    made the *API* base non-overridable: this is the same rule applied to the
    one path that hands a URL to a browser rather than to :func:`_send`
    (SEC-819, contract §4.1 step 3).

    Built from :data:`HUB_URL`'s **origin** — scheme, host and port — because
    ``HUB_URL`` addresses the API (``https://dev.botcamp.xyz/api``) while the
    approval page is a page on the site (``https://dev.botcamp.xyz/connect``).
    The dev override is picked up for free, so pointing this install at a LAN
    hub retargets the approval link with it.
    """
    parts = urllib.parse.urlsplit(HUB_URL)
    code = urllib.parse.urlencode({"code": user_code})
    return f"{parts.scheme}://{parts.netloc}/connect?{code}"


def site_url(path: str) -> str:
    """A page on Botcamp's site, built from :data:`HUB_URL`'s origin.

    The same rule as :func:`approval_url`: a link this install hands a browser
    goes where this install was configured to go, never anywhere a reply
    named. ``path`` is the caller's own, starting with ``/``.
    """
    parts = urllib.parse.urlsplit(HUB_URL)
    return f"{parts.scheme}://{parts.netloc}{path}"


def _warn_if_offsite(code: DeviceCode) -> None:
    """Log once when the hub asked for a browser somewhere else.

    Not an error: :func:`approval_url` has already decided where the admin
    goes, so a mismatch changes nothing this process does. It is logged
    because a hub that starts answering with a third-party host is either
    misconfigured or hostile, and neither should pass silently. One line per
    attempt, through :func:`_log_safe` so the ``?code=`` query — the only
    part of either URL that is a secret-shaped value — stays out of a shared
    log, and never the ``device_code``.
    """
    origin = urllib.parse.urlsplit(HUB_URL).netloc
    for candidate in (code.verification_uri_complete, code.verification_uri):
        if candidate and urllib.parse.urlsplit(candidate).netloc != origin:
            log.warning(
                "hub: the device-code reply pointed at %s, which is not this "
                "install's hub (%s) — ignoring it and using the locally built "
                "approval link",
                _log_safe(candidate),
                origin,
            )
            return


def _decode(reply: Reply, what: str) -> dict:
    """``reply.body`` as a JSON object, regardless of its status.

    Split out of :func:`_json` so a caller that must read a body the hub sent
    with a *non-2xx* status — the submission lifecycle's ``400`` deterministic
    rejection (§5.2) is exactly that shape — can do so without re-deriving
    this decoding.
    """
    if len(reply.body) > MAX_LISTING_BYTES:
        raise HubError(
            f"The hub's answer for {what} is over the "
            f"{MAX_LISTING_BYTES}-byte limit",
            status=0,
        )
    try:
        payload = json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HubError(f"The hub's answer for {what} is not JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise HubError(f"The hub's answer for {what} is not a JSON object")
    return payload


def _json(reply: Reply, what: str) -> dict:
    """The decoded body of a successful reply, or the sentence saying why not."""
    if reply.status < 200 or reply.status >= 300:
        raise HubError(_refusal(reply, what), status=reply.status)
    return _decode(reply, what)


def _refusal(reply: Reply, what: str) -> str:
    """The hub's own sentence for a non-2xx, or a generic one naming the status.

    The hub's ``detail`` (:func:`_detail`) is preferred because it is the
    only text that can say *why* — but it is truncated and never trusted to
    be the whole message: it is a string from another service that ends up
    in an operator's error slot. This is the *publish*-credential wording;
    :func:`_access_refusal` is the one a gated-agent read uses instead.
    """
    detail = _detail(reply)
    if reply.status == 401 or reply.status == 403:
        detail = detail or (
            "The hub refused this token. Reconnect Botcamp in the Agent Hub (Agents → Hub)."
        )
    return f"The hub refused {what} ({reply.status})" + (
        f": {detail[:400]}" if detail else ""
    )


def _enveloped(payload: dict) -> dict:
    """``payload``'s ``data`` object when Botcamp wrapped it, else ``payload``.

    Botcamp answers every route as ``{"data": {...}}`` — ``/condor/me``,
    ``/condor/device/approve`` and ``/agents/{slug}`` alike — while §3.2 of the
    contract shows the bare listing. Both are read, so neither side has to move
    first; a listing that happens to carry a non-object ``data`` field is left
    as it is.
    """
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _model(payload: dict, model, what: str):
    """``payload`` as ``model``, or the sentence saying the hub's shape is wrong."""
    try:
        return model.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic's own message is the sentence
        raise HubError(
            f"The hub described {what} in a shape this Condor cannot read: {exc}"
        ) from exc


# ── The catalogue ────────────────────────────────────────────────────────


#
# PR #111 ships no browse/search endpoint — only a lookup by a slug the
# caller must already know (``GET /api/agents/{slug}``) and a download of
# one of its approved versions (``GET /api/agents/{slug}/versions/{version}``).
# There is nothing here that answers "what does the hub have" — a caller
# that wants that has no answer until Botcamp ships a catalogue browse
# (TASK-004); every surface that used to call ``browse`` now asks the
# operator for a slug instead.


def _detail(reply: Reply) -> str:
    """The hub's own ``detail``/``error`` sentence, or ``""``. Truncated and
    never trusted to be the whole story — see :func:`_refusal`.
    """
    try:
        body = json.loads(reply.body.decode("utf-8"))
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or "")[:400]
    except Exception:  # noqa: BLE001 - a hub that answers HTML still gets ""
        pass
    return ""


def _access_refusal(reply: Reply, what: str) -> HubError:
    """``reply`` as the sentence a gated-agent read deserves.

    Deliberately not :func:`_refusal`'s wording, which is about a revoked
    *publish* credential and would mislead here. PR #111's own
    ``ACCESS_ERRORS`` draws exactly these lines, and this keeps them apart
    rather than collapsing them into one generic refusal: ``402`` is a
    membership or purchase gate, ``403`` is a private agent or a token
    without the ``agents:read`` scope, ``401`` is "sign in" (no token, or one
    the hub no longer honours), and ``404`` is "no such agent, or no approved
    version yet".
    """
    detail = _detail(reply)
    if reply.status == 402:
        return HubError(
            f"{what}: this agent requires a Botcamp membership or purchase to "
            "clone it" + (f" ({detail})" if detail else ""),
            status=402,
        )
    if reply.status == 401:
        return HubError(
            detail
            or f"{what}: sign in to Botcamp to clone this agent — connect a "
            "profile in the Agent Hub (Agents → Hub).",
            status=401,
        )
    if reply.status == 403:
        return HubError(
            detail or f"{what}: Botcamp says this agent is private.", status=403
        )
    if reply.status == 404:
        return HubError(detail or f"{what}: not found on Botcamp.", status=404)
    return HubError(_refusal(reply, what), status=reply.status)


def _header(headers: dict | None, name: str) -> str:
    """``headers[name]``, matched case-insensitively. ``""`` when absent."""
    lname = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == lname:
            return value
    return ""


def get(slug: str, token: str = "") -> AgentListing:
    """One agent's catalogue entry, with its approved versions.

    ``slug`` goes through ``validate_identifier`` **before anything is
    sent** — the order the identifier guard's own docstring requires, and the
    reason a ``../``, a ``?``, a ``#`` or a space is an ``IdentifierError``
    here rather than a request to somewhere else.

    ``token`` is optional — PR #111's own contract for this endpoint:
    omitted, this is the anonymous read a free agent answers; a linked
    profile's own token is what turns a membership/purchase gate into a 200
    for an account that actually has access. Raises :class:`HubError`
    through :func:`_access_refusal` — 404 not found, 403 private, 402 needs a
    membership or purchase, 401 sign in — never the publish-token wording a
    revoked *publish* credential gets.
    """
    slug = _agent_slug(slug)
    what = f"agent '{slug}'"
    reply = _send("GET", f"{HUB_URL}/agents/{slug}", token=(token or "").strip())
    if reply.status < 200 or reply.status >= 300:
        raise _access_refusal(reply, what)
    return _model(_enveloped(_decode(reply, what)), AgentListing, what)


def browse(query: str = "", page: int = 1, token: str = "") -> list[CatalogueEntry]:
    """One page of Botcamp's catalogue of published agents, newest first.

    Anonymous on Botcamp's side; this install's token rides along when there
    is one, so nothing about the answer depends on whether it was sent. A
    short or empty page is the last one. ``query`` and ``page`` are this
    caller's, encoded here as a query string — never a path segment — and
    held to a length and a range before anything is sent.
    """
    query = (query or "").strip()
    if len(query) > MAX_CATALOGUE_QUERY:
        raise IdentifierError(
            f"A catalogue search is at most {MAX_CATALOGUE_QUERY} characters"
        )
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 1000:
        raise IdentifierError(f"Invalid catalogue page {page!r}")
    what = "the agent catalogue"
    params = urllib.parse.urlencode({"query": query, "page": page})
    reply = _send("GET", f"{HUB_URL}/agents?{params}", token=(token or "").strip())
    if reply.status < 200 or reply.status >= 300:
        raise _access_refusal(reply, what)
    listings = _enveloped(_decode(reply, what)).get("listings")
    if not isinstance(listings, list):
        raise HubError(f"The hub's answer for {what} carries no listings")
    if len(listings) > MAX_CATALOGUE_PAGE:
        raise HubError(
            f"The hub's answer for {what} lists {len(listings)} agents, over "
            f"the {MAX_CATALOGUE_PAGE}-per-page limit"
        )
    entries: list[CatalogueEntry] = []
    for raw in listings:
        if not isinstance(raw, dict):
            continue
        entry = _model(raw, CatalogueEntry, "a catalogue entry")
        try:
            _agent_slug(entry.slug)
        except IdentifierError:
            log.warning("hub: catalogue entry with an unusable slug dropped")
            continue
        entries.append(entry)
    return entries


class FetchedVersion(NamedTuple):
    """One downloaded, digest-verified agent version — the whole of what
    :func:`fetch` hands back.
    """

    data: bytes
    #: The hub's own ``X-Agent-Version`` — the concrete number ``"latest"``
    #: resolved to, so a caller that asked for it can still record one.
    version: int
    #: The hex sha256 the response's own header reported, already verified
    #: against ``data``.
    sha256: str


def fetch(slug: str, version: str = "latest", *, token: str = "") -> FetchedVersion:
    """One agent version's bytes, verified against the response's own
    ``X-Agent-Sha256`` header before anything parses them.

    There is no earlier listing call this cross-checks against. Unlike the
    abandoned ``listing_id``/``digest`` contract :func:`get` once carried,
    PR #111 answers the integrity check in the *same* response as the bytes
    — there is nothing to pass in to skip a second round trip, because there
    is no second round trip to skip. ``slug`` and ``version`` are both
    addressed with what the **caller** gave, after the guards below, and
    never with anything a reply echoed back — the one rule every
    caller-controlled path segment in this module holds to.

    ``version`` is ``"latest"`` (the default — Botcamp resolves it) or a
    positive integer as a string; anything else is an ``IdentifierError``
    before a request is sent. A reply naming no digest at all is refused
    rather than trusted: a hub that cannot say what it served is not
    offering integrity, and silently skipping the check is how a
    verification becomes decoration.
    """
    slug = _agent_slug(slug)
    version = _version_segment(version)
    what = f"the bundle of '{slug}' version {version}"
    reply = _send(
        "GET",
        f"{HUB_URL}/agents/{slug}/versions/{version}",
        token=(token or "").strip(),
    )
    if reply.status < 200 or reply.status >= 300:
        raise _access_refusal(reply, what)
    if len(reply.body) > MAX_BUNDLE_BYTES:
        raise HubError(f"{what} is over the {MAX_BUNDLE_BYTES}-byte limit")
    digest = _header(reply.headers, "X-Agent-Sha256").strip().lower()
    if not digest:
        raise HubError(f"{what} carries no X-Agent-Sha256 header — nothing was parsed")
    got = hashlib.sha256(reply.body).hexdigest()
    if got != digest:
        raise HubError(
            f"{what} does not match its own X-Agent-Sha256 header (expected "
            f"{digest[:12]}…, got {got[:12]}…) — nothing was parsed"
        )
    resolved = _header(reply.headers, "X-Agent-Version").strip()
    resolved_version = (
        int(resolved)
        if resolved.isdigit()
        else (int(version) if version.isdigit() else 0)
    )
    log.info(
        "hub: fetched %s (%d bytes, digest ok)",
        _log_safe(f"{HUB_URL}/agents/{slug}/versions/{version}"),
        len(reply.body),
    )
    return FetchedVersion(data=reply.body, version=resolved_version, sha256=digest)


# ── Status of an already-installed agent (CORR-812) ────────────────────────
#
# An install that downloaded an agent's version has no way to learn Botcamp
# later stopped serving it — the agent delisted, or that version no longer
# ``APPROVED``. This is the read for that question, asked about a version
# this install already has a local copy of, which is why it is shaped
# differently from :func:`get`: a ``404``, or a 200 whose ``versions`` no
# longer names the installed one, is itself the useful answer, not a
# failure to report.

#: How long a confirmed status answer is trusted before the next page view
#: re-asks the hub. Bounds the fan-out the CORR-812 proposed solution warns
#: about: an operator re-opening the agents page every few seconds must not
#: turn into a hub request every few seconds.
STATUS_CACHE_S = 600


class ListingStatus(BaseModel):
    """What one live check of an installed agent version found.

    ``state`` is ``"ok"`` (the installed version is still listed among
    ``versions``), ``"delisted"`` (the hub confirmed it is not — a ``404``,
    or a 200 that no longer names this version among its approved ones), or
    ``"unknown"`` — a network failure, a 5xx, a malformed reply, or an access
    answer (401/402/403) this install cannot currently read and so cannot
    use to confirm anything either way. There is no ``"badge_changed"``:
    PR #111 carries no badge on this read path at all.
    """

    state: str = "unknown"
    checked_at: str = ""
    detail: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def listing_status(slug: str, version: int, *, token: str = "") -> ListingStatus:
    """Ask the hub about one installed agent version, never raising.

    Every other reader of ``GET /agents/{slug}`` (:func:`get`) treats a
    non-2xx as a failure the caller must handle; this one exists for exactly
    the case where "the hub no longer serves this" is itself the useful
    answer. An access refusal (401/402/403) reads as ``"unknown"`` rather
    than a confirmed removal: it says this install cannot currently read the
    agent, not that the agent is gone.
    """
    slug = _agent_slug(slug)
    checked_at = _now()
    try:
        reply = _send("GET", f"{HUB_URL}/agents/{slug}", token=(token or "").strip())
    except HubError:
        return ListingStatus(state="unknown", checked_at=checked_at)

    if 200 <= reply.status < 300:
        try:
            listing = _model(
                _enveloped(_decode(reply, f"agent '{slug}'")),
                AgentListing,
                f"agent '{slug}'",
            )
        except HubError:
            return ListingStatus(state="unknown", checked_at=checked_at)
        state = (
            "ok"
            if any(entry.version == version for entry in listing.versions)
            else "delisted"
        )
        return ListingStatus(state=state, checked_at=checked_at)

    if reply.status == 404:
        return ListingStatus(
            state="delisted", checked_at=checked_at, detail=_detail(reply)
        )

    return ListingStatus(state="unknown", checked_at=checked_at)


def _stale(checked_at: str) -> bool:
    if not checked_at:
        return True
    try:
        when = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - when).total_seconds() >= STATUS_CACHE_S


def _status_payload(stamp) -> dict:
    return {
        "tracked": True,
        "slug": stamp.hub_slug,
        "version": stamp.hub_version,
        "status": stamp.hub_status or "ok",
        "checked_at": stamp.hub_checked_at,
        "live_check_failed": False,
    }


def installed_agent_status(slug: str, force: bool = False) -> dict:
    """What this install knows right now about ``slug``'s Botcamp provenance.

    ``{"tracked": False}`` when ``slug`` was never installed or updated from
    a Botcamp agent — a plain file import has nothing here to check.
    Otherwise re-asks the hub through :func:`listing_status` at most once per
    :data:`STATUS_CACHE_S` (``force`` bypasses that), and persists only a
    *confirmed* answer — ``"ok"`` or ``"delisted"`` — back into the agent's
    stamp. An ``"unknown"`` live check is returned for this one read with
    ``live_check_failed`` set, **without** overwriting the stamp: the local
    files and execution state stay exactly as they were, and ``checked_at``
    keeps naming the last time the hub gave a definite answer rather than
    the last time it was asked — which is what lets a dashboard say "last
    confirmed Tuesday" through an outage instead of silently forgetting it.
    """
    from condor.agents.bundle import agent_home, read_stamp, write_stamp

    home = agent_home(slug)
    stamp = read_stamp(home)
    if stamp is None or not stamp.hub_slug:
        return {"tracked": False}

    if not force and not _stale(stamp.hub_checked_at):
        return _status_payload(stamp)

    result = listing_status(stamp.hub_slug, stamp.hub_version, token=token())
    if result.state == "unknown":
        payload = _status_payload(stamp)
        payload["live_check_failed"] = True
        return payload

    stamp = stamp.model_copy(
        update={"hub_status": result.state, "hub_checked_at": result.checked_at}
    )
    write_stamp(home, stamp)
    return _status_payload(stamp)


# ── Publishing ───────────────────────────────────────────────────────────


def publish(slug: str, token: str, notes: str = "", author_name: str = "") -> Receipt:
    """Upload this install's agent ``slug``, as the bundle an export would hand over.

    Built through FEAT-133's :func:`condor.agents.bundle.build` and rendered
    with its :func:`~condor.agents.bundle.render`, which is to say: **the exact
    bytes ``GET /agents/{slug}/bundle`` returns for the same agent**. There is
    no second serializer, and ``tests/test_hub.py`` compares the two byte for
    byte.

    It therefore inherits ``build``'s refusals, and the one that matters here is
    FEAT-144's: an agent carrying a value that can only be a secret raises
    before anything is sent. That refusal was written for exactly this moment —
    "a published bundle cannot be unpublished out of somebody's download" — so
    it is not re-implemented, re-ranked or softened on this path.

    Nothing is queued, retried or left on disk when this fails. The failure is
    the caller's to report and the operator's to retry; see the module
    docstring on why the sharing outbox's shape is wrong here.
    """
    from condor.agents import bundle as agent_bundle

    if not (token or "").strip():
        raise NoTokenError(
            "Publishing needs a linked Botcamp profile. Connect one in the Agent Hub (Agents → Hub)."
        )
    # Before the build, so a missing agent is answered without stamping its
    # bookkeeping — ``build`` writes ``bundle_version``/``bundle_digest`` on
    # success and an export refused for a missing token should leave no trace.
    bundle = agent_bundle.build(slug, notes=notes, author_name=author_name)
    text = agent_bundle.render(bundle)
    payload = text.encode("utf-8")
    body = json.dumps(
        {
            "slug": bundle.agent.slug,
            "agent_uid": bundle.agent.agent_uid,
            "version": bundle.version,
            "notes": bundle.notes,
            "digest": hashlib.sha256(payload).hexdigest(),
            # The bundle verbatim, as text, so what the hub stores is what this
            # install would have handed over on a USB stick.
            "bundle": text,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    reply = _send("POST", f"{HUB_URL}/agents", token=token.strip(), payload=body)
    _clear_token_if_revoked(reply)
    # No token, no bundle text, no digest — the agent's name and the hub's
    # answer are the whole of what this is allowed to record.
    log.info(
        "hub: submitted '%s' version %d (%d bytes) — hub said %d",
        bundle.agent.slug,
        bundle.version,
        len(payload),
        reply.status,
    )
    receipt = _submission_from_reply(reply, bundle.agent.slug)
    # A fresh submission replaces whatever this slug's prior record said,
    # wholesale — never merged with it. A new revision is a new submission
    # (§5.4) and the old submission_id/listing_id/badge must not leak into
    # this one just because the key is the same agent.
    _store_submission(bundle.agent.slug, receipt)
    return receipt


def _submission_from_reply(reply: Reply, slug: str) -> Receipt:
    """``reply`` as a :class:`Receipt`, true to what the hub actually said.

    A 2xx is the normal ``202`` accepted-for-processing case (§5.2). A ``400``
    is read as the submission lifecycle's own shape — ``{"status":
    "rejected", "reasons": [...]}`` — rather than the generic hub-refusal
    sentence, because that is what a deterministic refusal *is* here: an
    answer about this submission, not a failure to reach the hub. Anything
    else (401, 403, 500, a transport failure, or a 400 that is not that shape)
    still raises, the same as it always did.
    """
    what = f"publishing '{slug}'"
    if 200 <= reply.status < 300:
        return _model(_json(reply, what), Receipt, f"the receipt for '{slug}'")
    if reply.status == 400:
        payload = _decode(reply, what)
        if str(payload.get("status") or "") == "rejected":
            return _model(payload, Receipt, f"the receipt for '{slug}'")
    raise HubError(_refusal(reply, what), status=reply.status)


def unpublish(listing_id: str, token: str) -> None:
    """Withdraw a listing. The account token is the whole authorization.

    **No delete-token capability.** ``condor/sharing/wire.py``'s
    ``delete_token_hash`` exists because that collector "must not know who you
    are", so a revocation has to prove possession of a secret instead of an
    identity. A hub publish is authenticated by an account, and the same account
    authenticates the withdrawal; minting a second capability for it would be
    cargo cult.

    It removes the listing. It does not recall a download, and the surface that
    offers it says so.
    """
    listing_id = _listing_id(listing_id)
    if not (token or "").strip():
        raise NoTokenError(
            "Withdrawing a listing needs a linked Botcamp profile. Connect one in "
            "the Agent Hub (Agents → Hub)."
        )
    reply = _send("DELETE", f"{HUB_URL}/agents/{listing_id}", token=token.strip())
    _clear_token_if_revoked(reply)
    if reply.status < 200 or reply.status >= 300:
        raise HubError(
            _refusal(reply, f"withdrawing '{listing_id}'"), status=reply.status
        )
    log.info("hub: withdrew listing '%s'", listing_id)


def local_submission(slug: str) -> Receipt | None:
    """This agent's most recently stored submission, or ``None``. No network.

    Reads what :func:`publish` or the last :func:`submission_status` poll
    stored, so a dashboard reload can show a pending or rejected submission
    without a hub round trip — the durable half of "resume status after a
    reload" (FEAT-150's design). ``None`` when this agent has never been
    published from this install, or the stored row cannot be read as a
    :class:`Receipt` (a hand-edited ``config.yml``, say).
    """
    submissions = _section().get("submissions")
    row = submissions.get(slug) if isinstance(submissions, dict) else None
    if not isinstance(row, dict):
        return None
    try:
        return Receipt.model_validate(row)
    except Exception:  # noqa: BLE001 - an unreadable stored row is "none on file"
        return None


def _store_submission(slug: str, receipt: Receipt) -> None:
    from config_manager import get_config_manager

    submissions = _section().get("submissions")
    submissions = dict(submissions) if isinstance(submissions, dict) else {}
    submissions[slug] = receipt.model_dump()
    get_config_manager().update_hub(submissions=submissions)


def _published_listing_provenance(listing_id: str) -> tuple[str, str]:
    """``(badge, published_at)`` for a submission's own listing, best-effort.

    ``get()`` is :func:`get`'s pull-side reader of PR #111's
    ``GET /agents/{slug}``, keyed on a catalogue slug and shaped as
    :class:`AgentListing` — neither of which this is. Botcamp ships no
    route a publish actually reaches (bullet 6 of FEAT-146's retarget:
    publishing exists today only as a session-authenticated website upload,
    with nothing for Condor to call), so this keeps :func:`submission_status`
    reading the *old*, forward-looking ``listing_id``-keyed shape it always
    read, independent of whatever the pull side's own model becomes. A hub
    that never answers this at all — true today — degrades to no badge,
    never a crash; :func:`submission_status` already catches
    :class:`HubError` around its one call here for exactly that.
    """
    listing_id = _listing_id(listing_id)
    what = f"listing '{listing_id}'"
    reply = _send("GET", f"{HUB_URL}/agents/{listing_id}")
    if reply.status < 200 or reply.status >= 300:
        raise HubError(_refusal(reply, what), status=reply.status)
    payload = _decode(reply, what)
    return str(payload.get("badge") or ""), str(payload.get("published_at") or "")


def submission_status(slug: str, token: str) -> Receipt:
    """Poll the hub for ``slug``'s current submission and persist the answer.

    ``GET /submissions/{id}`` (§5.3) repeats only ``submission_id``,
    ``status``, ``submitted_at``, ``decided_at``, ``reasons`` and
    ``listing_id`` — never ``slug``/``agent_uid``/``version``/``digest``/
    ``bytes`` again — so those survive here by merging the fresh answer over
    the record :func:`publish` stored, rather than replacing it outright.

    Raises :class:`HubError` if this slug has no submission on file (nothing
    to poll — a caller should check :func:`local_submission` first, the way
    the web route does, to answer "never published" without a network call),
    or if the hub refuses the read, including its own ``403`` for a
    submission a different author credential uploaded (§5.3).
    """
    stored = local_submission(slug)
    if stored is None or not stored.submission_id:
        raise HubError(f"No submission is on record for '{slug}'")
    if not (token or "").strip():
        raise NoTokenError(
            "Checking a submission needs a linked Botcamp profile. Connect one "
            "in the Agent Hub (Agents → Hub)."
        )
    submission_id = _segment(stored.submission_id, "submission id")
    what = f"the status of '{slug}' submission"
    reply = _send("GET", f"{HUB_URL}/submissions/{submission_id}", token=token.strip())
    _clear_token_if_revoked(reply)
    fresh = _model(_json(reply, what), Receipt, what)
    merged = stored.model_copy(
        update={
            "status": fresh.status,
            "decided_at": fresh.decided_at,
            "reasons": fresh.reasons,
            "listing_id": fresh.listing_id,
        }
    )
    if merged.status == "published" and merged.listing_id and not merged.badge:
        # The submission answer does not repeat the badge or an outside host
        # can look at; one listing read is the provenance a dashboard needs
        # to render "Published" honestly. A failure here must not turn a
        # genuinely published submission back into an error — the status
        # itself is already settled.
        try:
            badge, published_at = _published_listing_provenance(merged.listing_id)
            merged = merged.model_copy(
                update={
                    "badge": badge,
                    "published_at": published_at or merged.published_at,
                }
            )
        except HubError:
            log.debug(
                "hub: could not fetch listing '%s' for badge provenance",
                merged.listing_id,
                exc_info=True,
            )
    _store_submission(slug, merged)
    return merged


# ── Device login (PR #111): link to a Botcamp profile, no pasted token ───


def device_start() -> DeviceStart:
    """Begin a device-login attempt, replacing any one this install was tracking.

    No ``client_name``/``scope`` in the body: omitting ``scope`` grants both
    ``agents:read`` and ``agents:publish``, the same all-or-nothing install-wide
    grant the pasted-token flow it replaces always had. A second call while an
    earlier attempt is still ``pending`` simply forgets that earlier
    ``device_code`` — this install only ever tracks the most recent attempt it
    started, and the hub's own ``expires_in`` deadline retires the old one on
    its side regardless.

    Returns a :class:`DeviceStart`, not the :class:`DeviceCode` that was
    parsed: the reply's own ``verification_uri``/``verification_uri_complete``
    stop here, and the ``approval_url`` handed on is built from
    :data:`HUB_URL` (SEC-819). The two are compared only to log a mismatch.
    """
    global _pending_device

    reply = _send("POST", f"{HUB_URL}/condor/device/code", payload=b"{}")
    code = _model(_json(reply, "starting a device login"), DeviceCode, "a device code")
    _warn_if_offsite(code)
    link = approval_url(code.user_code)
    now = time.monotonic()
    with _device_lock:
        _pending_device = {
            "device_code": code.device_code,
            "user_code": code.user_code,
            # The locally built link, cached so every later pending poll
            # answers with the same string the first one did.
            "approval_url": link,
            "interval": max(1, code.interval or 5),
            # Two clocks for two audiences: `deadline` (monotonic) is what this
            # process gates polling on, immune to a wall-clock step; `wall_expires_at`
            # (time.time()) is what a caller renders as a countdown.
            "deadline": now + max(1, code.expires_in or 600),
            "wall_expires_at": time.time() + max(1, code.expires_in or 600),
            "last_polled": 0.0,
        }
    log.info("hub: device login started (user code %s)", code.user_code)
    return DeviceStart(
        user_code=code.user_code,
        approval_url=link,
        expires_in=code.expires_in,
        interval=code.interval,
    )


def device_state() -> DeviceLinkState:
    """This install's current device-login/link state. **Never the credential.**

    Three cases, in the order checked:

    1. Already linked (``config.yml`` carries a token and a profile handle) —
       answered from local config, no hub call at all.
    2. A device login is in flight — polled from the hub at most once per
       ``interval`` seconds (the device-flow contract's own rate limit; a
       caller that asks more often than that gets the locally cached answer
       rather than a second request). On a 200, **this is the one call that
       ever sees the credential**: it is stored via
       :func:`config_manager.ConfigManager.update_hub` immediately and never
       put in the :class:`DeviceLinkState` this function returns. Past the
       600s deadline the attempt reads as ``"expired"`` without a further
       hub call at all — this install stops polling rather than polling
       forever.
    3. Nothing pending and nothing linked — ``"none"``.

    A transition to ``denied``/``expired``/``invalid``/linked is reported for
    exactly the one poll that observed it and then forgotten (the pending
    attempt is cleared).
    """
    section = _section()
    linked_handle = str(section.get("profile_handle") or "")
    if linked_handle and section.get("token"):
        return DeviceLinkState(
            status="linked",
            profile_handle=linked_handle,
            scope=list(section.get("scope") or []),
        )

    with _device_lock:
        pending = dict(_pending_device) if _pending_device else None
    if pending is None:
        return DeviceLinkState(status="none")

    now = time.monotonic()
    if now >= pending["deadline"]:
        # The 600s device-code deadline: stop polling rather than forever,
        # and without spending a request on an attempt the hub has already
        # retired on its own side.
        _clear_pending_device()
        return DeviceLinkState(status="expired")

    def _pending_reply() -> DeviceLinkState:
        return DeviceLinkState(
            status="pending",
            user_code=pending["user_code"],
            approval_url=pending["approval_url"],
            expires_at=pending["wall_expires_at"],
        )

    if now - pending["last_polled"] < pending["interval"]:
        return _pending_reply()

    reply = _send(
        "POST",
        f"{HUB_URL}/condor/device/token",
        payload=json.dumps(
            {"device_code": pending["device_code"]}, separators=(",", ":")
        ).encode("utf-8"),
    )
    with _device_lock:
        if _pending_device is not None:
            _pending_device["last_polled"] = now

    if reply.status == 200:
        payload = _decode(reply, "the device token")
        access_token = str(payload.get("access_token") or "")
        scope_raw = payload.get("scope")
        if isinstance(scope_raw, str):
            scope_list = [s for s in scope_raw.split() if s]
        elif isinstance(scope_raw, list):
            scope_list = [str(s) for s in scope_raw]
        else:
            scope_list = []
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        profile_handle = str(user.get("name") or user.get("id") or "")
        if access_token:
            # The one write of the credential, as soon as it exists — never
            # handed back past this function. The token is issued once per
            # device_code and never reissued, so this is the only place it is
            # ever read.
            from config_manager import get_config_manager

            get_config_manager().update_hub(
                token=access_token,
                profile_handle=profile_handle,
                scope=scope_list,
                user=user,
            )
            log.info("hub: linked to Botcamp profile '%s'", profile_handle)
        _clear_pending_device()
        return DeviceLinkState(
            status="linked",
            profile_handle=profile_handle
            or str(_section().get("profile_handle") or ""),
            scope=scope_list or list(_section().get("scope") or []),
        )

    if reply.status == 400:
        payload = _decode(reply, "the device token")
        error = str(payload.get("error") or "")
        if error == "authorization_pending":
            return _pending_reply()
        if error == "slow_down":
            with _device_lock:
                if _pending_device is not None:
                    _pending_device["interval"] += 5
            return _pending_reply()
        if error == "access_denied":
            _clear_pending_device()
            return DeviceLinkState(status="denied")
        if error == "expired_token":
            _clear_pending_device()
            return DeviceLinkState(status="expired")
        if error == "invalid_grant":
            _clear_pending_device()
            return DeviceLinkState(status="invalid")
        raise HubError(
            f"The hub described the device login in an error this Condor "
            f"does not know: {error!r}",
            status=reply.status,
        )

    raise HubError(_refusal(reply, "polling the device login"), status=reply.status)


class DisconnectResult(BaseModel):
    """What happened to this install's Botcamp link when it was cleared.

    ``revoked`` is ``True`` only once the hub itself confirmed
    ``DELETE /api/condor/me`` (PR #111), ``False`` when a token existed and
    the hub could not be reached or refused the revoke, and ``None`` when
    there was nothing to tell the hub — a pending device login simply
    cancelled, or this install held no token in the first place. The local
    credential is cleared in every case; this only says whether Botcamp's
    own copy of it was actually told, because reporting a revoke that did
    not happen is worse than reporting none.
    """

    revoked: bool | None = None
    error: str = ""


def device_cancel() -> DisconnectResult:
    """Cancel a device-login attempt in flight, or disconnect a linked profile.

    A ``pending`` attempt is simply forgotten locally — the device-flow
    contract has no cancel endpoint, and an unpolled device code just expires
    at the hub on its own at the 600s deadline.

    A ``linked`` profile is disconnected by first asking the hub to revoke
    the presented token (``DELETE /api/condor/me``, PR #111). That call did
    not exist before PR #111, which is why FEAT-148's disconnect cleared only
    the local copy and the module docstring once said "there is nothing to
    tell the hub" — there is now. The local credential, scope and user are
    still cleared **regardless of whether the hub call succeeds**: a network
    failure or a 5xx must not leave this install stuck pretending to be
    connected. What changes is that the caller learns whether the hub's own
    copy was actually revoked, via the returned :class:`DisconnectResult`,
    instead of this function silently assuming it.
    """
    with _device_lock:
        pending = _pending_device is not None

    if pending:
        _clear_pending_device()
        log.info("hub: device login cancelled")
        return DisconnectResult(revoked=None)

    from config_manager import get_config_manager

    current_token = token()
    result = DisconnectResult(revoked=None)
    if current_token:
        try:
            reply = _send("DELETE", f"{HUB_URL}/condor/me", token=current_token)
        except HubError as exc:
            result = DisconnectResult(revoked=False, error=str(exc))
            log.warning(
                "hub: could not revoke the Botcamp token during disconnect: %s", exc
            )
        else:
            if 200 <= reply.status < 300:
                result = DisconnectResult(revoked=True)
            else:
                result = DisconnectResult(
                    revoked=False, error=_refusal(reply, "revoking this token")
                )
                log.warning(
                    "hub: could not revoke the Botcamp token during disconnect (%d)",
                    reply.status,
                )

    get_config_manager().update_hub(token="", profile_handle="", scope=[], user={})
    log.info("hub: disconnected from Botcamp")
    return result


def _clear_pending_device() -> None:
    global _pending_device
    with _device_lock:
        _pending_device = None


# ── Who the linked token identifies, read live (PR #111's `GET /me`) ─────


class MeUser(BaseModel):
    """The account identity ``GET /api/condor/me`` describes a token with."""

    id: str = ""
    name: str = ""
    slug: str = ""


class MeInfo(BaseModel):
    """Who this install's token identifies, and what it may do — read fresh
    from the hub rather than trusted from the device-login record.

    :func:`device_state` stores ``profile_handle`` and ``scope`` once, at
    link time, and never asks again while ``status`` reads ``"linked"``.
    This is the live answer: it is what catches a token Botcamp's own
    profile page revoked out from under this install, before the next
    publish attempt finds out the hard way.
    """

    user: MeUser = Field(default_factory=MeUser)
    scopes: list[str] = Field(default_factory=list)


def me(token: str) -> MeInfo:
    """Who ``token`` identifies right now, and which scopes it carries.

    A 401 means the hub no longer honours this token — revoked from the
    profile's own token list, most likely, rather than anything this
    install did — and :func:`_clear_token_if_revoked` clears this install's
    copy the same as every other token-bearing call does, before the
    :class:`HubError` reaches the caller. The caller does not need to clear
    anything itself; it only needs to stop showing a stale identity.
    """
    if not (token or "").strip():
        raise NoTokenError(
            "No Botcamp profile is linked. Connect one in the Agent Hub (Agents → Hub)."
        )
    reply = _send("GET", f"{HUB_URL}/condor/me", token=token.strip())
    _clear_token_if_revoked(reply)
    payload = _json(reply, "the signed-in Botcamp account")
    data = payload.get("data")
    return _model(
        data if isinstance(data, dict) else {}, MeInfo, "the signed-in Botcamp account"
    )


def _clear_token_if_revoked(reply: Reply) -> None:
    """A 401 from any token-bearing hub call means this install's copy is
    revoked or wrong, never a network or request-shape problem — the token is
    opaque and has no refresh path, so the one thing to do is stop presenting
    it. Cleared **locally only**; there is nothing to tell the hub.
    """
    if reply.status == 401:
        from config_manager import get_config_manager

        get_config_manager().update_hub(token="", profile_handle="", scope=[], user={})
        log.warning("hub: the hub answered 401 — clearing the stored Botcamp token")


# ── The token, which lives in config.yml and is never rendered ───────────


def token() -> str:
    """This install's Botcamp token, or ``""``.

    Stored in ``config.yml``'s ``hub`` section, the way the sharing identity is
    (``condor/sharing/consent.py``). It is never returned by a GET route, never
    logged, and never put in a URL or a body — see :func:`_send`.
    """
    return str(_section().get("token") or "")


def set_token(value: str) -> bool:
    """Store (or clear) the Botcamp token. Returns whether one is now set.

    Clearing is passing an empty string, which is the only way to revoke it
    locally and is offered beside the field for that reason.
    """
    from config_manager import get_config_manager

    cleaned = (value or "").strip()
    get_config_manager().update_hub(token=cleaned)
    return bool(cleaned)


def _section() -> dict:
    """The ``hub`` section of ``config.yml``, or ``{}``.

    Reading must never be the thing that materializes ``config.yml`` on a fresh
    clone — the guard :func:`condor.sharing.consent._cm` sets, for the same
    reason.
    """
    from pathlib import Path

    from config_manager import ConfigManager

    if ConfigManager._instance is None and not Path("config.yml").exists():
        return {}
    try:
        return ConfigManager.instance().get_hub()
    except Exception:  # pragma: no cover - a broken config must not break browsing
        log.debug("hub: could not read config", exc_info=True)
        return {}
