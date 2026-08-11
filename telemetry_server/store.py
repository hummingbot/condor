"""Persistence for the collector — one interface, two backends.

Production is Postgres (FEAT-024 chose it: known stack, ``ON CONFLICT`` gives
idempotency in one line, monthly partitions make retention a ``DROP TABLE``).
But a collector whose tests need a database daemon is a collector whose tests do
not run, so the same interface also speaks SQLite, which is what the suite in
``tests/test_telemetry_server.py`` drives. The two backends share their SQL:
every statement is written once with ``$n`` placeholders and a ``{jsonb}`` cast
hole, and each backend adapts only those two things. Sharing the statements is
the point — a second copy would drift exactly the way a second copy of the event
taxonomy would.

Everything an envelope contributes is parameterised. No value from a payload is
ever formatted into a statement; the only string interpolation in this file is
the partition maintenance in :mod:`telemetry_server.rollup`, which builds names
from dates it computed itself.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"

_PLACEHOLDER = re.compile(r"\$(\d+)")


# ── The shared statements ────────────────────────────────────────────────
# `$2` appears twice in the install upsert (first_seen and last_seen start
# equal); the SQLite adapter re-orders arguments by order of appearance, so a
# repeated placeholder is safe in both dialects.

UPSERT_INSTALL = """
INSERT INTO installs (
    install_id, first_seen, last_seen, level, version, branch,
    os, arch, python, in_docker, has_hb_api, has_gateway, has_web,
    user_count, server_count, agent_count, llm_providers, dropped_total)
VALUES ($1, $2, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
        $13, $14, $15, $16, $17)
ON CONFLICT (install_id) DO UPDATE SET
    last_seen     = excluded.last_seen,
    level         = excluded.level,
    version       = excluded.version,
    branch        = excluded.branch,
    os            = excluded.os,
    arch          = excluded.arch,
    python        = excluded.python,
    in_docker     = excluded.in_docker,
    has_hb_api    = excluded.has_hb_api,
    has_gateway   = excluded.has_gateway,
    has_web       = excluded.has_web,
    user_count    = excluded.user_count,
    server_count  = excluded.server_count,
    agent_count   = excluded.agent_count,
    llm_providers = excluded.llm_providers,
    dropped_total = installs.dropped_total + excluded.dropped_total
"""
# `first_seen` is deliberately absent from the DO UPDATE list: first contact is
# a fact about the past and a later envelope has no business moving it.

INSERT_EVENT = """
INSERT INTO events (id, install_id, ts, received_at, name, props)
VALUES ($1, $2, $3, $4, $5, $6{jsonb})
ON CONFLICT DO NOTHING
"""

UPSERT_INSTALL_DAY = """
INSERT INTO install_days (install_id, day, events, errors, version)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (install_id, day) DO UPDATE SET
    events  = install_days.events + excluded.events,
    errors  = install_days.errors + excluded.errors,
    version = excluded.version
