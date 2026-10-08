"""Report every hackathon agent's PnL and volume to the race API, once a minute.

The race API names its agents by **strategy slug**. Each one is resolved to the
Condor strategies with that slug. Bot PnL and volume are the portfolio page's
Bots tile: every running controller on the live bot status, realized plus
unrealized, and the volume it reports, attributed by the same namespace, deed
and extra-bot bases the bots page uses. Standalone executors its sessions
created (``controller_id = "{agent}.{strategy}_{N}"``) are added from
``fetch_agent_performance_batch``. A non-USD executor quote is restated in USD
there; a live controller is already the quote the portfolio page sums.

Run it from the repo root, where ``config.yml`` and ``.env`` live::

    uv run python scripts/hackathon_monitor.py --dry-run --once   # look first
    uv run python scripts/hackathon_monitor.py --start-race       # at the gun
    uv run python scripts/hackathon_monitor.py                    # after a restart

``--start-race`` records what every agent had already made and traded, and that
baseline is subtracted from every later post, so the race counts from its start
and not from each strategy's first test run. It is kept in the state file and
survives a restart; without one the lifetime figures are posted as they are.

When a Condor strategy's slug differs from the race id, name it in an agent map:
a YAML file of race agent id → Condor run keys (``agent.strategy``). An id in
the map is measured by those keys only; any other id is matched by slug. A bot
deployed outside that run's namespace, with no deed, is named under ``bots``
and added to the same total. The file is re-read every minute, so an edit
takes effect without a restart::

    churn: [stable_churn_operator.stable_churn_supervisor]
    adaptive-orca-lp: [alice.orca_lp, bob.orca_lp]
    delta-raptor:
      runs: [delta_raptor.rlusd_xrp_maker]
      bots: [delta_raptor_usd_desk_sit]

Environment (``.env`` is read):

- ``HACKATHON_API_URL``    base URL of the race site, e.g. ``https://example.org``
- ``HACKATHON_API_TOKEN``  bearer token
- ``HACKATHON_SLUG``       defaults to ``agent-builders-cup-1``
- ``HACKATHON_SERVERS``    comma-separated Condor server names; defaults to
  every server in ``config.yml``
- ``HACKATHON_AGENT_MAP``  path of the agent map; none by default. Start from
  ``scripts/hackathon_agents.example.yml``, which lists the field

An agent's figure is the sum over all of those servers. A server that stops
answering keeps contributing the last figure it gave (remembered in the state
file, so a restart does not lose it); one that has never answered counts as
nothing and is named in the log every minute.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Run as a file (``python scripts/hackathon_monitor.py``) the repo root is not
# on the path, and everything below imports from it.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("hackathon_monitor")

DEFAULT_SLUG = "agent-builders-cup-1"
DEFAULT_INTERVAL = 60.0
DEFAULT_STATE = ".condor/hackathon/{slug}.json"
HTTP_TIMEOUT = 20.0
# One server's whole fetch. A dead host must cost the minute a bounded wait, not
# the other servers' figures.
SERVER_TIMEOUT = 45.0


@dataclass(frozen=True)
class Totals:
    """One agent's cumulative figures, in quote."""

    pnl: float = 0.0
    volume: float = 0.0

    def minus(self, other: "Totals") -> "Totals":
        return Totals(self.pnl - other.pnl, self.volume - other.volume)

    def plus(self, other: "Totals") -> "Totals":
        return Totals(self.pnl + other.pnl, self.volume + other.volume)


# ── The race API ──


def race_url(base_url: str, slug: str) -> str:
    return f"{base_url.rstrip('/')}/api/hackathons/{slug}/race-data"


def parse_agent_ids(payload: Any) -> list[str]:
    """The agent ids out of the GET response, in race order.

    The site answers ``{"data": {"agents": [{"agent_id": ...}, ...]}}``; any
    other shape is an error rather than an empty field.
    """
    try:
        return [agent["agent_id"] for agent in payload["data"]["agents"]]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Unexpected race-data response: {json.dumps(payload)[:300]}"
        ) from exc


def parse_post_result(payload: Any) -> tuple[int, list[str]]:
    """``(accepted, unknown ids)`` out of the POST response."""
    try:
        data = payload["data"]
        return data["accepted"], data["unknown"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Unexpected race-data POST response: {json.dumps(payload)[:300]}"
        ) from exc


def build_payload(
    agent_ids: Iterable[str], totals: dict[str, Totals], now: float
) -> dict[str, Any]:
    """The POST body. An agent with no figure this minute is left out."""
    stamp = datetime.fromtimestamp(now, tz=timezone.utc)
    return {
        "timestamp": stamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "agents": [
            {
                "agent_id": agent_id,
                "pnl_quote": round(totals[agent_id].pnl, 6),
                "volume_quote": round(totals[agent_id].volume, 6),
            }
            for agent_id in agent_ids
            if agent_id in totals
        ],
    }


async def fetch_agent_ids(session: Any, url: str) -> list[str]:
    async with session.get(url) as response:
        response.raise_for_status()
        return parse_agent_ids(await response.json())


async def post_race_data(
    session: Any, url: str, payload: dict[str, Any]
) -> tuple[int, list[str]]:
    async with session.post(url, json=payload) as response:
        if response.status >= 400:
            body = (await response.text())[:300]
            raise RuntimeError(f"POST {url} answered {response.status}: {body}")
        return parse_post_result(await response.json())


# ── Who is who ──


@dataclass(frozen=True)
class AgentMap:
    """Race agent id → the run keys to measure, and bot bases to add.

    ``runs`` is the bots-page join (namespace, declared names, deeds). ``bots``
    names bases that join matches nothing for: deployed beside the run, outside
    its namespace, and never written into a deed.
    """

    runs: dict[str, list[str]]
    bots: dict[str, list[str]]


