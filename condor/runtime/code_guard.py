"""Keep agent-written code from taking the main process down with it.

``run_code`` snippets and routines run on Condor's own event loop — that is the
feature (see ``condor/code_runner.py``): a snippet reaches exactly what a
routine reaches. The cost is that synchronous code in them runs *on* the loop,
so ``asyncio.wait_for`` cannot stop it, and the 2026-10-07 freeze was exactly
that: a snippet's ``glob.glob("/**/x.py", recursive=True)`` walked ``/sys``
symlink cycles on the main thread for hours, and the dashboard, every agent and
Condor's own SIGTERM handler waited behind it.

This is not a sandbox and does not try to be: the callers are models making
mistakes, not adversaries, and anything here can be stepped around on purpose.
What it does is turn the mistakes we have actually seen into an error on the
offending line instead of a dead process:

* **A deadline that can interrupt synchronous code.** Two hooks raise
  :class:`CodeRunDeadline` once a guarded run is past its budget:
  ``sys.monitoring`` on the snippet's own code objects (a ``while True`` loop,
  however tight) and an audit hook on everything the snippet calls (any
  syscall: file I/O, ``scandir``, sockets). It is a ``BaseException`` so
  ``except Exception`` in the snippet cannot swallow it. Code that is handling
  an exception — a ``finally``, an ``except``, a ``with`` exit — may run for up
  to :data:`INTERRUPT_GRACE` seconds, so cleanup can release what it holds;
  everything else is interrupted again, so a loop that swallows it still ends.
* **A short policy list**, enforced through the same audit hook: no listing of
  ``/``, ``/proc``, ``/sys`` or ``/dev``; no ``input()`` (there is no stdin);
  no signalling Condor's own process; no ``time.sleep`` longer than
  :data:`MAX_BLOCKING_SLEEP` (via a wrapper: ``time.sleep`` has no audit event
  before Python 3.13).
* **A static check** (:func:`check_source`) for the exits with no audit event
  at all — ``os._exit``, ``os.abort``, ``signal.raise_signal``.
* **``SystemExit`` / ``KeyboardInterrupt`` containment** (:func:`contain_exit`).
  asyncio re-raises both out of the event loop, so a bare ``exit()`` in a
  snippet used to shut Condor down with exit code 0.

A run Condor starts on a snippet's behalf to outlive it — a background routine
from ``start_routine`` — must be spawned in :func:`detached_context`; otherwise
its task inherits the snippet's scope and is cut when the snippet's run ends.

Everything is scoped by a :class:`ContextVar`, so only code running in a guarded
run's context — its own task and any task it spawned — is affected; the rest of
Condor never pays more than one ``ContextVar.get()`` per audit event.

What it cannot do: interrupt a single long call into C (a huge ``sorted``, a
``subprocess.run`` waiting on its child) or cap memory. Those need the snippet
in another process; until then :mod:`condor.runtime.loop_watchdog` at least
logs where the loop is stuck.
"""

from __future__ import annotations

import ast
import contextlib
import contextvars
import dis
import functools
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from types import CodeType
from typing import Iterator

logger = logging.getLogger(__name__)

# How long code handling a deadline interruption (a ``finally``, an ``except``,
# a ``with`` exit) may run before it is interrupted too. Long enough to release
# a lock or close a file; short enough that a cleanup that hangs is still cut.
INTERRUPT_GRACE = 2.0

# The longest ``time.sleep`` a guarded run may make. Every second of it is a
# second the whole event loop — dashboard, agents, Telegram — stands still.
MAX_BLOCKING_SLEEP = 1.0

# Directories no agent has a reason to list, and the root, which is where every
# "find this file" search that ran for hours started. ``/proc`` and ``/sys`` are
# the ones with cycles; ``/dev`` has entries that block on open.
_FORBIDDEN_TREES = ("/proc", "/sys", "/dev")

# ``sys.monitoring`` tool ids 0-2 and 5 are claimed by convention (debugger,
# coverage, profiler, optimizer). Take a free one of the rest.
_TOOL_IDS = (4, 3)
_TOOL_NAME = "condor-code-guard"


class CodeRunDeadline(BaseException):
    """A guarded run went past its budget. A ``BaseException`` on purpose."""


class GuardViolation(Exception):
    """A guarded run tried something that would take the main process down."""


