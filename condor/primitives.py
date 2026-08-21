"""What a snippet or a routine can call — discoverable from inside the process.

Condor's primitive library is ``condor.fetchers`` and friends: 60-odd typed,
documented functions that already exist. The problem this module solves is not
capability but *discovery* — ``run_code`` binds exactly three names
(``context``, ``client``, ``__name__``), so everything else is a blind
``import condor.*`` that the model has to already know about. Prose lists drift;
resident tool schemas cost tokens on every turn whether trading comes up or not.

So the index is **derived from the code it documents** and read on demand:

    from condor.primitives import catalog, describe, call_routine
    print(catalog())                                  # grouped one-liner index
    print(describe("market_data.fetch_candles"))      # signature + full docstring
    print(describe("routine:pool_scan"))              # config fields and defaults
    snap = await call_routine("portfolio_snapshot")   # compose, don't reimplement

Curation is one dict (``_CATALOG``) in one file; a new fetcher appears the
moment it is written, and ``tests/test_primitives_catalog.py`` fails CI if it
lands without a docstring or a typed signature.

Primitives are **imported, not bound**. A routine must be able to write the
identical line a snippet writes, which is the invariant FEAT-047 established and
this feature keeps.
"""

from __future__ import annotations

import contextvars
import difflib
import importlib
import inspect
from contextlib import contextmanager
from typing import Any

import condor.reports as reports
from routines.base import RoutineResult, normalize_result

# Display group -> module path. The whole curation surface of this feature: one
# list, deliberately shorter than "every module under condor" — a catalog nobody
# can read on impulse is a catalog nobody reads.
_CATALOG: dict[str, str] = {
    "market_data": "condor.fetchers.market_data",
    "portfolio": "condor.fetchers.portfolio",
    "executors": "condor.fetchers.executors",
    "positions": "condor.fetchers.positions",
    "orders": "condor.fetchers.orders",
    "bots": "condor.fetchers.bots",
    "connectors": "condor.fetchers.connectors",
    "reports": "condor.reports",
    "rates": "condor.rates",
}

# ``client`` is the Hummingbot API handle, untyped by convention across the whole
# fetchers package (it is duck-typed, and importing the SDK type into every
# module for an annotation buys nothing). Exempting it by name is what lets the
# signature check in CI be strict about everything else.
UNTYPED_PARAMS = frozenset({"client"})

_ROUTINE_PREFIX = "routine:"

# Longest catalog line before the parameter list is elided; `describe` has the
# full one, so a wide signature costs a lookup rather than a wrapped line.
_MAX_SIG = 88


# ── the index ──


def _load(group: str):
    """Import one catalogued module. Raises KeyError for an unknown group."""
    return importlib.import_module(_CATALOG[group])


def _public_callables(mod) -> list[tuple[str, Any]]:
    """The module's public surface, in name order.

    ``__all__`` when the module declares one — ``condor.reports`` is a package
    that re-exports from ``.builder``/``.store``, so a ``__module__`` test would
    find nothing there. Otherwise: public callables *defined here*, which keeps
    a fetcher module from listing everything it imported.
    """
    declared = getattr(mod, "__all__", None)
    if declared:
        pairs = [(n, getattr(mod, n, None)) for n in declared]
    else:
        pairs = [
            (n, o)
            for n, o in vars(mod).items()
            if not n.startswith("_")
            and callable(o)
            and getattr(o, "__module__", "") == mod.__name__
        ]
    return sorted(((n, o) for n, o in pairs if callable(o)), key=lambda p: p[0])


def _compact_signature(obj) -> str:
    """``(client, trading_pair, interval='1m', ...)`` — names and defaults, no types.

    The types are the expensive half of an ``inspect.signature`` render and the
    least useful one in an index; ``describe`` returns the annotated signature.
    """
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):  # builtins and C-level callables
        return "(...)"

    parts: list[str] = []
    for name, p in sig.parameters.items():
        if p.kind is p.VAR_POSITIONAL:
            parts.append(f"*{name}")
        elif p.kind is p.VAR_KEYWORD:
            parts.append(f"**{name}")
        elif p.default is p.empty:
            parts.append(name)
        else:
            parts.append(f"{name}={p.default!r}")

    out = ""
    for i, part in enumerate(parts):
        candidate = f"{out}, {part}" if out else part
        if len(candidate) > _MAX_SIG and i:
            return f"({out}, ...)"
        out = candidate
    return f"({out})"


def _summary(obj) -> str:
    """First line of the docstring — the whole of what a catalog line explains."""
    doc = inspect.getdoc(obj) or ""
    return doc.strip().split("\n")[0].strip()


