"""Counting a 429, per agent and per venue (FEAT-138).

GitHub issue [#276]: "Sampling the logs suggested one host was 'intermittent,
may recover'. Counting its attempts showed 8 attempts, 8 failures, 0 successes
and 27 refusals: it was consistently refused." And "Throttling generally looked
like a mysterious strategy bug or a vendor outage, not like what it was."

Nothing in Condor counted a 429 before this. These pin the whole chain:

- the tool path classifies a venue refusal apart from every other failure, and
  counting it changes no call's outcome;
- the counters make #276's reading reproducible — 8 attempts, 0 successes, 8
  refusals — from numbers rather than from a log sample;
- a refused venue is reported as a *venue condition* and reaches the engine's
  ``RefusalLog``, its journal and the next tick's prompt as the venue's doing,
  never as the agent's error;
- call volume is attributed per agent and per host, so the agent that consumed a
  host's budget can be named ([[CORR-726]]).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from condor.agents import throttle as throttle_mod
from condor.agents.risk import RefusalLog
from condor.agents.throttle import ThrottleLedger, throttle_log_path
from mcp_servers.hummingbot_api import throttle as writer
from mcp_servers.hummingbot_api.exceptions import MaxConnectionsAttemptError, ToolError
from mcp_servers.hummingbot_api.middleware import handle_errors


@pytest.fixture(autouse=True)
def _own_log(tmp_path, monkeypatch):
    """A log of this test's own, and a ledger that has read none of it.

    The ledger is process-wide on purpose (the byte offset into the shared log
    is per-process state), so a test that left it folded would hand its counts
    to the next one.
    """
    path = tmp_path / "throttle.jsonl"
    monkeypatch.setenv(writer.THROTTLE_LOG_ENV, str(path))
    throttle_mod.reset_ledger()
    yield path
    throttle_mod.reset_ledger()


def _record(**kwargs):
    writer.record_call(
        kwargs.pop("tool", "get_prices"), kwargs.pop("outcome"), **kwargs
    )


# ── Classifying what came back ──


@pytest.mark.parametrize(
    "error",
    [
        "429, message='Too Many Requests', url='https://api.gate.io/...'",
        "Too Many Requests",
        "rate limit exceeded for this endpoint",
        "RATELIMIT: slow down",
        "Request throttled, retry later",
    ],
)
def test_a_venue_refusal_is_recognised_from_its_prose(error):
    assert writer.is_throttle_refusal(Exception(error))


def test_an_http_status_is_believed_before_the_prose():
    # aiohttp carries the status and the venue's wording varies by exchange, so
    # the status is the reliable half.
    class Refused(Exception):
        status = 429

    assert "429" in writer.is_throttle_refusal(Refused("nothing quotable here"))


@pytest.mark.parametrize(
    "error",
    [
        "insufficient balance",
        "price 1429.50 rejected by the venue",
        "order 4290 not found",
        "connection reset by peer",
        "",
    ],
)
def test_an_ordinary_failure_is_not_counted_as_a_refusal(error):
    # A number that merely contains 429 is not a status code. Miscounting here
    # would turn a strategy's own bad order into "the venue is throttling us",
    # which is the #276 confusion with the signs reversed.
    assert writer.is_throttle_refusal(Exception(error)) == ""


def test_the_venue_is_read_off_the_calls_own_arguments():
    assert writer.venue_of({"connector_name": "gate_io"}) == "gate_io"
    assert writer.venue_of({"connector_names": ["binance"]}) == "binance"
    # Two venues in one call cannot attribute a refusal to either of them.
    assert writer.venue_of({"connector_names": ["a", "b"]}) == writer.VENUE_UNKNOWN
    assert writer.venue_of({"trading_pair": "SOL-USDC"}) == writer.VENUE_UNKNOWN


# ── The tool path counts, and decides nothing ──


def _tool(result=None, raises=None):
    @handle_errors("get prices")
    async def get_prices(connector_name: str = "gate_io"):
        if raises is not None:
            raise raises
        return result

    return get_prices


def _settings_labels(
    monkeypatch, agent="market-maker", caller_id="mm-1", host="brigado"
):
    from mcp_servers.hummingbot_api.settings import settings

    monkeypatch.setattr(settings, "agent_slug", agent, raising=False)
    monkeypatch.setattr(settings, "caller_id", caller_id, raising=False)
    monkeypatch.setattr(settings, "server_name", host, raising=False)


def test_a_429_in_the_tool_path_is_counted_against_the_agent_and_the_venue(
    monkeypatch, _own_log
):
    _settings_labels(monkeypatch)

    with pytest.raises(ToolError):
        asyncio.run(_tool(raises=Exception("429, message='Too Many Requests'"))())

    events = [json.loads(line) for line in _own_log.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["outcome"] == writer.OUTCOME_REFUSED
    assert events[0]["venue"] == "gate_io"
    assert events[0]["host"] == "brigado"
    assert events[0]["agent"] == "market-maker"
    assert events[0]["agent_id"] == "mm-1"
    assert events[0]["tool"] == "get_prices"


def test_counting_changes_no_calls_outcome(monkeypatch, _own_log):
    """The fifth criterion, and the whole safety of this feature.

    A counter that could refuse, retry or reshape a call would be the limiter
    alternative 2 rejected. So each of the four paths through ``handle_errors``
    has to return or raise exactly what it did before the counter existed.
    """
    _settings_labels(monkeypatch)

    assert asyncio.run(_tool(result="ok")()) == "ok"

    # A connection error is still translated to ToolError, with its own text.
    with pytest.raises(ToolError, match="unreachable"):
        asyncio.run(_tool(raises=MaxConnectionsAttemptError("unreachable"))())

    # An existing ToolError passes through untouched, not re-wrapped.
    original = ToolError("already a tool error")
    with pytest.raises(ToolError) as caught:
        asyncio.run(_tool(raises=original)())
    assert caught.value is original

    # Anything else is wrapped with the action name, as before.
    with pytest.raises(ToolError, match="Failed to get prices: boom"):
        asyncio.run(_tool(raises=RuntimeError("boom"))())


def test_a_broken_recorder_cannot_break_a_call(monkeypatch, _own_log):
    """Accounting is best-effort at the call site, by construction."""
    _settings_labels(monkeypatch)

    def explode(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(
        "mcp_servers.hummingbot_api.middleware.record_call", explode, raising=True
    )

    assert asyncio.run(_tool(result="still fine")()) == "still fine"
    with pytest.raises(ToolError, match="Failed to get prices: boom"):
        asyncio.run(_tool(raises=RuntimeError("boom"))())


def test_recording_is_off_with_no_log_handed_over(monkeypatch, tmp_path):
    """A standalone launch (uvx, an external MCP host) writes nothing."""
    monkeypatch.delenv(writer.THROTTLE_LOG_ENV, raising=False)
    _settings_labels(monkeypatch)

    assert asyncio.run(_tool(result="ok")()) == "ok"
    assert not list(tmp_path.glob("*.jsonl"))


# ── The counters answer #276's question ──


def test_eight_attempts_and_eight_refusals_report_eight_refused_none_ok(_own_log):
    """#276's reading, reproduced from the counters instead of the logs."""
    for _ in range(8):
        _record(
            outcome=writer.OUTCOME_REFUSED,
            venue="gate_io",
            host="brigado",
            agent="market-maker",
            agent_id="mm-1",
            reason="venue returned HTTP 429 (too many requests)",
        )

    book = ThrottleLedger(path=_own_log)
    assert len(book.ingest()) == 8

    (row,) = book.venue_conditions()
    assert (row["attempts"], row["successes"], row["refusals"]) == (8, 0, 8)
    assert row["venue"] == "gate_io"
    assert row["condition"] == "throttled"


