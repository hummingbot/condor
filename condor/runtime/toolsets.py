"""MCP toolset builders for agent sessions (ARCH-190).

Builds the per-session MCP server configs (condor + hummingbot subprocesses)
that every surface — Telegram, web dashboard, consult/delegate, the tick
engine — hands to its client. Platform-neutral runtime foundation: this used
to live in ``handlers/agents/_shared.py``, which the runtime could only reach
via lazy imports; ``handlers`` now re-exports from here instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from condor.acp.client import bot_process_marker

log = logging.getLogger(__name__)


def get_project_dir() -> str:
    """Get the condor project root directory (where .mcp.json lives).

    The ACP agent auto-discovers stdio MCP servers from .mcp.json in the cwd,
    so we just need to point it at the project root.
    """
    return str(Path(__file__).parent.parent.parent)


def _bot_token() -> str:
    """This bot's Telegram token, read at call time.

    Goes through ``utils.config`` for its ``load_dotenv()`` side effect, so the
    marker built here is derived from the same token ``main.py`` hands the
    reaper — a mismatch would silently strand orphaned subprocess trees.
    """
    import utils.config  # noqa: F401  (imported for load_dotenv())

    return os.environ.get("TELEGRAM_TOKEN", "")


def _bot_id_args() -> list[str]:
    """``--bot-id <digest>`` for an MCP subprocess, or nothing without a token.

    The digest is what the startup reaper (``reap_stale_acp_trees``) seeds on to
    find subprocess trees orphaned by a previous crash. It replaced the raw
    token, which used to sit here in clear text (SEC-095). Both MCP servers
    parse their argv with ``parse_known_args``, so the flag is inert to them —
    it exists purely to be visible in ``ps``.
    """
    marker = bot_process_marker(_bot_token())
    return ["--bot-id", marker] if marker else []


def _env_entries(**values: Any) -> list[dict[str, str]]:
    """ACP ``env`` entries (``{"name", "value"}``), skipping empty values.

    This is the channel every secret an MCP subprocess needs must travel on: the
    ACP bridge and the pydantic-ai stdio client both overlay these onto the
    child's inherited environment, which — unlike argv — no other local user can
    read out of ``ps``.

    Values are coerced to ``str``: YAML loads an unquoted numeric credential as
    int (e.g. ``password: 123``), and an int in the environment mapping breaks
    subprocess spawning — for pydantic-ai backends (lmstudio:/ollama:/
    openrouter:) it surfaces as a StdioServerParameters validation error.
    """
    entries = (
        (name, str(value)) for name, value in values.items() if value is not None
    )
    return [{"name": name, "value": value} for name, value in entries if value]


def _condor_mcp_args(
    chat_id: int | str,
    user_id: int,
    agent_slug: str | None = None,
    server_name: str | None = None,
    delegate_worker: bool = False,
) -> list[str]:
    """Build CLI args for the condor MCP subprocess.

    ``delegate_worker`` marks a *background Condor worker* — the detached session
    ``delegate`` starts (FEAT-032). The chat and that worker share one agent
    record, so the flag is what tells the subprocess which seat it is sitting in.

    The bot token travels in the server's ``env``, never here: argv is
    world-readable via ``ps`` (SEC-095). What argv carries instead is
    ``--bot-id``, the token's non-secret digest, which is how the startup reaper
    recognizes our own leaked subprocess trees.
    """
    # MCP server expects int chat_id. For web sessions (string keys like "web_42"),
    # use user_id instead — in Telegram DMs, chat_id == user_id anyway.
    effective_chat_id = chat_id if isinstance(chat_id, int) else user_id
    args = [
        "--chat-id",
        str(effective_chat_id),
        "--user-id",
        str(user_id),
    ]
    args.extend(_bot_id_args())
    if agent_slug:
        args.extend(["--agent-slug", str(agent_slug)])
    if server_name:
        args.extend(["--server-name", str(server_name)])
    if delegate_worker:
        args.append("--delegate-worker")
    return args


def _hummingbot_mcp_args(server: dict[str, Any], server_name: str) -> list[str]:
    """Build CLI args for the hummingbot MCP subprocess.

    Only non-secret coordinates travel here — the API username/password go in
    the server's ``env`` instead, since argv is world-readable via ``ps``
    (SEC-095). Every element is a string: YAML loads unquoted numerics as int
    (e.g. ``port: 8000``), and pydantic-ai's StdioServerParameters rejects
    non-string args when starting LM Studio / other local-model sessions.
    """
    api_url = f"http://{server['host']}:{server['port']}"
    return [
        "run",
        "python",
        "-m",
        "mcp_servers.hummingbot_api",
        "--url",
        api_url,
        "--server-name",
        str(server_name),
    ] + _bot_id_args()


def build_mcp_servers_for_session(
    user_id: int,
    chat_id: int | str,
    user_data: dict | None = None,
    server_name: str | None = None,
    agent_slug: str | None = None,
    delegate_worker: bool = False,
) -> list[dict[str, Any]]:
    """Build dynamic MCP server configs for an agent session.

    Returns ACP-format mcpServers that override the static .mcp.json entries by
    name. Always includes the condor MCP server; hummingbot is added when a
    valid server can be resolved for the user.

    ``server_name`` pins the run to that Condor server (an Agent with
    ``server_required``); when omitted, the chat's ambient server is resolved
    from the user's preferences, then from the first accessible server. Each
    candidate must exist *and* be reachable by ``user_id`` — a name this user
    has no grant on resolves to the next candidate, never to its credentials.

    ``agent_slug`` scopes the condor MCP tools' memory/skills to that Agent's
    own stores (``agents/{slug}/``). Without it the tools target the chat
    condor's stores — correct for chat sessions, wrong for an Agent run: a
    serverless consult/tick would silently read and write the CHAT's memory
    and skills instead of the Agent's own (e.g. an agent unable to find its
    inherited ``routine_cookbook`` skill).

    ``delegate_worker`` marks a background Condor delegation (FEAT-032) so the
    subprocess picks up the worker framing and refuses to delegate again.
    """
    from config_manager import get_config_manager, get_effective_server

    cm = get_config_manager()

    # Resolve which hummingbot server to use (explicit override > user
    # preferences). Every candidate is held to existence *and* reach, because
    # the name that comes out of here decides whose API credentials go into the
    # subprocess env below. ``chat_id`` is not a principal — ``chat_defaults``
    # is a global map keyed by chat, so a caller who can name someone else's
    # chat could name their server too (SEC-178, the SEC-164 shape). The subject
    # is always ``user_id``, the authenticated owner of the run; the same
    # predicate guards TickEngine._resolve_server.
    def usable(name: str | None) -> bool:
        return bool(name and cm.get_server(name)) and cm.has_server_access(
            user_id, name
        )

    def candidates():
        # Lazy on purpose: resolving the chat default writes back into
        # ``user_data``, so it must not run when a pin already answered.
        yield server_name
        yield get_effective_server(chat_id, user_data)
        accessible = cm.get_accessible_servers(user_id)
        yield accessible[0] if accessible else None

    server_name = next((name for name in candidates() if usable(name)), None)

    # Condor MCP -- runs as stdio subprocess, tools work locally without TCP bridge
    # Pass resolved server_name so start_agent uses the correct server
    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.condor"]
        + _condor_mcp_args(
            chat_id,
            user_id,
            agent_slug,
            server_name=server_name,
            delegate_worker=delegate_worker,
        ),
        "env": _env_entries(TELEGRAM_BOT_TOKEN=_bot_token()),
    }

    if not server_name:
        log.warning(
            "No accessible server for user %s (chat %s) — "
            "agent will start without mcp-hummingbot",
            user_id,
            chat_id,
        )
        return [condor]

    server = cm.get_server(server_name)
    if not server:
        log.warning(
            "Server '%s' resolved for user %s but not found in servers config — "
            "agent will start without mcp-hummingbot",
            server_name,
            user_id,
        )
        return [condor]

    # Credentials go in env, not argv: the API username/password used to sit on
    # the command line, where any local `ps` recovered them (SEC-095). The
    # non-secret coordinates (url, server name) stay on argv, where they make a
    # running subprocess identifiable.
    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": _hummingbot_mcp_args(server, server_name),
        "env": _env_entries(
            HUMMINGBOT_API_USERNAME=server["username"],
            HUMMINGBOT_API_PASSWORD=server["password"],
        ),
    }

    return [mcp_hummingbot, condor]
