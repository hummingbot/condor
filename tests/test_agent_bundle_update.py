"""Updating an installed agent from a newer bundle (FEAT-145).

Four properties, in the order they matter:

* **It refuses what is not an update.** A different agent, an agent with no
  basis for claiming this bundle as its upstream, an older bundle, a shipped
  agent and a live trading loop are each a blocker, and a blocked plan writes
  nothing.
* **The diff is honest.** Every row of FEAT-145's table is asserted on a
  fixture carrying it: added, changed, unchanged, local_only, conflict,
  removed, conflict_removed, and the operator's own file that is none of them.
* **Everything this install earned survives.** Runtime output is byte-identical
  afterwards, a conflict keeps the local bytes, the mute set keeps the
  operator's curation, and the replaced bytes are kept out of the library.
* **Nothing is started**, and the preview and the write describe one plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from condor.agents import bundle as ab
from condor.agents.agent import AgentStore
from condor.layering import publishable_files
from condor.memory.mutes import load_mutes, set_muted
from condor.memory.paths import agent_home

AGENT_MD = (
    "---\n"
    "name: Drifter\n"
    "description: a traveller\n"
    "agent_key: claude-code\n"
    "tools: []\n"
    "when_to_consult: when something must move\n"
    "server_required: true\n"
    "server_name: ''\n"
    "created_by: 0\n"
    "created_at: '2026-01-01T00:00:00+00:00'\n"
    "---\n"
    "\n"
    "{body}\n"
)

SKILL_MD = (
    "---\nname: {slug}\ndescription: d\nwhen_to_use: whenever\nsource: builtin\n"
    "---\n\n{body}\n"
)

ROUTINE_PY = (
    "from pydantic import BaseModel\n\n\n"
    "class Config(BaseModel):\n    pass\n\n\n"
    "async def run(config, context):\n    return {body!r}\n"
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


def _file(path: str, text: str) -> ab.BundleFile:
    return ab.BundleFile(path=path, sha256=ab._digest(text), text=text)


def _bundle(
    *,
    slug: str = "drifter",
    agent_uid: str = "a" * 32,
    version: int = 1,
    files: list[ab.BundleFile] | None = None,
) -> ab.Bundle:
    """A hand-assembled bundle, so a test states exactly what travels."""
    return ab.Bundle(
        schema=ab.SCHEMA,
        kind=ab.KIND,
        exported_at="2026-10-15T12:00:00Z",
        version=version,
        agent=ab.BundleAgent(slug=slug, name="Drifter", agent_uid=agent_uid),
        files=(
            files
            if files is not None
            else [
                _file("AGENT.md", AGENT_MD.format(body="Travel.")),
                _file(
                    "skills/recon/SKILL.md", SKILL_MD.format(slug="recon", body="Look.")
                ),
                _file("routines/scan.py", ROUTINE_PY.format(body="v1")),
            ]
        ),
    )


@pytest.fixture
def installed():
    """An imported agent, with the stamp its import left behind."""
    plan = ab.plan(_bundle(), user_id=7)
    ab.apply(plan, user_id=7)
    return agent_home("drifter")


# ── The stamp ──


def test_import_writes_a_stamp_that_cannot_travel(installed):
    """The record of what the bundle said, invisible to the export."""
    stamp = ab.read_stamp(installed)
    assert stamp is not None
    assert stamp.agent_uid == "a" * 32
    assert stamp.version == 1
    assert set(stamp.digests) == {
        "AGENT.md",
        "skills/recon/SKILL.md",
        "routines/scan.py",
    }
    assert ab.STAMP_FILENAME not in {p.as_posix() for p in publishable_files(installed)}
    assert ab.STAMP_FILENAME not in {f.path for f in ab.build("drifter").files}


def test_an_unreadable_stamp_is_no_basis(installed):
    """``None``, not an empty stamp — the two mean opposite things."""
    ab.stamp_path(installed).write_text("{not json", "utf-8")
    assert ab.read_stamp(installed) is None


# ── Blockers ──


def test_a_different_agent_is_not_an_update(installed):
    before = (installed / "AGENT.md").read_bytes()
    plan = ab.plan_update(_bundle(agent_uid="b" * 32, version=2), "drifter")

    assert [b.code for b in plan.blockers] == ["different_agent"]
    assert "a" * 32 in plan.blockers[0].message
    assert "b" * 32 in plan.blockers[0].message
    with pytest.raises(ab.BundleError):
        ab.apply_update(plan, user_id=7)
    assert (installed / "AGENT.md").read_bytes() == before


def test_an_agent_with_no_basis_blocks_and_is_left_alone():
    """Authored here, or imported before the stamp existed: import-as-new."""
    home = agent_home("homegrown")
    _write(home / "AGENT.md", AGENT_MD.format(body="Mine."))
    before = (home / "AGENT.md").read_bytes()

    plan = ab.plan_update(_bundle(slug="homegrown"), "homegrown")

    assert [b.code for b in plan.blockers] == ["no_basis"]
    assert "import it as a new agent" in plan.blockers[0].message.lower()
    with pytest.raises(ab.BundleError):
        ab.apply_update(plan, user_id=7)
    assert (home / "AGENT.md").read_bytes() == before


def test_an_older_bundle_blocks_and_an_equal_version_only_warns(installed):
    ab.apply_update(ab.plan_update(_bundle(version=3), "drifter"), user_id=7)

    older = ab.plan_update(_bundle(version=2), "drifter")
    assert [b.code for b in older.blockers] == ["older_bundle"]

    rebuild = _bundle(
        version=3,
        files=[
            _file("AGENT.md", AGENT_MD.format(body="Travel further.")),
            _file("skills/recon/SKILL.md", SKILL_MD.format(slug="recon", body="Look.")),
            _file("routines/scan.py", ROUTINE_PY.format(body="v1")),
        ],
    )
    same = ab.plan_update(rebuild, "drifter")
    assert same.blockers == []
    assert [w.code for w in same.warnings] == ["same_version_new_build"]
    # Still a full diff, which is the point: the number only orders.
    assert same.counts == {"changed": 1, "unchanged": 2}


def test_a_live_loop_blocks_and_no_engine_does_not(installed, monkeypatch):
    assert ab.plan_update(_bundle(version=2), "drifter").blockers == []

    monkeypatch.setattr(
        "condor.agents.engine.get_engine", lambda slug: object(), raising=True
    )
    plan = ab.plan_update(_bundle(version=2), "drifter")
    assert [b.code for b in plan.blockers] == ["live_loop"]
    assert "running" in plan.blockers[0].message


def test_an_agent_that_is_not_installed_blocks():
    plan = ab.plan_update(_bundle(slug="nobody"), "nobody")
    assert [b.code for b in plan.blockers] == ["no_such_agent"]


def test_a_shipped_agent_is_out_of_scope(monkeypatch):
    from condor.paths import stock_agents_root

    _write(stock_agents_root() / "shipped" / "AGENT.md", AGENT_MD.format(body="Ship."))
    plan = ab.plan_update(_bundle(slug="shipped"), "shipped")
    assert [b.code for b in plan.blockers] == ["stock_agent"]


def test_an_altered_bundle_blocks_before_any_write(installed):
    bad = _bundle(version=2)
    bad.files[1].text = "---\nname: recon\n---\n\nSomething else.\n"
    plan = ab.plan_update(bad, "drifter")

    assert [b.code for b in plan.blockers] == ["altered"]
    with pytest.raises(ab.BundleError):
        ab.apply_update(plan, user_id=7)


# ── The diff, row by row ──


def _newer(**body) -> ab.Bundle:
    """Version 2: ``scan.py`` changed, ``probe`` added, ``recon`` as given."""
    return _bundle(
        version=2,
        files=[
            _file("AGENT.md", AGENT_MD.format(body="Travel.")),
            _file(
                "skills/recon/SKILL.md",
                SKILL_MD.format(slug="recon", body=body.get("recon", "Look.")),
            ),
            _file("routines/scan.py", ROUTINE_PY.format(body="v2")),
            _file("routines/probe.py", ROUTINE_PY.format(body="new")),
        ],
    )


def test_added_changed_and_unchanged_on_one_fixture(installed):
    plan = ab.plan_update(_newer(), "drifter")
    verdicts = {row.path: row.verdict for row in plan.files}

    assert verdicts["routines/probe.py"] == "added"
    assert verdicts["routines/scan.py"] == "changed"
    assert verdicts["skills/recon/SKILL.md"] == "unchanged"
    assert verdicts["AGENT.md"] == "unchanged"
    assert plan.counts == {"added": 1, "changed": 1, "unchanged": 2}


def test_a_locally_edited_file_upstream_left_alone_is_kept(installed):
    edited = _write(
        installed / "skills" / "recon" / "SKILL.md", "---\nname: recon\n---\n\nMine.\n"
    )
    before = edited.read_bytes()

    plan = ab.plan_update(_newer(), "drifter")
    assert _verdict(plan, "skills/recon/SKILL.md") == "local_only"

    ab.apply_update(plan, user_id=7)
    assert edited.read_bytes() == before


def test_a_file_edited_on_both_sides_is_a_conflict_that_keeps_local(installed):
    edited = _write(
        installed / "skills" / "recon" / "SKILL.md", "---\nname: recon\n---\n\nMine.\n"
    )
    before = edited.read_bytes()

    plan = ab.plan_update(_newer(recon="Look harder."), "drifter")
    row = _row(plan, "skills/recon/SKILL.md")
    assert row.verdict == "conflict"
    # The plan carries the bundle's copy, so a surface can offer it.
    assert "Look harder." in row.incoming

    ab.apply_update(plan, user_id=7)
    assert edited.read_bytes() == before


def test_a_file_the_bundle_dropped_is_deleted_unless_it_was_edited(installed):
    dropped = _bundle(
        version=2,
        files=[
            _file("AGENT.md", AGENT_MD.format(body="Travel.")),
            _file("routines/scan.py", ROUTINE_PY.format(body="v1")),
        ],
    )
    plan = ab.plan_update(dropped, "drifter")
    assert _verdict(plan, "skills/recon/SKILL.md") == "removed"
    ab.apply_update(plan, user_id=7)
    assert not (installed / "skills" / "recon" / "SKILL.md").exists()
    # And the stamp forgets it, so a later bundle re-adding it reads as added.
    assert "skills/recon/SKILL.md" not in ab.read_stamp(installed).digests


def test_a_dropped_file_the_operator_edited_survives(installed):
    edited = _write(
        installed / "skills" / "recon" / "SKILL.md", "---\nname: recon\n---\n\nMine.\n"
    )
    dropped = _bundle(
        version=2,
        files=[
            _file("AGENT.md", AGENT_MD.format(body="Travel.")),
            _file("routines/scan.py", ROUTINE_PY.format(body="v1")),
        ],
    )
    plan = ab.plan_update(dropped, "drifter")
    assert _verdict(plan, "skills/recon/SKILL.md") == "conflict_removed"

    ab.apply_update(plan, user_id=7)
    assert edited.read_text("utf-8") == "---\nname: recon\n---\n\nMine.\n"


def test_the_operators_own_file_is_never_listed_as_removed(installed):
    mine = _write(installed / "routines" / "mine.py", ROUTINE_PY.format(body="mine"))

    plan = ab.plan_update(_newer(), "drifter")
    assert "routines/mine.py" not in {row.path for row in plan.files}

    ab.apply_update(plan, user_id=7)
    assert mine.read_text("utf-8") == ROUTINE_PY.format(body="mine")


def test_an_upstream_file_at_a_path_the_operator_already_uses_is_a_conflict(installed):
    """FEAT-145's table does not reach this; keeping local is its own rule."""
    mine = _write(installed / "routines" / "probe.py", ROUTINE_PY.format(body="mine"))

    plan = ab.plan_update(_newer(), "drifter")
    assert _verdict(plan, "routines/probe.py") == "conflict"

    ab.apply_update(plan, user_id=7)
    assert mine.read_text("utf-8") == ROUTINE_PY.format(body="mine")


