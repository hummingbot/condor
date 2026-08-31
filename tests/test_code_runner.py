"""Running a snippet where a routine runs (FEAT-047).

The properties pinned here are the ones that make a snippet worth writing
instead of a routine file: top-level ``await`` with no boilerplate, tracebacks
that point at the caller's own line, a timeout that still hands back what was
printed before the cut, and capture that cannot leak between concurrent runs or
swallow the bot's own output.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio
import io
import sys

import pytest

import condor.reports as reports
from condor import code_runner
from condor.code_runs import CodeRunStore
from condor.reports import rendering


class _FakeClient:
    """Stands in for the cached HummingbotAPIClient the main process hands over."""


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store under tmp_path, and a client that resolves without a server."""
    written = CodeRunStore(tmp_path / "code_runs")
    monkeypatch.setattr(code_runner, "get_code_run_store", lambda: written)

    import config_manager

    async def _client(*a, **kw):
        return _FakeClient()

    monkeypatch.setattr(config_manager, "get_client", _client)
    return written


@pytest.fixture
def no_client(monkeypatch):
    """No server resolves — the case a pure-pandas snippet must survive."""
    import config_manager

    async def _raise(*a, **kw):
        raise ValueError("No servers configured")

    monkeypatch.setattr(config_manager, "get_client", _raise)


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "CHARTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(
        reports, "INDEX_FILE", tmp_path / "reports" / "reports_index.json"
    )
    monkeypatch.setattr(
        rendering, "plotly_bundle", lambda: "/**\n* plotly.js v0.0.0\n*/\nwindow.Plotly={};"
    )
    return tmp_path / "reports"


def run(code: str, **kw) -> dict:
    return asyncio.run(code_runner.execute_code(code, **kw))


# ── the snippet contract ──


def test_a_plain_snippet_prints_and_returns_its_result(store):
    out = run("print('hello')\nresult = 1 + 1")

    assert out["status"] == "ok"
    assert out["stdout"] == "hello\n"
    assert out["result"] == "2"
    assert out["error"] == "" and out["traceback"] == ""


def test_top_level_await_needs_no_boilerplate(store):
    out = run("import asyncio\nawait asyncio.sleep(0)\nresult = 'awaited'")

    assert out["status"] == "ok"
    assert out["result"] == "awaited"


def test_a_pandas_result_renders_as_the_value_not_its_repr(store):
    out = run("import pandas as pd\nresult = pd.Series([1, 2, 3]).mean()")

    assert out["status"] == "ok"
    assert out["result"] == "2.0"


def test_a_snippet_that_sets_no_result_reports_an_empty_one(store):
    assert run("x = 5")["result"] == ""


def test_the_snippet_reaches_the_routine_context(store):
    """``context`` is the same object a routine gets, carrying its server."""
    out = run(
        "result = (type(context).__name__, context.server_name, type(client).__name__)",
        server_name="prod",
    )

    assert out["result"] == "('WebRoutineContext', 'prod', '_FakeClient')"


def test_a_missing_client_is_a_notice_in_stdout_not_a_failure(store, no_client):
    out = run("result = client is None")

    assert out["status"] == "ok"
    assert out["result"] == "True"
    assert "no API client available" in out["stdout"]


# ── failure, readable by the model that wrote the code ──


def test_an_error_traceback_starts_at_the_callers_own_line(store):
    out = run("x = 1\ny = 2\nraise ValueError('boom')")

    assert out["status"] == "error"
    assert out["error"] == "ValueError: boom"
    first_frame = [
        line
        for line in out["traceback"].splitlines()
        if line.strip().startswith("File")
    ][0]
    assert '"<code_run>", line 3' in first_frame, out["traceback"]
    assert "code_runner.py" not in out["traceback"], "harness frames must be trimmed"


def test_a_syntax_error_is_a_recorded_run_not_a_raise(store):
    out = run("def (:")

    assert out["status"] == "error"
    assert "SyntaxError" in out["error"]
    assert "<code_run>" in out["traceback"]
    assert store.get(out["id"]) is not None


def test_a_failed_run_is_retrievable_whole_afterwards(store):
    out = run("raise ZeroDivisionError('division by zero')")

    stored = store.get(out["id"])
    assert stored["traceback"] == out["traceback"]
    assert stored["code"] == "raise ZeroDivisionError('division by zero')"


def test_a_timeout_is_cut_and_keeps_what_was_printed(store):
    out = run(
        "import asyncio\nprint('before')\nawait asyncio.sleep(30)\nprint('after')",
        timeout=1,
    )

    assert out["status"] == "timeout"
    assert "before" in out["stdout"]
    assert "after" not in out["stdout"]
    assert "Timed out after 1s" in out["error"]


