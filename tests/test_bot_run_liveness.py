"""A run says whether it is still live, and which controllers it deployed.

Two facts the dashboard needs about a bot run were sitting unread in the
payload, and reading the wrong field for the first one is what filed the live
fleet under "Archived runs" (FEAT-089).

**Liveness.** ``run_status`` looks like the field that answers "is this bot
running", and it is not: upstream never writes ``RUNNING``. Measured over 150
runs on a real server the whole distribution is ``(STOPPED, ARCHIVED)`` x137,
``(CREATED, DEPLOYED)`` x8 and ``(STOPPED, DEPLOYED)`` x5 -- so the eight bots
that were trading at that moment all reported the literal string ``CREATED``.
What "still running" means here is a container that is deployed and has no stop
time.

**Attribution.** ``deployment_config`` is a JSON *string* carrying the deploy
request, and inside it ``controllers_config`` names every controller the run was
started with. That is the run -> controller mapping straight from the
deployment: it is correct for a run with no performance snapshots left at all,
and it is what lets a closed executor be credited to the run that created it
rather than to whichever live bot happens to share its config id. The route used
to discard the whole blob.
"""

from condor.web.routes.controller_performance import (
    _parse_bot_run,
    parse_controller_ids,
)

DEPLOYED = {
    "instance_name": "sell-btcbrl-20260901",
    "controllers_config": ["btcbrl-sell__alloc_5_tp_3bp", "btcbrl-sell__alloc_10_tp_5bp"],
    "image": "hummingbot/hummingbot:latest",
}


# ── Liveness ──


def test_a_deployed_run_with_no_stop_time_is_live_however_it_reports_itself():
    run = _parse_bot_run(
        {
            "bot_name": "sell-btcbrl-20260901-20260831-215524",
            "deployed_at": "2026-08-31T21:55:25.019057+00:00",
            "stopped_at": None,
            "deployment_status": "DEPLOYED",
            # The value every trading bot on a real server actually reports.
            "run_status": "CREATED",
        }
    )
    assert run.is_live is True


def test_a_run_with_a_stop_time_is_not_live_even_while_its_container_stands():
    """``(STOPPED, DEPLOYED)`` is a real pairing: the bot stopped, the container
    has not been reaped yet. It belongs to history, not to the fleet."""
    run = _parse_bot_run(
        {
            "bot_name": "b",
            "deployed_at": "2026-08-21T18:05:02+00:00",
            "stopped_at": "2026-08-25T05:46:25+00:00",
            "deployment_status": "DEPLOYED",
            "run_status": "STOPPED",
        }
    )
    assert run.is_live is False


def test_an_archived_run_is_not_live():
    run = _parse_bot_run(
        {
            "bot_name": "b",
            "stopped_at": "2026-08-25T05:46:25+00:00",
            "deployment_status": "ARCHIVED",
            "run_status": "STOPPED",
        }
    )
    assert run.is_live is False


# ── The controllers the run declared ──


def test_the_run_carries_the_controller_ids_its_deployment_named():
    import json

    run = _parse_bot_run(
        {
            "bot_name": "sell-btcbrl",
            "deployment_status": "DEPLOYED",
            "deployment_config": json.dumps(DEPLOYED),
        }
    )
    assert run.controller_ids == [
        "btcbrl-sell__alloc_5_tp_3bp",
        "btcbrl-sell__alloc_10_tp_5bp",
    ]


def test_the_deploy_blob_itself_is_not_forwarded():
    """Only the ids survive. The rest of the deploy request is bulk nobody
    reads, and forwarding payloads whole is the bloat ``bots.py`` warns about."""
    import json

    run = _parse_bot_run(
        {"bot_name": "b", "deployment_config": json.dumps(DEPLOYED)}
    )
    assert "deployment_config" not in run.model_dump()
    assert "image" not in str(run.model_dump())


def test_a_controller_named_by_its_file_is_the_same_id_as_one_named_bare():
    """A deploy may name the file; every other route names the bare id. Two
    spellings of one controller would attribute nothing."""
    assert parse_controller_ids({"controllers_config": ["main.yml", "grid.yaml"]}) == [
        "main",
        "grid",
    ]


def test_an_unreadable_deployment_config_costs_the_attribution_not_the_run():
    for blob in ("not json", "", None, 7, [], {"controllers_config": "main"}):
        assert parse_controller_ids(blob) == []
    run = _parse_bot_run({"bot_name": "b", "deployment_config": "{oops"})
    assert run.bot_name == "b"
    assert run.controller_ids == []


def test_blank_entries_are_dropped_rather_than_becoming_a_controller_called_nothing():
    assert parse_controller_ids({"controllers_config": ["a", "", "  ", None, "b"]}) == [
        "a",
        "b",
    ]
