"""Data provider registry -- discovers and runs providers for trading agents."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from .base import BaseProvider, ProviderResult

if TYPE_CHECKING:
    from condor.agents.ownership import OwnedBot

log = logging.getLogger(__name__)

# Python provider registry
_REGISTRY: dict[str, BaseProvider] = {}


def _auto_register() -> None:
    """Import built-in provider modules."""
    from . import drift  # noqa: F401
    from . import executors  # noqa: F401
    from . import positions  # noqa: F401


def register_provider(provider: BaseProvider) -> None:
    _REGISTRY[provider.name] = provider
    log.debug("Registered provider: %s (core=%s)", provider.name, provider.is_core)


def get_provider(name: str) -> BaseProvider | None:
    if not _REGISTRY:
        _auto_register()
    return _REGISTRY.get(name)


def list_providers() -> list[BaseProvider]:
    if not _REGISTRY:
        _auto_register()
    return list(_REGISTRY.values())


def list_core_providers() -> list[BaseProvider]:
    return [p for p in list_providers() if p.is_core]


class ProviderRegistry:
    """Convenience wrapper used by TickEngine."""

    async def run_core_providers(
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        owned: list[OwnedBot] | None = None,
        names: Collection[str] | None = None,
    ) -> dict[str, ProviderResult]:
        """Run the core providers and return {name: ProviderResult} dict.

        ``bot_names`` are the bases the session owns per its ownership ledger, so
        a session operating several bots sees all of them in its core data.
        ``owned`` is that ledger's records, which scope each base's PnL to the
        window this session held it over.
        ``names`` narrows the run to those core providers (the shutdown winddown
        only needs ``executors``); ``None`` runs every one, as the tick does.
        """
        if not _REGISTRY:
            _auto_register()

        async def _run_one(provider: BaseProvider) -> ProviderResult:
            try:
                return await provider.execute(
                    client,
                    config,
                    agent_id=agent_id,
                    bot_names=bot_names,
                    owned=owned,
                )
            except Exception:
                log.exception("Core provider %s failed", provider.name)
                return ProviderResult(
                    name=provider.name,
                    data={},
                    summary=f"(provider {provider.name} failed)",
                )

        providers = [
            p for p in list_core_providers() if names is None or p.name in names
        ]
        # The providers are independent (none reads another's result), so their
        # API round trips run concurrently; gather keeps registration order.
        outcomes = await asyncio.gather(*(_run_one(p) for p in providers))
        return {result.name: result for result in outcomes}
