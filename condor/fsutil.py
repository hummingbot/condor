"""One atomic file write for the whole codebase.

Seven independent copies of the "write a temp file, then ``os.replace`` it over
the target" dance used to live in ``condor/`` (memory store, canvas, routine
hooks, code runs, the report store twice, the runtime status registry and the
pickle persistence). They had already drifted apart in ways that mattered:
only one had ``fsync``, only one wrote to a *fixed* temp name (which defeats
the atomicity as soon as two writers race — CORR-032), two wrote in the locale
encoding instead of UTF-8, and two left the temp file visible in a directory
the app lists. ARCH-148 collapses them into this module.

The implementation here is the **safest superset** of what the copies did:

* **Unique temp file per writer.** ``tempfile.mkstemp`` opens it ``O_EXCL`` in
  the target's own directory, so concurrent writers never share — and thus
  never tear — the temp file, and ``os.replace`` stays a same-filesystem
  rename.
* **Hidden, target-derived temp name** (``.<target>.<random>.tmp``), so a
  half-written file is never picked up by a listing of the directory and is
  obvious to a human who finds one.
* **Durability.** The data is ``fsync``-ed before the rename and the parent
  directory is ``fsync``-ed after it, so a power loss leaves either the old
  file or the new one — never a zero-length survivor of the rename. Pass
  ``fsync=False`` on a writer hot enough that the barrier costs more than the
  data is worth; every current caller keeps it on (an agent tick, a report
  save and a config save are all far slower than the flush).
* **UTF-8 always**, so a file written on a machine with a non-UTF-8 locale is
  still readable on one that has it.
* **Mode preserved.** ``mkstemp`` creates ``0600``, which would silently
  tighten a file that already existed with looser permissions; the target's
  current mode is carried over when there is one, and new files stay ``0600``.
* **No temp file left behind**, on success (the rename consumes it) or on any
  failure, including ``KeyboardInterrupt``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["atomic_write_bytes", "atomic_write_text", "atomic_write_json"]

# Mode for a file this helper creates from scratch. Matches what
# ``tempfile.mkstemp`` produces, i.e. what most of the folded-in copies already
# left on disk: these are the application's own data files, not shared ones.
_NEW_FILE_MODE = 0o600


def atomic_write_bytes(
    path: str | Path, data: bytes, *, fsync: bool = True, mkdir: bool = True
) -> None:
    """Create-or-replace ``path`` with ``data``, atomically.

    A reader of ``path`` sees either the previous contents or the complete new
    ones, never a partial write, and a crash at any point leaves the previous
    file intact.
    """
    path = Path(path)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)

    mode = _target_mode(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(data)
            if fsync:
                handle.flush()
                os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        # Nothing published: drop the temp file rather than leave litter next
        # to the real one.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    if fsync:
        _fsync_dir(path.parent)


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
    mkdir: bool = True,
) -> None:
    """:func:`atomic_write_bytes` for text. UTF-8 unless told otherwise."""
    atomic_write_bytes(path, text.encode(encoding), fsync=fsync, mkdir=mkdir)


def atomic_write_json(
    path: str | Path,
    data: Any,
    *,
    fsync: bool = True,
    mkdir: bool = True,
    **json_kw: Any,
) -> None:
    """:func:`atomic_write_text` over ``json.dumps(data, **json_kw)``.

    Serialization happens *before* the temp file is created, so a value that
    cannot be encoded raises without having touched the filesystem at all.
    """
    atomic_write_text(path, json.dumps(data, **json_kw), fsync=fsync, mkdir=mkdir)


def _target_mode(path: Path) -> int:
    """Permissions to publish with: the target's own, or ``0600`` if new."""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return _NEW_FILE_MODE


def _fsync_dir(directory: Path) -> None:
    """Flush the rename itself. Best effort: not every filesystem allows it."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
