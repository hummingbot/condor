"""The collector (FEAT-024) — the tests that make a public endpoint survivable.

Three things are being proven here, in descending order of how much they matter.

**The wire contract is the client's, not a hand-copy.** The first test builds
its envelope by running the real emitter from ``condor/telemetry/`` and handing
the result to ``context.envelope()`` — the same code path a live install uses.
If FEAT-023 ever changes shape, this file fails rather than the collector
silently dropping a field in production.

**Untrusted input cannot do damage.** This endpoint is unauthenticated and
public by design, so most of what follows is negative: an oversized body, an
over-long batch, a rate-limited caller and a malformed envelope each have to be
refused *without touching the database*, and no refusal may echo any part of
the request back.

**Idempotency is real.** The client retries after a timeout. If a retry could
inflate a count, every adoption number the exercise exists to produce would be
unreliable.

The whole suite runs against SQLite in a ``tmp_path``. Nothing here binds a
port, starts a container, or outlives the test.
"""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from telemetry_server import ingest, rollup
from telemetry_server.app import create_app
from telemetry_server.store import open_store

REPO = Path(__file__).resolve().parent.parent
QUERIES = REPO / "telemetry_server" / "queries"


# ── Harness ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_limits():
    """The limiters are process-wide; a leftover bucket would leak between tests."""
    ingest.by_ip.reset()
    ingest.by_install.reset()
    yield
    ingest.by_ip.reset()
    ingest.by_install.reset()


@pytest.fixture
def store(tmp_path):
    store = asyncio.run(open_store(str(tmp_path / "telemetry.db")))
    yield store
    asyncio.run(store.close())


@pytest.fixture
def client(store):
    with TestClient(create_app(store)) as client:
        yield client