def test_attempts_always_equal_the_three_outcomes(_own_log):
    for outcome in (
        writer.OUTCOME_OK,
        writer.OUTCOME_OK,
        writer.OUTCOME_REFUSED,
        writer.OUTCOME_ERROR,
    ):
        _record(
            outcome=outcome, venue="binance", host="brigado", agent="a", agent_id="a-1"
        )

    book = ThrottleLedger(path=_own_log)
    book.ingest()
    (row,) = book.venue_conditions()
    assert row["attempts"] == row["successes"] + row["refusals"] + row["errors"] == 4
    assert (row["successes"], row["refusals"], row["errors"]) == (2, 1, 1)


def test_a_refusal_long_past_keeps_its_counts_and_loses_its_condition(_own_log):
    _record(
        outcome=writer.OUTCOME_REFUSED,
        venue="gate_io",
        host="brigado",
        agent="a",
        agent_id="a-1",
        now=1_000.0,
    )

    book = ThrottleLedger(path=_own_log)
    book.ingest()

    # The counts are the record; the condition is what an operator can act on.
    fresh = book.venue_conditions(now=1_000.0 + 10)
    stale = book.venue_conditions(now=1_000.0 + throttle_mod.THROTTLED_FOR_SECONDS + 1)
    assert fresh[0]["condition"] == "throttled"
    assert stale[0]["condition"] == "ok"
    assert stale[0]["refusals"] == 1


