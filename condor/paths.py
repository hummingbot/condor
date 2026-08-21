"""Where the runtime keeps what it knows. The only place a runtime path is built.

Three stores used to derive their root by walking up from ``agents/`` and back
down *into the Python package* -- ``<repo>/condor/.runtime/<name>`` -- written
out three times, in three modules, as a literal expression. That put this
deployment's data inside the code, and it meant a test run wrote into the
developer's live install because there was no single knob to turn.

Now there is one root and one knob:

    <repo>/.condor/                    # or $CONDOR_RUNTIME_ROOT
    ├── users/{user_id}/
    │   ├── conversations/{conv_id}/   # meta.json, transcript.jsonl, …
    │   └── delegations/{task_id}/     # status.json, transcript.md, events.json
    ├── state/{namespace}/
    └── telemetry/

**The user is the first path segment**, for both stores. A person's whole
footprint is one directory: it can be listed, tarred, handed to support or
deleted without a scan, and reading someone else's is not a permission check
that a route could forget to make -- it is a path the caller cannot name.

Two rules this module keeps:

* :func:`runtime_root` is a *function call at every use*, never a module
  constant. The env override has to be observable after import, or the test
  fixture that isolates the suite cannot work and the MCP/ACP subprocesses
  cannot inherit it.
* :func:`safe_id` is the single guard. Three near-identical regexes used to
  live in ``conversations.py``, ``state.py`` and ``delegation_history.py``;
  they collapse here. Same behaviour as before: refuse, never sanitize.

Policy stays with the module that owns it. ``state.py`` still decides that an
``{agent}.{strategy}`` namespace resolves under its strategy directory; only
the fallback root comes from here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

RUNTIME_ROOT_ENV = "CONDOR_RUNTIME_ROOT"

RUNTIME_DIRNAME = ".condor"

# <repo>: condor/paths.py -> condor/ -> <repo>
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The runtime store this one replaced. Only :mod:`condor.migrations` may read it.
LEGACY_RUNTIME_ROOT = _PROJECT_ROOT / "condor" / ".runtime"

USERS_DIRNAME = "users"
CONVERSATIONS_DIRNAME = "conversations"
DELEGATIONS_DIRNAME = "delegations"

# Every id here becomes a directory name, so none of them may escape one.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class UnsafeIdError(ValueError):
    """Raised for a value that could not be turned into one path segment."""


def safe_id(value: int | str) -> str:
    """The value as a path segment, or refuse.

    ``..`` is rejected wherever it appears rather than only as a whole segment:
    an id reaching here comes straight off a URL path in some callers, and there
    is no legitimate id with a double dot in it.
    """
    text = str(value)
    if not text or ".." in text or not _SAFE_ID.match(text):
        raise UnsafeIdError(
            f"Invalid runtime id {text!r}: "
            "use letters, digits, dot, dash or underscore."
        )
    return text


def runtime_root() -> Path:
    """Everything durable this deployment knows, in one directory.

    ``$CONDOR_RUNTIME_ROOT`` overrides it -- read on every call, so a test (or a
    second install sharing a checkout) can repoint the whole runtime at once.
    """
    override = os.environ.get(RUNTIME_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return _PROJECT_ROOT / RUNTIME_DIRNAME


def users_root() -> Path:
    return runtime_root() / USERS_DIRNAME


def user_dir(user_id: int | str) -> Path:
    return users_root() / safe_id(user_id)


def conversations_dir(user_id: int | str) -> Path:
    return user_dir(user_id) / CONVERSATIONS_DIRNAME


def conversation_dir(user_id: int | str, conv_id: str) -> Path:
    return conversations_dir(user_id) / safe_id(conv_id)


def delegations_dir(user_id: int | str) -> Path:
    return user_dir(user_id) / DELEGATIONS_DIRNAME


def delegation_dir(user_id: int | str, task_id: str) -> Path:
    return delegations_dir(user_id) / safe_id(task_id)


def state_dir(namespace: str) -> Path:
    """The fallback home for a state namespace (``state.py`` owns the policy)."""
    return runtime_root() / "state" / safe_id(namespace)


def telemetry_dir() -> Path:
    return runtime_root() / "telemetry"


def iter_user_ids() -> Iterator[str]:
    """Every user with a runtime footprint.

    The cross-user seam: every caller of this is by definition an admin path or
    the boot reconciler. Anything scoped to one person builds their directory
    instead of walking this.
    """
    base = users_root()
    try:
        children = sorted(base.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir():
            yield child.name
