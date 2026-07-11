"""Unit tests for the skill-curation loop (refactor-05 Phase 3).

Capture → curate → promote: learnings promotion, auto-trigger preconditions,
the curation flow (session persistence, prompt mandates, scoped git commit,
notification with promotion proposals), and the engine's session-end hook.
"""

import asyncio
from types import SimpleNamespace

import yaml

from condor.agents import agent as agent_module
from condor.agents import curation as curation_module
from condor.agents import journal as journal_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import Agent
from condor.agents.curation import (
    build_curation_prompt,
    count_unpromoted_learnings,
    should_curate,
    start_skill_curation,
)
from condor.agents.journal import JournalManager, allocate_session_dir, write_session_meta


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(journal_module, "_DATA_ROOT", tmp_path)


def _make_agent(tmp_path, monkeypatch, slug="acme") -> Agent:
    _patch_roots(monkeypatch, tmp_path)
    a = Agent(slug=slug, name="Acme", agent_key="claude-code")
    a.agent_dir.mkdir(parents=True, exist_ok=True)
    (a.agent_dir / "AGENT.md").write_text(f"---\nname: {slug}\n---\n\nBody.\n")
    return a


_LEARNINGS = [
    "JTO order book thins dramatically after 22:00 UTC",
    "grid fills lag several seconds on OKX perpetual venues",
    "funding flips negative before Asian session opens usually",
    "spread floor 0.0012 survives fee drag comfortably",
]


def _seed_learnings(agent_dir, n=4):
    _, session_dir = allocate_session_dir(agent_dir)
    write_session_meta(session_dir, {"kind": "tick_loop", "strategy": "s1", "status": "stopped"})
    jm = JournalManager(f"{agent_dir.name}_1", session_dir=session_dir, agent_dir=agent_dir)
    for text in _LEARNINGS[:n]:
        jm.append_learning(text)
    return jm


# ── learnings promotion ──


