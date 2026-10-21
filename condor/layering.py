"""Copy-on-write between the shipped library and this install (FEAT-115).

:mod:`condor.memory.paths` says *where* the two roots are and in what order a
read consults them. This module is the other half: what happens the first time
somebody **writes** to something that only exists in the shipped one.

The answer is copy-on-write, and loud. The stock item is copied down into the
local root once, the copy is stamped with ``forked_from``/``forked_at``, and a
line naming both sides is logged. From then on the local copy shadows stock for
that *item* and nothing else — the install keeps receiving upstream's changes to
every other file of the same agent.

**Why fork here and refuse in ``skills.py``.** ``SkillStore._readonly_error``
deliberately refuses to fork a shared playbook for one agent rather than doing
it silently, and that reasoning is correct on *its* axis: within one install,
forking the shared playbook for one agent produces a divergence between two
agents on the same box that nobody would ever look for. Stock-versus-local is
the opposite case. The divergence is between this install and upstream, it is
the thing the operator is deliberately doing, and it is recorded on disk. The
two guards compose and the within-install rule is unchanged.

**Deleting a stock item is refused, not tombstoned.** No ``.deleted`` markers
and no shadow manifest: the answer already shipped as FEAT-090's mute set, which
removes a playbook or routine from the *run* rather than hiding it from a list,
and is reversible. :func:`stock_delete_error` is the refusal, and it names the
mute.

**And one way back.** Once every write is local, the maintainer editing an agent
through the product has no way to author the library at all — so
:func:`publish_to_stock` is not a nice-to-have, it is what keeps the library
authorable. It is the single sanctioned write into the shipped tree; the dirty
tree it leaves is *intended*, and the maintainer commits it.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from condor.frontmatter import parse_frontmatter, render_frontmatter
from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

FORKED_FROM_KEY = "forked_from"
FORKED_AT_KEY = "forked_at"
CREATED_BY_KEY = "created_by"


def content_digest(path: Path) -> str:
    """``sha256:<12>`` of a file's content, with line endings normalized.

    Twelve hex characters is the same order of collision resistance a git short
    hash gives, against a corpus of a few hundred markdown files. It identifies
    *which* upstream revision was forked; it is not a security claim.

    Newlines are normalized to LF before hashing, because the stamp has to mean
    the same thing on every platform. Hashing raw bytes made the digest depend
    on the checkout's line endings — the same file gave sha256:6d9b716cc24c on
    an LF checkout and sha256:ee60f3851972 on a CRLF one — so a ``forked_from``
    written on one host compared as *changed* on another, and every fork looked
    stale for a reason that had nothing to do with upstream. ``.gitattributes``
    now keeps the tracked tree LF; this makes the comparison correct even where
    it does not, such as a local root carried between machines.
    """
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    digest = hashlib.sha256(raw).hexdigest()
    return f"sha256:{digest[:12]}"


def _homes(agent_slug: str | None) -> tuple[Path, Path]:
    """``(local, stock)`` for an agent, imported at call time.

    ``condor.memory`` reaches back here (``SkillStore`` forks a stock playbook
    before it edits one), and its package ``__init__`` imports ``skills``, so a
    module-level import in either direction closes a cycle. This one is inside
    the call, which is also where the env overrides have to be read anyway.
    """
    from condor.memory.paths import agent_home_layers

    return agent_home_layers(agent_slug)


def stock_path(agent_slug: str | None, *rel: str) -> Path | None:
    """The stock copy of an item, or ``None`` when the library ships none."""
    candidate = _homes(agent_slug)[1].joinpath(*rel)
    return candidate if candidate.exists() else None


def local_path(agent_slug: str | None, *rel: str) -> Path:
    """Where a write of this item lands. Always local; may not exist yet."""
    return _homes(agent_slug)[0].joinpath(*rel)


def stock_only(local: Path, stock: Path) -> bool:
    """True when the item exists **only** in the shipped library.

    The question every destructive path asks before it acts: a delete that would
    remove a stock file is refused, one that would remove a local file is not.
    """
    return not local.exists() and stock.exists()


def resolves_to_stock(agent_slug: str | None, *rel: str) -> bool:
    """:func:`stock_only` for an item addressed by agent slug and relative path."""
    local, stock = _homes(agent_slug)
    return stock_only(local.joinpath(*rel), stock.joinpath(*rel))


def stock_delete_error(item: str, *, mute_kind: str = "") -> str:
    """The refusal message for deleting a shipped item, naming the mute.

    ``mute_kind`` is ``"skill"`` or ``"routine"`` where FEAT-090's switch
    applies; left empty the message still refuses but offers only the edit.
    """
    base = (
        f"'{item}' ships with Condor and cannot be deleted — an update would "
        "bring it straight back."
    )
    if mute_kind:
        return (
            f"{base} Mute it instead (manage_agents mute_{mute_kind}), which "
            "takes it out of the run and is reversible."
        )
    return f"{base} Edit it instead: your change is kept as a local fork."


def stamp_fork(path: Path, digest: str) -> bool:
    """Write ``forked_from``/``forked_at`` into a markdown file's frontmatter.

    Returns whether a stamp was written. Public because the boot migration
    hoists an install's already-modified files out of the tracked tree and has
    to record what they diverged from, without having a stock file to copy.
    """
    if path.suffix != ".md":
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    meta, body = parse_frontmatter(text)
    if not meta:
        return False
    meta[FORKED_FROM_KEY] = digest
    meta[FORKED_AT_KEY] = datetime.now(timezone.utc).isoformat()
    try:
        atomic_write_text(path, render_frontmatter(meta, body))
    except OSError:
        return False
    return True


def _stamp(path: Path, source: Path, digest: str) -> None:
    """Record the fork in a markdown file's frontmatter, if it has any.

    A file with no frontmatter is copied unstamped rather than being given one:
    inventing a header changes what the file *is*, and the two kinds that carry
    no frontmatter here (a routine's ``.py``, a bundled reference file) are read
    by machinery that would not survive it. That gap is real and recorded in the
    feature rather than papered over with a sidecar nobody would read.
    """
    if not stamp_fork(path, digest) and path.suffix == ".md":
        log.debug("No frontmatter to stamp on the fork of %s", source)


def fork_path(local: Path, stock: Path, label: str = "") -> Path:
    """The write target, copying ``stock`` down onto ``local`` first if needed.

    The primitive every write path goes through, agent-keyed or not (the shared
    libraries have no agent slug to hang off). Three outcomes, in order:

    - the item is already local — returned untouched, no copy, no stamp;
    - the item is stock only — copied down once (a file or a whole skill /
      strategy folder), stamped, logged, and the local path returned;
    - the item exists in neither — the local path is returned so the caller
      creates it, which is the ordinary "new thing" case.

    Idempotent by construction: the first branch is what a second call takes.
    """
    if local.exists() or not stock.exists():
        return local

    name = label or local.name
    try:
        local.parent.mkdir(parents=True, exist_ok=True)
        if stock.is_dir():
            shutil.copytree(stock, local)
            digest = ""
            for path in sorted(local.rglob("*.md")):
                source = stock / path.relative_to(local)
                if source.is_file():
                    digest = content_digest(source)
                    _stamp(path, source, digest)
        else:
            digest = content_digest(stock)
            shutil.copy2(stock, local)
            _stamp(local, stock, digest)
    except OSError:
        log.warning("Could not fork %s from stock", name, exc_info=True)
        return local

    log.info("agents: forked %s from stock %s", name, digest or "(dir)")
    return local


def fork_if_stock(agent_slug: str | None, *rel: str) -> Path:
    """The local path to write ``<agent>/<*rel>`` at, forking stock down first."""
    local, stock = _homes(agent_slug)
    return fork_path(
        local.joinpath(*rel),
        stock.joinpath(*rel),
        label=f"{agent_slug or ''}/{'/'.join(rel)}",
    )


def carry_fork_stamp(path: Path, meta: dict) -> dict:
    """Copy an existing fork stamp at ``path`` into ``meta``, in place.

    A store that rebuilds frontmatter from a dataclass would otherwise erase the
    stamp on the very next save, and the fork would become invisible one edit
    after it was made. Called by every writer that renders a fresh ``meta``
    rather than patching the one on disk.
    """
    if path.suffix != ".md" or not path.exists():
        return meta
    try:
        existing, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return meta
    for key in (FORKED_FROM_KEY, FORKED_AT_KEY):
        if isinstance(existing, dict) and key in existing:
            meta[key] = existing[key]
    return meta


@dataclass(frozen=True)
class StaleFork:
    """A forked item whose stock counterpart has moved, or gone, since the fork."""

    path: Path
    """The local file that is being used instead of stock."""
    rel: str
    """Its path relative to the local root — what a message should show."""
    forked_from: str
    """The digest of the stock file at fork time; ``""`` when never stamped."""
    stock_digest: str | None
    """The stock file's digest now; ``None`` when it no longer exists."""
    owner: str = ""
    """Which library it belongs to -- an agent slug, or ``_shared``."""

    @property
    def label(self) -> str:
        """What a message should show: the item qualified by its library.

        :attr:`rel` is relative to *one* library's root, so three agents with a
        customized playbook each render as a bare ``AGENT.md`` and the reader
        cannot tell which is which. Nine agents ship, so that is the ordinary
        case rather than an edge one.
        """
        return f"{self.owner}/{self.rel}" if self.owner else self.rel

    @property
    def retired(self) -> bool:
        """Whether upstream removed the stock counterpart entirely."""
        return self.stock_digest is None

    @property
    def unprovenanced(self) -> bool:
        """Shadowing stock with no record of ever having been the same file.

        Two ways to get here, and they need the same sentence. A file with no
        frontmatter cannot hold a stamp -- a routine's ``.py`` is the case that
        matters, since the library ships nineteen of them -- so a forked skill
        folder stamps its ``SKILL.md`` and nothing else. And a file the agent
        *created* under a name upstream later ships collides the same way: local
        wins per item, so upstream's version never appears.
        """
        return not self.forked_from


def _legacy_digest(path: Path) -> str:
    """What :func:`content_digest` returned before newlines were normalized.

    A stamp written from a CRLF working tree holds the raw-bytes hash, and after
    normalization it would compare as changed for a reason that has nothing to
    do with upstream. Checking the legacy form as well keeps those stamps
    meaningful instead of reporting every one of them stale once.
    """
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}"