# ── What this install earned ──


def test_runtime_output_is_byte_identical_afterwards(installed):
    """Every runtime path survives the write untouched — ``mutes.yml`` apart.

    That one exception is not a leak: the mute set is the only runtime file an
    update deliberately writes, because what arrives new has to arrive switched
    off. The curation it already held survives, which is the property that
    matters, and ``test_new_items_arrive_muted_and_curation_survives`` is where
    it is asserted.
    """
    _write(installed / "mutes.yml", "skills:\n- recon\n")
    runtime = {
        "store/user_7/x.md": "my memory\n",
        "config.yml": "risk_limits:\n  max_loss_usd: 12.5\n",
        "sessions/session_1/log.md": "ran\n",
        "learnings.md": "learned\n",
        "state.json": '{"n": 1}\n',
        "owned_bots.json": "[]\n",
        "disowned_bots.json": "[]\n",
        "proposals/p.md": "proposed\n",
        "delegations/d.json": "{}\n",
        "dry_runs/1.json": "{}\n",
    }
    for rel, text in runtime.items():
        _write(installed / Path(rel), text)

    ab.apply_update(ab.plan_update(_newer(), "drifter"), user_id=7)

    for rel, text in runtime.items():
        assert (installed / Path(rel)).read_text("utf-8") == text, rel
    assert "recon" in load_mutes("drifter")["skills"]