def _string_list(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise RuntimeError(f"{label} must be a string or a list of strings")


def load_agent_map(path: Path) -> AgentMap:
    """Race agent id → Condor run keys and extra bot bases, from the YAML file.

    A string or a list is run keys, as before. A mapping may set ``runs`` and
    ``bots``; anything else is rejected so a typo does not silently drop a key.
    """
    import yaml

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path}: expected a mapping of agent id to run keys")
    runs: dict[str, list[str]] = {}
    bots: dict[str, list[str]] = {}
    for agent_id, spec in raw.items():
        aid = str(agent_id)
        if isinstance(spec, (str, list)):
            runs[aid] = _string_list(spec, f"{path}: {aid}")
            bots[aid] = []
            continue
        if isinstance(spec, dict) and set(spec).issubset({"runs", "bots"}):
            runs[aid] = _string_list(spec.get("runs") or [], f"{path}: {aid}.runs")
            bots[aid] = _string_list(spec.get("bots") or [], f"{path}: {aid}.bots")
            continue
        raise RuntimeError(
            f"{path}: {aid} must map to a run key, a list of them, or runs/bots"
        )
    return AgentMap(runs=runs, bots=bots)


def deed_bases_by_run() -> dict[str, list[str]]:
    """Bot bases the bots page credits to each run, from the deed index.

    The same last join ``/bots`` uses: a recorded deploy, not a name. A bot
    inside a namespace is already covered by the owner's namespace and is
    listed again here only when the index says so; callers dedupe.
    """
    from condor.agents.deed_index import build_deed_index

    out: dict[str, list[str]] = {}
    for base, ref in build_deed_index().bots.items():
        bases = out.setdefault(ref.run_key, [])
        if base not in bases:
            bases.append(base)
    return out


def match_owners(
    agent_ids: Iterable[str],
    owners: Iterable[Any],
    agent_map: dict[str, list[str]] | None = None,
) -> dict[str, list]:
    """Each race agent id → the Condor strategies it names.

    An id in ``agent_map`` names exactly the run keys listed there, including a
    chat or delegation: the bots page credits a bot to whichever run deployed
    it, and a delegation's namespace is empty. Any other id is a strategy slug,
    so it can match under more than one agent; all of them count. A full run
    key (``agent.strategy``) is accepted too. An unmapped pseudo-run is never a
    race agent — slug matching still requires a namespace.
    """
    agent_map = agent_map or {}
    named = {key for keys in agent_map.values() for key in keys}
    owners = [
        owner for owner in owners if owner.namespace or owner.run_key in named
    ]
    matched: dict[str, list] = {}
    for agent_id in agent_ids:
        if agent_id in agent_map:
            keys = agent_map[agent_id]
            matched[agent_id] = [owner for owner in owners if owner.run_key in keys]
        else:
            matched[agent_id] = [
                owner
                for owner in owners
                if agent_id in (owner.strategy_slug, owner.run_key)
            ]
    return matched


async def collect_totals(
    client: Any,
    matched: dict[str, list],
    extra_bots: dict[str, list[str]] | None = None,
    ledger: dict[str, dict[str, float]] | None = None,
    server: str = "",
) -> tuple[dict[str, Totals], set[str], dict[str, dict[str, float]]]:
    """Lifetime PnL and volume per race agent, and which ones came back degraded.

    Bot figures are the portfolio page's Bots tile (live controller status).
    Executor figures still come from one batch over the session tags: the two
    sets are disjoint, so the sum is the bots on screen plus every standalone
    executor the sessions created. A client with no bot status — the unit-test
    double — keeps the batch's snapshot merge instead.

    Which bots, is the bots page's rule (``attributionIndex``): the namespace,
    any declared legacy name, and the bots the deed index recorded for this run
    — the only way a delegation's ``quad-e1`` is credited, since the name proves
    nothing. ``extra_bots`` adds bases that rule does not see. Every base for one
    race agent is partitioned together, so a short extra name such as
    ``midas_vol_race`` does not also swallow ``midas_vol_race-…``. An agent with
    no matching strategy and no named bots has traded nothing and reads zero.
    """
    from condor.agents.performance import fetch_agent_performance_batch

    extra_bots = extra_bots or {}
    deeds = deed_bases_by_run()
    tags: dict[str, list[str]] = {}
    bases: dict[str, list[str]] = {}
    for agent_id, owners in matched.items():
        owner_tags: list[str] = []
        seen: list[str] = []
        for owner in owners:
            these = list(owner.agent_ids) or [owner.run_key]
            owner_tags.extend(these)
            for base in (
                owner.namespace,
                *owner.declared_bots,
                *deeds.get(owner.run_key, ()),
            ):
                if base and base not in seen:
                    seen.append(base)
        for base in extra_bots.get(agent_id, []):
            if base and base not in seen:
                seen.append(base)
        if not owner_tags and seen:
            owner_tags.append(agent_id)
        tags[agent_id] = owner_tags
        if seen and owner_tags:
            bases[owner_tags[0]] = seen

    all_tags = [tag for owner_tags in tags.values() for tag in owner_tags]
    failed: set[str] = set()
    try:
        live = await read_portfolio_controllers(client)
    except Exception as exc:  # noqa: BLE001 - the snapshot still answers
        log.warning("Portfolio bot status failed (%s); using the snapshot", exc)
        live = None
    # This tick's per-bot snapshot, and the set of bots currently live. Both are
    # taken before any retired generation is added, so a bot is never counted as
    # both live and retired.
    snapshots = bot_rows_by_name(live) if live is not None else {}
    # Live status is the portfolio page. Passing the bases into the batch as
    # well would add the snapshot on top of it.
    perf = await fetch_agent_performance_batch(
        client, all_tags, None if live is not None else bases, failed_ids=failed
    )

    totals: dict[str, Totals] = {}
    degraded: set[str] = set()
    for agent_id, owner_tags in tags.items():
        batch_pnl = sum(perf[tag].total_pnl for tag in owner_tags if tag in perf)
        batch_vol = sum(perf[tag].volume for tag in owner_tags if tag in perf)
        totals[agent_id] = Totals(pnl=batch_pnl, volume=batch_vol)
        live_tot = Totals()
        retired_tot = Totals()
        if live is not None and owner_tags:
            live_tot = portfolio_bot_totals(live, bases.get(owner_tags[0], []))
            totals[agent_id] = totals[agent_id].plus(live_tot)
            # ...plus every generation this agent has retired. Wrapped so a bad
            # archive can never cost the live figure: the worst case is the old
            # understated number, never a failed tick.
            try:
                retired_tot = await inactive_bot_totals(
                    client,
                    bases.get(owner_tags[0], []),
                    ledger if ledger is not None else {},
                    server,
                    snapshots,
                )
                totals[agent_id] = totals[agent_id].plus(retired_tot)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Retired-generation accounting failed for %s (%s)", agent_id, exc
                )
        if os.environ.get("HACKATHON_DEBUG_RETIRED"):
            log.warning(
                "[breakdown] %-52s batch(pnl=%+.4f vol=%10.2f) live(pnl=%+.4f "
                "vol=%10.2f) retired(pnl=%+.4f vol=%10.2f)",
                agent_id,
                batch_pnl,
                batch_vol,
                live_tot.pnl,
                live_tot.volume,
                retired_tot.pnl,
                retired_tot.volume,
            )
        if failed.intersection(owner_tags):
            degraded.add(agent_id)
    return totals, degraded, snapshots


