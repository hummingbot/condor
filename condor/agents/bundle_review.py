"""What a reader should look at before a bundle runs (FEAT-144).

:mod:`condor.agents.bundle`'s export scrubs three ``AGENT.md`` front-matter
fields and never looks at file *content*, so a bundle can carry an API key, a
private RPC URL with a token in its query string, a skill whose prose steers
the model at a tool that signs, a routine that calls ``exec``, or risk limits
far wider than this install would ever set. This module is the one review that
names those things, and it reports — it never rewrites and never refuses.

**Report, never scrub.** ``condor/sharing/scrub.py`` substitutes pseudonyms into
a *transcript*, where a mangled string costs nothing. A bundle's files are code
and configuration: replacing a string inside ``routines/r.py`` or a YAML config
produces an agent that silently does not work. ``conversations._redact``'s
governing principle — redact by key name, never by value — names value-guessing
as how a redactor "either misses one or mangles a trading pair". So the output
here is a list of places to look.

**Who decides what.** Three callers, one function:

- **Export** refuses on a ``certain`` ``secret`` finding. An export is the one
  moment the value is still fixable by the person who owns it, and a published
  bundle carrying a live key cannot be unpublished out of somebody's download.
  Everything else warns.
- **Import** refuses *nothing new*. :func:`condor.agents.bundle.plan`'s existing
  refusals (unknown schema, wrong kind, digest mismatch, path traversal) stand,
  routines and skills land muted, and these findings are what the operator reads
  before unmuting them item by item.
- **A hub** re-runs this same function on a submission rather than trusting
  whoever exported it. In the hub case the exporter *is* the adversary and runs
  their own copy of the exporter, so an export-side check is advice and the
  receiving side's check is the one that matters.

**Stdlib, plus two modules that are themselves stdlib-only.**
:mod:`condor.runtime.secrets` and :mod:`condor.runtime.danger` and nothing else.
That portability is the point rather than tidiness — a submission pipeline must
be able to run the receiver's own review, and a review it cannot run is a review
it will approximate differently.

The promise is about *this module's own import edges*, which is the same thing
the two modules above mean by it: both declare themselves stdlib-only and both
still pull in ``condor.acp`` through ``condor/runtime/__init__.py``, exactly as
anything under ``condor/agents/`` pulls in ``TickEngine`` through
``condor/agents/__init__.py``. ``tests/test_bundle_review.py`` asserts the edges
off this file's own source, so the guard holds wherever the file sits, and
asserts in a subprocess that reviewing needs neither the web app nor the bot.

**It takes files, not a Bundle.** :func:`review_files` is the primitive and
:func:`review` is the convenience over anything with ``.files`` carrying
``.path`` and ``.text``. Typed structurally on purpose: importing
:mod:`condor.agents.bundle` for the annotation would be an import edge back into
pydantic and a cycle besides, and the hub has a parsed submission rather than
this install's model.

**A Finding never carries the matched value**, following
:class:`condor.runtime.secrets.Finding`'s rule exactly: offsets, so a caller
that needs the bytes slices ``text[start:end]`` itself and owns what it does
with them. :attr:`Finding.context` is the one exception and only for the two
categories where the text *is* the finding — see its docstring.

**This is best-effort, and the copy a surface shows must say so.** Borrowing
``secrets.py``'s own sentence rather than softening it: the bot warns, it does
not prevent. The honest claim for an imported bundle is that every executable
file was named before anything was written and that a review found these
things — not that it is safe. There is no sandbox in this process: a routine is
imported by ``discover_routines_from_path`` and runs inside Condor, and
``run_code`` has no isolation either. Building one is a far larger piece of
work than this, and a review that lands is worth more than a sandbox that does
not.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple, Protocol

from condor.runtime.danger import (
    CREATE_EXECUTOR_TOOLS,
    DANGEROUS_TOOLS,
    LEVERAGE_TOOL,
)
from condor.runtime.secrets import redact, scan

# ── Categories ──

#: A key-shaped value, straight from :func:`condor.runtime.secrets.scan`. The
#: ``certain`` flag is passed through unchanged: this module does not re-draw
#: the line that module drew at ambiguity, where a bare 64-hex is also a tx
#: hash and an 87-char base58 is also a transaction signature.
SECRET = "secret"

#: A host this agent reaches. Loopback and private ranges are labelled apart
#: from a public host deliberately: an agent talking to the local Hummingbot API
#: is the normal case in this product, and reaching a stranger's host is the
#: finding. Conflating them would make the category unreadable.
ENDPOINT = "endpoint"

#: A tool that moves money, named in prose or in code. A *context* report and
#: never an accusation — see :data:`STEERED_TOOLS`.
TOOL_STEER = "tool_steer"

#: A door onto arbitrary strings as code, in a ``.py`` file that will be
#: imported into this process.
CODE_RUN = "code_run"

#: A guardrail the bundle brings with it. Values, never a verdict.
RISK_LIMITS = "risk_limits"

#: The tool names a bundle's prose or code can steer the model at. One list,
#: read out of :mod:`condor.runtime.danger` rather than restated here, because a
#: second copy would be a second answer to "which tools move money".
#:
#: Noisy in a trading library by construction — every strategy skill names its
#: own executor tool, and
#: ``agents/adaptive_grid_trader/skills/liquidation_guard/SKILL.md`` legitimately
#: says "before calling ``create_grid_executor()``". The only thing that
#: distinguishes that from an instruction to open a position for somebody else
#: is the surrounding sentence, which is why each finding carries its line and
#: why the category is ordered second-to-last and never refuses anything. If the
#: volume ever buries the rest, the fix is presentation (collapse by tool name),
#: not a cleverer matcher: a matcher that tries to tell "call this" from "make it
#: call this" is a model reading attacker-controlled instructions, which is
#: itself injectable by them.
STEERED_TOOLS: frozenset[str] = frozenset(
    set(DANGEROUS_TOOLS) | set(CREATE_EXECUTOR_TOOLS) | {LEVERAGE_TOOL}
)

#: ``exec``/``eval``/``__import__``/``subprocess`` by name, and ``run_code``
#: which is Condor's own snippet runner holding an unrestricted API client.
#: ``open(`` is handled apart, by :data:`_OPEN_WRITE_RE`, because only a write
#: mode is worth a line.
_CODE_RUN_RE = re.compile(
    r"\b(?:exec|eval|__import__)\s*\(|\b(?:subprocess|run_code)\b"
)

#: ``open(...)`` with a mode string that writes. Bounded lookahead rather than a
#: real parse: this is a line to look at, not a verdict, and a 200-character
#: window covers every call anybody writes on one line.
_OPEN_WRITE_RE = re.compile(r"\bopen\s*\([^)]{0,200}?['\"][rbt+]*[wax+][rwaxbt+]*['\"]")

#: Everything after the identifier in a :data:`_CODE_RUN_RE` match — the ``(``
#: and whatever whitespace preceded it — so ``label`` is the construct's name
#: and not the bytes that happened to match.
_IDENT_TAIL_RE = re.compile(r"\W+$")

#: Every scheme an agent's files plausibly dial. The host is group ``host``;
#: userinfo, path, query and fragment are deliberately outside it, since a
#: private RPC URL carries its token in the query string and the *host* is the
#: thing worth labelling.
_URL_RE = re.compile(
    r"\b(?:https?|wss?|mqtts?|grpc|redis|rediss|mongodb(?:\+srv)?|"
    r"postgres(?:ql)?|mysql|amqps?)://(?P<host>[^\s/?#\"'`<>)\]},\\]+)",
    re.IGNORECASE,
)

#: A bare ``host:port`` — ``localhost:8000``, ``10.0.0.4:1883`` — which is how
#: half the endpoints in a config file are written. The leading guard keeps it
#: off the second half of a URL the pattern above already matched, off a
#: ``module.attr:12`` traceback line and off a bare ``:8000``.
_HOSTPORT_RE = re.compile(
    r"(?<![\w.:/@-])"
    r"(?P<host>localhost|(?:\d{1,3}\.){3}\d{1,3}"
    r"|[a-zA-Z0-9][a-zA-Z0-9-]*(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,})"
    r":(?P<port>\d{2,5})(?![\w.])"
)

#: Hosts that are this box. Reported, because an operator still wants to know an
#: imported agent talks to a local service, but labelled apart from a stranger.
LOOPBACK = "loopback"
#: Hosts on a network this install is on but nobody else is: RFC1918, link-local,
#: ``*.local``, ``*.internal``, Docker's gateway name. Not this box, not the
#: internet.
PRIVATE = "private"
#: Anything else — a host on the internet. The one endpoint label that is a
#: finding rather than a note.
PUBLIC = "public"

#: The extensions scanned for each category. ``secret`` and ``endpoint`` read
#: every file, because a key is a key wherever it sits; ``tool_steer`` reads the
#: prose and the code that could carry an instruction; ``code_run`` and
#: ``risk_limits`` read only the file kind that can mean anything.
_PROSE_SUFFIXES = (".md",)
_CODE_SUFFIXES = (".py",)


class Finding(NamedTuple):
    """One thing to look at, located but not quoted.

    ``start`` and ``end`` are offsets into that file's ``text``, so
    ``text[start:end]`` is the exact run — the contract
    :class:`condor.runtime.secrets.Finding` sets. ``line`` is 1-based and is
    what a surface renders.
    """

    #: One of :data:`SECRET`, :data:`ENDPOINT`, :data:`TOOL_STEER`,
    #: :data:`CODE_RUN`, :data:`RISK_LIMITS`.
    category: str
    #: The kind within the category, always from a closed set this module or
    #: :mod:`condor.runtime.secrets` owns — a ``secrets`` kind, an endpoint
    #: class, a tool name, a construct name, or a limit's field name. Never a
    #: substring of the file.
    label: str
    path: str
    line: int
    start: int
    end: int
    #: Whether the shape has no plausible alternative. Passed through from
    #: ``secrets.scan`` for :data:`SECRET`; ``False`` everywhere else, because
    #: every other category reports a thing that is routine as often as not.
    certain: bool = False
    #: The text a surface shows beside ``label``, and empty for every category
    #: but two. :data:`TOOL_STEER` carries the matched *line*, because the
    #: surrounding sentence is the entire finding — run through
    #: :func:`condor.runtime.secrets.redact` first, so a certain secret sitting
    #: on a steering line does not travel in a finding. :data:`RISK_LIMITS`
    #: carries the limit's figure, because a limit reported without its value
    #: says nothing. Nothing else ever populates it.
    context: str = ""


class ReviewableFile(Protocol):
    """One inlined file: a path and its text. Satisfied by ``BundleFile``."""

    path: str
    text: str


class ReviewableBundle(Protocol):
    """Anything carrying inlined files. Satisfied by ``Bundle`` and ``Submission``."""

    files: Sequence[ReviewableFile]


def review(bundle: ReviewableBundle) -> list[Finding]:
    """Every finding in this bundle, most-decisive first.

    The whole review, and the only one: export, the import preview and a hub's
    submission check all land here, so a bundle cannot be described one way by
    the install that sent it and another by the install that receives it.
    """
    return review_files((entry.path, entry.text) for entry in bundle.files)


def review_files(files: Iterable[tuple[str, str]]) -> list[Finding]:
    """:func:`review` over ``(path, text)`` pairs — the primitive.

    Separate from :func:`review` for the two callers that have no bundle object:
    a hub holding a submission in whatever shape it parsed, and the test that
    reviews the shipped ``agents/`` tree straight off disk.
    """
    findings: list[Finding] = []
    for path, text in files:
        findings += _secrets(path, text)
        findings += _endpoints(path, text)
        if path.endswith(_PROSE_SUFFIXES) or path.endswith(_CODE_SUFFIXES):
            findings += _tool_steer(path, text)
        if path.endswith(_CODE_SUFFIXES):
            findings += _code_run(path, text)
        if path.endswith(_PROSE_SUFFIXES):
            findings += _risk_limits(path, text)
    findings.sort(key=_rank)
    return findings


def certain_secrets(findings: Sequence[Finding]) -> list[Finding]:
    """The findings an *export* refuses on. Named here so one list decides it."""
    return [f for f in findings if f.category == SECRET and f.certain]


def refusal(findings: Sequence[Finding]) -> str:
    """The sentence an export refuses with, or ``""`` when it may proceed.

    Names every certain secret with its file and span, because the operator's
    next move is to open that file at that offset — and the value itself is
    deliberately not in the message, which is the only reason the message is
    safe to log.
    """
    certain = certain_secrets(findings)
    if not certain:
        return ""
    where = ", ".join(
        f"{f.path}:{f.line} (bytes {f.start}–{f.end}, {f.label})" for f in certain
    )
    return (
        f"This agent's library carries {len(certain)} value that can only be a "
        f"secret, so it will not be exported: {where}. A published bundle "
        "cannot be unpublished out of somebody's download — remove the value "
        "and export again."
    )


def line_of(text: str, offset: int) -> int:
    """The 1-based line ``offset`` falls on."""
    return text.count("\n", 0, offset) + 1


def line_text(text: str, offset: int, limit: int = 200) -> str:
    """The line ``offset`` falls on, redacted and bounded.

    Redacted because the line is attacker-controlled text that a finding is
    about to carry out of the file; bounded because a minified or generated line
    is not context, it is the whole file on one row.
    """
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    raw = text[start : end if end != -1 else len(text)].strip()
    safe, _ = redact(raw)
    return safe if len(safe) <= limit else safe[: limit - 1] + "…"


# ── The scanners ──


def _secrets(path: str, text: str) -> list[Finding]:
    return [
        Finding(
            category=SECRET,
            label=found.kind,
            path=path,
            line=line_of(text, found.start),
            start=found.start,
            end=found.end,
            certain=found.certain,
        )
        for found in scan(text)
    ]


def _endpoints(path: str, text: str) -> list[Finding]:
    """Every host these bytes dial, labelled by how far away it is.

    A ``host:port`` inside a URL the first pattern already matched is dropped by
    span overlap rather than by a cleverer regex: ``https://x.com:8443/y`` is one
    endpoint, and two findings on the same bytes would read as two hosts.
    """
    found: list[Finding] = []
    spans: list[tuple[int, int]] = []
    for pattern in (_URL_RE, _HOSTPORT_RE):
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(
                start < prior_end and prior_start < end
                for prior_start, prior_end in spans
            ):
                continue
            spans.append((start, end))
            found.append(
                Finding(
                    category=ENDPOINT,
                    label=_host_class(_host_of(match.group("host"))),
                    path=path,
                    line=line_of(text, start),
                    start=start,
                    end=end,
                )
            )
    return found


def _host_of(raw: str) -> str:
    """The host alone: userinfo dropped, brackets unwrapped, port removed.

    Lowercased and with a trailing dot removed so ``EVIL.example.`` and
    ``evil.example`` classify alike.
    """
    host = raw.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    else:
        host = host.split(":", 1)[0]
    return host.lower().rstrip(".")


def _host_class(host: str) -> str:
    """:data:`LOOPBACK`, :data:`PRIVATE` or :data:`PUBLIC` for one host."""
    if host in ("localhost", "::1", "0.0.0.0") or host.startswith("127."):
        return LOOPBACK
    if host.endswith(".localhost"):
        return LOOPBACK
    octets = host.split(".")
    if len(octets) == 4 and all(part.isdigit() for part in octets):
        first, second = int(octets[0]), int(octets[1])
        if first == 10:
            return PRIVATE
        if first == 172 and 16 <= second <= 31:
            return PRIVATE
        if first == 192 and second == 168:
            return PRIVATE
        if first == 169 and second == 254:
            return PRIVATE
        return PUBLIC
    if host.endswith((".local", ".internal")) or host == "host.docker.internal":
        return PRIVATE
    return PUBLIC


def _tool_steer(path: str, text: str) -> list[Finding]:
    """Every money-moving tool name in this file, with the line that names it."""
    found: list[Finding] = []
    for match in _TOOL_NAME_RE.finditer(text):
        found.append(
            Finding(
                category=TOOL_STEER,
                label=match.group(0),
                path=path,
                line=line_of(text, match.start()),
                start=match.start(),
                end=match.end(),
                context=line_text(text, match.start()),
            )
        )
    return found


def _code_run(path: str, text: str) -> list[Finding]:
    """Every door onto arbitrary code in a ``.py`` file the import will write.

    ``label`` is the construct, not the matched bytes: ``exec(`` is reported as
    ``exec`` and an ``open(..., "w")`` as ``open-write``, so a surface can group
    by what it means.
    """
    found: list[Finding] = []
    for match in _CODE_RUN_RE.finditer(text):
        found.append(
            Finding(
                category=CODE_RUN,
                label=_IDENT_TAIL_RE.sub("", match.group(0)),
                path=path,
                line=line_of(text, match.start()),
                start=match.start(),
                end=match.end(),
            )
        )
    for match in _OPEN_WRITE_RE.finditer(text):
        found.append(
            Finding(
                category=CODE_RUN,
                label="open-write",
                path=path,
                line=line_of(text, match.start()),
                start=match.start(),
                end=match.end(),
            )
        )
    return found


def _risk_limits(path: str, text: str) -> list[Finding]:
    """Every ``default_config.risk_limits`` figure this file brings with it.

    One finding per limit, so each carries the offsets of its own key and a
    surface can put one figure beside the one this install would otherwise run.

    Read by indentation rather than by a YAML parse, for two reasons that point
    the same way. A parse returns values and no offsets, and this module's
    ``Finding`` is built on offsets. And PyYAML is not the standard library,
    while the portability this module promises is the reason a hub can run it.

    The key *path* is tracked rather than just the depth, so only
    ``default_config`` → ``risk_limits`` → a limit counts. That is the one block
    ``load_full_config`` turns into the agent's brakes; a ``risk_limits:`` nested
    under anything else, or at the top of the front matter, is prose.
    """
    front = _front_matter(text)
    if front is None:
        return []
    body, base = front

    found: list[Finding] = []
    offset = base
    stack: list[tuple[int, str]] = []
    for raw in body.splitlines(keepends=True):
        match = _KEY_RE.match(raw)
        if match:
            indent = len(match.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path_keys = [key for _, key in stack] + [match.group("key")]
            value = (match.group("value") or "").strip()
            if not value:
                stack.append((indent, match.group("key")))
            elif path_keys[:-1] == _LIMITS_PATH:
                key_start = offset + match.start("key")
                found.append(
                    Finding(
                        category=RISK_LIMITS,
                        label=match.group("key"),
                        path=path,
                        line=line_of(text, key_start),
                        start=key_start,
                        end=offset + match.end("value"),
                        context=value,
                    )
                )
        offset += len(raw)
    return found


#: One ``key:`` or ``key: value`` line of a front-matter mapping. A list item,
#: a blank line and a comment all fail to match, which is what keeps them out of
#: the key path :func:`_risk_limits` walks.
_KEY_RE = re.compile(
    r"^(?P<indent> *)(?P<key>[A-Za-z_][\w-]*)\s*:(?:[ \t]*(?P<value>\S.*?))?[ \t]*\n?$"
)

#: The only parent chain whose keys become an agent's guardrails.
_LIMITS_PATH = ["default_config", "risk_limits"]

#: The steered tool names as one alternation, longest first so
#: ``create_grid_executor`` is not reported as a prefix of something longer.
_TOOL_NAME_RE = re.compile(
    r"\b(?:" + "|".join(sorted(STEERED_TOOLS, key=len, reverse=True)) + r")\b"
)


def _front_matter(text: str) -> tuple[str, int] | None:
    """``(the front-matter body, its offset in ``text``)``, or ``None``.

    Hand-rolled for the same reason :func:`_risk_limits` is: the caller needs
    offsets into the original text, which no parser hands back.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[4 : end + 1], 4


# ── Ordering ──

#: Most-decisive first, so a long ``tool_steer`` list in a legitimate trading
#: agent never buries a key. A loopback or private endpoint sits below
#: ``tool_steer`` and above ``risk_limits``: it is a note, not a finding, and the
#: brakes stay last because they are the one category that is always present.
_ORDER = (
    (SECRET, True),
    (SECRET, False),
    (ENDPOINT, PUBLIC),
    (CODE_RUN, None),
    (TOOL_STEER, None),
    (ENDPOINT, PRIVATE),
    (ENDPOINT, LOOPBACK),
    (RISK_LIMITS, None),
)


def _rank(finding: Finding) -> tuple[int, str, int]:
    for index, (category, qualifier) in enumerate(_ORDER):
        if finding.category != category:
            continue
        if qualifier is None:
            return index, finding.path, finding.start
        if category == SECRET and finding.certain is qualifier:
            return index, finding.path, finding.start
        if category == ENDPOINT and finding.label == qualifier:
            return index, finding.path, finding.start
    return len(_ORDER), finding.path, finding.start
