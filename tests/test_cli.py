"""Tests for the condor CLI (condor/cli/): env writing, harness
detection, selection parsing, and the stop escape hatch."""

import pytest

import condor.cli as cli
from condor.cli.commands import init as init_cmd


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("SOME_TEMPLATE_KEY=\nADMIN_USER_ID=\n")
    monkeypatch.setattr(init_cmd, "ENV_FILE", env)
    monkeypatch.setattr(init_cmd, "ENV_EXAMPLE", example)
    return env


def test_write_env_var_creates_from_example(env_file):
    init_cmd._write_env_var("ADMIN_USER_ID", "42")
    content = env_file.read_text()
    assert "ADMIN_USER_ID=42" in content
    assert "SOME_TEMPLATE_KEY=" in content  # template lines preserved


def test_write_env_var_is_idempotent(env_file):
    init_cmd._write_env_var("ADMIN_USER_ID", "42")
    init_cmd._write_env_var("ADMIN_USER_ID", "99")
    content = env_file.read_text()
    assert "ADMIN_USER_ID=99" in content
    assert "ADMIN_USER_ID=42" not in content
    assert content.count("ADMIN_USER_ID=") == 1


def test_write_env_var_appends_new_key(env_file):
    init_cmd._write_env_var("NEW_KEY", "value")
    assert "NEW_KEY=value" in env_file.read_text()
    assert init_cmd._read_env_file()["NEW_KEY"] == "value"


def test_detect_harnesses_by_config_dir(tmp_path, monkeypatch):
    """Config dirs count as evidence even when the binary is not on PATH."""
    monkeypatch.setattr(init_cmd.shutil, "which", lambda name: None)
    (tmp_path / ".hermes").mkdir()
    detected = init_cmd.detect_harnesses(home=tmp_path)
    assert detected == {"claude-code": False, "openclaw": False, "hermes": True}


def test_detect_harnesses_by_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        init_cmd.shutil, "which", lambda name: "/bin/x" if name == "claude" else None
    )
    detected = init_cmd.detect_harnesses(home=tmp_path)
    assert detected["claude-code"] is True
    assert detected["openclaw"] is False


def test_select_harnesses_flag_parsing():
    assert init_cmd._select_harnesses("claude-code,hermes") == [
        "claude-code",
        "hermes",
    ]


def test_select_harnesses_none_means_empty():
    assert init_cmd._select_harnesses("none") == []


def test_select_harnesses_rejects_unknown():
    with pytest.raises(SystemExit):
        init_cmd._select_harnesses("cursor")


# -- exit-code contract --------------------------------------------------------------


def test_fail_carries_the_stable_exit_code(capsys):
    from condor.cli.output import ExitCode, fail

    with pytest.raises(SystemExit) as exc:
        fail("boom", ExitCode.NOT_RUNNING)
    assert exc.value.code == 3
    assert "Error: boom (code 3)" in capsys.readouterr().err


def test_socket_down_maps_to_not_running(monkeypatch, capsys):
    from condor.control import client as control_client

    async def fake_call_control(method, params=None, **kw):
        raise control_client.ControlError(503, "control socket unavailable at x")

    monkeypatch.setattr(control_client, "call_control", fake_call_control)
    with pytest.raises(SystemExit) as exc:
        cli.main(["stop"])
    assert exc.value.code == 3
    assert "condor serve" in capsys.readouterr().err


# -- condor stop: the harness-independent escape hatch ------------------------


def test_stop_by_run_id_and_by_slug(monkeypatch, capsys):
    from condor.control import client as control_client

    calls = []

    async def fake_call_control(method, params=None, **kw):
        calls.append((method, params))
        if method == "agent.list":
            return {
                "agents": [
                    {"agent_id": "01KXPDJDW4ZM87R9QY36BDDXX6", "agent_slug": "mm"},
                    {"agent_id": "01KXPDJDW4ZM87R9QY36BDDXX7", "agent_slug": "other"},
                ]
            }
        return {"stopped": True, "closed": params["close"]}

    monkeypatch.setattr(control_client, "call_control", fake_call_control)

    # A ULID target stops exactly that run — no listing round-trip.
    cli.main(["stop", "01KXPDJDW4ZM87R9QY36BDDXX6"])
    assert calls == [
        (
            "agent.verb",
            {
                "slug": "",
                "verb": "stop",
                "agent_id": "01KXPDJDW4ZM87R9QY36BDDXX6",
                "close": False,
            },
        )
    ]
    assert "stopped" in capsys.readouterr().out

    # A slug target stops all ITS live runs (and only its).
    calls.clear()
    cli.main(["stop", "mm", "--close"])
    assert calls[0][0] == "agent.list"
    stops = [p for m, p in calls if m == "agent.verb"]
    assert stops == [
        {
            "slug": "mm",
            "verb": "stop",
            "agent_id": "01KXPDJDW4ZM87R9QY36BDDXX6",
            "close": True,
        }
    ]


def test_stop_reports_no_live_runs(monkeypatch, capsys):
    from condor.control import client as control_client

    async def fake_call_control(method, params=None, **kw):
        return {"agents": []}

    monkeypatch.setattr(control_client, "call_control", fake_call_control)
    cli.main(["stop"])
    assert "no live runs" in capsys.readouterr().out