def test_promote_learning_moves_to_promoted_section(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    jm = _seed_learnings(agent.agent_dir, n=3)
    assert count_unpromoted_learnings(agent.agent_dir) == 3

    assert jm.promote_learning("grid fills lag several seconds on OKX perpetual venues") is True
    text = (agent.agent_dir / "learnings.md").read_text()
    assert "## Promoted" in text
    assert count_unpromoted_learnings(agent.agent_dir) == 2
    # The promoted line is preserved, timestamped, under Promoted.
    promoted = text.split("## Promoted")[1]
    assert "grid fills lag" in promoted

    # Unknown text → False, nothing changes.
    assert jm.promote_learning("never existed") is False
    assert count_unpromoted_learnings(agent.agent_dir) == 2


# ── auto-trigger preconditions ──


def test_should_curate_preconditions(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    # No learnings, no sessions → no pass.
    assert should_curate(agent.agent_dir) is False

    _seed_learnings(agent.agent_dir, n=4)  # creates 1 tick session + 4 learnings
    # Enough learnings but only 1 tick session → still no (≥2-session evidence rule).
    assert should_curate(agent.agent_dir) is False

    _, d2 = allocate_session_dir(agent.agent_dir)
    write_session_meta(d2, {"kind": "tick_loop", "strategy": "s1", "status": "stopped"})
    assert should_curate(agent.agent_dir) is True


# ── the curation flow ──


def test_curation_flow_end_to_end(tmp_path, monkeypatch):
    from condor.agents import run as run_module
    from condor.agents.run import RunResult

    agent = _make_agent(tmp_path, monkeypatch)
    _seed_learnings(agent.agent_dir, n=3)

    seen = {}

    async def fake_run(agent_arg, prompt, *, permission_policy, **kw):
        seen["prompt"] = prompt
        seen["policy"] = permission_policy
        return RunResult(
            text=(
                "Patched the floor.\n\nCHANGELOG:\n- grid-rules: raised spread floor\n\n"
                "PROMOTION PROPOSALS:\n- executor fee table belongs in shared\n"
            ),
            events=[{"type": "text", "text": "done"}],
        )

    commits = {}

    def fake_commit(slug, session_id, changelog):
        commits["slug"] = slug
        commits["session_id"] = session_id
        commits["changelog"] = changelog
        return "committed abc123"

    notes = []

    async def fake_notify(chat_id, text, bot=None):
        notes.append(text)

    monkeypatch.setattr(run_module, "run_agent", fake_run)
    monkeypatch.setattr(curation_module, "_git_commit_skills", fake_commit)
    monkeypatch.setattr(curation_module, "_notify", fake_notify)

    async def scenario():
        session_id = await start_skill_curation(
            agent_slug="acme", user_id=1, chat_id=42, trigger="test"
        )
        # Let the background task finish.
        for _ in range(20):
            await asyncio.sleep(0)
        return session_id

    session_id = asyncio.run(scenario())
    assert session_id == "acme_2"  # session 1 was the seeded tick session

    # Mandates + inputs are in the prompt; the pass runs unattended (AUTO).
    assert 'action="patch"' in seen["prompt"]
    assert "AT LEAST TWO sessions" in seen["prompt"]
    assert "JTO order book thins" in seen["prompt"]
    assert session_id in seen["prompt"]  # the id used for promote_learning calls
    assert seen["policy"] is None

    # Session persisted with kind: curation and finalized.
    session_dir = agent.agent_dir / "sessions" / "session_2"
    meta = yaml.safe_load((session_dir / "meta.yml").read_text())
    assert meta["kind"] == "curation"
    assert meta["status"] == "done"
    assert (session_dir / "transcript.md").exists()

    # Scoped git commit with the changelog; notification carries proposals.
    assert commits["slug"] == "acme"
    assert commits["session_id"] == session_id
    assert "raised spread floor" in commits["changelog"]
    assert len(notes) == 1
    assert "Promotion proposals" in notes[0]
    assert "fee table belongs in shared" in notes[0]
    assert "committed abc123" in notes[0]


def test_curation_flow_records_error(tmp_path, monkeypatch):
    from condor.agents import run as run_module

    agent = _make_agent(tmp_path, monkeypatch)

    async def boom(*a, **kw):
        raise RuntimeError("model exploded")

    async def fake_notify(chat_id, text, bot=None):
        fake_notify.text = text

    monkeypatch.setattr(run_module, "run_agent", boom)
    monkeypatch.setattr(curation_module, "_notify", fake_notify)

    async def scenario():
        sid = await start_skill_curation(
            agent_slug="acme", user_id=1, chat_id=42, trigger="test"
        )
        for _ in range(20):
            await asyncio.sleep(0)
        return sid

    sid = asyncio.run(scenario())
    meta = yaml.safe_load(
        (agent.agent_dir / "sessions" / f"session_{sid.rsplit('_', 1)[1]}" / "meta.yml").read_text()
    )
    assert meta["status"] == "error"
    assert "model exploded" in meta["error"]
    assert "failed" in fake_notify.text


# ── engine session-end hook ──


def _hook_stub(config, is_experiment=False):
    return SimpleNamespace(
        is_experiment=is_experiment,
        config=config,
        agent=SimpleNamespace(slug="acme", agent_dir=None),
        agent_id="acme_1",
        user_id=1,
        chat_id=2,
    )


def test_maybe_curate_fires_when_preconditions_met(tmp_path, monkeypatch):
    from condor.agents.engine import TickEngine

    agent = _make_agent(tmp_path, monkeypatch)
    started = {}

    monkeypatch.setattr(curation_module, "should_curate", lambda d: True)

    async def fake_start(**kw):
        started.update(kw)
        return "acme_9"

    monkeypatch.setattr(curation_module, "start_skill_curation", fake_start)

    stub = _hook_stub({"curate_on_stop": True})
    stub.agent = SimpleNamespace(slug="acme", agent_dir=agent.agent_dir)

    async def scenario():
        TickEngine._maybe_curate(stub, "manual stop")
        for _ in range(10):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert started["agent_slug"] == "acme"
    assert "manual stop" in started["trigger"]


def test_maybe_curate_respects_config_and_experiments(tmp_path, monkeypatch):
    from condor.agents.engine import TickEngine

    agent = _make_agent(tmp_path, monkeypatch)
    monkeypatch.setattr(curation_module, "should_curate", lambda d: True)
    fired = []

    async def fake_start(**kw):
        fired.append(kw)
        return "x"

    monkeypatch.setattr(curation_module, "start_skill_curation", fake_start)

    async def scenario():
        off = _hook_stub({"curate_on_stop": False})
        off.agent = SimpleNamespace(slug="acme", agent_dir=agent.agent_dir)
        TickEngine._maybe_curate(off, "stop")

        exp = _hook_stub({"curate_on_stop": True}, is_experiment=True)
        exp.agent = SimpleNamespace(slug="acme", agent_dir=agent.agent_dir)
        TickEngine._maybe_curate(exp, "stop")
        for _ in range(10):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert fired == []


def test_curation_prompt_contains_shared_readonly_rule(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    prompt = build_curation_prompt(agent, "acme_5", "learnings", "digest", "- [x] d")
    assert "PROMOTION PROPOSALS" in prompt
    assert "read-only" in prompt
    assert "NEVER use action=\"edit\"" in prompt
    assert "GOOD outcome to change nothing" in prompt
