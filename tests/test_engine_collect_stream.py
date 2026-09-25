"""READ-672: ``TickEngine._collect_stream`` is a test seam, not a ``wait_for`` adapter.

The tick budget is the ``asyncio.timeout`` block in ``_run_model``; the wrapper
only passes ``prompt_stream`` through and stops at ``PromptDone``.
"""

import asyncio
from pathlib import Path

from condor.acp.client import PromptDone, TextChunk
from condor.agents import engine as engine_module
from condor.agents.engine import TickEngine


def test_engine_never_mentions_wait_for():
    assert "wait_for" not in Path(engine_module.__file__).read_text()


def test_collect_stream_docstring_names_the_seam_and_the_real_budget():
    doc = TickEngine._collect_stream.__doc__ or ""
    assert "prompt_stream" in doc
    assert "Test seam" in doc
    assert "asyncio.timeout" in doc
    assert "_run_model" in doc


def test_collect_stream_stops_at_prompt_done_for_a_client_that_does_not():
    class _NoBreakClient:
        async def prompt_stream(self, prompt):
            yield TextChunk(text="a")
            yield PromptDone(stop_reason="end_turn")
            yield TextChunk(text="after done")

    async def collect():
        # ``self`` is unused by the wrapper.
        return [
            e async for e in TickEngine._collect_stream(None, _NoBreakClient(), "p")
        ]

    events = asyncio.run(collect())
    assert [type(e) for e in events] == [TextChunk, PromptDone]