def test_a_venue_condition_is_folded_across_agents_not_per_agent(_own_log):
    """A venue refusing is a fact about the venue, not about one agent.

    Splitting it per agent is exactly what let it read as one agent's bug.
    """
    for agent in ("market-maker", "arb"):
        _record(
            outcome=writer.OUTCOME_REFUSED,
            venue="gate_io",
            host="brigado",
            agent=agent,
            agent_id=f"{agent}-1",
        )

    book = ThrottleLedger(path=_own_log)
    book.ingest()
    rows = book.venue_conditions()
    assert len(rows) == 1
    assert rows[0]["refusals"] == 2
    assert "agent" not in rows[0]


def test_the_agent_consuming_a_hosts_budget_can_be_named(_own_log):
    """[[CORR-726]] from the accounting side: who starved the host."""
    for _ in range(40):
        _record(
            outcome=writer.OUTCOME_OK,
            venue="gate_io",
            host="brigado",
            agent="poller",
            agent_id="p-1",
        )
    _record(
        outcome=writer.OUTCOME_OK,
        venue="gate_io",
        host="brigado",
        agent="arb",
        agent_id="a-1",
    )

    book = ThrottleLedger(path=_own_log)
    book.ingest()
    callers = book.caller_volume()

    # Heaviest first, so the name is the first thing read.
    assert callers[0]["agent"] == "poller"
    assert callers[0]["agent_id"] == "p-1"
    assert callers[0]["host"] == "brigado"
    assert callers[0]["attempts"] == 40
    assert callers[1]["agent"] == "arb"


def test_two_sessions_of_one_agent_are_two_consumers(_own_log):
    for session in ("mm-1", "mm-2"):
        _record(
            outcome=writer.OUTCOME_OK,
            venue="gate_io",
            host="brigado",
            agent="mm",
            agent_id=session,
        )

    book = ThrottleLedger(path=_own_log)
    book.ingest()
    assert {row["agent_id"] for row in book.caller_volume()} == {"mm-1", "mm-2"}


def test_each_session_drains_only_its_own_refusals(_own_log):
    for session in ("mm-1", "mm-2"):
        _record(
            outcome=writer.OUTCOME_REFUSED,
            venue="gate_io",
            host="brigado",
            agent="mm",
            agent_id=session,
        )

    book = ThrottleLedger(path=_own_log)
    book.ingest()

    mine = book.drain_refusals("mm-1")
    assert [e["agent_id"] for e in mine] == ["mm-1"]
    # Drained once, not twice — and the other session's is still waiting.
    assert book.drain_refusals("mm-1") == []
    assert len(book.drain_refusals("mm-2")) == 1


def test_a_sessions_venues_name_only_what_refused_that_session(_own_log):
    _record(
        outcome=writer.OUTCOME_OK,
        venue="binance",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )
    _record(
        outcome=writer.OUTCOME_REFUSED,
        venue="gate_io",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )
    _record(
        outcome=writer.OUTCOME_REFUSED,
        venue="kucoin",
        host="brigado",
        agent="mm",
        agent_id="mm-2",
    )

    book = ThrottleLedger(path=_own_log)
    book.ingest()

    assert [row["venue"] for row in book.session_venues("mm-1")] == ["gate_io"]
    assert [row["venue"] for row in book.session_venues("mm-2")] == ["kucoin"]
    assert book.session_venues("nobody") == []


def test_the_fold_is_incremental_and_survives_a_reset(_own_log):
    _record(
        outcome=writer.OUTCOME_OK,
        venue="gate_io",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )
    book = ThrottleLedger(path=_own_log)
    assert len(book.ingest()) == 1
    # Nothing new: an ingest that re-read the file would double every count.
    assert book.ingest() == []

    _record(
        outcome=writer.OUTCOME_OK,
        venue="gate_io",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )
    assert len(book.ingest()) == 1
    assert book.caller_volume()[0]["attempts"] == 2

    # The writer truncates an oversized log; what is already folded stays
    # folded and the offset restarts rather than reading past the new end.
    _own_log.write_text("")
    assert book.ingest() == []
    _record(
        outcome=writer.OUTCOME_OK,
        venue="gate_io",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )
    assert len(book.ingest()) == 1
    assert book.caller_volume()[0]["attempts"] == 3


