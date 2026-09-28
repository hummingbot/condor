"""``manage_agent_controllers`` — an agent's own controller source (FEAT-126).

The routines pattern applied to a new kind of file. Local actions
(``list``/``read``/``write``/``delete``) run in-process against
:mod:`condor.agent_controllers`, as ``create_routine`` does. Server actions
(``status``/``sync``/``upload_config``/``pull``) cross to the main process over
``call_main_api`` — this subprocess holds no Hummingbot client — with
``server_name=settings.active_server``.

Who may target what follows ``manage_routines``: ``agent=`` is honoured only for
Condor (the chat onboarding a controller into an agent), ``shared=true`` only
for Condor with no target, and an agent treats ``_shared`` controllers as
read-only.
"""

from __future__ import annotations

from urllib.parse import quote

from condor.agent_controllers import (
    CONTROLLER_MD,
    ControllerError,
    agent_controllers,
    check_controller_name,
    check_sample_name,
    delete,
    get_controller,
    owns,
    read_samples,
    write_controller_md,
    write_sample,
    write_source,
)
from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.settings import settings

_NO_SERVER = (
    "No active server is selected for this session, so there is no server to "
    "compare with or push to. Ask the user to select a server (/servers in "
    "Telegram, or the server selector in the dashboard) and try again."
)

LOCAL_ACTIONS = ("list", "read", "write", "delete")
SERVER_ACTIONS = ("status", "sync", "upload_config", "pull")


def _target(agent: str | None, shared: bool | None) -> tuple[str, bool]:
    """``(slug, shared)`` this call acts on, or :class:`ControllerError`.

    A bound specialist acts on itself; naming another agent is refused rather
    than silently ignored, and so is ``shared`` — an agent reads ``_shared`` but
    never writes it. Condor may target any agent, or the shared library.
    """
    from condor.memory.paths import CHAT_SLUG

    own = settings.specialist_slug
    if own:
        if agent and agent != own:
            raise ControllerError(
                "an agent manages only its own controllers — omit `agent`"
            )
        if shared:
            raise ControllerError(
                "_shared controllers are read-only to agents — write your own "
                "under a new name instead"
            )
        return own, False

    if agent:
        from condor.agents.agent import AgentStore

        if AgentStore().get(agent) is None:
            raise ControllerError(f"no agent named '{agent}'")
        return agent, False
    return CHAT_SLUG, bool(shared)


def list_controllers(slug: str) -> dict:
    rows = [src.to_dict() for src in agent_controllers(slug).values()]
    return {
        "agent": slug,
        "controllers": rows,
        "hint": (
            "Read the controller_sources skill before your first sync. Server "
            'state: manage_agent_controllers(action="status").'
        ),
    }


def read_controller(slug: str, name: str, sample: str | None) -> dict:
    src = get_controller(slug, check_controller_name(name))
    if src is None:
        raise ControllerError(f"controller '{name}' not found")
    if sample is not None:
        check_sample_name(sample)
        path = src.samples.get(sample)
        if path is None:
            raise ControllerError(
                f"'{name}' has no sample '{sample}' "
                f"(styles: {', '.join(src.samples) or 'none'})"
            )
        return {
            "name": name,
            "sample": sample,
            "yaml": path.read_text(encoding="utf-8"),
        }

    md_path = src.directory / CONTROLLER_MD
    return {
        **src.to_dict(),
        "code": src.source_path.read_text(encoding="utf-8"),
        "controller_md": (
            md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
        ),
        "samples": read_samples(src),
    }


def _refuse_shared_shadow(slug: str, name: str, shared: bool) -> None:
    """Refuse writing an agent-owned copy under a shared controller's name.

    Two different files under one server name drift against each other forever;
    specialising a shared controller means copying it under a NEW name.
    """
    if shared or owns(slug, name, shared=False):
        return
    existing = get_controller(slug, name)
    if existing is not None and existing.shared:
        raise ControllerError(
            f"'{name}' is a shared controller and cannot be edited here — copy it "
            "under a new name into your own folder instead"
        )


