"""Telemetry (FEAT-023) — the tests that make the privacy promise checkable.

The interesting assertions here are the negative ones. Most of this file exists
to prove that things do *not* happen: that a default install emits nothing, that
forbidden values cannot reach an envelope even when a caller hands them over,
and that a tap can never take the host down with it.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from condor.telemetry import consent, context, emitter, outbox, schema, taps


@pytest.fixture
def install(tmp_path, monkeypatch):
    """An isolated install: its own config.yml, its own runtime dir, no env."""
    import config_manager as cm_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", None, raising=False)
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "data"))
    cm_module.ConfigManager.reset_instance()
    emitter.discard_buffer()
    emitter.set_hosted(True)
    consent.refresh()
    yield tmp_path
    cm_module.ConfigManager.reset_instance()
    emitter.discard_buffer()
    consent.refresh()


def _grant(level="usage"):
    from config_manager import get_config_manager

    get_config_manager()  # materialize config.yml in the tmp cwd
    consent.grant(level)


# ── The default is the ping floor ────────────────────────────────────────


def test_a_fresh_install_pings_and_says_nothing_more(install):
    """No consent recorded means install counting only — the ping floor."""
    assert consent.state() == consent.UNKNOWN
    assert consent.level() == consent.PING

    for name in schema.EVENTS:
        emitter.emit(name, surface="telegram")

    names = {e["name"] for e in emitter.drain()[0]}
    assert names == set(schema.PING_EVENTS)


def test_usage_events_write_nothing_to_disk_at_the_ping_floor(install):
    """Acceptance criterion: no telemetry directory is created at all by
    activity that the install has no consent to report."""
    emitter.set_hosted(False)  # the spooling path, which is the one that does I/O
    emitter.emit("command", name="portfolio", surface="telegram")
    assert not outbox.root().exists()
    assert not (install / "config.yml").exists()


def test_env_off_overrides_a_granted_consent(install, monkeypatch):
    _grant("usage")
    assert consent.level() == consent.USAGE

    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", "off", raising=False)
    consent.refresh()

    assert consent.level() == consent.OFF
    emitter.emit("command", name="bots", surface="telegram")
    assert emitter.buffered() == 0


def test_a_nonsense_env_level_is_ignored_not_obeyed(install, monkeypatch):
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", "yes-please", raising=False)
    consent.refresh()
    assert consent.level() == consent.PING  # the floor, not whatever was typed


def test_ping_level_sends_only_the_adoption_events(install):
    _grant("ping")
    emitter.emit("heartbeat", uptime_h=1.0)
    emitter.emit("command", name="portfolio", surface="telegram")
    emitter.emit("trade", venue="cex", connector="binance", side="buy")

    names = [e["name"] for e in emitter.drain()[0]]
    assert names == ["heartbeat"]


# ── Nothing transmits ────────────────────────────────────────────────────


def test_usage_activity_never_transmits_without_consent(install, monkeypatch):
    """A full session of activity must produce zero outbound usage events."""
    calls = []
    monkeypatch.setattr(outbox, "post", lambda env: calls.append(env))

    for _ in range(50):
        emitter.emit("command", name="portfolio", surface="telegram")
    taps.on_error(RuntimeError("boom"), where="test")

    assert asyncio.run(emitter.flush("test")) == 0
    assert calls == []
    assert not outbox.root().exists()


def test_the_ping_floor_actually_flushes_without_an_answer(install, monkeypatch):
    """An unanswered install still delivers its adoption events — the count is
    only worth anything if it leaves the machine."""
    sent = []

    async def ok_post(envelope):
        sent.append(envelope)
        return True

    monkeypatch.setattr(outbox, "endpoint", lambda: "http://collector.invalid")
    monkeypatch.setattr(outbox, "post", ok_post)

    assert consent.state() == consent.UNKNOWN
    emitter.emit("install")
    assert asyncio.run(emitter.flush("test")) == 1
    assert [e["name"] for env in sent for e in env["events"]] == ["install"]
    assert sent[0]["level"] == consent.PING


def test_the_endpoint_is_fixed_and_not_configurable(install, monkeypatch):
    """The collector address is compiled in; no env var may redirect it."""
    monkeypatch.setenv("CONDOR_TELEMETRY_URL", "http://attacker.invalid/v1/events")

    assert outbox.endpoint() == outbox.COLLECTOR_URL
    assert outbox.COLLECTOR_URL == "https://telemetry.hummingbot.org/v1/events"


def test_no_endpoint_means_events_only_ever_reach_a_local_file(install, monkeypatch):
    """With delivery impossible, events park in the capped outbox instead of leaving."""
    _grant("usage")
    monkeypatch.setattr(outbox, "endpoint", lambda: None)
    emitter.emit("command", name="portfolio", surface="telegram")

    assert asyncio.run(emitter.flush("test")) == 0

    stashed = outbox.take_stashed()
    assert [e["name"] for e in stashed] == ["command"]


def test_undelivered_events_are_retried_and_never_duplicated(install, monkeypatch):
    _grant("usage")
    monkeypatch.setattr(outbox, "endpoint", lambda: "http://collector.invalid")

    attempts = []

    async def failing_post(envelope):
        attempts.append(envelope)
        return False

    monkeypatch.setattr(outbox, "post", failing_post)
    emitter.emit("command", name="bots", surface="telegram")
    assert asyncio.run(emitter.flush("first")) == 0

    sent = []

    async def ok_post(envelope):
        sent.append(envelope)
        return True

    monkeypatch.setattr(outbox, "post", ok_post)
    assert asyncio.run(emitter.flush("retry")) == 1

    ids = [e["id"] for env in sent for e in env["events"]]
    assert len(ids) == len(set(ids)) == 1


def test_the_outbox_respects_its_cap(install):
    _grant("usage")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    events = [
        {"id": str(i), "ts": now, "name": "command", "props": {}}
        for i in range(outbox.MAX_OUTBOX_EVENTS + 250)
    ]
    outbox.stash(events)
    assert len(outbox.take_stashed()) == outbox.MAX_OUTBOX_EVENTS


def test_withdrawing_usage_consent_destroys_what_was_collected(install):
    _grant("usage")
    emitter.emit("command", name="portfolio", surface="telegram")
    outbox.stash(
        [
            {
                "id": "x",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "name": "command",
            }
        ]
    )
    assert outbox.outbox_path().exists()

    consent.set_level("ping")

    assert consent.level() == consent.PING
    assert emitter.buffered() == 0
    assert not outbox.outbox_path().exists()


def test_off_is_not_a_grantable_answer(install):
    """The consent form has no "off": a stale or unrecognized answer lands on
    ping, the floor, and set_level rejects "off" outright."""
    from config_manager import get_config_manager

    get_config_manager()  # materialize config.yml in the tmp cwd
    assert consent.grant("off") == consent.PING
    assert consent.state() == consent.GRANTED
    assert consent.set_level("off") == consent.PING  # rejected, level unchanged
    assert consent.level() == consent.PING


# ── A refusal is honoured ────────────────────────────────────────────────


def _refuse_like_an_older_build(tmp_path):
    """Leave behind the `config.yml` the shipped "No thanks" button wrote, then
    read it the way a fresh boot of the current build would."""
    import yaml

    import config_manager as cm_module

    (tmp_path / "config.yml").write_text(
        yaml.safe_dump(
            {
                "telemetry": {
                    "consent": "denied",
                    "level": "off",
                    "decided_at": "2026-01-01T00:00:00Z",
                }
            }
        )
    )
    cm_module.ConfigManager.reset_instance()
    consent.refresh()


def test_a_refusal_recorded_by_an_older_build_survives_the_upgrade(install):
    """The regression this file exists to prevent: an admin said no under the
    build that had a "No thanks" button, and the upgrade that introduced the
    ping floor must not read that recorded no as silence."""
    from condor import telemetry

    _refuse_like_an_older_build(install)

    assert consent.state() == consent.DENIED
    assert consent.level() == consent.OFF
    assert telemetry.init(hosted=True) == consent.OFF

    # Nothing materialized on the way through: no identity, no count, no event.
    assert consent.install_id() == ""
    emitter.emit("install")
    emitter.emit("command", name="portfolio", surface="telegram")
    taps.on_error(RuntimeError("boom"), where="test")

    assert emitter.buffered() == 0
    assert asyncio.run(emitter.flush("test")) == 0
    assert not outbox.root().exists()
    # And it is not asked again — the answer is on file.
    assert consent.should_prompt("1.2.3") is False


def test_the_environment_can_still_opt_a_refusing_install_back_in(install, monkeypatch):
    """The override is authoritative in both directions, so an operator can
    re-enable an install that refused without hand-editing `config.yml`."""
    _refuse_like_an_older_build(install)
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", "ping", raising=False)
    consent.refresh()

    assert consent.level() == consent.PING
    assert consent.state() == consent.DENIED  # the stored answer is left intact


def test_turning_reporting_off_is_a_withdrawal_not_a_pause(install):
    """`deny()` is the in-product off switch: it destroys what was collected
    and, unlike the env kill switch, is durable."""
    _grant("usage")
    emitter.emit("command", name="portfolio", surface="telegram")
    outbox.stash(
        [
            {
                "id": "x",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "name": "command",
            }
        ]
    )
    assert outbox.outbox_path().exists()

    assert consent.deny() == consent.OFF

    assert consent.state() == consent.DENIED
    assert consent.level() == consent.OFF
    assert emitter.buffered() == 0
    assert not outbox.outbox_path().exists()

    emitter.emit("command", name="portfolio", surface="telegram")
    assert emitter.buffered() == 0


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.text = None

    async def answer(self):
        return None

    async def edit_message_text(self, text, **kwargs):
        self.text = text


class _FakeTap:
    """An update carrying one inline-keyboard tap from the admin."""

    def __init__(self, data):
        self.callback_query = _FakeQuery(data)
        self.effective_user = type("U", (), {"id": 7})()


def test_a_stale_no_thanks_tap_is_recorded_as_the_refusal_it_is(install, monkeypatch):
    """The prompt no longer offers "off", so such a tap can only come from a
    message an older build sent — where the button read "No thanks". Rounding
    it up to the floor would be the same defect as ignoring a stored one."""
    from condor.telemetry import prompt
    from config_manager import get_config_manager

    _grant("usage")
    # The real ConfigManager, so the refusal genuinely lands on disk; only the
    # "is this the admin" question is answered for us.
    monkeypatch.setattr(type(get_config_manager()), "is_admin", lambda self, uid: True)

    tap = _FakeTap("telemetry:off")
    asyncio.run(prompt.callback_handler(tap, None))

    assert consent.state() == consent.DENIED
    assert consent.level() == consent.OFF
    assert "report nothing" in tap.callback_query.text
    emitter.emit("command", name="portfolio", surface="telegram")
    assert emitter.buffered() == 0


def test_a_refusing_install_can_opt_back_in_from_the_dashboard(install):
    """The way back on is a level, not a `.env` edit — otherwise "no" is a trap."""
    _refuse_like_an_older_build(install)

    assert consent.set_level(consent.PING) == consent.PING
    assert consent.state() == consent.GRANTED
    assert consent.level() == consent.PING
    assert consent.install_id()  # identity is materialized only now


# ── The leak test ────────────────────────────────────────────────────────

FORBIDDEN = {
    "amount": 12345.67,
    "balance": 98765.43,
    "pnl": -420.0,
    "api_key": "sk-live-DEADBEEFCAFE",
    "secret_key": "topsecret",
    "wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
    "wallet_address": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    "trading_pair": "SOL-USDC",
    "pair": "BTC-USDT",
    "server_url": "http://10.0.0.4:8000",
    "server_name": "my-vps",
    "hostname": "trading-box.local",
    "user_id": 843214321,
    "username": "federico",
    "chat_id": 12345,
    "prompt": "buy me some sol",
    "reply": "sure thing",
    "order_id": "OID-99182",
    "message": "Insufficient balance: 12.5 SOL",
    "big": "x" * 10_000,
}


def test_no_forbidden_value_survives_any_declared_event(install):
    """Feed every event a props dict stuffed with everything we promised never
    to collect, and assert none of it comes out the other side."""
    _grant("usage")

    for name in schema.EVENTS:
        clean = schema.sanitize(name, dict(FORBIDDEN))
        blob = json.dumps(clean)

        for key, value in FORBIDDEN.items():
            assert key not in clean, f"{name} let through the key {key!r}"
            assert str(value) not in blob, f"{name} let through the value of {key!r}"

        for value in clean.values():
            if isinstance(value, str):
                assert len(value) <= schema.MAX_STR
            if isinstance(value, list):
                assert all(len(v) <= schema.MAX_STR for v in value)


def test_an_undeclared_property_is_dropped_not_passed_through(install):
    clean = schema.sanitize(
        "trade",
        {"venue": "cex", "connector": "binance", "amount": 5.0, "pair": "SOL-USDC"},
    )
    assert clean == {"venue": "cex", "connector": "binance"}


def test_an_unknown_enum_value_is_snapped_to_other(install):
    clean = schema.sanitize(
        "command", {"name": "totally_made_up", "surface": "carrier_pigeon"}
    )
    assert clean == {"name": "other", "surface": "other"}


def test_a_long_free_form_string_is_truncated(install):
    clean = schema.sanitize("upstream_error", {"service": "llm", "op": "z" * 500})
    assert len(clean["op"]) == schema.MAX_STR


# ── emit() never raises ──────────────────────────────────────────────────


class Unserializable:
    def __repr__(self):  # pragma: no cover - must never be reached in a payload
        raise RuntimeError("even repr explodes")


def test_emit_survives_everything_a_caller_can_do_to_it(install):
    _grant("usage")

    emitter.emit("no_such_event_name", whatever=1)
    emitter.emit("command", name=Unserializable(), surface=Unserializable())
    emitter.emit("command", **{})
    emitter.emit("error", frames=None, sig=None, where=None)

    # And from a thread that has no event loop at all.
    failures = []

    def in_thread():
        try:
            emitter.emit("command", name="portfolio", surface="telegram")
        except BaseException as exc:  # pragma: no cover - the assertion is below
            failures.append(exc)

    thread = threading.Thread(target=in_thread)
    thread.start()
    thread.join()
    assert failures == []


def test_a_broken_schema_cannot_break_the_caller(install, monkeypatch):
    """The blast-radius test: telemetry failing must never propagate."""
    _grant("usage")

    def explode(*_a, **_kw):
        raise ValueError("schema is on fire")

    monkeypatch.setattr(schema, "sanitize", explode)
    emitter.emit("command", name="portfolio", surface="telegram")  # must not raise

    monkeypatch.setattr(consent, "level", explode)
    emitter.emit("command", name="portfolio", surface="telegram")  # must not raise

    # Undone here, not at teardown: the `install` fixture unwinds after
    # monkeypatch and would otherwise call the exploding stub itself.
    monkeypatch.undo()


def test_a_broken_tap_cannot_break_a_handler(install, monkeypatch):
    _grant("usage")
    monkeypatch.setattr(taps, "emit", lambda *a, **kw: 1 / 0)

    taps.on_error(RuntimeError("x"), where="test")
    taps.confirmation("manage_bots", "allow")
    taps.strategy_run({"execution_mode": "loop"}, ticks=3)
    taps.routine_run(
        routine="x", kind="oneshot", trigger="manual", duration_ms=1, ok=True
    )
    taps.agent_turn(kind="chat")
    taps.version_change("aaa", "bbb", 2)


def test_the_web_tap_lets_route_exceptions_through(install):
    """Swallowing here would turn a 500 into "no response returned"."""
    _grant("usage")

    class Req:
        url = type("U", (), {"path": "/api/v1/bots"})()
        method = "GET"
        scope = {}

    async def boom(_request):
        raise RuntimeError("the route failed")

    with pytest.raises(RuntimeError, match="the route failed"):
        asyncio.run(taps.web_tap(Req(), boom))


# ── The taps report shapes, not content ──────────────────────────────────


def test_an_error_reports_its_type_and_a_hash_never_its_message(install):
    _grant("usage")
    secret = "Insufficient balance 12.5 SOL on wallet 7xKXtg2CW87d97TXJSD"
    try:
        raise ValueError(secret)
    except ValueError as exc:
        taps.on_error(exc, where="handlers.trading", surface="telegram")

    event = emitter.drain()[0][0]
    blob = json.dumps(event)
    assert event["props"]["exc_type"] == "ValueError"
    assert secret not in blob
    assert "12.5" not in blob
    assert len(event["props"]["sig"]) == 12
    # Frames are repo-relative, so no home directory and no username.
    for frame in event["props"].get("frames", []):
        assert not frame.startswith("/")


def test_a_callback_verb_drops_the_identifier_after_it(install):
    """`admin:approve_12345` must report `approve`, never the id."""
    assert taps._verb_of("approve_12345") == "approve"
    assert taps._verb_of("view_MyPrivateBotName") == "view"
    assert taps._verb_of("set_slippage") == "set_slippage"


def test_the_user_hash_is_not_the_telegram_id_and_is_install_scoped(install):
    _grant("usage")
    first = taps.user_hash(843214321)
    assert first and "843214321" not in first
    assert len(first) == 16
    assert taps.user_hash(843214321) == first

    # A different install secret yields a different hash for the same person.
    consent._update(install_secret="a-completely-different-secret")
    assert taps.user_hash(843214321) != first


def test_a_private_routine_is_reported_as_custom(install):
    assert taps.shipped_routine_name("definitely_not_a_shipped_routine") == "custom"


def test_the_install_context_carries_no_identity(install):
    _grant("usage")
    blob = json.dumps(context.envelope([], 0, "usage"))
    import getpass
    import socket

    for identifying in (socket.gethostname(), getpass.getuser(), str(install)):
        if identifying:
            assert identifying not in blob


def test_the_envelope_says_which_surface_the_install_runs(install, monkeypatch):
    """`mode` is one of two fixed names — the adoption signal local mode was
    built for, and nothing that could point at a host or a chat."""
    monkeypatch.setattr("utils.config.CONDOR_MODE", "local", raising=False)
    assert context.config_shape()["mode"] == "local"
    monkeypatch.setattr("utils.config.CONDOR_MODE", "telegram", raising=False)
    assert context.config_shape()["mode"] == "telegram"


# ── Every install is counted ─────────────────────────────────────────────


def test_a_fresh_install_is_counted_without_anyone_answering(install):
    """The claim that gives this feature its floor. `install` used to be emitted
    only from the Telegram consent callback, so an install that never answered
    — every local-mode one — was never counted at all."""
    import condor.telemetry as telemetry

    telemetry.init(hosted=True)

    assert consent.state() == consent.UNKNOWN  # nobody answered anything
    assert [e["name"] for e in emitter.drain()[0]] == ["install"]


def test_an_install_is_counted_once_however_many_times_it_boots(install):
    import condor.telemetry as telemetry

    telemetry.init(hosted=True)
    emitter.drain()

    telemetry.init(hosted=True)
    telemetry.init(hosted=True)
    assert emitter.drain()[0] == []


def test_answering_the_prompt_does_not_count_the_install_a_second_time(install):
    import condor.telemetry as telemetry

    telemetry.init(hosted=True)
    emitter.drain()

    consent.grant("usage")
    assert consent.mark_install_reported() is False


def test_an_install_silenced_by_the_environment_is_never_counted(install, monkeypatch):
    """`CONDOR_TELEMETRY=off` is the kill switch, and counting is not exempt
    from it — nothing is recorded and config.yml is left as it was found."""
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", "off", raising=False)
    import condor.telemetry as telemetry

    assert telemetry.init(hosted=True) == consent.OFF
    assert emitter.drain()[0] == []
    assert not (install / "config.yml").exists()


# ── Asking, on both surfaces ─────────────────────────────────────────────


def test_telegram_and_the_dashboard_disclose_exactly_the_same_thing():
    """Two prompts mean two chances to disagree about what is collected. The
    dashboard card renders `DISCLOSURE`; the Telegram message is built from it."""
    from condor.telemetry.prompt import _TEXT, DISCLOSURE

    assert DISCLOSURE["headline"] in _TEXT
    assert DISCLOSURE["always_on"] in _TEXT
    assert DISCLOSURE["optional"] in _TEXT
    assert DISCLOSURE["doc"] in _TEXT
    for never in DISCLOSURE["never"]:
        assert never in _TEXT


def test_the_prompt_offers_no_way_to_switch_counting_off():
    """`off` is the operator's env kill switch, never a button — on either
    surface. A stray option here would quietly make the floor optional."""
    from condor.telemetry.prompt import DISCLOSURE, OPTIONS

    levels = {option["level"] for option in OPTIONS}
    assert levels == set(consent.ANSWER_LEVELS)
    assert consent.OFF not in levels
    assert [o["level"] for o in DISCLOSURE["options"]] == [o["level"] for o in OPTIONS]


# ── Out of process ───────────────────────────────────────────────────────


def test_a_process_without_a_flush_job_spools_to_its_own_file(install):
    """The MCP server has its own interpreter and no job_queue; an in-memory
    ring there would be silently lost."""
    _grant("usage")
    emitter.set_hosted(False)
    emitter.emit("mcp_tool", tool="manage_bots", ok=True, duration_ms=12)

    assert emitter.buffered() == 0
    assert outbox.spool_path().exists()

    emitter.set_hosted(True)
    drained = outbox.drain_spools()
    assert [e["name"] for e in drained] == ["mcp_tool"]
    assert not outbox.spool_path().exists()


# ── Cost and limits ──────────────────────────────────────────────────────


def test_the_rate_limiter_confesses_instead_of_flooding(install):
    _grant("usage")
    for _ in range(emitter.RATE_PER_MIN + 40):
        emitter.emit("error", where="loop", exc_type="ValueError", sig="abc")

    events, dropped = emitter.drain()
    assert len(events) == emitter.RATE_PER_MIN
    assert dropped == 40


def test_emit_costs_under_50_microseconds_on_the_hot_path(install):
    _grant("usage")
    iterations = 2000
    started = time.perf_counter()
    for _ in range(iterations):
        emitter.emit("command", name="portfolio", surface="telegram")
    per_call = (time.perf_counter() - started) / iterations
    assert per_call < 50e-6, f"emit() took {per_call * 1e6:.1f}us"


def test_emit_is_free_below_usage(install):
    """The gate a trading path actually pays for: at the ping floor a usage
    event is rejected by the schema gate before any work happens."""
    iterations = 2000
    started = time.perf_counter()
    for _ in range(iterations):
        emitter.emit("command", name="portfolio", surface="telegram")
    per_call = (time.perf_counter() - started) / iterations
    assert per_call < 50e-6


def test_the_ring_cannot_grow_without_bound(install):
    _grant("usage")
    for _ in range(emitter.RING_MAX * 2):
        emitter._buffer.append({"id": "x", "name": "command", "props": {}})
    assert emitter.buffered() == emitter.RING_MAX


# ── The Telegram seam ────────────────────────────────────────────────────


class _FakeUpdate:
    """Just enough of an Update for the tap, which only ever reads."""

    def __init__(self, text: str = "", callback: str = "", user_id: int = 55):
        self.effective_user = type("U", (), {"id": user_id})()
        self.message = type("M", (), {"text": text})() if text else None
        self.callback_query = type("C", (), {"data": callback})() if callback else None


def test_the_telegram_tap_reports_the_command_never_the_message(install):
    _grant("usage")
    asyncio.run(taps.telegram_tap(_FakeUpdate(text="/trade SOL-USDC buy 12.5"), None))

    events = [e for e in emitter.drain()[0] if e["name"] == "command"]
    assert len(events) == 1
    blob = json.dumps(events[0])
    assert events[0]["props"]["name"] == "trade"
    assert "SOL-USDC" not in blob and "12.5" not in blob


def test_an_unknown_command_does_not_widen_the_value_space(install):
    _grant("usage")
    asyncio.run(taps.telegram_tap(_FakeUpdate(text="/some_private_fork_command"), None))
    events = [e for e in emitter.drain()[0] if e["name"] == "command"]
    assert events[0]["props"]["name"] == "other"


def test_a_callback_reports_module_and_verb_only(install):
    _grant("usage")
    asyncio.run(
        taps.telegram_tap(_FakeUpdate(callback="admin:approve_843214321"), None)
    )
    event = emitter.drain()[0][0]
    assert event["name"] == "action"
    assert event["props"] == {
        "module": "admin",
        "verb": "approve",
        "surface": "telegram",
    }
    assert "843214321" not in json.dumps(event)


def test_the_telegram_tap_emits_nothing_before_consent(install):
    """The acceptance criterion, at the seam rather than at the emitter:
    commands and callbacks are usage events, and the ping floor drops them."""
    assert consent.state() == consent.UNKNOWN
    for update in (
        _FakeUpdate(text="/portfolio"),
        _FakeUpdate(text="/keys"),
        _FakeUpdate(callback="bots:deploy_mybot"),
    ):
        asyncio.run(taps.telegram_tap(update, None))
    assert emitter.buffered() == 0
    assert not outbox.root().exists()


def test_the_telegram_tap_never_raises_into_the_handler(install):
    _grant("usage")

    class Hostile:
        @property
        def effective_user(self):
            raise RuntimeError("nope")

    asyncio.run(taps.telegram_tap(Hostile(), None))  # must not raise
