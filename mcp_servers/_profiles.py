"""The profile machinery both MCP servers share (FEAT-066, FEAT-091, ARCH-289).

Each ``server.py`` owns its own rings — the name tables in its ``profiles.py``,
its ``DEFAULT_TOOL_PROFILE``, its module namespace — but the *mechanics* around
them were byte-for-byte identical in both: resolve every name in the table back
to a function at import, refuse an unknown profile, subtract the operator's
mutes, mount the rest. Those are the feature's security invariants (an unknown
profile raises rather than widening to ``full``, a mute only ever subtracts, a
renamed tool fails the import), and they belong in one place: a new rule added
here — say, refusing a mute that would empty a profile — lands on both seats at
once instead of drifting between two copies.

A leaf module on purpose. It must import neither ``server.py`` (importing one
parses argv and builds a ``FastMCP`` singleton as a side effect) nor anything
that builds one — ``mcp_servers/hummingbot_api/__init__.py`` lazy-loads ``main``
precisely so that the tables stay reachable without waking a server. Hence the
``FastMCP`` annotation below is a type-checking import only.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - annotation only, never imported at runtime
    from mcp.server.fastmcp import FastMCP


def parse_profile_flags(default_profile: str) -> tuple[str, tuple[str, ...]]:
    """``(--profile, --mute-tools)`` off argv, read at import.

    Both flags have to be resolved *here* rather than once a server is already
    starting its stdio loop: which tools exist is decided when the module
    registers them, which is import time, and the mute subtracts from the
    profile inside ``register_tools``, which runs at import too. Hence
    ``parse_known_args`` — every other flag a spawner passes (``--url``,
    ``--server-name``, ``--chat-id``, …) stays inert here, and a run under
    pytest, whose argv is the test runner's, resolves the defaults.

    ``default_profile`` is the caller's, not this module's: each server owns its
    own ``DEFAULT_TOOL_PROFILE`` and its own rings. An empty or absent
    ``--mute-tools`` is the norm — the spawner only puts it on the line when the
    operator has actually switched something off — and blanks in it are not
    names, so ``"a, b,,c"`` is ``("a", "b", "c")``.

    The single declaration of the wire format both servers and
    ``condor.runtime.toolsets`` have to agree on: renaming a flag or widening
    the separator contract is one edit here, not a two-file change a reviewer
    can half-land.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", default=default_profile)
    parser.add_argument("--mute-tools", default="")
    args, _ = parser.parse_known_args()
    muted = tuple(n.strip() for n in (args.mute_tools or "").split(",") if n.strip())
    return args.profile, muted


def make_resolver(namespace: dict[str, Any]) -> Callable[[str], Any]:
    """A ``_resolve(name)`` that looks tools up in one server's ``globals()``.

    The namespace is captured, not copied, so resolution stays lazy: a server
    may build its resolver before the last tool is defined.
    """

    def _resolve(name: str):
        """The tool function ``name`` refers to — or a loud failure at import.

        A name in ``profiles.PROFILE_TOOLS`` with nothing behind it is a rename
        that only landed on one side. Failing the import is the whole point: the
        quiet alternative is a seat that mounts one tool fewer than its table
        claims.
        """
        fn = namespace.get(name)
        if not callable(fn):
            raise RuntimeError(
                f"profiles.py names a tool this module does not define: {name!r}"
            )
        return fn

    return _resolve


def resolve_profiles(
    namespace: dict[str, Any],
    profile_tools: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple]:
    """``profile → the tool functions it registers``, resolved in ``namespace``.

    The rings live in each server's ``profiles.py`` as plain name strings,
    because the web process has to read them to draw a switch per tool and
    cannot import a ``server.py`` to ask. Here the names are resolved back into
    functions, at import, which is what keeps the table and the functions
    provably in step.
    """
    resolve = make_resolver(namespace)
    return {
        profile: tuple(resolve(name) for name in names)
        for profile, names in profile_tools.items()
    }


def register_tools(
    server: FastMCP,
    tool_profiles: Mapping[str, tuple],
    profile: str,
    muted: Iterable[str] = (),
) -> None:
    """Register this profile's tools on ``server``, minus the muted ones.

    Raises on an unknown profile rather than degrading to ``full``: the only
    spawner that passes the flag is ``condor.runtime.toolsets``, so a name that
    does not resolve is a bug there, and silently widening a seat is the one
    failure mode this feature exists to prevent.

    ``muted`` is the operator's per-agent curation (FEAT-091), arriving on argv
    as ``--mute-tools``. It only ever subtracts — ``mute ⊆ profile``, always —
    and a name this profile never mounts is ignored rather than refused: seats
    mount different rings, so "off here, never mounted there" is an ordinary
    difference between seats and not a mistake to report.
    """
    try:
        tools = tool_profiles[profile]
    except KeyError:
        raise ValueError(
            f"Unknown tool profile {profile!r}; expected one of "
            f"{sorted(tool_profiles)}"
        ) from None
    switched_off = set(muted)
    for fn in tools:
        if fn.__name__ in switched_off:
            continue
        server.tool()(fn)
