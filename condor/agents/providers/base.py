"""Base interface for trading agent data providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderResult:
    name: str
    data: dict[str, Any]
    summary: str  # 1-5 line text for LLM prompt


class BaseProvider:
    """Abstract base for trading agent data providers."""

    name: str = ""
    is_core: bool = False

    async def execute(
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        since: float = 0.0,
    ) -> ProviderResult:
        """Gather this provider's slice of core data.

        ``since`` is the instant the session took its bots over, for providers
        that must report only what this session produced rather than a bot's
        whole lifetime.

        ``bot_names`` are the bases the running session owns (from its ownership
        ledger); ``None`` means "not supplied" and providers that care fall back
        to the configured ``bot_name``.
        """
        raise NotImplementedError