@dataclass
class GuardScope:
    """One guarded run: who it is and when (if ever) it must stop.

    ``deadline`` is a ``time.monotonic()`` value, or ``None`` for code with no
    budget (a continuous routine). It is mutable so :meth:`expire` can end a
    finished run's stragglers: a task the snippet spawned and never awaited
    still carries this scope in its context, and is cut at its next line.
    """

    label: str
    deadline: float | None = None
    budget: float | None = field(default=None, repr=False)
    _interrupted_at: float | None = field(default=None, repr=False)

    def expire(self) -> None:
        self.deadline = time.monotonic()

    def should_interrupt(self) -> bool:
        """True when the run is past its deadline and is not cleaning up.

        Raising on every line after the deadline meant a ``finally`` never got
        to run its first line, and a lock it held stayed held for everyone else.
        So code that is handling the interruption is spared — for a while. The
        test is the exception being handled, not a timer: the first raise can
        land in C code that swallows it, and a timer started then would spend
        the cleanup window on the very loop it was meant to stop.
        """
        if not self.interrupt_due():
            return False
        if self._interrupted_at is None:
            self._interrupted_at = time.monotonic()
        return True

    def interrupt_due(self) -> bool:
        """:meth:`should_interrupt` without recording that it happened."""
        if self.deadline is None:
            return False
        now = time.monotonic()
        if now <= self.deadline:
            return False
        # Handling *any* exception counts: the interruption itself, the
        # CancelledError of a snippet that timed out while awaiting (wait_for
        # cut it, not this guard), and the GeneratorExit a suspended
        # generator's ``finally`` runs under.
        if sys.exception() is None:
            return True
        # The grace runs from this guard's own interruption, or from the
        # deadline when something else (wait_for) did the interrupting.
        since = self._interrupted_at or self.deadline
        return now - since >= INTERRUPT_GRACE


_SCOPE: contextvars.ContextVar[GuardScope | None] = contextvars.ContextVar(
    "condor_code_guard_scope", default=None
)

_installed = False
_tool_id: int | None = None


def current_scope() -> GuardScope | None:
    return _SCOPE.get()


# ── Runtime hooks ──


def _deadline(scope: GuardScope, where: str) -> CodeRunDeadline:
    budget = f" of {scope.budget:g}s" if scope.budget else ""
    return CodeRunDeadline(f"interrupted {where} after its budget{budget} ran out")


def _as_path(value) -> str | None:
    """An audit-event path argument as an absolute, normalized ``str``.

    Pure string work only: anything that could itself raise an audit event
    (``os.getcwd`` inside ``abspath``) would re-enter the hook. Relative paths,
    file descriptors and ``None`` (the cwd) are never the root or a system tree,
    so they come back ``None`` and are let through.
    """
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    elif not isinstance(value, str):
        fspath = getattr(value, "__fspath__", None)
        if fspath is None:
            return None
        try:
            return _as_path(fspath())
        except Exception:
            return None
    if not value.startswith("/"):
        return None
    return os.path.normpath(value)


def _check_listing(args) -> None:
    path = _as_path(args[0]) if args else None
    if path is None:
        return
    if path == "/" or any(
        path == tree or path.startswith(tree + "/") for tree in _FORBIDDEN_TREES
    ):
        raise GuardViolation(
            f"listing {path!r} is not allowed here: a search from the filesystem "
            "root (or through /proc, /sys, /dev) can run for hours and freezes "
            "Condor while it does. Open the exact path you need instead."
        )


def _check_kill(args) -> None:
    pid = args[0] if args else None
    if pid in (0, -1, os.getpid(), -os.getpgrp()):
        raise GuardViolation("signalling Condor's own process is not allowed here")


def _check_killpg(args) -> None:
    if args and args[0] in (0, os.getpgrp()):
        raise GuardViolation(
            "signalling Condor's own process group is not allowed here"
        )


def _check_sleep(secs) -> None:
    if isinstance(secs, (int, float)) and secs > MAX_BLOCKING_SLEEP:
        raise GuardViolation(
            f"time.sleep({secs:g}) would freeze all of Condor for {secs:g}s; "
            f"use `await asyncio.sleep({secs:g})` instead"
        )


def _deny(message: str):
    def check(_args) -> None:
        raise GuardViolation(message)

    return check


_POLICY = {
    "os.scandir": _check_listing,
    "os.listdir": _check_listing,
    "os.kill": _check_kill,
    "os.killpg": _check_killpg,
    "signal.pthread_kill": _deny("signalling Condor's threads is not allowed here"),
    "builtins.input": _deny(
        "input() is not available here: there is no one at stdin, and waiting "
        "for it freezes Condor"
    ),
}


