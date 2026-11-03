"""CORR-582: a failed stop_controllers must not leave a stale "stopping" mark.

``stop_controllers_endpoint`` marks the controllers as stopping *before* the
upstream call so the UI reacts immediately. When the call fails nothing was
stopped, so ``manual_kill_switch`` never flips — and the only other clearing
path in ``overlay_stopping_state`` is gated on exactly that flag. The mark
therefore survived for the whole 300s transitional TTL, painting the
controllers as "stopping" (a state the upstream payload cannot report: its
``status`` is a hardcoded "running", which is why the overlay exists at all)
and disabling the very button an operator would use to retry.

``stop_bot_endpoint`` has always cleared its own mark on failure; these tests
pin the same symmetry for controllers, and pin that a *successful* stop still
leaves the mark in place so the overlay can show "stopping" until the kill
switch is observed.
"""

import asyncio

import pytest
from fastapi import HTTPException

import condor.web.routes.bots as bots_module
from condor.web.models import WebUser
from condor.web.routes.bots import (
    ControllerActionRequest,
    get_stopping_bots,
    get_stopping_controllers,
)

_USER = WebUser(id=1, role="admin")

SERVER = "srv"
BOT = "bot-1"
CONTROLLERS = ["pmm_sol", "pmm_eth"]


class _Controllers:
    """The controllers sub-API ``_set_kill_switches`` actually drives."""

    def __init__(self, update_error: Exception | dict[str, Exception] | None):
        # A plain Exception fails every controller (total failure); a dict
        # fails only the controllers named as keys (partial failure).
        self._update_error = update_error
        self.updated: list[str] = []

    async def get_bot_controller_configs(self, _bot_name):
        return [
            {"id": cid, "_config_name": cid, "manual_kill_switch": cid in self.updated}
            for cid in CONTROLLERS
        ]

    async def update_bot_controller_config(self, _bot_name, config_name, _update):
        error = self._update_error
        if isinstance(error, dict):
            error = error.get(config_name)
        if error is not None:
            raise error
        self.updated.append(config_name)
        return {"updated": True}


class _BotOrchestration:
    def __init__(self, stop_error: Exception | None):
        self._stop_error = stop_error

    async def stop_and_archive_bot(self, _bot_name):
        if self._stop_error is not None:
            raise self._stop_error
        return {"stopped": True}


class _FakeClient:
    def __init__(self, error: Exception | None = None):
        self.controllers = _Controllers(error)
        self.bot_orchestration = _BotOrchestration(error)


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, *_args, **_kwargs):
        return True

    async def get_client(self, _name):
        return self._client


@pytest.fixture
def bind_client(monkeypatch):
    """Point the route module at a client, and start from a clean state store."""

    def _bind(client):
        monkeypatch.setattr(bots_module, "get_config_manager", lambda: _FakeCM(client))
        return client

    for cid in CONTROLLERS:
        bots_module.clear_controller_stopping(SERVER, BOT, cid)
    bots_module.clear_bot_stopping(SERVER, BOT)
    yield _bind
    for cid in CONTROLLERS:
        bots_module.clear_controller_stopping(SERVER, BOT, cid)
    bots_module.clear_bot_stopping(SERVER, BOT)


def _stop_controllers():
    return asyncio.run(
        bots_module.stop_controllers_endpoint(
            name=SERVER,
            bot_name=BOT,
            body=ControllerActionRequest(controller_names=list(CONTROLLERS)),
            user=_USER,
        )
    )


def test_a_failed_stop_clears_the_stopping_marks(bind_client):
    """The real failure path: every update rejected → ValueError → marks gone."""
    bind_client(_FakeClient(RuntimeError("backend rejected the config write")))

    with pytest.raises(HTTPException) as caught:
        _stop_controllers()

    # The caller still gets the same upstream failure response.
    assert caught.value.status_code in (400, 502)

    stopping = get_stopping_controllers(SERVER)
    assert stopping == set(), (
        "a stop that did not happen must not leave controllers painted as "
        f"stopping for the transitional TTL; still marked: {stopping}"
    )