def write_controller(
    slug: str,
    shared: bool,
    name: str,
    code: str | None,
    sample: str | None,
    controller_md: str | None,
) -> dict:
    check_controller_name(name)
    _refuse_shared_shadow(slug, name, shared)
    if sample is not None:
        return write_sample(slug, name, sample, code or "", shared=shared)
    out: dict = {}
    if controller_md is not None:
        out["controller_md"] = write_controller_md(slug, name, controller_md, shared)
    if code:
        out.update(write_source(slug, name, code, shared=shared))
    elif controller_md is None:
        raise ControllerError("code is required (or controller_md, or sample=+code)")
    out.setdefault("written", True)
    out.setdefault("name", name)
    out["next"] = (
        'Check the server with manage_agent_controllers(action="status", '
        f'name="{name}") and sync if it is missing or, after your own edit, drifted.'
    )
    return out


def delete_controller(slug: str, shared: bool, name: str, sample: str | None) -> dict:
    check_controller_name(name)
    if not owns(slug, name, shared):
        existing = get_controller(slug, name)
        if existing is not None and existing.shared:
            raise ControllerError(
                f"'{name}' is a shared controller — agents cannot delete it"
            )
    return delete(slug, name, sample=sample, shared=shared)


def _path(slug: str, *parts: str) -> str:
    return "/agents/" + "/".join(
        quote(p, safe="") for p in (slug, "controllers", *parts)
    )


async def server_action(
    action: str,
    slug: str,
    name: str | None,
    sample: str | None,
    controller_type: str | None,
    configs: list[str] | None,
    config_name: str | None,
    overwrite: bool,
) -> dict:
    server = settings.active_server
    if not server:
        return {"error": _NO_SERVER}

    try:
        if action == "status":
            data = await call_main_api(
                "GET", f"{_path(slug)}?server_name={quote(server, safe='')}"
            )
            rows = data.get("controllers", []) if isinstance(data, dict) else []
            if name:
                rows = [r for r in rows if r.get("name") == name]
                if not rows:
                    return {"error": f"controller '{name}' not found"}
            return {"server_name": server, "controllers": rows}

        if action == "sync":
            check_controller_name(name)
            return await call_main_api(
                "POST",
                _path(slug, name, "sync"),
                {"server_name": server, "overwrite": bool(overwrite)},
            )

        if action == "upload_config":
            check_controller_name(name)
            check_sample_name(sample)
            return await call_main_api(
                "POST",
                _path(slug, name, "configs", sample),
                {
                    "server_name": server,
                    "config_name": config_name,
                    "overwrite": bool(overwrite),
                },
            )

        # pull
        check_controller_name(name)
        if not controller_type:
            return {
                "error": "controller_type is required for pull (directional_trading, "
                "market_making or generic — see manage_controllers list)"
            }
        return await call_main_api(
            "POST",
            _path(slug, "pull"),
            {
                "server_name": server,
                "controller_type": controller_type,
                "controller_name": name,
                "configs": list(configs or []),
                "overwrite": bool(overwrite),
            },
        )
    except APIError as exc:
        return {"error": f"{action} failed: {exc}"}


async def manage_agent_controllers(
    action: str,
    name: str | None = None,
    agent: str | None = None,
    sample: str | None = None,
    code: str | None = None,
    controller_md: str | None = None,
    controller_type: str | None = None,
    configs: list[str] | None = None,
    config_name: str | None = None,
    overwrite: bool = False,
    shared: bool | None = None,
) -> dict:
    if action not in LOCAL_ACTIONS + SERVER_ACTIONS:
        return {"error": f"Unknown action: {action}"}
    try:
        slug, to_shared = _target(agent, shared)
        if action == "list":
            return list_controllers(slug)
        if action in ("read", "write", "delete") and not name:
            return {"error": "name is required"}
        if action == "read":
            return read_controller(slug, name, sample)
        if action == "write":
            return write_controller(slug, to_shared, name, code, sample, controller_md)
        if action == "delete":
            return delete_controller(slug, to_shared, name, sample)
        if action in ("sync", "upload_config", "pull") and not name:
            return {"error": "name is required"}
        if action == "upload_config" and not sample:
            return {"error": "sample is required (the style to upload)"}
        return await server_action(
            action, slug, name, sample, controller_type, configs, config_name, overwrite
        )
    except ControllerError as exc:
        return {"error": str(exc)}
