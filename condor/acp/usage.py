"""What a model has read and written, in one shape for every backend (FEAT-120).

Both clients already receive exact usage from their backend — the ACP adapter on
every ``session/prompt`` response and ``usage_update`` notification, pydantic-ai
on every run — and this is the one type they fold it into. It lives in
``condor.acp`` because both clients import it and ``condor.runtime`` already
depends on this package, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

# Summed by ``+`` and subtracted by ``-``. The context readings are not: they
# are the latest value, and a sum of two occupancies means nothing.
_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "unpriced_turns",
)


@dataclass
class TokenUsage:
    """A running tally of tokens and their price at API rates.

    **``input_tokens`` is inclusive**: every prompt token the model read, cache
    hits and cache writes included, so ``total_tokens`` means "everything the
    model read and wrote" on both backends. pydantic-ai (like OpenAI's
    ``prompt_tokens``) already counts that way. Anthropic does not — its
    ``input_tokens`` excludes both cache counters — so the ACP fold adds them
    back in (:meth:`from_acp`). ``cache_*`` are therefore subsets of
    ``input_tokens``, never additions to it.

    ``cost_usd`` is what the tokens would cost at API prices, summed over
    whatever could be priced. A Claude subscription is not billed per token, so
    this is an estimate and never a spend. ``unpriced_turns`` counts the runs
    whose price could not be known (a local model, an id the bundled price
    table does not have): a chat with both is a lower bound.

    The ACP adapter keeps a background task's result out of the turn's token
    counts and reports only its cost and context. Those tokens are missing here
    while their cost is not; reconstructing them is not worth it.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_turns: int = 0
    # Latest-value fields: overwritten, never summed.
    context_used: int | None = None
    context_size: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def is_zero(self) -> bool:
        """Nothing was counted. The context readings do not count as usage."""
        return not any(getattr(self, name) for name in _COUNTERS)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        summed = {
            name: getattr(self, name) + getattr(other, name) for name in _COUNTERS
        }
        return TokenUsage(
            **summed,
            context_used=(
                other.context_used
                if other.context_used is not None
                else self.context_used
            ),
            context_size=(
                other.context_size
                if other.context_size is not None
                else self.context_size
            ),
        )

    def __sub__(self, other: TokenUsage) -> TokenUsage:
        """What ``self`` has that ``other`` had not. Floored at zero.

        The context readings are ``self``'s: a delta taken now carries the
        latest occupancy, not a difference of two.
        """
        return TokenUsage(
            **{
                name: max(getattr(self, name) - getattr(other, name), 0)
                for name in _COUNTERS
            },
            context_used=self.context_used,
            context_size=self.context_size,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {f.name: getattr(self, f.name) for f in fields(self)}
        # Float subtraction leaves dust (0.41 - 0.4 = 0.00999…); a micro-dollar
        # is below anything the readout shows.
        out["cost_usd"] = round(self.cost_usd, 6)
        out["total_tokens"] = self.total_tokens
        return out

    @classmethod
    def from_dict(cls, data: dict | None) -> TokenUsage:
        """Tolerant both ways: unknown keys are ignored, missing ones read as 0.

        A meta written before FEAT-120 has no ``usage`` at all, which reads as
        zero — "unknown before this existed", not a real reading.
        """
        if not isinstance(data, dict):
            return cls()
        usage = cls()
        for name in _COUNTERS:
            value = data.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(usage, name, float(value) if name == "cost_usd" else int(value))
        for name in ("context_used", "context_size"):
            value = data.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(usage, name, value)
        return usage

    @classmethod
    def from_acp(cls, usage: Any) -> TokenUsage:
        """A ``session/prompt`` response's ``usage``, with input made inclusive.

        The adapter reports Anthropic's split — ``inputTokens`` excludes
        ``cachedReadTokens`` and ``cachedWriteTokens`` (its own ``totalTokens``
        is the sum of all four) — so they are added back into input here.
        """
        if not isinstance(usage, dict):
            return cls()

        def count(key: str) -> int:
            value = usage.get(key)
            return int(value) if isinstance(value, (int, float)) else 0

        cache_read = count("cachedReadTokens")
        cache_write = count("cachedWriteTokens")
        return cls(
            input_tokens=count("inputTokens") + cache_read + cache_write,
            output_tokens=count("outputTokens"),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
