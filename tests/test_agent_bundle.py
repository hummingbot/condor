"""An agent travels as one file (FEAT-133).

Three properties, in the order they matter:

* **What leaves is the agent, not the install.** The bundle carries library
  content only, with the three install-local fields rewritten in its copy and
  the files on disk untouched.
* **Nothing lands that was not reviewed.** A tampered digest, an unknown
  schema, a foreign ``kind`` and a traversal path are each refused *whole*,
  with nothing written.
* **What lands arrives switched off.** Every imported routine and skill is
  muted, the agent is registered and loadable, and nothing is started.
"""

from __future__ import annotations

import json

import pytest

from condor.agents import bundle as ab
from condor.agents.agent import AgentStore
from condor.agents.strategy import AlreadyExistsError
from condor.frontmatter import parse_frontmatter
from condor.layering import publishable_files
from condor.memory.mutes import load_mutes
from condor.memory.paths import agent_home, stock_agent_home
from condor.memory.skills import SkillStore
from condor.paths import stock_agents_root

AGENT_MD = (
    "---\n"
    "name: {name}\n"
    "description: a traveller\n"
    "agent_key: claude-code\n"
    "tools: []\n"
    "when_to_consult: when something must move\n"
    "server_required: true\n"
    "server_name: {server}\n"
    "created_by: {created_by}\n"
    "created_at: '2026-01-01T00:00:00+00:00'\n"
    "{extra}"
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
    "async def run(config, context):\n    return 'ok'\n"
)


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


def _agent_md(name="Traveller", server="", created_by=0, extra="", body="Travel."):
    return AGENT_MD.format(
        name=name, server=server, created_by=created_by, extra=extra, body=body
    )


@pytest.fixture
def traveller():
    """A library agent with one of everything the layout defines."""
    home = agent_home("traveller")
    _write(home / "AGENT.md", _agent_md())
    _write(home / "skills" / "s" / "SKILL.md", SKILL_MD.format(slug="s", body="Look."))
    _write(home / "routines" / "r.py", ROUTINE_PY)
    _write(home / "controllers" / "c" / "c.py", "# controller\n")
    _write(home / "controllers" / "c" / "sample_configs" / "a.yml", "pair: SOL-USDC\n")
    _write(home / "loops" / "l" / "loop.md", "---\nname: l\n---\n\nTick.\n")
    _write(home / "shutdown.md", "Stand down.\n")
    return home


# ── Export: what travels, and what does not ──


def test_export_carries_exactly_the_library_files(traveller):
    paths = [f.path for f in ab.build("traveller").files]
    assert sorted(paths) == sorted(
        [
            "AGENT.md",
            "controllers/c/c.py",
            "controllers/c/sample_configs/a.yml",
            "loops/l/loop.md",
            "routines/r.py",
            "shutdown.md",
            "skills/s/SKILL.md",
        ]
    )


def test_export_carries_no_runtime_output(traveller):
    """The selection is ``publishable_files``, so runtime output never travels.

    The last two are [[CORR-720]]'s holes — a root ``state/`` the layout never
    defined and compiled bytecode under an allowed directory.
    """
    _write(traveller / "store" / "user_7" / "x.md", "mine\n")
    _write(traveller / "sessions" / "1" / "log.md", "ran\n")
    _write(traveller / "dry_runs" / "1.json", "{}\n")
    _write(traveller / "learnings.md", "learned\n")
    _write(traveller / "config.yml", "a: 1\n")
    _write(traveller / "mutes.yml", "skills: [s]\n")
    _write(traveller / "proposals" / "p.md", "proposed\n")
    _write(traveller / "state" / "op.json", "{}\n")
    _write(traveller / "routines" / "__pycache__" / "r.pyc", "bytecode\n")

    paths = {f.path for f in ab.build("traveller").files}
    for leaked in (
        "store/user_7/x.md",
        "sessions/1/log.md",
        "dry_runs/1.json",
        "learnings.md",
        "config.yml",
        "mutes.yml",
        "proposals/p.md",
        "state/op.json",
        "routines/__pycache__/r.pyc",
    ):
        assert leaked not in paths