def _routine_entries() -> list[dict]:
    """Every routine this install can run, or [] when discovery is unavailable.

    Discovery touches the filesystem and imports routine modules; a broken
    routine elsewhere in the library must cost a missing catalog section, not a
    failed ``catalog()`` call.
    """
    try:
        from condor.routine_store import get_routine_store

        return sorted(get_routine_store().list_routines(), key=lambda r: r["name"])
    except Exception:  # pragma: no cover - discovery failure is environmental
        return []


def _all_refs() -> list[str]:
    """Every ref ``describe`` accepts, for near-match suggestions."""
    refs: list[str] = []
    for group in _CATALOG:
        try:
            mod = _load(group)
        except Exception:  # pragma: no cover - a broken module is not a crash here
            continue
        refs.extend(f"{group}.{name}" for name, _ in _public_callables(mod))
    refs.extend(f"{_ROUTINE_PREFIX}{r['name']}" for r in _routine_entries())
    return refs


def catalog(group: str = "") -> str:
    """A grouped, one-line-per-primitive index of what this process can call.

    Args:
        group: Narrow to a single group — one of the module groups, or
            ``"routines"``. Empty returns everything.

    Returns:
        Text meant to be ``print``ed. Each line is
        ``name(compact signature) — first docstring line``; pass any of them to
        :func:`describe` for the full signature and docstring.
    """
    groups = list(_CATALOG) if not group else [group] if group in _CATALOG else []
    want_routines = group in ("", "routines")
    if group and not groups and not want_routines:
        return (
            f"Unknown group '{group}'. Groups: "
            + ", ".join(list(_CATALOG) + ["routines"])
            + "."
        )

    lines: list[str] = []
    for name in groups:
        try:
            mod = _load(name)
        except Exception as e:  # pragma: no cover - environmental
            lines.append(f"\n{name} — unavailable ({type(e).__name__}: {e})")
            continue
        lines.append(f"\n{name}  ({_CATALOG[name]})")
        for fn_name, obj in _public_callables(mod):
            summary = _summary(obj)
            tail = f" — {summary}" if summary else ""
            lines.append(f"  {fn_name}{_compact_signature(obj)}{tail}")

    if want_routines:
        entries = _routine_entries()
        lines.append(
            "\nroutines  (await call_routine(name, config) — "
            "start_routine(name, config) for a backgrounded run)"
        )
        for entry in entries:
            kind = " [continuous]" if entry.get("is_continuous") else ""
            desc = (entry.get("description") or "").strip()
            lines.append(f"  {entry['name']}{kind}" + (f" — {desc}" if desc else ""))
        if not entries:
            lines.append("  (none discovered)")

    header = (
        "condor.primitives — import these in a snippet or a routine.\n"
        'describe("<ref>") for one full signature + docstring; '
        'describe("routine:<name>") for its config fields.\n'
        'catalog("<group>") for one group only: '
        + ", ".join(list(_CATALOG) + ["routines"])
        + "."
    )
    return header + "\n" + "\n".join(lines).lstrip("\n")


def _describe_routine(name: str) -> str:
    for entry in _routine_entries():
        if entry["name"] == name or entry["name"].split("/")[-1] == name:
            break
    else:
        return _unknown(f"{_ROUTINE_PREFIX}{name}")

    kind = "continuous" if entry.get("is_continuous") else "one-shot"
    head = (
        f"routine:{entry['name']}  ({kind}, category={entry.get('category') or '—'}, "
        f"source={entry.get('source') or '—'})"
    )
    body = [head, "", entry.get("description") or "(no description)", "", "config:"]
    fields = entry.get("fields") or {}
    if not fields:
        body.append("  (no fields)")
    for field, meta in fields.items():
        default = meta.get("default")
        desc = (meta.get("description") or "").strip()
        body.append(
            f"  {field}: {meta.get('type', 'Any')} = {default!r}"
            + (f" — {desc}" if desc else "")
        )
    call = (
        f'await start_routine("{entry["name"]}", {{...}})'
        if entry.get("is_continuous")
        else f'await call_routine("{entry["name"]}", {{...}})'
    )
    body += ["", call]
    return "\n".join(body)


def _unknown(ref: str) -> str:
    """A wrong guess costs a line, not a run: name the near misses."""
    close = difflib.get_close_matches(ref, _all_refs(), n=5, cutoff=0.5)
    out = [f"Unknown ref '{ref}'."]
    if close:
        out.append("Did you mean:")
        out.extend(f"  {c}" for c in close)
    out.append('Call catalog() for the full index, or catalog("<group>") for one.')
    return "\n".join(out)


