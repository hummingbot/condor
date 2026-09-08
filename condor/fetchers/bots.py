"""Fetch bot data from Hummingbot API."""

import asyncio
import logging
from typing import Any, NamedTuple, Optional

from condor.fetchers.bot_performance import extract_snapshots as _extract_perf_snapshots

logger = logging.getLogger(__name__)

# Per-call budget for one of the optional enrichment fetches below. The bots
# page renders without them (no age / config / DB perf), so a slow server
# should cost us those columns, never the whole response.
ENRICHMENT_TIMEOUT = 15.0


def extract_bots_list(result: Any) -> list[dict]:
    """Normalize the various API response formats into a list of bot dicts."""
    if result is None:
        logger.warning("Bot status API returned None")
        return []
    if isinstance(result, str):
        logger.warning(
            "Bot status API returned string (possibly HTML error page): %s",
            result[:200],
        )
        return []
    if isinstance(result, dict):
        if result.get("status") == "error":
            logger.warning(
                "Bot status API returned error: %s", result.get("message", result)
            )
            return []
        data = result.get("data", {})
        if isinstance(data, dict):
            return [
                {"bot_name": k, **v} for k, v in data.items() if isinstance(v, dict)
            ]
        elif isinstance(data, list):
            return [b for b in data if isinstance(b, dict)]
        return []
    elif isinstance(result, list):
        return [b for b in result if isinstance(b, dict)]
    logger.warning("Bot status API returned unexpected type: %s", type(result).__name__)
    return []


def _pick_value(live: dict, db: dict, key: str, default: Any = None) -> Any:
    """Merge a field preferring live, by key presence — not truthiness.

    An empty live reading (``0``, ``{}``, ``[]``) is legitimate — a freshly
    redeployed controller has closed nothing and holds no positions — so it must
    win over the DB snapshot instead of falling through to a stale value from a
    previous deploy. Only an absent key falls back to the DB.
    """
    if key in live:
        return live[key]
    return db.get(key, default)


def _pick(live: dict, db: dict, key: str, default: float = 0.0) -> float:
    """Presence-based merge of a numeric field, coerced to float."""
    val = _pick_value(live, db, key, default)
    return float(val if val is not None else default)