def test_export_leaves_inherited_shared_skills_behind(traveller):
    """A shared playbook is a *sibling* of the home, so it cannot be reached.

    Asserted against a fixture where the agent really does resolve it, so this
    is "not bundled" rather than "not there".
    """
    from condor.memory.paths import shared_skills_root

    _write(
        shared_skills_root() / "lp_rebalance" / "SKILL.md",
        SKILL_MD.format(slug="lp_rebalance", body="Rebalance."),
    )
    assert SkillStore("traveller").read("lp_rebalance") is not None

    paths = {f.path for f in ab.build("traveller").files}
    assert "skills/s/SKILL.md" in paths
    assert not any("lp_rebalance" in path for path in paths)


def test_export_scrubs_the_three_install_local_fields(traveller):
    _write(
        traveller / "AGENT.md",
        _agent_md(
            server="prod-sg",
            created_by=424242,
            extra="forked_from: sha256:abc123abc123\nforked_at: '2026-02-02T00:00:00Z'\n",
        ),
    )
    before = (traveller / "AGENT.md").read_text("utf-8")

    carried = next(f for f in ab.build("traveller").files if f.path == "AGENT.md")
    meta, _ = parse_frontmatter(carried.text)
    assert meta["server_name"] == ""
    assert meta["created_by"] == 0
    assert "forked_from" not in meta
    assert "forked_at" not in meta

    # The file on disk still says what it said.
    assert (traveller / "AGENT.md").read_text("utf-8") == before


def test_export_refuses_a_non_utf8_file_naming_it(traveller):
    (traveller / "skills" / "s" / "ref.md").write_bytes(b"\xff\xfe not text")
    with pytest.raises(ab.BundleError) as exc:
        ab.build("traveller")
    assert "skills/s/ref.md" in str(exc.value)


def test_export_reads_a_shipped_agent_too():
    """Local shadows stock per file, which is the read order everything uses."""
    stock = stock_agent_home("shipped")
    _write(stock / "AGENT.md", _agent_md(name="Shipped"))
    _write(stock / "skills" / "s" / "SKILL.md", SKILL_MD.format(slug="s", body="Up."))
    _write(
        agent_home("shipped") / "skills" / "s" / "SKILL.md",
        SKILL_MD.format(slug="s", body="Mine."),
    )

    files = {f.path: f.text for f in ab.build("shipped").files}
    assert set(files) == {"AGENT.md", "skills/s/SKILL.md"}
    assert "Mine." in files["skills/s/SKILL.md"]


# ── Parse: refusing a document this install should not read ──


def test_parse_refuses_an_unknown_schema_naming_both_numbers(traveller):
    payload = json.loads(ab.render(ab.build("traveller")))
    payload["schema"] = 2
    with pytest.raises(ab.BundleError) as exc:
        ab.parse(json.dumps(payload))
    assert "2" in str(exc.value) and "1" in str(exc.value)
    assert not agent_home("traveller_2").exists()


def test_parse_refuses_a_foreign_kind_with_a_sentence(traveller):
    payload = json.loads(ab.render(ab.build("traveller")))
    payload["kind"] = "something-else"
    with pytest.raises(ab.BundleError) as exc:
        ab.parse(json.dumps(payload))
    assert "something-else" in str(exc.value)


def test_parse_refuses_bytes_that_are_not_json():
    with pytest.raises(ab.NotJsonError):
        ab.parse(b"not a document at all")


# ── Plan: every refusal, before anything is written ──


def _bundle_for(traveller, as_dict=False):
    payload = json.loads(ab.render(ab.build("traveller")))
    return payload if as_dict else ab.parse(json.dumps(payload))


