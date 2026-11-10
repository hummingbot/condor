"""The per-tool retry budget must let a model correct a malformed call.

pydantic-ai retries a failed tool call once by default. One malformed call then
ends the run with ``UnexpectedModelBehavior: Tool 'X' exceeded max retries count
of 1`` -- observed live: a trend-aware LP loop died on tick 4, and the same pack
completed tick 1 unchanged once the budget was raised.
"""

import pytest

from condor.acp.pydantic_ai_client import DEFAULT_TOOL_RETRY_BUDGET, PydanticAIClient


def _client(**kwargs) -> PydanticAIClient:
    return PydanticAIClient(model="openai:gpt-4o-mini", **kwargs)


def test_default_budget_allows_a_correction():
    assert DEFAULT_TOOL_RETRY_BUDGET >= 2, (
        "a retry budget of 1 leaves the model no chance to correct a malformed "
        "tool call, and an unattended loop loses the tick"
    )
    assert _client().tool_retries == DEFAULT_TOOL_RETRY_BUDGET


@pytest.mark.parametrize("budget", [DEFAULT_TOOL_RETRY_BUDGET, 5, 1])
def test_budget_is_passed_to_the_agent(monkeypatch, budget):
    """The value has to reach Agent(...), not just sit on the client.

    Without this the wiring could be deleted and every other test here would
    still pass.
    """
    import pydantic_ai

    seen = {}

    class SpyAgent:
        def __init__(self, model, **kwargs):
            seen["model"] = model
            seen.update(kwargs)

    # ``_make_agent`` imports Agent when it runs, so patching the source module
    # is what the production call actually resolves.
    monkeypatch.setattr(pydantic_ai, "Agent", SpyAgent)

    client = _client(tool_retries=budget)
    made = client._make_agent(model="dummy-model", toolsets=[], capabilities=None)

    assert isinstance(made, SpyAgent)
    assert seen["retries"] == budget


def test_callers_can_restore_the_old_single_attempt():
    assert _client(tool_retries=1).tool_retries == 1


@pytest.mark.parametrize("bad", [0, -1])
def test_zero_or_negative_budget_is_refused(bad):
    with pytest.raises(ValueError):
        _client(tool_retries=bad)