def test_a_corrupt_line_is_dropped_and_the_rest_still_fold(_own_log):
    _own_log.write_text(
        "not json\n"
        + json.dumps({"outcome": "ok", "agent": "mm", "agent_id": "mm-1", "venue": "x"})
        + "\n[]\n"
    )
    book = ThrottleLedger(path=_own_log)
    assert len(book.ingest()) == 1


def test_a_missing_log_folds_to_nothing(tmp_path):
    book = ThrottleLedger(path=tmp_path / "never-written.jsonl")
    assert book.ingest() == []
    assert book.venue_conditions() == []
    assert book.caller_volume() == []


# ── It reaches the refusal log, the journal and the prompt ──


def test_a_throttle_refusal_is_recorded_beside_the_gates_own(_own_log):
    log = RefusalLog()
    log.note("create_position_executor", "would exceed position limit")
    log.note(
        "get_prices",
        "venue returned HTTP 429",
        kind=RefusalLog.KIND_THROTTLE,
        venue="gate_io",
        host="brigado",
    )

    kinds = [entry["kind"] for entry in log.drain()]
    assert kinds == [RefusalLog.KIND_RISK, RefusalLog.KIND_THROTTLE]


def test_a_gate_refusal_still_defaults_to_the_risk_kind():
    """Every existing caller keeps its meaning without naming the kind."""
    log = RefusalLog()
    log.note("manage_bots", "outside namespace")
    (entry,) = log.drain()
    assert entry["kind"] == RefusalLog.KIND_RISK
    assert entry["venue"] == ""


def test_the_prompt_tells_the_agent_the_venue_refused_not_the_gate():
    """The third criterion on the agent-facing surface.

    Rendered under the gate's heading, a 429 reads to the model as "the runtime
    refused what you asked for" — the reading that made throttling look like a
    strategy fault. It gets its own block saying the venue did it.
    """
    from condor.agents.prompts import build_tick_prompt

    rendered = _prompt_with_refusals(
        build_tick_prompt,
        [
            {
                "tool": "create_order_executor",
                "reason": "over the limit",
                "kind": "risk",
            },
            {
                "tool": "get_prices",
                "reason": "venue returned HTTP 429",
                "kind": "throttle",
                "venue": "gate_io",
                "host": "brigado",
            },
        ],
    )

    assert "VENUE THROTTLED LAST TICK" in rendered
    assert "gate_io" in rendered
    # The gate's block still carries the gate's own refusal, and only it.
    gate_block = rendered.split("[REFUSED LAST TICK")[1].split("[")[0]
    assert "create_order_executor" in gate_block
    assert "get_prices" not in gate_block


def test_a_tick_with_only_throttle_refusals_draws_no_gate_block():
    from condor.agents.prompts import build_tick_prompt

    rendered = _prompt_with_refusals(
        build_tick_prompt,
        [
            {
                "tool": "get_prices",
                "reason": "429",
                "kind": "throttle",
                "venue": "gate_io",
            }
        ],
    )

    assert "REFUSED LAST TICK" not in rendered
    assert "VENUE THROTTLED LAST TICK" in rendered


def _prompt_with_refusals(build_tick_prompt, refusals):
    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug="mm")
    strategy = SimpleNamespace(
        instructions="Trade.",
        agent_key="claude-code",
        slug="grid",
        agent_slug="mm",
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config={"execution_mode": "loop"},
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        cached_routines_section="",
        refusals=refusals,
    )


# ── The wire the subprocess is handed ──


def test_the_spawner_hands_the_subprocess_its_log_and_its_labels():
    """The subprocess derives nothing: the path and the labels are given to it.

    A child that recomputed the default log path could land somewhere else than
    its parent whenever the parent's own root was overridden — and deriving it
    would mean the MCP server importing ``condor`` to count a refusal.
    """
    from condor.runtime.toolsets import _hummingbot_mcp_args

    args = _hummingbot_mcp_args(
        {"host": "127.0.0.1", "port": 8000},
        "brigado",
        "tick",
        (),
        "market-maker",
        "mm-1",
    )

    assert args[args.index("--agent-slug") + 1] == "market-maker"
    assert args[args.index("--caller-id") + 1] == "mm-1"


