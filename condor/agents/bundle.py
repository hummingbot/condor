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
(:mod:`condor.memory.mutes`) so the code is inert until the operator reads it
and unmutes it item by item. Import creates; it never overwrites, never starts
anything, and never writes into the shipped tree.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from condor.frontmatter import parse_frontmatter, render_frontmatter
from condor.fsutil import atomic_write_text
from condor.layering import (
    CREATED_BY_KEY,
    FORKED_AT_KEY,
    FORKED_FROM_KEY,
    publishable_files,
)
from condor.memory.paths import CHAT_SLUG, agent_home, agent_home_layers, safe_slug
from condor.paths import UnsafeIdError

log = logging.getLogger(__name__)

#: Bumped whenever the document's shape changes in a way an older reader would
#: misread. An unknown value is refused **whole**, the stance
#: ``condor/sharing/wire.py`` sets: a bundle from a newer Condor fails loudly
#: rather than being half-understood.
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

#: The frontmatter keys rewritten on the way out. Named here so the export
#: scrub, the import stamp and the preview all read one list.
SCRUBBED_KEYS = (SERVER_NAME_KEY, CREATED_BY_KEY, FORKED_FROM_KEY, FORKED_AT_KEY)


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
    """The identity a reviewer sees before opening a single file."""

    slug: str
    name: str = ""
    description: str = ""
    when_to_consult: str = ""


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
    agent: BundleAgent
    files: list[BundleFile] = Field(default_factory=list)


class PlannedFile(BaseModel):
    """A file the import would write, with the size a reviewer judges it by."""

    path: str
    bytes: int


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
    #: Every reason this import is refused. Empty means it would run.
    refusals: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals and not self.collision

    def summary(self) -> dict:
        """The plan without the file bodies — what a surface shows a human."""
        return self.model_dump(exclude={"bundle"})


# ── Export ──


def build(slug: str) -> Bundle:
    """The bundle for ``slug``, read from disk and scrubbed. Writes nothing.

    Reads **both** layers, local shadowing stock per file, so a shipped agent
    travels as readily as one this install authored — which is the same read
    order ``resolve_agent_file`` applies and the reason a forked ``AGENT.md``
    does not cost an install upstream's skills.

    Raises :class:`BundleError` for an agent that does not exist, a file whose
    bytes are not UTF-8 (named, so the operator can remove it), and a bundle
    over :data:`MAX_BUNDLE_BYTES`.
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

    files: list[BundleFile] = []
    for rel, source in sources:
        text = _read_text(source, rel)
        if rel.name.endswith(".md"):
            text = _scrub_markdown(text)
        files.append(BundleFile(path=rel.as_posix(), sha256=_digest(text), text=text))

    bundle = Bundle(
        schema=SCHEMA,
        kind=KIND,
        exported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        agent=BundleAgent(
            slug=slug,
            name=agent.name,
            description=agent.description,
            when_to_consult=agent.when_to_consult,
        ),
        files=files,
    )

    size = len(render(bundle).encode("utf-8"))
    if size > MAX_BUNDLE_BYTES:
        raise BundleError(
            f"'{slug}' renders to {size} bytes, over the {MAX_BUNDLE_BYTES}-byte "
            "bundle limit — move its largest files out of the agent's library"
        )
    return bundle


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


def plan(bundle: Bundle, as_slug: str = "", user_id: int = 0) -> ImportPlan:
    """Everything a human needs to decide, computed without writing anything.

    ``user_id`` is not in the feature's sketch of this signature and is here for
    one reason: the preview has to show the ``created_by`` it will stamp and the
    ``agent_key`` it will resolve, and the only way the dialog and the write can
    be guaranteed to agree on those is for both to come out of this call.

    Every refusal is collected rather than raised, so a reviewer sees all of
    them at once; :func:`apply` is the one that turns them into an error.
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

    agent_key, inherited = _resolve_agent_key(bundle, user_id)

    return ImportPlan(
        bundle=bundle,
        slug=slug,
        source_slug=bundle.agent.slug,
        name=bundle.agent.name,
        description=bundle.agent.description,
        when_to_consult=bundle.agent.when_to_consult,
        exported_at=bundle.exported_at,
        schema_version=bundle.schema_version,
        files=planned,
        executables=executables,
        total_bytes=sum(row.bytes for row in planned + executables),
        stamps={CREATED_BY_KEY: str(user_id), SERVER_NAME_KEY: ""},
        mutes=_mutes_for(sorted(seen)),
        agent_key=agent_key,
        agent_key_inherited=inherited,
        collision=collision,
        suggested_slug=suggested,
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
    result["next_step"] = (
        "Nothing is running. Read each imported routine and skill, then unmute "
        "the ones you want this agent to use."
    )
    return result


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
    return ""


def _collision(slug: str) -> tuple[str, str]:
    """``(taken slug, first free alternative)`` — or two empty strings.

    ``AgentStore.get`` resolves through **both** layers, so a shipped agent is
    refused as firmly as a local one. Import creates; it never replaces, because
    updating an agent from a bundle is a different verb with a diff-and-confirm
    problem of its own, and conflating the two is how a tuned local agent gets
    silently reset.
    """
    from condor.agents.agent import AgentStore

    if slug == CHAT_SLUG:
        return slug, _first_free(slug)
    store = AgentStore()
    if store.get(slug) is not None or agent_home(slug).exists():
        return slug, _first_free(slug)
    return "", ""


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
    """
    path = home / AGENT_MD
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta = meta if isinstance(meta, dict) else {}
    meta[CREATED_BY_KEY] = user_id
    meta[SERVER_NAME_KEY] = ""
    meta.pop(FORKED_FROM_KEY, None)
    meta.pop(FORKED_AT_KEY, None)
    meta["agent_key"] = import_plan.agent_key
    atomic_write_text(path, render_frontmatter(meta, body))