# ── Direct-venue agents (no Hummingbot bot/controller/executor at all) ──
#
# theta's order path (``theta_hedge``) signs locally against Derive's v3 API
# and never creates a Hummingbot bot, controller, or executor — so neither of
# ``collect_totals``'s two sources (the Bots tile, the executor-tag batch) can
# ever see its trades, by construction, not by a missing agent-map entry. This
# reads the venue directly for the one subaccount theta trades from and
# returns its lifetime fill volume + realized PnL in the same ``Totals`` shape
# everything else in this file already uses, so it drops into the normal
# baseline/since-start/state-file machinery unmodified.
#
# One venue read, scoped to one agent id. A failure here must not cost the
# other 13 agents their tick (same rule ``_read_server`` already applies per
# server), so the caller wraps this in a try/except and logs rather than
# raises.
DIRECT_VENUE_AGENTS: dict[str, dict[str, Any]] = {
    "theta-covered-call-hedge-desk": {
        "keystore": "/home/hummingbot/condor/bots/credentials/theta/derive_perpetual.yml",
        "subaccount_id": 107967,
    },
    # flyby places Derive orders from its own routine, so it has no Condor
    # controller or executor for the bot-status path to read: its fills are only
    # visible on the venue. Measured here from the same subaccount the routine signs with.
    "flyby-long-volatility-collector-desk": {
        "keystore": "/home/hummingbot/condor/bots/credentials/master_account/connectors/derive_perpetual.yml",
        "subaccount_id": 74536,
    },
}


async def fetch_direct_venue_totals() -> dict[str, Totals]:
    """Lifetime fill volume + current PnL for agents with no Condor-visible bot.

    Volume is every fill's ``trade_price * trade_amount`` from
    ``private/get_trade_history``, summed across pages. PnL is the account's
    present figure, not just what has settled: ``realized_pnl`` from the trade
    history (closed legs) plus ``unrealized_pnl`` from each OPEN position via
    ``private/get_positions`` (mark-to-market on anything still held).

    Funding (``cumulative_funding`` / ``pending_funding``) is deliberately left
    out of PnL. Verified against a live position (identity closes to the last
    digit): ``unrealized_pnl == unrealized_pnl_excl_fees - total_fees``, with
    no funding term anywhere in it — Derive's own schema confirms funding is
    tracked separately, as a margin-calculation input, not a PnL component
    (``pending_funding`` is "added to the portfolio value for margin
    calculations"; ``net_settlements`` is unrelated settlement cashflow,
    "subtracted from the portfolio value for margin calculations", not
    funding). So funding genuinely sits outside unrealized_pnl rather than
    being folded into it — the reason to exclude it here is that it is not,
    by itself, the account's P&L: a short can accrue positive funding while
    the position itself is underwater on price, which funding-only would
    misreport as green. At the size seen on theta's book (sub-cent) it would
    not matter either way, but the number must stay right as it scales.
    Empty dict (not an exception) if nothing is configured.
    """
    if not DIRECT_VENUE_AGENTS:
        return {}

    import sys as _sys
    from pathlib import Path as _Path

    theta_routines = _Path(
        "/home/hummingbot/condor/.condor/agents/theta/routines"
    )
    theta_root = theta_routines.parent
    for p in (str(theta_routines), str(theta_root)):
        if p not in _sys.path:
            _sys.path.insert(0, p)
    from _theta_derive import from_condor_keystore, _post  # noqa: E402

    out: dict[str, Totals] = {}
    for agent_id, spec in DIRECT_VENUE_AGENTS.items():
        creds = from_condor_keystore(keystore=spec["keystore"])
        sub_id = int(spec["subaccount_id"])
        volume = 0.0
        pnl = 0.0
        page = 1
        while True:
            resp = await _post(
                creds,
                "get_trade_history",
                {"subaccount_id": sub_id, "page": page, "page_size": 1000},
            )
            trades = resp.get("trades") or []
            for t in trades:
                volume += abs(float(t.get("trade_price") or 0)) * abs(
                    float(t.get("trade_amount") or 0)
                )
                pnl += float(t.get("realized_pnl") or 0)
            pagination = resp.get("pagination") or {}
            num_pages = int(pagination.get("num_pages") or 1)
            if page >= num_pages:
                break
            page += 1
        positions = await _post(
            creds, "get_positions", {"subaccount_id": sub_id}
        )
        for pos in positions.get("positions") or []:
            pnl += float(pos.get("unrealized_pnl") or 0)
        out[agent_id] = Totals(pnl=pnl, volume=volume)
    return out


