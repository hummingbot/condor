"""An agent as one file: export it, read it, import it (FEAT-133).

Moving an agent used to mean copying ``agents/<slug>/`` by hand and then
repairing whatever was true only of the install it came from. This module is
the artifact that replaces that: one JSON document, ``<slug>.condor-agent.json``,
carrying the agent's *library* content and nothing this install accumulated by
running it.

**Why JSON and not an archive.** The central safety requirement here is
review-before-run — an imported routine executes inside the Condor process,
unsandboxed, and this module builds no sandbox. A zip is opaque to the person
deciding whether to trust it, and it would import a parser surface the repo has
never had to defend (zip-slip, symlink members, compression bombs). A JSON
document with every file inlined is readable by eye, pasteable, sendable as a
Telegram document, and needs no delimiter rules — string encoding is the
framing.

**One builder behind the preview and the write.** :func:`plan` computes
everything a human needs in order to decide and writes nothing; :func:`apply`
writes exactly what that plan described. The rule is
``condor/sharing/share.py``'s: the only way to promise that pressing Import
does what the dialog showed is for both to call one function.

**What does not travel.** Three fields are facts about the exporting install
and are rewritten in the bundle's copy (the files on disk are untouched):
``server_name`` names a server in *this* ``config.yml`` and three separate pin
guards would refuse it on the receiving box; ``created_by`` is a real Telegram
id that points to nobody elsewhere ([[CORR-705]]); ``forked_from``/``forked_at``
describe a divergence from *this* install's stock tree. The selection of *which*
files travel is :func:`condor.layering.publishable_files`, unchanged and
un-second-guessed — it is the one answer to "what is this agent, as opposed to
what this install accumulated", and a second filter here would leave two
selectors disagreeing.

**What arrives switched off.** Every imported routine and skill is muted
(:mod:`condor.memory.mutes`), and every imported routine *file* arrives
without execution consent (:mod:`condor.agents.code_trust`, FEAT-147). The two
are separate on purpose and both are needed: a mute says what the agent is
told about, and is subtracted *after* discovery has already imported the file,
so on its own it left the review surfaces — the ``/routines`` list and the
source viewer — running the very code they were opened to review. Consent says
what may execute at all, per file and by content digest. Import creates; it
never overwrites, never starts anything, and never writes into the shipped
tree.

**Who this agent is, and what it needs** (FEAT-143). A bundle that travels
between strangers carries four more manifest fields, all in schema 1 because
no schema-1 bundle without them ever reached ``main``:

* ``agent.agent_uid`` — a stable 32-hex identity that survives the rename
  ``as_slug`` performs, so two homes that came from one bundle can still be
  recognised as the same agent. It is *not* spelled ``agent_id``: that name
  already means the slug everywhere in this codebase.
* ``version`` and ``notes`` — an integer that orders two bundles of one
  ``agent_uid``, bumped only when the content actually changed, and the free
  text the exporter wrote about the change. A version is meaningful only
  *within one lineage*: two installs that import one bundle, edit and
  re-export both produce version 2 with different content. The authoritative
  answer for an upgrade is the file diff, never this number.
* ``author`` — a **claim**. Nothing in Condor verifies it, Condor has no key
  infrastructure, and every surface that renders it must say so.
* ``requires`` — **derived from the files, never authored**, so it cannot
  drift from what the agent actually names. :func:`requires_of` is the one
  derivation and both the export and the preview call it, which is why a
  hand-edited ``requires`` block in a bundle changes nothing.

An unmet requirement never refuses an import. "I will add that exchange's API
key once I have read the agent" is the honest case, and everything that lands
is muted and unstarted anyway: the preview says what will not work here, and
the operator decides.

**What the content itself carries** (FEAT-144). The scrub above rewrites three
front-matter fields and looks at no file *content*, so a bundle could carry an
API key, a stranger's endpoint, a skill steering the model at a tool that signs,
or risk limits far wider than this install would set, and nothing said so.
:mod:`condor.agents.bundle_review` is the one review that names those; it is
pure and stdlib-only so a hub can re-run exactly this install's review on a
submission. :func:`build` refuses on a value that can only be a secret and
warns on everything else; :func:`plan` attaches the findings and refuses
nothing new.

**And a second verb beside the import** (FEAT-145). A newer bundle of an agent
this install already has is an *update*, not a name collision: without one the
new version lands beside the old under ``<slug>_2``, the memory forks, and the
dashboard lists two agents one of which is stale. :func:`plan_update` shows
what would change file by file and writes nothing; :func:`apply_update` writes
exactly that. Library files are replaced, runtime output is untouched, a file
the operator edited keeps its bytes, whatever arrives new is muted, and nothing
is started. Its bookkeeping is :data:`STAMP_FILENAME` — a dotfile in the home,
so the export cannot see it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field

from condor.agents import bundle_review, code_trust
from condor.frontmatter import parse_frontmatter, render_frontmatter
from condor.fsutil import atomic_write_text
from condor.layering import (
    CREATED_BY_KEY,
    FORKED_AT_KEY,
    FORKED_FROM_KEY,
    fork_if_stock,
    is_library_path,
    publishable_files,
)
from condor.memory.paths import CHAT_SLUG, agent_home, agent_home_layers, safe_slug
from condor.paths import UnsafeIdError

log = logging.getLogger(__name__)

#: Bumped whenever the document's shape changes in a way an older reader would
#: misread. An unknown value is refused **whole**, the stance
#: ``condor/sharing/wire.py`` sets: a bundle from a newer Condor fails loudly
#: rather than being half-understood.
#:
#: Still 1 after FEAT-143 added four manifest fields, and that is the
#: deliberate answer to the question that item's own risk note poses: a schema
#: bump exists to protect readers of bundles already in the wild, and
#: ``condor/agents/bundle.py`` had not reached ``main`` when the fields landed,
#: so no bundle without them was ever emitted by a released Condor. Shipping
#: schema 2 would have been a version number nothing had ever written. The
#: *reader* is tolerant regardless — every new field has a default, so a
#: manifest that omits them all parses, and the missing values are minted or
#: derived on the way in.
SCHEMA = 1

#: Stamped on every bundle and checked on the way in, so an unrelated JSON file
#: is answered with a sentence instead of a ``KeyError``.
KIND = "condor-agent-bundle"

#: The cap on one bundle, in bytes of rendered JSON. Enforced on export *and*
#: again on import, so the HTTP boundary is not the only limit — the MCP and
#: chat doors go through the same check. Same order as
#: ``wire.MAX_SHARE_BYTES``; an agent's library is a few dozen text files.
MAX_BUNDLE_BYTES = 2_000_000

#: The file name an exported bundle carries.
FILENAME_SUFFIX = ".condor-agent.json"

AGENT_MD = "AGENT.md"
SERVER_NAME_KEY = "server_name"
AGENT_UID_KEY = "agent_uid"
BUNDLE_VERSION_KEY = "bundle_version"
BUNDLE_DIGEST_KEY = "bundle_digest"

#: The frontmatter keys rewritten on the way out. Named here so the export
#: scrub, the import stamp and the preview all read one list.
SCRUBBED_KEYS = (SERVER_NAME_KEY, CREATED_BY_KEY, FORKED_FROM_KEY, FORKED_AT_KEY)

#: The ``AGENT.md`` keys that are *this install's* bundle bookkeeping rather
#: than part of the agent, dropped from the carried copy. They have to be
#: dropped rather than merely ignored: the export stamps them back onto disk
#: after computing the digest, so carrying them would make every export see
#: different bytes than the last one measured and bump the version forever.
#: ``agent_uid`` is deliberately **not** here — it is the one thing that must
#: travel.
BOOKKEEPING_KEYS = (BUNDLE_VERSION_KEY, BUNDLE_DIGEST_KEY)


class BundleError(ValueError):
    """A bundle this install refuses, with the sentence explaining why."""


class NotJsonError(BundleError):
    """The bytes are not a JSON document at all — a wrong file, not a bad one."""


# ── The document ──


class BundleFile(BaseModel):
    """One library file, inlined.

    ``sha256`` is the **full** hex digest of ``text`` encoded as UTF-8 — not
    :func:`condor.layering.content_digest`'s ``sha256:<12>``, which hashes a
    file on disk and truncates for a log line. The importer recomputes it from
    the JSON alone, so a hand-edited bundle is caught; truncating would be
    choosing brevity in the one place the digest is actually load-bearing.
    """

    path: str
    sha256: str
    text: str


class BundleAgent(BaseModel):
    """The identity a reviewer sees before opening a single file.

    ``agent_uid`` is the identity; ``slug`` is only the name, and the importer
    is allowed to change it. Empty on a bundle written before FEAT-143, and
    minted on the way in — see :func:`_minted_uid`.
    """

    slug: str
    name: str = ""
    description: str = ""
    when_to_consult: str = ""
    agent_uid: str = ""


class BundleAuthor(BaseModel):
    """Who the exporter *says* made this. Verified by nothing, anywhere.

    ``handle`` is reserved for the account a hub attaches at publish time and
    is empty on a bundle exported locally. The ``install_secret``/
    ``share_secret`` pair is deliberately not reused to sign any of this: those
    exist to prove something locally and never leave the box, and a signature
    would answer "who built this" while the dangerous payload is free-text
    instructions a verified author can ship as readily as anyone.
    """

    name: str = ""
    handle: str = ""


class BundleSkills(BaseModel):
    """The skill slugs the agent's loops name, split by where they come from.

    ``carried`` arrive in ``files``; ``inherited`` are expected from
    ``agents/_shared/skills``, which is structurally absent from a bundle (a
    shared playbook is a *sibling* of the agent home, so an export cannot
    reach it). The split is the whole point: only the second kind can be
    missing on the receiving install.
    """

    carried: list[str] = Field(default_factory=list)
    inherited: list[str] = Field(default_factory=list)


class BundleRequires(BaseModel):
    """What this agent needs, derived from its own files. Never authored.

    A hand-written requirements block drifts from the files the moment a loop's
    connector changes and nothing notices, so this is read out of the content
    by :func:`requires_of` at export *and* again at preview — the importer
    never trusts the block a bundle carries.
    """

    #: Every connector the loops and the controller sample configs name,
    #: sorted and deduplicated.
    connectors: list[str] = Field(default_factory=list)
    #: ``AGENT.md``'s ``tools:`` allowlist, verbatim. Empty means unrestricted
    #: and is recorded as ``[]``, never expanded to "every tool".
    tools: list[str] = Field(default_factory=list)
    #: The model the agent asks for.
    agent_key: str = ""
    skills: BundleSkills = Field(default_factory=BundleSkills)


class Bundle(BaseModel):
    """A whole agent, as one self-describing document.

    ``schema`` is spelled ``schema_version`` in Python because a pydantic field
    named ``schema`` shadows a ``BaseModel`` attribute; the alias is what lands
    on the wire, and :func:`render` dumps by alias.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=SCHEMA, alias="schema")
    kind: str = KIND
    exported_at: str = ""
    #: Advisory, and only within one ``agent_uid``'s lineage — see the module
    #: docstring. It orders two bundles of one agent; it does not establish who
    #: is upstream, and the file diff is the authoritative answer.
    version: int = 1
    #: Free text the exporting caller supplied, carried verbatim. The changelog.
    notes: str = ""
    author: BundleAuthor = Field(default_factory=BundleAuthor)
    requires: BundleRequires = Field(default_factory=BundleRequires)
    agent: BundleAgent
    files: list[BundleFile] = Field(default_factory=list)


