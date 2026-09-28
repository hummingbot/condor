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

import asyncio
import difflib
import logging
import time
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

#: A running bot keeps the class it imported; a push never reaches it.
BOTS_BEHIND_NOTE = (
    "they keep the OLD class until stopped, archived and redeployed (that is "
    "the user's call). Do not restart them. Bots deployed from now on run the "
    "new code."
)

#: The API process keeps the class its backtests imported first.
BACKTESTS_NOTE = (
    "Backtests keep the OLD class until the API restarts: do not trust a "
    "backtest of this controller until then. Restarting is the user's call, "
    "never yours (a restart reaps running executors)."
)

#: A saved config is copied into a bot at deploy time, so replacing it moves no bot.
CONFIG_OVERWRITE_NOTE = (
    "Running bots keep the config they were deployed with; this changes the "
    "saved config only."
)

#: How long a drift preview authorizes an overwrite of the same server copy.
PREVIEW_TTL = 15 * 60


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


# ── Impact of a push (FEAT-129) ──
#
# A controller has four copies: the agent's FOLDER, the SERVER's, the class
# each RUNNING BOT imported, and the class the API's BACKTESTS imported. A sync
# changes exactly one of them — the server copy — and every impact message says
# which of the other three stay behind, in the same words everywhere.


@dataclass(frozen=True)
class SharedOwner:
    """Another agent (or the ``_shared`` library) with a controller of this name."""

    agent: str
    #: Its folder copy is byte-for-byte what is being pushed.
    same_code: bool


@dataclass(frozen=True)
class LiveBot:
    """A running bot whose deployed configs name this controller."""

    bot_name: str
    config_ids: tuple[str, ...]


@dataclass(frozen=True)
class ControllerImpact:
    """Who a push of one controller to one server affects."""

    controller: str
    server_name: str
    shared_owners: tuple[SharedOwner, ...]
    #: ``None`` = could not check — never read as "none".
    live_bots: tuple[LiveBot, ...] | None
    live_bots_error: str = ""
    #: False when the server lacked the controller: a create replaces nothing.
    server_had_copy: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "controller": self.controller,
            "server_name": self.server_name,
            "shared_owners": [asdict(o) for o in self.shared_owners],
            "live_bots": (
                None
                if self.live_bots is None
                else [
                    {"bot_name": b.bot_name, "config_ids": list(b.config_ids)}
                    for b in self.live_bots
                ]
            ),
            "live_bots_error": self.live_bots_error,
            "server_had_copy": self.server_had_copy,
        }

    def render(self, *, overwriting: bool) -> str:
        """The four copies, always in the same order.

        ``overwriting=True`` describes what an overwrite *would* do (a preview,
        the human's prompt); ``False`` what a push *did*.
        """
        name, server = self.controller, self.server_name or "the server"
        if not self.server_had_copy:
            head = (
                f"Created the SERVER copy of '{name}' on {server} from your "
                "FOLDER copy."
            )
        elif overwriting:
            head = (
                f"This replaces the SERVER copy of '{name}' on {server} with "
                "your FOLDER copy (the server copy is backed up first)."
            )
        else:
            head = (
                f"Replaced the SERVER copy of '{name}' on {server} with your "
                "FOLDER copy (the old one is backed up)."
            )

        if self.shared_owners:
            owners = ", ".join(
                f"{o.agent} ({'same code' if o.same_code else 'different code'})"
                for o in self.shared_owners
            )
            owners_line = (
                f"• Other agents with a '{name}': {owners} — the server holds one "
                "copy per name, shared by all of them; those with different code "
                "will see drift on their next status."
            )
        else:
            owners_line = f"• Other agents with a '{name}': none on this Condor."

        if not self.server_had_copy:
            bots_line = (
                "• Running bots using it: none — no bot can run a controller the "
                "server did not have."
            )
            backtests_line = "• Backtests: they import it fresh; nothing is stale."
        else:
            if self.live_bots is None:
                why = f" ({self.live_bots_error})" if self.live_bots_error else ""
                bots_line = (
                    f"• Running bots using it: could not check running bots{why} "
                    "— treat as unknown, not as none. Any that use it "
                    f"{BOTS_BEHIND_NOTE}"
                )
            elif self.live_bots:
                bots = ", ".join(
                    f"{b.bot_name} ({len(b.config_ids)} config"
                    f"{'' if len(b.config_ids) == 1 else 's'})"
                    for b in self.live_bots
                )
                bots_line = f"• Running bots using it: {bots} — {BOTS_BEHIND_NOTE}"
            else:
                bots_line = (
                    "• Running bots using it: none. Bots deployed from now on run "
                    "the new code."
                )
            backtests_line = f"• {BACKTESTS_NOTE}"
        return "\n".join((head, owners_line, bots_line, backtests_line))


