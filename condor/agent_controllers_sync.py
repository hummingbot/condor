"""An agent's controllers against a Hummingbot API server (FEAT-126).

The network half of :mod:`condor.agent_controllers`. Every function here takes
an API ``client`` and runs in the **main process** — the Condor MCP subprocess
holds no client and reaches these through the ``/agents/{slug}/controllers``
routes. The dashboard (FEAT-127) calls the same routes.

The stance is :mod:`condor.venue_drift`'s: a server that did not answer is
``unreachable`` and is **never** read as agreement. And the folder is the
source of truth: a server copy that differs is ``drift``, which is refused
until the caller says ``overwrite`` — and even then the server's copy is kept
first under ``.server_backups/``.

Outcomes are returned, not raised: a refusal carries ``refused: true``, a
``reason`` and (for drift) a ``diff``, so an agent can read it and ask. Only
a caller error (unknown controller, bad name) raises
:class:`~condor.agent_controllers.ControllerError`.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal

from condor.agent_controllers import (
    ControllerError,
    ControllerSource,
    adopt,
    backup_server_copy,
    check_config_name,
    check_controller_name,
    default_config_name,
    load_sample,
    normalise_source,
    read_source,
    source_digest,
)

log = logging.getLogger(__name__)

Verdict = Literal["missing", "in_sync", "drift", "unreachable"]

#: A drift diff longer than this is cut: enough to judge the change, not a dump.
MAX_DIFF_LINES = 200

BACKTEST_STALE_NOTE = (
    "The API keeps the old class for backtests until it restarts: do not trust "
    "a backtest of this controller until someone restarts the API (a restart "
    "reaps running executors, so that is the user's call, never yours). Bots "
    "deployed from now on start a fresh container and do run the new code."
)


@dataclass(frozen=True)
class ServerStatus:
    """Where one controller stands on one server."""

    verdict: Verdict
    #: :func:`source_digest` of the server's copy, when it was read.
    server_digest: str = ""
    #: The type folder the server holds it under, when present.
    server_type: str = ""
    #: Why it is ``unreachable``, or a note on a ``drift`` (a type mismatch).
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


def unified_diff(
    server_text: str, folder_text: str, name: str, server_name: str
) -> str:
    """Server copy → folder copy, capped at :data:`MAX_DIFF_LINES` lines."""
    lines = list(
        difflib.unified_diff(
            normalise_source(server_text).splitlines(keepends=True),
            normalise_source(folder_text).splitlines(keepends=True),
            fromfile=f"{server_name or 'server'}:{name}",
            tofile=f"folder:{name}",
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        extra = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES] + [f"... ({extra} more diff lines)\n"]
    return "".join(lines)


def _content(payload: Any) -> str | None:
    """The source text out of ``get_controller``'s ``{name, type, content}``."""
    if isinstance(payload, dict):
        content = payload.get("content")
        return content if isinstance(content, str) else None
    return payload if isinstance(payload, str) else None


async def _listing(client: Any) -> tuple[dict[str, list[str]] | None, str]:
    try:
        listing = await client.controllers.list_controllers()
    except Exception as exc:  # noqa: BLE001 - any failure is "did not answer"
        return None, f"list_controllers failed: {exc}"
    if not isinstance(listing, dict):
        return None, f"list_controllers returned {type(listing).__name__}"
    return listing, ""


async def _server_copy(
    client: Any, src: ControllerSource, listing: dict[str, list[str]]
) -> tuple[ServerStatus, str | None]:
    """The status of ``src`` against an already-fetched listing, plus the server text."""
    present = [
        ctype
        for ctype, names in listing.items()
        if isinstance(names, (list, tuple, set)) and src.name in names
    ]
    if not present:
        return ServerStatus("missing"), None

    server_type = src.controller_type if src.controller_type in present else present[0]
    try:
        payload = await client.controllers.get_controller(server_type, src.name)
    except Exception as exc:  # noqa: BLE001
        return (
            ServerStatus("unreachable", server_type=server_type, detail=str(exc)),
            None,
        )
    text = _content(payload)
    if text is None:
        return (
            ServerStatus(
                "unreachable",
                server_type=server_type,
                detail="get_controller returned no source content",
            ),
            None,
        )

    digest = source_digest(text)
    if src.controller_type and server_type != src.controller_type:
        return (
            ServerStatus(
                "drift",
                server_digest=digest,
                server_type=server_type,
                detail=(
                    f"the server holds '{src.name}' as {server_type}, the folder "
                    f"says {src.controller_type}"
                ),
            ),
            text,
        )
    verdict: Verdict = "in_sync" if digest == src.digest else "drift"
    return ServerStatus(verdict, server_digest=digest, server_type=server_type), text


