"""Base interface for trading agent data providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from condor.agents.ownership import OwnedBot


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
        owned: list[OwnedBot] | None = None,
    ) -> ProviderResult:
        """Gather this provider's slice of core data.

        ``owned`` is the session's ownership ledger (one record per base, with
        its takeover and release instants), for providers that must report only
        what this session produced rather than a bot's whole lifetime. Turn it
        into per-base windows with
        :func:`condor.agents.attribution.ownership_windows`; never flatten it to
        one instant.

        ``bot_names`` are the bases the running session owns (from its ownership
        ledger); ``None`` means "not supplied" and providers that care fall back
        to the configured ``bot_name``.
        """
        raise NotImplementedError