def _audit(event: str, args) -> None:
    scope = _SCOPE.get()
    if scope is None:
        return
    if scope.should_interrupt():
        raise _deadline(scope, f"at {event}")
    check = _POLICY.get(event)
    if check is not None:
        check(args)


_original_sleep = time.sleep


@functools.wraps(_original_sleep)
def _guarded_sleep(secs):
    scope = _SCOPE.get()
    if scope is not None:
        if scope.should_interrupt():
            raise _deadline(scope, "at time.sleep")
        _check_sleep(secs)
    return _original_sleep(secs)


_WATCHED = 0  # set by install(): LINE | JUMP

# Where an exception enters a handler. The exception is not yet marked as being
# handled there, so an interruption at this instruction would look like one
# outside any cleanup and bypass the handler — a ``with`` block's ``__exit__``
# never ran, and the lock it would have released stayed held.
_HANDLER_ENTRY = dis.opmap["PUSH_EXC_INFO"]


def _arm(code: CodeType) -> None:
    sys.monitoring.set_local_events(
        _tool_id, code, _WATCHED | sys.monitoring.events.INSTRUCTION
    )


def _on_line(code: CodeType, _line: int) -> None:
    """Arm :func:`_on_instruction` for ``code`` when an interruption is due.

    Neither this nor :func:`_on_jump` raises. Snippet code is only ever
    interrupted from :func:`_on_instruction`, the one place that knows exactly
    which instruction is next — and on CPython 3.12 that matters: raised from a
    JUMP callback, on a compiler-generated instruction or on a handler's first
    instruction, the exception skips the ``finally``/``except``/``__exit__`` it
    should have run.
    """
    scope = _SCOPE.get()
    if scope is not None and scope.interrupt_due():
        _arm(code)


def _on_jump(code: CodeType, _src: int, _dst: int) -> None:
    """As :func:`_on_line`. Needed because LINE does not reliably fire inside a
    one-line loop, and a backward jump always does."""
    scope = _SCOPE.get()
    if scope is not None and scope.interrupt_due():
        _arm(code)


_unsafe: dict[CodeType, frozenset[int]] = {}


def _safe_to_raise(code: CodeType, offset: int) -> bool:
    """Whether raising at ``offset`` lands inside the frame's exception table.

    Not at a compiler-generated instruction (no source line — a loop's
    back-edge, the shuffling around a handler) and not at a handler's entry.
    """
    unsafe = _unsafe.get(code)
    if unsafe is None:
        lineless = {
            off
            for start, end, line in code.co_lines()
            if line is None
            for off in range(start, end, 2)
        }
        raw = code.co_code
        entries = {off for off in range(0, len(raw), 2) if raw[off] == _HANDLER_ENTRY}
        unsafe = frozenset(lineless | entries)
        if len(_unsafe) > 256:  # snippets are throwaway; so is this cache
            _unsafe.clear()
        _unsafe[code] = unsafe
    return offset not in unsafe


def _on_instruction(code: CodeType, offset: int) -> None:
    """Interrupt at the first instruction where it is safe to, then disarm.

    Stays armed while the interruption is not due — the run is cleaning up —
    so the moment cleanup ends, or its grace runs out, is not missed in a
    tight loop. Disarmed before raising, so it cannot fire again on the
    handler the exception is about to enter.
    """
    scope = _SCOPE.get()
    if scope is None or not scope.interrupt_due():
        if scope is None:
            sys.monitoring.set_local_events(_tool_id, code, _WATCHED)
        return
    if not _safe_to_raise(code, offset):
        return
    sys.monitoring.set_local_events(_tool_id, code, _WATCHED)
    scope.should_interrupt()  # records the interruption
    raise _deadline(scope, f"in {code.co_name}")


def install() -> None:
    """Install the audit hook and claim a ``sys.monitoring`` tool. Idempotent.

    An audit hook can never be removed, which is why :func:`_audit` returns on
    its first line for everything outside a guarded run.
    """
    global _installed, _tool_id, _WATCHED
    if _installed:
        return
    sys.addaudithook(_audit)
    # Process-wide, but a pass-through for everything outside a guarded run —
    # including every other thread, whose context never carries a scope.
    time.sleep = _guarded_sleep
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is not None:
        for tool_id in _TOOL_IDS:
            if monitoring.get_tool(tool_id) is None:
                monitoring.use_tool_id(tool_id, _TOOL_NAME)
                events = monitoring.events
                monitoring.register_callback(tool_id, events.LINE, _on_line)
                monitoring.register_callback(tool_id, events.JUMP, _on_jump)
                monitoring.register_callback(
                    tool_id, events.INSTRUCTION, _on_instruction
                )
                _WATCHED = events.LINE | events.JUMP
                _tool_id = tool_id
                break
        else:
            logger.warning(
                "No free sys.monitoring tool id; run_code deadlines will only "
                "interrupt snippets at their next syscall, not in pure-CPU loops"
            )
    _installed = True


