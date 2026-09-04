"""Key-shaped values in free text — found at ingress, before anything is written.

``condor.runtime.conversations._redact`` states the governing principle for tool
arguments: *redact by key name, never by value*, because guessing which string
is a secret is how a redactor either misses one or mangles a trading pair. Free
text has no key names, so this module has to guess by value — and it therefore
guesses only where the shape has no plausible alternative, and defers to the
user everywhere else.

That line is drawn once, here, and it is drawn at ambiguity:

===============================  ==================================  ==========
Shape                            Also is                             Verdict
===============================  ==================================  ==========
BIP-39 phrase, 12–24 words       nothing                             redact
``[174,47,…]``, 64 ints 0–255    nothing                             redact
64 hex, ``0x`` optional          a tx hash, a sha256 digest          warn only
base58, 87–88 chars              a Solana **transaction signature**  warn only
===============================  ==================================  ==========

A 64-byte value is not distinguishable from key material by any test that does
not already know the answer. Redacting the bottom two by default would break
"check this tx", which is routine here, on every use — so they are reported as
``certain=False`` and the surfaces warn about them once instead of eating them.

The ``0x`` on the hex row is optional, and that is the deliberate half of the
trade-off rather than a slip. MetaMask's "Show private key" — and every EVM
wallet export that follows it — hands the user 64 bare hex characters, so
requiring the prefix meant the single most common way a private key gets pasted
was the one shape this module never saw. Dropping the prefix does widen the net:
a Bitcoin txid, a block hash and any sha256 digest are bare 64-hex too, and
those are ordinary things to paste here. It is affordable because of where this
row already sits — ``certain=False`` means nothing is ever eaten, a surface
warns at most once per conversation per kind and only if the user left the
notice on, and at egress the value was already being replaced by the scrubber's
generic last-net pattern, so the only thing that changes there is that it is
counted as what it is. The alternative — asking for a nearby "private key"
keyword — was rejected: it is context the scrubber cannot see when it imports
this shape, and it fails on exactly the paste that is only the key.
Stated plainly, and deliberately: **an EVM private key pasted into free text
reaches the model and the transcript.** The bot warns; it does not prevent.

**This module only ever sees text.** A key that arrives as a *picture* — the
screenshot of a wallet export, of a terminal, of a dashboard panel — reaches the
model and the transcript with nothing here looking at it, because nothing here
can: an attached image travels beside the text and never through
:func:`redact` (FEAT-098). Stated here rather than left for a user to discover,
since this is where the guarantee is written down. It is accepted, not solved —
OCR-ing every upload to feed a regex is disproportionate to the risk and would
put a second, worse guesser in the ingress path — and the boundary is worth
knowing when reading the promise above: **the bot warns about what you type; it
sees nothing of what you paste as an image.**

**A Finding never carries the value.** It carries offsets, so a finding can be
logged, counted and put in telemetry without becoming the leak it exists to
prevent. A caller that needs the bytes slices ``text[start:end]`` itself, and
owns what it does with them from there.

**Two callers, one set of shapes.** This module runs at *ingress*
(:func:`condor.runtime.client.prompt`, before the model call and before the
first disk write) and :mod:`condor.sharing.scrub` runs at *egress*, on data
already written. The scrubber substitutes stable pseudonyms and counts
categories, which is its own business; what the two share is the question "is
this run of words a recovery phrase", and that question is answered exactly
once — here, against the vendored wordlist. The scrubber imports
:func:`phrase_spans` and the shapes rather than restating them, because a
second copy of a detector is a copy that drifts, and the half that drifts is
never the half anyone is looking at.

Pure: no I/O beyond reading the vendored wordlist once, no logging of anything
derived from the text, no state.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

# ── The shapes ───────────────────────────────────────────────────────────
#
# The delimiter guard is a lookaround on ``[A-Za-z0-9_-]`` rather than ``\b``,
# for the reason ``condor.sharing.scrub`` gives at length: ``\b`` fires between
# a hyphen and a letter, so a hyphenated order id would present its tail as a
# standalone token. The lookaround refuses to start or end a match inside an
# identifier, which puts bot names and trading pairs structurally out of reach.

_NOT_ID_BEFORE = r"(?<![A-Za-z0-9_\-])"
_NOT_ID_AFTER = r"(?![A-Za-z0-9_\-])"

#: 64 hex, ``0x`` optional. An EVM private key — and equally a transaction
#: hash, a Bitcoin txid or a sha256 digest. The prefix is optional because a
#: wallet export writes the bare form; see the table above for why that widening
#: is affordable on a warn-only row. The prefix is consumed greedily, so the
#: ``0x`` form is one finding of 66 characters and never two.
HEX64_RE = re.compile(_NOT_ID_BEFORE + r"(?:0x)?[0-9a-fA-F]{64}" + _NOT_ID_AFTER)

# Base58 excludes 0, O, I and l on purpose — that is what makes a run of this
# alphabet an encoded key rather than a word. The 87–88 bound is exact: a
# 64-byte Solana secret key encodes to 87 or 88 characters, and a 32-byte
# *address* to 32–44, so an address can never reach this pattern.
B58_KEY_RE = re.compile(_NOT_ID_BEFORE + r"[1-9A-HJ-NP-Za-km-z]{87,88}" + _NOT_ID_AFTER)

# What ``solana-keygen`` writes and every wallet export offers: 64 bytes as a
# JSON array of ints. The 0–255 bound is checked after the match rather than
# spelled in the regex, which keeps the pattern readable and the check exact.
KEYPAIR_ARRAY_RE = re.compile(r"\[\s*\d{1,3}(?:\s*,\s*\d{1,3}){63}\s*,?\s*\]")

# ── BIP-39 recovery phrases ──────────────────────────────────────────────
#
# Twelve or more consecutive words of 3–8 letters, in any case, separated by
# any run of whitespace or commas and each optionally numbered — the shape of a
# recovery phrase, and only the *candidate* for one. The separator is
# deliberately permissive because this is a prefilter: a phrase pasted out of a
# wallet UI arrives one word per line, out of a backup sheet as "1. legal
# 2. winner", and out of a chat with whatever spacing the paste carried (this
# is SEC-230's finding, and the shapes above are the ones that were reaching a
# collector verbatim). Case is permissive for the same reason and not for a
# hypothetical one: BIP-39 writes its wordlist in lowercase, but a phrase
# arrives however the thing that produced it wrote it, and a phone keyboard
# capitalises the first word of a message on its own. Matching only [a-z] let
# "Legal winner thank…" past both this filter and the sharing scrubber that
# imports it. Shape alone decides nothing in either direction — an English
# sentence can reach twelve long words, and a real phrase can repeat one — so
# :func:`phrase_spans` decides it against the vendored wordlist, case-folded
# to it, where membership is otherwise exact.

_SEED_ORDINAL = r"(?:\d{1,2}[.)]\s*)?"
SEED_CANDIDATE_RE = re.compile(
    _NOT_ID_BEFORE
    + _SEED_ORDINAL
    + r"[A-Za-z]{3,8}(?:[\s,]+"
    + _SEED_ORDINAL
    + r"[A-Za-z]{3,8}){11,}"
    + _NOT_ID_AFTER
)
# The separator is captured so a run that turns out not to be a phrase can be
# re-emitted exactly as it came in, line breaks and all.
SEED_SPLIT_RE = re.compile(r"([\s,]+)")
SEED_ORDINAL_RE = re.compile(r"^\d{1,2}[.)]\s*")

#: The shortest run of wordlist entries treated as a phrase. Twelve is the
#: smallest mnemonic BIP-39 defines, so a shorter run is a coincidence.
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
    of exact membership buys both directions at once, and costs no dependency:
    ``bip_utils`` and ``mnemonic`` are not worth pulling in for one static list.

    The file is the canonical list, sha256
    ``2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda``, 2048
    words, ``abandon`` to ``zoo``. A missing or unreadable file degrades to no
    phrase detection rather than raising: this runs on the one funnel every
    turn crosses, and a mangled install must not take the agent down with it.
    """
    global _bip39
    if _bip39 is None:
        try:
            _bip39 = frozenset(_BIP39_PATH.read_text(encoding="utf-8").split())
        except OSError:  # pragma: no cover - a mangled install
            log.warning("Could not read the BIP-39 wordlist", exc_info=True)
            _bip39 = frozenset()
    return _bip39


