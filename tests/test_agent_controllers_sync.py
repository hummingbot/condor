"""An agent's controllers against a Hummingbot API server (FEAT-126).

Every server here is :class:`FakeControllers` (modelled on
``test_hummingbot_mcp_tools.FakeControllers``): nothing reaches a real API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor import agent_controllers as ac
from condor.agent_controllers_sync import (
    MAX_DIFF_LINES,
    clear_previews,
    controller_statuses,
    pull_controller,
    sync_controller,
    unified_diff,
    upload_sample_config,
)

MM_SOURCE = '''"""Pure maker around mid."""
from x import MarketMakingControllerBase


class PmmKing(MarketMakingControllerBase):
    pass
'''


class FakeControllers:
    """The controllers router, in memory. Raises when ``down``."""

    def __init__(self):
        self.code: dict[tuple[str, str], str] = {}
        self.configs: dict[str, dict] = {}
        self.posts: list[tuple] = []
        self.config_posts: list[tuple] = []
        self.down = False
        self.reject_config: str | None = None

    def _check(self):
        if self.down:
            raise ConnectionError("server down")

    async def list_controllers(self):
        self._check()
        out: dict[str, list[str]] = {
            "directional_trading": [],
            "market_making": [],
            "generic": [],
        }
        for ctype, name in self.code:
            out[ctype].append(name)
        return out

    async def get_controller(self, controller_type, controller_name):
        self._check()
        key = (controller_type, controller_name)
        if key not in self.code:
            raise KeyError(f"404 {controller_name}")
        return {
            "name": controller_name,
            "type": controller_type,
            "content": self.code[key],
        }

    async def create_or_update_controller(
        self, controller_type, controller_name, controller_data
    ):
        self._check()
        self.posts.append((controller_type, controller_name, controller_data))
        self.code[(controller_type, controller_name)] = controller_data["content"]
        return {"message": "saved"}

    async def get_controller_config_template(self, controller_type, controller_name):
        return {}

    async def validate_controller_config(self, controller_type, controller_name, cfg):
        if self.reject_config:
            raise ValueError(self.reject_config)
        return {"message": "Configuration is valid"}

    async def list_controller_configs(self):
        self._check()
        return [{"id": k, **v} for k, v in self.configs.items()]

    async def get_controller_config(self, config_name):
        self._check()
        if config_name not in self.configs:
            raise KeyError(f"404 {config_name}")
        return {**self.configs[config_name], "id": config_name}

    async def create_or_update_controller_config(self, config_name, config):
        self.config_posts.append((config_name, config))
        self.configs[config_name] = {k: v for k, v in config.items() if k != "id"}
        return {"message": "saved"}


class FakeClient:
    def __init__(self):
        self.controllers = FakeControllers()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_previews():
    """Impact previews are process-global (FEAT-129): none leak between tests."""
    clear_previews()
    yield
    clear_previews()


@pytest.fixture
def home(tmp_path):
    base = tmp_path / "agents" / "mm"
    base.mkdir(parents=True)
    (base / "AGENT.md").write_text("---\nname: mm\n---\n\nBody.\n")
    d = base / "controllers" / "pmm_king"
    (d / "sample_configs").mkdir(parents=True)
    (d / "pmm_king.py").write_text(MM_SOURCE)
    (d / "sample_configs" / "aggressive.yml").write_text(
        "connector_name: binance\nspread: 0.01\nposition_mode: PositionMode.ONEWAY\n"
    )
    return base


def _src():
    return ac.get_controller("mm", "pmm_king")


# ── status ──


def test_status_verdicts(home):
    client = FakeClient()
    assert _run(controller_statuses(client, [_src()]))["pmm_king"].verdict == "missing"

    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE.replace(
        "\n", "\r\n"
    )
    status = _run(controller_statuses(client, [_src()]))["pmm_king"]
    assert status.verdict == "in_sync" and status.server_digest == _src().digest

    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE + "# hotfix\n"
    assert _run(controller_statuses(client, [_src()]))["pmm_king"].verdict == "drift"


def test_a_server_error_is_unreachable_never_in_sync(home):
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE
    client.controllers.down = True
    status = _run(controller_statuses(client, [_src()]))["pmm_king"]
    assert status.verdict == "unreachable" and "server down" in status.detail


def test_a_failed_get_is_unreachable_too(home):
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE

    async def boom(*_a):
        raise RuntimeError("500")

    client.controllers.get_controller = boom
    assert _run(controller_statuses(client, [_src()]))["pmm_king"].verdict == (
        "unreachable"
    )


def test_a_different_type_folder_is_drift_and_never_synced(home):
    client = FakeClient()
    client.controllers.code[("generic", "pmm_king")] = MM_SOURCE
    status = _run(controller_statuses(client, [_src()]))["pmm_king"]
    assert status.verdict == "drift" and status.server_type == "generic"

    out = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))
    assert out["refused"] and client.controllers.posts == []


# ── sync ──


def test_sync_creates_a_missing_controller_with_an_object_body(home):
    client = FakeClient()
    out = _run(sync_controller(client, "mm", _src(), "srv"))
    assert out["changed"] and out["created"] and "backtest_cache_stale" not in out
    ((ctype, name, body),) = client.controllers.posts
    assert (ctype, name) == ("market_making", "pmm_king")
    assert body == {"content": MM_SOURCE, "type": "market_making"}


def test_sync_in_sync_sends_nothing(home):
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE
    out = _run(sync_controller(client, "mm", _src(), "srv"))
    assert out["changed"] is False and client.controllers.posts == []


def test_sync_refuses_drift_with_a_diff_and_no_post(home):
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE + "# hotfix\n"
    out = _run(sync_controller(client, "mm", _src(), "srv"))
    assert out["refused"] and out["verdict"] == "drift"
    assert "-# hotfix" in out["diff"]
    assert client.controllers.posts == []
    assert not (home / "controllers" / "pmm_king" / ac.BACKUPS_DIRNAME).exists()


def test_sync_overwrite_backs_up_then_posts_and_says_backtests_are_stale(home):
    client = FakeClient()
    server_copy = MM_SOURCE + "# hotfix\n"
    client.controllers.code[("market_making", "pmm_king")] = server_copy
    # The drift refusal is the preview an overwrite requires (FEAT-129).
    assert _run(sync_controller(client, "mm", _src(), "brigado"))["refused"]
    out = _run(sync_controller(client, "mm", _src(), "brigado", overwrite=True))

    backup = Path(out["backup"])
    assert backup.parent == home / "controllers" / "pmm_king" / ac.BACKUPS_DIRNAME
    assert backup.name.startswith("brigado-") and backup.read_text() == server_copy
    assert out["backtest_cache_stale"] is True and "restart" in out["message"]
    assert client.controllers.code[("market_making", "pmm_king")] == MM_SOURCE
    # Nothing new is discovered from the backup dir.
    assert list(ac.agent_controllers("mm")) == ["pmm_king"]


def test_sync_refuses_when_unreachable(home):
    client = FakeClient()
    client.controllers.down = True
    out = _run(sync_controller(client, "mm", _src(), "srv", overwrite=True))
    assert out["refused"] and out["verdict"] == "unreachable"


def test_sync_refuses_an_unknown_type(home):
    (home / "controllers" / "pmm_king" / "pmm_king.py").write_text("class A: pass\n")
    with pytest.raises(ac.ControllerError, match="CONTROLLER.md"):
        _run(sync_controller(FakeClient(), "mm", _src(), "srv"))


def test_the_diff_is_capped():
    server = "".join(f"a{i}\n" for i in range(500))
    folder = "".join(f"b{i}\n" for i in range(500))
    diff = unified_diff(server, folder, "x", "srv")
    assert len(diff.splitlines()) == MAX_DIFF_LINES + 1
    assert "more diff lines" in diff


# ── upload_config ──


def _synced():
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE
    return client


def test_upload_config_uses_the_namespaced_default_name(home):
    client = _synced()
    out = _run(upload_sample_config(client, _src(), "aggressive", "srv"))
    assert out["changed"] and out["config_name"] == "pmm_king__aggressive"
    ((name, body),) = client.controllers.config_posts
    assert name == "pmm_king__aggressive"
    assert body["controller_name"] == "pmm_king"
    assert body["controller_type"] == "market_making"
    assert body["position_mode"] == "ONEWAY"  # cleaned like any save


def test_upload_config_is_idempotent(home):
    client = _synced()
    _run(upload_sample_config(client, _src(), "aggressive", "srv"))
    out = _run(upload_sample_config(client, _src(), "aggressive", "srv"))
    assert out["changed"] is False and len(client.controllers.config_posts) == 1


def test_upload_config_refuses_a_differing_config_unless_overwrite(home):
    client = _synced()
    client.controllers.configs["pmm_king__aggressive"] = {
        "controller_name": "pmm_king",
        "controller_type": "market_making",
        "connector_name": "binance",
        "spread": 0.05,
        "position_mode": "ONEWAY",
    }
    out = _run(upload_sample_config(client, _src(), "aggressive", "srv"))
    assert out["refused"] and "spread: 0.05" in out["diff"]
    assert client.controllers.config_posts == []

    out = _run(
        upload_sample_config(client, _src(), "aggressive", "srv", overwrite=True)
    )
    assert out["changed"] and out["replaced"]
    assert client.controllers.configs["pmm_king__aggressive"]["spread"] == 0.01


def test_upload_config_needs_the_controller_synced_first(home):
    out = _run(upload_sample_config(FakeClient(), _src(), "aggressive", "srv"))
    assert out["refused"] and "sync first" in out["reason"]


def test_upload_config_surfaces_validation_errors_verbatim(home):
    client = _synced()
    client.controllers.reject_config = "spread: Input should be a valid number"
    out = _run(upload_sample_config(client, _src(), "aggressive", "srv"))
    assert out["refused"] and out["error"] == "spread: Input should be a valid number"


def test_upload_config_honours_config_name(home):
    client = _synced()
    out = _run(
        upload_sample_config(client, _src(), "aggressive", "srv", config_name="mine")
    )
    assert out["config_name"] == "mine" and "mine" in client.controllers.configs


# ── pull ──


def test_pull_writes_local_only_strips_ids_and_round_trips(home, tmp_path):
    client = FakeClient()
    client.controllers.code[("market_making", "rebate_mill")] = MM_SOURCE
    client.controllers.configs["rebate_mill__tight"] = {
        "controller_name": "rebate_mill",
        "controller_type": "market_making",
        "_config_name": "x",
        "spread": 0.001,
    }
    out = _run(
        pull_controller(
            client, "mm", "market_making", "rebate_mill", ["rebate_mill__tight"]
        )
    )
    assert out["changed"] and out["styles_written"] == ["tight"]

    local = tmp_path / "agents" / "mm" / "controllers" / "rebate_mill"
    assert (local / "rebate_mill.py").read_text() == MM_SOURCE
    sample = (local / "sample_configs" / "tight.yml").read_text()
    assert "id:" not in sample and "_config_name" not in sample
    assert not (tmp_path / "stock-agents" / "mm").exists()

    src = ac.get_controller("mm", "rebate_mill")
    assert _run(controller_statuses(client, [src]))["rebate_mill"].verdict == "in_sync"


def test_pull_refuses_to_clobber_a_differing_folder_copy(home):
    client = FakeClient()
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE + "# server\n"
    out = _run(pull_controller(client, "mm", "market_making", "pmm_king"))
    assert out["refused"] and "overwrite=true" in out["reason"]
    assert "# server" not in _src().source_path.read_text()

    out = _run(
        pull_controller(client, "mm", "market_making", "pmm_king", overwrite=True)
    )
    assert out["changed"] and "# server" in _src().source_path.read_text()


def test_pull_records_the_type_when_the_ast_cannot(home, tmp_path):
    client = FakeClient()
    client.controllers.code[("generic", "odd")] = "class A: pass\n"
    _run(pull_controller(client, "mm", "generic", "odd"))
    assert ac.get_controller("mm", "odd").controller_type == "generic"


# ── routes ──


class FakeConfigManager:
    def __init__(self, client, allowed=("srv",)):
        self.client = client
        self.allowed = set(allowed)

    def has_server_access(self, user_id, server_name, min_permission=None):
        return server_name in self.allowed

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        if self.client is None:
            raise ConnectionError("offline")
        return self.client


@pytest.fixture
def http(home, monkeypatch):
    import condor.web.routes.agents as agents_routes
    import config_manager
    from condor.web.auth import get_current_user
    from condor.web.models import WebUser

    client = _synced()
    cm = FakeConfigManager(client)
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: cm)
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(agents_routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=1, username="u", first_name="U", role="user"
    )
    return TestClient(app), client, cm


def test_route_lists_with_and_without_a_server(http):
    web, client, _cm = http
    plain = web.get("/agents/mm/controllers").json()
    assert plain["controllers"][0]["name"] == "pmm_king"
    assert "server" not in plain["controllers"][0]

    rows = web.get("/agents/mm/controllers", params={"server_name": "srv"}).json()
    assert rows["controllers"][0]["server"]["verdict"] == "in_sync"


def test_route_reads_an_offline_server_as_unreachable(http):
    web, _client, cm = http
    cm.client = None
    rows = web.get("/agents/mm/controllers", params={"server_name": "srv"}).json()
    assert rows["controllers"][0]["server"]["verdict"] == "unreachable"


def test_routes_enforce_server_access(http):
    web, _client, _cm = http
    assert (
        web.get("/agents/mm/controllers", params={"server_name": "nope"}).status_code
        == 403
    )
    assert (
        web.post(
            "/agents/mm/controllers/pmm_king/sync", json={"server_name": "nope"}
        ).status_code
        == 403
    )


def test_route_sync_upload_and_pull(http):
    web, client, _cm = http
    client.controllers.code[("market_making", "pmm_king")] = MM_SOURCE + "# x\n"
    refused = web.post(
        "/agents/mm/controllers/pmm_king/sync", json={"server_name": "srv"}
    ).json()
    assert refused["refused"] and refused["diff"]

    ok = web.post(
        "/agents/mm/controllers/pmm_king/sync",
        json={"server_name": "srv", "overwrite": True},
    ).json()
    assert ok["backtest_cache_stale"] is True

    up = web.post(
        "/agents/mm/controllers/pmm_king/configs/aggressive",
        json={"server_name": "srv"},
    ).json()
    assert up["config_name"] == "pmm_king__aggressive"

    client.controllers.code[("generic", "chessboard")] = "class A: pass\n"
    pulled = web.post(
        "/agents/mm/controllers/pull",
        json={
            "server_name": "srv",
            "controller_type": "generic",
            "controller_name": "chessboard",
        },
    ).json()
    assert pulled["pulled"] is True


def test_route_404s(http):
    web, _client, _cm = http
    assert web.get("/agents/ghost/controllers").status_code == 404
    assert (
        web.post(
            "/agents/mm/controllers/ghost/sync", json={"server_name": "srv"}
        ).status_code
        == 404
    )