async def read_portfolio_controllers(client: Any) -> list[dict] | None:
    """Controllers the portfolio page's Bots tile sums, or ``None`` if unavailable.

    ``None`` means this client has no bot-status call (the test double). An
    empty list means the server has no running bot, which is what the page shows.
    """
    getter = getattr(
        getattr(client, "bot_orchestration", None), "get_active_bots_status", None
    )
    if getter is None:
        return None
    from condor.fetchers.bots import build_bots_page

    page = build_bots_page(await getter())
    return list(page.get("controllers") or [])


def portfolio_bot_totals(controllers: list[dict], bases: list[str]) -> Totals:
    """Sum the portfolio page's controller rows that these bases own.

    Longest base wins, the same partition the bots page uses, so a namespace
    and a longer tagged sibling of it are not both credited.
    """
    from condor.fetchers.bot_performance import partition_instances

    by_name: dict[str, Totals] = {}
    for ctrl in controllers:
        name = str(ctrl.get("bot_name") or "")
        if not name:
            continue
        row = Totals(
            float(ctrl.get("global_pnl_quote") or 0),
            float(ctrl.get("volume_traded") or 0),
        )
        by_name[name] = by_name.get(name, Totals()).plus(row)
    assigned = partition_instances({name: {} for name in by_name}, bases)
    total = Totals()
    for names in assigned.values():
        for name in names:
            total = total.plus(by_name[name])
    return total


# -- Bots that are no longer live: archived runs, and stopped-but-unarchived --
#
# A race agent's figure is not the figure of the bot it happens to be running
# this minute. Every redeploy -- the agent's own cold start, a drawdown kill, an
# operator revision -- starts the new bot's counters at zero, and the retired
# bot's lifetime contribution used to vanish from the total. That dropped the
# reported volume AND hid the loss the retired sleeve had already realised
# (quad-e4 stopped on its own drawdown cap and its -$39.26 disappeared, leaving
# the entry reading +$1.87 profit). So an agent total is now:
#
#     live bots + every retired generation
#
# Two sources, and they are not interchangeable:
#
#   1. ARCHIVED runs. Authoritative and immutable: the sqlite the stopped bot
#      left behind, read once per process (``fetch_archived_run`` caches it).
#      PnL is the sum of each executor's ``net_pnl_quote`` -- the same
#      fee-inclusive net the live fleet reports -- and NOT the model's
#      ``total_pnl``. ``total_pnl`` is not gross: condor/archived_pnl.py charges
#      a fee per fill, but on the sell leg only, so it is net of roughly HALF the
#      fee load. On the r4 archive the fills' fees were BUY 0.575221 + SELL
#      0.513049 = 1.088270 and total_pnl read +0.660335 -- gross minus the sell
#      legs alone; charging the buy legs too gives +0.085114, which still is not
#      the executors' -0.515330. The two are different estimators of one history,
#      not two conventions for one, and the executors' net is the one that
#      matches what the board already published for that bot while it ran.
#
#      Volume comes from the trade walk (``total_volume``); PnL comes from the
#      executor rows. That mix is deliberate: each mirrors what the live path
#      publishes (``volume_traded`` is trade-derived, the live PnL is fee-net per
#      controller) and each is the reliable source for its own metric. The
#      executor table's volume column is not usable -- a closed executor was seen
#      there with filled_amount_quote 0.0 beside non-zero fees. Do not expect the
#      two to agree; HACKATHON_DEBUG_RETIRED prints both side by side.
#
#   2. STOPPED-but-unarchived runs. No route can read their sqlite: the archived
#      reader refuses any path outside ``bots/archived/``, and
#      ``/bot-orchestration/<bot>/history`` asks the live container (commlib),
#      which answers with empty trades once the strategy has stopped. The only
#      surviving record is the last snapshot this monitor took while that bot was
#      still live, so ``bot_ledger`` keeps one per bot name in the state file.
#
# UNREALIZED is never taken from a retired generation. The inventory carries
# into the live fleet, so crediting a retired generation's unrealized would count
# one open position twice and double the loss at every generation change.


def realized_usd(executor: Any) -> float:
    """One retired executor's realized PnL, in USD.

    Two corrections a naive sum gets wrong:

    - Only CLOSED executors are realized. ``net_pnl_quote`` on an executor that
      never closed is a mark-to-market, and crediting that as realized would put
      unrealized into a retired generation -- the one thing this design must not
      do. Filter on ``close_timestamp``: ``status`` is the raw numeric enum out of
      the DB (every executor on api-2 reads "4"), so a status check would match
      nothing and silently let everything through.
    - ``usd_rate``. Every other figure from this fetch is restated in USD --
      ``convert_trades_to_usd`` runs before the volume is totalled -- but the
      executors keep their native quote, and upstream's own
      ``calculate_pnl_from_executors`` multiplies by the rate. Without it a
      generation quoted in anything but USD posts a native-quote PnL beside a USD
      volume, off by the FX rate.
    """
    if not float(getattr(executor, "close_timestamp", 0.0) or 0.0):
        return 0.0
    pnl = float(getattr(executor, "pnl", 0.0) or 0.0)
    return pnl * float(getattr(executor, "usd_rate", 1.0) or 1.0)