class Spy:
    """A store that refuses to be used, so "no database contact" is checkable."""

    def __init__(self):
        self.calls = 0

    async def record(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("the database was reached for a request we refuse")

    async def ping(self):
        return True

    async def close(self):
        return None


@pytest.fixture
def spy_client():
    spy = Spy()
    with TestClient(create_app(spy)) as client:
        yield client, spy


def _event(name="command", props=None, ts=None, event_id=None):
    return {
        "id": event_id or uuid.uuid4().hex,
        "ts": ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "name": name,
        "props": (
            {"name": "portfolio", "surface": "telegram"} if props is None else props
        ),
    }


def _envelope(events=None, **overrides):
    """The shape condor/telemetry/context.py actually puts on the wire."""
    envelope = {
        "schema": 1,
        "install_id": uuid.uuid4().hex,
        "level": "usage",
        "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": {
            "version": "54ad4dc",
            "branch": "main",
            "python": "3.12",
            "os": "linux",
            "arch": "arm64",
            "in_docker": True,
        },
        "config": {
            "has_web": True,
            "has_gateway": False,
            "has_hb_api": True,
            "llm_providers": ["openai", "openrouter"],
            "user_count": 3,
            "server_count": 2,
            "agent_count": 4,
        },
        "dropped": 0,
        "events": [_event()] if events is None else events,
    }
    envelope.update(overrides)
    return envelope


def _rows(store, sql, *args):
    return asyncio.run(store.fetch(sql, *args))


# ── The contract is the client's ─────────────────────────────────────────


def test_an_envelope_built_by_the_real_client_is_accepted_whole(
    client, store, tmp_path, monkeypatch
):
    """The acceptance criterion that matters: a genuine FEAT-023 envelope lands.

    Nothing here hand-writes the wire format. The events come out of the real
    emitter and the envelope out of ``context.envelope``, so this test is the
    thing that fails if emitter and collector ever drift apart.
    """
    import config_manager as cm_module
    from condor.telemetry import consent, context, emitter

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", None, raising=False)
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY_URL", None, raising=False)
    monkeypatch.setattr(
        "condor.agents.agent._DATA_ROOT", str(tmp_path / "data"), raising=False
    )
    cm_module.ConfigManager.reset_instance()
    emitter.discard_buffer()
    emitter.set_hosted(True)
    consent.refresh()
    try:
        cm_module.get_config_manager()
        consent.grant("usage")

        emitter.emit("install")
        emitter.emit("command", name="portfolio", surface="telegram", authorized=True)
        emitter.emit("action", module="bots", verb="deploy", surface="web")
        emitter.emit("trade", venue="cex", connector="binance", side="buy")
        emitter.emit(
            "agent_turn", kind="chat", provider="openai", model="gpt-5", tool_calls=3
        )
        emitter.emit("error", where="handlers.bots", exc_type="ValueError", sig="ab12")

        events, dropped = emitter.drain()
        envelope = context.envelope(events, dropped, consent.level())
    finally:
        cm_module.ConfigManager.reset_instance()
        emitter.discard_buffer()
        consent.refresh()

    response = client.post("/v1/events", json=envelope)

    assert response.status_code == 202
    assert response.json() == {
        "accepted": len(events),
        "rejected": 0,
        "duplicates": 0,
    }

    stored = _rows(store, "SELECT name FROM events ORDER BY name")
    assert sorted(n for (n,) in stored) == sorted(e["name"] for e in events)

    # The install context lands as columns, including the capability flags the
    # client sends but the original design's table did not have.
    (row,) = _rows(
        store,
        "SELECT level, version, branch, os, in_docker, has_hb_api, has_web, "
        "user_count, llm_providers FROM installs",
    )
    assert row[0] == "usage"
    assert row[1] == envelope["app"]["version"]
    assert json.loads(row[8]) == envelope["config"]["llm_providers"]


def test_the_taxonomy_has_exactly_one_definition_in_the_repo():
    """``ingest`` validates against the emitter's own module, not a copy."""
    source = (REPO / "telemetry_server" / "ingest.py").read_text(encoding="utf-8")
    assert "from condor.telemetry import schema" in source

    definitions = []
    for path in REPO.rglob("*.py"):
        if any(part in {".venv", "node_modules", "__pycache__"} for part in path.parts):
            continue
        if re.search(
            r"^EVENTS(\s*:\s*[^=]+)?\s*=", path.read_text(encoding="utf-8"), re.M
        ):
            definitions.append(path.relative_to(REPO).as_posix())

    assert definitions == ["condor/telemetry/schema.py"]


# ── Idempotency and partial acceptance ───────────────────────────────────


def test_the_same_envelope_twice_stores_one_copy(client, store):
    """The client retries after a timeout; a retry must not inflate a metric."""
    envelope = _envelope([_event(), _event(), _event()])

    first = client.post("/v1/events", json=envelope)
    second = client.post("/v1/events", json=envelope)

    assert first.json()["accepted"] == 3
    assert second.json() == {"accepted": 0, "rejected": 0, "duplicates": 3}

    assert _rows(store, "SELECT COUNT(*) FROM events")[0][0] == 3
    # install_days is incremented from newly inserted rows only, so the rollup
    # tables cannot be inflated by a retry either.
    assert _rows(store, "SELECT SUM(events) FROM install_days")[0][0] == 3


def test_one_unknown_name_and_one_stray_prop_cost_two_rejections(client, store):
    """Partial acceptance: a single client bug must not erase a good batch."""
    envelope = _envelope(
        [
            _event(),
            _event(name="a_command_from_a_newer_client"),
            _event(props={"name": "bots", "surface": "web", "wallet": "0xdeadbeef"}),
        ]
    )

    response = client.post("/v1/events", json=envelope)

    assert response.status_code == 202
    assert response.json()["rejected"] == 2
    assert response.json()["accepted"] == 2

    (props,) = _rows(store, "SELECT props FROM events WHERE name = $1", "command")[1]
    assert "wallet" not in props
    assert "0xdeadbeef" not in props


def test_a_duplicate_id_inside_one_envelope_is_counted_once(client, store):
    shared = uuid.uuid4().hex
    envelope = _envelope([_event(event_id=shared), _event(event_id=shared)])

    assert client.post("/v1/events", json=envelope).json() == {
        "accepted": 1,
        "rejected": 1,
        "duplicates": 0,
    }
    assert _rows(store, "SELECT COUNT(*) FROM events")[0][0] == 1


def test_a_clock_from_next_year_is_clamped_not_stored(client, store):
    """Self-hosted clocks drift and occasionally lie. Believe them within 48h."""
    future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    past = (datetime.now(timezone.utc) - timedelta(days=400)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    client.post("/v1/events", json=_envelope([_event(ts=future), _event(ts=past)]))

    stored = [
        datetime.fromisoformat(ts) for (ts,) in _rows(store, "SELECT ts FROM events")
    ]
    now = datetime.now(timezone.utc)
    for ts in stored:
        assert abs(ts - now) <= ingest.CLOCK_SLACK + timedelta(minutes=1)


# ── Untrusted input ──────────────────────────────────────────────────────


def test_an_oversized_body_is_refused_without_a_query(spy_client):
    client, spy = spy_client
    body = b'{"schema":1,"pad":"' + b"a" * (ingest.MAX_BODY_BYTES + 1024) + b'"}'

    response = client.post(
        "/v1/events", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}
    assert spy.calls == 0


def test_an_oversized_body_is_refused_even_without_a_content_length():
    """Content-Length is the caller's claim, so the stream is counted as it arrives."""

    class Chunked:
        headers: dict = {}
        client = None

        async def stream(self):
            for _ in range(4):
                yield b"a" * (ingest.MAX_BODY_BYTES // 3)

    with pytest.raises(ingest.Refused) as refusal:
        asyncio.run(ingest.read_body(Chunked()))
    assert refusal.value.reason == "body_too_large"


def test_a_ten_thousand_event_envelope_is_refused_without_a_query(spy_client):
    """Two caps guard this, and the outer one bites first.

    Ten thousand events cannot fit inside a 1 MB body, so such an envelope is
    refused while it is still bytes on a socket — a stronger guarantee than the
    event-count cap. The count cap is what catches the envelope that *does* fit,
    which is asserted separately below.
    """
    client, spy = spy_client

    response = client.post(
        "/v1/events", json=_envelope([_event() for _ in range(10_000)])
    )

    assert response.status_code == 413
    assert response.json()["error"] in ("body_too_large", "too_many_events")
    assert spy.calls == 0


def test_more_events_than_the_cap_are_refused_without_a_query(spy_client):
    client, spy = spy_client
    oversized = [_event() for _ in range(ingest.MAX_EVENTS + 1)]

    response = client.post("/v1/events", json=_envelope(oversized))

    assert response.status_code == 413
    assert response.json() == {"error": "too_many_events"}
    assert spy.calls == 0


@pytest.mark.parametrize(
    "envelope, reason",
    [
        (
            {
                "schema": 99,
                "install_id": uuid.uuid4().hex,
                "level": "usage",
                "events": [],
            },
            "unknown_schema",
        ),
        (
            {"schema": 1, "install_id": "not-a-uuid", "level": "usage", "events": []},
            "invalid_envelope",
        ),
        (
            {
                "schema": 1,
                "install_id": uuid.uuid4().hex,
                "level": "root",
                "events": [],
            },
            "invalid_envelope",
        ),
        (
            {
                "schema": 1,
                "install_id": uuid.uuid4().hex,
                "level": "usage",
                "events": {},
            },
            "invalid_envelope",
        ),
        ([1, 2, 3], "invalid_envelope"),
    ],
)
def test_a_malformed_envelope_is_refused_without_a_query(spy_client, envelope, reason):
    client, spy = spy_client

    response = client.post("/v1/events", json=envelope)

    assert response.status_code == 400
    assert response.json() == {"error": reason}
    assert spy.calls == 0


def test_a_refusal_never_echoes_the_request(spy_client):
    """No reflector, and no oracle for what the server did with a probe."""
    marker = "canary-9f13ab-do-not-reflect"
    client, _ = spy_client

    responses = [
        client.post("/v1/events", content=marker.encode()),
        client.post("/v1/events", json={"schema": 1, "install_id": marker}),
        client.post("/v1/events", json=_envelope(install_id=marker)),
    ]

    for response in responses:
        assert response.status_code in (400, 413)
        assert marker not in response.text
        assert set(response.json()) == {"error"}


def test_body_that_is_not_json_is_refused(spy_client):
    client, spy = spy_client
    response = client.post("/v1/events", content=b"{not json at all")
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_json"}
    assert spy.calls == 0


def test_exceeding_the_ip_rate_limit_returns_429_without_a_query(spy_client):
    """The limiter runs before the JSON parser, so a flood costs us nothing."""
    client, spy = spy_client
    for _ in range(ingest.RATE_PER_HOUR):
        ingest.by_ip.allow("testclient")

    response = client.post("/v1/events", json=_envelope())

    assert response.status_code == 429
    assert response.json() == {"error": "rate_limited"}
    assert spy.calls == 0


def test_a_flood_from_one_install_is_limited_even_across_addresses(spy_client):
    """The per-install bucket is the one an attacker cannot rotate away from."""
    client, spy = spy_client
    install_id = str(uuid.uuid4())
    for _ in range(ingest.RATE_PER_HOUR):
        ingest.by_install.allow(install_id)

    response = client.post("/v1/events", json=_envelope(install_id=install_id))

    assert response.status_code == 429
    assert spy.calls == 0


def test_the_limiter_key_space_is_bounded(spy_client):
    """An attacker-chosen key must not be a memory exhaustion primitive."""
    limiter = ingest.RateLimiter(per_hour=60, max_keys=32)
    for index in range(5_000):
        limiter.allow(f"install-{index}")
    assert len(limiter._buckets) <= 32


def test_a_forwarded_header_is_ignored_unless_a_proxy_is_trusted(monkeypatch):
    """Honouring X-Forwarded-For by default would be a one-header limit bypass."""

    class Request:
        headers = {"x-forwarded-for": "1.2.3.4"}

        class client:
            host = "10.0.0.1"

    monkeypatch.delenv("TELEMETRY_TRUSTED_PROXY", raising=False)
    assert ingest.client_ip(Request()) == "10.0.0.1"

    monkeypatch.setenv("TELEMETRY_TRUSTED_PROXY", "true")
    assert ingest.client_ip(Request()) == "1.2.3.4"


def test_a_payload_cannot_reach_the_sql(client, store):
    """Every value is a bound parameter; nothing is formatted into a statement."""
    injection = "1'); DROP TABLE events; --"
    envelope = _envelope()
    envelope["app"]["version"] = injection
    envelope["config"]["llm_providers"] = [injection]

    assert client.post("/v1/events", json=envelope).status_code == 202

    assert _rows(store, "SELECT COUNT(*) FROM events")[0][0] == 1
    (version,) = _rows(store, "SELECT version FROM installs")[0]
    assert "DROP TABLE" not in version


def test_unknown_envelope_fields_are_dropped_not_stored(client, store):
    envelope = _envelope()
    envelope["app"]["hostname"] = "trading-box.local"
    envelope["config"]["api_key"] = "sk-live-secret"
    envelope["surprise"] = {"nested": "value"}

    assert client.post("/v1/events", json=envelope).status_code == 202

    dump = json.dumps(_rows(store, "SELECT * FROM installs"))
    assert "trading-box" not in dump
    assert "sk-live-secret" not in dump


def test_absurd_counts_are_clamped(client, store):
    envelope = _envelope()
    envelope["config"]["user_count"] = 10**18
    envelope["dropped"] = 10**18

    client.post("/v1/events", json=envelope)

    (users, dropped) = _rows(store, "SELECT user_count, dropped_total FROM installs")[0]
    assert users == ingest.MAX_COUNT
    assert dropped == ingest.MAX_DROPPED


# ── Health ───────────────────────────────────────────────────────────────


def test_health_reports_the_database(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_health_degrades_when_the_database_is_gone():
    class Broken:
        async def ping(self):
            raise RuntimeError("connection refused")

        async def close(self):
            return None

    with TestClient(create_app(Broken())) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


# ── Rollups and queries ──────────────────────────────────────────────────


def _seed_cohorts(store):
    """Three installs with hand-checkable retention, written straight to the
    rollup table so the arithmetic under test is the rollup's, not ingest's."""
    base = datetime(2026, 7, 1, tzinfo=timezone.utc).date()
    plan = {
        "aaaaaaaa-0000-4000-8000-000000000001": [0, 1, 7],  # retained at D1 and D7
        "aaaaaaaa-0000-4000-8000-000000000002": [0, 1],  # D1 only
        "aaaaaaaa-0000-4000-8000-000000000003": [0],  # never came back
    }
    for install_id, offsets in plan.items():
        for offset in offsets:
            asyncio.run(
                store.execute(
                    "INSERT INTO install_days (install_id, day, events, errors, version)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    install_id,
                    base + timedelta(days=offset),
                    10,
                    2 if offset == 0 else 0,
                    "54ad4dc",
                )
            )
    return base


def test_retention_is_computed_from_the_permanent_table(store):
    base = _seed_cohorts(store)
    asyncio.run(rollup.run(store, today=base + timedelta(days=40)))

    def metric(name):
        rows = _rows(
            store,
            "SELECT value FROM daily_metrics WHERE day = $1 AND metric = $2",
            base.isoformat(),
            name,
        )
        return rows[0][0] if rows else None

    assert metric("cohort_size") == 3
    assert metric("retention_d1") == 2
    assert metric("retention_d7") == 1
    assert metric("retention_d30") == 0
    assert metric("dau") == 3
    assert metric("error_rate") == pytest.approx(6 / 30)


def test_retention_survives_the_loss_of_the_raw_events(store):
    """install_days is written on the ingest path precisely so this holds."""
    base = _seed_cohorts(store)
    asyncio.run(rollup.run(store, today=base + timedelta(days=40)))
    before = _rows(store, "SELECT COUNT(*) FROM daily_metrics")[0][0]

    asyncio.run(store.execute("DELETE FROM events"))
    asyncio.run(rollup.run(store, today=base + timedelta(days=40)))

    assert _rows(store, "SELECT COUNT(*) FROM daily_metrics")[0][0] >= before - 5
    assert (
        _rows(
            store,
            "SELECT value FROM daily_metrics WHERE metric = $1 AND day = $2",
            "retention_d7",
            base.isoformat(),
        )[0][0]
        == 1
    )


def test_the_rollup_summarises_a_real_ingest(client, store):
    client.post(
        "/v1/events",
        json=_envelope(
            [
                _event(),
                _event(props={"name": "bots", "surface": "telegram"}),
                _event(
                    name="agent_turn",
                    props={
                        "kind": "chat",
                        "provider": "openai",
                        "model": "gpt-5",
                        "tool_calls": 4,
                        "outcome": "done",
                    },
                ),
                _event(
                    name="error",
                    props={
                        "where": "handlers.bots",
                        "exc_type": "ValueError",
                        "sig": "ab12",
                    },
                ),
                _event(
                    name="confirmation", props={"tool": "execute", "decision": "allow"}
                ),
            ]
        ),
    )
    asyncio.run(rollup.run(store))

    metrics = {
        (metric, dim): value
        for metric, dim, value in _rows(
            store, "SELECT metric, dim, value FROM daily_metrics"
        )
    }
    assert metrics[("event_rank", "command")] == 2
    assert metrics[("command_rank", "bots")] == 1
    assert metrics[("agent_provider", "openai")] == 1
    assert metrics[("agent_tool_calls_avg", "")] == 4
    assert metrics[("confirmation_decision", "allow")] == 1
    assert metrics[("error_group", "ValueError:ab12")] == 1
    assert metrics[("version_share", "54ad4dc")] == 1


def _statements(path):
    """Split a query file into runnable statements.

    Comments come out first: they are prose, and prose contains semicolons.
    """
    body = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def test_every_query_runs_and_answers_its_question(client, store):
    base = _seed_cohorts(store)
    client.post("/v1/events", json=_envelope())
    asyncio.run(rollup.run(store, today=base + timedelta(days=40)))

    results = {}
    for path in sorted(QUERIES.glob("*.sql")):
        results[path.name] = [_rows(store, sql) for sql in _statements(path)]

    assert set(results) == {
        "adoption.sql",
        "agent_economics.sql",
        "feature_usage.sql",
        "reliability.sql",
        "retention.sql",
    }
    # Each one has to return numbers, not merely parse.
    assert any(row[1] == "dau" for row in results["adoption.sql"][0])
    assert (base.isoformat(), 3, 2, 1, 0) in [
        tuple(row) for row in results["retention.sql"][0]
    ]
    assert any(row[1] == "command" for row in results["feature_usage.sql"][0])
    assert results["reliability.sql"][0]
    assert results["agent_economics.sql"]


def test_no_query_can_correlate_installs_through_user_hash():
    """`user_hash` is salted per install, so a global GROUP BY on it would be
    inventing a cross-install identity the client deliberately refused to give."""
    for path in QUERIES.glob("*.sql"):
        for sql in _statements(path):
            assert "user_hash" not in sql


def test_sqlite_retention_deletes_what_postgres_would_drop(store, monkeypatch):
    monkeypatch.setenv("TELEMETRY_RETENTION_DAYS", "30")
    old = datetime.now(timezone.utc) - timedelta(days=200)
    asyncio.run(
        store.execute(
            "INSERT INTO events (id, install_id, ts, received_at, name, props)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            uuid.uuid4().hex,
            uuid.uuid4().hex,
            old,
            old,
            "heartbeat",
            "{}",
        )
    )

    assert (
        asyncio.run(
            rollup.maintain_partitions(store, datetime.now(timezone.utc).date())
        )
        == []
    )
    assert _rows(store, "SELECT COUNT(*) FROM events")[0][0] == 0


def test_partition_names_run_a_month_ahead():
    """The Postgres path builds names from dates it computed, never from input."""
    assert rollup._next_month(rollup._month_start(datetime(2026, 12, 31).date())) == (
        datetime(2027, 1, 1).date()
    )


# ── Packaging ────────────────────────────────────────────────────────────


def test_asyncpg_is_an_extra_and_never_a_runtime_dependency():
    """`uv sync` without the extra must not pull a database driver into a bot."""
    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    project = manifest["project"]

    assert not any("asyncpg" in dep for dep in project["dependencies"])
    extras = project["optional-dependencies"]
    assert any("asyncpg" in dep for dep in extras["telemetry-server"])
