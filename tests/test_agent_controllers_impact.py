"""A controller push's impact, the preview it requires, and the prompt that shows it (FEAT-129).

Every server is a fake (``FakeControllers`` from the FEAT-126 tests, plus a fake
bot-orchestration router): nothing reaches a real API.
"""

from __future__ import annotations

import asyncio

import pytest

from condor import agent_controllers as ac
from condor import agent_controllers_sync as sync_mod
from condor.agent_controllers_sync import (
    PREVIEW_TTL,
    ControllerImpact,
    LiveBot,
    SharedOwner,
    clear_previews,
    controller_impact,
    preview_for,
    sync_controller,
    upload_sample_config,
)
from condor.runtime import confirmations as confirmations_module
from condor.runtime.confirmations import (
    NO_PREVIEW_DETAIL,
    ConfirmationRegistry,
    build_permission_callback,
)
from tests.test_agent_controllers_sync import MM_SOURCE, FakeControllers


class FakeBots:
    """``bot_orchestration``: a status payload, or a raise."""

    def __init__(self, bots: dict[str, dict] | None = None):
        self.bots = bots or {}
        self.fail: Exception | None = None
        self.payload = None

    async def get_active_bots_status(self):
        if self.fail:
            raise self.fail
        if self.payload is not None:
            return self.payload
        return {
            "status": "success",
            "data": {n: {"status": "running"} for n in self.bots},
        }


class FakeClient:
    def __init__(self, bots: dict[str, list[dict]] | None = None):
        self.controllers = FakeControllers()
        self.bot_configs = bots or {}
        self.bot_orchestration = FakeBots(self.bot_configs)
        self.controllers.get_bot_controller_configs = self._bot_configs
        self.config_fail: Exception | None = None

    async def _bot_configs(self, bot_name):
        if self.config_fail:
            raise self.config_fail
        return self.bot_configs.get(bot_name, [])


def _run(coro):
    return asyncio.run(coro)


def _write_controller(root, name, source):
    d = root / "controllers" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(source)


def _agent(tmp_path, slug):
    base = tmp_path / "agents" / slug
    base.mkdir(parents=True, exist_ok=True)
    (base / "AGENT.md").write_text(f"---\nname: {slug}\n---\n\nBody.\n")
    return base


@pytest.fixture(autouse=True)
def _no_previews():
    clear_previews()
    yield
    clear_previews()


@pytest.fixture
def home(tmp_path):
    base = _agent(tmp_path, "mm")
    _write_controller(base, "pmm_king", MM_SOURCE)
    return base


def _src(slug="mm"):
    return ac.get_controller(slug, "pmm_king")


PMM_CONFIG = {
    "id": "king_btc",
    "controller_name": "pmm_king",
    "controller_type": "market_making",
}


# ── 1. The impact, standalone ──


def test_shared_owners_with_same_and_different_code(home, tmp_path):
    _write_controller(_agent(tmp_path, "twin"), "pmm_king", MM_SOURCE)
    _write_controller(_agent(tmp_path, "rival"), "pmm_king", MM_SOURCE + "# v2\n")
    _write_controller(_agent(tmp_path, "other"), "chessboard", "class A: pass\n")

    impact = _run(controller_impact(FakeClient(), "mm", _src(), "srv"))

    assert impact.shared_owners == (
        SharedOwner("rival", False),
        SharedOwner("twin", True),
    )


def test_a_shared_library_copy_is_reported_once(home, tmp_path):
    shared = tmp_path / "agents" / "_shared"
    _write_controller(shared, "pmm_king", MM_SOURCE + "# library\n")
    # Two agents that only inherit it: still one "_shared" owner.
    _agent(tmp_path, "a1")
    _agent(tmp_path, "a2")

    impact = _run(controller_impact(FakeClient(), "mm", _src(), "srv"))

    assert impact.shared_owners == (SharedOwner("_shared", False),)


def test_a_live_bot_matches_by_name_and_a_type_mismatch_is_excluded(home):
    client = FakeClient(
        {
            "king-btc": [PMM_CONFIG, {**PMM_CONFIG, "id": "king_eth"}],
            "other-bot": [{"id": "x", "controller_name": "chessboard"}],
            "wrong-type": [
                {"id": "g", "controller_name": "pmm_king", "controller_type": "generic"}
            ],
            "untyped": [{"id": "u", "controller_name": "pmm_king"}],
        }
    )

    impact = _run(controller_impact(client, "mm", _src(), "srv"))

    assert impact.live_bots == (
        LiveBot("king-btc", ("king_btc", "king_eth")),
        LiveBot("untyped", ("u",)),
    )
    text = impact.render(overwriting=True)
    assert "king-btc (2 configs)" in text and "untyped (1 config)" in text
    assert "OLD class until stopped, archived and redeployed" in text
    assert "Do not restart them" in text


