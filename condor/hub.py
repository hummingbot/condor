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
2. *The hub is a discovery and moderation service, not a security boundary.* A
   :data:`VALIDATED` badge is a claim that a human reviewed one submitted
   revision at one point in time. It is carried in the listing, rendered as
   provenance, and read by **nothing in this module that decides whether to
   write**. A badge never shortens the import review — a bundle validated as
   submitted is not the bundle the operator will run after editing it here.
3. *Integrity between hub and client is a content digest over HTTPS, not a
   signature.* :func:`get` reports the bundle's sha256 and :func:`fetch`
   verifies the bytes against it before any caller parses them; FEAT-133's
   per-file digests then catch any later edit. ``author`` is an account claim
   the hub records at upload and nothing binds it to the bytes
   cryptographically — which is why every surface labels it unverified.

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
property straight back.

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
from typing import NamedTuple

from pydantic import BaseModel, Field

from condor.agents.bundle import (
    MAX_BUNDLE_BYTES,
    BundleAuthor,
    BundleFinding,
    BundleRequires,
)
from condor.fetchers._identifiers import IdentifierError, validate_identifier

log = logging.getLogger(__name__)

#: Where the hub lives. **Compiled in, like the sharing collector's address,
#: and no environment variable redirects it** — see the module docstring: this
#: constant is half of the SSRF answer, and ``tests/test_hub.py`` asserts that
#: setting every plausible env name and re-importing changes nothing.
#:
#: The hostname itself is not Condor's decision to make. The service lives in a
#: codebase that is not this one, and standing it up — who reviews a submission
#: for the ``validated`` tier, how a publish token is issued, whether the hub
#: runs FEAT-144's review as a Python dependency or a reimplementation — is
#: TASK-004. Changing it is a code change by design.
HUB_URL = "https://hub.botcamp.xyz/v1"

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


class Listing(BaseModel):
    """One agent the hub offers, as the catalogue describes it.

    Every field is the hub's claim except the two that are checked:
    ``digest``, which :func:`fetch` verifies the downloaded bytes against, and
    ``agent_uid``, which the import path re-derives from the bundle itself
    (FEAT-143's ``plan`` reads the manifest, never this). ``requires`` and
    ``findings`` are carried in FEAT-143's and FEAT-144's own shapes rather
    than copies of them, so a field added to either reaches a hub listing
    without a second definition here.
    """

    listing_id: str
    #: The identity that decides whether installing this is a create or an
    #: update (FEAT-145). Advisory here; the plan derives its own.
    agent_uid: str = ""
    slug: str = ""
    name: str = ""
    description: str = ""
    version: int = 1
    notes: str = ""
    #: Attested by the hub at upload — an account claim, not a signature.
    author: BundleAuthor = Field(default_factory=BundleAuthor)
    #: :data:`VALIDATED` or :data:`COMMUNITY`. Provenance, and nothing in this
    #: module reads it to decide anything.
    badge: str = COMMUNITY
    #: Full lowercase hex sha256 of the bundle bytes. The one integrity check.
    digest: str = ""
    bytes: int = 0
    published_at: str = ""
    requires: BundleRequires = Field(default_factory=BundleRequires)
    #: FEAT-144's review, **as the hub computed it on submission**. The
    #: export-side review is advice to the author, who controls its input; this
    #: one is the only review whose input the author does not control.
    findings: list[BundleFinding] = Field(default_factory=list)
    #: Which review produced ``findings``, so a client can see when the hub's
    #: copy and this install's have drifted apart (TASK-004).
    review_version: str = ""


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


class LinkStart(BaseModel):
    """What ``POST /v1/link/start`` handed back (contract §4.2).

    ``code`` rides in the URL Condor opens in a browser tab; ``verification``
    is the string shown on both ends for the human to compare, the phishing
    defense the short ``code`` cannot be on its own. ``link_id`` is used only
    to poll — it is never rendered, logged, or returned past
    :func:`link_start`'s own caller.
    """

    link_id: str
    code: str
    verification: str
    expires_at: str
    poll_interval_s: int = 3
    #: ``{HUB_URL}/link?code=...`` — built here, from the compiled-in
    #: :data:`HUB_URL`, rather than by the dashboard reassembling the host
    #: itself out of a bare ``code``. One place decides what Condor opens a
    #: browser tab to.
    link_url: str = ""


class LinkState(BaseModel):
    """What a caller may learn about this install's Botcamp link.

    Never the credential — see :func:`link_state`. ``status`` is one of
    ``"none"`` (nothing pending, nothing linked), ``"pending"`` (a pairing is
    in flight; ``code``/``verification``/``expires_at``/``link_url`` are
    set), ``"denied"`` or ``"expired"`` (a pairing just ended that way —
    reported once, to the poll that observed it), or ``"linked"``
    (``profile_handle`` is set).
    """

    status: str = "none"
    code: str = ""
    verification: str = ""
    expires_at: str = ""
    link_url: str = ""
    profile_handle: str = ""