def test_a_tampered_file_is_refused_naming_its_path(traveller):
    payload = _bundle_for(traveller, as_dict=True)
    for entry in payload["files"]:
        if entry["path"] == "routines/r.py":
            entry["text"] += "\nimport os; os.system('curl evil')\n"
    parsed = ab.parse(json.dumps(payload))

    import_plan = ab.plan(parsed, as_slug="arrival", user_id=7)
    assert any("routines/r.py" in r for r in import_plan.refusals)
    with pytest.raises(ab.BundleError):
        ab.apply(import_plan, user_id=7)
    assert not agent_home("arrival").exists()


@pytest.mark.parametrize(
    "runtime",
    [
        # The one that starts a loop: boot reconciliation scans exactly this
        # path, and a live state with ``restart_on_boot`` is all it reads
        # before starting the strategy — no operator presses Start.
        "loops/l/sessions/session_1/status.json",
        "mutes.yml",  # an import deciding its own muting
        "config.yml",  # the pins a run was started with
        "state.json",
        "owned_bots.json",
        "store/user_7/note.md",
        "state/anything.json",  # a root folder the layout never defined
    ],
)
def test_a_runtime_path_is_refused_whole(traveller, runtime):
    """Inside the directory is not enough: it has to be library content.

    A bundle is hand-editable JSON, so the export's own filter
    (``publishable_files``) protects nothing on the way in — the import door
    has to hold the same rule, which is why both now read
    :func:`condor.layering.is_library_path`.
    """
    payload = _bundle_for(traveller, as_dict=True)
    payload["files"].append({"path": runtime, "sha256": ab._digest("{}"), "text": "{}"})
    import_plan = ab.plan(ab.parse(json.dumps(payload)), as_slug="arrival", user_id=7)

    assert any(runtime in r for r in import_plan.refusals)
    with pytest.raises(ab.BundleError):
        ab.apply(import_plan, user_id=7)
    assert not agent_home("arrival").exists()


def test_every_path_a_real_export_produces_still_lands(traveller):
    """The round trip, so the guard above cannot quietly refuse real content."""
    import_plan = ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7)

    assert import_plan.refusals == []
    landed = {row.path for row in import_plan.files + import_plan.executables}
    assert landed == {p.as_posix() for p in publishable_files(traveller)}


@pytest.mark.parametrize(
    "escape",
    ["../../../agents/condor/AGENT.md", "/etc/passwd", "skills/../../x"],
)
def test_a_traversal_path_is_refused_whole(traveller, escape):
    shipped = stock_agent_home("condor")
    _write(shipped / "AGENT.md", _agent_md(name="Condor"))
    before = (shipped / "AGENT.md").read_bytes()
    stock_listing = sorted(p.as_posix() for p in stock_agents_root().rglob("*"))

    payload = _bundle_for(traveller, as_dict=True)
    payload["files"].append(
        {"path": escape, "sha256": ab._digest("pwned"), "text": "pwned"}
    )
    import_plan = ab.plan(ab.parse(json.dumps(payload)), as_slug="arrival", user_id=7)

    assert any(escape in r for r in import_plan.refusals)
    with pytest.raises(ab.BundleError):
        ab.apply(import_plan, user_id=7)
    assert not agent_home("arrival").exists()
    assert (shipped / "AGENT.md").read_bytes() == before
    assert sorted(p.as_posix() for p in stock_agents_root().rglob("*")) == stock_listing


def test_a_taken_local_slug_is_refused_and_a_free_one_named(traveller):
    bundle = _bundle_for(traveller)
    before = (traveller / "AGENT.md").read_bytes()

    import_plan = ab.plan(bundle, user_id=7)
    assert import_plan.collision == "traveller"
    assert import_plan.suggested_slug == "traveller_2"
    with pytest.raises(AlreadyExistsError) as exc:
        ab.apply(import_plan, user_id=7)
    assert "traveller" in str(exc.value) and "traveller_2" in str(exc.value)
    assert (traveller / "AGENT.md").read_bytes() == before


def test_a_stock_only_slug_is_refused_too(traveller):
    shipped = stock_agent_home("shipped")
    _write(shipped / "AGENT.md", _agent_md(name="Shipped"))
    before = (shipped / "AGENT.md").read_bytes()

    import_plan = ab.plan(_bundle_for(traveller), as_slug="shipped", user_id=7)
    assert import_plan.collision == "shipped"
    assert import_plan.suggested_slug == "shipped_2"
    with pytest.raises(AlreadyExistsError):
        ab.apply(import_plan, user_id=7)
    assert (shipped / "AGENT.md").read_bytes() == before


