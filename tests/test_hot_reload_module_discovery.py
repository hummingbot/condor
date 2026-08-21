"""The hot-reload list is derived, so it cannot drift out of sync with the tree.

The list this replaced was hand-maintained, and it had drifted both ways: it
named three ``handlers.dex.swap_*`` modules that do not exist and omitted six
that do, including ``handlers.dex.router``. Both failures are silent —
``reload_handlers`` only touches names already in ``sys.modules``, so a stale
entry is a no-op and a missing entry means the watcher logs a successful reload
while the running bot goes on executing the old code.

These pin the two properties the derivation has to keep: it covers every module
under ``handlers/``, and it orders children before parents.
"""

import importlib.util
from pathlib import Path

import main

HANDLERS_ROOT = Path(main.__file__).parent / "handlers"


def test_every_handler_module_on_disk_is_discovered():
    """No module under handlers/ is left out of the reload list."""
    discovered = set(main._discover_handler_modules())

    on_disk = set()
    for path in HANDLERS_ROOT.rglob("*.py"):
        parts = path.relative_to(HANDLERS_ROOT.parent).with_suffix("").parts
        if "__pycache__" in parts or "store" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            on_disk.add(".".join(parts))

    assert on_disk, "found no handler modules — the walk is broken"
    assert not on_disk - discovered, f"never reloaded: {sorted(on_disk - discovered)}"


def test_the_dex_modules_the_old_list_missed_are_covered():
    """Regression: these six exist and were absent from the hand-written list."""
    discovered = set(main._discover_handler_modules())
    for name in (
        "handlers.dex.router",
        "handlers.dex.swap",
        "handlers.dex.liquidity",
        "handlers.dex.lp_monitor_handlers",
        "handlers.dex.geckoterminal",
        "handlers.dex.visualizations",
    ):
        assert name in discovered, f"{name} would not hot-reload"


def test_no_module_in_the_reload_set_is_a_dead_name():
    """Every name resolves to something importable.

    The old list carried handlers.dex.swap_quote / swap_execute / swap_history,
    none of which exist. A dead name never raises — it is simply skipped.
    """
    names = [*main._EXTRA_RELOAD_MODULES, *main._discover_handler_modules()]
    dead = [n for n in names if importlib.util.find_spec(n) is None]
    assert not dead, f"names that resolve to nothing: {dead}"


def test_children_reload_before_their_parents():
    """A package's __init__ re-binds from cache, so submodules must go first.

    ``importlib.reload`` is not recursive: reloading ``handlers.dex``
    re-executes ``from .router import ...`` against whatever
    ``handlers.dex.router`` is already in ``sys.modules``. Unless the submodule
    was refreshed first, the package re-binds the stale object and the edit is
    lost.
    """
    order = main._discover_handler_modules()
    position = {name: i for i, name in enumerate(order)}

    for name, index in position.items():
        parent = name.rpartition(".")[0]
        while parent:
            if parent in position:
                assert index < position[parent], (
                    f"{name} reloads after its parent {parent}; "
                    "the parent would re-bind the stale submodule"
                )
            parent = parent.rpartition(".")[0]


def test_the_runtime_is_never_reloaded():
    """condor.runtime holds live agent subprocess handles.

    Re-executing those modules resets the registry and silently orphans every
    running agent, so the derivation must not be able to reach them.
    """
    names = [*main._EXTRA_RELOAD_MODULES, *main._discover_handler_modules()]
    assert not [n for n in names if n.startswith("condor.runtime")]