def test_a_seat_with_no_session_stamps_no_session_label():
    from condor.runtime.toolsets import _hummingbot_mcp_args

    args = _hummingbot_mcp_args({"host": "h", "port": 1}, "brigado", "full", ())

    # Honestly unattributed rather than attributed to an empty string.
    assert "--caller-id" not in args
    assert "--agent-slug" not in args


def _spawned(monkeypatch, **kwargs) -> dict[str, dict]:
    """``{mcp server name: config}`` for a session the spawner actually builds."""
    from condor.runtime import toolsets

    class _CM:
        def get_server(self, name):
            return {"host": "h", "port": 1, "username": "u", "password": "p"}

        def has_server_access(self, user_id, name, *a, **kw):
            return True

        def get_accessible_servers(self, user_id):
            return ["local"]

    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _CM(), raising=False
    )
    monkeypatch.setattr(
        "config_manager.get_effective_server", lambda *a, **k: "local", raising=False
    )
    return {
        s["name"]: s for s in toolsets.build_mcp_servers_for_session(42, 42, **kwargs)
    }


def test_the_env_key_the_spawner_sets_is_the_one_the_writer_reads(
    monkeypatch, _own_log
):
    """One rename cannot silently switch recording off.

    The spawner writes the env entry by keyword and the writer reads it by
    constant, so this is the only thing connecting the two names — and the
    resolved value has to be the reader's own path, not a path the child would
    recompute for itself.
    """
    spawned = _spawned(monkeypatch, agent_slug="perps", caller_id="perps.4", tick=True)

    env = {e["name"]: e["value"] for e in spawned["mcp-hummingbot"]["env"]}
    assert env[writer.THROTTLE_LOG_ENV] == str(throttle_log_path())
    assert env[writer.THROTTLE_LOG_ENV] == str(_own_log)


def test_the_spawner_stamps_the_agent_and_session_on_the_tick_subprocess(monkeypatch):
    spawned = _spawned(monkeypatch, agent_slug="perps", caller_id="perps.4", tick=True)

    argv = spawned["mcp-hummingbot"]["args"]
    assert argv[argv.index("--agent-slug") + 1] == "perps"
    assert argv[argv.index("--caller-id") + 1] == "perps.4"


def test_the_log_lives_under_the_runtime_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(writer.THROTTLE_LOG_ENV, raising=False)
    monkeypatch.setenv("CONDOR_DATA_DIR", str(tmp_path))

    assert throttle_log_path() == tmp_path / "throttle.jsonl"


# ── It reaches the engine's tick, the status and the route ──


class _Journal:
    """Only what ``_journal_refusals`` touches."""

    def __init__(self):
        self.actions: list[tuple[int, str, str]] = []

    def append_action(self, tick, action, reasoning, risk_note=""):
        self.actions.append((tick, action, reasoning))


def _engine(agent_id="mm-1", journal=None):
    """A stand-in carrying only the state the three methods under test read."""
    from condor.agents.engine import TickEngine

    class _Engine:
        _ingest_throttle_refusals = TickEngine._ingest_throttle_refusals
        _throttled_venues = TickEngine._throttled_venues
        _journal_refusals = TickEngine._journal_refusals

        def __init__(self):
            self.agent_id = agent_id
            self.journal = journal
            self._refusals = RefusalLog()
            self._last_refusals: list[dict] = []
            self._last_error = ""

    return _Engine()


def test_a_tick_folds_the_venues_refusals_into_its_journal(_own_log):
    """The first criterion end to end: counted in the tool path, journaled here."""
    _record(
        outcome=writer.OUTCOME_REFUSED,
        venue="gate_io",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
        reason="venue returned HTTP 429 (too many requests)",
    )

    journal = _Journal()
    engine = _engine(journal=journal)
    engine._ingest_throttle_refusals()
    engine._last_refusals = engine._refusals.drain()
    engine._journal_refusals(7)

    assert len(engine._last_refusals) == 1
    assert engine._last_refusals[0]["kind"] == RefusalLog.KIND_THROTTLE
    assert engine._last_refusals[0]["venue"] == "gate_io"

    tick, action, reasoning = journal.actions[0]
    # Its own label: a reader scanning the journal must be able to tell "the
    # venue refused us" from "the gate refused the agent" without reading prose.
    assert (tick, action) == (7, "venue_throttled")
    assert "gate_io" in reasoning and "429" in reasoning