def _link_url(code: str) -> str:
    """``{HUB_URL}/link?code=...``, the tab Condor opens for the admin.

    ``code`` is a short, URL-safe string by contract (``"WXYZ-4821"``), but it
    is still hub-supplied text headed into a URL this process tells a browser
    to open — ``urlencode`` rather than an f-string, so nothing in it can add
    a second query parameter or otherwise reshape the address.
    """
    return f"{HUB_URL}/link?{urllib.parse.urlencode({'code': code})}"


#: The one pairing attempt this install is tracking, or ``None``. Deliberately
#: in-memory and not in ``config.yml``: a pairing is a short-lived, interactive
#: act with an admin watching the result, the same reasoning the module
#: docstring gives for there being no durable publish outbox. A restart mid-pairing
#: simply loses the attempt, which matches the contract's "Condor's own backend
#: is responsible for invalidating its local memory of the previous attempt."
#: Guarded by :data:`_link_lock` because the web routes dispatch these calls
#: through ``asyncio.to_thread``, i.e. a worker thread pool rather than the
#: event loop.
_pending_link: dict | None = None
_link_lock = threading.Lock()


class Reply(NamedTuple):
    """One HTTP exchange's outcome. The seam the tests drive instead of a socket."""

    status: int
    body: bytes


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
        return Reply(response.status, response.read(MAX_BUNDLE_BYTES + 1))


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
        return Reply(exc.code, exc.read(MAX_LISTING_BYTES) or b"")
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

    The hub's ``detail`` is preferred because it is the only text that can say
    *why* — but it is truncated and never trusted to be the whole message: it
    is a string from another service that ends up in an operator's error slot.
    """
    detail = ""
    try:
        body = json.loads(reply.body.decode("utf-8"))
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "")
    except Exception:  # noqa: BLE001 - a hub that answers HTML still gets a sentence
        detail = ""
    if reply.status == 401 or reply.status == 403:
        detail = detail or (
            "The hub refused this token. Set a current one in Settings → Agents."
        )
    return f"The hub refused {what} ({reply.status})" + (
        f": {detail[:400]}" if detail else ""
    )


def _model(payload: dict, model, what: str):
    """``payload`` as ``model``, or the sentence saying the hub's shape is wrong."""
    try:
        return model.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic's own message is the sentence
        raise HubError(
            f"The hub described {what} in a shape this Condor cannot read: {exc}"
        ) from exc


# ── The catalogue ────────────────────────────────────────────────────────


def browse(query: str = "", page: int = 1) -> list[Listing]:
    """What the hub offers, newest first. No account, no token.

    Browsing is anonymous deliberately: the listing is public content, and
    gating it behind a Botcamp login would make the read half useless for the
    operator who has no login — which is most of them on day one. Publishing is
    the act that needs an identity, because it is the act that creates a claim.

    ``query`` is sent as a query *parameter*, URL-encoded, so it never becomes
    a path segment and needs no identifier check — it cannot change which
    endpoint the URL addresses. A listing a reader cannot parse is dropped with
    a log line rather than failing the whole page: one malformed row on the hub
    must not make the catalogue unbrowsable.
    """
    page = max(1, int(page or 1))
    params = urllib.parse.urlencode({"query": query or "", "page": page})
    url = f"{HUB_URL}/agents?{params}"
    payload = _json(_send("GET", url), "the agent catalogue")
    rows = payload.get("listings")
    if not isinstance(rows, list):
        raise HubError("The hub's catalogue did not carry a list of listings")
    listings: list[Listing] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            listings.append(Listing.model_validate(row))
        except Exception:  # noqa: BLE001 - one bad row is not a broken catalogue
            log.info("hub: skipping a listing this Condor cannot read")
    return listings


def get(listing_id: str) -> Listing:
    """One listing, with the digest :func:`fetch` will hold the bytes to.

    ``listing_id`` goes through ``validate_identifier`` **before anything is
    sent** — the order the identifier guard's own docstring requires, and the
    reason a ``../``, a ``?``, a ``#`` or a space is an ``IdentifierError``
    here rather than a request to somewhere else.
    """
    listing_id = _listing_id(listing_id)
    url = f"{HUB_URL}/agents/{listing_id}"
    payload = _json(_send("GET", url), f"listing '{listing_id}'")
    return _model(payload, Listing, f"listing '{listing_id}'")


