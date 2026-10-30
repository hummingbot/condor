"""Say where the event loop is stuck, while it is stuck.

Condor runs the dashboard, Telegram, every agent and every routine on one
asyncio loop, so anything that blocks it freezes all of them at once — and
because the loop is also where log lines are produced, a freeze used to look
like silence. Diagnosing the 2026-10-07 freeze took ``sudo py-spy dump``.

This is that dump, done automatically: a heartbeat scheduled on the loop, and a
daemon thread that, once the heartbeat is :data:`STALL_AFTER` seconds late, logs
the loop thread's current stack — once per stall, and again when it recovers.

It only observes. :mod:`condor.runtime.code_guard` is what interrupts agent
code; this covers everything it cannot (a long call into C, a blocking call in
Condor's own code) by at least naming the line.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger(__name__)

STALL_AFTER = 10.0
_BEAT = 1.0


class LoopWatchdog:
    def __init__(
        self, loop: asyncio.AbstractEventLoop, stall_after: float = STALL_AFTER
    ):
        self._loop = loop
        self._stall_after = stall_after
        # Fine enough to see a stall of ``stall_after``, never busier than 1s.
        self._interval = min(_BEAT, stall_after / 4)
        self._loop_thread = threading.get_ident()
        self._last_beat = time.monotonic()
        self._stop = threading.Event()
        self._handle: asyncio.TimerHandle | None = None
        self._thread = threading.Thread(
            target=self._watch, name="condor-loop-watchdog", daemon=True
        )

    def start(self) -> None:
        self._beat()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._handle is not None:
            self._handle.cancel()

    def _beat(self) -> None:
        self._last_beat = time.monotonic()
        if not self._stop.is_set():
            self._handle = self._loop.call_later(self._interval, self._beat)

    def _stack(self) -> str:
        frame = sys._current_frames().get(self._loop_thread)
        if frame is None:
            return "  (loop thread not found)\n"
        return "".join(traceback.format_stack(frame))

    def _watch(self) -> None:
        stalled_since: float | None = None
        while not self._stop.wait(self._interval):
            lag = time.monotonic() - self._last_beat
            if lag >= self._stall_after and stalled_since is None:
                stalled_since = self._last_beat
                logger.error(
                    "Event loop blocked for %.0fs — dashboard, agents and Telegram "
                    "are frozen until it returns. Loop thread is at:\n%s",
                    lag,
                    self._stack(),
                )
            elif lag < self._interval * 2 and stalled_since is not None:
                logger.warning(
                    "Event loop unblocked after %.0fs",
                    time.monotonic() - stalled_since,
                )
                stalled_since = None


def start(
    loop: asyncio.AbstractEventLoop | None = None, stall_after: float = STALL_AFTER
) -> LoopWatchdog:
    """Start watching ``loop`` (default: the running one). Call from its thread."""
    watchdog = LoopWatchdog(loop or asyncio.get_running_loop(), stall_after)
    watchdog.start()
    return watchdog