def test_a_throttled_venue_is_never_recorded_as_the_agents_error(_own_log):
    """The third criterion. #276: it "looked like a mysterious strategy bug"."""
    _record(
        outcome=writer.OUTCOME_REFUSED,
        venue="gate_io",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )

    engine = _engine(journal=_Journal())
    engine._ingest_throttle_refusals()

    assert engine._last_error == ""
    # It is reported instead as a condition of the venue, with its counts.
    (row,) = engine._throttled_venues()
    assert row["venue"] == "gate_io"
    assert row["condition"] == "throttled"
    assert row["refusals"] == 1


def test_a_session_nothing_refused_reports_an_empty_list(_own_log):
    _record(
        outcome=writer.OUTCOME_OK,
        venue="binance",
        host="brigado",
        agent="mm",
        agent_id="mm-1",
    )

    engine = _engine(journal=_Journal())
    engine._ingest_throttle_refusals()

    # Empty, not absent: "checked, nothing refused" is a different statement
    # from "no counters at all".
    assert engine._throttled_venues() == []
    assert engine._refusals.drain() == []


def test_an_unreadable_log_leaves_the_tick_exactly_as_it_was(monkeypatch):
    """Accounting may never fail a tick."""

    def explode():
        raise OSError("gone")

    monkeypatch.setattr(throttle_mod, "ledger", explode, raising=True)

    engine = _engine(journal=_Journal())
    engine._ingest_throttle_refusals()

    assert engine._refusals.drain() == []
    assert engine._throttled_venues() == []


def test_a_running_instance_carries_its_throttled_venues(_own_log):
    from condor.web.routes import agents as agents_routes

    info = {
        "agent_id": "mm-1",
        "session_num": 1,
        "status": "running",
        "tick_count": 2,
        "daily_pnl": 0.0,
        "server_name": "brigado",
        "throttled_venues": [
            {
                "host": "brigado",
                "venue": "gate_io",
                "condition": "throttled",
                "attempts": 8,
                "successes": 0,
                "refusals": 8,
                "errors": 0,
                "last_refusal_at": 1.0,
                "last_reason": "429",
            }
        ],
    }
    engine = SimpleNamespace(
        get_info=lambda: info, journal=None, session_dir=None, agent_id="mm-1"
    )

    inst = agents_routes._instance_from_engine(engine, {})

    assert inst.throttled_venues[0]["venue"] == "gate_io"
    assert inst.throttled_venues[0]["refusals"] == 8
    # Not folded into the run's own error.
    assert inst.last_error == ""


def test_an_older_engine_reports_no_throttled_venues():
    from condor.web.routes import agents as agents_routes

    info = {
        "agent_id": "mm-1",
        "session_num": 1,
        "status": "running",
        "tick_count": 0,
        "daily_pnl": 0.0,
        "server_name": "brigado",
    }
    engine = SimpleNamespace(
        get_info=lambda: info, journal=None, session_dir=None, agent_id="mm-1"
    )

    assert agents_routes._instance_from_engine(engine, {}).throttled_venues == []


def test_the_route_reports_the_condition_and_names_the_consumer(_own_log):
    """Criteria three and four, on the wire the dashboard reads."""
    from condor.web.routes import agents as agents_routes

    for _ in range(8):
        _record(
            outcome=writer.OUTCOME_REFUSED,
            venue="gate_io",
            host="brigado",
            agent="poller",
            agent_id="p-1",
            reason="venue returned HTTP 429 (too many requests)",
        )
    _record(
        outcome=writer.OUTCOME_OK,
        venue="binance",
        host="brigado",
        agent="arb",
        agent_id="a-1",
    )

    payload = asyncio.run(agents_routes.get_venue_conditions(user=None))

    (throttled,) = [v for v in payload.venues if v.condition == "throttled"]
    assert (
        throttled.venue,
        throttled.attempts,
        throttled.successes,
        throttled.refusals,
    ) == (
        "gate_io",
        8,
        0,
        8,
    )
    # The venue row is about the venue and carries no agent at all.
    assert not hasattr(throttled, "agent")
    # The consumer is named on the other fold, heaviest first.
    assert payload.callers[0].agent == "poller"
    assert payload.callers[0].host == "brigado"
    assert payload.callers[0].attempts == 8


def test_the_route_answers_an_install_that_has_counted_nothing(_own_log):
    from condor.web.routes import agents as agents_routes

    payload = asyncio.run(agents_routes.get_venue_conditions(user=None))

    assert payload.venues == [] and payload.callers == []