def test_agent_md_keeps_this_installs_own_facts(installed):
    from condor.frontmatter import parse_frontmatter

    agent_md = installed / "AGENT.md"
    meta, _ = parse_frontmatter(agent_md.read_text("utf-8"))
    assert meta["created_by"] == 7

    store = AgentStore()
    agent = store.get("drifter")
    agent.server_name = "live-box"
    store.update(agent)

    newer = _bundle(
        version=2,
        files=[
            _file("AGENT.md", AGENT_MD.format(body="Travel differently.")),
            _file("skills/recon/SKILL.md", SKILL_MD.format(slug="recon", body="Look.")),
            _file("routines/scan.py", ROUTINE_PY.format(body="v1")),
        ],
    )
    plan = ab.plan_update(newer, "drifter")
    assert _verdict(plan, "AGENT.md") == "changed"
    ab.apply_update(plan, user_id=7)

    meta, body = parse_frontmatter(agent_md.read_text("utf-8"))
    assert "Travel differently." in body
    assert meta["created_by"] == 7
    assert meta["server_name"] == "live-box"
    assert meta["agent_uid"] == "a" * 32
    assert meta["bundle_version"] == 2


def test_a_server_pin_is_not_mistaken_for_an_edited_agent_md(installed):
    """Pinning a server must not freeze upstream's instructions out."""
    store = AgentStore()
    agent = store.get("drifter")
    agent.server_name = "live-box"
    store.update(agent)

    assert _verdict(ab.plan_update(_newer(), "drifter"), "AGENT.md") == "unchanged"