@pytest.mark.parametrize(
    "mode", ["raise", "error_payload", "config_raise", "no_router"]
)
def test_failing_to_list_bots_is_unknown_never_none(home, mode):
    client = FakeClient({"king-btc": [PMM_CONFIG]})
    if mode == "raise":
        client.bot_orchestration.fail = ConnectionError("down")
    elif mode == "error_payload":
        client.bot_orchestration.payload = {"status": "error", "message": "boom"}
    elif mode == "config_raise":
        client.config_fail = RuntimeError("500")
    else:
        del client.bot_orchestration

    impact = _run(controller_impact(client, "mm", _src(), "srv"))

    assert impact.live_bots is None and impact.live_bots_error
    assert impact.to_dict()["live_bots"] is None
    text = impact.render(overwriting=True)
    assert "could not check running bots" in text
    assert "unknown, not as none" in text
    assert "Running bots using it: none" not in text


def test_a_slow_bot_config_read_is_unknown(home, monkeypatch):
    import condor.fetchers.bots as bots_mod

    monkeypatch.setattr(bots_mod, "ENRICHMENT_TIMEOUT", 0.05)
    client = FakeClient({"king-btc": [PMM_CONFIG]})

    async def slow(_bot):
        await asyncio.sleep(1)
        return [PMM_CONFIG]

    client.controllers.get_bot_controller_configs = slow
    impact = _run(controller_impact(client, "mm", _src(), "srv"))
    assert impact.live_bots is None and "timed out" in impact.live_bots_error


def test_render_always_states_every_copy_and_backtests():
    empty = ControllerImpact("pmm_king", "brigado", (), ())
    for overwriting in (True, False):
        text = empty.render(overwriting=overwriting)
        assert "SERVER copy" in text and "FOLDER copy" in text
        assert "Other agents with a 'pmm_king': none" in text
        assert "Running bots using it: none" in text
        # Each backtest runs in a fresh worker that imports the server copy.
        assert (
            "The next backtest and any bot deployed from now on use the new code"
            in text
        )
        assert "no API restart needed" in text
        _assert_no_restart_the_api(text)
        assert "OLD class" not in text
    assert empty.render(overwriting=True).startswith("This replaces")
    assert empty.render(overwriting=False).startswith("Replaced")

    created = ControllerImpact("pmm_king", "brigado", (), (), server_had_copy=False)
    text = created.render(overwriting=False)
    assert text.startswith("Created the SERVER copy") and "FOLDER copy" in text
    assert "The next backtest and any bot deployed from now on use the new code" in text
    _assert_no_restart_the_api(text)
    assert "Running bots using it: none" in text and "next backtest" in text


def _assert_no_restart_the_api(text):
    """No controller-sync text ever asks for an API restart."""
    lowered = text.lower()
    for phrase in ("restart the api", "api restarts", "until the api", "reaps"):
        assert phrase not in lowered, phrase


# ── 2. Preview registry and sync wiring ──


def _drifted(bots=None):
    client = FakeClient(bots)
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE + "# hotfix\n"
    return client


def test_the_drift_refusal_carries_the_impact_and_records_a_preview(home):
    client = _drifted({"king-btc": [PMM_CONFIG]})
    out = _run(sync_controller(client, "mm", _src(), "srv"))

    assert out["refused"] and out["diff"] and client.controllers.posts == []
    assert out["impact"]["live_bots"] == [
        {"bot_name": "king-btc", "config_ids": ["king_btc"]}
    ]
    assert out["impact_text"].startswith("This replaces the SERVER copy")
    assert "impact" in out["reason"]
    preview = preview_for("mm", "pmm_king", "srv")
    assert preview is not None and preview.impact.live_bots[0].bot_name == "king-btc"