def bot_rows_by_name(controllers: list[dict]) -> dict[str, dict[str, float]]:
    """Per live bot name: its rows' {volume, realized} - the ledger snapshot."""
    out: dict[str, dict[str, float]] = {}
    for ctrl in controllers:
        name = str(ctrl.get("bot_name") or "")
        if not name:
            continue
        row = out.setdefault(name, {"volume": 0.0, "realized": 0.0})
        row["volume"] += float(ctrl.get("volume_traded") or 0)
        row["realized"] += float(ctrl.get("realized_pnl_quote") or 0)
    return out


# Archive listings, held briefly. The listing is one cheap call, but it fired
# once per agent per server per tick (14x); more to the point, its UNION across
# servers is what the ledger is filtered against, so a generation archived on one
# server is never also credited from another server's snapshot in the same tick.
_ARCHIVE_PATHS: dict[str, tuple[float, list[str]]] = {}
_ARCHIVE_PATHS_TTL = 120.0


def clear_archive_paths_cache() -> None:
    """Forget the cached listings, so a test (or a re-pointed client) re-lists."""
    _ARCHIVE_PATHS.clear()


def fleet_archived_names() -> set[str]:
    """Every bot name known to hold an archive, on any server."""
    from condor.fetchers.archived_run import extract_bot_name

    names: set[str] = set()
    for _at, paths in _ARCHIVE_PATHS.values():
        names |= {extract_bot_name(p) for p in paths}
    return names


async def listed_archive_paths(client: Any, server: str) -> list[str]:
    """One server's archive db paths, from cache when listed recently."""
    now = time.monotonic()
    cached = _ARCHIVE_PATHS.get(server)
    if cached is not None and now - cached[0] < _ARCHIVE_PATHS_TTL:
        return list(cached[1])
    reader = getattr(client, "archived_bots", None)
    if reader is None:
        return []
    try:
        dbs = await reader.list_databases()
    except Exception as exc:  # noqa: BLE001 - no archive list is not a failed tick
        log.warning("Archived-bot list failed on %s (%s)", server, exc)
        # Fall back to the last good listing rather than crediting nothing: a
        # failed listing must never make a generation disappear for a tick.
        return list(cached[1]) if cached else []
    if not isinstance(dbs, list):
        return []
    paths = [d for d in dbs if isinstance(d, str)]
    _ARCHIVE_PATHS[server] = (now, paths)
    return list(paths)


async def archived_bot_rows(client: Any, server: str) -> dict[str, Any]:
    """{bot name: ArchivedBotPerformance} for every archive on one server."""
    from condor.fetchers.archived_run import extract_bot_name, fetch_archived_run

    out: dict[str, Any] = {}
    for db_path in await listed_archive_paths(client, server):
        name = extract_bot_name(db_path)
        try:
            out[name] = await fetch_archived_run(client, server, db_path)
        except Exception as exc:  # noqa: BLE001 - one bad archive, not the server
            log.warning("Archived run %s on %s unreadable (%s)", name, server, exc)
    return out


async def inactive_bot_totals(
    client: Any,
    bases: list[str],
    ledger: dict[str, dict[str, float]],
    server: str,
    live_rows: dict[str, dict[str, float]],
) -> Totals:
    """Volume + net realized PnL from this agent's retired generations.

    The ledger is read, never mutated: an entry whose bot also has an archive is
    SKIPPED here (the archive supersedes it), and leaving the record in place
    means a later agent's failed listing cannot make a generation vanish
    mid-tick.
    """
    from condor.fetchers.bot_performance import partition_instances

    def _live_volume(rows: dict[str, dict[str, float]], name: str) -> float:
        return float((rows.get(name) or {}).get("volume") or 0.0)

    def reporting(name: str) -> bool:
        """Is this bot still giving a real live reading?

        A bot the API still lists whose strategy has died reports zeros. That is
        the absence of a reading, not a reading of zero, so a retired generation
        must not be dismissed as live on the strength of it.
        """
        row = live_rows.get(name) or {}
        return bool(row.get("volume") or row.get("realized"))

    archived = await archived_bot_rows(client, server)
    superseded = set(archived) | fleet_archived_names()

    rows: dict[str, Totals] = {}
    for name, perf in archived.items():
        executors = perf.executors or []
        closed = [
            e for e in executors if float(getattr(e, "close_timestamp", 0.0) or 0.0)
        ]
        total_volume = float(getattr(perf, "total_volume", 0.0) or 0.0)
        pnl = sum(realized_usd(e) for e in executors)
        if total_volume <= 0.0 and closed:
            # A generation that closed executors cannot have traded nothing, so a
            # zero here means the trade walk came back empty or truncated -- the
            # real way this figure goes wrong. (A divergence from the executors'
            # own filled column is NOT a warning sign: the two legitimately span
            # 0.46x to 3.7x on the archives we have.)
            log.warning(
                "Archived run %s on %s credits %d closed executor(s) but reports "
                "trade volume 0 (stats_source=%s): the walk looks truncated",
                name,
                server,
                len(closed),
                getattr(perf, "stats_source", "?"),
            )
        # An archive and a live row sharing a name are two generations IF the live
        # counters restarted: cumulative volume cannot fall within one generation,
        # so a live reading below the archive's total means a redeploy under a
        # reused name and the archive must still be counted. Only once the live
        # reading has caught up is "already counted live" the safe read.
        if reporting(name) and _live_volume(live_rows, name) >= total_volume:
            continue
        if reporting(name):
            # Credited from the archive AND from the live fleet this tick. This is
            # the one double count the design accepts, and it is deliberately
            # LOUD: the volume comparison cannot separate a fresh generation that
            # has traded less (the reuse case this rule exists for) from a stale
            # partial reading of the old one, and no rule is sound in both
            # directions. Counting the archive is the point of the change, so the
            # live row stands too -- do NOT "fix" it by dropping the name from the
            # live sum, which under-counts the genuine reuse case.
            log.warning(
                "Archived run %s on %s is ALSO live (live volume %.2f < archive "
                "%.2f): this name is credited from both the archive and the live "
                "fleet this tick",
                name,
                server,
                _live_volume(live_rows, name),
                total_volume,
            )
        rows[name] = Totals(pnl=pnl, volume=total_volume)
        if os.environ.get("HACKATHON_DEBUG_RETIRED"):
            log.warning(
                "[retired-src] %s %s stats_source=%s executors=%d closed=%d "
                "trade_vol=%.2f",
                server,
                name,
                getattr(perf, "stats_source", "?"),
                len(executors),
                len(closed),
                total_volume,
            )
    for name, snap in ledger.items():
        if reporting(name) or name in superseded:
            continue
        rows[name] = Totals(
            pnl=float(snap.get("realized") or 0.0),
            volume=float(snap.get("volume") or 0.0),
        )
    if not rows:
        return Totals()

    assigned = partition_instances({name: {} for name in rows}, bases)
    total = Totals()
    for names in assigned.values():
        for name in names:
            total = total.plus(rows[name])
    if os.environ.get("HACKATHON_DEBUG_RETIRED"):
        # Audit line: which retired generations were credited, and to whom.
        log.warning(
            "[retired] %s bases=%s | %s | TOTAL pnl=%+.4f vol=%.2f",
            server,
            bases,
            "; ".join(
                "%s(pnl=%+.4f trade_vol=%.2f)" % (n, rows[n].pnl, rows[n].volume)
                for n in sorted(rows)
            ),
            total.pnl,
            total.volume,
        )
    return total


