"""The learned-openers store (FEAT-073).

Everything here is arithmetic and a file, which is the point: the model only
ever names an intent, and every claim the openers make about "what you usually
ask" is decided by :mod:`condor.agents.starters`. So the tests are about the
ranking being a measurement — a repeat outranks a one-off, a stale hit falls
below a fresh pair, the file cannot grow without bound — and about the file
being unable to break a chat when it is damaged.

The store root is the autouse ``_isolated_runtime_root`` fixture's, so nothing
here goes near a developer's real agent directory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from condor.agents import starters
from condor.memory.paths import store_root

USER = 4242
AGENT = "market_making_expert"

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _intent(label: str, **kw) -> dict:
    return {"label": label, **kw}


def _labels(entries) -> list[str]:
    return [entry.label for entry in entries]


# ── Writing ──


def test_merge_inserts_a_new_intent_at_one():
    [entry] = starters.merge(USER, AGENT, [_intent("Rebalance my SOL-USDC range")], NOW)

    assert entry.slug == "rebalance_my_sol_usdc_range"
    assert entry.label == "Rebalance my SOL-USDC range"
    assert entry.count == 1
    assert entry.score == 1.0
    assert entry.last_seen == NOW


def test_merge_persists_to_starters_json_beside_the_memories():
    starters.merge(USER, AGENT, [_intent("Rebalance my SOL-USDC range")], NOW)

    path = store_root(USER, AGENT) / starters.STARTERS_FILENAME
    assert path.is_file()
    assert starters.read(USER, AGENT)[0].label == "Rebalance my SOL-USDC range"


def test_merge_is_per_agent_and_per_user():
    starters.merge(USER, AGENT, [_intent("Rebalance my range")], NOW)

    assert starters.read(USER, "orca_lp_expert") == []
    assert starters.read(9999, AGENT) == []


def test_a_second_sighting_increments_the_count_and_the_score():
    starters.merge(USER, AGENT, [_intent("Check my portfolio")], NOW)
    [entry] = starters.merge(USER, AGENT, [_intent("Check my portfolio")], NOW)

    assert entry.count == 2
    # No time passed, so nothing decayed: 1.0 aged by nothing, plus one.
    assert entry.score == 2.0


def test_the_label_keeps_its_first_form_but_the_hint_improves():
    starters.merge(
        USER, AGENT, [_intent("Check my portfolio", hint="Balances and PNL")], NOW
    )
    [entry] = starters.merge(
        USER,
        AGENT,
        # Same slug, differently capitalised, with a better second line.
        [_intent("CHECK my portfolio", hint="What moved since yesterday")],
        NOW,
    )

    assert entry.label == "Check my portfolio"
    assert entry.hint == "What moved since yesterday"


def test_an_intent_without_a_usable_label_is_dropped():
    assert (
        starters.merge(USER, AGENT, [_intent("   "), _intent("!!!"), {}, None], NOW)
        == []
    )


def test_an_icon_outside_the_vocabulary_is_dropped_not_stored():
    [entry] = starters.merge(
        USER, AGENT, [_intent("Rebalance", icon="unicorn", skill="clmm-rebalance")], NOW
    )

    assert entry.icon == ""
    assert entry.skill == "clmm_rebalance"


def test_a_known_icon_survives():
    [entry] = starters.merge(USER, AGENT, [_intent("Rebalance", icon="LP")], NOW)

    assert entry.icon == "lp"


# ── Ranking ──


def test_decay_actually_decays():
    starters.merge(USER, AGENT, [_intent("Rebalance")], NOW)
    one_halflife = NOW + timedelta(days=starters.HALFLIFE_DAYS)
    [entry] = starters.merge(USER, AGENT, [_intent("Rebalance")], one_halflife)

    # The first hit is worth half by now, so the pair is 1.5 rather than 2.0.
    assert entry.count == 2
    assert entry.score == 0.5 + 1.0


def test_a_repeat_outranks_an_older_single_hit():
    starters.merge(USER, AGENT, [_intent("Rebalance my range")], NOW)
    later = NOW + timedelta(days=1)
    starters.merge(USER, AGENT, [_intent("Show me the funding rates")], later)
    starters.merge(USER, AGENT, [_intent("Rebalance my range")], later)

    assert _labels(starters.read(USER, AGENT)) == [
        "Rebalance my range",
        "Show me the funding rates",
    ]


def test_two_halflives_ago_scores_below_twice_this_week():
    """The acceptance criterion, as arithmetic rather than as a claim."""
    long_ago = NOW - timedelta(days=60)
    starters.merge(USER, AGENT, [_intent("Audit my old grid")], long_ago)
    starters.merge(
        USER, AGENT, [_intent("Rebalance my range")], NOW - timedelta(days=3)
    )
    starters.merge(USER, AGENT, [_intent("Rebalance my range")], NOW)

    ranked = starters.read(USER, AGENT)
    assert _labels(ranked) == ["Rebalance my range", "Audit my old grid"]
    assert ranked[0].score > ranked[1].score


def test_top_returns_the_best_three():
    for i in range(5):
        # Repeat each one (5 - i) times, so the order is known by construction.
        for _ in range(5 - i):
            starters.merge(USER, AGENT, [_intent(f"Intent {i}")], NOW)

    assert _labels(starters.top(USER, AGENT)) == ["Intent 0", "Intent 1", "Intent 2"]
    assert len(starters.top(USER, AGENT, limit=10)) == 5


def test_the_file_is_capped_and_keeps_the_best():
    # One repeat for the intent we expect to survive the cull, then a flood.
    starters.merge(USER, AGENT, [_intent("Keeper")], NOW)
    starters.merge(USER, AGENT, [_intent("Keeper")], NOW)
    for i in range(starters.MAX_ENTRIES + 5):
        starters.merge(USER, AGENT, [_intent(f"Filler {i}")], NOW)

    ranked = starters.read(USER, AGENT)
    assert len(ranked) == starters.MAX_ENTRIES
    assert ranked[0].label == "Keeper"


# ── Damage ──


def test_no_file_reads_as_no_openers():
    assert starters.read(USER, AGENT) == []
    assert starters.top(USER, AGENT) == []


def test_a_corrupt_file_reads_as_no_openers():
    starters.merge(USER, AGENT, [_intent("Rebalance")], NOW)
    path = store_root(USER, AGENT) / starters.STARTERS_FILENAME
    path.write_text("{not json at all", encoding="utf-8")

    assert starters.read(USER, AGENT) == []


def test_one_unparseable_row_does_not_take_the_file_with_it():
    import json

    starters.merge(USER, AGENT, [_intent("Rebalance")], NOW)
    path = store_root(USER, AGENT) / starters.STARTERS_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"].insert(0, {"nothing": "usable"})
    path.write_text(json.dumps(data), encoding="utf-8")

    assert _labels(starters.read(USER, AGENT)) == ["Rebalance"]


def test_a_naive_timestamp_on_disk_still_ranks():
    import json

    starters.merge(USER, AGENT, [_intent("Rebalance")], NOW)
    path = store_root(USER, AGENT) / starters.STARTERS_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"][0]["last_seen"] = "2026-08-27T12:00:00"
    path.write_text(json.dumps(data), encoding="utf-8")

    [entry] = starters.merge(USER, AGENT, [_intent("Rebalance")], NOW)
    assert entry.count == 2


# ── Serving them ──


def _client(user_id: int = USER):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=user_id, username="u", first_name="U", role="user"
    )
    return TestClient(app)


def _agent(tmp_path, monkeypatch):
    from condor.agents import agent as agent_module
    from condor.agents.agent import AgentStore

    root = tmp_path / "agents"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(root))
    return AgentStore().create(name="Market Making Expert").slug


def test_a_user_with_nothing_learned_is_served_an_empty_list(tmp_path, monkeypatch):
    slug = _agent(tmp_path, monkeypatch)

    body = _client().get(f"/agents/{slug}/starters").json()

    assert body == {"starters": []}


def test_the_endpoint_serves_the_learned_rows_best_first(tmp_path, monkeypatch):
    slug = _agent(tmp_path, monkeypatch)
    starters.merge(
        USER, slug, [_intent("Rebalance my range", hint="Re-centre it")], NOW
    )
    starters.merge(USER, slug, [_intent("Rebalance my range")], NOW)
    starters.merge(USER, slug, [_intent("Show me funding", icon="chart")], NOW)

    rows = _client().get(f"/agents/{slug}/starters").json()["starters"]

    assert [row["title"] for row in rows] == ["Rebalance my range", "Show me funding"]
    assert rows[0]["prompt"] == "Rebalance my range"
    assert rows[0]["hint"] == "Re-centre it"
    assert rows[1]["icon"] == "chart"


def test_the_endpoint_serves_at_most_three(tmp_path, monkeypatch):
    slug = _agent(tmp_path, monkeypatch)
    for i in range(6):
        starters.merge(USER, slug, [_intent(f"Intent {i}")], NOW)

    assert len(_client().get(f"/agents/{slug}/starters").json()["starters"]) == 3


def test_one_users_habits_are_not_served_to_another(tmp_path, monkeypatch):
    slug = _agent(tmp_path, monkeypatch)
    starters.merge(USER, slug, [_intent("Rebalance my range")], NOW)

    assert _client(user_id=9999).get(f"/agents/{slug}/starters").json() == {
        "starters": []
    }


def test_an_unknown_agent_is_a_404(tmp_path, monkeypatch):
    _agent(tmp_path, monkeypatch)

    assert _client().get("/agents/nobody_here/starters").status_code == 404