def test_overwrite_without_a_preview_is_refused_and_sends_nothing(home):
    client = _drifted()
    out = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))

    assert out["refused"] and out["preview_required"] is True
    assert out["reason"].startswith("preview_required")
    assert out["diff"] and out["impact_text"]
    assert client.controllers.posts == []
    assert not (home / "controllers" / "pmm_king" / ac.BACKUPS_DIRNAME).exists()
    # The refusal is itself the preview: the retry goes through.
    again = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))
    assert again["overwritten"] is True


def test_overwrite_after_the_server_changed_is_refused(home):
    client = _drifted()
    _run(sync_controller(client, "mm", _src(), "srv"))
    # Someone edits the server after the human saw the preview.
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE + "# other\n"

    out = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))

    assert out["refused"] and out["preview_required"] is True
    assert "changed since the last preview" in out["reason"]
    assert client.controllers.posts == []


def test_overwrite_after_the_folder_changed_is_refused(home):
    client = _drifted()
    _run(sync_controller(client, "mm", _src(), "srv"))
    (home / "controllers" / "pmm_king" / "pmm_king.py").write_text(MM_SOURCE + "# v3\n")

    out = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))

    assert out["refused"] and out["preview_required"] is True
    assert client.controllers.posts == []


def test_overwrite_with_a_matching_preview_returns_the_post_impact(home):
    client = _drifted({"king-btc": [PMM_CONFIG]})
    _run(sync_controller(client, "mm", _src(), "brigado"))

    out = _run(sync_controller(client, "mm", _src(), "brigado", overwrite=True))

    assert out["overwritten"] is True and "backtest_cache_stale" not in out
    assert client.controllers.code[("market_making", "pmm_king")] == MM_SOURCE
    assert out["impact_text"].startswith("Replaced the SERVER copy")
    assert "king-btc" in out["impact_text"]
    assert (
        "The next backtest and any bot deployed from now on use the new code"
        in out["message"]
    )
    _assert_no_restart_the_api(out["message"])
    assert out["impact"]["live_bots"][0]["bot_name"] == "king-btc"
    # One preview, one overwrite.
    assert preview_for("mm", "pmm_king", "brigado") is None


def test_previews_are_per_agent_and_server(home):
    client = _drifted()
    _run(sync_controller(client, "mm", _src(), "srv"))
    assert preview_for("mm", "pmm_king", "other") is None
    assert preview_for("someone", "pmm_king", "srv") is None
    out = _run(sync_controller(client, "mm", _src(), "other", overwrite=True))
    assert out["preview_required"] is True


def test_a_preview_expires(home, monkeypatch):
    client = _drifted()
    _run(sync_controller(client, "mm", _src(), "srv"))
    real = sync_mod.time.time()
    monkeypatch.setattr(sync_mod.time, "time", lambda: real + PREVIEW_TTL + 1)

    assert preview_for("mm", "pmm_king", "srv") is None
    out = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))
    assert out["preview_required"] is True and client.controllers.posts == []


def test_missing_reports_shared_owners_and_needs_no_preview(home, tmp_path):
    _write_controller(_agent(tmp_path, "rival"), "pmm_king", MM_SOURCE + "# v2\n")
    client = FakeClient({"king-btc": [PMM_CONFIG]})

    out = _run(sync_controller(client, "mm", _src(), "srv"))

    assert out["created"] is True and len(client.controllers.posts) == 1
    assert out["impact"]["shared_owners"] == [{"agent": "rival", "same_code": False}]
    assert out["impact"]["live_bots"] == []
    assert out["impact_text"].startswith("Created the SERVER copy")
    assert preview_for("mm", "pmm_king", "srv") is None


def test_in_sync_and_type_mismatch_are_unchanged(home):
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE
    out = _run(sync_controller(client, "mm", _src(), "srv"))
    assert out["verdict"] == "in_sync" and "impact" not in out

    client = FakeClient()
    client.controllers.code[("generic", "pmm_king")] = MM_SOURCE
    out = _run(sync_controller(client, "mm", _src(), "srv"))
    assert out["refused"] and "type folders" in out["reason"] and "impact" not in out


def test_upload_config_overwrite_says_running_bots_keep_their_config(home):
    samples = home / "controllers" / "pmm_king" / "sample_configs"
    samples.mkdir()
    (samples / "aggressive.yml").write_text("connector_name: binance\nspread: 0.01\n")
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE
    client.controllers.configs["pmm_king__aggressive"] = {"spread": 0.5}

    out = _run(
        upload_sample_config(client, _src(), "aggressive", "srv", overwrite=True)
    )

    assert out["replaced"] is True
    assert "Running bots keep the config they were deployed with" in out["message"]


