"""FEAT-147: nothing an imported bundle brought executes before consent.

Four things are asserted here, in the order the feature builds them:

1. the record itself — absent means *not gated*, a digest means *this exact
   content*, and an unparseable file allows nothing;
2. the gate inside discovery — a probe routine whose module level writes a
   sentinel file leaves it absent across every surface that lists or reads it,
   and present only once the file is allowed;
3. the refusal — every run path reports the same sentence, naming the gesture
   that clears it;
4. the import — ``bundle.apply`` arms the record, and ``store/code_trust.yml``
   can never travel in a bundle.
"""

import json

import pytest
import yaml

import condor.routine_store as routine_store_mod
import routines.base as base
from condor.agents import code_trust
from condor.layering import is_library_path
from condor.memory.mutes import load_mutes, set_muted
from condor.memory.paths import agent_home
from routines.base import (
    assistant_routines,
    assistant_routines_dir,
    pending_routines,
)

# A routine whose *module level* writes a file. Importing it is observable;
# listing it must not be.
PROBE = '''
from pathlib import Path

CATEGORY = "Probe"

Path({sentinel!r}).write_text("imported")

from pydantic import BaseModel


class Config(BaseModel):
    """{desc}"""

    value: int = 1


async def run(config, context):
    return "ok"
'''


