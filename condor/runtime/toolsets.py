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
from collections.abc import Sequence
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


def seat_profile(agent_slug: str | None, tick: bool) -> str:
    """Which tool profile a seat mounts (FEAT-066).

    Tool allowlists are only enforced for pydantic-ai model keys; an ACP bridge
    runs unrestricted, so for those seats the surface a session MOUNTS *is* the
    permission model. One rule, in one place, for both subprocesses — they share
    a profile vocabulary precisely so a seat is described here and nowhere else.

    - ``tick`` — the unattended loop. No Gateway config or container control, no
      repointing the API server, no direct liquidity moves outside an executor,
      and none of the orchestration family that starts and stops the very loop it
      is running inside.
    - ``agent`` — an attended specialist (a consult, a chat bound to an agent, a
      background copy of one). Keeps its domain tools: the LP experts name
      ``manage_amm``, the shared ``recover_orphaned_position`` playbook closes a
      stranded position with ``manage_clmm``, and ``strategy_builder`` — shared,
      so every agent inherits it — has the agent author and launch its own
      strategy. It loses only the operator surface no agent's tool list names.
    - ``full`` — the chat coordinator, where a human confirms every dangerous
      call, and any launch with no seat at all.

    Note the axis is attendance, not identity: a specialist consulted with a user
    watching and the same specialist ticking unattended are different seats, and
    neither ``agent_slug`` nor ``--delegate-worker`` tells them apart, which is
    why ``tick`` is passed in rather than derived.

    Attendance is necessary for ``full`` but not sufficient: what separates it
    from ``agent`` is the admin ring, and that ring is the *server owner's*.
    :func:`build_mcp_servers_for_session` downgrades a ``full`` seat to ``agent``
    once it knows which server the run resolved onto and that the caller does not
    own it (SEC-252) — a question this function cannot answer, since no server is
    resolved yet when it is called.
    """
    from condor.memory.paths import CHAT_SLUG

    if tick:
        return "tick"
    return "agent" if agent_slug and agent_slug != CHAT_SLUG else "full"


def seat_tools(agent_slug: str | None, tick: bool = False) -> list[dict[str, Any]]:
    """Every tool this seat mounts: ``{name, server, description, muted}``.

    The question the brain panel asks, answered where the seat is already
    described: :func:`seat_profile` picks the ring, the two ``profiles.py`` leaf
    modules say which names are in it, and :func:`condor.memory.mutes.load_mutes`
    says which of those the operator has switched off (FEAT-091).

    Names come from ``mcp_servers.*.profiles`` rather than from the servers
    themselves on purpose: importing either ``server.py`` parses argv and builds
    a ``FastMCP`` singleton, neither of which a web request has any business
    doing. ``server.py`` resolves the same names against its own functions at
    import, so a table that drifts fails there, loudly, and not here.

    ``muted`` is rendered, not filtered: the operator has to see the switch that
    is off in order to switch it back on. What the *agent* gets is the rows with
    ``muted`` false — the same shape the skill and routine catalogs already use.
    """
    from condor.memory.mutes import load_mutes
    from mcp_servers.condor import profiles as condor_profiles
    from mcp_servers.hummingbot_api import profiles as hummingbot_profiles

    profile = seat_profile(agent_slug, tick)
    muted = load_mutes(agent_slug)["tools"]
    return [
        {
            "name": name,
            "server": label,
            "description": module.TOOL_DESCRIPTIONS.get(name, ""),
            "muted": name in muted,
        }
        for label, module in (
            ("condor", condor_profiles),
            ("hummingbot", hummingbot_profiles),
        )
        for name in module.PROFILE_TOOLS[profile]
    ]


def _muted_tool_args(muted_tools: Sequence[str]) -> list[str]:
    """``--mute-tools a,b,c`` — or nothing at all when nothing is muted.

    Nothing on the line is the point: an install where no operator has switched
    a tool off spawns byte-identical argv to before FEAT-091 existed, so the flag
    can never be blamed for a session that behaves differently.

    Both servers are handed the *same* list, and each ignores the names it does
    not mount. A mute is one fact about one agent; splitting it per server would
    give the spawner a second place to be wrong about which tool lives where.
    """
    return ["--mute-tools", ",".join(muted_tools)] if muted_tools else []