def _shared_owners(
    agent_slug: str | None, src: ControllerSource
) -> tuple[SharedOwner, ...]:
    """Every other local, stock or ``_shared`` owner of ``src.name`` (disk only)."""
    from condor.agent_controllers import agent_controllers, shared_controllers
    from condor.memory.paths import CHAT_SLUG, iter_agent_slugs

    own = agent_slug or CHAT_SLUG
    owners: list[SharedOwner] = []
    if not src.shared:
        # Reported once, not once per agent that inherits it.
        library = shared_controllers().get(src.name)
        if library is not None:
            owners.append(SharedOwner("_shared", library.digest == src.digest))
    for slug in iter_agent_slugs():
        if slug == own:
            continue
        try:
            other = agent_controllers(slug).get(src.name)
        except Exception:  # noqa: BLE001 - one unreadable folder is not fatal
            log.debug("impact: could not read %s's controllers", slug, exc_info=True)
            continue
        if other is None or other.shared:
            continue
        owners.append(SharedOwner(slug, other.digest == src.digest))
    return tuple(owners)


def _bot_name(bot: dict) -> str:
    return str(bot.get("bot_name") or bot.get("name") or "")


def _uses(config: dict, src: ControllerSource) -> bool:
    if config.get("controller_name") != src.name:
        return False
    ctype = config.get("controller_type")
    return not ctype or not src.controller_type or ctype == src.controller_type


async def _live_bots(
    client: Any, src: ControllerSource
) -> tuple[tuple[LiveBot, ...] | None, str]:
    """The running bots whose deployed configs use ``src``, or ``(None, why)``.

    Uncached on purpose: a preview is rare, and missing a bot deployed a minute
    ago is the wrong direction. A timeout is a failure, never an empty answer.
    """
    from condor.fetchers.bots import (
        ENRICHMENT_TIMEOUT,
        _fetch_one_bot_configs,
        extract_bots_list,
    )

    orchestration = getattr(client, "bot_orchestration", None)
    if orchestration is None:
        return None, "this server client cannot list bots"
    try:
        raw = await asyncio.wait_for(
            orchestration.get_active_bots_status(), timeout=ENRICHMENT_TIMEOUT
        )
    except asyncio.TimeoutError:
        return None, f"listing bots timed out after {ENRICHMENT_TIMEOUT:.0f}s"
    except Exception as exc:  # noqa: BLE001
        return None, f"listing bots failed: {exc}"
    # extract_bots_list reads an error payload as [] — here that would be a lie.
    if (
        raw is None
        or isinstance(raw, str)
        or (isinstance(raw, dict) and raw.get("status") == "error")
        or not isinstance(raw, (dict, list))
    ):
        return None, "listing bots returned an error"

    names = [n for n in (_bot_name(b) for b in extract_bots_list(raw)) if n]
    results = await asyncio.gather(
        *(
            asyncio.wait_for(
                _fetch_one_bot_configs(client, n), timeout=ENRICHMENT_TIMEOUT
            )
            for n in names
        ),
        return_exceptions=True,
    )
    bots: list[LiveBot] = []
    for name, configs in zip(names, results):
        if isinstance(configs, BaseException):
            what = (
                "timed out"
                if isinstance(configs, asyncio.TimeoutError)
                else f"failed: {configs}"
            )
            return None, f"reading {name}'s controller configs {what}"
        ids = tuple(
            str(c.get("id") or c.get("controller_id") or "?")
            for c in configs
            if _uses(c, src)
        )
        if ids:
            bots.append(LiveBot(name, ids))
    return tuple(bots), ""


async def controller_impact(
    client: Any,
    agent_slug: str | None,
    src: ControllerSource,
    server_name: str,
    *,
    server_had_copy: bool = True,
) -> ControllerImpact:
    """Who pushing ``src`` to ``server_name`` affects. Never raises."""
    try:
        owners = _shared_owners(agent_slug, src)
    except Exception:  # noqa: BLE001 - disk only; an impact must still render
        log.warning("impact: shared-owner scan failed", exc_info=True)
        owners = ()
    if not server_had_copy:
        live, error = (), ""
    else:
        live, error = await _live_bots(client, src)
    return ControllerImpact(
        controller=src.name,
        server_name=server_name,
        shared_owners=owners,
        live_bots=live,
        live_bots_error=error,
        server_had_copy=server_had_copy,
    )