def _code_objects(code: CodeType) -> Iterator[CodeType]:
    yield code
    for const in code.co_consts:
        if isinstance(const, CodeType):
            yield from _code_objects(const)


def _watch_lines(code: CodeType) -> None:
    """Watch every line and jump of ``code`` for a passed deadline.

    Only the snippet's own code objects — the functions, lambdas and
    comprehensions it defines included — so library code it calls runs at full
    speed. INSTRUCTION events are armed per code object only once an
    interruption is due (see :func:`_on_jump`), never before.
    """
    if _tool_id is None:
        return
    for obj in _code_objects(code):
        sys.monitoring.set_local_events(_tool_id, obj, _WATCHED)


@contextlib.contextmanager
def guarded(scope: GuardScope, code: CodeType | None = None) -> Iterator[GuardScope]:
    """Run the body (and every task it spawns) under ``scope``.

    ``code`` is the compiled snippet; passing it lets the deadline interrupt
    pure-CPU loops in it. Without it the deadline still fires at the next
    syscall.
    """
    install()
    if code is not None and scope.deadline is not None:
        _watch_lines(code)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def routine_scope(label: str) -> GuardScope:
    """The scope a routine runs under: the policy, and its caller's deadline.

    A routine has no budget of its own (continuous ones run until stopped), but
    one called from a snippet via ``call_routine`` must not outlive the snippet
    that is waiting on it.
    """
    parent = _SCOPE.get()
    if parent is not None:
        return GuardScope(label=label, deadline=parent.deadline, budget=parent.budget)
    return GuardScope(label=label)


def detached_context() -> contextvars.Context:
    """A copy of the current context with no guard scope in it.

    For ``asyncio.create_task(..., context=detached_context())`` wherever Condor
    starts a run meant to outlive its caller. Without it, a background routine
    started from a snippet carried that snippet's deadline and was cut — at its
    next I/O, past ``except Exception`` — once the snippet finished.
    """
    context = contextvars.copy_context()
    context.run(_SCOPE.set, None)
    return context


@contextlib.contextmanager
def contain_exit() -> Iterator[None]:
    """Turn ``SystemExit`` / ``KeyboardInterrupt`` into an ordinary error.

    asyncio's task step re-raises both straight out of the event loop, which
    ends ``asyncio.run`` and with it Condor — so ``exit()`` in a snippet was a
    clean shutdown with exit code 0 and nothing in the log.
    """
    try:
        yield
    except (SystemExit, KeyboardInterrupt) as e:
        raise GuardViolation(
            f"{type(e).__name__} is not allowed here: it would stop Condor. "
            "Set `result` and let the code finish instead."
        ) from None


# ── Static check ──

# Calls that end or crash the process and raise no audit event first, so the
# hook never sees them. Matched by (module, attribute) on ``module.attr(...)``
# and on ``from module import attr``.
_PROCESS_ENDERS = {
    ("os", "_exit"),
    ("os", "abort"),
    ("posix", "_exit"),
    ("posix", "abort"),
    ("signal", "raise_signal"),
    ("faulthandler", "_sigsegv"),
    ("faulthandler", "_sigabrt"),
}


def check_source(tree: ast.AST) -> str | None:
    """A reason to refuse this code before it runs, or ``None``.

    Deliberately narrow: only calls that end the process outright. Everything
    else is the runtime hooks' job, where the error lands on the real line.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if (node.module, alias.name) in _PROCESS_ENDERS:
                    return _ender_message(node.module, alias.name, node.lineno)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (node.value.id, node.attr) in _PROCESS_ENDERS
        ):
            return _ender_message(node.value.id, node.attr, node.lineno)
    return None


def _ender_message(module: str, name: str, lineno: int) -> str:
    return (
        f"line {lineno}: {module}.{name} is not allowed: it would stop Condor's "
        "main process (the dashboard and every agent with it)"
    )
