"""The dashboard half of the telemetry consent prompt (FEAT-023).

A Telegram install is asked once, next to the boot notification. An install
running without Telegram has no bot to be asked through, so before this the
question could not be put to it at all: consent stayed ``unknown`` forever and
no amount of willingness could move it off the ping floor. These tests pin the
contract the dashboard consent card is built on.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.settings as settings_routes
from condor.telemetry import consent
from condor.web.auth import get_current_user
from condor.web.models import WebUser

ADMIN = WebUser(id=1, username="admin", first_name="A", role="admin")
TRADER = WebUser(id=2, username="trader", first_name="T", role="user")


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


@pytest.fixture
def app(tmp_path, monkeypatch):
    """An isolated install, so the answers these tests give are written to a
    config.yml that dies with the test."""
    import config_manager as cm_module
    from condor.telemetry import emitter

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", None, raising=False)
    cm_module.ConfigManager.reset_instance()
    emitter.discard_buffer()
    consent.refresh()

    # Only the route's own authorization check is faked; `consent` keeps its
    # real ConfigManager so a PUT genuinely lands on disk.
    monkeypatch.setattr(settings_routes, "get_config_manager", FakeConfigManager)

    api = FastAPI()
    api.include_router(settings_routes.router)
    yield api

    cm_module.ConfigManager.reset_instance()
    emitter.discard_buffer()
    consent.refresh()


def as_user(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_an_install_that_never_answered_reports_itself_as_unanswered(app):
    """`unknown` plus `can_change` is the whole trigger for the consent card."""
    body = as_user(app, ADMIN).get("/settings/telemetry").json()

    assert body["consent"] == "unknown"
    assert body["level"] == "ping"  # counted meanwhile — the floor
    assert body["can_change"] is True
    assert body["env_overridden"] is False


def test_every_seat_may_read_what_the_install_shares(app):
    """Transparency is not an admin feature. Answering is."""
    body = as_user(app, TRADER).get("/settings/telemetry").json()

    assert body["consent"] == "unknown"
    assert body["can_change"] is False
    assert body["disclosure"]["never"]  # the same list, for everyone


def test_the_card_is_served_the_same_words_telegram_sends(app):
    from condor.telemetry.prompt import DISCLOSURE

    body = as_user(app, ADMIN).get("/settings/telemetry").json()
    assert body["disclosure"] == DISCLOSURE


def test_a_non_admin_cannot_answer_for_the_install(app):
    resp = as_user(app, TRADER).put("/settings/telemetry", params={"level": "usage"})

    assert resp.status_code == 403
    assert consent.state() == consent.UNKNOWN


def test_answering_in_the_dashboard_retires_the_question(app):
    """The card's whole job: move consent off `unknown` and record the level.
    Once it is granted the banner stops rendering, on this surface and the
    other one."""
    resp = as_user(app, ADMIN).put("/settings/telemetry", params={"level": "usage"})

    assert resp.status_code == 200
    assert resp.json() == {"level": "usage", "consent": "granted"}
    assert consent.state() == consent.GRANTED
    assert consent.level() == consent.USAGE

    after = as_user(app, ADMIN).get("/settings/telemetry").json()
    assert after["consent"] == "granted"


def test_only_counting_is_a_real_answer_too(app):
    """ "Only count my install" is an answer, not a refusal to answer — it has
    to retire the prompt, or the admin gets asked again forever."""
    as_user(app, ADMIN).put("/settings/telemetry", params={"level": "ping"})

    assert consent.state() == consent.GRANTED
    assert consent.level() == consent.PING


def test_the_dashboard_cannot_switch_install_counting_off(app):
    resp = as_user(app, ADMIN).put("/settings/telemetry", params={"level": "off"})

    assert resp.status_code == 400
    assert consent.state() == consent.UNKNOWN


def test_an_environment_pinned_install_is_not_asked_and_cannot_be_answered(
    app, monkeypatch
):
    """With `CONDOR_TELEMETRY` set there is no question left to put: the card
    reports the override instead of offering buttons, and a PUT is refused."""
    monkeypatch.setattr("utils.config.CONDOR_TELEMETRY", "off", raising=False)
    consent.refresh()

    body = as_user(app, ADMIN).get("/settings/telemetry").json()
    assert body["env_overridden"] is True
    assert body["level"] == "off"

    resp = as_user(app, ADMIN).put("/settings/telemetry", params={"level": "usage"})
    assert resp.status_code == 409