def stale_forks(agent_slug: str | None) -> list[StaleFork]:
    """Forked items of one agent whose stock counterpart has since moved.

    The other half of copy-on-write, and the half that was missing.
    :func:`fork_path` records ``forked_from`` and nothing ever read it back, so
    a customized playbook kept shadowing stock for ever: the update succeeded,
    upstream's rewrite landed on disk, and the agent never saw it. Upstream
    churns exactly these files, and they carry trading rules and risk limits, so
    the divergence is both silent and consequential.

    This only *reports*. Local still wins — that is the deliberate promise of
    FEAT-115, and an update that quietly took upstream's version back would be
    the old loud failure wearing a new coat. What the operator gets is the
    sentence nobody was being told.

    Items with no stamp are skipped: a file the operator authored from scratch
    was never a fork of anything, so there is nothing for it to be stale
    against.
    """
    local, stock = _homes(agent_slug)
    # Read the owner off the resolved path rather than from ``agent_slug``,
    # which is None for the chat agent and would leave its items unqualified.
    return _compare_layer(local, stock, local.name)


def _compare_layer(
    local: Path, stock: Path, owner: str, rel_prefix: str = ""
) -> list[StaleFork]:
    """Every file under ``local`` measured against ``stock/<same relative path>``.

    Split out of :func:`stale_forks` because one layer does not resolve that
    way: the chat agent's routines fall back to the repo-root ``routines/``
    rather than to ``<stock>/condor/routines`` (see :func:`_chat_routine_forks`),
    so it needs the same comparison against a stock root of its own.

    ``rel_prefix`` keeps the reported path the one the reader would look for --
    a chat routine is reported as ``routines/<name>.py``, which is where it sits
    under the agent, not as the bare filename the scan happens to walk.
    """
    if not local.is_dir():
        return []

    out: list[StaleFork] = []
    for path in sorted(local.rglob("*")):
        if not path.is_file():
            continue

        rel = path.relative_to(local)
        counterpart = stock / rel
        shown = f"{rel_prefix}/{rel}" if rel_prefix else str(rel)
        forked_from = _stamp_of(path)

        if not counterpart.is_file():
            # No stock counterpart. Either upstream retired something this
            # install forked -- worth saying, since reads never reconcile the
            # roots and a withdrawn playbook keeps running -- or it is an
            # ordinary file the agent authored, which was never a fork of
            # anything and has nothing to be stale against.
            if forked_from:
                out.append(StaleFork(path, shown, forked_from, None, owner))
            continue

        now = content_digest(counterpart)
        if forked_from:
            if now == forked_from or _legacy_digest(counterpart) == forked_from:
                continue
            out.append(StaleFork(path, shown, forked_from, now, owner))
            continue

        # No stamp, but stock ships this exact path, so the local copy is
        # shadowing it. Identical content means the fork simply never diverged
        # -- nothing to report. Different content means upstream's version is
        # unreachable and nothing recorded that it ever matched.
        if now != content_digest(path):
            out.append(StaleFork(path, shown, "", now, owner))
    return out