# -- The state file: the baseline, and each server's last figures --


def read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _dump(totals: dict[str, Totals]) -> dict[str, dict[str, float]]:
    return {a: {"pnl": t.pnl, "volume": t.volume} for a, t in totals.items()}


def _parse(rows: Any) -> dict[str, Totals]:
    return {
        agent_id: Totals(float(row["pnl"]), float(row["volume"]))
        for agent_id, row in (rows or {}).items()
    }


def load_baseline(path: Path) -> dict[str, Totals] | None:
    """What each agent had at the start of the race, or ``None`` if never taken."""
    state = read_state(path)
    return _parse(state["baseline"]) if "baseline" in state else None


def save_baseline(path: Path, totals: dict[str, Totals], now: float) -> None:
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds")
    write_state(
        path, {**read_state(path), "started_at": stamp, "baseline": _dump(totals)}
    )


def load_last_good(path: Path) -> dict[str, dict[str, Totals]]:
    """Per server, the last clean figure each agent had there."""
    servers = read_state(path).get("last_good") or {}
    return {server: _parse(rows) for server, rows in servers.items()}


def save_last_good(path: Path, last_good: dict[str, dict[str, Totals]]) -> None:
    dumped = {server: _dump(rows) for server, rows in last_good.items()}
    write_state(path, {**read_state(path), "last_good": dumped})


# Retired generations no source can read any more, seeded by hand. Kept here
# rather than only in the state file, which --dry-run never writes and a reset
# would erase, taking the recorded loss with it. Each figure was measured and the
# comment says from where: do not add one without that evidence.
SEEDED_RETIRED: dict[str, dict[str, dict[str, float]]] = {
    "hummingbot-api-1": {
        # quad-e4 was killed by its own drawdown cap at 03:21:42 on 2026-10-08
        # with the container still up and reporting zeros. api-1 holds no archive
        # for it and no route reads a stopped bot's sqlite, so this is its last
        # live snapshot: the board's own drop, 112,495.04 -> 4,330.18, whose
        # difference is exactly the volume below, and its executors'
        # net_pnl_quote, all realized, no positions left.
        "quad-e4-20261007-051441": {"volume": 108164.86, "realized": -39.26},
    },
}


def load_bot_ledger(path: Path) -> dict[str, dict[str, dict[str, float]]]:
    """Per server, the last snapshot of every bot this monitor saw live.

    Kept after a bot stops. For a stopped-but-unarchived bot this is the only
    surviving record of what it traded, because nothing can read its sqlite.

    Nothing prunes this ledger: every bot name seen since the race started
    accumulates, a few dozen rows, and one that is archived is skipped rather than
    removed -- bounded by the number of generations, not by time.
    """
    servers = read_state(path).get("bot_ledger") or {}
    seeded = {
        server: {name: dict(snap) for name, snap in rows.items()}
        for server, rows in SEEDED_RETIRED.items()
    }
    ledger: dict[str, dict[str, dict[str, float]]] = {
        server: dict(rows) for server, rows in seeded.items()
    }
    for server, rows in servers.items():
        target = ledger.setdefault(server, {})
        for name, snap in (rows or {}).items():
            if name in seeded.get(server, {}):
                # Seeded names are AUTHORITATIVE: they cover bots no source can
                # read any more, so no state entry may override one. A live
                # reading recorded while the bot ran still can -- that happens in
                # the merge, not here.
                continue
            target[name] = dict(snap)
    return ledger


def save_bot_ledger(path: Path, ledger: dict[str, dict[str, dict[str, float]]]) -> None:
    write_state(path, {**read_state(path), "bot_ledger": ledger})


def distinct_servers(servers: dict[str, dict], wanted: list[str]) -> list[str]:
    """The servers to read, each API once.

    ``wanted`` restricts the set and must name configured servers. Two names for
    one host and port are one API, and counting it twice would double every
    figure on it, so only the first name is kept.
    """
    unknown = [name for name in wanted if name not in servers]
    if unknown:
        raise ValueError(f"Unknown server(s): {', '.join(unknown)}")
    names: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for name in wanted or list(servers):
        config = servers[name] or {}
        key = (str(config.get("host", name)), str(config.get("port", "")))
        if key in seen:
            log.warning("%s is the same API as %s; reading it once", name, seen[key])
            continue
        seen[key] = name
        names.append(name)
    return names