def phrase_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of every maximal run of :data:`SEED_MIN_WORDS`+ words.

    Scanning *inside* a candidate rather than judging it whole is what lets a
    phrase pasted mid-sentence be caught without taking the sentence around it:
    "my phrase is ``abandon … zoo`` please check" yields the phrase and leaves
    the question. Numbering opens the run with it — a span that started at
    ``legal`` and left ``1.`` behind would redact the phrase and leave a
    numbered list of nothing.

    Spans are in ``text``'s own coordinates, non-overlapping, in order.
    """
    words = bip39_words()
    if not words:
        return []

    spans: list[tuple[int, int]] = []
    for candidate in SEED_CANDIDATE_RE.finditer(text):
        base = candidate.start()
        for start, end in _spans_within(candidate.group(0), words):
            spans.append((base + start, base + end))
    return spans


def _spans_within(candidate: str, words: frozenset[str]) -> list[tuple[int, int]]:
    """The runs inside one shape-matched candidate, in its own coordinates."""
    spans: list[tuple[int, int]] = []
    run_start: int | None = None
    run_end = 0
    counted = 0
    # Separators and bare ordinals not yet claimed by a run, as offset pairs.
    pending: list[tuple[int, int]] = []
    ordinal_at: int | None = None  # where an ordinal first appears in pending

    def flush() -> None:
        nonlocal run_start, counted
        if run_start is not None and counted >= SEED_MIN_WORDS:
            spans.append((run_start, run_end))
        run_start = None
        counted = 0

    offset = 0
    # The candidate always starts on a word or its ordinal, so even positions
    # are tokens and odd ones are the separators between them.
    for index, piece in enumerate(SEED_SPLIT_RE.split(candidate)):
        start, offset = offset, offset + len(piece)
        if index % 2:
            pending.append((start, offset))
            continue
        token = SEED_ORDINAL_RE.sub("", piece)
        # The wordlist is lowercase; the paste need not be.
        if token.lower() in words:
            if run_start is None:
                # Opening a run takes the numbering with it and leaves the
                # prose in front of it alone.
                if ordinal_at is not None:
                    run_start = pending[ordinal_at][0]
                else:
                    run_start = start
            run_end = offset
            counted += 1
            pending.clear()
            ordinal_at = None
        elif not token:  # a bare "1." — numbering, not a word that breaks a run
            if ordinal_at is None:
                ordinal_at = len(pending)
            pending.append((start, offset))
        else:
            flush()
            pending.clear()
            ordinal_at = None
    flush()
    return spans


# ── Findings ─────────────────────────────────────────────────────────────

MNEMONIC = "mnemonic"
SOLANA_KEYPAIR = "solana-keypair"
EVM_HEX64 = "evm-hex64"
SOLANA_B58 = "solana-b58-64"

#: Every kind :func:`scan` can report, and whether it is certain enough to eat.
KINDS: dict[str, bool] = {
    MNEMONIC: True,
    SOLANA_KEYPAIR: True,
    EVM_HEX64: False,
    SOLANA_B58: False,
}

#: What a certain finding is replaced with. Readable to the model on purpose:
#: it can say something useful about a hole it can name, and is only confused
#: by one it cannot.
_MARKERS = {
    MNEMONIC: "[redacted: mnemonic]",
    SOLANA_KEYPAIR: "[redacted: solana-keypair]",
}


class Finding(NamedTuple):
    """One key-shaped run. Offsets into the scanned text, never the value."""

    kind: str
    certain: bool
    start: int
    end: int


def _bytes_ok(match: str) -> bool:
    return all(
        0 <= int(part.strip()) <= 255 for part in match[1:-1].split(",") if part.strip()
    )


def keypair_array_spans(text: str) -> list[tuple[int, int]]:
    """Offsets of every real keypair array in ``text`` — the shape *and* the bound.

    :data:`KEYPAIR_ARRAY_RE` only finds a bracketed run of exactly 64 ints of
    one to three digits; the 0–255 check is what decides that run is a key
    rather than a list of numbers that happens to be 64 long. Neither half
    means anything alone, so they are one function — and one function is what
    :mod:`condor.sharing.scrub` imports to ask the same question at *egress*
    that this module asks at *ingress*. A second copy there would be a second
    calibration of "what is a keypair", which is exactly what :func:`phrase_spans`
    already exists to prevent for the recovery-phrase shape.
    """
    return [
        match.span()
        for match in KEYPAIR_ARRAY_RE.finditer(text)
        if _bytes_ok(match.group(0))
    ]


def scan(text: str) -> list[Finding]:
    """Every key-shaped run in ``text``, in order. Never returns the value."""
    if not text:
        return []

    findings: list[Finding] = [
        Finding(MNEMONIC, True, start, end) for start, end in phrase_spans(text)
    ]
    findings += [
        Finding(SOLANA_KEYPAIR, True, start, end)
        for start, end in keypair_array_spans(text)
    ]
    for match in HEX64_RE.finditer(text):
        findings.append(Finding(EVM_HEX64, False, *match.span()))
    for match in B58_KEY_RE.finditer(text):
        findings.append(Finding(SOLANA_B58, False, *match.span()))

    findings.sort(key=lambda finding: (finding.start, finding.end))
    return findings


def redact(text: str) -> tuple[str, list[Finding]]:
    """``text`` with every *certain* finding replaced, and what was found.

    Ambiguous findings survive untouched — see the table in the module
    docstring for why, before assuming that is an oversight. The returned
    findings are all of them, ambiguous ones included, with offsets into the
    **original** ``text``: they are what a surface warns on and what telemetry
    counts, and neither wants to be told only about what was eaten.
    """
    findings = scan(text)
    certain = [finding for finding in findings if finding.certain]
    if not certain:
        return text, findings

    out: list[str] = []
    cursor = 0
    for finding in certain:
        if finding.start < cursor:  # pragma: no cover - shapes cannot overlap
            continue
        out.append(text[cursor : finding.start])
        out.append(_MARKERS[finding.kind])
        cursor = finding.end
    out.append(text[cursor:])
    return "".join(out), findings


def counts(findings: list[Finding]) -> dict[str, int]:
    """``{kind: n}`` for the kinds that occurred — the telemetry shape.

    Counts and categories only, which is the contract every tap in
    :mod:`condor.telemetry.taps` already holds itself to.
    """
    tally: dict[str, int] = {}
    for finding in findings:
        tally[finding.kind] = tally.get(finding.kind, 0) + 1
    return tally
