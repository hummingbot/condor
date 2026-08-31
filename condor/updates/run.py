"""One update run: its steps, its journal, and who is watching it.

A Condor update ends one step short of running the new code. It used to
``exec`` the process at the end, and that is the one part of an update Condor
cannot do safely: it is almost never the top of its own process tree. Started
through ``make run``, a shell wrapper or a supervisor, re-execing races the
parent into bringing a *second* Condor up on the same port, against the same
config and the same Telegram token. So the run stops at the last safe point —
code on disk, dependencies synced, dashboard rebuilt — records that a relaunch
is owed (:func:`relaunch_pending`), and lets a human do the one thing a human
is better placed to do. The surfaces turn that record into a banner.

The durable record is still the contract: every step transition is one
``atomic_write_json`` to ``data/update_run.json``, and both surfaces read that
file. Telegram, being in-process, additionally registers an observer for
zero-latency message edits — the same "core emits, a surface registers itself"
shape as :func:`condor.notifications.register_push_sink`. The dashboard polls
the journal and needs nothing else.

One run at a time, process-wide. A second :func:`start` returns the run already
in flight rather than queueing it, which is what lets an admin start the update
on Telegram and watch it finish in the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from condor import paths
from condor.fsutil import atomic_write_json
from condor.updates import components
from utils import updater

log = logging.getLogger(__name__)

# Run states. ``RESTARTING`` is legacy: it is what a version of Condor that
# exec'd itself left in the journal, and boot still judges such a run
# (:func:`finalize_pending_run`). Nothing writes it any more.
RUNNING = "running"
RESTARTING = "restarting"
SUCCEEDED = "succeeded"
FAILED = "failed"

# Step states.
PENDING = "pending"
OK = "ok"
SKIPPED = "skipped"

# Command output kept per step. Enough to diagnose, far under Telegram's limit.
OUTPUT_TAIL_CHARS = 2000


def tail(output: str, max_lines: int = 15, max_chars: int = OUTPUT_TAIL_CHARS) -> str:
    """Last few lines of command output — build logs run to thousands."""
    text = (output or "").strip() or "(no output)"
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = ["..."] + lines[-max_lines:]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = "..." + text[-max_chars:]
    return text


# ── Wire shapes ──


@dataclass
class Step:
    """One command's worth of the plan, and how it went."""

    key: str
    label: str
    state: str = PENDING
    started: float | None = None
    ended: float | None = None
    output_tail: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "started": self.started,
            "ended": self.ended,
            "output_tail": self.output_tail,
        }

    @classmethod
    def from_wire(cls, raw: dict) -> "Step":
        return cls(
            key=str(raw.get("key") or ""),
            label=str(raw.get("label") or ""),
            state=str(raw.get("state") or PENDING),
            started=raw.get("started"),
            ended=raw.get("ended"),
            output_tail=str(raw.get("output_tail") or ""),
        )


@dataclass
class Run:
    """An update from the moment it was confirmed to the moment it was judged."""

    id: str
    started: float
    actor: dict[str, Any]
    components: list[str]
    steps: list[Step] = field(default_factory=list)
    state: str = RUNNING
    from_commit: str | None = None
    target_commit: str | None = None
    error: str | None = None
    ended: float | None = None
    # The admin pressed Done on a finished run. Journaled, because the panel is
    # a view over a durable file: dismissal held in component state came back on
    # every reload and every relaunch, so the last update anyone ever ran was
    # still on screen days later with no way to clear it.
    acknowledged: bool = False
    # Not journaled: how an in-process caller waits for a run it did not await.
    done: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False, compare=False
    )

    @property
    def live(self) -> bool:
        return self.state in (RUNNING, RESTARTING)

    def step(self, key: str) -> Step | None:
        for step in self.steps:
            if step.key == key:
                return step
        return None

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started": self.started,
            "actor": dict(self.actor),
            "components": list(self.components),
            "steps": [s.to_wire() for s in self.steps],
            "state": self.state,
            "from_commit": self.from_commit,
            "target_commit": self.target_commit,
            "error": self.error,
            "ended": self.ended,
            "acknowledged": self.acknowledged,
        }

    @classmethod
    def from_wire(cls, raw: dict) -> "Run":
        run = cls(
            id=str(raw.get("id") or ""),
            started=float(raw.get("started") or 0.0),
            actor=raw.get("actor") if isinstance(raw.get("actor"), dict) else {},
            components=[str(c) for c in (raw.get("components") or [])],
            steps=[
                Step.from_wire(s)
                for s in (raw.get("steps") or [])
                if isinstance(s, dict)
            ],
            state=str(raw.get("state") or RUNNING),
            from_commit=raw.get("from_commit"),
            target_commit=raw.get("target_commit"),
            error=raw.get("error"),
            ended=raw.get("ended"),
            acknowledged=bool(raw.get("acknowledged")),
        )
        if not run.live:
            run.done.set()
        return run


