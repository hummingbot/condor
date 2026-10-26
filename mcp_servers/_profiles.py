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
parses argv and builds a ``MCPServer`` singleton as a side effect) nor anything
that builds one — ``mcp_servers/hummingbot_api/__init__.py`` lazy-loads ``main``
precisely so that the tables stay reachable without waking a server. Hence the
``MCPServer`` annotation below is a type-checking import only, and the one SDK
import this module needs at runtime is deferred to registration, by which point
the caller is a server.
"""

from __future__ import annotations

import argparse
import functools
import inspect
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - annotation only, never imported at runtime
    from mcp.server.mcpserver import MCPServer


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


def parse_execution_mode() -> str:
    """``--execution-mode`` off argv, read at import; ``""`` when absent.

    The third thing a spawner can say about a seat, beside its ring and its
    mutes, and the one that is a *property of the run* rather than of the agent:
    the same tick engine builds a rehearsal and a live loop out of one config.

    Read here, at import, for the same reason the other two are: the restriction
    it carries is applied inside :func:`register_tools`, which runs at import.
    ``parse_known_args`` again, so a server nobody passes the flag to — every
    chat seat, and a run under pytest whose argv is the test runner's — reads
    ``""`` and restricts nothing.

    The *meaning* of a mode is deliberately not here. This module knows the wire
    format; which actions a mode forbids is policy, and policy lives in
    ``condor.runtime.danger`` where the rest of the tool-call classification
    already is (SEC-616, SEC-626). A server turns the string into a ``refusal``
    callable and hands it back.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--execution-mode", default="")
    args, _ = parser.parse_known_args()
    return (args.execution_mode or "").strip()


def _gated(fn: Any, refusal: Callable[[str, dict[str, Any]], str | None]) -> Any:
    """``fn``, wrapped so ``refusal`` can turn a call back before it runs.

    The refusal is returned as the tool's ordinary ``{"error": ...}`` result
    rather than raised: a model reads the reason and picks another route, where
    an exception reaches it as a tool failure it is apt to retry verbatim. It is
    the same shape every tool in these servers already uses to say no.

    ``functools.wraps`` carries ``__name__`` (which is how ``muted`` and every
    golden-list test identify a tool), ``__doc__`` (the description the model
    reads) and ``__wrapped__`` — and that last one is load-bearing:
    ``inspect.signature`` follows it, so MCPServer derives the schema from the
    *original* signature and the wrapper's ``**kwargs`` never reaches the wire.
    """

    @functools.wraps(fn)
    async def gated(**kwargs: Any) -> Any:
        reason = refusal(fn.__name__, kwargs)
        if reason:
            return {"error": reason}
        return await fn(**kwargs)

    return gated


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


def _surface_errors(fn):
    """Keep a tool's exception message in the result the model reads.

    MCP SDK >= 2.1 reports any non-SDK exception as a bare
    ``Error executing tool <name>``. Agents act on the message, so re-raise
    as the SDK's ToolError, which is passed through verbatim.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def register_tools(
    server: MCPServer,
    tool_profiles: Mapping[str, tuple],
    profile: str,
    muted: Iterable[str] = (),
    refusal: Callable[[str, dict[str, Any]], str | None] | None = None,
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

    ``refusal`` is the third subtraction, and the only one that cuts *within* a
    tool: ``(tool_name, kwargs) -> reason | None``, consulted on every call, so
    a mode that forbids some of a tool's actions and needs the rest can say so
    (CORR-748). ``run_code``'s ``history``/``get`` and ``manage_routines``'
    ``list``/``describe``/``read_routine`` are exactly that case — a dry run
    cannot rehearse at all without them, so neither tool can simply be muted.

    It is enforced *here*, in the subprocess that owns the tool, rather than at
    the session's permission callback, because the callback is only reached when
    the client asks: ``ACPClient._on_request_permission`` answers a request a
    bridge chooses to send, and a bridge that auto-approves its own tool calls —
    or implements no permission method at all — never sends one. The gate then
    refuses nothing while reporting nothing, which is how a dry run executed a
    snippet and fetched live candles with the policy that forbids it intact and
    its unit test passing. A tool this process declines to run cannot be
    auto-approved by anybody.

    Every tool must be a coroutine function. The SDK runs a sync ``def`` on a
    worker thread with no running loop, and ``_surface_errors`` awaits what it
    wraps — so a sync tool is refused here, at startup, rather than discovered
    on its first call.
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
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"MCP tool {fn.__name__!r} must be an async function")
        if fn.__name__ in switched_off:
            continue
        # Both wrappers, error surfacing OUTERMOST. A refusal returns a dict
        # and raises nothing, so it passes through untouched; but if the gate
        # itself ever raised, the model would otherwise read a bare "Error
        # executing tool" instead of the reason.
        gated = _gated(fn, refusal) if refusal else fn
        server.tool()(_surface_errors(gated))