def describe(ref: str) -> str:
    """One primitive in full: its live signature and its whole docstring.

    Args:
        ref: ``"<group>.<function>"`` (as printed by :func:`catalog`) or
            ``"routine:<name>"``.

    Returns:
        Text meant to be ``print``ed. An unknown ref returns its near matches
        instead of raising — a bad guess should cost a line, not a run.
    """
    ref = (ref or "").strip()
    if ref.startswith(_ROUTINE_PREFIX):
        return _describe_routine(ref[len(_ROUTINE_PREFIX) :].strip())

    group, _, fn_name = ref.partition(".")
    if not fn_name or group not in _CATALOG:
        return _unknown(ref)
    try:
        mod = _load(group)
    except Exception as e:  # pragma: no cover - environmental
        return f"Group '{group}' ({_CATALOG[group]}) failed to import: {e}"

    obj = dict(_public_callables(mod)).get(fn_name)
    if obj is None:
        return _unknown(ref)

    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):  # pragma: no cover - builtins
        sig = "(...)"
    prefix = "async def " if inspect.iscoroutinefunction(obj) else "def "
    if inspect.isclass(obj):
        prefix = "class "
    doc = inspect.getdoc(obj) or "(undocumented)"
    return (
        f"{_CATALOG[group]}.{fn_name}\n\n{prefix}{fn_name}{sig}\n\n{doc}\n\n"
        f"from {_CATALOG[group]} import {fn_name}"
    )


# ── composition ──

# The chain of routines currently being called, innermost last. A ContextVar, so
# an ``asyncio.gather`` branch inherits the caller's chain without seeing its
# siblings' — each Task runs in a *copy* of the context.
_STACK: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "condor_routine_stack", default=()
)

# The context of the snippet or routine that is running, so a nested call
# inherits its server, its bot and the user it executes for instead of falling
# back to an ambient default. Bound by code_runner and RoutineStore.
_CONTEXT: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "condor_routine_context", default=None
)

# Deep enough for real composition (a report routine calling two collectors that
# each call one), shallow enough that a runaway chain fails as a message rather
# than as a recursion limit. `delegate` bounds itself the same way.
_MAX_DEPTH = 3

DEFAULT_CALL_TIMEOUT = 60.0


@contextmanager
def bind_context(ctx):
    """Make ``ctx`` the context a nested :func:`call_routine` inherits.

    Called by the two things that build one — ``code_runner`` for a snippet and
    ``RoutineStore._execute_and_record`` for a routine — so a nested run lands on
    the caller's server, as the caller's user, with the caller's bot. Without it
    a nested routine would silently resolve the chat's ambient default, which is
    the bug class FEAT-051 closed for pinned servers.
    """
    token = _CONTEXT.set(ctx)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _resolve_context(explicit):
    """The context a nested run should use: explicit, inherited, or a bare one."""
    if explicit is not None:
        return explicit
    inherited = _CONTEXT.get()
    if inherited is not None:
        return inherited
    from condor.routine_store import WebRoutineContext, get_routine_store

    return WebRoutineContext("", bot=get_routine_store().get_bot(), chat_id=0)


def _resolve_routine(name: str):
    from condor.routine_store import get_routine_store

    routine = get_routine_store()._resolve_routine(name)
    if routine is None:
        raise ValueError(_unknown(f"{_ROUTINE_PREFIX}{name}"))
    return routine


def _validate_config(routine, name: str, config: dict | None):
    """Build the routine's Config, or raise naming the fields it accepts.

    A ValidationError alone says which key is wrong; the model still has to go
    looking for the right one. Attaching the field list makes the traceback it
    reads contain the fix.
    """
    try:
        return routine.config_class(**(config or {}))
    except Exception as e:
        fields = ", ".join(routine.config_class.model_fields) or "(none)"
        raise ValueError(
            f"Invalid config for routine '{name}': {e}\nFields: {fields}\n"
            f'See describe("routine:{name}") for types and defaults.'
        ) from e


def _guard_stack(name: str) -> tuple[str, ...]:
    stack = _STACK.get()
    if name in stack:
        chain = " → ".join(stack + (name,))
        raise RecursionError(
            f"Routine '{name}' is already running in this call chain ({chain}). "
            "A routine cannot call itself, directly or through another routine."
        )
    if len(stack) >= _MAX_DEPTH:
        chain = " → ".join(stack + (name,))
        raise RecursionError(
            f"Routine call depth {_MAX_DEPTH} exceeded ({chain}). Flatten the "
            "chain, or background the tail with start_routine()."
        )
    return stack + (name,)