class PlannedFile(BaseModel):
    """A file the import would write, with the size a reviewer judges it by."""

    path: str
    bytes: int


class UnmetRequirement(BaseModel):
    """One thing this agent needs that this install cannot supply.

    Never a refusal. ``blocking`` says whether the agent can do its job at all
    without it — a connector it cannot reach and a shared playbook that does
    not exist here are blocking; a tool this install does not mount and a model
    that had to be substituted leave the agent degraded but runnable.

    ``have`` is what the install offers instead, so the sentence a surface
    renders is "it wants X, you have Y" rather than a bare absence.
    """

    #: ``"connector"``, ``"tool"``, ``"skill"`` or ``"model"``.
    kind: str
    name: str
    have: list[str] = Field(default_factory=list)
    blocking: bool = False


class BundleFinding(BaseModel):
    """One :class:`condor.agents.bundle_review.Finding`, on the wire (FEAT-144).

    A field-for-field mirror and nothing more. It exists because the review's
    ``Finding`` is a ``NamedTuple`` — which pydantic serializes positionally, so
    a surface would be reading ``finding[3]`` — and because the review module is
    deliberately stdlib-only and may not grow a pydantic import to say this
    itself. :func:`_finding_rows` builds these by keyword from ``_asdict()``, and
    ``tests/test_bundle_review.py`` asserts the two field sets are equal, so a
    field added to the review cannot silently stop reaching the dialog.
    """

    category: str
    label: str
    path: str
    line: int
    start: int
    end: int
    certain: bool = False
    context: str = ""


def _finding_rows(bundle: Bundle) -> list[BundleFinding]:
    """The review of ``bundle``, as the rows a surface renders."""
    return [
        BundleFinding(**finding._asdict()) for finding in bundle_review.review(bundle)
    ]


class ImportPlan(BaseModel):
    """What an import would do, computed without writing anything.

    Carries the bundle itself so :func:`apply` writes the very bytes this plan
    was computed from, and :meth:`summary` is the JSON-safe view both the
    preview endpoint and the apply endpoint return — which is what makes "the
    dialog cannot show something the write does not do" checkable rather than
    aspirational.
    """

    model_config = ConfigDict(populate_by_name=True)

    bundle: Bundle
    slug: str
    source_slug: str
    name: str
    description: str
    when_to_consult: str
    exported_at: str
    schema_version: int
    #: The identity that survives ``as_slug``: carried when the bundle has one,
    #: minted from the bundle's own content when it does not.
    agent_uid: str = ""
    version: int = 1
    notes: str = ""
    author: BundleAuthor = Field(default_factory=BundleAuthor)
    #: Derived here from the bundle's files, never read off its manifest.
    requires: BundleRequires = Field(default_factory=BundleRequires)
    #: What this install cannot supply. Never a reason to refuse the import.
    unmet: list[UnmetRequirement] = Field(default_factory=list)
    #: What a reader should look at before unmuting any of this (FEAT-144).
    #: Most-decisive first, and never a refusal on this side — the import's
    #: protection is that nothing arrives unmuted and nothing is started, and
    #: these are what the operator reads while deciding.
    findings: list[BundleFinding] = Field(default_factory=list)
    #: Everything that is not a ``.py``, in bundle order.
    files: list[PlannedFile] = Field(default_factory=list)
    #: The ``.py`` files, named individually: a routine runs in the Condor
    #: process and this review is the only gate there is.
    executables: list[PlannedFile] = Field(default_factory=list)
    total_bytes: int = 0
    #: Fields the write will stamp, as the strings the dialog renders.
    stamps: dict[str, str] = Field(default_factory=dict)
    #: ``{"routines": [...], "skills": [...]}`` — what arrives switched off.
    mutes: dict[str, list[str]] = Field(default_factory=dict)
    agent_key: str = ""
    agent_key_inherited: bool = False
    #: The taken slug, when this bundle cannot land under the name it asked for.
    collision: str = ""
    #: The first free ``<slug>_2``, ``<slug>_3`` … when there is a collision.
    suggested_slug: str = ""
    #: The ``agent_uid`` of the agent already installed under ``collision``.
    #: Empty when there is no collision, or when the installed agent carries no
    #: identity at all.
    collision_agent_uid: str = ""
    #: Whether the collision is the *same agent*, newer — in which case the
    #: answer is :func:`plan_update` and not a rename (FEAT-145). Stated as a
    #: field rather than left for a surface to derive by comparing two uids,
    #: so the dialog and the chat cannot disagree about which verb applies.
    is_update: bool = False
    #: Every reason this import is refused. Empty means it would run.
    refusals: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals and not self.collision

    def summary(self) -> dict:
        """The plan without the file bodies — what a surface shows a human."""
        return self.model_dump(exclude={"bundle"})


# ── What the agent needs, read out of what the agent is ──


def requires_of(files: Sequence[BundleFile]) -> BundleRequires:
    """The capability set these files ask for. The one derivation there is.

    Called by :func:`build` so a bundle states it, and again by :func:`plan` so
    a preview never has to trust what a bundle states. That is not redundancy:
    a bundle is hand-editable JSON, and a ``requires`` block understating what
    the files name would be a preview that lied about what will not work.

    Requirements are *named capabilities*, not a version range, because there
    is no Condor version to compare against: ``telemetry.context.version()`` is
    a git short hash and ``pyproject.toml`` has said ``0.1.0`` since the repo
    began, so ``condor_min_version`` would have nothing to mean.

    The connector of a loop is read through :func:`condor.preflight.requirements_of`
    — FEAT-139's single reader of a *stated* requirement — and only a loop that
    states nothing (``Requirements.declared`` false) falls back to its
    ``default_config.connector_name``. ``trading_requirements`` is never parsed
    here: a second parse of a field that already has a reader is exactly the
    two-selectors-disagreeing hazard this module refuses elsewhere.
    """
    from condor.preflight import requirements_of

    texts = {entry.path: entry.text for entry in files}
    meta = _frontmatter(texts.get(AGENT_MD, ""))

    connectors: set[str] = set()
    named_skills: set[str] = set()
    for path, text in texts.items():
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "loops" and parts[2] == "loop.md":
            loop = _frontmatter(text)
            config = loop.get("default_config")
            config = config if isinstance(config, dict) else {}
            stated = requirements_of(config)
            connector = (
                stated.connector
                if stated.declared
                else str(config.get("connector_name") or "")
            )
            if connector.strip():
                connectors.add(connector.strip())
            for skill in loop.get("skills") or []:
                if str(skill).strip():
                    named_skills.add(str(skill).strip())
        elif (
            len(parts) == 4
            and parts[0] == "controllers"
            and parts[2] == "sample_configs"
            and path.endswith((".yml", ".yaml"))
        ):
            connector = str(_yaml_mapping(text).get("connector_name") or "")
            if connector.strip():
                connectors.add(connector.strip())

    carried = {
        path.split("/")[1]
        for path in texts
        if path.startswith("skills/") and len(path.split("/")) > 2
    }
    return BundleRequires(
        connectors=sorted(connectors),
        tools=[str(tool) for tool in (meta.get("tools") or [])],
        agent_key=str(meta.get("agent_key") or ""),
        skills=BundleSkills(
            carried=sorted(named_skills & carried),
            inherited=sorted(named_skills - carried),
        ),
    )


def _frontmatter(text: str) -> dict:
    meta, _ = parse_frontmatter(text)
    return meta if isinstance(meta, dict) else {}


