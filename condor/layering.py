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
from datetime import datetime, timezone
from pathlib import Path

from condor.frontmatter import parse_frontmatter, render_frontmatter
from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

FORKED_FROM_KEY = "forked_from"
FORKED_AT_KEY = "forked_at"


def content_digest(path: Path) -> str:
    """``sha256:<12>`` of a file's bytes — short enough to read in a log line.

    Twelve hex characters is the same order of collision resistance a git short
    hash gives, against a corpus of a few hundred markdown files. It identifies
    *which* upstream revision was forked; it is not a security claim.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
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


def clear_fork_stamp(path: Path) -> None:
    """Drop ``forked_from``/``forked_at`` — used when publishing back into stock.

    A stock file that still claims to be a fork of a stock file would make every
    install that pulls it look forked from itself.
    """
    if path.suffix != ".md":
        return
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return
    if not meta or not (FORKED_FROM_KEY in meta or FORKED_AT_KEY in meta):
        return
    meta.pop(FORKED_FROM_KEY, None)
    meta.pop(FORKED_AT_KEY, None)
    try:
        atomic_write_text(path, render_frontmatter(meta, body))
    except OSError:
        log.warning("Could not clear the fork stamp on %s", path, exc_info=True)


# ── Publishing: the one sanctioned write into the shipped library ──

# What a published agent consists of. An allowlist rather than a denylist, and
# deliberately: a whole-directory copy would drag ``store/``, ``sessions/`` and
# the mute set into the tracked tree, which is the exact wound this feature
# closed. Anything not named here is this install's, not the library's.
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
    }
)


def publishable_files(home: Path, path: str = "") -> list[Path]:
    """Every library file under ``home``, relative to it — runtime output skipped.

    ``path`` narrows to one file or folder (``skills/recon``,
    ``strategies/grid``); empty takes the whole agent. The runtime filter applies
    either way, so naming a folder cannot smuggle a store out.
    """
    rel_parts = tuple(part for part in path.split("/") if part and part != "..")
    base = home.joinpath(*rel_parts)
    if base.is_file():
        return [Path(*rel_parts)]
    if not base.is_dir():
        return []

    found: list[Path] = []
    for candidate in sorted(base.rglob("*")):
        if not candidate.is_file() or candidate.name.startswith("."):
            continue
        rel = candidate.relative_to(home)
        if any(part in _RUNTIME_NAMES for part in rel.parts):
            continue
        found.append(rel)
    return found


def publish_to_stock(agent_slug: str, path: str = "") -> dict:
    """Copy this install's agent (or one item of it) into the shipped library.

    The inverse of :func:`fork_if_stock`, and the only write into the tracked
    tree in the product. The fork stamp is cleared on the way in — a shipped
    file that still claimed to be forked from a shipped file would make every
    install that pulls it look forked from itself.

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
            clear_fork_stamp(destination)
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