def test_the_reserved_default_slug_is_refused(traveller):
    import_plan = ab.plan(_bundle_for(traveller), as_slug="condor", user_id=7)
    assert import_plan.collision == "condor"


# ── Apply: what lands, and in what state ──


def test_import_writes_only_into_the_local_root(traveller):
    stock_before = sorted(p.as_posix() for p in stock_agents_root().rglob("*"))

    ab.apply(ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7)

    assert (agent_home("arrival") / "AGENT.md").exists()
    assert sorted(p.as_posix() for p in stock_agents_root().rglob("*")) == stock_before


def test_the_imported_agent_md_names_the_importer_and_no_server(traveller):
    ab.apply(ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7)
    meta, _ = parse_frontmatter((agent_home("arrival") / "AGENT.md").read_text("utf-8"))
    assert meta["created_by"] == 7
    assert meta["server_name"] == ""


def test_imported_code_arrives_muted_and_the_agent_is_registered(traveller):
    ab.apply(ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7)

    muted = load_mutes("arrival")
    assert muted["routines"] == {"r"}
    assert muted["skills"] == {"s"}
    assert AgentStore().get("arrival") is not None


def test_a_muted_import_is_offered_to_nobody_until_it_is_unmuted(traveller):
    from routines.base import assistant_routines

    ab.apply(ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7)

    assert "s" not in SkillStore("arrival").list_index()
    assert "r" not in assistant_routines("arrival", force_reload=True)

    from condor.memory.mutes import set_muted

    set_muted("arrival", "routines", "r", False)
    offered = assistant_routines("arrival", force_reload=True)
    assert "r" in offered
    assert "s" not in SkillStore("arrival").list_index()


def test_import_starts_nothing(traveller):
    """No engine, no delegation — the result names the next step, never takes it."""
    from condor.agents.engine import get_all_engines
    from condor.paths import delegations_dir

    _write(
        traveller / "loops" / "l" / "loop.md",
        "---\nname: l\nconfig:\n  frequency: 60\n---\n\nTick.\n",
    )
    result = ab.apply(
        ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7
    )

    assert not [a for a in get_all_engines() if a.startswith("arrival")]
    assert not delegations_dir(7).exists()
    assert result["imported"] is True
    assert "unmute" in result["next_step"]


def test_a_round_trip_is_lossless_for_everything_that_travels(traveller):
    ab.apply(ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7)

    source = {p.as_posix() for p in publishable_files(traveller)}
    landed = {p.as_posix() for p in publishable_files(agent_home("arrival"))}
    assert source == landed

    for rel in source - {"AGENT.md"}:
        assert (traveller / rel).read_text("utf-8") == (
            agent_home("arrival") / rel
        ).read_text("utf-8")

    # AGENT.md differs only in the fields that are deliberately not carried.
    before, _ = parse_frontmatter((traveller / "AGENT.md").read_text("utf-8"))
    after, body = parse_frontmatter(
        (agent_home("arrival") / "AGENT.md").read_text("utf-8")
    )
    assert body == parse_frontmatter((traveller / "AGENT.md").read_text("utf-8"))[1]
    for key in ("server_name", "created_by", "agent_key"):
        before.pop(key, None)
        after.pop(key, None)
    assert before == after


def test_no_restart_is_needed_to_use_an_imported_agent(traveller):
    """Every store resolves from disk on each call, so the next call sees it."""
    from condor.agent_controllers import agent_controllers
    from condor.agents.strategy import StrategyStore
    from routines.base import assistant_routines

    assert AgentStore().get("arrival") is None

    ab.apply(ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7), user_id=7)

    assert AgentStore().get("arrival") is not None
    assert [s.slug for s in StrategyStore().list("arrival")] == ["l"]
    assert "c" in agent_controllers("arrival")
    assert SkillStore("arrival", include_muted=True).read("s") is not None
    assert "r" in assistant_routines("arrival", include_muted=True, force_reload=True)