@pytest.fixture
def isolated_routines(tmp_path, monkeypatch):
    """Agent routine dirs under ``tmp_path``, discovery caches emptied."""
    monkeypatch.setattr(base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(base, "_routines_cache", None)
    monkeypatch.setattr(base, "_routines_mtimes", {})
    monkeypatch.setattr(base, "_path_caches", {})
    return tmp_path


@pytest.fixture
def imported_agent(isolated_routines):
    """An agent whose two routines arrived in a bundle: armed, nothing allowed.

    Returns ``(slug, sentinel_of)`` where ``sentinel_of(name)`` is the file
    that routine writes if it is ever imported.
    """
    slug = "perps"
    routines_dir = assistant_routines_dir(slug)
    routines_dir.mkdir(parents=True, exist_ok=True)
    sentinels = {}
    for name in ("funding_watch", "lp_scanner"):
        sentinels[name] = isolated_routines / f"{name}.ran"
        (routines_dir / f"{name}.py").write_text(
            PROBE.format(sentinel=str(sentinels[name]), desc=f"{name} description")
        )
    code_trust.arm(slug)
    return slug, sentinels


def _source_of(slug, name):
    return (assistant_routines_dir(slug) / f"{name}.py").read_text()


# ── 1. the record ──


def test_an_agent_nobody_imported_is_not_gated(isolated_routines):
    assert code_trust.load("perps") is None
    assert code_trust.is_armed("perps") is False
    # The permissive answer, because this is the operator's own code.
    assert code_trust.allowed("perps", "anything", "print()") is True
    assert code_trust.state("perps", "anything", "print()") == code_trust.ALLOWED


def test_arming_gates_everything_and_allows_nothing(isolated_routines):
    code_trust.arm("perps")
    assert code_trust.is_armed("perps") is True
    assert code_trust.load("perps") == code_trust.CodeTrust(routines={})
    assert code_trust.allowed("perps", "lp_scanner", "print()") is False


def test_arming_twice_keeps_what_was_allowed(isolated_routines):
    code_trust.arm("perps")
    code_trust.allow("perps", "lp_scanner", "print()")
    code_trust.arm("perps")
    assert code_trust.allowed("perps", "lp_scanner", "print()") is True


def test_consent_is_content_and_not_a_name(isolated_routines):
    code_trust.arm("perps")
    code_trust.allow("perps", "lp_scanner", "print('v1')")

    assert code_trust.state("perps", "lp_scanner", "print('v1')") == code_trust.ALLOWED
    # The same name, different bytes: this is FEAT-145's bundle update, and a
    # hand edit, and both must come back for a second read.
    assert code_trust.state("perps", "lp_scanner", "print('v2')") == code_trust.CHANGED
    assert code_trust.state("perps", "other", "print('v1')") == code_trust.PENDING


def test_revoking_keeps_the_agent_gated(isolated_routines):
    code_trust.arm("perps")
    code_trust.allow("perps", "lp_scanner", "print()")
    code_trust.revoke("perps", "lp_scanner")

    assert code_trust.is_armed("perps") is True  # the file is never removed
    assert code_trust.allowed("perps", "lp_scanner", "print()") is False


def test_allowing_an_ungated_agent_is_refused(isolated_routines):
    """Writing the record would gate every sibling as a side effect."""
    with pytest.raises(code_trust.PendingConsentError):
        code_trust.allow("perps", "lp_scanner", "print()")
    assert code_trust.is_armed("perps") is False


def test_an_unparseable_record_allows_nothing(isolated_routines):
    """The inverse of ``load_mutes``'s choice, and for the inverse reason."""
    path = code_trust.trust_path("perps")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("routines: [this is: not a mapping\n")

    trust = code_trust.load("perps")
    assert trust is not None  # still gated
    assert trust.routines == {}
    assert code_trust.allowed("perps", "lp_scanner", "print()") is False


def test_the_record_lives_where_a_bundle_cannot_reach(isolated_routines):
    code_trust.arm("perps")
    rel = code_trust.trust_path("perps").relative_to(agent_home("perps"))

    assert str(rel) == "store/code_trust.yml"
    assert is_library_path("store/code_trust.yml") is False
    assert is_library_path(str(rel)) is False


# ── 2. the gate: nothing is imported ──


def test_listing_an_imported_routine_does_not_run_it(imported_agent):
    slug, sentinels = imported_agent

    assert assistant_routines(slug) == {}
    assert assistant_routines(slug, include_muted=True) == {}
    assert [row.name for row in pending_routines(slug)] == [
        "funding_watch",
        "lp_scanner",
    ]
    assert not any(path.exists() for path in sentinels.values())


def test_a_forced_reload_does_not_override_consent(imported_agent):
    """A forced re-import of code nobody allowed is the exact failure mode."""
    slug, sentinels = imported_agent

    assert assistant_routines(slug, force_reload=True, include_muted=True) == {}
    assert not any(path.exists() for path in sentinels.values())


def test_a_pending_row_carries_what_a_listing_needs(imported_agent):
    slug, _ = imported_agent
    row = next(r for r in pending_routines(slug) if r.name == "funding_watch")

    assert row.category == "Probe"
    assert row.description == "funding_watch description"
    assert row.source == f"agent:{slug}"
    assert row.reason == code_trust.PENDING
    assert row.digest == code_trust.digest_of(_source_of(slug, "funding_watch"))
    # Deliberately no run_fn and no config_class: a row that must not be run
    # cannot reach a caller that would run it.
    assert not hasattr(row, "run_fn")
    assert not hasattr(row, "config_class")


def test_allowing_one_routine_loads_exactly_that_one(imported_agent):
    slug, sentinels = imported_agent
    code_trust.allow(slug, "funding_watch", _source_of(slug, "funding_watch"))

    loaded = assistant_routines(slug)
    assert set(loaded) == {"funding_watch"}
    assert sentinels["funding_watch"].exists()
    assert not sentinels["lp_scanner"].exists()
    assert [row.name for row in pending_routines(slug)] == ["lp_scanner"]


def test_consent_takes_effect_without_the_file_changing(imported_agent):
    """The mtime cache must not outlive the decision that fed it."""
    slug, sentinels = imported_agent
    assert assistant_routines(slug) == {}  # primes the per-directory cache

    code_trust.allow(slug, "lp_scanner", _source_of(slug, "lp_scanner"))

    assert set(assistant_routines(slug)) == {"lp_scanner"}
    assert sentinels["lp_scanner"].exists()


def test_revoking_takes_a_loaded_routine_back_out(imported_agent):
    slug, _ = imported_agent
    code_trust.allow(slug, "lp_scanner", _source_of(slug, "lp_scanner"))
    assert set(assistant_routines(slug)) == {"lp_scanner"}

    code_trust.revoke(slug, "lp_scanner")
    assert assistant_routines(slug) == {}
    assert [row.name for row in pending_routines(slug)] == [
        "funding_watch",
        "lp_scanner",
    ]


def test_an_allowed_routine_edited_on_disk_is_pending_again(imported_agent):
    slug, _ = imported_agent
    path = assistant_routines_dir(slug) / "lp_scanner.py"
    code_trust.allow(slug, "lp_scanner", path.read_text())
    assert set(assistant_routines(slug)) == {"lp_scanner"}

    path.write_text(path.read_text() + "\n# one more line\n")

    assert assistant_routines(slug) == {}
    row = next(r for r in pending_routines(slug) if r.name == "lp_scanner")
    assert row.reason == code_trust.CHANGED
    assert "changed on disk" in row.refusal()


def test_muting_a_pending_routine_works_and_executes_nothing(imported_agent):
    slug, sentinels = imported_agent

    set_muted(slug, "routine", "lp_scanner", True)

    assert load_mutes(slug)["routines"] == {"lp_scanner"}
    rows = {row.name: row for row in pending_routines(slug)}
    assert rows["lp_scanner"].muted is True
    assert rows["funding_watch"].muted is False
    assert not any(path.exists() for path in sentinels.values())

    set_muted(slug, "routine", "lp_scanner", False)
    assert load_mutes(slug)["routines"] == set()
    assert not any(path.exists() for path in sentinels.values())


def test_a_locally_created_agent_is_untouched(isolated_routines):
    """This item gates imported code, not the operator's own."""
    sentinel = isolated_routines / "local.ran"
    routines_dir = assistant_routines_dir("homegrown")
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "scan.py").write_text(
        PROBE.format(sentinel=str(sentinel), desc="a local routine")
    )

    assert set(assistant_routines("homegrown")) == {"scan"}
    assert pending_routines("homegrown") == []
    assert sentinel.exists()
    assert not code_trust.trust_path("homegrown").exists()


