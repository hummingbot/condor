"""Reading a Botcamp agent zip into a Condor :class:`~condor.agents.bundle.Bundle`.

Botcamp (``hummingbot/botcamp-site`` PR #111) stores and serves a cloned
agent's code as a zip of its library files — the same ``AGENT.md``/
``loops/``/``skills/``/``controllers/``/``routines/``/``conf/`` layout
:func:`condor.agents.bundle.build` reads off disk — never Condor's own
single-document ``.condor-agent.json``. ``condor/agents/bundle.py``'s own
module docstring answers "why JSON and not an archive" for Condor's *own*
export, and that answer is untouched: this module does not make Condor
write zips, it only reads somebody else's, because that is the wire shape
the hub actually picked (``src/app/api/agents/[slug]/versions/[version]/
route.ts`` in PR #111).

One layout difference is converted on the way in: a Hummingbot V2 controller
at ``controllers/<generic|market_making|directional_trading>/<name>.py``
lands under ``controllers/<name>/<name>.py``, because that is the only
shape Condor's own controller loader looks for — ``src/lib/agent-
package.ts``'s own comment says as much: "Condor only lists
controllers/<name>/<name>.py". A race entry already in Condor's own layout
travels unchanged.

Every defense here runs independently of whatever the hub already checked
at upload (``agent-package.ts``'s ``validateAgentPackage``, which caps file
count, per-file size, total size, rejects symlinks and unsafe paths before
ever storing the zip). ``condor/hub.py``'s trust model — "a bundle is
untrusted code wherever it came from, and the gate is on the receiving
install" — draws no exception for bytes a service already claims to have
validated, because the one byte stream this install actually reads is the
one that arrived in this process, not the one that left Botcamp's.
"""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile

from condor.agents.bundle import (
    AGENT_MD,
    AGENT_UID_KEY,
    KIND,
    MAX_BUNDLE_BYTES,
    SCHEMA,
    Bundle,
    BundleAgent,
    BundleError,
    BundleFile,
    NotJsonError,
    _digest,
    _frontmatter,
    _refuse_path,
    requires_of,
)

#: Mirrors the hub's own per-package limits (``agent-package.ts``'s
#: ``AGENT_PACKAGE_LIMITS``), so a decompression bomb is refused on this
#: side regardless of what the hub already enforced at upload.
MAX_FILES = 300
MAX_FILE_BYTES = 1_048_576

#: Hummingbot V2's controller-type directories (``agent-package.ts``'s
#: ``CONTROLLER_TYPES``). A ``.py`` one level under one of these is the
#: layout a race entry used; Condor's own loader only looks under
#: ``controllers/<name>/<name>.py``.
_HB_CONTROLLER_TYPES = frozenset({"generic", "market_making", "directional_trading"})


def _hub_agent_uid(agent_slug: str) -> str:
    """A stable ``agent_uid`` for a Botcamp agent that stamps none of its own.

    A pre-FEAT-143 zip, or one from a hub revision that never carried
    ``agent_uid`` in ``AGENT.md``, must still resolve to the *same* agent on
    every revision pulled — that is the whole point of the field
    (:func:`condor.agents.bundle._minted_uid`'s own docstring). Content hashing
    cannot give that here the way it can for a local export: Botcamp serves a
    new revision specifically *because* files changed, so hashing the zip's
    own content would mint a new identity every time anything in it changed,
    and a pull of the next revision would read as a different agent occupying
    the installed slug rather than an update of it.

    Keyed on :data:`condor.hub.HUB_URL` and the hub's own catalogue slug
    instead — both stable across revisions, and together they are exactly
    what "the same Botcamp agent" means to this install.
    """
    from condor.hub import HUB_URL

    return hashlib.sha256(
        f"condor-botcamp-agent-uid\0{HUB_URL}\0{agent_slug}".encode("utf-8")
    ).hexdigest()[:32]


def _condor_controller_path(path: str) -> str:
    """``path``, rewritten off the Hummingbot V2 controller layout if it is one.

    ``controllers/<type>/<name>.py`` becomes ``controllers/<name>/<name>.py``
    for exactly the three Hummingbot V2 type directories; everything else —
    including Condor's own ``controllers/<name>/<name>.py`` and a loose file
    neither layout recognises — travels unchanged, the same "ship it either
    way" stance the hub's own check takes (it only warns about a loose one).
    """
    parts = path.split("/")
    if (
        len(parts) == 3
        and parts[0] == "controllers"
        and parts[1] in _HB_CONTROLLER_TYPES
        and parts[2].endswith(".py")
        and parts[2] != "__init__.py"
    ):
        name = parts[2][: -len(".py")]
        return f"controllers/{name}/{name}.py"
    return path


