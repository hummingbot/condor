"""The scrubber: replace what we know, then pattern-match what we don't.

Two tiers, applied in this order, because the first is *exact* and the second
is a *guess*.

**Tier 1 — known-value substitution.** The install already holds the values
that matter: its server names and base URLs, its LLM provider keys, its
Telegram token, its wallet addresses, its user ids and usernames, its home
directory. Every occurrence of each, anywhere in a turn, is replaced. This is
the important move, and it is the same principle
``condor.runtime.conversations._redact`` states — redact against a contract,
not a hunch — applied to *values* instead of keys. We are not guessing which
string is a secret; we are replacing strings we know are ours.

**Tier 2 — structural patterns.** Only for shapes that cannot plausibly be
anything else: an EVM address, a 64-hex blob, a base58 address, a prefixed API
token, a long mixed-case secret run, an email, a URL carrying a query string or
userinfo, an IP, a seed-phrase-shaped run of words, a 64-byte Solana keypair
array. Everything here is
best-effort by construction, and it now has two consumers with very different
safety nets behind it. An explicit share ends in a dialog that shows the user
the exact bytes before anything is sent, so a pattern that misses is at least
visible. The sweep in :mod:`condor.sharing.sweep` sends what nobody has read:
there **the scrubber is the last gate**, and the compensating controls are the
ones that module's docstring holds — a narrow definition of what may be taken
at all, a chip in the header that cannot be dismissed while it happens, and
revocation after. Calibrate a new pattern against the sweep rather than the
dialog. It is the stricter of the two callers, and the "nothing was replaced"
the dialog prints is only ever as honest as the pattern that produced it.

**Pseudonyms are stable and one-way.** ``TAG_{hmac_sha256(share_secret, value)[:6]}``.
Within a transcript the same wallet is the same ``SOL_ADDR_a3f91c`` in every
turn, so the agent's reasoning about "that wallet" still reads coherently. The
secret never leaves the box, so the mapping is not reversible by us, and two
installs sharing the same address produce different pseudonyms.

**Quantities are deliberately not scrubbed.** Balances, sizes, PnL and prices
survive: a corpus in which nobody can tell whether the agent computed the right
answer defeats the point of collecting it. Once the wallet, the server and the
user are pseudonymous, a number is not on its own an identifier. The consent
copy says so rather than hiding it.

This module imports nothing from :mod:`condor.telemetry` — see the package
docstring for why that is load-bearing rather than tidy.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import types
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from pydantic_core import PydanticUndefined

from condor.runtime.conversations import TurnEntry
from condor.runtime.secrets import HEX64_RE as _HEX64_RE
from condor.runtime.secrets import KEYPAIR_ARRAY_RE as _KEYPAIR_RE
from condor.runtime.secrets import MNEMONIC as _MNEMONIC
from condor.runtime.secrets import SEED_CANDIDATE_RE as _WORD_RUN_RE
from condor.runtime.secrets import SOLANA_KEYPAIR as _SOLANA_KEYPAIR
from condor.runtime.secrets import bip39_words, keypair_array_spans, phrase_spans

log = logging.getLogger(__name__)

# Every category the scrubber can report, with the tag its pseudonyms carry.
# Tier 1 names what the *install* knew; tier 2 names what a *pattern* caught.
# The full set is always present in the counts, zeros included, so a build whose
# scrubber silently stopped matching shows up on the server as a class of shares
# with all-zero counts — the early-warning role the collector's ``rejected``
# counter already plays.
TIER1_CATEGORIES = (
    "known_key",
    "known_server",
    "known_url",
    "known_wallet",
    "known_user",
    "known_path",
)
TIER2_CATEGORIES = (
    "evm_addr",
    "hex64",
    "sol_addr",
    "api_key",
    "secret",
    "email",
    "url",
    "ip",
    "seed_phrase",
    "solana_keypair",
)
CATEGORIES = TIER1_CATEGORIES + TIER2_CATEGORIES

# The shapes ``condor.runtime.secrets`` is certain enough to *eat* at ingress,
# each mapped to the tier-2 category that catches the same shape here.
#
# The two modules were already meant to share one calibration, and for the
# phrase shape they did; the keypair array was certain at ingress and invisible
# at egress for a release (SEC-331). That gap is not a missing regex so much as
# a missing statement that the two sets have to match, because the ingress gate
# does not cover for this one: ``secrets.redact`` runs on what the *user typed*,
# while a key reaches a transcript through a tool payload — a read of
# ``id.json``, a ``run_code`` stdout — that no funnel ever saw, and archived
# turns predate ingress redaction entirely. For those, this module is the last
# gate. The guard test in ``tests/test_sharing_scrub.py`` fails when
# ``secrets.KINDS`` grows a certain kind this table does not name.
CERTAIN_KIND_CATEGORIES: dict[str, str] = {
    _MNEMONIC: "seed_phrase",
    _SOLANA_KEYPAIR: "solana_keypair",
}

_TAGS = {
    "known_key": "API_KEY",
    "known_server": "SERVER",
    "known_url": "URL",
    "known_wallet": "WALLET",
    "known_user": "USER",
    "known_path": "HOME",
    "evm_addr": "EVM_ADDR",
    "hex64": "HEX64",
    "sol_addr": "SOL_ADDR",
    "api_key": "API_KEY",
    "secret": "SECRET",
    "email": "EMAIL",
    "url": "URL",
    "ip": "IP",
    "seed_phrase": "SEED_PHRASE",
    "solana_keypair": "SOLANA_KEYPAIR",
}

# Categories whose value is case-insensitive on the wire, so the pseudonym has
# to be too: a checksummed EVM address and its lowercase spelling are the same
# address, and a transcript that gave them two pseudonyms would read as two.
_CASE_FOLDED = frozenset({"evm_addr", "hex64", "ip", "email", "known_url"})

# The shortest known value worth substituting. Below this a "known value" is
# more likely to be a fragment of an unrelated word than an identifier — and a
# purely numeric one needs a longer floor still, because a six-digit user id and
# a six-digit price are the same string.
_MIN_KNOWN_CHARS = 4
_MIN_KNOWN_DIGITS = 6

# Nesting past this is not walked, and what sits there is elided rather than
# emitted: the payload came from a tool we do not own, so an unwalked value is
# one this module cannot promise is clean.
#
# One deeper than ``_REDACT_MAX_DEPTH`` in ``condor.runtime.conversations`` so
# the two budgets line up where it matters. ``_redact`` runs on a tool call's
# ``input`` dict from depth 0, while :meth:`Scrubber.turn` enters at the *call*
# and reaches ``input`` at depth 1 — the extra level pays for that offset, so a
# value ``_redact`` kept on disk is a value this still walks.
_MAX_DEPTH = 7

# What replaces a container sitting at the cap — ``_redact``'s own marker, so a
# transcript reads the same whichever gate elided it.
_ELIDED = "…"

# ── Tier 2 patterns ──────────────────────────────────────────────────────
#
# Order is load-bearing and is the order of :data:`_PATTERNS` below: the most
# specific shape claims a run before a broader one can. ``0xabc…`` is an EVM
# address before it is a 42-character alphanumeric run, and a base58 string is
# an address before it is "a long token with no whitespace".
#
# The delimiter guard is a lookaround on ``[A-Za-z0-9_-]`` rather than ``\b``,
# and that difference is the whole reason order can be trusted. ``\b`` fires
# between a hyphen and a letter, so a Binance client order id
# (``x-XEKWYICX…``) would present its tail as a standalone base58 token. The
# lookaround refuses to start or end a match inside an identifier, so hyphenated
# ids, bot names and trading pairs are structurally out of reach.

_NOT_ID_BEFORE = r"(?<![A-Za-z0-9_\-])"
_NOT_ID_AFTER = r"(?![A-Za-z0-9_\-])"

_EVM_RE = re.compile(r"0x[0-9a-fA-F]{40}" + _NOT_ID_AFTER)
_TOKEN_RE = re.compile(
    _NOT_ID_BEFORE
    + r"(?:sk-ant-|sk-proj-|sk-|xoxb-|xoxp-|xoxa-|ghp_|gho_|ghs_|github_pat_|AKIA|AIza)"
    r"[A-Za-z0-9_\-]{8,}"
)
# Base58 excludes 0, O, I and l on purpose — that is what makes a 32–44 run of
# this alphabet an address rather than a word.
_SOL_RE = re.compile(_NOT_ID_BEFORE + r"[1-9A-HJ-NP-Za-km-z]{32,44}" + _NOT_ID_AFTER)
# A long run with no whitespace that holds *both* a digit and a letter. The two
# lookaheads are what keep a 32-character English compound or a long decimal out
# of it; a real secret is mixed by construction. It is the last net, so anything
# the specific patterns already claimed never reaches it.
_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9+/=_\-])"
    r"(?=[A-Za-z0-9+/=]*[0-9])(?=[A-Za-z0-9+/=]*[A-Za-z])"
    r"[A-Za-z0-9+/=]{32,}"
    r"(?![A-Za-z0-9+/=])"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s<>\"']+")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])"
)
# The shapes a recovery phrase can be pasted in, the wordlist that decides
# whether a run of words is one, the scan that finds the runs, and the
# ``[64 ints]`` keypair array with the 0–255 bound that decides *it*, all live
# in ``condor.runtime.secrets``. That module answers the same question at
# *ingress* — before a pasted phrase reaches the model or the first disk write
# — and two copies of a detector is two calibrations, only one of which anyone
# is ever looking at. What stays here is what is the scrubber's own business:
# the pseudonym, the count, and the category name on the wire.
#
# ``bip39_words`` is re-exported for the guard test that asserts the vendored
# list is the canonical one.


def _octets_ok(match: str) -> bool:
    return all(part.isdigit() and int(part) <= 255 for part in match.split("."))


# ── What the scrubber covers ─────────────────────────────────────────────
#
# ``wire.envelope`` posts ``model_dump()`` of the *whole* entry, so every field
# this module does not touch travels verbatim. Naming the fields here — text,
# thought, tool_calls — would leave the redaction rule owned by one module and
# silently depended on by another, and it would fail **open**: a field added
# later by someone who has never opened ``condor/sharing/`` would ship
# unredacted, and no test would fail, because none of them enumerate the model.
#
# So coverage is derived from ``TurnEntry`` itself. Each field is placed in a
# bucket by its declared type:
#
#   ``TEXT``     a string — scrubbed through :meth:`Scrubber.text`
#   ``PAYLOAD``  a container — walked by :meth:`Scrubber.payload`
#   ``SCALAR``   a number or a flag — no free text can hide in one
#
# ``extra="ignore"`` on ``TurnEntry`` makes the declared fields the whole
# surface, so the enumeration is complete by construction, and a new text field
# is redacted the day it is added rather than the day someone notices.
#
# A field whose type fits no bucket — a nested model, say — is left
# ``UNCLASSIFIED`` and never travels: :meth:`Scrubber.turn` drops it to its
# default, or refuses the share outright if it has none. That is the same move
# ``ATTRIBUTABLE_SURFACES`` makes for an unrecognised surface. The guard test in
# ``tests/test_sharing_scrub.py`` fails on the same condition, so a build breaks
# before it can take that path.

TEXT = "text"
PAYLOAD = "payload"
SCALAR = "scalar"
UNCLASSIFIED = ""

BUCKETS = (TEXT, PAYLOAD, SCALAR)

_SCALAR_TYPES = (bool, int, float)
_PAYLOAD_TYPES = (list, tuple, set, frozenset, dict)


def classify(annotation) -> str:
    """The bucket one field annotation falls into, or :data:`UNCLASSIFIED`."""
    if annotation is str:
        return TEXT
    if annotation in _SCALAR_TYPES:
        return SCALAR
    if annotation is Any:
        return PAYLOAD  # unknown at rest, and ``payload`` walks anything
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        # ``str | None`` is text. A union of buckets is walked as a payload,
        # which handles a string, a container and a scalar alike.
        inner = {classify(arg) for arg in get_args(annotation) if arg is not type(None)}
        if UNCLASSIFIED in inner or not inner:
            return UNCLASSIFIED
        return inner.pop() if len(inner) == 1 else PAYLOAD
    container = origin or annotation
    if isinstance(container, type) and issubclass(container, _PAYLOAD_TYPES):
        return PAYLOAD
    return UNCLASSIFIED


#: ``{field name: bucket}`` for every field ``TurnEntry`` declares, computed
#: once at import. The guard test reads this.
TURN_FIELDS: dict[str, str] = {
    name: classify(field.annotation) for name, field in TurnEntry.model_fields.items()
}


def _dropped(name: str, value):
    """An unclassified field, removed from the share rather than sent raw.

    Fail closed: a shape this module does not know how to walk is a shape it
    cannot promise is clean. ``TurnEntry``'s growth contract says every field
    past ``role`` carries a default that reads as "not recorded", so dropping to
    that default is both valid and honest. A field with no default cannot be
    dropped, so the share does not go at all — the sweep catches that per
    conversation and the button reports it.
    """
    default = TurnEntry.model_fields[name].get_default(call_default_factory=True)
    if default is PydanticUndefined:
        raise ValueError(
            f"Sharing cannot classify the required turn field {name!r}; "
            "teach condor.sharing.scrub.classify about it before sharing."
        )
    log.warning("Sharing dropped the unclassified turn field %r from a share", name)
    return default


class Scrubber:
    """One share's substitution table and the counts it accumulated.

    Built once per share and then applied to every turn, so a pseudonym is
    stable across the whole transcript rather than only within a line.
    """

    def __init__(self, secret: str, known: list[tuple[str, str]] | None = None):
        self._secret = (secret or "").encode("utf-8")
        self.counts: dict[str, int] = {name: 0 for name in CATEGORIES}
        # Longest first: a base URL has to be replaced whole before the host
        # inside it is replaced on its own, or the result is a pseudonym
        # embedded in the remains of a URL.
        self._known = sorted(
            {
                (value, category)
                for value, category in (known or [])
                if _worth_substituting(value)
            },
            key=lambda item: (-len(item[0]), item[0]),
        )

    # ── Pseudonyms ──

    def pseudonym(self, category: str, value: str) -> str:
        """``TAG_xxxxxx`` — stable for this value on this install, and one-way.

        Keyed on the install's ``share_secret``, which is never transmitted, so
        the same wallet gets a different pseudonym on every install and none of
        them can be reversed into the address.
        """
        tag = _TAGS.get(category, "VALUE")
        if category == "seed_phrase":
            return tag  # one marker; a phrase has no identity worth preserving
        material = value.lower() if category in _CASE_FOLDED else value
        digest = hmac.new(self._secret, material.encode("utf-8"), hashlib.sha256)
        return f"{tag}_{digest.hexdigest()[:6]}"

    def _hit(self, category: str, value: str) -> str:
        self.counts[category] = self.counts.get(category, 0) + 1
        return self.pseudonym(category, value)

    # ── Text ──

    def text(self, value: str) -> str:
        """One string, both tiers, in order."""
        if not value:
            return value
        out = self._tier1(value)
        return self._tier2(out)

    def _tier1(self, value: str) -> str:
        for known, category in self._known:
            if known in value:
                replacement = self.pseudonym(category, known)
                self.counts[category] += value.count(known)
                value = value.replace(known, replacement)
        return value

    def _tier2(self, value: str) -> str:
        for category, pattern, handler in _PATTERNS:
            value = pattern.sub(lambda m, c=category, h=handler: h(self, c, m), value)
        return value

    # ── Structures ──

    def payload(self, value, depth: int = 0):
        """A tool call's arguments or result, walked. Values only, never keys.

        A key is the tool author's contract — ``_redact`` already replaced the
        credential-shaped ones by name before this ever reached disk. A value is
        what a model or an exchange wrote, so it is what this has to look at.
        """
        if depth >= _MAX_DEPTH:
            # Fail closed, like ``_dropped`` and like ``_redact``'s own cap: a
            # leaf string is cheap to scrub and has nothing below it to walk, so
            # it still gets a pseudonym; anything with more structure under it is
            # elided rather than handed to the collector unread.
            if isinstance(value, str):
                return self.text(value)
            if isinstance(value, (dict, list, tuple)):
                return _ELIDED
            return value
        if isinstance(value, dict):
            return {str(k): self.payload(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.payload(v, depth + 1) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value

    def turn(self, entry: TurnEntry) -> TurnEntry:
        """One transcript line, scrubbed into a new entry.

        Every field the model declares, by its declared type — see the note
        above :data:`TURN_FIELDS` for why the fields are read off the model
        rather than named here.

        ``TurnEntry`` *is* the wire format for a share — the model already on
        disk, not a parallel extraction path — so the scrubbed turn is the same
        type and every existing reader of a transcript can read a share.
        """
        update: dict = {}
        for name, bucket in TURN_FIELDS.items():
            if bucket == SCALAR:
                continue
            value = getattr(entry, name)
            if bucket == TEXT:
                update[name] = self.text(value) if isinstance(value, str) else value
            elif bucket == PAYLOAD:
                # Each element from depth 0, so a tool call is walked exactly as
                # deep as it was when this loop named ``tool_calls`` itself.
                update[name] = (
                    [self.payload(item) for item in value]
                    if isinstance(value, (list, tuple))
                    else self.payload(value)
                )
            else:
                update[name] = _dropped(name, value)
        return entry.model_copy(update=update)


# ── Tier-2 handlers ──────────────────────────────────────────────────────


def _plain(scrubber: "Scrubber", category: str, match: re.Match) -> str:
    return scrubber._hit(category, match.group(0))


def _ipv4(scrubber: "Scrubber", category: str, match: re.Match) -> str:
    text = match.group(0)
    return scrubber._hit(category, text) if _octets_ok(text) else text


def _url(scrubber: "Scrubber", category: str, match: re.Match) -> str:
    """Replace only a URL that carries userinfo or a query string.

    A bare ``https://docs.hummingbot.org/v2`` is documentation, and a corpus
    that lost it lost part of what the agent was reasoning about. Credentials
    live in the userinfo and in the query, so those are what goes — and the
    scheme and host stay, because "which service was this" is the useful half.
    """
    text = match.group(0).rstrip(".,;:)]}\"'")
    tail = text[len(text.split("://", 1)[0]) + 3 :]
    authority = tail.split("/", 1)[0]
    if "@" not in authority and "?" not in text:
        return match.group(0)
    scheme = text.split("://", 1)[0]
    host = authority.split("@")[-1]
    kept = f"{scheme}://{host}/{scrubber._hit(category, text)}"
    return kept + match.group(0)[len(text) :]


def _seed_phrase(scrubber: "Scrubber", category: str, match: re.Match) -> str:
    """Replace each maximal run of wordlist entries inside the candidate.

    The regex only found a candidate of the right *shape*;
    :func:`condor.runtime.secrets.phrase_spans` decides which parts of it are
    really a phrase, and returns those parts as offsets. Everything between
    them — the prose a phrase was pasted into, the "and it is not working"
    after it — is re-emitted byte for byte, line breaks and numbering intact.
    """
    src = match.group(0)
    spans = phrase_spans(src)
    if not spans:
        return src
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(src[cursor:start])
        out.append(scrubber._hit(category, src[start:end]))
        cursor = end
    out.append(src[cursor:])
    return "".join(out)


def _keypair(scrubber: "Scrubber", category: str, match: re.Match) -> str:
    """Replace the candidate only if every one of its 64 elements is a byte.

    The regex found the *shape* — a bracketed run of exactly 64 small ints —
    and :func:`condor.runtime.secrets.keypair_array_spans` decides whether that
    run is really a key, the same division of labour ``_seed_phrase`` makes with
    :func:`phrase_spans` and ``_ipv4`` makes with ``_octets_ok``. The bound is
    the whole guard: a 64-long list of numbers is not rare in a tool result, and
    a corpus in which any of them came back as ``SOLANA_KEYPAIR_a3f91c`` would
    be silently corrupted. Out-of-range elements leave it byte for byte, exactly
    as ``999.1.1.1`` survives ``_ipv4``.
    """
    src = match.group(0)
    return scrubber._hit(category, src) if keypair_array_spans(src) else src


# Order matters — see the note above the patterns.
_PATTERNS: tuple[tuple[str, re.Pattern, object], ...] = (
    ("seed_phrase", _WORD_RUN_RE, _seed_phrase),
    ("solana_keypair", _KEYPAIR_RE, _keypair),
    ("url", _URL_RE, _url),
    ("email", _EMAIL_RE, _plain),
    ("hex64", _HEX64_RE, _plain),
    ("evm_addr", _EVM_RE, _plain),
    ("api_key", _TOKEN_RE, _plain),
    ("sol_addr", _SOL_RE, _plain),
    ("secret", _SECRET_RE, _plain),
    ("ip", _IPV4_RE, _ipv4),
    ("ip", _IPV6_RE, _plain),
)


# ── The install's own values ─────────────────────────────────────────────


def _worth_substituting(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    floor = _MIN_KNOWN_DIGITS if value.isdigit() else _MIN_KNOWN_CHARS
    return len(value) >= floor


def install_values(user_id: int | str | None = None) -> list[tuple[str, str]]:
    """Everything this install knows about itself that must not leave it.

    Read defensively: a broken config, a missing preference file or an
    unreadable home directory must degrade the table, never fail the share.
    Tier 2 is still behind it and the user still sees the result.
    """
    values: list[tuple[str, str]] = []

    def add(value, category: str) -> None:
        if isinstance(value, str) and value.strip():
            values.append((value.strip(), category))
        elif isinstance(value, int):
            values.append((str(value), category))

    # Servers: the name is what the agent says out loud, the URL is what it
    # calls, and the credentials are what config.yml holds for it.
    try:
        from config_manager import get_config_manager

        cm = get_config_manager()
        for name, server in (cm.list_servers() or {}).items():
            add(name, "known_server")
            host = str((server or {}).get("host") or "")
            port = (server or {}).get("port")
            if host:
                add(host, "known_url")
                if port:
                    add(f"{host}:{port}", "known_url")
                    for scheme in ("http", "https"):
                        add(f"{scheme}://{host}:{port}", "known_url")
            add((server or {}).get("username"), "known_user")
            add((server or {}).get("password"), "known_key")
        for user in cm.get_all_users() or []:
            add((user or {}).get("user_id"), "known_user")
            add((user or {}).get("username"), "known_user")
    except Exception:  # noqa: BLE001 - a share must not need a healthy config
        log.debug("Sharing could not read the config for known values", exc_info=True)

    # Provider credentials, from the environment this process was given. Names
    # only ever reach telemetry; the values only ever reach this table.
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "TELEGRAM_TOKEN",
        "CUSTOM_LLM_API_KEY",
    ):
        add(os.environ.get(var, ""), "known_key")

    # This install's VAPID signing key (FEAT-083). Tier 1 and not a tier-2
    # pattern on purpose: a raw P-256 scalar is 43 base64url characters, which
    # is also an id, a digest and half the opaque strings in a transcript, so
    # the shape is not decidable -- but the *value* is one this install knows,
    # which is exactly what this table is for. It reaches a transcript by one
    # plausible route, and it is the likeliest one: an operator debugging "push
    # stopped working" pastes the key file into the chat.
    try:
        from condor.push import configured_private_key

        add(configured_private_key(), "known_key")
    except Exception:  # noqa: BLE001 - no key is the common case, not a failure
        log.debug("Sharing could not read the VAPID key", exc_info=True)

    # This user's own saved endpoints and wallets.
    if user_id is not None:
        try:
            from condor.preferences import (
                get_custom_providers,
                get_gateway_prefs,
                load_user_data_for,
            )

            user_data = load_user_data_for(int(user_id))
            for provider in get_custom_providers(user_data) or []:
                add((provider or {}).get("base_url"), "known_url")
                add((provider or {}).get("api_key"), "known_key")
            for wallet in get_gateway_prefs(user_data).get("wallet_networks") or {}:
                add(wallet, "known_wallet")
        except Exception:  # noqa: BLE001
            log.debug("Sharing could not read user preferences", exc_info=True)

    # The home directory: every path in a traceback or a tool result carries it,
    # and it is the operator's username spelled out.
    try:
        home = str(Path.home())
        if home and home not in ("/", ""):
            add(home, "known_path")
    except Exception:  # noqa: BLE001
        pass

    return values


def scrubber(
    *,
    secret: str,
    user_id: int | str | None = None,
    known: list[tuple[str, str]] | None = None,
) -> Scrubber:
    """One share's :class:`Scrubber`, with the install's table already built.

    The factory exists so a caller that scrubs turn by turn — :func:`bound`,
    which redacts only the turns it admits — builds the known-value table the
    same way :func:`scrub` does, rather than reassembling it at the call site.
    Read ``counts`` off the returned object once the last turn has gone through.

    ``known`` is injectable so a test can state the install's values instead of
    inheriting the developer's; production passes ``user_id`` and lets
    :func:`install_values` build it.
    """
    return Scrubber(secret, known if known is not None else install_values(user_id))


def scrub(
    turns: list[TurnEntry],
    *,
    secret: str,
    user_id: int | str | None = None,
    known: list[tuple[str, str]] | None = None,
) -> tuple[list[TurnEntry], dict[str, int]]:
    """``(scrubbed turns, counts)`` — the whole of what a share is allowed to be."""
    one = scrubber(secret=secret, user_id=user_id, known=known)
    return [one.turn(turn) for turn in turns], one.counts