# ── capture isolation ──


def test_concurrent_runs_do_not_read_each_others_output(store):
    async def both():
        return await asyncio.gather(
            code_runner.execute_code(
                "import asyncio\nprint('A1')\nawait asyncio.sleep(0.05)\nprint('A2')",
                label="a",
            ),
            code_runner.execute_code(
                "import asyncio\nprint('B1')\nawait asyncio.sleep(0.05)\nprint('B2')",
                label="b",
            ),
        )

    first, second = asyncio.run(both())

    assert first["stdout"] == "A1\nA2\n"
    assert second["stdout"] == "B1\nB2\n"


def test_the_process_stdout_is_untouched_before_during_and_after(store):
    real = io.StringIO()
    original = sys.stdout
    sys.stdout = real
    try:
        print("before the run")
        out = run("print('inside the run')")
        print("after the run")
    finally:
        sys.stdout = original

    assert out["stdout"] == "inside the run\n"
    assert real.getvalue() == "before the run\nafter the run\n"


def test_installing_the_proxy_twice_does_not_stack_it(store):
    original = sys.stdout
    try:
        code_runner._install_stdout_proxy()
        once = sys.stdout
        code_runner._install_stdout_proxy()
        assert sys.stdout is once
    finally:
        sys.stdout = original


# ── reports ──


def test_a_snippet_that_saves_a_report_gets_a_report_id(store, reports_dir):
    out = run(
        "from condor.reports import ReportBuilder\n"
        "r = ReportBuilder('Snippet report')\n"
        "r.markdown('hi')\n"
        "result = await r.save()",
        agent="quant",
        label="funding drift",
    )

    assert out["status"] == "ok"
    assert out["report_id"] and out["report_id"] == out["result"]

    entry = reports.list_reports()[0][0]
    assert entry["agent"] == "quant"
    assert entry["source_type"] == "code"
    assert entry["source_name"] == "funding drift"


def test_a_routine_called_from_a_snippet_is_filed_under_the_snippets_agent(
    store, reports_dir, monkeypatch
):
    """ARCH-217: the nested report lands in the dock the enclosing run does.

    The nested runner used to hardcode the inner routine's own library owner,
    which is ``"condor"`` for every shared routine — so an agent that composed
    work out of ``call_routine`` filed the result under Condor while its own
    snippet report was stamped with the agent, and ``list_reports`` matches
    ``agent`` exactly, so the agent's dock never showed it.
    """
    from pydantic import BaseModel

    from condor import routine_store
    from routines.base import RoutineInfo

    class _Config(BaseModel):
        """A routine from the shared library."""

    async def inner(config, context):
        builder = reports.ReportBuilder("Nested report")
        builder.markdown("hi")
        await builder.save()
        return "done"

    library = {
        "inner": RoutineInfo(
            name="inner", config_class=_Config, run_fn=inner, source="global"
        )
    }
    monkeypatch.setattr(routine_store, "get_routine", library.get)

    out = run(
        "from condor.primitives import call_routine\n"
        "result = (await call_routine('inner')).report_id",
        agent="quant",
        label="composed",
    )

    assert out["status"] == "ok", out.get("output")
    assert reports.get_report(out["result"])["agent"] == "quant"
    listed, _ = reports.list_reports(agent="quant")
    assert [entry["title"] for entry in listed] == ["Nested report"]


def test_a_report_saved_before_a_failure_is_still_reported(store, reports_dir):
    out = run(
        "from condor.reports import ReportBuilder\n"
        "r = ReportBuilder('Half a report')\n"
        "r.markdown('hi')\n"
        "await r.save()\n"
        "raise RuntimeError('after the save')"
    )

    assert out["status"] == "error"
    assert out["report_id"], "the id is read in a finally, not on the happy path"


# ── persistence ──


def test_every_run_lands_in_the_store_newest_first(store):
    run("result = 1", label="first")
    run("result = 2", label="second")

    assert [e["label"] for e in store.list()] == ["second", "first"]
    assert store.list()[0]["status"] == "ok"


def test_the_run_records_its_provenance(store):
    out = run(
        "pass",
        agent="quant",
        label="probe",
        server_name="prod",
        session_key="web:7:slot-1",
    )

    stored = store.get(out["id"])
    assert stored["agent"] == "quant"
    assert stored["label"] == "probe"
    assert stored["server"] == "prod"
    assert stored["session_key"] == "web:7:slot-1"
    assert stored["duration_ms"] >= 0