def test_the_chat_has_no_pending_half(isolated_routines):
    assert pending_routines(None) == []
    assert pending_routines("condor") == []


# ── 3. the refusal ──


def test_resolving_a_pending_routine_names_the_allow_action(imported_agent):
    slug, sentinels = imported_agent
    store = routine_store_mod.RoutineStore()

    with pytest.raises(code_trust.PendingConsentError) as exc:
        store._resolve_routine(f"{slug}/lp_scanner")

    assert "has not been allowed to run" in str(exc.value)
    assert "allow it" in str(exc.value)
    assert not sentinels["lp_scanner"].exists()


def test_a_genuinely_unknown_routine_still_reads_as_missing(imported_agent):
    slug, _ = imported_agent
    store = routine_store_mod.RoutineStore()

    assert store._resolve_routine(f"{slug}/no_such_thing") is None


def test_the_mcp_routine_tool_refuses_with_the_same_sentence(
    imported_agent, monkeypatch
):
    """The agent's own path to its routines, which bypasses the store."""
    from mcp_servers.condor.tools import routines as tool

    slug, sentinels = imported_agent
    # ``specialist_slug`` is derived from ``agent_slug``, never stored.
    monkeypatch.setattr(tool.settings, "agent_slug", slug)

    with pytest.raises(code_trust.PendingConsentError) as exc:
        tool._resolve_routine("lp_scanner")
    assert "has not been allowed to run" in str(exc.value)

    # ``describe`` is a read, so it answers with the refusal instead of raising.
    described = tool.describe_routine("lp_scanner")
    assert "has not been allowed to run" in described["error"]
    assert not sentinels["lp_scanner"].exists()


def test_an_unparseable_record_leaves_every_routine_pending(imported_agent):
    slug, sentinels = imported_agent
    code_trust.trust_path(slug).write_text("routines: [this is: not a mapping\n")

    assert assistant_routines(slug, include_muted=True) == {}
    assert [row.name for row in pending_routines(slug)] == [
        "funding_watch",
        "lp_scanner",
    ]
    assert not any(path.exists() for path in sentinels.values())