def fetch(listing_id: str, listing: "Listing | None" = None) -> bytes:
    """The bundle behind a listing, **verified against the listing's digest**.

    The digest check is what makes it safe for the preview and the install to
    each do their own fetch rather than passing bytes through the browser: both
    are held to the same listing digest, so they are the same bundle or the
    second one raises. It happens here, before any caller parses anything —
    :func:`condor.agents.bundle.parse` is a tolerant reader by design and
    handing it unverified bytes would mean the integrity check ran, at best,
    after the manifest had already been read.

    A listing with no digest is refused rather than fetched on trust. A hub
    that cannot say what it is serving is not offering integrity, and silently
    skipping the check is how a verification becomes decoration.

    The download is addressed with the id the **caller** gave, after
    ``_listing_id``, and never with the ``listing_id`` the reply carried. The
    reply's is an unvalidated string from another service, and interpolating it
    into a path would let a hub answer re-aim the download — the compiled-in
    base would still hold the host, but the whole point of the guard is that no
    unchecked value reaches a path segment, whoever wrote it. A reply naming a
    different listing is refused instead, since one of the two answers is then
    about an agent nobody asked for.
    """
    listing_id = _listing_id(listing_id)
    # A caller that already holds the listing passes it rather than making this
    # ask again: the route needs it for the provenance it renders, and a second
    # GET to somebody else's service for a value in hand is a request nobody
    # asked for. It is re-checked below regardless, so passing one in cannot
    # skip anything.
    listing = listing if listing is not None else get(listing_id)
    if listing.listing_id and listing.listing_id != listing_id:
        raise HubError(
            f"Asked the hub for listing '{listing_id}' and it described "
            f"'{listing.listing_id}' — nothing was downloaded"
        )
    digest = (listing.digest or "").strip().lower()
    if not digest:
        raise HubError(
            f"Listing '{listing_id}' carries no digest, so the bundle it serves "
            "cannot be verified — nothing was downloaded"
        )
    url = f"{HUB_URL}/agents/{listing_id}/bundle"
    reply = _send("GET", url)
    if reply.status < 200 or reply.status >= 300:
        raise HubError(
            _refusal(reply, f"the bundle of '{listing_id}'"), status=reply.status
        )
    if len(reply.body) > MAX_BUNDLE_BYTES:
        raise HubError(
            f"The bundle of '{listing_id}' is over the {MAX_BUNDLE_BYTES}-byte limit"
        )
    got = hashlib.sha256(reply.body).hexdigest()
    if got != digest:
        raise HubError(
            f"The bundle the hub served for '{listing_id}' does not match the "
            f"digest its listing reported (expected {digest[:12]}…, got "
            f"{got[:12]}…) — nothing was parsed"
        )
    log.info("hub: fetched %s (%d bytes, digest ok)", _log_safe(url), len(reply.body))
    return reply.body


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
            "Publishing needs a linked Botcamp profile. Connect one in Settings → Agents."
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
            "Settings → Agents."
        )
    reply = _send("DELETE", f"{HUB_URL}/agents/{listing_id}", token=token.strip())
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
            "in Settings → Agents."
        )
    submission_id = _segment(stored.submission_id, "submission id")
    what = f"the status of '{slug}' submission"
    reply = _send("GET", f"{HUB_URL}/submissions/{submission_id}", token=token.strip())
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
            listing = get(merged.listing_id)
            merged = merged.model_copy(
                update={
                    "badge": listing.badge,
                    "published_at": listing.published_at or merged.published_at,
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


# ── Account pairing (FEAT-148): link to a Botcamp profile, no pasted token ──


def link_start() -> LinkStart:
    """Begin a pairing attempt, replacing any one this install was tracking.

    No token: contract §4.2 is the one pairing endpoint that needs none, since
    there isn't a credential yet. A second call while an earlier attempt is
    still ``pending`` simply forgets that earlier ``link_id`` — the contract
    puts that bookkeeping on Condor's side, not the hub's — so this install
    only ever tracks the most recent attempt it started.
    """
    global _pending_link

    reply = _send("POST", f"{HUB_URL}/link/start", payload=b"{}")
    start = _model(_json(reply, "starting a pairing"), LinkStart, "a pairing start")
    start = start.model_copy(update={"link_url": _link_url(start.code)})
    with _link_lock:
        _pending_link = {
            "link_id": start.link_id,
            "code": start.code,
            "verification": start.verification,
            "expires_at": start.expires_at,
            "poll_interval_s": max(1, start.poll_interval_s or 3),
            "last_polled": 0.0,
        }
    log.info("hub: pairing started (code %s)", start.code)
    return start


def link_state() -> LinkState:
    """This install's current pairing/link state. **Never the credential.**

    Three cases, in the order checked:

    1. Already linked (``config.yml`` carries a token and a profile handle) —
       answered from local config, no hub call at all.
    2. A pairing is in flight — polled from the hub at most once per
       ``poll_interval_s`` (contract §4.3's own rate limit on this endpoint;
       a caller that asks more often than that gets the locally cached answer
       rather than a second request). On ``accepted``, **this is the one call
       that ever sees the credential**: it is stored via
       :func:`config_manager.ConfigManager.update_hub` immediately and never
       put in the :class:`LinkState` this function returns.
    3. Nothing pending and nothing linked — ``"none"``.

    A transition to ``denied``/``expired``/``accepted`` is reported for
    exactly the one poll that observed it and then forgotten (the pending
    attempt is cleared), the same "answered once" shape the credential itself
    gets in the contract.
    """
    section = _section()
    linked_handle = str(section.get("profile_handle") or "")
    if linked_handle and section.get("token"):
        return LinkState(status="linked", profile_handle=linked_handle)

    with _link_lock:
        pending = dict(_pending_link) if _pending_link else None
    if pending is None:
        return LinkState(status="none")

    now = time.monotonic()
    if now - pending["last_polled"] < pending["poll_interval_s"]:
        return LinkState(
            status="pending",
            code=pending["code"],
            verification=pending["verification"],
            expires_at=pending["expires_at"],
            link_url=_link_url(pending["code"]),
        )

    link_id = _segment(pending["link_id"], "link id")
    reply = _send("GET", f"{HUB_URL}/link/{link_id}")
    if reply.status == 404:
        # A link_id the hub never issued, or one already fully consumed and
        # forgotten (contract §4.3) — either way, nothing left to track.
        _clear_pending()
        return LinkState(status="expired")
    payload = _json(reply, "the pairing status")
    status = str(payload.get("status") or "")

    if status == "pending":
        with _link_lock:
            if _pending_link is not None:
                _pending_link["last_polled"] = now
                _pending_link["expires_at"] = (
                    payload.get("expires_at") or pending["expires_at"]
                )
        return LinkState(
            status="pending",
            code=pending["code"],
            verification=pending["verification"],
            expires_at=str(payload.get("expires_at") or pending["expires_at"]),
            link_url=_link_url(pending["code"]),
        )

    if status in ("denied", "expired"):
        _clear_pending()
        return LinkState(status=status)

    if status == "accepted":
        credential = str(payload.get("credential") or "")
        profile_handle = str(payload.get("profile_handle") or "")
        if credential:
            # The one write of the credential, as soon as it exists — never
            # handed back past this function, per the contract's "the
            # credential arrives only in the backend poll response; no token
            # in URL, fragment, browser response or log."
            from config_manager import get_config_manager

            get_config_manager().update_hub(
                token=credential, profile_handle=profile_handle
            )
            log.info("hub: linked to Botcamp profile '%s'", profile_handle)
        _clear_pending()
        return LinkState(
            status="linked",
            profile_handle=profile_handle
            or str(_section().get("profile_handle") or ""),
        )

    raise HubError(
        f"The hub described the pairing in a status this Condor does not "
        f"know: {status!r}"
    )


def link_cancel() -> None:
    """Cancel a pairing in flight, or disconnect a linked profile.

    Whichever this install is currently holding: a ``pending`` attempt is
    cancelled at the hub (contract §4.4) and forgotten locally; a ``linked``
    profile is disconnected by clearing the stored credential and handle —
    **locally, and regardless of whether the hub can be reached**, per
    contract §4.5's "Clearing the link removes local credential and reflects
    disconnected status even if the hub is unavailable." There is nothing to
    tell the hub in that second case: the credential being cleared is the
    whole of what "disconnect" means on Condor's side.
    """
    with _link_lock:
        pending = dict(_pending_link) if _pending_link else None

    if pending is not None:
        link_id = _segment(pending["link_id"], "link id")
        reply = _send("DELETE", f"{HUB_URL}/link/{link_id}")
        _clear_pending()
        # A 409 means the hub says it was already accepted — the contract
        # says to treat that as "assume it went through", not as an error to
        # surface, since the credential may already be in flight to us.
        if reply.status not in (200, 404, 409):
            raise HubError(
                _refusal(reply, "cancelling the pairing"), status=reply.status
            )
        log.info("hub: pairing cancelled")
        return

    from config_manager import get_config_manager

    get_config_manager().update_hub(token="", profile_handle="")
    log.info("hub: disconnected from Botcamp")


def _clear_pending() -> None:
    global _pending_link
    with _link_lock:
        _pending_link = None


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
