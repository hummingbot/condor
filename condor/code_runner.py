"""Run an ad-hoc Python snippet with a routine's primitives (FEAT-047).

This is the thinner sibling of ``RoutineStore._execute_and_record``: same main
process, same :class:`WebRoutineContext`, same report attribution — with the
routine file and its config schema removed. What a snippet can reach, a routine
can reach, and vice versa; that is the point, because "it worked in run_code" is
otherwise a bug class.

The snippet contract is deliberately *no* contract: the code is a script body,
top-level ``await`` is allowed, ``print()`` is the output and an optional
``result`` variable is the return value. ``PyCF_ALLOW_TOP_LEVEL_AWAIT`` buys
that without wrapping the source in a function, which is what keeps traceback
line numbers pointing at the line the caller actually wrote — the single most
useful property when the caller is a model about to fix its own snippet.

There is no sandbox and that is deliberate — a snippet is meant to reach exactly
what a routine reaches. It follows that calling ``execute_code`` is handing over
the whole process, so every caller must gate it at least as tightly as
``condor/web/routes/code.py`` does (SEC-151); "the caller is authenticated" is
not a gate.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import inspect
import io
import logging
import sys
import time
import traceback

import condor.reports as reports
from condor.code_runs import CodeRun, get_code_run_store, new_run_id
from condor.memory.paths import CHAT_SLUG

logger = logging.getLogger(__name__)

# The name compiled snippets carry, and therefore the name in their tracebacks.
CODE_FILENAME = "<code_run>"

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 120.0

# Where a run's ``print`` goes. Per-context, so concurrent runs cannot read each
# other's output — which only holds because every run gets its own asyncio.Task
# (a Task copies the context; awaiting a bare coroutine would share ours and
# leak the buffer into the caller).
_CAPTURE: contextvars.ContextVar[io.StringIO | None] = contextvars.ContextVar(
    "code_run_stdout", default=None
)


class _StdoutProxy:
    """``sys.stdout`` that routes to the running snippet's buffer, else onward.

    Installed process-wide and never removed, so it must be invisible to
    everything that is not a code run: with no capture buffer set it is a pass
    through to the stream it wrapped, and the bot's own output is untouched.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        buffer = _CAPTURE.get()
        if buffer is not None:
            return buffer.write(data)
        return self._stream.write(data)

    def flush(self):
        buffer = _CAPTURE.get()
        if buffer is None:
            self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _install_stdout_proxy() -> None:
    """Wrap ``sys.stdout`` once. The isinstance check is the idempotence.

    Not a "did we already do it" flag: something else may have replaced
    ``sys.stdout`` since (a test harness, a debugger), and then the wrapper is
    gone and capture has to be re-established over the new stream.
    """
    if not isinstance(sys.stdout, _StdoutProxy):
        sys.stdout = _StdoutProxy(sys.stdout)


def _trim_traceback(text: str) -> str:
    """Drop the harness frames above the snippet's own.

    Everything before the first ``File "<code_run>"`` line is this module
    calling ``eval``; a model reading the traceback to fix its code should see
    only its code.
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if CODE_FILENAME in line:
            head = lines[0] if lines and lines[0].startswith("Traceback") else ""
            return head + "".join(lines[i:])
    return text


def _render_result(namespace: dict) -> str:
    """The snippet's ``result`` global as text, or "" when it never set one.

    ``str`` rather than ``repr``: the readers are a model and a human, and
    ``2.0`` says what ``np.float64(2.0)`` says with less noise, while a
    DataFrame renders as its table either way.
    """
    if "result" not in namespace:
        return ""
    value = namespace["result"]
    if value is None:
        return ""
    try:
        return str(value)
    except Exception as e:  # a __str__ that raises must not fail the run
        return f"<unrenderable result: {type(e).__name__}: {e}>"


async def _run_in_task(
    compiled,
    namespace: dict,
    buffer,
    agent: str,
    source: str,
    out: dict,
    owner_id: int = 0,
):
    """The snippet itself, in its own task context.

    Setting the capture ContextVar *here* (not in the caller) is what keeps the
    buffer private to this run, and the report id is read in a ``finally`` so a
    snippet that saved a report and then raised still reports it.
    ``owner_id`` is the authenticated caller (``chat_id`` in ``execute_code``);
    it stamps any report the snippet saves so the web routes can authorize
    reads/deletes against it (SEC-196).
    """
    _CAPTURE.set(buffer)
    reports.reset_last_report_id()
    try:
        with (
            reports.attribute_owner(owner_id),
            reports.attribute_to(agent),
            reports.default_source("code", source),
        ):
            outcome = eval(compiled, namespace)  # noqa: S307 - this IS the feature
            if inspect.isawaitable(outcome):
                await outcome
    finally:
        out["report_id"] = reports.get_last_report_id() or ""


async def execute_code(
    code: str,
    *,
    server_name: str = "",
    agent: str = "",
    label: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    chat_id: int = 0,
    session_key: str = "",
) -> dict:
    """Compile, run and persist one snippet. Returns the stored record as a dict."""
    from condor.routine_store import WebRoutineContext, get_routine_store
    from config_manager import get_client

    run_id = new_run_id()
    agent = agent or CHAT_SLUG
    timeout = max(1.0, min(float(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    buffer = io.StringIO()
    started = time.monotonic()

    run = CodeRun(
        id=run_id,
        created=time.time(),
        agent=agent,
        label=label,
        server=server_name,
        code=code,
        session_key=session_key,
    )

    try:
        compiled = compile(
            code, CODE_FILENAME, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        )
    except SyntaxError as e:
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"
        run.traceback = _trim_traceback(traceback.format_exc())
        run.duration_ms = int((time.monotonic() - started) * 1000)
        get_code_run_store().save(run)
        return run.to_dict()

    # The exact context a routine gets, so the two paths cannot diverge — the
    # active server travels as a preference, which is how get_client resolves it.
    context = WebRoutineContext(
        server_name, bot=get_routine_store().get_bot(), chat_id=chat_id
    )
    # Best effort: a pure-pandas snippet must still run when no server resolves,
    # and the reason belongs in the run's own output, not in a raised error.
    try:
        client = await get_client(chat_id, context=context)
    except Exception as e:
        client = None
        buffer.write(f"[run_code] no API client available ({e}); `client` is None\n")

    namespace = {
        "__name__": "__code_run__",
        "context": context,
        "client": client,
    }

    _install_stdout_proxy()
    out: dict = {"report_id": ""}
    task = asyncio.create_task(
        _run_in_task(
            compiled,
            namespace,
            buffer,
            agent,
            label or run_id,
            out,
            owner_id=chat_id,
        )
    )
    try:
        await asyncio.wait_for(task, timeout=timeout)
        run.status = "ok"
    except (asyncio.TimeoutError, TimeoutError):
        run.status = "timeout"
        run.error = f"Timed out after {timeout:g}s"
    except asyncio.CancelledError:
        run.status = "timeout"
        run.error = "Cancelled"
        raise
    except BaseException as e:
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"
        run.traceback = _trim_traceback(
            "".join(traceback.format_exception(type(e), e, e.__traceback__))
        )
    finally:
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.stdout = buffer.getvalue()
        run.result = _render_result(namespace)
        run.report_id = out.get("report_id", "")
        # Never log the snippet or its output: a run can hold credentials it
        # printed, and the application log has neither the cap nor the gitignore
        # that data/code_runs/ has.
        logger.info(
            "code run %s finished status=%s in %dms",
            run_id,
            run.status,
            run.duration_ms,
        )
        get_code_run_store().save(run)

    return run.to_dict()
