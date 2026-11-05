"""Greptile, PR #314: ``GET /routines`` never imports an agent's own routine,
so the Config form it builds from ``fields`` always showed "No configurable
fields" for one — covering every agent's own routines, shipped and locally
created, not just hub installs.

The chosen fix left the listing alone and added a route the Config form calls
once it is actually open, resolving through :meth:`RoutineStore._resolve_routine`
— the same import an allowed routine's ``execute``/``start_continuous`` already
does — rather than teaching discovery to parse ``Config`` statically. This
covers that route, not the resolution itself (``tests/test_code_trust.py``
covers that one import/pending boundary).
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routines_module
from condor.agents.code_trust import PendingConsentError
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")


class FakeStore:
    def __init__(self, fields=None, error=None):
        self._fields = fields
        self._error = error

    def get_routine_fields(self, routine_name):
        if self._error is not None:
            raise self._error
        return self._fields


@pytest.fixture
def client_for(monkeypatch):
    def _build(store: FakeStore):
        monkeypatch.setattr(routines_module, "get_routine_store", lambda: store)
        app = FastAPI()
        app.include_router(routines_module.router)
        app.dependency_overrides[get_current_user] = lambda: USER
        return TestClient(app)

    return _build


def test_an_allowed_routines_fields_resolve(client_for):
    client = client_for(FakeStore(fields={"value": {"type": "integer", "default": 1}}))

    resp = client.get("/routines/mine/scratch/fields")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"fields": {"value": {"type": "integer", "default": 1}}}


def test_a_pending_routines_fields_are_refused_not_500d(client_for):
    client = client_for(FakeStore(error=PendingConsentError("mine/scratch")))

    resp = client.get("/routines/mine/scratch/fields")

    assert resp.status_code == 403
