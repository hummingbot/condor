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
userinfo, an IP, a seed-phrase-shaped run of words. Everything here is
best-effort by construction, which is why the feature ships an explicit button
whose dialog shows the user the exact bytes before anything is sent.

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
)
CATEGORIES = TIER1_CATEGORIES + TIER2_CATEGORIES

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

# Nesting past this is not walked, matching ``_redact``'s bound in
# ``condor.runtime.conversations``: the payload came from a tool we do not own.
_MAX_DEPTH = 6

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

_HEX64_RE = re.compile(r"0x[0-9a-fA-F]{64}" + _NOT_ID_AFTER)
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
# Twelve or more consecutive lowercase words of 3–8 letters, separated by any
# run of whitespace or commas and each optionally numbered — the shape of a
# BIP-39 recovery phrase, and the *candidate* for one. The separator is
# deliberately permissive because it is only a prefilter: a phrase pasted out
# of a wallet UI arrives one word per line, out of a backup sheet as "1. legal
# 2. winner", and out of a chat with whatever spacing the paste carried. Shape
# alone is not enough in either direction: an English sentence can reach twelve
# long words, and a real phrase can repeat one, so neither a prose test nor a
# distinctness test decides this. :func:`_seed_phrase` decides it against the
# vendored wordlist, where membership is exact — narrowing the shape here would
# only move the decision away from the wordlist.
_SEED_ORDINAL = r"(?:\d{1,2}[.)]\s*)?"
_WORD_RUN_RE = re.compile(
    _NOT_ID_BEFORE
    + _SEED_ORDINAL
    + r"[a-z]{3,8}(?:[\s,]+"
    + _SEED_ORDINAL
    + r"[a-z]{3,8}){11,}"
    + _NOT_ID_AFTER
)
# The separator is captured so a run that turns out not to be a phrase can be
# re-emitted exactly as it came in, line breaks and all.
_SEED_SPLIT_RE = re.compile(r"([\s,]+)")
_SEED_ORDINAL_RE = re.compile(r"^\d{1,2}[.)]\s*")

# The shortest run of wordlist entries treated as a phrase. Twelve is the
# smallest mnemonic BIP-39 defines, so a shorter run is a coincidence.
SEED_MIN_WORDS = 12

_BIP39_PATH = Path(__file__).resolve().parent / "bip39_english.txt"
_bip39: frozenset[str] | None = None


def bip39_words() -> frozenset[str]:
    """The vendored BIP-39 English wordlist, read once.

    Vendored rather than guessed at. The alternative was a structural heuristic
    — "twelve short lowercase words that never repeat" — and it failed in the
    direction that matters: BIP-39's own test vector (``legal winner thank year
    wave sausage worth useful legal winner thank yellow``) repeats three words,
    so a distinctness rule waves a real recovery phrase straight through. 14 kB
    of exact membership buys both directions at once.

    The file is the canonical list, sha256
    ``2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda``, 2048
    words, ``abandon`` to ``zoo``. A missing or unreadable file degrades to no
    phrase detection rather than failing the share — tier 2's other patterns and
    the user's own eyes are still in front of it.
    """
    global _bip39
    if _bip39 is None:
        try:
            _bip39 = frozenset(_BIP39_PATH.read_text(encoding="utf-8").split())
        except OSError:  # pragma: no cover - a mangled install
            log.warning("Sharing could not read the BIP-39 wordlist", exc_info=True)
            _bip39 = frozenset()
    return _bip39


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
    """Replace each maximal run of ``SEED_MIN_WORDS``+ wordlist entries.

    The regex only found a candidate of the right *shape*; membership is what
    decides. Scanning inside the candidate rather than judging it whole is what
    lets a phrase pasted mid-sentence be caught without taking the sentence
    around it: "my phrase is ``abandon … zoo`` please check" loses the phrase
    and keeps the question.
    """
    words = bip39_words()
    if not words:
        return match.group(0)

    out: list[str] = []
    run: list[str] = []  # the pieces of the current run, verbatim
    pending: list[str] = []  # separators and ordinals not yet claimed by a run
    ordinal_at: int | None = None  # where an ordinal first appears in pending
    counted = 0  # wordlist entries in the run, which ordinals are not

    def flush() -> None:
        nonlocal counted
        if counted >= SEED_MIN_WORDS:
            out.append(scrubber._hit(category, "".join(run)))
        else:
            out.extend(run)
        run.clear()
        counted = 0

    # The candidate always starts on a word or its ordinal, so even positions
    # are tokens and odd ones are the separators between them.
    for index, piece in enumerate(_SEED_SPLIT_RE.split(match.group(0))):
        if index % 2:
            pending.append(piece)
            continue
        token = _SEED_ORDINAL_RE.sub("", piece)
        if token in words:
            if run:
                run.extend(pending)  # inside the run: the phrase's own spacing
            else:
                # Opening a run takes the numbering with it and leaves the
                # prose in front of it alone.
                cut = len(pending) if ordinal_at is None else ordinal_at
                out.extend(pending[:cut])
                run.extend(pending[cut:])
            pending.clear()
            ordinal_at = None
            run.append(piece)
            counted += 1
        elif not token:  # a bare "1." — numbering, not a word that breaks a run
            if ordinal_at is None:
                ordinal_at = len(pending)
            pending.append(piece)
        else:
            flush()
            out.extend(pending)
            pending.clear()
            ordinal_at = None
            out.append(piece)
    flush()
    out.extend(pending)
    return "".join(out)


# Order matters — see the note above the patterns.
_PATTERNS: tuple[tuple[str, re.Pattern, object], ...] = (
    ("seed_phrase", _WORD_RUN_RE, _seed_phrase),
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


def scrub(
    turns: list[TurnEntry],
    *,
    secret: str,
    user_id: int | str | None = None,
    known: list[tuple[str, str]] | None = None,
) -> tuple[list[TurnEntry], dict[str, int]]:
    """``(scrubbed turns, counts)`` — the whole of what a share is allowed to be.

    ``known`` is injectable so a test can state the install's values instead of
    inheriting the developer's; production passes ``user_id`` and lets
    :func:`install_values` build it.
    """
    table = known if known is not None else install_values(user_id)
    scrubber = Scrubber(secret, table)
    return [scrubber.turn(turn) for turn in turns], scrubber.counts
