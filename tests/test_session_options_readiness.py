"""The model picker offers what this machine can actually run (ARCH-*).

``/sessions/options`` used to list every key in the catalog unconditionally, so
picking Ollama on a box with no Ollama server — or a CLI bridge that was never
installed — failed at session start, which costs the session (see
``test_session_switch``). The readiness probe behind ``condor doctor`` and the
setup wizard now annotates the rows, so the reason is on the row before the
pick rather than in a banner after it.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.llm.readiness import MISSING, READY, UNVERIFIED, Readiness
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import sessions as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")

STATES = {
    "claude-code": Readiness(READY, "installed and logged in"),
    "claude-acp": Readiness(READY, "installed and logged in"),
    "gemini": Readiness(UNVERIFIED, "installed; run `npx @google/gemini-cli` once"),
    "copilot": Readiness(MISSING, "not installed — npm install -g @github/copilot"),
    "codex": Readiness(READY, "installed and logged in"),
    "ollama": Readiness(MISSING, "not reachable at http://localhost:11434/v1"),
    "lmstudio": Readiness(READY, "2 model(s) available"),
    "openrouter": Readiness(MISSING, "needs an API key"),
    "custom": Readiness(UNVERIFIED, "cannot be checked from here"),
}


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def get_accessible_servers(self, user_id):
        return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes, "get_config_manager", lambda: FakeConfigManager())
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr("condor.preferences.get_custom_providers", lambda *a: [])
    monkeypatch.setattr("condor.preferences.get_active_agent_key", lambda *a: "")
    # The cache is module state, so a previous test's sweep must not answer this
    # one's request.
    monkeypatch.setattr(routes, "_readiness_cache", (0.0, {}))

    async def fake_probe_all(bases, env=None):
        return {b: STATES[b] for b in dict.fromkeys(bases)}

    monkeypatch.setattr("condor.llm.readiness.probe_all", fake_probe_all)

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _rows(client) -> dict[str, dict]:
    res = client.get("/sessions/options")
    assert res.status_code == 200
    return {a["key"]: a for a in res.json()["agents"]}


def test_a_provider_that_is_not_here_is_marked_unrunnable_with_the_fix(client):
    rows = _rows(client)
    assert rows["ollama:"]["ready"] is False
    assert "not reachable" in rows["ollama:"]["detail"], "the row names the fix"
    assert rows["copilot"]["ready"] is False


def test_an_unproven_login_stays_pickable(client):
    """UNVERIFIED is a heuristic about a credentials file, never a refusal."""
    rows = _rows(client)
    assert rows["gemini"]["ready"] is True
    assert rows["claude-acp:opus"]["ready"] is True, "the suffix probes as its base"


def test_a_probe_that_fails_leaves_every_row_offered(client, monkeypatch):
    async def boom(bases, env=None):
        raise OSError("no npm, no sockets")

    monkeypatch.setattr("condor.llm.readiness.probe_all", boom)
    monkeypatch.setattr(routes, "_readiness_cache", (0.0, {}))

    rows = _rows(client)
    assert all("ready" not in r for r in rows.values()), (
        "unannotated reads as 'offer it' — a picker that cannot probe is not "
        "a picker with nothing in it"
    )