# ── 3. The confirmation shows the preview ──

OPTIONS = [
    {"optionId": "allow", "kind": "allow_once"},
    {"optionId": "deny", "kind": "reject_once"},
]


class _RecordingChannel:
    def __init__(self):
        self.delivered = []

    async def deliver(self, pending):
        self.delivered.append(pending)


@pytest.fixture
def registry(monkeypatch):
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


def _session(monkeypatch, key, **fields):
    from tests.runtime.test_confirmations import _install_session

    return _install_session(monkeypatch, key, **fields)


def _ask(registry, session_key, tool_call):
    """Run one call through the gate, deny it, and return what the human saw."""
    channel = _RecordingChannel()
    cb = build_permission_callback(session_key, 1, [channel], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(cb(tool_call, OPTIONS))
        for _ in range(200):
            if channel.delivered:
                break
            await asyncio.sleep(0.005)
        pending = channel.delivered[0]
        await registry.resolve(pending.id, approved=False, by_user_id=1)
        await task
        return pending

    return asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def _overwrite_call(**extra):
    return {
        "tool": "mcp__condor__manage_agent_controllers",
        "input": {"action": "sync", "name": "pmm_king", "overwrite": True, **extra},
    }


def test_an_overwrite_confirmation_carries_the_recorded_impact(
    home, registry, monkeypatch
):
    from condor.runtime.keys import SessionKey

    client = _drifted({"king-btc": [PMM_CONFIG]})
    _run(sync_controller(client, "mm", _src(), "brigado"))
    key = SessionKey.telegram(42)
    _session(monkeypatch, key, agent_slug="mm", server_name="brigado")

    pending = _ask(registry, str(key), _overwrite_call())

    expected = preview_for("mm", "pmm_king", "brigado").impact.render(overwriting=True)
    assert pending.detail == expected and "king-btc" in pending.detail
    assert pending.to_wire()["detail"] == expected

    sent = {}

    class _Bot:
        async def send_message(self, **kwargs):
            sent.update(kwargs)

    from condor.runtime.channels import TelegramChannel

    asyncio.run(TelegramChannel(_Bot(), 42).deliver(pending))
    assert expected in sent["text"]
    assert sent["text"].index(pending.summary) < sent["text"].index(expected)
    assert sent["text"].endswith("Approve this action?")


def test_condor_targeting_another_agent_reads_that_agents_preview(
    home, registry, monkeypatch
):
    from condor.runtime.keys import SessionKey

    _run(sync_controller(_drifted(), "mm", _src(), "brigado"))
    key = SessionKey.telegram(43)
    _session(monkeypatch, key, agent_slug="", server_name="brigado")

    pending = _ask(registry, str(key), _overwrite_call(agent="mm"))
    assert pending.detail.startswith("This replaces the SERVER copy of 'pmm_king'")


def test_with_no_preview_the_confirmation_says_it_will_be_refused(
    home, registry, monkeypatch
):
    from condor.runtime.keys import SessionKey

    key = SessionKey.telegram(44)
    _session(monkeypatch, key, agent_slug="mm", server_name="brigado")

    pending = _ask(registry, str(key), _overwrite_call())
    assert pending.detail == NO_PREVIEW_DETAIL


def test_a_detail_that_raises_still_registers_the_confirmation(
    home, registry, monkeypatch
):
    from condor.runtime.keys import SessionKey

    def boom(*_a, **_k):
        raise RuntimeError("preview store exploded")

    monkeypatch.setattr(sync_mod, "preview_for", boom)
    key = SessionKey.telegram(45)
    _session(monkeypatch, key, agent_slug="mm", server_name="brigado")

    pending = _ask(registry, str(key), _overwrite_call())
    assert pending.detail == confirmations_module.DETAIL_UNAVAILABLE


def test_other_tools_have_no_detail(registry):
    pending = _ask(
        registry, "tg:31337", {"tool": "place_order", "input": {"trading_pair": "X"}}
    )
    assert pending.detail == ""
    # A non-overwriting controller call is not even gated; an upload_config
    # overwrite is, but carries no preview.
    pending = _ask(
        registry,
        "tg:31337",
        {
            "tool": "manage_agent_controllers",
            "input": {
                "action": "upload_config",
                "name": "pmm_king",
                "sample": "aggressive",
                "overwrite": True,
            },
        },
    )
    assert pending.detail == ""
