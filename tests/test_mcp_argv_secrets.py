"""No secret may ever reach an MCP subprocess's argv (SEC-095).

``ps -eo args`` is world-readable, so anything on a child's command line is
readable by every local process. These tests pin the two halves of the fix: the
spawner puts credentials on the ``env`` channel only, and the startup reaper —
which used to find our subprocess trees by grepping ``ps`` for the bot token —
still finds them through the non-secret marker that replaced it.
"""

import subprocess
import sys

import pytest

BOT_TOKEN = "1234567890:AAtotallySecretTelegramToken"
API_USER = "condor-api-user"
API_PASSWORD = "sup3r-s3cret-api-password"


@pytest.fixture
def session_servers(monkeypatch):
    """``build_mcp_servers_for_session`` with a stub server and a known token."""
    import config_manager

    class _OneServer:
        def get_accessible_servers(self, user_id):
            return ["prod"]

        def has_server_access(self, user_id, name, *a, **kw):
            return True

        def get_server_permission(self, user_id, name):
            from config_manager import ServerPermission

            return ServerPermission.OWNER

        def get_server(self, name):
            return {
                "host": "10.0.0.5",
                "port": 8000,
                "username": API_USER,
                "password": API_PASSWORD,
            }

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _OneServer())
    monkeypatch.setattr(config_manager, "get_effective_server", lambda *a, **k: "prod")
    monkeypatch.setenv("TELEGRAM_TOKEN", BOT_TOKEN)

    from handlers.agents._shared import build_mcp_servers_for_session

    return build_mcp_servers_for_session(42, 42)


def _env_of(server: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in server.get("env", [])}


def test_no_secret_appears_anywhere_in_argv(session_servers):
    """The regression guard: scan every arg of every spawned server."""
    assert len(session_servers) == 2, "expected both condor and mcp-hummingbot"

    for server in session_servers:
        argv = " ".join(server["args"])
        for secret in (BOT_TOKEN, API_PASSWORD, API_USER):
            assert (
                secret not in argv
            ), f"{server['name']} leaks a secret on argv (visible to `ps`): {argv}"
        # The flags that used to carry them are gone too, so a future edit
        # cannot quietly refill them.
        for flag in ("--bot-token", "--password", "--username"):
            assert flag not in server["args"]


def test_secrets_travel_on_the_env_channel_instead(session_servers):
    condor = next(s for s in session_servers if s["name"] == "condor")
    hummingbot = next(s for s in session_servers if s["name"] == "mcp-hummingbot")

    assert _env_of(condor)["TELEGRAM_BOT_TOKEN"] == BOT_TOKEN
    assert _env_of(hummingbot)["HUMMINGBOT_API_USERNAME"] == API_USER
    assert _env_of(hummingbot)["HUMMINGBOT_API_PASSWORD"] == API_PASSWORD

    # Non-secret coordinates stay on argv, where they identify a live process.
    assert "--url" in hummingbot["args"]
    assert "http://10.0.0.5:8000" in hummingbot["args"]
    assert hummingbot["args"][hummingbot["args"].index("--server-name") + 1] == "prod"


def test_both_servers_carry_the_reaper_marker(session_servers):
    from condor.acp.client import bot_process_marker

    marker = bot_process_marker(BOT_TOKEN)
    assert marker and BOT_TOKEN not in marker

    for server in session_servers:
        args = server["args"]
        assert args[args.index("--bot-id") + 1] == marker


def test_marker_is_per_bot_and_absent_without_a_token(monkeypatch):
    from condor.acp.client import bot_process_marker

    assert bot_process_marker(BOT_TOKEN) != bot_process_marker(BOT_TOKEN + "x")
    assert bot_process_marker("") == ""

    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    from handlers.agents._shared import _bot_id_args

    assert _bot_id_args() == []


# ── the reaper still finds our trees without the token on argv ──


def _run_reaper(monkeypatch, rows, token=BOT_TOKEN):
    """Run the reaper against a fake process table.

    ``ps`` itself is faked (rather than ``_ps_rows``/``_descendant_pids``) so the
    real snapshot-and-walk code runs, and every fork the reaper makes is counted.
    Returns ``(pids signalled, argv of each ``ps`` invocation)``.
    """
    from condor.acp import client as acp_client

    signalled: list[int] = []
    ps_calls: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):
        ps_calls.append(list(cmd))
        fmt = cmd[-1]
        if fmt.endswith("args="):
            text = "".join(f"{p} {ppid} {args}\n" for p, ppid, args in rows)
        else:
            text = "".join(f"{p} {ppid}\n" for p, ppid, _ in rows)
        return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")

    monkeypatch.setattr(acp_client.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        acp_client, "_signal_all", lambda pids, _pg, _sig: signalled.extend(pids)
    )
    monkeypatch.setattr(acp_client, "_alive", lambda _p: False)

    acp_client.reap_stale_acp_trees(token, wait_s=0)
    return set(signalled), ps_calls


