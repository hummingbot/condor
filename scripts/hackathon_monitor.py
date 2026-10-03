"""Report every hackathon agent's PnL and volume to the race API, once a minute.

The race API names its agents by **strategy slug**. Each one is resolved to the
Condor strategies with that slug and measured the way the bots page measures
them: the bots under the strategy's namespace (``{agent}-{strategy}``, stopped
instances included) plus the standalone executors its sessions created
(``controller_id = "{agent}.{strategy}_{N}"``). The figures come from
``fetch_agent_performance_batch``, the aggregator behind the dashboard, so the
number posted is the number on screen: PnL is realized plus the open book of
whatever is still running, and a non-USD quote is restated in USD.

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
the map is measured by those keys only; any other id is matched by slug. The
file is re-read every minute, so an edit takes effect without a restart::

    churn: [stable_churn_operator.stable_churn_supervisor]
    adaptive-orca-lp: [alice.orca_lp, bob.orca_lp]

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


def load_agent_map(path: Path) -> dict[str, list[str]]:
    """Race agent id → Condor run keys, from the runner's YAML file."""
    import yaml

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path}: expected a mapping of agent id to run keys")
    agent_map: dict[str, list[str]] = {}
    for agent_id, keys in raw.items():
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise RuntimeError(
                f"{path}: {agent_id} must map to a run key or a list of them"
            )
        agent_map[str(agent_id)] = keys
    return agent_map


def match_owners(
    agent_ids: Iterable[str],
    owners: Iterable[Any],
    agent_map: dict[str, list[str]] | None = None,
) -> dict[str, list]:
    """Each race agent id → the Condor strategies it names.

    An id in ``agent_map`` names exactly the run keys listed there. Any other id
    is a strategy slug, so it can match under more than one agent; all of them
    count. A full run key (``agent.strategy``) is accepted too. Pseudo-runs
    (chat, delegation) have an empty namespace and are never a race agent.
    """
    owners = [owner for owner in owners if owner.namespace]
    agent_map = agent_map or {}
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
    client: Any, matched: dict[str, list]
) -> tuple[dict[str, Totals], set[str]]:
    """Lifetime PnL and volume per race agent, and which ones came back degraded.

    One batch for the whole field. An owner's bots ride on the first of its
    session tags (or on its run key when it has no session yet): the batch adds a
    tag's bots to that tag's executors, and the two sets are disjoint, so the sum
    over an owner's tags is its bots plus every executor its sessions created.
    An agent with no matching strategy has traded nothing and reads zero.
    """
    from condor.agents.performance import fetch_agent_performance_batch

    tags: dict[str, list[str]] = {}
    bases: dict[str, list[str]] = {}
    for agent_id, owners in matched.items():
        tags[agent_id] = []
        for owner in owners:
            owner_tags = list(owner.agent_ids) or [owner.run_key]
            tags[agent_id].extend(owner_tags)
            bases[owner_tags[0]] = [owner.namespace, *owner.declared_bots]

    all_tags = [tag for owner_tags in tags.values() for tag in owner_tags]
    failed: set[str] = set()
    perf = await fetch_agent_performance_batch(
        client, all_tags, bases, failed_ids=failed
    )

    totals: dict[str, Totals] = {}
    degraded: set[str] = set()
    for agent_id, owner_tags in tags.items():
        totals[agent_id] = Totals(
            pnl=sum(perf[tag].total_pnl for tag in owner_tags if tag in perf),
            volume=sum(perf[tag].volume for tag in owner_tags if tag in perf),
        )
        if failed.intersection(owner_tags):
            degraded.add(agent_id)
    return totals, degraded


# ── The state file: the baseline, and each server's last figures ──


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
        self, name: str, matched: dict[str, list]
    ) -> tuple[dict[str, Totals], set[str]] | None:
        from config_manager import get_config_manager

        async def read() -> tuple[dict[str, Totals], set[str]]:
            client = await get_config_manager().get_client(name)
            return await collect_totals(client, matched)

        try:
            return await asyncio.wait_for(read(), SERVER_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - one server down is not the tick
            log.warning("Server %s did not answer: %s", name, exc or type(exc).__name__)
            return None

    async def measure(self) -> tuple[dict[str, Totals], dict[str, set[str]]]:
        """Lifetime totals for the field over every server, and what is unknown."""
        from condor.agents.fleet_map import build_fleet_map
        from config_manager import get_config_manager

        agent_map = load_agent_map(self.args.agent_map) if self.args.agent_map else {}
        matched = match_owners(self.agent_ids, build_fleet_map(), agent_map)
        for agent_id, owners in matched.items():
            if owners:
                continue
            if agent_id in agent_map:
                log.warning(
                    "%s: no Condor strategy with run key %s",
                    agent_id,
                    ", ".join(agent_map[agent_id]) or "(none listed)",
                )
            else:
                log.warning(
                    "%s: no Condor strategy with that slug; map it to a run key "
                    "in the agent map",
                    agent_id,
                )

        names = distinct_servers(get_config_manager().list_servers(), self.args.servers)
        answers = await asyncio.gather(
            *(self._read_server(name, matched) for name in names)
        )
        totals, unknown = combine(
            self.agent_ids, dict(zip(names, answers)), self.last_good
        )
        for server, agents in sorted(unknown.items()):
            log.warning(
                "No figure ever from %s for %d agent(s); counted as nothing",
                server,
                len(agents),
            )
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