def test_new_items_arrive_muted_and_curation_survives(installed):
    assert load_mutes("drifter")["routines"] == {"scan"}
    set_muted("drifter", "routines", "scan", False)
    assert load_mutes("drifter")["routines"] == set()

    result = ab.apply_update(ab.plan_update(_newer(), "drifter"), user_id=7)

    assert result["mutes"] == {"routines": ["probe"], "skills": []}
    mutes = load_mutes("drifter")
    # The new routine is off; the one the operator unmuted after reading it is
    # still on, and `scan` *changed* — a changed item is not re-muted.
    assert "probe" in mutes["routines"]
    assert "scan" not in mutes["routines"]
    assert mutes["skills"] == {"recon"}


def test_a_replaced_routine_needs_consent_again(installed):
    from condor.agents import code_trust

    text = (installed / "routines" / "scan.py").read_text("utf-8")
    code_trust.allow("drifter", "scan", text)
    assert code_trust.allowed("drifter", "scan", text)

    ab.apply_update(ab.plan_update(_newer(), "drifter"), user_id=7)

    updated = (installed / "routines" / "scan.py").read_text("utf-8")
    assert code_trust.state("drifter", "scan", updated) == code_trust.CHANGED
    assert code_trust.state("drifter", "probe", "whatever") == code_trust.PENDING


