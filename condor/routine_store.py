"""Shared in-memory store for routine instances and results.

Bridges Telegram handler and web API so both can see
the same instances, schedule runs, and read results.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import time
import traceback
from pathlib import Path
from typing import Any

import condor.reports as reports
from condor import primitives, routine_hooks
from condor.telemetry import taps as telemetry_taps
from routines.base import (
    RoutineResult,
    discover_routines,
    discover_routines_from_path,
    get_routine,
    normalize_result,
)

logger = logging.getLogger(__name__)


class _HttpBot:
    """Fallback bot that sends Telegram messages via HTTP when no real bot is available."""

    def __init__(self):
        import os

        self._token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get(
            "TELEGRAM_TOKEN", ""
        )

    async def _post(self, method: str, data: dict, files: dict | None = None):
        if not self._token:
            return None
        import httpx

        url = f"https://api.telegram.org/bot{self._token}/{method}"
        async with httpx.AsyncClient(timeout=30) as client:
            if files:
                resp = await client.post(url, data=data, files=files)
            else:
                resp = await client.post(url, json=data)
            result = resp.json()
            if not result.get("ok"):
                logger.warning(f"Telegram {method} failed: {resp.text}")
            return result

    async def send_message(self, *a, **kw):
        chat_id = kw.get("chat_id") or (a[0] if a else None)
        text = kw.get("text") or (a[1] if len(a) > 1 else "")
        data = {"chat_id": chat_id, "text": text}
        if kw.get("parse_mode"):
            data["parse_mode"] = kw["parse_mode"]
        return await self._post("sendMessage", data)

    async def send_photo(self, *a, **kw):
        chat_id = kw.get("chat_id") or (a[0] if a else None)
        photo = kw.get("photo") or (a[1] if len(a) > 1 else None)
        data = {"chat_id": chat_id}
        if kw.get("caption"):
            data["caption"] = kw["caption"]
        files = {"photo": ("chart.png", photo, "image/png")} if photo else None
        return await self._post("sendPhoto", data, files=files)

    async def send_document(self, *a, **kw):
        import mimetypes

        chat_id = kw.get("chat_id") or (a[0] if a else None)
        document = kw.get("document") or (a[1] if len(a) > 1 else None)
        data = {"chat_id": chat_id}
        if kw.get("caption"):
            data["caption"] = kw["caption"]
        files = None
        if document is not None:
            # Honor the buffer's name (e.g. "Daily_PnL.html") and the explicit
            # `filename` kwarg so the file arrives with a real name + extension
            # instead of a generic, extension-less "file".
            filename = kw.get("filename") or getattr(document, "name", None) or "file"
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            files = {"document": (filename, document, mime)}
        return await self._post("sendDocument", data, files=files)

    async def edit_message_text(self, *a, **kw):
        data = {k: v for k, v in kw.items() if v is not None}
        return await self._post("editMessageText", data)

    def has_token(self) -> bool:
        """Can this actually deliver? Without a token every send is a no-op.

        Read by ``delegate.resolve_bot`` so the ladder can fall through to the
        dashboard bell rather than to a sender that silently drops the message.
        """
        return bool(self._token)

    async def get_chat_member(self, *a, **kw):
        """Raw ``getChatMember`` envelope — the web routes' chat-ownership check
        (SEC-198) reads ``result.status`` out of it when no live bot is around."""
        chat_id = kw.get("chat_id") or (a[0] if a else None)
        user_id = kw.get("user_id") or (a[1] if len(a) > 1 else None)
        return await self._post(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}
        )


_http_bot = _HttpBot()


def _agent_of(routine) -> str:
    """Which assistant produced this routine's reports.

    A routine's ``source`` is ``"agent:<slug>"`` for an agent/expert-local routine
    or ``"global"`` for the shared/condor library — so reports are attributed to
    the owning expert, else to the chat ``condor``.
    """
    src = getattr(routine, "source", "global") or "global"
    return src.split(":", 1)[1] if src.startswith("agent:") else "condor"


def _run_outcome_text(routine_name: str, summary: str, error: str | None) -> str:
    """The one-line outcome of a finished run, for the conversation transcript.

    Built from the same two values the instance record keeps — its ``error`` and
    its already-clipped ``last_result`` — so the note a conversation reads and
    the record the dashboard shows can never disagree about the same run.
    """
    if error:
        return f"❌ Routine {routine_name} failed: {error}"
    return f"✅ Routine {routine_name} done\n\n{(summary or '').strip()}".rstrip()


class WebRoutineContext:
    """Lightweight context so routines can run without Telegram."""

    def __init__(self, server_name: str, bot=None, chat_id: int = 0):
        from condor.preferences import SERVER_PIN_KEY, USER_PREFERENCES_KEY

        self._chat_id = chat_id
        # The server this run was launched against, readable by name — for a routine
        # that must *record* it (backtest_chart files its results under it).
        self.server_name = server_name
        self.bot = bot if bot is not None else _http_bot
        # And the same server as an active-server preference, which is how
        # get_client(chat_id, context=...) resolves it. This dict was keyed
        # "preferences" while the preference API reads USER_PREFERENCES_KEY
        # ("user_preferences"), so the server a web or agent run was launched with
        # never reached its client — every such run silently picked the chat default.
        # Pinned, not preferred: this run was launched against `server_name`, so
        # the chat's own default must not override it — the chat may well be a
        # chat whose default is a different server (see `get_effective_server`).
        self._user_data: dict[str, Any] = {
            USER_PREFERENCES_KEY: {"general": {"active_server": server_name}},
            SERVER_PIN_KEY: True,
        }

    @property
    def user_data(self) -> dict:
        return self._user_data


# How many finished-run results stay resident (CORR-142). Results must outlive
# their run — the dashboard renders the detail panel after the run ends and only
# fetches the chart PNG when the user opens that run, and an MCP agent that
# submitted with `run_async` comes back for it via `get_instance` an unbounded
# time later. So this is deliberately generous: it is a memory backstop, not a
# cache TTL. What it prevents is the unbounded case — every `execute()` mints a
# fresh instance_id, and a RoutineResult can hold a raw PNG in `chart_image`, so
# an agent running a chart routine on a tick would otherwise pin megabytes a day
# in a process designed to run for weeks.
_MAX_RESULTS = 200

# CORR-163: the same bound, applied to the instance records themselves.
# `_results` was the expensive half of the leak (a RoutineResult can pin a raw
# PNG) but the `_instances` entry that owns it leaked in exactly the same shape:
# every `execute()` mints a fresh id, and only the explicit `remove_instance`
# and `stop` paths ever dropped one, so a completed one-shot was never reaped.
#
# Retention, not deletion-on-completion: a finished one-shot is precisely what
# the dashboard's detail panel renders after the run ends and what an MCP agent
# reads back through `get_instance` after a `run_async`, so the record has to
# outlive its own run. Only *terminal* instances count against the cap —
# anything in flight, continuous or scheduled is exempt from the count and from
# eviction, so a scheduled routine can never be reaped out from under its own
# task no matter how many one-shots run alongside it.
#
# Deliberately the same number as `_MAX_RESULTS`: an instance older than the
# result cap has already lost its result and is a hollow record, and sharing the
# bound is what keeps the two dicts from drifting apart.
_MAX_TERMINAL_INSTANCES = _MAX_RESULTS

# Statuses a run cannot come back from. `running` (one-shot in flight, or a
# Telegram-owned instance, which keeps that status for its whole life) and
# `scheduled` (between ticks of an interval schedule) are the live ones.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped"})


class RoutineStore:
    """Singleton store for routine instances and results."""

    def __init__(self) -> None:
        self._instances: dict[str, dict] = {}
        self._results: dict[str, RoutineResult] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._bot = None  # Telegram bot instance, set via set_bot()

    def set_bot(self, bot) -> None:
        """Inject the Telegram bot so web-triggered routines can send messages."""
        self._bot = bot

    def get_bot(self):
        """Return the registered Telegram bot, or None if not set yet."""
        return self._bot

    # ── Discovery ──

    def _discover_all(self) -> dict[str, "RoutineInfo"]:
        """Discover routines: the general library (root ``routines/``) + each agent's.

        The chat ``condor`` sees the general library plus every agent's routines
        (prefixed). Each agent's own routines stay isolated under its slug.

        Discovery is mtime-cached in routines.base: only new/changed files are
        (re)imported, so this stays cheap on every list call while edits are
        still picked up without a restart.
        """
        all_routines = dict(discover_routines())

        # Scan agents/*/routines/
        agents_dir = Path(__file__).resolve().parent.parent / "agents"
        if agents_dir.exists():
            for agent_dir in sorted(agents_dir.iterdir()):
                # `_`-prefixed dirs are not agents (`_shared`, `_defaults`) —
                # same rule as AgentStore._iter_agent_dirs, so a library dir can
                # never surface here as a routine owner named `_shared/...`.
                if agent_dir.name.startswith("_"):
                    continue
                routines_path = agent_dir / "routines"
                if not routines_path.is_dir():
                    continue
                slug = agent_dir.name
                agent_routines = discover_routines_from_path(
                    routines_path, agent_slug=slug
                )
                for rname, rinfo in agent_routines.items():
                    # Shallow-copy before prefixing: the RoutineInfo is shared
                    # with the discovery cache and must keep its bare name.
                    prefixed_info = copy.copy(rinfo)
                    prefixed_info.name = f"{slug}/{rname}"
                    all_routines[prefixed_info.name] = prefixed_info

        return all_routines

    def _get_report_counts(self) -> dict[str, int]:
        """Get report count per routine source_name."""
        try:
            from condor.reports import list_reports

            reports, _ = list_reports(limit=1000)
            counts: dict[str, int] = {}
            for r in reports:
                sn = r.get("source_name", "")
                if sn:
                    counts[sn] = counts.get(sn, 0) + 1
            return counts
        except Exception:
            return {}

    def list_routines(self) -> list[dict]:
        all_routines = self._discover_all()
        report_counts = self._get_report_counts()
        out = []
        for name, info in all_routines.items():
            out.append(
                {
                    "name": name,
                    "description": info.description,
                    "is_continuous": info.is_continuous,
                    "category": info.category,
                    "source": info.source,
                    "fields": info.get_fields(),
                    "last_modified": info.last_modified,
                    "report_count": report_counts.get(name, 0)
                    or report_counts.get(name.split("/")[-1], 0),
                }
            )
        return out

    # ── Instances ──

    def list_instances(self) -> list[dict]:
        out = []
        for iid, meta in self._instances.items():
            entry = {"instance_id": iid, **meta}
            if iid in self._results:
                entry["has_result"] = True
            out.append(entry)
        return out

    def get_instance(self, instance_id: str) -> dict | None:
        meta = self._instances.get(instance_id)
        if not meta:
            return None
        entry = {"instance_id": instance_id, **meta}
        result = self._results.get(instance_id)
        if result:
            entry["has_result"] = True
            entry["result_text"] = result.text[:2000]
            entry["has_chart"] = result.chart_image is not None
            entry["table_data"] = result.table_data
            entry["table_columns"] = result.table_columns
            entry["sections"] = result.sections
        # Ensure error is always present in response
        entry.setdefault("error", None)
        return entry

    def add_instance(self, instance_id: str, metadata: dict) -> None:
        self._instances[instance_id] = metadata
        self._prune_instances()

    def remove_instance(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)
        self._tasks.pop(instance_id, None)
        # The result dies with its instance. Every read path resolves the
        # instance first (the dashboard detail and chart endpoints via
        # _authorized_instance, the MCP tool via get_instance), so a result left
        # behind here is unreachable memory rather than a cache.
        self._results.pop(instance_id, None)

    def _has_live_task(self, instance_id: str) -> bool:
        """True while the instance's own task is still on the loop."""
        task = self._tasks.get(instance_id)
        return task is not None and not task.done()

    def _prune_instances(self) -> None:
        """Evict the oldest terminal instances past ``_MAX_TERMINAL_INSTANCES``.

        The backstop for the unbounded case: every ``execute()`` mints a fresh
        instance id and a one-shot that completes normally is removed by nobody,
        so without this the dict is monotonic for the life of the process.

        Only instances in a terminal status are candidates, and only once their
        task is off the loop — a run still in flight, a continuous routine and a
        scheduled one between ticks are all exempt from eviction *and* from the
        count, so no amount of one-shot churn can reap a live instance or push a
        live one over the edge. The just-finished run is excluded on its own
        prune for the same reason (its task is still executing this call); it is
        the youngest candidate anyway, so it is never the one to go.

        Oldest-first by last run, falling back to creation for an instance that
        never ran. Eviction goes through ``remove_instance`` so a dropped
        instance takes its result and its task entry with it, preserving
        CORR-142's ``set(_results) <= set(_instances)``.
        """
        terminal = [
            (meta.get("last_run_at") or meta.get("created_at") or 0.0, iid)
            for iid, meta in self._instances.items()
            if meta.get("status") in _TERMINAL_STATUSES and not self._has_live_task(iid)
        ]
        excess = len(terminal) - _MAX_TERMINAL_INSTANCES
        if excess <= 0:
            return
        terminal.sort()
        for _, iid in terminal[:excess]:
            self.remove_instance(iid)

    # ── Results ──

    def store_result(self, instance_id: str, result: RoutineResult) -> None:
        """Record a run's result, keeping ``_results`` bounded to _MAX_RESULTS.

        Popping before inserting refreshes recency (dicts keep insertion order),
        so a continuous instance that stores a result every tick stays at the
        young end and the entries evicted are the oldest untouched ones.

        Not gated on the instance existing: the Telegram path calls this from
        ``_execute_routine`` *before* it registers the instance via
        ``_sync_instance_to_store``, so a gate here would drop every
        Telegram-triggered result on its first run.
        """
        self._results.pop(instance_id, None)
        self._results[instance_id] = result
        while len(self._results) > _MAX_RESULTS:
            self._results.pop(next(iter(self._results)))

    def get_result(self, instance_id: str) -> RoutineResult | None:
        return self._results.get(instance_id)

    # ── Execution ──

    def _gen_id(self) -> str:
        return hashlib.md5(f"{time.time()}{id(object())}".encode()).hexdigest()[:8]

    async def _fire_hooks(
        self, instance_id: str, result, report_id: str | None, failed: bool
    ) -> None:
        """Dispatch post-execution hooks for a stored instance. Never raises."""
        meta = self._instances.get(instance_id)
        routine_name = meta.get("routine_name") if meta else None
        if not routine_name:
            return
        # Built outside the try on purpose (CORR-175). Binding the arguments
        # happens here, at coroutine creation; nothing inside ``dispatch`` runs
        # until it is awaited. So a call that no longer matches the signature
        # raises TypeError *here* and surfaces, while every failure the dispatch
        # itself hits stays swallowed below. The boundary is when the error
        # happens, not its type: a TypeError raised while delivering is a
        # runtime failure and must not break the run either.
        coro = routine_hooks.dispatch(
            routine_name,
            result,
            report_id,
            failed=failed,
            bot=(self._bot or _http_bot),
            # Only the hooks of whoever started this run fire (SEC-152).
            owner_id=meta.get("user_id"),
        )
        try:
            await coro
        except Exception as e:
            logger.error(
                f"Post-execution hooks failed for {routine_name}[{instance_id}]: {e}"
            )

    async def _report_run(
        self, instance_id: str, summary: str, error: str | None
    ) -> None:
        """Report a finished run to the conversation that asked for it.

        One note, delivered twice. The ``system`` turn is the *record*: the
        routine sibling of ``delegate._record_completion_turn``, without which
        the conversation ended on "I started it" and ``replay_context`` told the
        next session the same incomplete story (ARCH-089). Recorded as a
        ``system`` turn so the replay reads it as a parenthetical note rather
        than as the agent's own words.

        The push is what the user *sees*. A recorded turn reaches an already-open
        dashboard only when the page is reloaded, so a run that failed thirty
        seconds in stayed invisible until someone refreshed. ``deliver_note``
        shows the same line immediately and costs nothing: a finished routine is
        worth showing, not worth a model turn to announce it.

        A run with no conversation behind it — the scheduler, the dashboard, the
        Telegram menu, an instance restored on boot — still lights the owner's
        bell (FEAT-048) and is otherwise a no-op: ``record_system`` ignores an
        empty id and a run with no session key reaches no surface. No delivery
        is allowed to raise: a missing note must not cost the user the run's own
        result or its hooks.
        """
        meta = self._instances.get(instance_id) or {}
        text = _run_outcome_text(meta.get("routine_name") or "", summary, error)

        # The bell is addressed to the *owner*, not to a conversation, so it is
        # the one surface a scheduled or dashboard-started run can reach.
        try:
            from condor.notifications import record

            await record(
                meta.get("user_id"),
                text,
                kind="routine",
                link="/routines?tab=reports",
            )
        except Exception:
            logger.debug(
                f"Could not notify owner about run {instance_id}", exc_info=True
            )

        conversation_id = meta.get("conversation_id") or ""
        if not conversation_id:
            return

        try:
            from condor.runtime.conversations import record_system

            record_system(meta.get("user_id"), conversation_id, text, kind="routine")
        except Exception:
            logger.debug(
                f"Could not record run {instance_id} in conversation {conversation_id}",
                exc_info=True,
            )

        session_key = meta.get("session_key") or ""
        if not session_key:
            return
        try:
            from condor.runtime import wake

            await wake.deliver_note(
                session_key=session_key,
                conversation_id=conversation_id,
                text=text,
                kind="routine",
            )
        except Exception:
            logger.debug(
                f"Could not show run {instance_id} in conversation {conversation_id}",
                exc_info=True,
            )

    def _new_instance_meta(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        user_id: int,
        source: str,
        conversation_id: str = "",
        session_key: str = "",
        **extra,
    ) -> dict:
        """Fresh instance-metadata dict shared by execute/start_continuous/schedule.

        ``conversation_id`` is the run's provenance: the conversation that asked
        for it, which its outcome is reported back to. ``session_key`` is the
        live session behind that conversation, which the outcome is *shown* in
        while it is still open. Both empty for everything with no conversation
        behind it (dashboard, Telegram, restored schedules).
        """
        return {
            "routine_name": routine_name,
            "config": config,
            "status": "running",
            "source": source,
            "server_name": server_name,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "session_key": session_key,
            "created_at": time.time(),
            "last_run_at": None,
            "last_result": None,
            "last_duration": None,
            "report_id": None,
            "run_count": 0,
            **extra,
        }

    async def _execute_and_record(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
        user_id: int = 0,
        *,
        status_after: str,
        failed_status: str | None = None,
        fire_hooks: bool = True,
        agent: str = "",
        trigger: str = "other",
    ) -> None:
        """Run a routine once, store the result, update instance metadata, fire hooks.

        Shared by one-shot, continuous and scheduled runs — the entry points
        only differ in loop/cancellation semantics. ``status_after`` is the
        instance status recorded after the run; ``failed_status`` (defaults to
        ``status_after``) is used when the run raises. ``CancelledError`` is
        recorded as a clean "Stopped by user" run so a stopped continuous
        routine still stores its final result.

        ``agent`` stamps the run's reports with an explicit producer. Left empty
        (web, Telegram, schedules) it is derived from the routine's own source
        via ``_agent_of``; the MCP runner passes it because it knows which
        assistant asked — which differs from the source when an agent runs a
        routine from the general library.
        """
        ctx = WebRoutineContext(server_name, bot=self._bot, chat_id=user_id)
        start = time.time()
        error_msg = None
        failed = False
        # The bare name (not "agent_slug/name") is what both report lookups
        # match on, and what routines that do call .source() already use.
        base_name = (routine.name or "").split("/")[-1]
        try:
            # run_scope is the one place the four attribution calls live
            # (ARCH-217): it resets the last report id and records who this run
            # executes for (SEC-196), which assistant asked, and the source that
            # keeps its reports findable. The context is published so a nested
            # call_routine() inside this routine inherits its server, bot and
            # user (FEAT-052).
            with (
                reports.run_scope(
                    owner=user_id,
                    agent=agent or _agent_of(routine),
                    source_type="routine",
                    source_name=base_name,
                ),
                primitives.bind_context(ctx),
            ):
                cfg = routine.config_class(**config)
                raw = await routine.run_fn(cfg, ctx)
            result = normalize_result(raw)
        except asyncio.CancelledError:
            result = RoutineResult(text="Stopped by user")
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(
                f"Routine {routine.name}[{instance_id}] failed: {type(e).__name__}: {e}\n{tb}"
            )
            error_msg = f"{type(e).__name__}: {e}"
            result = RoutineResult(text=f"Error: {error_msg}\n\n{tb}")
            failed = True

        duration = time.time() - start
        report_id = reports.get_last_report_id()
        # Clipped once: the instance record and the conversation note are the
        # same summary, so they cannot tell the user different stories.
        summary = result.text[:500]

        # Both writes are gated on the instance still being there. A cancel that
        # lands mid-run is recorded here *after* stop() already removed the
        # instance, and storing the result then would resurrect an entry no read
        # path can reach — unreachable and, without the gate, immortal.
        if instance_id in self._instances:
            self.store_result(instance_id, result)
            self._instances[instance_id].update(
                {
                    "status": (
                        (failed_status or status_after) if failed else status_after
                    ),
                    "last_run_at": time.time(),
                    "last_result": summary,
                    "last_duration": duration,
                    # The run's report is what a reader wants to open; the text
                    # is only what the caller (or the LLM) was handed back. A
                    # run that rendered nothing keeps the previous run's report
                    # out of the record.
                    "report_id": report_id,
                    "run_count": self._instances[instance_id].get("run_count", 0) + 1,
                    "error": error_msg,
                }
            )
            # The run just reached its final status: this is the moment a new
            # terminal instance appears, so it is the moment to enforce the cap.
            self._prune_instances()

        # Usage telemetry (FEAT-023): whether it ran, how it was triggered and
        # how long it took. The routine's own name only when the file ships in
        # this repo — a user's private routine is called `custom` — and never
        # its config, its output or its error text.
        telemetry_taps.routine_run(
            routine=telemetry_taps.shipped_routine_name(
                (routine.name or "").split("/")[-1]
            ),
            kind="continuous" if getattr(routine, "continuous", False) else "oneshot",
            trigger=trigger,
            duration_ms=int(duration * 1000),
            ok=not failed,
        )

        await self._report_run(instance_id, summary, error_msg)

        if fire_hooks:
            await self._fire_hooks(instance_id, result, report_id, failed)

    def _resolve_routine(self, routine_name: str):
        """Resolve a routine by name, supporting 'agent_slug/routine_name' format."""
        # Try global first
        routine = get_routine(routine_name)
        if routine:
            return routine
        # Try agent format: slug/name
        if "/" in routine_name:
            slug, rname = routine_name.split("/", 1)
            agents_dir = (
                Path(__file__).resolve().parent.parent / "agents" / slug / "routines"
            )
            agent_routines = discover_routines_from_path(agents_dir, agent_slug=slug)
            return agent_routines.get(rname)
        return None

    async def execute(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        user_id: int = 0,
        agent: str = "",
        conversation_id: str = "",
        session_key: str = "",
    ) -> str:
        """Run a one-shot routine from the web. Returns instance_id.

        ``agent`` overrides report attribution (see ``_execute_and_record``);
        ``conversation_id`` and ``session_key`` are where the finished run
        reports back to and shows itself (see ``_report_run``).
        """
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")

        instance_id = self._gen_id()
        self._instances[instance_id] = self._new_instance_meta(
            routine_name,
            config,
            server_name,
            user_id,
            source="web",
            conversation_id=conversation_id,
            session_key=session_key,
        )

        task = asyncio.create_task(
            self._run_oneshot(
                instance_id, routine, config, server_name, user_id, agent=agent
            )
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_oneshot(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
        user_id: int = 0,
        agent: str = "",
        trigger: str = "manual",
    ) -> None:
        await self._execute_and_record(
            instance_id,
            routine,
            config,
            server_name,
            user_id,
            status_after="completed",
            failed_status="failed",
            agent=agent,
            trigger=trigger,
        )

    async def start_continuous(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        user_id: int = 0,
        agent: str = "",
        conversation_id: str = "",
        session_key: str = "",
    ) -> str:
        """Start a continuous routine as a background task. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")
        if not routine.is_continuous:
            raise ValueError(
                f"Routine '{routine_name}' is not continuous — use execute() instead"
            )

        instance_id = self._gen_id()
        self._instances[instance_id] = self._new_instance_meta(
            routine_name,
            config,
            server_name,
            user_id,
            source="mcp",
            conversation_id=conversation_id,
            session_key=session_key,
        )

        task = asyncio.create_task(
            self._run_continuous(
                instance_id, routine, config, server_name, user_id, agent=agent
            )
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_continuous(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
        user_id: int = 0,
        agent: str = "",
    ) -> None:
        # Continuous routines message the user from inside their own loop and
        # only end on stop/error, so per-run completion hooks are intentionally
        # not fired for them (they would only trigger once, at shutdown).
        await self._execute_and_record(
            instance_id,
            routine,
            config,
            server_name,
            user_id,
            status_after="stopped",
            fire_hooks=False,
            agent=agent,
            trigger="manual",
        )

    async def schedule(
        self,
        routine_name: str,
        config: dict,
        server_name: str,
        interval_sec: int,
        user_id: int = 0,
    ) -> str:
        """Schedule a routine to repeat at interval_sec. Returns instance_id."""
        routine = self._resolve_routine(routine_name)
        if not routine:
            raise ValueError(f"Routine '{routine_name}' not found")

        instance_id = self._gen_id()
        self._instances[instance_id] = self._new_instance_meta(
            routine_name,
            config,
            server_name,
            user_id,
            source="web",
            status="scheduled",
            schedule={"type": "interval", "interval_sec": interval_sec},
        )

        task = asyncio.create_task(
            self._run_scheduled(
                instance_id, routine, config, server_name, interval_sec, user_id
            )
        )
        self._tasks[instance_id] = task
        return instance_id

    async def _run_scheduled(
        self,
        instance_id: str,
        routine,
        config: dict,
        server_name: str,
        interval_sec: int,
        user_id: int = 0,
    ) -> None:
        try:
            while instance_id in self._instances:
                await self._execute_and_record(
                    instance_id,
                    routine,
                    config,
                    server_name,
                    user_id,
                    status_after="scheduled",
                    trigger="schedule",
                )
                # A cancel that lands mid-run is swallowed (and recorded) by
                # _execute_and_record; stop() removed the instance, so bail out
                # instead of sleeping through one more interval.
                if instance_id not in self._instances:
                    break
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info(f"Scheduled routine {instance_id} cancelled")

    def stop(self, instance_id: str) -> bool:
        task = self._tasks.pop(instance_id, None)
        if task and not task.done():
            task.cancel()

        if instance_id in self._instances:
            self._instances[instance_id]["status"] = "stopped"
            del self._instances[instance_id]
            # Same reasoning as remove_instance: stop() drops the instance
            # record entirely, so its result is already unreachable.
            self._results.pop(instance_id, None)
            return True
        return False


# Singleton
_store: RoutineStore | None = None


def get_routine_store() -> RoutineStore:
    global _store
    if _store is None:
        _store = RoutineStore()
    return _store