def combine(
    agent_ids: Iterable[str],
    answers: dict[str, tuple[dict[str, Totals], set[str]] | None],
    last_good: dict[str, dict[str, Totals]],
) -> tuple[dict[str, Totals], dict[str, set[str]]]:
    """Sum the field across servers, and say which figures were not fresh.

    ``answers`` maps a server to its ``(totals, degraded)``, or ``None`` when it
    did not answer at all. A clean figure is used and remembered in
    ``last_good``; anything else falls back to the last clean one from that
    server. Returns the sums and ``{server: agents}`` for the pairs that have
    never had a clean figure, which count as nothing.
    """
    agent_ids = list(agent_ids)
    totals = {agent_id: Totals() for agent_id in agent_ids}
    unknown: dict[str, set[str]] = {}
    for server, answer in answers.items():
        fresh, degraded = answer if answer is not None else ({}, set(agent_ids))
        known = last_good.setdefault(server, {})
        for agent_id in agent_ids:
            if agent_id in fresh and agent_id not in degraded:
                known[agent_id] = fresh[agent_id]
            if agent_id in known:
                totals[agent_id] = totals[agent_id].plus(known[agent_id])
            else:
                unknown.setdefault(server, set()).add(agent_id)
    return totals, unknown


def since_start(
    totals: dict[str, Totals], baseline: dict[str, Totals] | None
) -> dict[str, Totals]:
    """Lifetime figures restated from the race start.

    An agent missing from the baseline had nothing when it was taken.
    """
    if baseline is None:
        return dict(totals)
    return {
        agent_id: t.minus(baseline.get(agent_id, Totals()))
        for agent_id, t in totals.items()
    }


# ── The loop ──