def _stamp_of(path: Path) -> str:
    """The ``forked_from`` digest recorded on ``path``, or ``""``.

    Only markdown can carry one: :func:`_stamp` deliberately refuses to invent
    frontmatter for a file that has none, because that would change what the
    file *is* and the machinery reading a routine's ``.py`` would not survive it.
    """
    if path.suffix != ".md":
        return ""
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""
    return str((meta or {}).get(FORKED_FROM_KEY) or "")


def locally_overridden(rel_paths: list[str]) -> list[str]:
    """Which of ``rel_paths`` this install already has its own version of.

    ``rel_paths`` are repo-relative, as ``incoming_paths`` reports them, so only
    those under ``agents/`` can have a local counterpart at all.

    This is the *predictive* half of the staleness story. :func:`stale_forks`
    compares a fork against the stock file on disk, and that only moves once the
    fast-forward has landed -- so it answers "upstream has already moved on"
    after the fact. This answers "the update you are about to apply will change
    files you have overridden" while there is still a decision to make.
    """
    from condor.paths import local_agents_root

    root = local_agents_root()
    if not root.is_dir():
        return []

    out: list[str] = []
    for rel in rel_paths:
        parts = PurePosixPath(rel).parts
        if len(parts) < 2:
            continue
        if parts[0] == "agents":
            local = root.joinpath(*parts[1:])
        elif parts[0] == "routines":
            # The chat agent's library is the exception: it is shipped at the
            # repo root, not under ``agents/``, and its local layer sits at
            # ``<local>/condor/routines`` (see ``_chat_routine_forks``). Without
            # this arm an update that rewrites a shipped routine the chat has
            # improved matched nothing, and the one warning that fires before
            # the change lands never mentioned it.
            local = root.joinpath(CHAT_LIBRARY, *parts)
        else:
            continue
        if local.is_file():
            out.append(rel)
    return out