def _condor_mcp_args(
    chat_id: int | str,
    user_id: int,
    agent_slug: str | None = None,
    server_name: str | None = None,
    delegate_worker: bool = False,
    profile: str = "full",
    session_key: str = "",
    muted_tools: Sequence[str] = (),
) -> list[str]:
    """Build CLI args for the condor MCP subprocess.

    ``delegate_worker`` marks a *background Condor worker* — the detached session
    ``delegate`` starts (FEAT-032). The chat and that worker share one agent
    record, so the flag is what tells the subprocess which seat it is sitting in.

    ``session_key`` is the seat's canonical key, and it rides argv for the same
    reason the ids do (SEC-180): env is not a channel we control end to end. The
    spawner puts it in ``CONDOR_SESSION_KEY`` for the *ACP* subprocess, but the
    MCP server is a grandchild — the bridge spawns it through the MCP SDK, which
    hands a stdio server the ``env`` from its config rather than this process's
    environment. Everything else the subprocess needs to know who it is already
    travels on argv precisely because of that; the key was the one identity value
    that did not, so ``delegate``/``run_code``/``send_notification`` posted an
    empty ``session_key`` and the route had no conversation to resolve — the
    outcome reached the bell and never the chat that asked for it. Not a secret,
    so ``ps`` is not an objection: it is ``web:{user}:{conversation}`` or
    ``tg:{chat}``, both of which argv already carries in pieces.
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
    if session_key:
        args.extend(["--session-key", str(session_key)])
    args.extend(["--profile", profile])
    args.extend(_muted_tool_args(muted_tools))
    return args


def _hummingbot_mcp_args(
    server: dict[str, Any],
    server_name: str,
    profile: str = "full",
    muted_tools: Sequence[str] = (),
) -> list[str]:
    """Build CLI args for the hummingbot MCP subprocess.

    Only non-secret coordinates travel here — the API username/password go in
    the server's ``env`` instead, since argv is world-readable via ``ps``
    (SEC-095). Every element is a string: YAML loads unquoted numerics as int
    (e.g. ``port: 8000``), and pydantic-ai's StdioServerParameters rejects
    non-string args when starting LM Studio / other local-model sessions.
    """
    api_url = f"http://{server['host']}:{server['port']}"
    return (
        [
            "run",
            "python",
            "-m",
            "mcp_servers.hummingbot_api",
            "--url",
            api_url,
            "--server-name",
            str(server_name),
            "--profile",
            profile,
        ]
        + _muted_tool_args(muted_tools)
        + _bot_id_args()
    )


def build_mcp_servers_for_session(
    user_id: int,
    chat_id: int | str,
    user_data: dict | None = None,
    server_name: str | None = None,
    agent_slug: str | None = None,
    delegate_worker: bool = False,
    tick: bool = False,
    session_key: str = "",
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

    ``tick`` marks the unattended loop seat, which mounts the narrowest tool
    profile on both subprocesses (FEAT-066). See :func:`seat_profile`.

    ``session_key`` is the chat seat's canonical key, and only a chat has one:
    a consult, a delegate worker and a tick run for nobody's conversation and
    pass nothing, which is what keeps their provenance honestly empty. See
    :func:`_condor_mcp_args` for why it travels on argv.
    """
    from condor.memory.mutes import load_mutes
    from config_manager import (
        ServerPermission,
        get_config_manager,
        get_effective_server,
    )

    cm = get_config_manager()
    profile = seat_profile(agent_slug, tick)
    # Read once, for both subprocesses: a mute is one fact about one agent, and
    # reading the file twice is two answers to the same question. Sorted so the
    # spawn line is stable between restarts, and empty for every agent nobody has
    # curated — in which case neither builder puts a flag on the line at all.
    muted_tools = sorted(load_mutes(agent_slug)["tools"])

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

    # The admin ring belongs to the server's owner (SEC-252). ``full`` is the
    # only profile that mounts ADMIN_TOOLS on mcp-hummingbot — configure_server,
    # manage_gateway_config, manage_gateway_container — and those act on the
    # resolved server with the OWNER's credentials, injected into the env below,
    # while enforcing no permission of their own: the subprocess has no notion of
    # the calling user, so nothing downstream can. Every other surface already
    # draws that line — ``require_owner`` for the dashboard's gateway lifecycle,
    # ``require_gateway_owner`` for Telegram's token list — so a chat seat must
    # draw it too. It can only be drawn here: ``seat_profile`` knows attendance,
    # and this is the first point that also knows *whose* server the seat landed
    # on. ``agent`` keeps every trading and liquidity tool, so a user shared in as
    # TRADER loses nothing they were ever permitted to do, and
    # ``get_server_permission`` answers OWNER for admins — the same free bypass
    # ``require_owner`` grants them.
    if profile == "full" and server_name:
        if cm.get_server_permission(user_id, server_name) != ServerPermission.OWNER:
            profile = "agent"

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
            profile=profile,
            session_key=session_key,
            muted_tools=muted_tools,
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
        "args": _hummingbot_mcp_args(server, server_name, profile, muted_tools),
        "env": _env_entries(
            HUMMINGBOT_API_USERNAME=server["username"],
            HUMMINGBOT_API_PASSWORD=server["password"],
        ),
    }

    return [mcp_hummingbot, condor]
