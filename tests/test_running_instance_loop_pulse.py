"""A running loop has to say what it is doing, not just that it is running.

``RunningInstance`` is what every strategy view reads a live session off. It
carried the config (frequency, budget, risk limits) and the counters, but not
the *pulse* — when the last tick ran, what it did, whether it failed — even
though ``TickEngine.get_info()`` had computed all of it. So a strategy page
could say "running" and nothing more, and the only surface that knew better was
the fleet band, which reads the engine directly.

These pin the pulse onto the wire model, from the same helpers the band uses.
"""

from types import SimpleNamespace

from condor.web.routes import agents as agents_routes


def _engine(**info):
    base = {
        "agent_id": "brigado.brl_mm_4",
        "session_num": 4,
        "status": "running",
        "tick_count": 14,
        "daily_pnl": 0.0,
        "frequency_sec": 60,
        "last_tick_at": 1_800_000_000.0,
        "max_ticks": 100,
        "last_error": "",
    }
    base.update(info)
    return SimpleNamespace(
        get_info=lambda: base,
        journal=None,
        session_dir=None,
        agent_id=base["agent_id"],
    )


def test_a_live_instance_reports_its_cadence_and_last_tick():
    inst = agents_routes._instance_from_engine(_engine(), {})

    assert inst.tick_count == 14
    assert inst.frequency_sec == 60
    # The two facts a countdown is built from. Without them the UI can only
    # print "running" and a number that never visibly changes.
    assert inst.last_tick_at == 1_800_000_000.0
    assert inst.max_ticks == 100


def test_a_tick_that_failed_is_carried_not_swallowed():
    inst = agents_routes._instance_from_engine(
        _engine(last_error="No API client available"), {}
    )

    assert inst.last_error == "No API client available"


def test_an_engine_that_never_ticked_reports_zero_not_none():
    """0, not null: `0 + frequency` is 1970, and the UI keys "no tick yet" off it."""
    inst = agents_routes._instance_from_engine(
        _engine(last_tick_at=0.0, tick_count=0), {}
    )

    assert inst.last_tick_at == 0.0
    assert inst.tick_count == 0
    assert inst.last_did is None
    assert inst.last_action == ""


def test_the_deed_comes_from_the_same_reader_the_fleet_band_uses(monkeypatch):
    """One loop must not describe itself differently on two screens."""
    deed = {
        "tick": 14,
        "at": 1.0,
        "tool": "manage_bots",
        "verb": "manage_bots:deploy",
        "summary": "Deploy pmm_btc_brl",
        "ok": True,
        "error": "",
    }
    monkeypatch.setattr("condor.agents.fleet_map.read_last_did", lambda engine: deed)
    monkeypatch.setattr(
        "condor.agents.fleet_map.read_last_action", lambda journal: "spreads widened"
    )

    inst = agents_routes._instance_from_engine(_engine(), {})

    assert inst.last_did == deed
    assert inst.last_action == "spreads widened"