@pytest.mark.asyncio
async def test_every_run_path_refuses(imported_agent):
    """``execute``, ``start_continuous`` and ``schedule`` share one resolver."""
    slug, sentinels = imported_agent
    store = routine_store_mod.RoutineStore()
    name = f"{slug}/lp_scanner"

    with pytest.raises(code_trust.PendingConsentError):
        await store.execute(name, {}, "local")
    with pytest.raises(code_trust.PendingConsentError):
        await store.start_continuous(name, {}, "local")
    with pytest.raises(code_trust.PendingConsentError):
        await store.schedule(name, {}, "local", interval_sec=60)

    assert not sentinels["lp_scanner"].exists()
    assert store.list_instances() == []


def test_the_store_lists_a_pending_routine_without_a_schema(imported_agent):
    slug, sentinels = imported_agent
    rows = {r["name"]: r for r in routine_store_mod.RoutineStore().list_routines()}

    row = rows[f"{slug}/lp_scanner"]
    assert row["pending"] is True
    assert row["pending_reason"] == code_trust.PENDING
    assert row["category"] == "Probe"
    assert row["description"] == "lp_scanner description"
    # No pydantic model, so no form — the honest cost of not running it.
    assert row["fields"] == {}
    assert not any(path.exists() for path in sentinels.values())


def test_a_loaded_routine_is_flagged_not_pending(imported_agent):
    slug, _ = imported_agent
    code_trust.allow(slug, "lp_scanner", _source_of(slug, "lp_scanner"))

    rows = {r["name"]: r for r in routine_store_mod.RoutineStore().list_routines()}
    assert rows[f"{slug}/lp_scanner"]["pending"] is False
    assert rows[f"{slug}/lp_scanner"]["fields"] == {
        "value": {"type": "int", "default": 1, "description": "value"}
    }
    assert f"{slug}/funding_watch" in rows  # still listed, still pending


# ── 4. the import ──


def test_apply_arms_the_record_and_says_nothing_ran(isolated_routines):
    """A bundle with two routines lands with both pending and both muted."""
    from condor.agents import bundle as bundle_mod

    source = agent_home("traveller")
    (source / "routines").mkdir(parents=True, exist_ok=True)
    (source / "AGENT.md").write_text(
        "---\nname: Traveller\ndescription: d\nwhen_to_consult: w\n---\n\nTravel.\n"
    )
    for name in ("funding_watch", "lp_scanner"):
        (source / "routines" / f"{name}.py").write_text(
            PROBE.format(sentinel=str(isolated_routines / f"{name}.ran"), desc=name)
        )

    document = json.loads(bundle_mod.render(bundle_mod.build("traveller")))
    plan = bundle_mod.plan(
        bundle_mod.parse(json.dumps(document)), as_slug="arrival", user_id=7
    )
    assert not plan.refusals

    result = bundle_mod.apply(plan, user_id=7)

    assert code_trust.is_armed("arrival") is True
    assert code_trust.load("arrival").routines == {}
    assert sorted(load_mutes("arrival")["routines"]) == ["funding_watch", "lp_scanner"]
    assert [row.name for row in pending_routines("arrival")] == [
        "funding_watch",
        "lp_scanner",
    ]
    assert not (isolated_routines / "funding_watch.ran").exists()
    assert not (isolated_routines / "lp_scanner.ran").exists()
    assert "Nothing is running" in result["next_step"]
    assert "allow" in result["next_step"]


def test_a_bundle_carrying_the_record_is_refused(isolated_routines):
    from condor.agents import bundle as bundle_mod

    refusal = bundle_mod._refuse_path("store/code_trust.yml")
    assert "not part of an agent's library" in refusal


def test_the_record_is_not_exported(isolated_routines):
    from condor.layering import publishable_files

    code_trust.arm("perps")
    home = agent_home("perps")
    (home / "routines").mkdir(parents=True, exist_ok=True)
    (home / "routines" / "lp_scanner.py").write_text("CATEGORY = 'x'\n")

    carried = {str(p) for p in publishable_files(home)}
    assert carried == {"routines/lp_scanner.py"}
    assert yaml.safe_load(code_trust.trust_path("perps").read_text()) == {
        "routines": {}
    }