def test_the_replaced_bytes_are_kept_out_of_the_library(installed):
    before = (installed / "routines" / "scan.py").read_text("utf-8")

    result = ab.apply_update(ab.plan_update(_newer(), "drifter"), user_id=7)

    backups = sorted((installed / ab.BACKUP_DIRNAME).rglob("routines/scan.py"))
    assert backups and backups[0].read_text("utf-8") == before
    assert result["backup"].endswith(backups[0].parent.parent.name)
    library = {p.as_posix() for p in publishable_files(installed)}
    assert not [p for p in library if p.startswith(ab.BACKUP_DIRNAME)]


# ── Interrupted, and started ──


def test_an_interrupted_update_leaves_the_previous_stamp(installed):
    plan = ab.plan_update(_newer(), "drifter")
    before = ab.read_stamp(installed).model_dump()

    real = ab.atomic_write_text
    calls: list[str] = []

    def exploding(path, text):
        if path.name == "scan.py":
            raise OSError("disk full")
        calls.append(path.name)
        return real(path, text)

    # Its own context, not the test's ``monkeypatch``: ``undo()`` on that one
    # would also revert the autouse fixture that repoints every durable root,
    # and the rest of this test would then read the developer's own install.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ab, "atomic_write_text", exploding)
        with pytest.raises(OSError):
            ab.apply_update(plan, user_id=7)

    # ``probe.py`` sorts before ``scan.py``, so it landed and the stamp did not.
    assert "probe.py" in calls
    assert ab.read_stamp(installed).model_dump() == before

    resumed = ab.plan_update(_newer(), "drifter")
    assert resumed.blockers == []
    # The file it got to still reads as this bundle's — writing it again is a
    # no-op — and the one it did not is still due.
    assert _verdict(resumed, "routines/probe.py") == "added"
    assert _verdict(resumed, "routines/scan.py") == "changed"
    assert ab.apply_update(resumed, user_id=7)["mutes"]["routines"] == ["probe"]
    assert "v2" in (installed / "routines" / "scan.py").read_text("utf-8")
    assert ab.read_stamp(installed).version == 2


def test_an_update_starts_nothing(installed):
    from condor.agents.engine import get_engine

    bundle = _bundle(
        version=2,
        files=[
            _file("AGENT.md", AGENT_MD.format(body="Travel.")),
            _file(
                "loops/grid/loop.md",
                "---\nname: grid\ndefault_config:\n  frequency: 60\n"
                "  connector_name: binance\n---\n\nTick.\n",
            ),
        ],
    )
    result = ab.apply_update(ab.plan_update(bundle, "drifter"), user_id=7)

    assert result["updated"] is True
    assert get_engine("drifter") is None
    # No delegation either: the loop the bundle brought states a frequency, and
    # an update that acted on it would be a tick nobody asked for.
    assert list((installed / "delegations").rglob("*")) == []
    assert "Nothing was started" in result["next_step"]


def test_the_plan_the_write_returns_is_the_plan_it_was_shown(installed):
    plan = ab.plan_update(_newer(), "drifter")
    previewed = plan.summary()
    applied = ab.apply_update(plan, user_id=7)

    for field in previewed:
        assert applied[field] == previewed[field], field
    assert set(applied) - set(previewed) == {"updated", "backup", "next_step"}


# ── The collision that is really an update ──


def test_an_import_collision_says_when_it_is_the_same_agent(installed):
    plan = ab.plan(_bundle(version=2), user_id=7)
    assert plan.collision == "drifter"
    assert plan.collision_agent_uid == "a" * 32
    assert plan.is_update is True

    other = ab.plan(_bundle(agent_uid="c" * 32, version=2), user_id=7)
    assert other.collision == "drifter"
    assert other.is_update is False


def test_a_stamp_is_valid_json_on_disk(installed):
    payload = json.loads(ab.stamp_path(installed).read_text("utf-8"))
    assert payload["agent_uid"] == "a" * 32
    assert payload["version"] == 1
    assert isinstance(payload["digests"], dict)


def _row(plan: ab.UpdatePlan, path: str) -> ab.UpdateFile:
    return next(row for row in plan.files if row.path == path)


def _verdict(plan: ab.UpdatePlan, path: str) -> str:
    return _row(plan, path).verdict