# ── The journal ──


def read_journal() -> Run | None:
    """The last run this install recorded, or None. Tolerant of junk."""
    path = paths.update_run_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a torn or hand-edited file is not fatal
        log.warning("Could not read %s; treating as no run", path)
        return None
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    try:
        return Run.from_wire(raw)
    except Exception:  # noqa: BLE001
        log.warning("Could not hydrate %s; treating as no run", path)
        return None


def _write_journal(run: Run) -> None:
    try:
        atomic_write_json(paths.update_run_path(), run.to_wire(), indent=2)
    except Exception:  # noqa: BLE001 - a failed write must not abort the update
        log.warning("Could not journal update run %s", run.id, exc_info=True)


# ── The observer bus ──

Observer = Callable[[Run], Awaitable[None]]

_observers: list[Observer] = []


def register_observer(fn: Observer) -> None:
    """Bind a live surface's renderer. Called by the surface."""
    if fn not in _observers:
        _observers.append(fn)


def unregister_observer(fn: Observer) -> None:
    if fn in _observers:
        _observers.remove(fn)


async def _emit(run: Run) -> None:
    """Journal the transition, then tell whoever is watching.

    The journal goes first and unconditionally: it is the record, and an
    observer that blew up must never cost the run its own history.
    """
    _write_journal(run)
    for observer in list(_observers):
        try:
            await observer(run)
        except Exception:  # noqa: BLE001 - a dead observer is not a dead run
            log.warning("Update observer failed", exc_info=True)


# ── The relaunch notice ──
#
# Deliberately in memory and nowhere else. The question it answers is "is this
# *process* older than the code on disk", and a process cannot be older than
# code that landed before it booted — so a fresh start is the only correct way
# to clear it, and that is exactly what module state does for free. Journaling
# it would survive the relaunch it asks for and the banner would never go away.

_relaunch: dict[str, Any] | None = None


def relaunch_pending() -> dict[str, Any] | None:
    """What this process is missing, or ``None`` if it is running the update.

    ``{"run_id", "from_commit", "target_commit", "branch", "at"}``. Read by both
    surfaces; the dashboard turns it into the banner every page carries.
    """
    return dict(_relaunch) if _relaunch else None


def _note_relaunch(run: Run, before: str, after: str, branch: str) -> None:
    global _relaunch
    _relaunch = {
        "run_id": run.id,
        "from_commit": before,
        "target_commit": after,
        "branch": branch,
        "at": time.time(),
    }
    log.info("Update %s landed %s → %s; relaunch owed", run.id, before[:7], after[:7])


# ── The live run ──

_current: Run | None = None
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def current() -> Run | None:
    """The run in flight, or the last one finished in this process."""
    return _current