def _yaml_mapping(text: str) -> dict:
    """A controller sample config as a mapping, or an empty one.

    A malformed sample config is a reason to report no connector, not to refuse
    an export: the file travels either way and the reviewer can read it.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def unmet_of(
    requires: BundleRequires,
    *,
    available_connectors: Sequence[str] | None = None,
    available_tools: Sequence[str] | None = None,
    available_skills: Sequence[str] | None = None,
    substituted_model: str = "",
) -> list[UnmetRequirement]:
    """Which requirements this install cannot meet, given what it has.

    Headless on purpose: the install's connector list arrives as an **argument**
    because reading it needs an async Hummingbot client, and this module takes
    no FastAPI and no Telegram import. ``None`` means *not asked* and is not the
    same as an empty list — an unreachable server is no evidence about which
    connectors are configured, so nothing is reported unmet for it.
    """
    unmet: list[UnmetRequirement] = []
    if available_connectors is not None:
        have = sorted(set(available_connectors))
        for name in requires.connectors:
            if name not in set(have):
                unmet.append(
                    UnmetRequirement(
                        kind="connector", name=name, have=have, blocking=True
                    )
                )
    if available_tools is not None:
        have = sorted(set(available_tools))
        short = {name.rsplit("__", 1)[-1] for name in have}
        for name in requires.tools:
            if name.rsplit("__", 1)[-1] not in short:
                unmet.append(UnmetRequirement(kind="tool", name=name, blocking=False))
    if available_skills is not None:
        have = set(available_skills)
        for name in requires.skills.inherited:
            if name not in have:
                unmet.append(UnmetRequirement(kind="skill", name=name, blocking=True))
    if substituted_model:
        unmet.append(
            UnmetRequirement(
                kind="model",
                name=requires.agent_key,
                have=[substituted_model],
                blocking=False,
            )
        )
    return unmet


def _shared_skill_slugs() -> list[str]:
    """The shared playbooks this install can lend an imported agent.

    Both layers, because ``agents/_shared/skills`` is read local-then-stock
    like everything else — a stock playbook is as resolvable as a local one.
    """
    from condor.memory.paths import shared_skills_roots

    return sorted(
        {
            child.name
            for root in shared_skills_roots()
            if root.is_dir()
            for child in root.iterdir()
            if child.is_dir()
        }
    )


# ── Export ──


def build(slug: str, notes: str = "", author_name: str = "") -> Bundle:
    """The bundle for ``slug``, read from disk and scrubbed.

    Reads **both** layers, local shadowing stock per file, so a shipped agent
    travels as readily as one this install authored — which is the same read
    order ``resolve_agent_file`` applies and the reason a forked ``AGENT.md``
    does not cost an install upstream's skills.

    The one thing it writes is this agent's own ``AGENT.md`` bookkeeping
    (FEAT-143): the ``agent_uid`` it had to mint, and the version and digest of
    what just went out. Export is the right place for that and a read path is
    not — ``AgentStore.get``'s contract is that the filesystem is read on every
    call, and putting a write behind it would make reading an agent mutate it.
    Nothing is stamped until after every check has passed, so a refused export
    leaves no trace.

    ``notes`` and ``author_name`` are the exporting caller's; neither is read
    by anything that decides what gets written.

    Raises :class:`BundleError` for an agent that does not exist, a file whose
    bytes are not UTF-8 (named, so the operator can remove it), a bundle over
    :data:`MAX_BUNDLE_BYTES`, and a file carrying a value that can only be a
    secret (FEAT-144). The rest of :func:`condor.agents.bundle_review.review`'s
    findings warn rather than refuse, and a caller that wants to show them calls
    ``review`` on the returned bundle — the same pure function this used, so the
    export door and the import door cannot describe one bundle two ways.
    """
    from condor.agents.agent import AgentStore

    try:
        slug = safe_slug(slug)
    except UnsafeIdError as exc:
        raise BundleError(str(exc)) from exc

    agent = AgentStore().get(slug)
    if agent is None:
        raise BundleError(f"No agent '{slug}' to export")

    sources = _layered_sources(slug)
    if not sources:
        raise BundleError(f"Agent '{slug}' has no library files to export")

    # Every agent on disk today predates ``agent_uid``, so most exports mint
    # one. ``uuid4`` and not a content hash here, unlike the import's mint: a
    # locally authored agent is a new lineage by definition, and two unrelated
    # agents that happen to hold identical bytes are still two agents.
    agent_uid = agent.agent_uid or uuid4().hex

    files: list[BundleFile] = []
    for rel, source in sources:
        text = _read_text(source, rel)
        if rel.name.endswith(".md"):
            text = _scrub_markdown(text)
        if rel.as_posix() == AGENT_MD:
            text = _carried_agent_md(text, agent_uid)
        files.append(BundleFile(path=rel.as_posix(), sha256=_digest(text), text=text))

    digest = _content_digest(files)
    version = (
        (agent.bundle_version or 1)
        if agent.bundle_digest == digest
        else (agent.bundle_version or 0) + 1
    )

    bundle = Bundle(
        schema=SCHEMA,
        kind=KIND,
        exported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        version=version,
        notes=notes,
        author=BundleAuthor(name=author_name),
        requires=requires_of(files),
        agent=BundleAgent(
            slug=slug,
            name=agent.name,
            description=agent.description,
            when_to_consult=agent.when_to_consult,
            agent_uid=agent_uid,
        ),
        files=files,
    )

    size = len(render(bundle).encode("utf-8"))
    if size > MAX_BUNDLE_BYTES:
        raise BundleError(
            f"'{slug}' renders to {size} bytes, over the {MAX_BUNDLE_BYTES}-byte "
            "bundle limit — move its largest files out of the agent's library"
        )
    # The one refusal FEAT-144 adds, and only on this side. A key is still
    # fixable by the person who owns it while the bundle is unexported, and a
    # published one cannot be unpublished out of somebody's download. Everything
    # else the review found is a warning a caller reads off
    # ``bundle_review.review(bundle)``; only a value that can be nothing but a
    # secret stops the export, which is the same loud posture the non-UTF-8
    # refusal above already takes rather than a new one.
    refusal = bundle_review.refusal(bundle_review.review(bundle))
    if refusal:
        raise BundleError(refusal)
    _stamp_bookkeeping(slug, agent_uid, version, digest)
    return bundle


def _carried_agent_md(text: str, agent_uid: str) -> str:
    """The ``AGENT.md`` the bundle carries: identity in, bookkeeping out."""
    meta, body = parse_frontmatter(text)
    meta = meta if isinstance(meta, dict) else {}
    meta[AGENT_UID_KEY] = agent_uid
    for key in BOOKKEEPING_KEYS:
        meta.pop(key, None)
    return render_frontmatter(meta, body)


def _content_digest(files: Sequence[BundleFile]) -> str:
    """``sha256:<12>`` over what travels — path and content, in sorted order.

    Over the *bundle's* files rather than a second walk of the home, which
    gives the version-bump rule three properties for free. Runtime output is
    already excluded, because the selection is ``publishable_files``. The three
    scrubbed install-local fields are already rewritten, so changing a server
    pin is not a new version of the agent. And the bookkeeping this very export
    is about to stamp is already dropped, so the digest is stable across
    exports of unchanged content — which is the whole point, since a digest
    that measured its own stamp would bump the version on every export forever.

    Short like :func:`condor.layering.content_digest`, and for the same reason:
    it identifies *which* content, and it is not a security claim — a bundle's
    integrity is the per-file ``sha256``, which is full length.
    """
    rolling = hashlib.sha256()
    for entry in sorted(files, key=lambda f: f.path):
        rolling.update(entry.path.encode("utf-8"))
        rolling.update(b"\0")
        rolling.update(entry.sha256.encode("utf-8"))
        rolling.update(b"\0")
    return f"sha256:{rolling.hexdigest()[:12]}"


def _stamp_bookkeeping(slug: str, agent_uid: str, version: int, digest: str) -> None:
    """Record on disk what this export just said, so the next one can compare.

    Patches the frontmatter in place rather than going through
    ``AgentStore.update``: that rebuilds ``meta`` from the dataclass, whose
    ``tools`` has already been through ``_with_implied_tools`` — saving it
    would quietly write an implied tool into the authored allowlist.

    Forks a stock ``AGENT.md`` down first, like every other write to one. A
    shipped agent that is exported therefore gains a local definition; that is
    the repo's fork-on-write rule, not an exception to it.
    """
    target = fork_if_stock(slug, AGENT_MD)
    if not target.exists():
        return
    try:
        meta, body = parse_frontmatter(target.read_text(encoding="utf-8"))
    except OSError:
        log.warning("agents: could not stamp bundle bookkeeping on '%s'", slug)
        return
    meta = meta if isinstance(meta, dict) else {}
    stamped = {
        AGENT_UID_KEY: agent_uid,
        BUNDLE_VERSION_KEY: version,
        BUNDLE_DIGEST_KEY: digest,
    }
    if all(meta.get(key) == value for key, value in stamped.items()):
        return
    meta.update(stamped)
    atomic_write_text(target, render_frontmatter(meta, body))


def render(bundle: Bundle) -> str:
    """The bundle as the text that goes in the file. Indented to be read."""
    return json.dumps(bundle.model_dump(by_alias=True), indent=2, ensure_ascii=False)


def filename_for(slug: str) -> str:
    return f"{slug}{FILENAME_SUFFIX}"


def _layered_sources(slug: str) -> list[tuple[Path, Path]]:
    """``(relative path, file on disk)`` for every library file of ``slug``.

    Stock first and local second so a local file shadows the shipped one of the
    same path, item by item — the per-item layering FEAT-115 established, not a
    whole-directory choice.
    """
    local, stock = agent_home_layers(slug)
    found: dict[Path, Path] = {}
    for root in (stock, local):
        if not root.is_dir():
            continue
        for rel in publishable_files(root):
            found[rel] = root / rel
    return sorted(found.items(), key=lambda pair: pair[0].as_posix())


def _read_text(source: Path, rel: Path) -> str:
    """UTF-8 text, or a refusal naming the file.

    Text only, deliberately. Nothing in a library agent is legitimately binary
    (``.md``, ``.py``, ``.yml``), and refusing at *export* keeps the artifact
    reviewable and the importer free of a base64 branch — a loud failure the
    operator can act on, rather than a bundle the far side rejects.
    """
    try:
        return source.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(
            f"'{rel.as_posix()}' is not UTF-8 text, so it cannot travel in a "
            "bundle — remove it from the agent's library and export again"
        ) from exc
    except OSError as exc:
        raise BundleError(f"Could not read '{rel.as_posix()}': {exc}") from exc


def _scrub_markdown(text: str) -> str:
    """This install's facts, dropped from the bundle's copy of a ``.md`` file.

    The mirror of :func:`condor.layering.scrub_for_stock`, on the other
    boundary: a bundled file must not assert something that is only true of the
    install that exported it. Untouched when the file carries none of the keys,
    so every other file is byte-identical to what is on disk and a round trip
    stays lossless.
    """
    meta, body = parse_frontmatter(text)
    if not isinstance(meta, dict) or not any(key in meta for key in SCRUBBED_KEYS):
        return text
    if SERVER_NAME_KEY in meta:
        meta[SERVER_NAME_KEY] = ""
    if CREATED_BY_KEY in meta:
        meta[CREATED_BY_KEY] = 0
    meta.pop(FORKED_FROM_KEY, None)
    meta.pop(FORKED_AT_KEY, None)
    return render_frontmatter(meta, body)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Import ──


def parse(data: bytes | str) -> Bundle:
    """A bundle from bytes, or a sentence saying why these bytes are not one.

    Refuses in the order a reader would: is this JSON at all, is it one of
    *ours*, is it one this Condor can read. The schema check names both numbers
    so the answer carries its own fix.
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_BUNDLE_BYTES:
        raise BundleError(
            f"Bundle is {len(raw)} bytes, over the {MAX_BUNDLE_BYTES}-byte limit"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotJsonError(f"This file is not a JSON document ({exc})") from exc
    if not isinstance(payload, dict):
        raise NotJsonError("A bundle is a JSON object; this file is not one")

    kind = payload.get("kind")
    if kind != KIND:
        raise BundleError(
            f"This is not a Condor agent bundle (kind: {kind!r}, expected " f"'{KIND}')"
        )
    schema = payload.get("schema")
    if schema != SCHEMA:
        raise BundleError(
            f"This bundle was made by a newer Condor (schema {schema}); this "
            f"install reads up to {SCHEMA}"
        )
    try:
        return Bundle.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic's own message is the sentence
        raise BundleError(f"Malformed bundle: {exc}") from exc


def plan(
    bundle: Bundle,
    as_slug: str = "",
    user_id: int = 0,
    available_connectors: Sequence[str] | None = None,
    available_tools: Sequence[str] | None = None,
) -> ImportPlan:
    """Everything a human needs to decide, computed without writing anything.

    ``user_id`` is not in the feature's sketch of this signature and is here for
    one reason: the preview has to show the ``created_by`` it will stamp and the
    ``agent_key`` it will resolve, and the only way the dialog and the write can
    be guaranteed to agree on those is for both to come out of this call.

    ``available_connectors`` and ``available_tools`` are what *this* install can
    supply, passed in rather than fetched so the module stays headless — the
    connector list needs an async Hummingbot client. Leaving either ``None``
    means "not asked", and nothing of that kind is then reported unmet.

    Every refusal is collected rather than raised, so a reviewer sees all of
    them at once; :func:`apply` is the one that turns them into an error. An
    unmet *requirement* is not a refusal at all — it is a sentence the preview
    shows and the operator decides about. Neither is a FEAT-144 *finding*: this
    side adds no refusal of its own, not even for a certain secret, because the
    export refusal is the one that can still be acted on and refusing here would
    only keep an operator from reading what arrived.
    """
    refusals: list[str] = []
    slug, slug_refusal = _target_slug(bundle, as_slug)
    if slug_refusal:
        refusals.append(slug_refusal)

    planned: list[PlannedFile] = []
    executables: list[PlannedFile] = []
    seen: set[str] = set()
    for entry in bundle.files:
        refusal = _refuse_path(entry.path)
        if refusal:
            refusals.append(refusal)
            continue
        if entry.path in seen:
            refusals.append(f"'{entry.path}' appears twice in this bundle")
            continue
        seen.add(entry.path)
        if _digest(entry.text) != entry.sha256:
            refusals.append(
                f"'{entry.path}' does not match its sha256 — the bundle has "
                "been altered since it was exported"
            )
            continue
        row = PlannedFile(path=entry.path, bytes=len(entry.text.encode("utf-8")))
        (executables if entry.path.endswith(".py") else planned).append(row)

    if AGENT_MD not in seen:
        refusals.append(f"A bundle without {AGENT_MD} is not an agent")

    collision, suggested = ("", "")
    if slug and not slug_refusal:
        collision, suggested = _collision(slug)

    agent_uid = bundle.agent.agent_uid or _minted_uid(bundle)
    collision_uid = _installed_agent_uid(collision) if collision else ""

    agent_key, inherited = _resolve_agent_key(bundle, user_id)

    requires = requires_of(bundle.files)
    unmet = unmet_of(
        requires,
        available_connectors=available_connectors,
        available_tools=available_tools,
        available_skills=_shared_skill_slugs(),
        substituted_model=agent_key if inherited and requires.agent_key else "",
    )

    return ImportPlan(
        bundle=bundle,
        slug=slug,
        source_slug=bundle.agent.slug,
        name=bundle.agent.name,
        description=bundle.agent.description,
        when_to_consult=bundle.agent.when_to_consult,
        exported_at=bundle.exported_at,
        schema_version=bundle.schema_version,
        agent_uid=agent_uid,
        version=bundle.version,
        notes=bundle.notes,
        author=bundle.author,
        requires=requires,
        unmet=unmet,
        findings=_finding_rows(bundle),
        files=planned,
        executables=executables,
        total_bytes=sum(row.bytes for row in planned + executables),
        stamps={CREATED_BY_KEY: str(user_id), SERVER_NAME_KEY: ""},
        mutes=_mutes_for(sorted(seen)),
        agent_key=agent_key,
        agent_key_inherited=inherited,
        collision=collision,
        suggested_slug=suggested,
        collision_agent_uid=collision_uid,
        is_update=bool(collision_uid) and collision_uid == agent_uid,
        refusals=refusals,
    )


def apply(import_plan: ImportPlan, user_id: int) -> dict:
    """Write exactly what ``import_plan`` described. Starts nothing.

    All validation already ran in :func:`plan`, so the only thing between here
    and a finished agent is disk. On any write error the directory this import
    created is removed, because a half-written agent is worse than none: it
    would be listed, loadable and wrong.
    """
    from condor.agents.strategy import AlreadyExistsError

    if import_plan.collision:
        raise AlreadyExistsError(_collision_message(import_plan))
    if import_plan.refusals:
        raise BundleError(import_plan.refusals[0])

    slug = import_plan.slug
    home = agent_home(slug)
    try:
        for entry in import_plan.bundle.files:
            target = home.joinpath(*entry.path.split("/"))
            atomic_write_text(target, entry.text)
        _stamp_agent_md(home, import_plan, user_id)
        _mute_imported(slug, import_plan.mutes)
        # And nothing it brought may *execute* until the operator allows that
        # file (FEAT-147). The mute above says what the agent is told; this
        # says what runs, and it is what makes "nothing is running" below
        # true of the review surfaces as well as of the agent.
        code_trust.arm(slug)
        # And last, the record of what this bundle said, so the newer bundle
        # that arrives next month can be told apart from a local edit
        # (FEAT-145). A dotfile, so ``publishable_files`` cannot see it and a
        # re-export of this agent cannot carry its own bookkeeping.
        write_stamp(
            home,
            BundleStamp(
                agent_uid=import_plan.agent_uid,
                version=import_plan.version,
                exported_at=import_plan.bundle.exported_at,
                digests=_stamp_digests(import_plan.bundle.files),
            ),
        )
    except Exception:
        shutil.rmtree(home, ignore_errors=True)
        raise

    log.info(
        "agents: imported '%s' as '%s' (%d file(s), %d muted)",
        import_plan.source_slug,
        slug,
        len(import_plan.bundle.files),
        sum(len(names) for names in import_plan.mutes.values()),
    )
    result = import_plan.summary()
    result["imported"] = True
    found = len(import_plan.findings)
    result["next_step"] = (
        "Nothing is running, and nothing it brought has been executed. Read "
        "each imported routine and skill, then allow the routines you want to "
        "run here and unmute the ones you want this agent to use."
        + (
            f" The review found {found} thing{'' if found == 1 else 's'} to look "
            "at first."
            if found
            else ""
        )
    )
    return result


def _minted_uid(bundle: Bundle) -> str:
    """The identity to give a bundle that carries none. 32 hex, like a uuid4.

    Derived from the bundle's own content, **not** random, for two reasons that
    both matter. ``plan`` runs twice on the web path — once for the preview and
    once for the write — and a random mint would make the two disagree about
    the very field that is supposed to be stable. And two installs importing
    one pre-FEAT-143 bundle should land on the same ``agent_uid``, because
    "these two homes came from one bundle" is exactly what the field is for;
    minting randomly per install would answer no for the one case that most
    needs a yes.
    """
    return hashlib.sha256(
        b"condor-agent-uid\0" + _content_digest(bundle.files).encode("utf-8")
    ).hexdigest()[:32]


def _target_slug(bundle: Bundle, as_slug: str) -> tuple[str, str]:
    """``(slug, refusal)`` — the name this bundle would land under."""
    wanted = (as_slug or bundle.agent.slug or "").strip()
    if not wanted:
        return "", "This bundle names no agent slug, and none was given"
    try:
        return safe_slug(wanted), ""
    except UnsafeIdError:
        return "", f"'{wanted}' is not a usable agent name"


def _refuse_path(path: str) -> str:
    """Why ``path`` may not be written under an agent home, or ``""``.

    The local-layer twin of the rule ``resolve_agent_file`` already applies:
    every part is one segment, so no absolute path, no ``..``, no ``/``, ``\\``
    or NUL inside a part. The write root is always computed from the resolver
    and never joined from the bundle, so this is the second of two guards
    rather than the only one.

    Staying *inside* the directory is not enough, though, because not every
    file under an agent home is library content. Runtime state is the install's
    own, and a bundle is hand-editable JSON: a crafted one carrying
    ``loops/<s>/sessions/session_1/status.json`` with a live state and
    ``restart_on_boot`` would be read by boot reconciliation as a crashed
    session and the loop started without the operator ever choosing Start —
    and ``mutes.yml`` would arrive deciding its own muting. So the path is
    held to :func:`condor.layering.is_library_path`, which is the export's own
    rule rather than a second list beside it.
    """
    if not path or path != path.strip():
        return f"'{path}' is not a usable file path"
    parts = path.split("/")
    try:
        for part in parts:
            safe_slug(part)
    except UnsafeIdError:
        return f"'{path}' would write outside the agent's own directory"
    if Path(path).is_absolute() or "\\" in path:
        return f"'{path}' would write outside the agent's own directory"
    if not is_library_path(path):
        return (
            f"'{path}' is not part of an agent's library — a bundle carries "
            "what the agent *is*, never this install's runtime state"
        )
    return ""


def _collision(slug: str) -> tuple[str, str]:
    """``(taken slug, first free alternative)`` — or two empty strings.

    ``AgentStore.get`` resolves through **both** layers, so a shipped agent is
    refused as firmly as a local one. Import creates; it never replaces, because
    updating an agent from a bundle is a different verb with a diff-and-confirm
    problem of its own, and conflating the two is how a tuned local agent gets
    silently reset. That verb is :func:`plan_update` / :func:`apply_update`
    (FEAT-145), and ``ImportPlan.is_update`` is how a surface knows the
    collision it is looking at is a case for it rather than for a rename.
    """
    from condor.agents.agent import AgentStore

    if slug == CHAT_SLUG:
        return slug, _first_free(slug)
    store = AgentStore()
    if store.get(slug) is not None or agent_home(slug).exists():
        return slug, _first_free(slug)
    return "", ""


def _installed_agent_uid(slug: str) -> str:
    """The identity of the agent already installed under ``slug``, or ``""``.

    ``AGENT.md``'s own ``agent_uid`` first and the import stamp second, which
    is the order that keeps answering after a local edit: a save through
    ``AgentStore._save`` rebuilds the front matter from the dataclass, and the
    stamp is the copy no agent-facing tool can rewrite (FEAT-145).
    """
    from condor.agents.agent import AgentStore

    agent = AgentStore().get(slug)
    if agent is not None and agent.agent_uid:
        return agent.agent_uid
    stamp = read_stamp(agent_home(slug))
    return stamp.agent_uid if stamp else ""


def _first_free(slug: str) -> str:
    """``<slug>_2``, ``<slug>_3`` … — the first one nothing answers to."""
    from condor.agents.agent import AgentStore

    store = AgentStore()
    for n in range(2, 100):
        candidate = f"{slug}_{n}"
        if store.get(candidate) is None and not agent_home(candidate).exists():
            return candidate
    return ""


def _collision_message(import_plan: ImportPlan) -> str:
    free = import_plan.suggested_slug
    tail = f" — import it as '{free}' instead" if free else ""
    return f"Agent '{import_plan.collision}' already exists{tail}"


def _mutes_for(paths: list[str]) -> dict[str, list[str]]:
    """The routine names and skill slugs an import would switch off.

    A routine is ``routines/<name>.py`` and its name is the file stem, exactly
    as ``discover_routines_from_path`` reads it; a skill is the directory under
    ``skills/``, which is how ``SkillStore`` slugs it. Nothing else is muted —
    a controller only runs after a deliberate sync to a Hummingbot server,
    which is already its own separate act.
    """
    routines = sorted(
        {
            path.split("/")[1][: -len(".py")]
            for path in paths
            if path.startswith("routines/")
            and path.endswith(".py")
            and len(path.split("/")) == 2
            and not path.split("/")[1].startswith("_")
        }
    )
    skills = sorted(
        {
            path.split("/")[1]
            for path in paths
            if path.startswith("skills/") and len(path.split("/")) > 2
        }
    )
    return {"routines": routines, "skills": skills}


def _mute_imported(slug: str, mutes: dict[str, list[str]]) -> None:
    from condor.memory.mutes import set_muted

    for kind, names in mutes.items():
        for name in names:
            set_muted(slug, kind, name, True)


def _resolve_agent_key(bundle: Bundle, user_id: int) -> tuple[str, bool]:
    """``(agent_key, inherited)`` for the imported agent.

    The key travels — an agent authored against a local Ollama model says
    something real about how it was tuned. It is kept when this install knows
    how to start it, and otherwise falls back to the importer's own active
    model, which is exactly what ``create`` already does for an unspecified one.
    """
    carried = ""
    for entry in bundle.files:
        if entry.path == AGENT_MD:
            meta, _ = parse_frontmatter(entry.text)
            carried = str((meta or {}).get("agent_key") or "")
            break
    if carried and _agent_key_known(carried, user_id):
        return carried, False
    fallback = _active_agent_key(user_id)
    if fallback:
        return fallback, True
    return carried, False


def _agent_key_known(agent_key: str, user_id: int) -> bool:
    """True when this install knows how to start ``agent_key``.

    Catalog membership, not a live probe: resolving a model here would shell out
    to every bridge on an import, and a bridge that happens to be logged out is
    not a reason to rewrite the agent's model. A family key
    (``ollama:<model>``, ``openrouter:<model>``) counts when the family is in
    the catalog; a ``custom@<endpoint>:<model>`` counts when the importer
    actually has that endpoint saved.
    """
    from condor.llm.options import AGENT_OPTIONS, selectable_agent_options
    from condor.llm.readiness import base_of

    if agent_key in selectable_agent_options():
        return True
    base = base_of(agent_key)
    if base == "custom":
        from condor.preferences import resolve_custom_endpoint

        base_url, _ = resolve_custom_endpoint(
            agent_key, user_id=user_id, env_fallback=False
        )
        return bool(base_url)
    return f"{base}:" in AGENT_OPTIONS


def _active_agent_key(user_id: int) -> str:
    try:
        from condor.preferences import get_active_agent_key

        return get_active_agent_key(user_id) or ""
    except Exception:  # noqa: BLE001 - no preferences is not a failed import
        return ""


def _stamp_agent_md(home: Path, import_plan: ImportPlan, user_id: int) -> None:
    """Make the written ``AGENT.md`` true of *this* install.

    The export already scrubbed these fields, so this is what makes a
    hand-assembled bundle land on the same footing: the importer owns the
    agent, the pin is empty, the fork stamp is gone, and the model is the one
    :func:`_resolve_agent_key` settled on.

    ``agent_uid`` is the one thing written here that is *not* local: it is the
    bundle's, or the one minted from it, unchanged by ``as_slug``. That is what
    makes two differently-named homes agree on which agent they are.
    ``bundle_version`` carries the lineage forward; ``bundle_digest`` does not,
    because it measured the exporter's content and this home's ``AGENT.md`` is
    no longer those bytes — so the first re-export from here is honestly a new
    version rather than a false match.
    """
    path = home / AGENT_MD
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta = meta if isinstance(meta, dict) else {}
    meta[CREATED_BY_KEY] = user_id
    meta[SERVER_NAME_KEY] = ""
    meta.pop(FORKED_FROM_KEY, None)
    meta.pop(FORKED_AT_KEY, None)
    meta["agent_key"] = import_plan.agent_key
    meta[AGENT_UID_KEY] = import_plan.agent_uid
    meta[BUNDLE_VERSION_KEY] = import_plan.version
    meta.pop(BUNDLE_DIGEST_KEY, None)
    atomic_write_text(path, render_frontmatter(meta, body))


# ── Update an installed agent from a newer bundle (FEAT-145) ──
#
# The import above creates and never replaces, which left a newer version of an
# agent you already have landing beside the old one under ``<slug>_2`` with the
# memory forked. This is the other verb: it shows what would change file by
# file, replaces the library content, and keeps everything this install earned
# by running the agent.
#
# **The split that makes it safe is already the export's own.**
# ``publishable_files`` divides a home into library content and what this
# install accumulated; an update replaces exactly the first and never looks at
# the second, so ``store/``, ``config.yml``, ``mutes.yml``, ``sessions/`` and
# the rest are untouched by construction rather than by a second denylist.
#
# **A conflict is kept, never merged.** These are markdown instructions a model
# reads and YAML a trader tuned: a conflict-marked ``SKILL.md`` is not a
# conflict to resolve later, it is a *new instruction* the model will follow. A
# file the operator edited keeps its bytes and the plan carries the bundle's
# copy so a surface can offer it. That default is the deliberate half — an
# operator's edit is the only content in the home nobody else can reproduce.

#: The import stamp, at the root of the agent home: the record of *what the
#: bundle said last time*, which is the only way to tell an operator's local
#: edit from an upstream change. A dotfile, so ``publishable_files`` cannot see
#: it and it can never travel in a bundle of its own agent — invisible to the
#: export by construction, not by being added to a list.
STAMP_FILENAME = ".bundle.json"

#: Where an update leaves the bytes it replaced or deleted, under a timestamp.
#: A dotdir, excluded from publishing for the same structural reason, and the
#: ``.server_backups`` idiom FEAT-126 set: an overwrite leaves the previous
#: bytes behind, out of the library.
BACKUP_DIRNAME = ".bundle_backup"

#: The per-path verdicts :func:`plan_update` assigns, computed from three
#: digests — the bundle's, the file on disk, and the stamp's.
ADDED = "added"
CHANGED = "changed"
UNCHANGED = "unchanged"
LOCAL_ONLY = "local_only"
CONFLICT = "conflict"
REMOVED = "removed"
CONFLICT_REMOVED = "conflict_removed"

#: The verdicts whose bytes :func:`apply_update` writes from the bundle.
_ADOPTED = (ADDED, CHANGED)

#: The verdicts whose previous bytes are copied aside before anything happens.
_BACKED_UP = (CHANGED, REMOVED)

#: The verdicts that keep what is on disk and leave the stamp alone, so the
#: divergence survives into every later update instead of being forgotten and
#: silently overwritten by the next bundle that touches the same path.
_KEPT_LOCAL = (LOCAL_ONLY, CONFLICT, CONFLICT_REMOVED)

#: The ``AGENT.md`` front-matter keys an update keeps from the installed copy
#: rather than taking from the bundle. Each is a fact about *this* install and
#: not part of the agent: who owns it here, when it landed here, which server
#: its orders go to, and which model this install resolved for it. Everything
#: else in the file — the instructions, the description, the ``tools``
#: allowlist — is library content and is replaced like any other file.
INSTALL_LOCAL_KEYS = (CREATED_BY_KEY, SERVER_NAME_KEY, "created_at", "agent_key")

#: What a diff of ``AGENT.md`` must not see. That file is the one place where
#: library content and this install's own facts share a document, so pinning a
#: server or resolving a model would otherwise read as the operator having
#: edited the agent's instructions — which would freeze upstream's instructions
#: out of every future update, the opposite of what this verb is for.
_IGNORED_AGENT_MD_KEYS = (
    *INSTALL_LOCAL_KEYS,
    *BOOKKEEPING_KEYS,
    AGENT_UID_KEY,
    FORKED_FROM_KEY,
    FORKED_AT_KEY,
)


class BundleStamp(BaseModel):
    """What the bundle said last time this install accepted one.

    ``digests`` is a digest per carried path over the *comparable* form of that
    path's bundled text (:func:`_comparable`), so it answers exactly one
    question: which upstream bytes has this install already taken for this
    file. A path whose local copy was kept in a conflict is deliberately **not**
    updated, which is what keeps the divergence visible to the next update.
    """

    agent_uid: str = ""
    version: int = 0
    exported_at: str = ""
    digests: dict[str, str] = Field(default_factory=dict)

    def knows(self, path: str) -> bool:
        return path in self.digests

    def upstream(self, path: str) -> str:
        return self.digests.get(path, "")


class UpdateBlocker(BaseModel):
    """Something that refuses the update, in words the operator can act on."""

    code: str
    message: str


class UpdateWarning(BaseModel):
    """A consequence worth knowing about. Never a reason to refuse."""

    code: str
    message: str


class UpdateFile(BaseModel):
    """One path, and what the update would do to it."""

    path: str
    verdict: str
    #: The bundle's copy, in bytes of UTF-8. ``0`` when the bundle carries none.
    bytes: int = 0
    #: ``True`` for a ``.py``. Execution consent is keyed by content
    #: (:mod:`condor.agents.code_trust`), so a replaced routine is pending
    #: again and has to be read and allowed before it can run.
    executable: bool = False
    #: The bundle's copy, carried **only** for a ``conflict``: the local bytes
    #: are what survives, so this is what a surface offers instead. Empty for
    #: every other verdict, which is what keeps a plan summary the size of a
    #: diff rather than the size of the agent.
    incoming: str = ""


class UpdatePlan(BaseModel):
    """What an update would do, computed without writing anything.

    Speaks ``condor/updates``' vocabulary one level down — blockers, warnings
    and a plan — rather than inventing a third, and keeps :func:`plan`'s
    one-builder rule: the dialog and the write call this same function, so
    pressing Update does exactly what the diff showed.

    ``unmet`` and ``findings`` are carried as their own lists rather than
    flattened into ``warnings``. Both already have a renderer on every surface
    that shows an import, and a second copy of each as a sentence would be the
    same fact reported twice in two shapes.
    """

    model_config = ConfigDict(populate_by_name=True)

    bundle: Bundle
    slug: str
    #: The identity the bundle claims, or the one minted from its content.
    agent_uid: str = ""
    #: The identity of the agent already installed here. Equal to the above is
    #: what makes this an update rather than a collision.
    installed_agent_uid: str = ""
    version: int = 1
    installed_version: int = 0
    exported_at: str = ""
    notes: str = ""
    author: BundleAuthor = Field(default_factory=BundleAuthor)
    requires: BundleRequires = Field(default_factory=BundleRequires)
    unmet: list[UnmetRequirement] = Field(default_factory=list)
    findings: list[BundleFinding] = Field(default_factory=list)
    #: One entry per path, in path order. A file the bundle never carried and
    #: this install never installed — the operator's own — is not listed at all.
    files: list[UpdateFile] = Field(default_factory=list)
    #: ``{verdict: count}`` for the verdicts present, so a surface can say what
    #: this update is without walking the list.
    counts: dict[str, int] = Field(default_factory=dict)
    #: ``{"routines": [...], "skills": [...]}`` — what arrives switched off.
    #: Only what is genuinely new: an item the operator unmuted after reading
    #: it stays unmuted, and a changed one is not re-muted.
    mutes: dict[str, list[str]] = Field(default_factory=dict)
    blockers: list[UpdateBlocker] = Field(default_factory=list)
    warnings: list[UpdateWarning] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers

    def summary(self) -> dict:
        """The plan without the bundle — what a surface shows a human."""
        return self.model_dump(exclude={"bundle"})


def stamp_path(home: Path) -> Path:
    """``agents/<slug>/.bundle.json``."""
    return home / STAMP_FILENAME


def read_stamp(home: Path) -> BundleStamp | None:
    """The import stamp under ``home``, or ``None`` when there is no basis.

    An unreadable or malformed stamp answers ``None`` rather than an empty one.
    The two are not the same: ``None`` means "nothing here says a bundle is
    this agent's upstream", which blocks the update and offers import-as-new,
    while an empty stamp would mean "upstream has never carried any of these
    files" and would classify the whole agent as a conflict against itself.

    Its paths are held to :func:`condor.layering.is_library_path`, the same rule
    :func:`_refuse_path` applies to a bundle's. No bundle can write this file —
    the import refuses a dotted path — but it is plain JSON in a directory the
    operator owns, and a path read out of it reaches ``apply_update``'s
    ``unlink`` of a ``removed`` row. A ``..`` typed in by hand must not be able
    to delete anything.
    """
    try:
        payload = json.loads(stamp_path(home).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        log.warning("agents: unreadable %s under %s", STAMP_FILENAME, home)
        return None
    if not isinstance(payload, dict):
        return None
    try:
        stamp = BundleStamp.model_validate(payload)
    except Exception:  # noqa: BLE001 - a stamp we cannot read is no basis
        log.warning("agents: malformed %s under %s", STAMP_FILENAME, home)
        return None
    kept = {
        path: digest for path, digest in stamp.digests.items() if is_library_path(path)
    }
    if len(kept) != len(stamp.digests):
        log.warning(
            "agents: dropped %d non-library path(s) from %s under %s",
            len(stamp.digests) - len(kept),
            STAMP_FILENAME,
            home,
        )
    return stamp.model_copy(update={"digests": kept})


def write_stamp(home: Path, stamp: BundleStamp) -> None:
    """Record what the bundle said, so the next update can compare.

    Written **last** by both the import and the update, so an interrupted write
    leaves a stamp describing the previous state. A file already replaced then
    reads as holding the bundle's bytes and is classified ``unchanged``, and one
    not reached yet still reads ``changed`` — so the next plan is correct and
    the update resumes rather than reporting the whole agent as a conflict.
    """
    atomic_write_text(
        stamp_path(home),
        json.dumps(stamp.model_dump(), indent=2, ensure_ascii=False, sort_keys=True),
    )


def _comparable(path: str, text: str) -> str:
    """The form of ``path``'s text that a diff is computed over.

    Identity for every file but ``AGENT.md``, whose front matter carries both
    library content and this install's own facts in one document — see
    :data:`_IGNORED_AGENT_MD_KEYS`. The mapping is compared as a *sorted*
    mapping and the body stripped, so a plain re-save through
    ``AgentStore._save`` (which rebuilds the front matter in its own key order)
    is not mistaken for the operator having rewritten the agent.
    """
    if path != AGENT_MD:
        return text
    meta, body = parse_frontmatter(text)
    meta = dict(meta) if isinstance(meta, dict) else {}
    for key in _IGNORED_AGENT_MD_KEYS:
        meta.pop(key, None)
    return json.dumps(meta, sort_keys=True, default=str) + "\n" + body.strip()


def _stamp_digests(files: Sequence[BundleFile]) -> dict[str, str]:
    """The comparable digest of every file a bundle carries, by path."""
    return {entry.path: _digest(_comparable(entry.path, entry.text)) for entry in files}


def _disk_digests(home: Path) -> dict[str, str]:
    """The comparable digest of every **library** file under ``home``.

    The selection is ``publishable_files``, which is what makes "runtime output
    is untouched" structural: a path this never yields is a path no verdict is
    ever computed for, so nothing under ``store/``, ``sessions/`` or beside
    ``config.yml`` can be replaced or deleted by an update.
    """
    found: dict[str, str] = {}
    for rel in publishable_files(home):
        path = rel.as_posix()
        try:
            found[path] = _digest(_comparable(path, (home / rel).read_text("utf-8")))
        except (OSError, UnicodeDecodeError):
            # Unreadable or not UTF-8: digest the raw bytes instead, so the file
            # reads as diverged from whatever the bundle carries rather than as
            # absent. Absent would make an update delete or replace it.
            try:
                found[path] = hashlib.sha256((home / rel).read_bytes()).hexdigest()
            except OSError:
                found[path] = "unreadable"
    return found


def _classify(
    carried: dict[str, str], disk: dict[str, str], stamp: BundleStamp
) -> list[tuple[str, str]]:
    """``(path, verdict)`` for every path either side has an opinion about.

    The table FEAT-145 states, plus the two cases it does not reach:

    * **A path on disk that the stamp never knew, holding bytes the bundle
      does not carry**, is a ``conflict`` rather than an ``added``. The
      operator has content at that path which this install never installed;
      replacing it would destroy the only bytes in the home nobody else can
      reproduce, which is the thing every other row of the table is arranged to
      avoid. Holding the bundle's *own* bytes it stays ``added`` — the write is
      then a no-op, and calling it unchanged would leave a new routine unmuted.
    * **A path the stamp knows whose disk copy already holds the bundle's
      bytes** is ``unchanged`` whatever the stamp says. Those two together are
      what makes an interrupted update resumable: the files it got to are
      recognised as done instead of reading as a conflict between the operator
      and upstream.

    A deleted file is folded into "edited" by comparing an absent digest, so a
    library file the operator removed is not resurrected behind their back —
    it is reported, with the bundle's copy on offer where upstream changed it.
    """
    verdicts: list[tuple[str, str]] = []
    for path in sorted(set(carried) | set(disk) | set(stamp.digests)):
        in_bundle = path in carried
        known = stamp.knows(path)
        local = disk.get(path, "")
        if not known and not in_bundle:
            continue  # the operator's own file — never listed, never touched
        if not known:
            diverged = bool(local) and local != carried[path]
            verdicts.append((path, CONFLICT if diverged else ADDED))
            continue
        if not in_bundle:
            kept = bool(local) and local != stamp.upstream(path)
            verdicts.append((path, CONFLICT_REMOVED if kept else REMOVED))
            continue
        if local and local == carried[path]:
            verdicts.append((path, UNCHANGED))
            continue
        edited = local != stamp.upstream(path)
        upstream_changed = carried[path] != stamp.upstream(path)
        if edited and upstream_changed:
            verdicts.append((path, CONFLICT))
        elif edited:
            verdicts.append((path, LOCAL_ONLY))
        elif upstream_changed:
            verdicts.append((path, CHANGED))
        else:
            verdicts.append((path, UNCHANGED))
    return verdicts


def _added_mutes(added: Sequence[str], known: Sequence[str]) -> dict[str, list[str]]:
    """The routines and skills an update switches off: the genuinely new ones.

    A ``changed`` item is **not** re-muted, and a name already in ``mutes.yml``
    is left exactly as it is. That asymmetry is a decision: re-muting on every
    update would make updating an agent cost the whole curation pass again, and
    the diff already told the operator which instructions changed. An item the
    operator unmuted after reading it stays unmuted.
    """
    fresh = _mutes_for(sorted(added))
    had = _mutes_for(sorted(known))
    return {
        kind: [name for name in names if name not in set(had[kind])]
        for kind, names in fresh.items()
    }


def plan_update(
    bundle: Bundle,
    slug: str,
    available_connectors: Sequence[str] | None = None,
    available_tools: Sequence[str] | None = None,
) -> UpdatePlan:
    """What updating ``slug`` from ``bundle`` would do. Writes nothing.

    Every blocker is collected rather than raised, so a reviewer sees all of
    them at once; :func:`apply_update` is the one that turns them into an error.
    The bundle is held to every check :func:`plan` applies to an import —
    per-file digest, no duplicate path, path containment through ``safe_slug``,
    library paths only, an ``AGENT.md`` present — and all of it before the
    first write.

    ``available_connectors`` and ``available_tools`` are what *this* install
    can supply, passed in rather than fetched so the module stays headless.
    ``None`` means "not asked", exactly as in :func:`unmet_of`.
    """
    from condor.agents.agent import AgentStore

    blockers: list[UpdateBlocker] = []
    warnings: list[UpdateWarning] = []

    carried: dict[str, str] = {}
    texts: dict[str, str] = {}
    for entry in bundle.files:
        refusal = _refuse_path(entry.path)
        if refusal:
            blockers.append(UpdateBlocker(code="unusable_path", message=refusal))
            continue
        if entry.path in texts:
            blockers.append(
                UpdateBlocker(
                    code="duplicate_path",
                    message=f"'{entry.path}' appears twice in this bundle",
                )
            )
            continue
        if _digest(entry.text) != entry.sha256:
            blockers.append(
                UpdateBlocker(
                    code="altered",
                    message=(
                        f"'{entry.path}' does not match its sha256 — the bundle "
                        "has been altered since it was exported"
                    ),
                )
            )
            continue
        texts[entry.path] = entry.text
        carried[entry.path] = _digest(_comparable(entry.path, entry.text))
    if AGENT_MD not in texts:
        blockers.append(
            UpdateBlocker(
                code="not_an_agent",
                message=f"A bundle without {AGENT_MD} is not an agent",
            )
        )

    incoming_uid = bundle.agent.agent_uid or _minted_uid(bundle)
    try:
        slug = safe_slug(slug)
    except UnsafeIdError:
        blockers.append(
            UpdateBlocker(
                code="unusable_slug", message=f"'{slug}' is not a usable agent name"
            )
        )
        return UpdatePlan(
            bundle=bundle,
            slug="",
            agent_uid=incoming_uid,
            version=bundle.version,
            exported_at=bundle.exported_at,
            notes=bundle.notes,
            author=bundle.author,
            blockers=blockers,
        )

    local, stock = agent_home_layers(slug)
    agent = AgentStore().get(slug)
    stamp = read_stamp(local)
    installed = local.is_dir()
    if not installed:
        if stock.is_dir() or agent is not None:
            blockers.append(
                UpdateBlocker(
                    code="stock_agent",
                    message=(
                        f"'{slug}' is an agent Condor ships, and the shipped "
                        "library is only written by publishing. Import this "
                        "bundle under another name instead."
                    ),
                )
            )
        else:
            blockers.append(
                UpdateBlocker(
                    code="no_such_agent",
                    message=(
                        f"No agent '{slug}' is installed here, so there is "
                        "nothing to update — import it as a new agent instead."
                    ),
                )
            )

    installed_uid = (agent.agent_uid if agent else "") or (
        stamp.agent_uid if stamp else ""
    )
    installed_version = (
        stamp.version if stamp else (agent.bundle_version if agent else 0)
    )

    if installed:
        if not installed_uid and stamp is None:
            blockers.append(
                UpdateBlocker(
                    code="no_basis",
                    message=(
                        f"'{slug}' carries no agent_uid and was never imported "
                        "from a bundle, so nothing here says this bundle is its "
                        "upstream. Import it as a new agent instead."
                    ),
                )
            )
        elif installed_uid and installed_uid != incoming_uid:
            blockers.append(
                UpdateBlocker(
                    code="different_agent",
                    message=(
                        f"This bundle is agent {incoming_uid}; '{slug}' here is "
                        f"agent {installed_uid}. Those are two different agents, "
                        "not an update — import it under another name."
                    ),
                )
            )
        elif stamp is None:
            warnings.append(
                UpdateWarning(
                    code="no_stamp",
                    message=(
                        f"'{slug}' has no import stamp, so there is no record of "
                        "what a bundle last said about it. Every file it already "
                        "has reads as a conflict and keeps its local bytes; only "
                        "files it does not have yet will land."
                    ),
                )
            )

    if stamp is not None and bundle.version < stamp.version:
        blockers.append(
            UpdateBlocker(
                code="older_bundle",
                message=(
                    f"This bundle is version {bundle.version} and '{slug}' is "
                    f"already at version {stamp.version} — it is not newer."
                ),
            )
        )
    elif (
        stamp is not None
        and bundle.version == stamp.version
        and carried != stamp.digests
    ):
        warnings.append(
            UpdateWarning(
                code="same_version_new_build",
                message=(
                    f"This bundle says version {bundle.version}, the same as the "
                    "one installed, but its files differ — a different build of "
                    "the same version. A version only orders bundles within one "
                    "agent's lineage; the diff is the authoritative answer."
                ),
            )
        )

    if _engine_running(slug):
        blockers.append(
            UpdateBlocker(
                code="live_loop",
                message=(
                    f"'{slug}' has a loop running right now. Swapping a skill's "
                    "instructions under a session that is mid-tick changes what "
                    "the model is told between one tool call and the next — stop "
                    "the loop first."
                ),
            )
        )

    disk = _disk_digests(local) if installed else {}
    rows: list[UpdateFile] = []
    for path, verdict in _classify(carried, disk, stamp or BundleStamp()):
        text = texts.get(path, "")
        rows.append(
            UpdateFile(
                path=path,
                verdict=verdict,
                bytes=len(text.encode("utf-8")) if text else 0,
                executable=path.endswith(".py"),
                incoming=text if verdict == CONFLICT else "",
            )
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1

    requires = requires_of(bundle.files)
    unmet = unmet_of(
        requires,
        available_connectors=available_connectors,
        available_tools=available_tools,
        available_skills=_shared_skill_slugs(),
    )

    return UpdatePlan(
        bundle=bundle,
        slug=slug,
        agent_uid=incoming_uid,
        installed_agent_uid=installed_uid,
        version=bundle.version,
        installed_version=installed_version,
        exported_at=bundle.exported_at,
        notes=bundle.notes,
        author=bundle.author,
        requires=requires,
        unmet=unmet,
        findings=_finding_rows(bundle),
        files=rows,
        counts=counts,
        mutes=_added_mutes(
            [row.path for row in rows if row.verdict == ADDED],
            # What this agent already had, which is every path either side
            # knows *minus* the ones this update is adding. Subtracting them is
            # what makes a resumed update still mute a routine whose file the
            # interrupted run had already written: it is on disk, but it is
            # this update's own work and not something the agent had.
            sorted(
                (set(stamp.digests if stamp else {}) | set(disk))
                - {row.path for row in rows if row.verdict == ADDED}
            ),
        ),
        blockers=blockers,
        warnings=warnings,
    )


def _engine_running(slug: str) -> bool:
    """Whether a ``TickEngine`` exists for ``slug`` — a live trading loop.

    A supervisor that cannot be reached answers *no engine*: this is one of six
    blockers and the only one that depends on live runtime state, and a preview
    that refused every update because the supervisor was mid-import would be a
    verb nobody could use. The route that applies re-plans before it writes, so
    the check runs again against the state of the moment.
    """
    try:
        from condor.agents.engine import get_engine

        return get_engine(slug) is not None
    except Exception:  # noqa: BLE001 - no supervisor is not a running loop
        log.debug("agents: could not ask the supervisor about '%s'", slug)
        return False


def apply_update(update_plan: UpdatePlan, user_id: int = 0) -> dict:
    """Write exactly what ``update_plan`` described. Starts nothing.

    All validation already ran in :func:`plan_update`, so the only thing
    between here and an updated agent is disk. There is no rollback and
    deliberately so — the home already holds an agent somebody has been
    running, and removing it on a half-finished write is the silent reset this
    whole verb exists to avoid. What stands in for one is the pair of
    guarantees the order of these steps gives: the previous bytes of every
    replaced or deleted file are copied aside *first*, and the stamp is
    rewritten *last*, so an interrupted update is resumable and reversible by
    hand.

    Nothing is started. No ``control_agent(start)``, no delegation, no tick:
    the result names the next step instead.
    """
    if update_plan.blockers:
        raise BundleError(update_plan.blockers[0].message)

    slug = update_plan.slug
    home = agent_home(slug)
    texts = {entry.path: entry.text for entry in update_plan.bundle.files}
    digests = dict((read_stamp(home) or BundleStamp()).digests)
    backup = (
        home / BACKUP_DIRNAME / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )

    for row in update_plan.files:
        target = home.joinpath(*row.path.split("/"))
        if row.verdict in _BACKED_UP and target.is_file():
            kept = backup.joinpath(*row.path.split("/"))
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, kept)
        if row.verdict in _ADOPTED:
            text = texts.get(row.path, "")
            if row.path == AGENT_MD:
                text = _updated_agent_md(target, text, update_plan)
            atomic_write_text(target, text)
            digests[row.path] = _digest(_comparable(row.path, texts.get(row.path, "")))
        elif row.verdict == REMOVED:
            target.unlink(missing_ok=True)
            _prune_empty(home, target.parent)
            digests.pop(row.path, None)
        elif row.verdict == UNCHANGED:
            digests[row.path] = _digest(_comparable(row.path, texts.get(row.path, "")))

    # An update keeps the curation the operator performed after the last import:
    # only what is genuinely new arrives switched off.
    _mute_imported(slug, update_plan.mutes)
    # Consent is keyed by content, so every replaced routine is pending again
    # and has to be read and allowed before it can run (FEAT-147). Arming is
    # idempotent in the only direction that matters — an agent already gated
    # keeps whatever has been allowed.
    code_trust.arm(slug)
    if _verdict_of(update_plan, AGENT_MD) == UNCHANGED:
        _carry_bookkeeping(home / AGENT_MD, update_plan)
    write_stamp(
        home,
        BundleStamp(
            agent_uid=update_plan.agent_uid,
            version=update_plan.version,
            exported_at=update_plan.bundle.exported_at,
            digests=digests,
        ),
    )

    log.info(
        "agents: updated '%s' to version %d (%s)",
        slug,
        update_plan.version,
        ", ".join(f"{n} {verdict}" for verdict, n in sorted(update_plan.counts.items()))
        or "no change",
    )
    result = update_plan.summary()
    result["updated"] = True
    result["backup"] = str(backup) if backup.is_dir() else ""
    result["next_step"] = _update_next_step(update_plan)
    return result


def _verdict_of(update_plan: UpdatePlan, path: str) -> str:
    for row in update_plan.files:
        if row.path == path:
            return row.verdict
    return ""


def _update_next_step(update_plan: UpdatePlan) -> str:
    """What the operator has to do now, naming only what actually happened."""
    parts = ["Nothing was started and nothing this update brought has run."]
    kept = [row.path for row in update_plan.files if row.verdict in _KEPT_LOCAL]
    if kept:
        parts.append(
            f"{len(kept)} file{'' if len(kept) == 1 else 's'} you had edited kept "
            f"your copy ({', '.join(kept[:5])}"
            + (", …" if len(kept) > 5 else "")
            + ")."
        )
    replaced = [
        row.path
        for row in update_plan.files
        if row.verdict in _ADOPTED and row.executable
    ]
    if replaced:
        parts.append(
            f"{len(replaced)} routine file{'' if len(replaced) == 1 else 's'} "
            "changed, so each needs reading and allowing again before it can run."
        )
    muted = sum(len(names) for names in update_plan.mutes.values())
    if muted:
        parts.append(
            f"{muted} new item{'' if muted == 1 else 's'} arrived muted — read "
            "each one before you switch it on."
        )
    return " ".join(parts)


def _updated_agent_md(path: Path, incoming: str, update_plan: UpdatePlan) -> str:
    """The bundle's ``AGENT.md``, with this install's own facts carried over.

    The body and every other front-matter key are the bundle's — that is the
    library content this verb exists to replace. :data:`INSTALL_LOCAL_KEYS` are
    taken from the copy already on disk, so an update cannot reassign the agent
    to the exporter, unpin the server its orders go to, or rewrite the model
    this install resolved for it.
    """
    meta, body = parse_frontmatter(incoming)
    meta = dict(meta) if isinstance(meta, dict) else {}
    try:
        local_meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        local_meta = {}
    local_meta = local_meta if isinstance(local_meta, dict) else {}
    for key in INSTALL_LOCAL_KEYS:
        if key in local_meta:
            meta[key] = local_meta[key]
    meta.pop(FORKED_FROM_KEY, None)
    meta.pop(FORKED_AT_KEY, None)
    meta.pop(BUNDLE_DIGEST_KEY, None)
    meta[AGENT_UID_KEY] = update_plan.agent_uid
    meta[BUNDLE_VERSION_KEY] = update_plan.version
    return render_frontmatter(meta, body)


def _carry_bookkeeping(path: Path, update_plan: UpdatePlan) -> None:
    """Move ``AGENT.md``'s lineage bookkeeping forward without touching content.

    Only needed when ``AGENT.md`` itself is ``unchanged`` — an adopted one is
    rewritten by :func:`_updated_agent_md` and a kept one is left alone, since
    writing to a file this update promised not to touch is exactly the thing
    the conflict verdict is a promise about.
    """
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        log.warning("agents: could not carry bookkeeping onto %s", path)
        return
    meta = dict(meta) if isinstance(meta, dict) else {}
    if (
        meta.get(AGENT_UID_KEY) == update_plan.agent_uid
        and meta.get(BUNDLE_VERSION_KEY) == update_plan.version
        and BUNDLE_DIGEST_KEY not in meta
    ):
        return
    meta[AGENT_UID_KEY] = update_plan.agent_uid
    meta[BUNDLE_VERSION_KEY] = update_plan.version
    meta.pop(BUNDLE_DIGEST_KEY, None)
    atomic_write_text(path, render_frontmatter(meta, body))


def _prune_empty(home: Path, directory: Path) -> None:
    """Remove the directories a deletion emptied, never ``home`` itself."""
    current = directory
    while current != home and home in current.parents:
        try:
            next(current.iterdir())
            return
        except StopIteration:
            pass
        except OSError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