# ── The two endpoints ──


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=7, username="u", first_name="U", role="user"
    )
    return TestClient(app)


def _upload(client, path: str, bundle: ab.Bundle):
    return client.post(
        path,
        files={"file": ("x.condor-agent.json", ab.render(bundle), "application/json")},
    )


def test_the_preview_endpoint_and_the_apply_endpoint_report_one_plan(installed):
    client = _client()
    bundle = _newer()

    preview = _upload(client, "/agents/drifter/update/preview", bundle)
    assert preview.status_code == 200
    # Writes nothing: the routine the bundle adds is still absent.
    assert not (installed / "routines" / "probe.py").exists()

    applied = _upload(client, "/agents/drifter/update", bundle)
    assert applied.status_code == 200

    shown, written = preview.json(), applied.json()
    for field, value in shown.items():
        assert written[field] == value, field
    assert shown["blockers"] == [] and shown["warnings"] == []
    assert {row["path"]: row["verdict"] for row in shown["files"]} == {
        "AGENT.md": "unchanged",
        "routines/probe.py": "added",
        "routines/scan.py": "changed",
        "skills/recon/SKILL.md": "unchanged",
    }
    assert (installed / "routines" / "probe.py").exists()


def test_the_update_endpoint_answers_400_for_a_blocked_plan(installed):
    client = _client()
    res = _upload(client, "/agents/drifter/update", _bundle(agent_uid="b" * 32))

    assert res.status_code == 400
    assert "two different agents" in res.json()["detail"]


def test_the_update_endpoint_refuses_a_file_that_is_not_a_bundle(installed):
    client = _client()
    before = (installed / "AGENT.md").read_bytes()

    assert (
        client.post(
            "/agents/drifter/update",
            files={"file": ("x.json", "not json at all", "application/json")},
        ).status_code
        == 415
    )
    assert (installed / "AGENT.md").read_bytes() == before


# ── The chat/MCP door ──


def test_manage_agents_upgrades_an_installed_agent(installed, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", 7, raising=False)
    path = installed.parent / "newer.condor-agent.json"
    path.write_text(ab.render(_newer()), "utf-8")

    result = trading_agent.manage_agents(
        action="upgrade", agent_slug="drifter", path=str(path)
    )

    assert result["updated"] is True
    assert result["mutes"] == {"routines": ["probe"], "skills": []}
    assert (installed / "routines" / "probe.py").exists()
    assert "v2" in (installed / "routines" / "scan.py").read_text("utf-8")


def test_manage_agents_upgrade_reports_a_blocker_with_the_plan(installed, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", 7, raising=False)
    path = installed.parent / "other.condor-agent.json"
    path.write_text(ab.render(_bundle(agent_uid="b" * 32)), "utf-8")

    result = trading_agent.manage_agents(
        action="upgrade", agent_slug="drifter", path=str(path)
    )

    assert "two different agents" in result["error"]
    assert [b["code"] for b in result["preview"]["blockers"]] == ["different_agent"]


def test_manage_agents_upgrade_needs_both_the_agent_and_the_bundle():
    from mcp_servers.condor.tools import trading_agent

    assert "agent_slug" in trading_agent.manage_agents(action="upgrade")["error"]
    assert (
        "path"
        in trading_agent.manage_agents(action="upgrade", agent_slug="drifter")["error"]
    )


def test_a_hand_edited_stamp_cannot_name_a_path_outside_the_library(installed):
    """Defence in depth: a ``removed`` row is an ``unlink`` (FEAT-145)."""
    stamp = ab.read_stamp(installed)
    payload = stamp.model_dump()
    payload["digests"]["../../escape.md"] = "sha256:deadbeef"
    payload["digests"]["store/user_7/x.md"] = "sha256:deadbeef"
    ab.stamp_path(installed).write_text(json.dumps(payload), "utf-8")

    reread = ab.read_stamp(installed)
    assert "../../escape.md" not in reread.digests
    assert "store/user_7/x.md" not in reread.digests
    assert set(reread.digests) == set(stamp.digests)

    plan = ab.plan_update(_newer(), "drifter")
    assert "../../escape.md" not in {row.path for row in plan.files}