def build_bots_page(
    raw_status: Any,
    *,
    ctrl_configs: Optional[dict[str, dict]] = None,
    bot_runs: Optional[dict[str, str]] = None,
    latest_perf: Optional[dict[str, dict]] = None,
) -> dict:
    """Transform raw BOTS_STATUS data into a BotsPageResponse-shaped dict.

    Single source of truth for the {controllers, bots, total_pnl, total_volume}
    transform, shared by the REST route and the WS broadcast path. Both feed it
    the same enrichment, fetched once by :func:`fetch_bots_enrichment` and
    cached per server, so a WS frame carries the same config, deployed_at,
    controller_id and connector/trading_pair as the REST body.

    The kwargs still default to empty maps, but that is degradation, not a
    supported mode: with no configs a controller reports ``config={}``, no
    deploy age, its raw performance key as its id, and a connector/pair guessed
    by splitting that key on underscores.

    Args:
        raw_status: Raw bot status API response (any of the shapes handled by
            ``extract_bots_list``).
        ctrl_configs: Controller configs keyed by config id / controller name.
        bot_runs: Deployed-at timestamps keyed by bot name.
        latest_perf: Latest DB performance snapshots keyed by controller_id.
    """
    ctrl_configs = ctrl_configs or {}
    bot_runs = bot_runs or {}
    latest_perf = latest_perf or {}

    bots_list = extract_bots_list(raw_status)
    controllers: list[dict] = []
    bots: list[dict] = []
    total_pnl = 0.0
    total_volume = 0.0

    for bot_data in bots_list:
        bot_name = bot_data.get("bot_name", "")
        bot_status = bot_data.get("status", "unknown")
        performance = bot_data.get("performance", {})
        error_logs = bot_data.get("error_logs", [])
        general_logs = bot_data.get("general_logs", [])
        if not isinstance(error_logs, list):
            error_logs = []
        if not isinstance(general_logs, list):
            general_logs = []

        num_controllers = 0

        if isinstance(performance, dict):
            for ctrl_name, ctrl_info in performance.items():
                if not isinstance(ctrl_info, dict):
                    continue

                num_controllers += 1
                ctrl_status = ctrl_info.get("status", "running")

                # Get config from pre-fetched configs
                ctrl_config = ctrl_configs.get(ctrl_name, {})
                config_id = ctrl_config.get("id") or ctrl_config.get(
                    "controller_id", ctrl_name
                )

                # Use latest DB performance if available, fallback to live bot status
                db_snap = latest_perf.get(config_id) or latest_perf.get(ctrl_name)
                if db_snap:
                    db_perf = db_snap.get("performance", db_snap)
                    if not isinstance(db_perf, dict):
                        db_perf = {}
                else:
                    db_perf = {}

                # Live performance from bot status (always available)
                live_perf = ctrl_info.get("performance", {})
                if not isinstance(live_perf, dict):
                    live_perf = {}

                # Merge: prefer live data for real-time fields, DB for historical consistency
                realized = _pick(live_perf, db_perf, "realized_pnl_quote")
                unrealized = _pick(live_perf, db_perf, "unrealized_pnl_quote")
                global_pnl = realized + unrealized
                global_pnl_pct = _pick(live_perf, db_perf, "global_pnl_pct")
                volume = _pick(live_perf, db_perf, "volume_traded")
                close_types = _pick_value(live_perf, db_perf, "close_type_counts", {})
                if not isinstance(close_types, dict):
                    close_types = {}
                positions = _pick_value(live_perf, db_perf, "positions_summary", [])
                if not isinstance(positions, list):
                    positions = []

                # Primary: config dict (correct keys)
                connector = ctrl_config.get("connector_name", "")
                trading_pair = ctrl_config.get("trading_pair", "")

                # Fallback: try DB snapshot, then parse from controller name
                if not connector:
                    connector = db_perf.get(
                        "connector", db_perf.get("connector_name", "")
                    )
                if not trading_pair:
                    trading_pair = db_perf.get("trading_pair", "")

                if not connector or not trading_pair:
                    parts = ctrl_name.split("_")
                    for i, part in enumerate(parts):
                        if "-" in part and part[0].isupper():
                            if not trading_pair:
                                trading_pair = part
                            if not connector and i > 0:
                                connector = "_".join(parts[:i])
                            break

                total_pnl += global_pnl
                total_volume += volume

                config_cname = ctrl_config.get("controller_name", "")
                display_name = config_cname or ctrl_name
                display_id = config_id or ctrl_name

                controllers.append(
                    {
                        "controller_name": display_name,
                        "controller_id": display_id,
                        "bot_name": bot_name,
                        "status": ctrl_status,
                        "connector": connector,
                        "trading_pair": trading_pair,
                        "realized_pnl_quote": realized,
                        "unrealized_pnl_quote": unrealized,
                        "global_pnl_quote": global_pnl,
                        "global_pnl_pct": global_pnl_pct,
                        "volume_traded": volume,
                        "close_type_counts": close_types,
                        "positions_summary": positions,
                        "deployed_at": bot_runs.get(bot_name),
                        "config": ctrl_config,
                    }
                )

        bots.append(
            {
                "bot_name": bot_name,
                "status": bot_status,
                "num_controllers": num_controllers,
                "error_count": len(error_logs),
                "deployed_at": bot_runs.get(bot_name),
                "error_logs": error_logs[-100:],
                "general_logs": general_logs[-100:],
            }
        )

    return {
        "controllers": controllers,
        "bots": bots,
        "total_pnl": total_pnl,
        "total_volume": total_volume,
        "server_online": True,
    }


async def fetch_bots_status(client, **_kw):
    """Fetch active bots status."""
    return await client.bot_orchestration.get_active_bots_status()


async def fetch_bot_runs(client, **_kw):
    """Fetch bot run history."""
    return await client.bot_orchestration.get_bot_runs()


# ── Bots-page enrichment ──


class BotsEnrichment(NamedTuple):
    """The three optional inputs :func:`build_bots_page` enriches its output with."""

    #: Controller configs keyed by config id *and* controller name.
    ctrl_configs: dict[str, dict]
    #: Deployed-at timestamps keyed by bot name.
    bot_runs: dict[str, str]
    #: Latest DB performance snapshots keyed by controller id.
    latest_perf: dict[str, dict]

    @classmethod
    def empty(cls) -> "BotsEnrichment":
        """A fresh, empty enrichment — what a caller falls back to on failure."""
        return cls({}, {}, {})


def _collect_bot_runs(result: Any, runs: dict[str, str]) -> None:
    """Merge a bot-runs API response into ``runs`` (bot_name -> deployed_at)."""
    if not isinstance(result, dict):
        return
    runs_data = result.get("data", result)
    if isinstance(runs_data, dict):
        for bot_name, run_info in runs_data.items():
            if isinstance(run_info, dict):
                deployed = run_info.get("deployed_at") or run_info.get("created_at")
                if deployed:
                    runs[bot_name] = str(deployed)
            elif isinstance(run_info, str):
                runs[bot_name] = run_info
    elif isinstance(runs_data, list):
        for run in runs_data:
            if isinstance(run, dict):
                bn = run.get("bot_name", "")
                deployed = run.get("deployed_at") or run.get("created_at")
                if bn and deployed:
                    runs[bn] = str(deployed)


