"""One bad sweep must not end the health monitor (XC-07).

``_health_check_loop`` had no per-iteration ``except``: the first exception out
of ``_sweep_sessions`` ended the task, and with it dead-session reaping and
idle detach, silently and until the next restart.
"""

import asyncio

from condor.runtime import sessions


def test_the_loop_keeps_sweeping_after_a_sweep_raises(monkeypatch):
    calls = []
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await real_sleep(0)

    async def sweep():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("registry changed size during iteration")
        if len(calls) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(sessions.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(sessions, "_sweep_sessions", sweep)

    asyncio.run(asyncio.wait_for(sessions._health_check_loop(), timeout=5))

    assert calls == [0, 1, 2]
