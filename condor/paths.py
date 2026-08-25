"""Where this deployment keeps what it knows. The only place such a path is built.

Three stores used to derive their root by walking up from ``agents/`` and back
down *into the Python package* -- ``<repo>/condor/.runtime/<name>`` -- written
out three times, in three modules, as a literal expression. That put this
deployment's data inside the code, and it meant a test run wrote into the
developer's live install because there was no single knob to turn.

There are **two** roots, not one, and this module is where both are named. They
are separate on purpose (``.gitignore`` has always listed them apart) and the
line between them is *who writes and when*:

    <repo>/.condor/                    # or $CONDOR_RUNTIME_ROOT
    ├── users/{user_id}/               # the runtime store: one conversation's
    │   ├── conversations/{conv_id}/   # worth of thinking, written turn by turn
    │   └── delegations/{task_id}/     # by a live session
    ├── state/{namespace}/
    └── telemetry/

    <repo>/data/                       # or $CONDOR_DATA_DIR
    ├── condor_bot_data.pickle         # the operational store: what the *bot*
    ├── notifications.json             # accumulated while it ran, readable and
    ├── routine_hooks.json             # editable by an operator
    ├── backtests/
    ├── code_runs/
    └── notes/, memory/

``data/`` is the older of the two and is named literally in agent-facing text
(``data/code_runs/`` in the ``run_code`` tool description), so folding it into
``.condor/`` would open a new split rather than close one. What it did lack was
a resolver: three of its stores built their path at import from the bare name
``data``, making it relative to the *working directory*, while ``main.py``'s
pickle and ``code_runs`` were already anchored at the repo. Both now come from
:func:`data_dir`.

**The user is the first path segment** of the runtime store. A person's whole
footprint is one directory: it can be listed, tarred, handed to support or
deleted without a scan, and reading someone else's is not a permission check
that a route could forget to make -- it is a path the caller cannot name.

Two rules this module keeps:

* :func:`runtime_root` and :func:`data_dir` are a *function call at every use*,
  never a module constant. The env override has to be observable after import,
  or the test fixture that isolates the suite cannot work and the MCP/ACP
  subprocesses cannot inherit it.
* :func:`safe_id` is the single guard. Three near-identical regexes used to
  live in ``conversations.py``, ``state.py`` and ``delegation_history.py``;
  they collapse here. Same behaviour as before: refuse, never sanitize.

**No data moved when the resolver arrived, and none needs to.** Every in-tree
launch path already runs with the repo as the working directory -- ``make``
uses ``tmux new-session -c $(CURDIR)``, agent subprocesses are spawned with
``cwd=get_project_dir()``, the updater chdirs to ``$CONDOR_DIR`` -- so for a
real install the old cwd-relative name and :func:`data_dir` are the same
directory, and an upgrade sees its whole history exactly where it left it. The
one install that would notice is one started by hand from some *other*
directory, which had therefore been splitting its own state all along (pickle
and code runs at the repo, bell and hooks at the cwd); its stray ``./data`` is
**not** migrated and **not** read as a fallback -- a cwd-relative fallback is
the very thing being removed. Point ``$CONDOR_DATA_DIR`` at it, or move those
files by hand.

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
DATA_DIR_ENV = "CONDOR_DATA_DIR"

RUNTIME_DIRNAME = ".condor"
DATA_DIRNAME = "data"

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


def data_dir() -> Path:
    """The operational store: what the bot accumulated while it ran.

    Anchored at the repo, never at the working directory -- the main process
    writes these files and every MCP/ACP subprocess reads them, and they need
    not agree on a cwd. ``$CONDOR_DATA_DIR`` overrides it, read on every call
    for the same reason :func:`runtime_root` reads its own.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return _PROJECT_ROOT / DATA_DIRNAME


def notifications_path() -> Path:
    """The per-user notification bell's store (``notifications.py``)."""
    return data_dir() / "notifications.json"


def routine_hooks_path() -> Path:
    """Per-routine, per-owner hook configuration (``routine_hooks.py``)."""
    return data_dir() / "routine_hooks.json"


def backtests_dir() -> Path:
    """One JSON file per backtest, plus ``_index.json`` (``backtest_store.py``)."""
    return data_dir() / "backtests"


def legacy_backtests_file() -> Path:
    """The single-file backtest store ``BacktestStore`` folds in and deletes."""
    return data_dir() / "backtests.json"


def code_runs_dir() -> Path:
    """One JSON file per ad-hoc code run (``code_runs.py``).

    Named literally in the ``run_code`` tool description an agent reads, so the
    default location is part of that contract.
    """
    return data_dir() / "code_runs"


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