def test_the_overlay_no_longer_paints_a_failed_stop_as_stopping(bind_client):
    """The observable surface: the REST/WS overlay both feed from this state."""
    bind_client(_FakeClient(RuntimeError("backend rejected the config write")))

    with pytest.raises(HTTPException):
        _stop_controllers()

    # The upstream payload: ``status`` is the hardcoded "running" the backend
    # always reports, and the kill switch is still off because nothing stopped.
    controllers = [
        {
            "bot_name": BOT,
            "controller_id": cid,
            "status": "running",
            "config": {"manual_kill_switch": False},
        }
        for cid in CONTROLLERS
    ]
    bots_module.overlay_stopping_state(SERVER, controllers, [])

    assert [c["status"] for c in controllers] == ["running", "running"]


def test_a_successful_stop_keeps_the_marks(bind_client):
    """The mark must survive until the kill switch is actually observed."""
    client = bind_client(_FakeClient())

    result = _stop_controllers()

    assert sorted(result["succeeded"]) == sorted(CONTROLLERS)
    assert get_stopping_controllers(SERVER) == {f"{BOT}:{cid}" for cid in CONTROLLERS}

    # And the overlay renders them as stopping while the flag is still unseen.
    controllers = [
        {
            "bot_name": BOT,
            "controller_id": cid,
            "status": "running",
            "config": {"manual_kill_switch": False},
        }
        for cid in CONTROLLERS
    ]
    bots_module.overlay_stopping_state(SERVER, controllers, [])
    assert [c["status"] for c in controllers] == ["stopping", "stopping"]
    assert client.controllers.updated == CONTROLLERS


def test_the_bot_and_controller_failure_paths_agree(bind_client):
    """Symmetry with ``stop_bot_endpoint``, whose failure path already cleared."""
    bind_client(_FakeClient(RuntimeError("backend down")))

    with pytest.raises(HTTPException):
        asyncio.run(
            bots_module.stop_bot_endpoint(name=SERVER, bot_name=BOT, user=_USER)
        )
    assert get_stopping_bots(SERVER) == set()

    with pytest.raises(HTTPException):
        _stop_controllers()
    assert get_stopping_controllers(SERVER) == set()


def test_a_partial_failure_clears_only_the_failed_marks(bind_client):
    """CORR-619: manage_bot_execution returns 200 with a non-empty ``failed``
    when at least one controller succeeded — the ``except`` branch never
    runs, so the success-path cleanup must clear the failed ids itself while
    leaving the succeeded ones marked stopping.
    """
    failing, ok = CONTROLLERS[0], CONTROLLERS[1]
    client = bind_client(_FakeClient({failing: RuntimeError("backend rejected")}))

    result = _stop_controllers()

    assert result["succeeded"] == [ok]
    assert result["failed"] == {failing: "backend rejected"}

    stopping = get_stopping_controllers(SERVER)
    assert stopping == {f"{BOT}:{ok}"}, (
        "the succeeded controller must stay marked stopping and the failed "
        f"one must not; got {stopping}"
    )
    assert client.controllers.updated == [ok]


def test_a_client_resolution_failure_leaves_no_marks(monkeypatch):
    """cm.get_client failing (outside the upstream call) must not leak marks
    for either endpoint — it now happens inside the ``try`` block.
    """

    class _FailingCM:
        def has_server_access(self, *_args, **_kwargs):
            return True

        async def get_client(self, _name):
            raise RuntimeError("server unreachable")

    monkeypatch.setattr(bots_module, "get_config_manager", lambda: _FailingCM())

    bots_module.clear_bot_stopping(SERVER, BOT)
    for cid in CONTROLLERS:
        bots_module.clear_controller_stopping(SERVER, BOT, cid)

    with pytest.raises(HTTPException):
        asyncio.run(
            bots_module.stop_bot_endpoint(name=SERVER, bot_name=BOT, user=_USER)
        )
    assert get_stopping_bots(SERVER) == set()

    with pytest.raises(HTTPException):
        _stop_controllers()
    assert get_stopping_controllers(SERVER) == set()