def _reap_with_ps(monkeypatch, rows, token=BOT_TOKEN):
    """Run the reaper against a fake ``ps`` snapshot; return the pids signalled."""
    return _run_reaper(monkeypatch, rows, token)[0]


def test_reaper_kills_the_tree_seeded_by_the_marker(monkeypatch):
    from condor.acp.client import bot_process_marker

    marker = f"--bot-id {bot_process_marker(BOT_TOKEN)}"
    rows = [
        (100, 1, "node claude-agent-acp"),
        (200, 100, "claude"),
        (300, 200, f"uv run python -m mcp_servers.condor --chat-id 42 {marker}"),
        (400, 200, f"uv run python -m mcp_servers.hummingbot_api --url u {marker}"),
        (999, 1, "some unrelated process"),
    ]
    assert _reap_with_ps(monkeypatch, rows) == {100, 200, 300, 400}


def test_reaper_leaves_interactive_claude_code_sessions_alone(monkeypatch):
    """An interactive session's MCP servers carry no marker of ours."""
    rows = [
        (100, 1, "node claude-code-acp --dangerously-skip-permissions"),
        (300, 100, "uv run python -m mcp_servers.condor"),
    ]
    assert _reap_with_ps(monkeypatch, rows) == set()


def test_reaper_ignores_another_bots_tree(monkeypatch):
    """Two bots on one host: the marker discriminates, as the token used to."""
    from condor.acp.client import bot_process_marker

    other = bot_process_marker("9876543210:AAsomeOtherBotsToken")
    rows = [
        (100, 1, "node claude-agent-acp"),
        (300, 100, f"uv run python -m mcp_servers.condor --bot-id {other}"),
    ]
    assert _reap_with_ps(monkeypatch, rows) == set()


def test_reaper_takes_one_ps_snapshot_however_many_roots(monkeypatch):
    """One ``ps`` per reap, not one per root (PERF-333).

    Three independent leaked trees used to mean the boot-path snapshot plus one
    full ``ps -eo pid=,ppid=`` fork per root — and a pid seen only by one of
    those later snapshots had no argv in the first one, so it slipped past the
    ``_protected`` guard unread. Both go away by walking the snapshot in hand.
    """
    from condor.acp.client import bot_process_marker

    marker = f"--bot-id {bot_process_marker(BOT_TOKEN)}"
    rows = [(999, 1, "some unrelated process")]
    expected: set[int] = set()
    for root in (100, 200, 300):  # three separate acp trees, each nested deep
        rows += [
            (root, 1, "node claude-agent-acp"),
            (root + 1, root, "claude"),
            (root + 2, root + 1, f"uv run python -m mcp_servers.condor {marker}"),
            (root + 3, root + 2, "uv run python -m mcp_servers.hummingbot_api"),
        ]
        expected |= {root, root + 1, root + 2, root + 3}

    signalled, ps_calls = _run_reaper(monkeypatch, rows)

    assert signalled == expected
    assert len(ps_calls) == 1, f"{len(ps_calls)} ps forks for 3 roots: {ps_calls}"
    assert ps_calls[0] == ["ps", "-eo", "pid=,ppid=,args="]


# ── the MCP servers still read what the spawner now sends ──


def test_condor_settings_read_the_token_from_env_not_argv(monkeypatch):
    from mcp_servers.condor import settings as settings_module

    monkeypatch.setattr(sys, "argv", ["prog", "--chat-id", "42"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    assert settings_module._parse_settings().bot_token == BOT_TOKEN

    # Falls back to the inherited name for a run started outside a session.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN", "inherited-token")
    assert settings_module._parse_settings().bot_token == "inherited-token"


def test_hummingbot_settings_read_credentials_from_env(monkeypatch):
    """Env creds must outrank the cached ~/.hummingbot_mcp/server.yml, exactly
    as the CLI args they replaced did — otherwise a host with that file spawns
    sessions pointed at stale credentials."""
    import mcp_servers.hummingbot_api.server as hb_server

    monkeypatch.setattr(
        sys, "argv", ["prog", "--url", "http://10.0.0.5:8000", "--server-name", "prod"]
    )
    monkeypatch.setenv("HUMMINGBOT_API_USERNAME", API_USER)
    monkeypatch.setenv("HUMMINGBOT_API_PASSWORD", API_PASSWORD)
    monkeypatch.setattr(hb_server.settings, "api_username", "stale-from-yml")
    monkeypatch.setattr(hb_server.settings, "api_password", "stale-from-yml")

    hb_server._apply_cli_args()

    assert hb_server.settings.api_username == API_USER
    assert hb_server.settings.api_password == API_PASSWORD
    assert hb_server.settings.api_url == "http://10.0.0.5:8000"
    assert hb_server.settings.server_name == "prod"