async def controller_statuses(
    client: Any, sources: Iterable[ControllerSource]
) -> dict[str, ServerStatus]:
    """One ``list_controllers`` call, then one ``get_controller`` per present one."""
    sources = list(sources)
    listing, error = await _listing(client)
    if listing is None:
        return {src.name: ServerStatus("unreachable", detail=error) for src in sources}
    out: dict[str, ServerStatus] = {}
    for src in sources:
        status, _ = await _server_copy(client, src, listing)
        out[src.name] = status
    return out


async def controller_status(
    client: Any, src: ControllerSource
) -> tuple[ServerStatus, str | None]:
    """:func:`controller_statuses` for one controller, keeping the server text."""
    listing, error = await _listing(client)
    if listing is None:
        return ServerStatus("unreachable", detail=error), None
    return await _server_copy(client, src, listing)


def _refused(name: str, status: ServerStatus, reason: str, **extra: Any) -> dict:
    return {
        "name": name,
        "verdict": status.verdict,
        "changed": False,
        "refused": True,
        "reason": reason,
        **extra,
    }


async def sync_controller(
    client: Any,
    agent_slug: str | None,
    src: ControllerSource,
    server_name: str,
    overwrite: bool = False,
) -> dict:
    """Upload ``src`` where the server lacks it; refuse drift unless ``overwrite``.

    - ``missing`` → created;
    - ``in_sync`` → nothing sent;
    - ``drift`` → refused with a diff, or with ``overwrite`` the server copy is
      backed up under the agent's ``.server_backups/``, replaced, and the result
      says backtests are stale;
    - ``unreachable`` → refused: not knowing is not agreeing.
    """
    from mcp_servers.hummingbot_api.tools.controllers import modify_controllers

    if not src.controller_type:
        raise ControllerError(src.type_error)

    status, server_text = await controller_status(client, src)
    if status.verdict == "unreachable":
        return _refused(
            src.name,
            status,
            f"the server did not answer ({status.detail}) — that is not 'in sync'; "
            "retry later",
        )
    if status.verdict == "in_sync":
        return {
            "name": src.name,
            "verdict": "in_sync",
            "changed": False,
            "message": f"'{src.name}' on {server_name} already matches the folder.",
        }

    folder_text = read_source(src)
    if status.verdict == "drift":
        diff = unified_diff(server_text or "", folder_text, src.name, server_name)
        if status.server_type and status.server_type != src.controller_type:
            return _refused(
                src.name,
                status,
                f"{status.detail} — sync never moves a controller between type "
                "folders; resolve it on the server or fix the folder's type",
                diff=diff,
            )
        if not overwrite:
            return _refused(
                src.name,
                status,
                f"the server's '{src.name}' differs from the folder. Read the diff "
                "and ask the user; pass overwrite=true only with their go-ahead "
                "(the server copy is backed up first), or pull the server's "
                "version if it is the right one.",
                diff=diff,
            )

        backup = backup_server_copy(
            agent_slug, src.name, server_name, server_text or ""
        )
        result = await modify_controllers(
            client,
            action="upsert",
            target="controller",
            controller_type=src.controller_type,
            controller_name=src.name,
            controller_code=folder_text,
            confirm_override=True,
        )
        log.info(
            "agent controllers: %s overwrote '%s' on %s (backup %s)",
            agent_slug,
            src.name,
            server_name,
            backup,
        )
        return {
            "name": src.name,
            "verdict": "drift",
            "changed": True,
            "overwritten": True,
            "backup": str(backup),
            "backtest_cache_stale": True,
            "message": f"Replaced '{src.name}' on {server_name}. {BACKTEST_STALE_NOTE}",
            "result": result.get("result"),
        }

    # missing: a brand-new class has never been imported, so nothing is stale.
    result = await modify_controllers(
        client,
        action="upsert",
        target="controller",
        controller_type=src.controller_type,
        controller_name=src.name,
        controller_code=folder_text,
        confirm_override=False,
    )
    if result.get("exists") and "current_code" in result:
        # Someone created it between the listing and the POST. Nothing was sent.
        return _refused(
            src.name,
            status,
            f"'{src.name}' appeared on {server_name} while syncing — run status again",
        )
    return {
        "name": src.name,
        "verdict": "missing",
        "changed": True,
        "created": True,
        "message": f"Created '{src.name}' ({src.controller_type}) on {server_name}.",
        "result": result.get("result"),
    }


def _comparable(config: dict) -> dict:
    """A config as it is compared: cleaned like a save, without its ``id``."""
    from condor.controller_configs import clean_config_for_save

    return {k: v for k, v in clean_config_for_save(config).items() if k != "id"}


def _yaml(data: dict) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=True, allow_unicode=True)