async def resolve(component_key: str, action: str) -> tuple[bool, str]:
    """Act on a blocker's offered resolution, on exactly the paths it named.

    Recomputed rather than taken from the caller: the screen the admin pressed
    the button on may be minutes old, and discarding a path that has since
    stopped conflicting would destroy work nobody was warned about.
    """
    component = components.get(component_key)
    if component is None:
        return False, f"Unknown component: {component_key}"

    blocks = await components.repo_blocks(component)
    conflicting = sorted(
        {p for b in blocks for p in b.paths if action in b.resolutions}
    )
    if not conflicting:
        return True, "Nothing conflicts any more."

    if action == "discard":
        ok, message = await updater.discard_paths(component.repo_dir, conflicting)
    elif action == "stash":
        ok, message = await updater.stash_paths(component.repo_dir, conflicting)
    else:
        return False, f"Unknown resolution: {action}"

    if ok:
        components.invalidate()
    return ok, message


def _plan(component_keys: list[str], statuses: dict[str, components.ComponentStatus]):
    """The steps that will run, as pending entries, in order.

    Built up front so the confirm screen and the progress screen are the same
    list — a step that turns out to be unnecessary is marked ``skipped`` rather
    than quietly missing.
    """
    steps: list[Step] = []
    for key in components.keys():
        if key not in component_keys:
            continue
        if key == components.HUMMINGBOT_API:
            status = statuses.get(key)
            mode = status.mode if status else "image"
            steps.append(
                Step(f"{key}.fast-forward", "Fast-forwarding the hummingbot-api repo")
            )
            steps.append(
                Step(
                    f"{key}.image",
                    (
                        "Rebuilding the API image"
                        if mode == "source"
                        else "Pulling the API image"
                    ),
                )
            )
            steps.append(Step(f"{key}.up", "Recreating the containers"))
            steps.append(Step(f"{key}.health", "Waiting for the API to answer"))
        else:
            # No restart step: the run stops with the new code on disk and asks
            # for the relaunch instead (see the module docstring).
            steps.append(Step(f"{key}.fast-forward", "Fast-forwarding Condor"))
            steps.append(Step(f"{key}.deps", "Syncing dependencies"))
            steps.append(Step(f"{key}.frontend", "Rebuilding the dashboard"))
    return steps


async def start(
    component_keys: list[str],
    *,
    resolutions: dict[str, str] | None = None,
    actor_user_id: int | None = None,
    actor_chat_id: Any = None,
) -> Run:
    """Begin an update and return immediately with the run to watch.

    Returns the run *already in flight* if there is one: a second caller means
    a second surface, not a second update.
    """
    global _current, _task

    async with _lock:
        if _current is not None and _current.live:
            return _current

        selected = [k for k in components.keys() if k in set(component_keys)]
        statuses = {s.key: s for s in await components.check()}

        run = Run(
            id=f"u-{int(time.time())}",
            started=time.time(),
            actor={"user_id": actor_user_id, "chat_id": actor_chat_id},
            components=selected,
            steps=_plan(selected, statuses),
        )
        _current = run
        _task = asyncio.create_task(_execute(run, resolutions or {}))
        return run


async def _begin(run: Run, key: str) -> Step | None:
    step = run.step(key)
    if step is None:
        return None
    step.state = RUNNING
    step.started = time.time()
    await _emit(run)
    return step


async def _finish(run: Run, step: Step, state: str, output: str = "") -> None:
    step.state = state
    step.ended = time.time()
    if output:
        step.output_tail = tail(output)
    await _emit(run)


async def _fail(run: Run, message: str) -> None:
    run.state = FAILED
    run.error = message
    run.ended = time.time()
    await _emit(run)
    run.done.set()


async def _execute(run: Run, resolutions: dict[str, str]) -> None:
    """Walk the plan. The first failure ends the run where it stands."""
    try:
        await _emit(run)

        for component_key, action in resolutions.items():
            ok, message = await resolve(component_key, action)
            if not ok:
                await _fail(run, f"Could not {action} in {component_key}: {message}")
                return

        for key in run.components:
            if key == components.HUMMINGBOT_API:
                if not await _update_hb_api(run):
                    return
            else:
                if not await _update_condor(run):
                    return

        components.invalidate()
        run.state = SUCCEEDED
        run.ended = time.time()
        await _emit(run)
        run.done.set()
    except asyncio.CancelledError:
        # A shutdown mid-run: the journal keeps whatever the last transition
        # said, and the next boot shows it as the run that never finished.
        raise
    except Exception as e:  # noqa: BLE001 - an update must never die silently
        log.exception("Update run %s crashed", run.id)
        await _fail(run, f"{type(e).__name__}: {e}")