#: The library every agent reads *under* its own — published skills, routines
#: and controllers. Not an agent, so ``iter_agent_slugs`` never yields it:
#: ``_is_agent_dir`` rejects a leading underscore, which is what kept three
#: whole trees out of the scan below.
SHARED_LIBRARY = "_shared"

#: The chat agent's own library. Named here because its routines are the one
#: layer that falls back to the repo root rather than to the shipped agent tree.
CHAT_LIBRARY = "condor"


def _chat_routine_forks() -> list[StaleFork]:
    """Chat-authored routines shadowing the library they were copied from.

    The one layer whose stock root is not ``<stock>/<slug>/``. FEAT-033 keeps
    the shipped library at the repo-root ``routines/`` and FEAT-115 moved only
    the *write* target under the agent, so the pair is
    ``(<local>/condor/routines, <repo>/routines)`` -- which is what
    :func:`routines.base.assistant_routines_dirs` resolves and what the loader
    actually reads.

    :func:`stale_forks` cannot see it. It asks ``_homes`` for the pair, gets
    ``<stock>/condor/routines`` as the stock side, finds nothing there on any
    install, and files every chat routine under "an ordinary file the agent
    authored" -- so improving a shipped routine, which is the whole point of
    the local layer, shadowed it in silence. Ask the loader where stock is
    instead.
    """
    from routines.base import assistant_routines_dirs

    local, stock = assistant_routines_dirs(None)
    return _compare_layer(local, stock, CHAT_LIBRARY, rel_prefix="routines")