async def _with_enrichment_timeout(coro, label: str, default: Any) -> Any:
    """Cap one enrichment call so a slow server degrades instead of hanging.

    Each fetcher gets its own budget: one slow endpoint must not cost us the
    other two.
    """
    try:
        return await asyncio.wait_for(coro, timeout=ENRICHMENT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(
            "Bots enrichment '%s' timed out after %.0fs", label, ENRICHMENT_TIMEOUT
        )
        return default


async def _fetch_ctrl_configs(client, bot_names: list[str]) -> dict[str, dict]:
    """Controller configs for the given bots, keyed by config id and by name."""
    configs_map: dict[str, dict] = {}
    if not bot_names:
        return configs_map

    async def _get_one(bn: str):
        try:
            configs = await client.controllers.get_bot_controller_configs(bn)
            if isinstance(configs, list):
                for cfg in configs:
                    cid = cfg.get("id") or cfg.get("controller_id", "")
                    if cid:
                        configs_map[cid] = cfg
                    cname = cfg.get("controller_name", "")
                    if cname and cname != cid:
                        configs_map[cname] = cfg
        except Exception:
            pass

    await asyncio.gather(*[_get_one(bn) for bn in bot_names])
    return configs_map


async def _fetch_deployed_runs(client, bot_names: list[str]) -> dict[str, str]:
    """Deployed-at timestamps keyed by bot name, for the Age column.

    Filtered to DEPLOYED on purpose: the unfiltered listing (:func:`fetch_bot_runs`)
    also returns ARCHIVED runs, each carrying a multi-KB ``final_status`` blob.
    On a remote server that is a multi-MB, multi-minute response that stalls the
    whole page (brigado: 4.5 MB / 274s unfiltered vs 121 KB / 5s filtered),
    leaving every bot without an age.
    """
    runs: dict[str, str] = {}
    try:
        _collect_bot_runs(
            await client.bot_orchestration.get_bot_runs(deployment_status="DEPLOYED"),
            runs,
        )
    except Exception:
        logger.debug("Bot runs not available")

    # Any active bot the filtered listing missed gets a targeted lookup
    # (~1 KB each) rather than falling back to the unfiltered listing.
    missing = [bn for bn in bot_names if bn not in runs]
    if missing:

        async def _get_one_run(bn: str):
            try:
                _collect_bot_runs(
                    await client.bot_orchestration.get_bot_runs(bot_name=bn, limit=1),
                    runs,
                )
            except Exception:
                pass

        await asyncio.gather(*[_get_one_run(bn) for bn in missing])
    return runs


async def _fetch_latest_perf(client) -> dict[str, dict]:
    """Latest controller performance snapshots from the DB, keyed by controller id."""
    perf_map: dict[str, dict] = {}
    try:
        perf_result = await client.bot_orchestration.get_latest_controller_performance()
        for snap in _extract_perf_snapshots(perf_result):
            cid = snap.get("controller_id", "")
            if cid:
                perf_map[cid] = snap
    except Exception:
        logger.debug("Latest controller performance not available")
    return perf_map


async def fetch_bots_enrichment(
    client, bots_list: Optional[list[dict]] = None, **_kw
) -> BotsEnrichment:
    """Fetch the three enrichment maps :func:`build_bots_page` takes.

    Fetched as one unit so both delivery paths can share a single cached answer:
    the REST route and every WS ``bots:<server>`` frame read it through
    ServerDataService, instead of the REST route enriching and the WS path
    shipping a stripped page the client has to repair.

    ``bots_list`` names the bots to enrich. It is optional because the
    ServerDataService fetch is handed only a client: with no list, the active
    bots are looked up first. The three calls run concurrently, each under its
    own :data:`ENRICHMENT_TIMEOUT`, and every failure degrades to an empty map.
    """
    if bots_list is None:
        try:
            bots_list = extract_bots_list(await fetch_bots_status(client))
        except Exception as e:
            logger.debug("Bot status not available for enrichment: %s", e)
            bots_list = []

    bot_names = [bn for b in bots_list if (bn := b.get("bot_name", ""))]

    ctrl_configs, bot_runs, latest_perf = await asyncio.gather(
        _with_enrichment_timeout(
            _fetch_ctrl_configs(client, bot_names), "controller configs", {}
        ),
        _with_enrichment_timeout(
            _fetch_deployed_runs(client, bot_names), "bot runs", {}
        ),
        _with_enrichment_timeout(_fetch_latest_perf(client), "latest performance", {}),
    )
    return BotsEnrichment(ctrl_configs, bot_runs, latest_perf)