async def _update_hb_api(run: Run) -> bool:
    """Move the checkout, put the new image on disk, and wait for it to serve."""
    component = components.get(components.HUMMINGBOT_API)
    assert component is not None and component.service is not None
    prefix = component.key

    step = await _begin(run, f"{prefix}.fast-forward")
    if step is not None:
        blocks = await components.repo_blocks(component)
        if blocks:
            await _finish(run, step, FAILED, blocks[0].message)
            await _fail(run, blocks[0].message)
            return False
        behind = await updater.check_for_updates(repo_dir=component.repo_dir)
        if behind.get("up_to_date") and not behind.get("error"):
            await _finish(run, step, SKIPPED, "Already up to date.")
        else:
            ok, output = await updater.fast_forward(component.repo_dir)
            await _finish(run, step, OK if ok else FAILED, output)
            if not ok:
                await _fail(run, "The hummingbot-api checkout could not be moved.")
                return False

    mode = await updater.compose_mode(component.repo_dir, component.service)
    step = await _begin(run, f"{prefix}.image")
    if step is not None:
        if mode == "source":
            ok, output = await updater.compose_build(
                component.repo_dir, component.service
            )
        else:
            ok, output = await updater.compose_pull(
                component.repo_dir, component.service
            )
        await _finish(run, step, OK if ok else FAILED, output)
        if not ok:
            await _fail(run, "The API image could not be produced.")
            return False

    step = await _begin(run, f"{prefix}.up")
    if step is not None:
        ok, output = await updater.compose_up(component.repo_dir)
        await _finish(run, step, OK if ok else FAILED, output)
        if not ok:
            await _fail(run, "The containers could not be recreated.")
            return False

    step = await _begin(run, f"{prefix}.health")
    if step is not None:
        url = components.hb_api_health_url()
        if not url:
            await _finish(run, step, SKIPPED, "No API server is configured to probe.")
        else:
            ok, output = await updater.wait_healthy(url)
            await _finish(run, step, OK if ok else FAILED, output)
            if not ok:
                await _fail(
                    run,
                    "The API container came up but never answered; it may be "
                    "crash-looping. Check `docker compose logs hummingbot-api`.",
                )
                return False

    return True


async def _update_condor(run: Run) -> bool:
    """Move the checkout, rebuild what the move invalidated, then hand over."""
    component = components.get(components.CONDOR)
    assert component is not None
    prefix = component.key

    before = await updater.get_local_commit_full(component.repo_dir)
    run.from_commit = before

    step = await _begin(run, f"{prefix}.fast-forward")
    if step is not None:
        blocks = await components.repo_blocks(component)
        if blocks:
            await _finish(run, step, FAILED, blocks[0].message)
            await _fail(run, blocks[0].message)
            return False
        ok, output = await updater.fast_forward(component.repo_dir)
        await _finish(run, step, OK if ok else FAILED, output)
        if not ok:
            await _fail(run, "Condor could not be fast-forwarded.")
            return False

    after = await updater.get_local_commit_full(component.repo_dir)
    run.target_commit = after

    step = await _begin(run, f"{prefix}.deps")
    if step is not None:
        ok, output = await updater.install_dependencies()
        await _finish(run, step, OK if ok else FAILED, output)
        if not ok:
            await _fail(
                run,
                "Code was pulled but dependencies failed. Fix it before " "restarting.",
            )
            return False

    # The Makefile builds the frontend before starting; an in-place update has
    # to do it here or the dashboard keeps serving the previous bundle.
    step = await _begin(run, f"{prefix}.frontend")
    if step is not None:
        if not await updater.frontend_needs_build(before, after):
            await _finish(run, step, SKIPPED, "The update did not touch frontend/.")
        else:
            ok, output = await updater.build_frontend(before, after)
            await _finish(run, step, OK if ok else FAILED, output)
            if not ok:
                await _fail(
                    run,
                    "Code and deps are updated, but the dashboard would come "
                    "back on the previous bundle.",
                )
                return False

    # Everything that can be done without ending the process is done. The one
    # remaining step belongs to a human, so the run is judged here rather than
    # at the next boot: it succeeded, and it owes a relaunch.
    if after and after != before:
        branch = await updater.get_current_branch(component.repo_dir)
        _note_relaunch(run, before, after, branch)

    return True