# ── Previews: look before you overwrite (FEAT-129) ──
#
# A drift refusal records what it showed; an overwrite is accepted only while a
# preview of the same server copy (and the same folder copy) exists, and the
# human's confirmation prompt reads it back. In memory, like the confirmations
# themselves: after a restart it only means "run sync once more".


@dataclass(frozen=True)
class ImpactPreview:
    impact: ControllerImpact
    #: The server copy the preview was taken against.
    server_digest: str
    #: The folder copy it would push.
    folder_digest: str
    created_at: float


_previews: dict[tuple[str, str, str], ImpactPreview] = {}


def _preview_key(agent: str | None, name: str, server: str) -> tuple[str, str, str]:
    from condor.memory.paths import CHAT_SLUG

    return (agent or CHAT_SLUG, name, server)


def record_preview(
    agent: str | None, name: str, server: str, preview: ImpactPreview
) -> None:
    _previews[_preview_key(agent, name, server)] = preview


def preview_for(agent: str | None, name: str, server: str) -> ImpactPreview | None:
    """The live preview for this push, or ``None`` (absent or older than the TTL)."""
    key = _preview_key(agent, name, server)
    preview = _previews.get(key)
    if preview is None:
        return None
    if time.time() - preview.created_at > PREVIEW_TTL:
        _previews.pop(key, None)
        return None
    return preview


def clear_previews() -> None:
    """Forget every preview (tests)."""
    _previews.clear()


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
    - ``drift`` → refused with a diff and the impact (recorded as a preview), or
      with ``overwrite`` — only while a preview of this same server and folder
      copy is live, else ``preview_required`` — the server copy is backed up
      under the agent's ``.server_backups/``, replaced, and the result names
      the running bots and backtests left on the old class;
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
        preview = preview_for(agent_slug, src.name, server_name)
        previewed = (
            preview is not None
            and preview.server_digest == status.server_digest
            and preview.folder_digest == src.digest
        )
        if not overwrite or not previewed:
            impact = await controller_impact(client, agent_slug, src, server_name)
            record_preview(
                agent_slug,
                src.name,
                server_name,
                ImpactPreview(
                    impact=impact,
                    server_digest=status.server_digest,
                    folder_digest=src.digest,
                    created_at=time.time(),
                ),
            )
            extra = {
                "diff": diff,
                "impact": impact.to_dict(),
                "impact_text": impact.render(overwriting=True),
            }
            if overwrite:
                return _refused(
                    src.name,
                    status,
                    "preview_required: run sync without overwrite first, and show "
                    "the user the diff and the impact"
                    + (
                        " — the server or folder copy changed since the last " "preview"
                        if preview is not None
                        else ""
                    )
                    + ". This refusal is that preview (diff and impact_text "
                    "below); retry overwrite=true only after the user has read "
                    "them and said yes.",
                    preview_required=True,
                    **extra,
                )
            return _refused(
                src.name,
                status,
                f"the server's '{src.name}' differs from the folder. Show the user "
                "the diff and the impact (impact_text, verbatim) and ask; pass "
                "overwrite=true only with their go-ahead (the server copy is "
                "backed up first), or pull the server's version if it is the "
                "right one.",
                **extra,
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
        # One preview authorizes one overwrite.
        _previews.pop(_preview_key(agent_slug, src.name, server_name), None)
        impact_text = preview.impact.render(overwriting=False)
        return {
            "name": src.name,
            "verdict": "drift",
            "changed": True,
            "overwritten": True,
            "backup": str(backup),
            "backtest_cache_stale": True,
            "impact": preview.impact.to_dict(),
            "impact_text": impact_text,
            "message": impact_text,
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
    impact = await controller_impact(
        client, agent_slug, src, server_name, server_had_copy=False
    )
    return {
        "name": src.name,
        "verdict": "missing",
        "changed": True,
        "created": True,
        "impact": impact.to_dict(),
        "impact_text": impact.render(overwriting=False),
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
            + (f" {CONFIG_OVERWRITE_NOTE}" if exists else "")
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