class Monitor:
    def __init__(self, args: argparse.Namespace, session: Any) -> None:
        self.args = args
        self.session = session
        self.url = race_url(args.url, args.slug)
        self.state_path = Path(args.state.format(slug=args.slug))
        self.agent_ids: list[str] = []
        # Per server, the last clean figure per agent: a failed fetch is reported
        # as the last thing known rather than as an agent that gave its PnL back.
        self.last_good = load_last_good(self.state_path)
        # Per server, the last snapshot of each bot seen live. Retained when a bot
        # stops so a retired generation keeps contributing (see inactive_bot_totals).
        self.bot_ledger = load_bot_ledger(self.state_path)
        self.posted = False

    async def refresh_agent_ids(self) -> None:
        """Re-read the field; keep the last list when the site does not answer."""
        try:
            ids = await fetch_agent_ids(self.session, self.url)
        except Exception as exc:  # noqa: BLE001 - the last list still stands
            if not self.agent_ids:
                raise
            log.warning("Could not refresh the agent list (%s); keeping it", exc)
            return
        if not ids:
            raise RuntimeError(f"GET {self.url} listed no agents")
        if ids != self.agent_ids:
            log.info("Race agents (%d): %s", len(ids), ", ".join(ids))
        self.agent_ids = ids

    async def _read_server(
        self, name: str, matched: dict[str, list], extra_bots: dict[str, list[str]]
    ) -> tuple[dict[str, Totals], set[str]] | None:
        from config_manager import get_config_manager

        async def read() -> tuple[dict[str, Totals], set[str], dict]:
            client = await get_config_manager().get_client(name)
            return await collect_totals(
                client, matched, extra_bots, self.bot_ledger.get(name, {}), name
            )

        try:
            return await asyncio.wait_for(read(), SERVER_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - one server down is not the tick
            log.warning("Server %s did not answer: %s", name, exc or type(exc).__name__)
            return None

    def _merge_ledger(self, names: list[str], answers: list) -> None:
        """Fold this tick's live per-bot snapshots into the persistent ledger.

        Entries for bots that have stopped are deliberately NOT removed here: a
        stopped bot's last snapshot is what keeps its volume and realized PnL on
        the board. Pruning happens only when a bot turns up archived, where the
        archive supersedes it.
        """
        for name, answer in zip(names, answers):
            if not answer or len(answer) < 3:
                continue
            rows = answer[2] or {}
            server_ledger = self.bot_ledger.setdefault(name, {})
            for bot_name, snap in rows.items():
                prior = server_ledger.get(bot_name)
                if prior and not snap.get("volume") and not snap.get("realized"):
                    # Still registered, no longer reporting (its strategy died).
                    # Keep what it was last worth rather than overwriting the
                    # record with a zero that is not a reading.
                    continue
                if prior:
                    # Merge keys, so a seeded note stands beside the reading.
                    merged = dict(prior)
                    merged.update(snap)
                    server_ledger[bot_name] = merged
                else:
                    server_ledger[bot_name] = dict(snap)

    async def measure(self) -> tuple[dict[str, Totals], dict[str, set[str]]]:
        """Lifetime totals for the field over every server, and what is unknown."""
        from condor.agents.fleet_map import build_fleet_map
        from config_manager import get_config_manager

        agent_map = (
            load_agent_map(self.args.agent_map)
            if self.args.agent_map
            else AgentMap(runs={}, bots={})
        )
        matched = match_owners(self.agent_ids, build_fleet_map(), agent_map.runs)
        for agent_id, owners in matched.items():
            found = {owner.run_key for owner in owners}
            if agent_id in agent_map.runs:
                missing = [key for key in agent_map.runs[agent_id] if key not in found]
                if missing or not owners:
                    log.warning(
                        "%s: no Condor strategy with run key %s",
                        agent_id,
                        ", ".join(missing) or "(none listed)",
                    )
                continue
            if not owners:
                log.warning(
                    "%s: no Condor strategy with that slug; map it to a run key "
                    "in the agent map",
                    agent_id,
                )

        names = distinct_servers(get_config_manager().list_servers(), self.args.servers)
        # List every server's archives BEFORE the per-server reads, which are
        # gathered concurrently. The retired-generation filter needs the union
        # across servers, and building it from inside those reads would leave it
        # empty or partial on the first tick after a restart -- a silent
        # nondeterminism in a correctness filter. Cheap: the listing is cached, so
        # the reads below reuse it.
        for name in names:
            try:
                client = await get_config_manager().get_client(name)
                await listed_archive_paths(client, name)
            except Exception as exc:  # noqa: BLE001 - a failed listing is not fatal
                log.warning(
                    "Archive listing for %s failed during prime (%s)", name, exc
                )
        answers = await asyncio.gather(
            *(self._read_server(name, matched, agent_map.bots) for name in names)
        )
        # Each answer carries this tick's per-bot snapshot as a third element;
        # fold those into the ledger, then hand combine() the pair it expects.
        self._merge_ledger(names, answers)
        totals, unknown = combine(
            self.agent_ids,
            {
                name: (answer[0], answer[1]) if answer else None
                for name, answer in zip(names, answers)
            },
            self.last_good,
        )
        for server, agents in sorted(unknown.items()):
            log.warning(
                "No figure ever from %s for %d agent(s); counted as nothing",
                server,
                len(agents),
            )
        try:
            direct = await fetch_direct_venue_totals()
        except Exception as exc:  # noqa: BLE001 - a direct-venue miss is not the tick
            log.warning("Direct-venue read failed (%s); using Condor figures only", exc)
            direct = {}
        for agent_id, extra in direct.items():
            if agent_id in totals:
                totals[agent_id] = totals[agent_id].plus(extra)
            unknown.pop(agent_id, None)
            for server_agents in unknown.values():
                server_agents.discard(agent_id)
        return totals, unknown

    async def tick(self) -> None:
        await self.refresh_agent_ids()
        totals, unknown = await self.measure()
        now = time.time()

        if self.args.start_race:
            if unknown:
                raise RuntimeError(
                    "Cannot take the baseline: no figure from "
                    + ", ".join(sorted(unknown))
                    + ". Fix the server or leave it out with --servers."
                )
            save_baseline(self.state_path, totals, now)
            log.info("Race started: baseline written to %s", self.state_path)
            self.args.start_race = False
        if not self.args.dry_run:
            save_last_good(self.state_path, self.last_good)
            save_bot_ledger(self.state_path, self.bot_ledger)

        baseline = load_baseline(self.state_path)
        payload = build_payload(self.agent_ids, since_start(totals, baseline), now)
        for row in payload["agents"]:
            log.info(
                "  %-28s pnl %12.2f   volume %14.2f",
                row["agent_id"],
                row["pnl_quote"],
                row["volume_quote"],
            )
        if self.args.dry_run:
            print(json.dumps(payload, indent=2))
            return
        accepted, unknown = await post_race_data(self.session, self.url, payload)
        self.posted = True
        log.info(
            "Posted %d agents, %d accepted (%s)",
            len(payload["agents"]),
            accepted,
            "since race start" if baseline is not None else "lifetime, no baseline",
        )
        if unknown:
            log.warning(
                "The site does not know %d agent ids: %s",
                len(unknown),
                ", ".join(unknown),
            )

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                # Before the first post there is nothing to protect: fail loudly
                # on a bad URL or token. After it, a bad minute is just skipped.
                if self.args.once or self.args.start_race or not self.posted:
                    raise
                log.exception("Tick failed; retrying next interval")
            if self.args.once:
                return
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, self.args.interval - elapsed))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=env.get("HACKATHON_API_URL", ""))
    parser.add_argument("--token", default=env.get("HACKATHON_API_TOKEN", ""))
    parser.add_argument("--slug", default=env.get("HACKATHON_SLUG", DEFAULT_SLUG))
    parser.add_argument(
        "--servers",
        default=env.get("HACKATHON_SERVERS", ""),
        help="comma-separated server names; default is every configured server",
    )
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument(
        "--agent-map",
        type=Path,
        default=env.get("HACKATHON_AGENT_MAP") or None,
        help="YAML of race agent id -> Condor run keys, for slugs that differ",
    )
    parser.add_argument("--once", action="store_true", help="one tick, then exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the payload instead of posting"
    )
    parser.add_argument(
        "--start-race",
        action="store_true",
        help="record the current figures as the baseline, then keep running",
    )
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("set HACKATHON_API_URL or pass --url")
    if not args.token:
        parser.error("set HACKATHON_API_TOKEN or pass --token")
    state = Path(args.state.format(slug=args.slug))
    args.servers = [name.strip() for name in args.servers.split(",") if name.strip()]
    if args.agent_map:
        args.agent_map = Path(args.agent_map)
        if not args.agent_map.is_file():
            parser.error(f"agent map {args.agent_map} does not exist")
        load_agent_map(args.agent_map)
    if args.start_race and load_baseline(state) is not None:
        parser.error(f"{state} already holds a baseline; delete it to restart the race")
    if args.start_race and args.dry_run:
        parser.error("--start-race writes the baseline; it cannot be a dry run")
    return args


async def main(argv: list[str] | None = None) -> None:
    import aiohttp
    from dotenv import load_dotenv

    load_dotenv()
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # A strategy that has not deployed yet is warned about on every fetch; the
    # monitor's own lines already say who has no figure.
    logging.getLogger("condor.agents.performance").setLevel(logging.ERROR)

    from config_manager import get_config_manager

    try:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {args.token}"},
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
        ) as session:
            await Monitor(args, session).run()
    finally:
        await get_config_manager().close_all_clients()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