def test_preview_and_apply_report_the_same_plan(traveller):
    """One builder behind both, so the dialog cannot show what the write won't do."""
    preview = ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7)
    written = ab.apply(preview, user_id=7)

    shown = preview.summary()
    for field, value in shown.items():
        assert written[field] == value, field
    assert set(written) - set(shown) == {"imported", "next_step"}


def test_the_preview_names_every_executable_separately(traveller):
    preview = ab.plan(_bundle_for(traveller), as_slug="arrival", user_id=7)

    assert sorted(f.path for f in preview.executables) == [
        "controllers/c/c.py",
        "routines/r.py",
    ]
    assert all(not f.path.endswith(".py") for f in preview.files)
    assert all(f.bytes > 0 for f in preview.executables)


# ── The three endpoints ──


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


def _upload(client, path: str, text: str, as_slug: str = ""):
    return client.post(
        path,
        files={"file": ("x.condor-agent.json", text, "application/json")},
        data={"as_slug": as_slug},
    )


def test_the_export_endpoint_answers_a_named_file(traveller):
    res = _client().get("/agents/traveller/bundle")

    assert res.status_code == 200
    assert "traveller.condor-agent.json" in res.headers["content-disposition"]
    assert {f["path"] for f in res.json()["files"]} == {
        p.as_posix() for p in publishable_files(traveller)
    }


def test_preview_and_apply_endpoints_report_the_same_thing(traveller):
    client = _client()
    text = client.get("/agents/traveller/bundle").text

    preview = _upload(client, "/agents/import/preview", text, "arrival")
    assert preview.status_code == 200
    assert not agent_home("arrival").exists()

    applied = _upload(client, "/agents/import", text, "arrival")
    assert applied.status_code == 200

    shown, written = preview.json(), applied.json()
    for field, value in shown.items():
        assert written[field] == value, field
    assert shown["files"] and shown["executables"]
    assert shown["mutes"] == {"routines": ["r"], "skills": ["s"]}
    assert shown["stamps"] == {"created_by": "7", "server_name": ""}


def test_the_import_endpoint_answers_409_for_a_taken_slug(traveller):
    client = _client()
    text = client.get("/agents/traveller/bundle").text

    res = _upload(client, "/agents/import", text)
    assert res.status_code == 409
    assert "traveller_2" in res.json()["detail"]


def test_the_import_endpoint_refuses_a_file_that_is_not_a_bundle(traveller):
    client = _client()
    assert _upload(client, "/agents/import", "not json at all").status_code == 415
    assert _upload(client, "/agents/import", '{"kind": "other"}').status_code == 400
    assert not agent_home("other").exists()


# ── The chat/MCP door ──


def test_manage_agents_exports_to_a_file_and_imports_it_back(traveller, monkeypatch):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", 7, raising=False)

    exported = trading_agent.manage_agents(action="export", agent_slug="traveller")
    assert exported["exported"] is True
    assert exported["path"].endswith(ab.FILENAME_SUFFIX)
    assert "routines/r.py" in exported["files"]

    landed = trading_agent.manage_agents(
        action="import", path=exported["path"], agent_slug="arrival"
    )
    assert landed["imported"] is True
    assert AgentStore().get("arrival") is not None
    assert load_mutes("arrival")["routines"] == {"r"}


def test_manage_agents_import_refuses_a_collision_and_shows_the_preview(
    traveller, monkeypatch
):
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(settings, "user_id", 7, raising=False)
    exported = trading_agent.manage_agents(action="export", agent_slug="traveller")

    refused = trading_agent.manage_agents(action="import", path=exported["path"])
    assert "traveller_2" in refused["error"]
    assert refused["preview"]["executables"]


def test_manage_agents_import_needs_a_path(traveller):
    from mcp_servers.condor.tools import trading_agent

    assert "path" in trading_agent.manage_agents(action="import")["error"]