def all_stale_forks() -> list[StaleFork]:
    """:func:`stale_forks` across every agent this install has, plus the shared root."""
    from condor.memory.paths import iter_agent_slugs

    out: list[StaleFork] = []
    seen: set[Path] = set()
    # The chat routines first: the per-agent scan below walks the same files
    # under ``<local>/condor`` and would judge them against the wrong root.
    for fork in [*_chat_routine_forks()]:
        seen.add(fork.path)
        out.append(fork)
    for slug in [None, SHARED_LIBRARY, *iter_agent_slugs()]:
        for fork in stale_forks(slug):
            if fork.path in seen:
                continue
            seen.add(fork.path)
            out.append(fork)
    return out


def write_preserving_stamp(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` without destroying its fork stamp.

    The three *stores* already do this by hand, rendering their frontmatter
    through :func:`carry_fork_stamp`. The web routes did not: they took a body
    the browser had round-tripped and wrote it straight down, and because the
    GET that produced it had read the **stock** file — which carries no stamp —
    the fork's stamp was written by :func:`fork_path` and erased by the very
    next line. Editing an agent through the dashboard is the primary way anyone
    edits these files, so in practice the stamp almost never survived.

    Factored out rather than inlined at the two call sites so a third write path
    cannot reintroduce the same bug by forgetting the dance.
    """
    if path.suffix != ".md":
        atomic_write_text(path, content)
        return
    meta, body = parse_frontmatter(content)
    atomic_write_text(
        path, render_frontmatter(carry_fork_stamp(path, dict(meta or {})), body)
    )


def scrub_for_stock(path: Path) -> None:
    """Drop this install's local facts from a file on its way into stock.

    Two of them, for one reason: a shipped file must not assert something that
    is only true on the install that published it.

    * ``forked_from``/``forked_at`` — a stock file that still claims to be a
      fork of a stock file would make every install that pulls it look forked
      from itself.
    * ``created_by`` — the publisher's real Telegram id, which names nobody on
      another box, yet the admin-principal rule and the legacy-restart owner
      fallback both still act on it ([[CORR-705]]). The stores keep recording
      the real creator in the local layer on purpose; this is the one boundary
      it must not cross ([[CORR-721]]).
    """
    if path.suffix != ".md":
        return
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return
    if not meta:
        return
    forked = FORKED_FROM_KEY in meta or FORKED_AT_KEY in meta
    claims_creator = meta.get(CREATED_BY_KEY, 0) not in (0, None)
    if not (forked or claims_creator):
        return
    meta.pop(FORKED_FROM_KEY, None)
    meta.pop(FORKED_AT_KEY, None)
    if claims_creator:
        meta[CREATED_BY_KEY] = 0
    try:
        atomic_write_text(path, render_frontmatter(meta, body))
    except OSError:
        log.warning("Could not scrub %s for publishing", path, exc_info=True)


# ── Publishing: the one sanctioned write into the shipped library ──

# Runtime output, by name at any depth. The half of the publishing contract that
# names what this install accumulated: a whole-directory copy would drag
# ``store/``, ``sessions/`` and the mute set into the tracked tree, which is the
# exact wound this feature closed. The other half is :func:`_root_library_dirs`,
# which decides the root positively, so a name nobody listed here is still not
# library content when it sits in a folder the layout never defined.
_RUNTIME_NAMES = frozenset(
    {
        "store",
        "proposals",
        "mutes.yml",
        "delegations",
        "sessions",
        "dry_runs",
        "learnings.md",
        "state.json",
        "config.yml",
        "owned_bots.json",
        "disowned_bots.json",
        # A controller's server copies kept by an overwriting sync (FEAT-126).
        # The dot only hides the *directory*; the files in it are plain names,
        # which the dotfile check below would let through.
        ".server_backups",
        # Compiled bytecode of an agent's own routines. Matched at any depth
        # because it appears *under* an allowed directory
        # (``routines/__pycache__/``), and the name is reserved by Python, so no
        # library item can legitimately claim it ([[CORR-720]]).
        "__pycache__",
    }
)


def _root_library_dirs() -> frozenset[str]:
    """The root directories the agent layout defines — nothing else is library.

    The other half of the contract above, and the half that makes it true
    rather than aspirational: ``_RUNTIME_NAMES`` can only name runtime
    output somebody already thought of, and an agent that invents a root folder
    — a routine keeping its own ``state/``, say — invents a name no denylist
    holds ([[CORR-720]]). At the root the layout *is* a closed set, so it is
    stated as one, each entry named by the module that owns it.

    Imported inside the function because :mod:`condor.agents.strategy` imports
    this module, so naming ``LOOPS_DIRNAME`` at module scope would be a cycle.
    """
    from condor.agent_controllers import CONTROLLERS_DIRNAME
    from condor.agents.strategy import LOOPS_DIRNAME
    from condor.memory.paths import ROUTINES_DIRNAME, SKILLS_DIRNAME

    return frozenset(
        {CONTROLLERS_DIRNAME, LOOPS_DIRNAME, ROUTINES_DIRNAME, SKILLS_DIRNAME}
    )


def _is_library_path(rel: Path, root_dirs: frozenset[str]) -> bool:
    """The layout rule of :func:`publishable_files`, in one place.

    Written once because it was once written twice: the whole-agent walk
    filtered and the named-file branch returned the path unread, so
    ``publishable_files(home, "state/op.json")`` handed back the one file the
    bare call refused ([[CORR-734]]). Both branches call this now, so naming a
    path narrows *which* files are considered and never *whether* they are
    filtered.

    ``root_dirs`` is passed in rather than looked up here so
    :func:`_root_library_dirs` is resolved once per selection and not once per
    file ([[CORR-720]]).
    """
    if rel.name.startswith("."):
        return False
    if any(part in _RUNTIME_NAMES for part in rel.parts):
        return False
    if len(rel.parts) > 1 and rel.parts[0] not in root_dirs:
        return False
    return True


def is_library_path(rel: str) -> bool:
    """Whether ``rel``, relative to an agent home, is library content.

    The path rule of :func:`publishable_files` as a predicate, so a door that
    takes paths from *outside* this install — :mod:`condor.agents.bundle`'s
    import — can refuse exactly what the export skips instead of keeping a
    second copy of the same list to drift from. That matters beyond tidiness:
    a bundle is hand-editable JSON, so a crafted one could otherwise write
    ``loops/<s>/sessions/session_1/status.json`` and have boot reconciliation
    read it as a crashed session and start the loop, with no operator ever
    pressing Start.

    Both rules of the export apply, plus one the export gets from the
    filesystem rather than from a name: a dotted *part* at any depth is
    refused here, where ``publishable_files`` only skips dotted file names
    (a dotted directory it meets is runtime output named in
    ``_RUNTIME_NAMES``). Nothing an export produces is rejected by that, so
    the round trip is unaffected.
    """
    parts = tuple(part for part in str(rel).split("/") if part)
    if not parts:
        return False
    if any(part.startswith(".") for part in parts):
        return False
    return _is_library_path(Path(*parts), _root_library_dirs())


def publishable_files(home: Path, path: str = "") -> list[Path]:
    """Every library file under ``home``, relative to it — runtime output skipped.

    ``path`` narrows to one file or folder (``skills/recon``,
    ``loops/grid``); empty takes the whole agent. It narrows which files are
    considered and never whether they are filtered — :func:`_is_library_path`
    judges a named file exactly as it judges one the walk found, so naming
    either a file or a folder cannot smuggle runtime output out.

    Two rules, because the layout is only closed at one level. A root
    *directory* must be one the layout defines (:func:`_root_library_dirs`); a
    root *file*, and any name at any depth, must not be runtime output
    (``_RUNTIME_NAMES``). Matching ``state`` at the root rather than by name is
    what keeps a ``skills/state/`` playbook publishable while an agent's root
    ``state/`` folder is not.
    """
    rel_parts = tuple(part for part in path.split("/") if part and part != "..")
    base = home.joinpath(*rel_parts)
    root_dirs = _root_library_dirs()
    if base.is_file():
        rel = Path(*rel_parts)
        return [rel] if _is_library_path(rel, root_dirs) else []
    if not base.is_dir():
        return []

    found: list[Path] = []
    for candidate in sorted(base.rglob("*")):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(home)
        if _is_library_path(rel, root_dirs):
            found.append(rel)
    return found


def publish_to_stock(agent_slug: str, path: str = "") -> dict:
    """Copy this install's agent (or one item of it) into the shipped library.

    The inverse of :func:`fork_if_stock`, and the only write into the tracked
    tree in the product. Every copy is run through :func:`scrub_for_stock` on
    the way in, which drops the facts that are true only of this install — the
    fork stamp and the publisher's ``created_by``.

    Returns the paths to commit, relative to the repo, so the maintainer can see
    what their tree now holds. Overwriting is the point here (publishing an
    updated playbook is the normal case), which is exactly why this is
    admin-only and why the caller is told what changed.
    """
    local_home, stock_home = _homes(agent_slug)
    if not local_home.is_dir():
        return {"error": f"'{agent_slug}' has nothing local to publish"}

    files = publishable_files(local_home, path)
    if not files:
        target = f"{agent_slug}/{path}" if path else agent_slug
        return {"error": f"Nothing publishable at '{target}'"}

    published: list[str] = []
    for rel in files:
        destination = stock_home / rel
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_home / rel, destination)
            scrub_for_stock(destination)
        except OSError:
            log.warning("Could not publish %s/%s", agent_slug, rel, exc_info=True)
            continue
        published.append(_repo_relative(destination))

    log.info(
        "agents: published %d file(s) of %s into stock", len(published), agent_slug
    )
    return {
        "published": True,
        "agent_slug": agent_slug,
        "paths": published,
        "note": (
            "These are now dirty in the repo, which is intended — review and "
            "commit them. Nothing else in the product writes here."
        ),
    }


def _repo_relative(path: Path) -> str:
    """``agents/<slug>/...`` when the shipped root sits in a checkout."""
    from condor.paths import stock_agents_root

    try:
        return str(path.relative_to(stock_agents_root().parent))
    except ValueError:
        return str(path)