# ── Boot ──


async def finalize_pending_run() -> Run | None:
    """Judge a run this process did not start and cannot finish. At boot, once.

    A run is only ever executed by the process that started it, so a journal
    still reading ``running`` or ``restarting`` when a *fresh* process reads it
    describes a run nobody is driving any more. Boot is the only place that can
    say so, and it must, or the panel polls a live-looking run for ever: there
    is no Done button on a run still in flight, a reload restores it, and a
    relaunch — the very thing the run asked for — restores it too.

    Two shapes reach here.

    ``restarting`` is the legacy crossing: updating *from* a Condor old enough
    to exec itself. That version journals ``restarting`` and dies, and the
    version it lands on is the only thing that can answer the one question it
    left — did the process come back on the commit it aimed at? HEAD answers it,
    so this is judged on the outcome and can succeed.

    ``running`` is an update that was interrupted: the process was killed, it
    crashed, or someone restarted it mid-run. There is no outcome to check —
    the plan stopped somewhere unknown — so it is failed honestly, naming the
    step it died on. Whatever landed before that is on disk and a new run
    fast-forwards over it; nothing here touches the working tree.
    """
    global _current

    run = read_journal()
    if run is None or not run.live:
        return None

    was_restarting = run.state == RESTARTING
    run.ended = time.time()

    # The step the process died on, whichever kind of interruption it was: the
    # legacy path always ends on ``.restart``, an interrupted one on whatever
    # was in flight. Both read better as "this is where it stopped" than as a
    # spinner that never resolves.
    step = None
    for candidate in run.steps:
        if candidate.key.endswith(".restart") or candidate.state == RUNNING:
            step = candidate
    if step is not None:
        step.ended = time.time()

    if not was_restarting:
        run.state = FAILED
        where = f" during {step.label.lower()}" if step is not None else ""
        run.error = (
            f"This update was interrupted{where} — Condor stopped before the "
            "run finished. Nothing was rolled back; start it again to pick up "
            "wherever it got to."
        )
        if step is not None:
            step.state = FAILED
            step.output_tail = run.error
        run.done.set()
        _current = run
        _write_journal(run)
        log.warning("Update %s was interrupted; judged failed at boot", run.id)
        return run

    head = await updater.get_local_commit_full()
    target = run.target_commit or ""

    if target and head and head == target:
        run.state = SUCCEEDED
        if step is not None:
            step.state = OK
            step.output_tail = f"Came back on {head[:7]}."
    else:
        run.state = FAILED
        run.error = (
            f"Came back on {head[:7] or 'an unknown commit'}, "
            f"expected {target[:7] or 'an unknown commit'}."
        )
        if step is not None:
            step.state = FAILED
            step.output_tail = run.error

    run.done.set()
    _current = run
    _write_journal(run)
    return run


def acknowledge_run(run_id: str = "") -> Run | None:
    """Mark the finished run as seen, so the panel stops showing it.

    The counterpart to Done. A run is dismissed *durably* because the panel has
    no state of its own worth trusting — it is a view over ``update_run.json``,
    and a dismissal that lived in the browser came back on the next reload.

    Refuses a live run (there is nothing to dismiss yet) and a stale ``run_id``
    (the panel may be minutes old and a newer run may have started since).
    Returns the run it marked, or ``None`` when it marked nothing.
    """
    run = _current or read_journal()
    if run is None or run.live:
        return None
    if run_id and run.id != run_id:
        return None
    if not run.acknowledged:
        run.acknowledged = True
        _write_journal(run)
    return run