async def _run_nested(routine, cfg, ctx, stack: tuple[str, ...], out: dict):
    """The inner routine, in its own task context.

    Everything here that writes a ContextVar — the stack push, the report
    attribution, the last-report reset — is confined to this task's *copy* of the
    context, which is precisely why the call runs in a Task rather than as a bare
    awaited coroutine (the measurement FEAT-047 recorded). Resetting the last
    report id is safe for the same reason, and necessary: without it a child that
    saves nothing would report the caller's own report as its own.

    Deliberately not ``_execute_and_record``: a nested call is an implementation
    detail of the caller, so it registers no dock instance, fires no post-run
    hook, sends the user no message and stamps no separate telemetry run. That
    split is what ``start_routine`` exists for.
    """
    _STACK.set(stack)
    reports.reset_last_report_id()
    base_name = (routine.name or "").split("/")[-1]
    owner = getattr(ctx, "_chat_id", 0) or 0
    from condor.routine_store import _agent_of

    try:
        with bind_context(ctx):
            with reports.attribute_owner(owner):
                with reports.attribute_to(_agent_of(routine)):
                    with reports.default_source("routine", base_name):
                        raw = await routine.run_fn(cfg, ctx)
    finally:
        out["report_id"] = reports.get_last_report_id() or ""
    return normalize_result(raw)


async def call_routine(
    name: str,
    config: dict | None = None,
    *,
    context: Any = None,
    timeout: float = DEFAULT_CALL_TIMEOUT,
) -> RoutineResult:
    """Run another routine inline and get its result back.

    The composition primitive: a routine or a snippet calls a routine that
    already does the work instead of reimplementing it. Concurrency is plain
    stdlib — ``await asyncio.gather(call_routine(a), call_routine(b))`` runs both
    and keeps their report attribution apart.

    A nested call is an implementation detail of the caller: it produces **no**
    dock instance, fires **no** post-run hook and sends the user **no** message.
    When you want those, use :func:`start_routine`.

    Args:
        name: Routine name, bare or ``"agent_slug/name"``.
        config: Config overrides, merged over the routine's defaults.
        context: Routine context to run against. Defaults to the caller's own,
            so the nested run inherits its server, bot and user.
        timeout: Seconds to allow the inner routine.

    Returns:
        Its :class:`~routines.base.RoutineResult`, with a ``report_id``
        attribute carrying the report the inner routine saved (``""`` if none),
        so the caller can link it.

    Raises:
        ValueError: unknown routine, continuous routine, or invalid config.
        RecursionError: a cycle, or a chain deeper than three.
        TimeoutError: the inner routine outran ``timeout``.
        RuntimeError: the inner routine raised; the cause is chained.
    """
    import asyncio

    routine = _resolve_routine(name)
    if routine.is_continuous:
        raise ValueError(
            f"Routine '{name}' is continuous — it never returns, so it cannot be "
            "called inline. Start it in the background with "
            f"start_routine('{name}') and stop it with "
            "manage_routines(action='stop', name=<instance_id>)."
        )

    stack = _guard_stack(name)
    cfg = _validate_config(routine, name, config)
    ctx = _resolve_context(context)

    out: dict = {"report_id": ""}
    task = asyncio.create_task(_run_nested(routine, cfg, ctx, stack, out))
    try:
        result = await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise TimeoutError(
            f"Routine '{name}' did not finish within {timeout:g}s. Raise "
            "`timeout=`, or run it in the background with start_routine()."
        ) from e
    except asyncio.CancelledError:
        raise
    except Exception as e:
        raise RuntimeError(f"Routine '{name}' failed: {type(e).__name__}: {e}") from e

    result.report_id = out["report_id"]
    return result


async def start_routine(
    name: str,
    config: dict | None = None,
    *,
    context: Any = None,
    agent: str = "",
) -> str:
    """Start a routine as a real background run and return its ``instance_id``.

    The full-fat path, unchanged: the run appears in the dock while it runs,
    fires its post-run hooks, keeps its result and reports back to the user.
    Use it for a routine that is slow, or continuous, or that the user should
    see. Read it back with ``manage_routines(action="get_instance",
    name=<instance_id>)``.

    Args:
        name: Routine name, bare or ``"agent_slug/name"``.
        config: Config overrides, merged over the routine's defaults.
        context: Routine context to run against. Defaults to the caller's own.
        agent: Attribute the run's reports to this assistant slug. Defaults to
            the routine's own owner.

    Returns:
        The instance id.
    """
    from condor.routine_store import get_routine_store

    routine = _resolve_routine(name)
    cfg = _validate_config(routine, name, config)
    ctx = _resolve_context(context)
    store = get_routine_store()
    server = getattr(ctx, "server_name", "") or ""
    user_id = getattr(ctx, "_chat_id", 0) or 0
    # The validated model, dumped: `execute` re-builds Config from this dict, and
    # sending the *validated* values keeps a coerced default from silently
    # differing between the inline and backgrounded paths.
    payload = cfg.model_dump()
    runner = store.start_continuous if routine.is_continuous else store.execute
    return await runner(name, payload, server, user_id, agent=agent)