def from_zip(data: bytes, *, agent_slug: str = "") -> Bundle:
    """A :class:`Bundle` built from a Botcamp agent zip's bytes.

    Raises :class:`~condor.agents.bundle.NotJsonError` for bytes that are not
    a zip at all — named for the sibling refusal
    :func:`condor.agents.bundle.parse` raises for its own wire, so a caller
    catching :class:`~condor.agents.bundle.BundleError` catches this too — and
    :class:`~condor.agents.bundle.BundleError` for everything this install
    refuses about the archive's content: too many members, a member over the
    per-file cap, the running total over
    :data:`condor.agents.bundle.MAX_BUNDLE_BYTES`, a symlink, a path
    :func:`condor.agents.bundle._refuse_path` would refuse at ``plan`` time
    (checked here too, so a traversal member is refused before it is even
    decoded), bytes that are not UTF-8, or no ``AGENT.md`` at all.

    Declared sizes are checked **before** a member is read, which is what
    makes the per-file and running-total caps a real bomb defense rather than
    decoration: a crafted entry that lies about being small and then
    decompresses huge still fails ``zipfile``'s own CRC check on read, and
    one that honestly declares itself huge is refused without ever being
    decompressed.

    ``agent_slug`` becomes the bundle's ``agent.slug`` — the hub's own
    catalogue slug, since nothing in the zip states one (a local export's
    ``BundleAgent.slug`` is the exporting install's own directory name, which
    a zip of files has no equivalent of). :func:`condor.agents.bundle.plan`
    already prefers its own ``as_slug`` argument over this field, so a caller
    that wants a different name still gets it.

    When the zip's own ``AGENT.md`` carries no ``agent_uid`` (a revision from
    before the hub started stamping one), the identity is :func:`_hub_agent_uid`
    rather than left for :func:`condor.agents.bundle._minted_uid` to derive
    from content — see that function's docstring for why a content hash would
    make the next revision of the same agent look like a different one.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise NotJsonError(f"This is not a zip archive ({exc})") from exc

    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_FILES:
        raise BundleError(
            f"This archive carries {len(members)} files, over the "
            f"{MAX_FILES}-file limit"
        )

    files: list[BundleFile] = []
    total = 0
    for info in members:
        path = _condor_controller_path(info.filename)
        if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            raise BundleError(f"'{path}' is a symlink, which a bundle may not carry")
        refusal = _refuse_path(path)
        if refusal:
            raise BundleError(refusal)
        if info.file_size > MAX_FILE_BYTES:
            raise BundleError(
                f"'{path}' is {info.file_size} bytes, over the "
                f"{MAX_FILE_BYTES}-byte per-file limit"
            )
        total += info.file_size
        if total > MAX_BUNDLE_BYTES:
            raise BundleError(
                f"This archive is over the {MAX_BUNDLE_BYTES}-byte bundle limit"
            )
        try:
            raw = archive.read(info)
        except (zipfile.BadZipFile, EOFError) as exc:
            raise BundleError(f"'{path}' is corrupt in this archive ({exc})") from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleError(f"'{path}' is not UTF-8 text ({exc})") from exc
        files.append(BundleFile(path=path, sha256=_digest(text), text=text))

    paths = {entry.path for entry in files}
    if AGENT_MD not in paths:
        raise BundleError(f"A bundle without {AGENT_MD} is not an agent")

    meta = _frontmatter(next(entry.text for entry in files if entry.path == AGENT_MD))
    agent_uid = str(meta.get(AGENT_UID_KEY) or "")
    if not agent_uid and agent_slug:
        # No stamp in this revision's AGENT.md: mint off the hub + slug, not
        # the content (BUG from Greptile's review — see _hub_agent_uid), so
        # the next revision of this same agent is still recognised as one.
        agent_uid = _hub_agent_uid(agent_slug)
    return Bundle(
        schema=SCHEMA,
        kind=KIND,
        version=1,
        agent=BundleAgent(
            slug=agent_slug,
            name=str(meta.get("name") or ""),
            description=str(meta.get("description") or ""),
            when_to_consult=str(meta.get("when_to_consult") or ""),
            agent_uid=agent_uid,
        ),
        requires=requires_of(files),
        files=files,
    )