async def upload_sample_config(
    client: Any,
    src: ControllerSource,
    sample: str,
    server_name: str,
    config_name: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Save a style as a config on the server, under ``{controller}__{sample}``.

    Idempotent (an equal config is not re-sent), refuses to replace a differing
    one without ``overwrite``, refuses when the controller itself is missing
    (sync first), and hands the API's validation errors back verbatim.
    """
    from mcp_servers.hummingbot_api.tools.controllers import modify_controllers

    data = load_sample(src, sample)
    config_name = check_config_name(
        config_name or default_config_name(src.name, sample)
    )
    desired = _comparable(data)

    status, _ = await controller_status(client, src)
    if status.verdict == "unreachable":
        return _refused(
            src.name,
            status,
            f"the server did not answer ({status.detail}) — retry later",
            config_name=config_name,
        )
    if status.verdict == "missing":
        return _refused(
            src.name,
            status,
            f"'{src.name}' is not on {server_name} yet — run sync first; a config "
            "for an absent controller cannot be validated",
            config_name=config_name,
        )

    try:
        existing_ids = {
            c.get("id") for c in await client.controllers.list_controller_configs()
        }
    except Exception as exc:  # noqa: BLE001
        return _refused(
            src.name,
            ServerStatus("unreachable", detail=str(exc)),
            f"could not list the server's configs ({exc}) — retry later",
            config_name=config_name,
        )

    exists = config_name in existing_ids
    if exists:
        try:
            current = await client.controllers.get_controller_config(config_name)
        except Exception as exc:  # noqa: BLE001
            return _refused(
                src.name,
                ServerStatus("unreachable", detail=str(exc)),
                f"could not read config '{config_name}' ({exc}) — retry later",
                config_name=config_name,
            )
        current_clean = _comparable(current if isinstance(current, dict) else {})
        if current_clean == desired:
            return {
                "name": src.name,
                "verdict": status.verdict,
                "config_name": config_name,
                "changed": False,
                "message": f"Config '{config_name}' on {server_name} already matches '{sample}'.",
            }
        if not overwrite:
            diff = "".join(
                difflib.unified_diff(
                    _yaml(current_clean).splitlines(keepends=True),
                    _yaml(desired).splitlines(keepends=True),
                    fromfile=f"{server_name}:{config_name}",
                    tofile=f"sample:{sample}",
                )
            )
            return _refused(
                src.name,
                status,
                f"config '{config_name}' exists on {server_name} and differs — pass "
                "overwrite=true to replace it, or upload under another config_name",
                config_name=config_name,
                diff=diff,
            )

    payload = dict(desired)
    payload["id"] = config_name
    try:
        result = await modify_controllers(
            client,
            action="upsert",
            target="config",
            config_name=config_name,
            config_data=payload,
            confirm_override=True,
        )
    except Exception as exc:  # noqa: BLE001 - validation errors, verbatim
        return _refused(
            src.name,
            status,
            f"the server rejected the config: {exc}",
            config_name=config_name,
            error=str(exc),
        )

    out = {
        "name": src.name,
        "verdict": status.verdict,
        "config_name": config_name,
        "changed": True,
        "replaced": exists,
        "message": (
            f"{'Replaced' if exists else 'Created'} config '{config_name}' on "
            f"{server_name} from style '{sample}'."
        ),
        "result": result.get("result"),
    }
    if status.verdict == "drift":
        out["warning"] = (
            f"'{src.name}' on {server_name} differs from the folder, so the "
            "config was validated against the server's version."
        )
    return out


async def pull_controller(
    client: Any,
    agent_slug: str | None,
    controller_type: str,
    name: str,
    configs: Iterable[str] = (),
    overwrite: bool = False,
) -> dict:
    """Adopt a server's controller (and named configs) into the agent's local folder."""
    check_controller_name(name)
    try:
        payload = await client.controllers.get_controller(controller_type, name)
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "changed": False,
            "refused": True,
            "reason": f"could not fetch '{name}' ({controller_type}) from the server: {exc}",
        }
    code = _content(payload)
    if code is None:
        return {
            "name": name,
            "changed": False,
            "refused": True,
            "reason": "the server returned no source content",
        }

    fetched: dict[str, dict] = {}
    missing: dict[str, str] = {}
    for config_id in configs:
        try:
            check_config_name(config_id)
            data = await client.controllers.get_controller_config(config_id)
        except Exception as exc:  # noqa: BLE001
            missing[config_id] = str(exc)
            continue
        if isinstance(data, dict):
            fetched[config_id] = data

    try:
        result = adopt(agent_slug, name, controller_type, code, fetched, overwrite)
    except ControllerError as exc:
        return {"name": name, "changed": False, "refused": True, "reason": str(exc)}
    result["changed"] = True
    if missing:
        result["configs_not_fetched"] = missing
    return result