"""

UPSERT_METRIC = """
INSERT INTO daily_metrics (day, metric, dim, value)
VALUES ($1, $2, $3, $4)
ON CONFLICT (day, metric, dim) DO UPDATE SET value = excluded.value
"""


def _to_qmark(sql: str, args: tuple) -> tuple[str, list]:
    """Rewrite ``$n`` placeholders as ``?`` and re-order args to match.

    Re-ordering by order of appearance is what makes a repeated ``$n`` legal in
    the shared statements above.
    """
    ordered: list = []

    def swap(match: re.Match) -> str:
        ordered.append(args[int(match.group(1)) - 1])
        return "?"

    return _PLACEHOLDER.sub(swap, sql), ordered


class Store:
    """What ingest and rollup are allowed to ask of a database.

    Two SQL fragments differ between the dialects and cannot be parameterised —
    casting a timestamp to a day, and reading one key out of a JSON column.
    Subclasses expose them as :attr:`DAY` / :meth:`json_get` and
    :mod:`telemetry_server.rollup` formats them into its aggregates. Every value
    that reaches those aggregates is still a bound parameter; what is
    interpolated is a constant chosen in this file and a key name chosen in
    ``rollup.py``, never anything an envelope carried.
    """

    #: SQL expression turning ``events.received_at`` into a day.
    DAY = "received_at::date"

    def json_get(self, column: str, key: str) -> str:
        """SQL reading one top-level key of a JSON column as text."""
        return f"{column}->>'{key}'"

    def json_num(self, column: str, key: str) -> str:
        """SQL reading one top-level key of a JSON column as a number."""
        return f"({column}->>'{key}')::numeric"

    async def migrate(self) -> None:
        raise NotImplementedError

    async def ping(self) -> bool:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def record(
        self, install: dict, events: list[dict], dropped: int
    ) -> tuple[int, int]:
        """Persist one validated envelope in a single transaction.

        Returns ``(stored, duplicates)``. ``install_days`` is incremented from
        *newly inserted* rows only, so the client's retry-after-timeout path
        cannot inflate a metric even though it legitimately re-sends the batch.
        """
        raise NotImplementedError

    async def fetch(self, sql: str, *args) -> list[tuple]:
        raise NotImplementedError

    async def execute(self, sql: str, *args) -> None:
        raise NotImplementedError

    async def put_metric(self, day: date, metric: str, dim: str, value: float) -> None:
        await self.execute(UPSERT_METRIC, day, metric, dim, float(value))


def _day_buckets(events: list[dict]) -> dict:
    """Group inserted events into ``(day) -> (count, errors)``."""
    buckets: dict[date, list[int]] = {}
    for event in events:
        day = event["ts"].astimezone(timezone.utc).date()
        slot = buckets.setdefault(day, [0, 0])
        slot[0] += 1
        if event["name"] in ("error", "upstream_error"):
            slot[1] += 1
    return buckets


class SqliteStore(Store):
    """The test and small-deployment backend.

    ``sqlite3`` is synchronous, so every call holds a :class:`threading.Lock`
    for the microseconds it takes — a thread lock rather than an asyncio one
    precisely because there is no ``await`` inside the critical section, and
    because an :class:`asyncio.Lock` binds itself to the first event loop that
    touches it, which a store built in one loop and served from another would
    trip over immediately.
    """

    DAY = "substr(received_at, 1, 10)"

    def json_get(self, column: str, key: str) -> str:
        return f"json_extract({column}, '$.{key}')"

    def json_num(self, column: str, key: str) -> str:
        # json_extract already yields SQLite's own numeric affinity for a JSON
        # number, so unlike Postgres there is nothing to cast.
        return f"json_extract({column}, '$.{key}')"

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def _run(self, sql: str, args: tuple) -> sqlite3.Cursor:
        # Only the shared INSERT carries the cast hole; leave every other
        # statement's text alone so a stray brace in a rollup aggregate can
        # never be mistaken for a format field.
        if "{jsonb}" in sql:
            sql = sql.format(jsonb="")
        statement, ordered = _to_qmark(sql, _as_text_dates(args))
        return self._conn.execute(statement, ordered)

    async def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                (MIGRATIONS / "001_init.sqlite.sql").read_text(encoding="utf-8")
            )
            self._conn.commit()

    async def ping(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()
        return True

    async def close(self) -> None:
        with self._lock:
            self._conn.close()

    async def record(
        self, install: dict, events: list[dict], dropped: int
    ) -> tuple[int, int]:
        with self._lock:
            try:
                self._run(UPSERT_INSTALL, _install_args(install, dropped, json.dumps))
                inserted: list[dict] = []
                for event in events:
                    cursor = self._run(
                        INSERT_EVENT, _event_args(event, str, json.dumps)
                    )
                    if cursor.rowcount:
                        inserted.append(event)
                for day, (count, errors) in _day_buckets(inserted).items():
                    self._run(
                        UPSERT_INSTALL_DAY,
                        (
                            install["install_id"],
                            day.isoformat(),
                            count,
                            errors,
                            install["version"],
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(inserted), len(events) - len(inserted)

    async def fetch(self, sql: str, *args) -> list[tuple]:
        with self._lock:
            return list(self._run(sql, args).fetchall())

    async def execute(self, sql: str, *args) -> None:
        with self._lock:
            self._run(sql, args)
            self._conn.commit()


def _as_text_dates(args: tuple) -> tuple:
    """SQLite has no date or timestamp type, and its implicit datetime adapter is
    deprecated. Dates and timestamps go in as ISO-8601 text, matching the DDL and
    sorting correctly."""
    return tuple(a.isoformat() if isinstance(a, (date, datetime)) else a for a in args)


class PostgresStore(Store):
    """The production backend. ``asyncpg`` is imported here and nowhere else."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    async def connect(self) -> None:
        import asyncpg  # noqa: PLC0415 - behind the telemetry-server extra

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=8)

    async def migrate(self) -> None:
        sql = (MIGRATIONS / "001_init.sql").read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def ping(self) -> bool:
        async with self._pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def record(
        self, install: dict, events: list[dict], dropped: int
    ) -> tuple[int, int]:
        import uuid as _uuid

        statement = INSERT_EVENT.format(jsonb="::jsonb")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    UPSERT_INSTALL, *_install_args(install, dropped, list)
                )
                inserted = []
                for event in events:
                    status = await conn.execute(
                        statement, *_event_args(event, _uuid.UUID, json.dumps)
                    )
                    # asyncpg returns "INSERT 0 1", or "INSERT 0 0" when the
                    # ON CONFLICT swallowed a retry.
                    if status.rsplit(" ", 1)[-1] != "0":
                        inserted.append(event)
                for day, (count, errors) in _day_buckets(inserted).items():
                    await conn.execute(
                        UPSERT_INSTALL_DAY,
                        _uuid.UUID(install["install_id"]),
                        day,
                        count,
                        errors,
                        install["version"],
                    )
        return len(inserted), len(events) - len(inserted)

    async def fetch(self, sql: str, *args) -> list[tuple]:
        async with self._pool.acquire() as conn:
            return [tuple(r) for r in await conn.fetch(sql, *args)]

    async def execute(self, sql: str, *args) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)


def _install_args(install: dict, dropped: int, providers) -> tuple:
    return (
        install["install_id"],
        install["received_at"],
        install["level"],
        install["version"],
        install["branch"],
        install["os"],
        install["arch"],
        install["python"],
        install["in_docker"],
        install["has_hb_api"],
        install["has_gateway"],
        install["has_web"],
        install["user_count"],
        install["server_count"],
        install["agent_count"],
        providers(install["llm_providers"]),
        dropped,
    )


def _event_args(event: dict, ident, dump) -> tuple:
    return (
        ident(event["id"]),
        ident(event["install_id"]),
        event["ts"],
        event["received_at"],
        event["name"],
        dump(event["props"]),
    )


async def open_store(dsn: str) -> Store:
    """Build the backend the DSN asks for, migrated and ready.

    Anything that is not a Postgres URL is a SQLite path, which is how the tests
    and a single-box trial run get a collector without installing a daemon.
    """
    if dsn.startswith(("postgres://", "postgresql://")):
        store: Store = PostgresStore(dsn)
        await store.connect()
    else:
        store = SqliteStore(dsn.removeprefix("sqlite:///").removeprefix("sqlite://"))
    await store.migrate()
    return store
