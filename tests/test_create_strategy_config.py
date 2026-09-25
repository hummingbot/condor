"""Creating a strategy with a config keeps its strategy-specific keys (ARCH-670).

``create_strategy`` used to write the first ``config.yml`` through the narrow
``save_agent_config(AgentConfig.from_dict(...))`` pair, which keeps only
``AgentConfig.model_fields`` — so ``trading_pair``, ``venues`` and friends were
silently dropped from the file. There is now one load/save pair for
``config.yml``: ``load_full_config`` / ``save_full_config``.
"""

import yaml
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.agents as agents_pkg
import condor.agents.config as config_module
from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def has_server_access(self, user_id, server_name, *a, **k):
        return True

    async def get_client(self, server_name):
        raise RuntimeError("no server in this test")


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    AgentStore().create(name="Brigado", description="BRL market making")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def test_create_strategy_keeps_strategy_keys_and_fills_core_defaults(
    tmp_path, monkeypatch
):
    client = _setup(tmp_path, monkeypatch)
    res = client.post(
        "/agents/brigado/strategies",
        json={
            "name": "SOL MM",
            "config": {"trading_pair": "SOL-USDC", "frequency_sec": 30},
        },
    )
    assert res.status_code == 200, res.text

    strategy = StrategyStore().get("brigado", res.json()["slug"])
    saved = yaml.safe_load((strategy.home / "config.yml").read_text())
    assert saved["trading_pair"] == "SOL-USDC"
    assert saved["frequency_sec"] == 30
    assert saved["server_name"] == "local"


def test_the_narrow_config_pair_is_gone():
    for name in ("load_agent_config", "save_agent_config"):
        assert not hasattr(config_module, name)
        assert not hasattr(agents_pkg, name)
        assert name not in agents_pkg.__all__
