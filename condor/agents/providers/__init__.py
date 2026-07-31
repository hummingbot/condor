"""Data provider registry -- discovers and runs providers for trading agents."""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseProvider, ProviderResult

log = logging.getLogger(__name__)

# Python provider registry
_REGISTRY: dict[str, BaseProvider] = {}


def _auto_register() -> None:
    """Import built-in provider modules."""
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
        self, client: Any, config: dict, agent_id: str = ""
    ) -> dict[str, ProviderResult]:
        """Run all core providers and return {name: ProviderResult} dict."""
        if not _REGISTRY:
            _auto_register()

        results: dict[str, ProviderResult] = {}
        for provider in list_core_providers():
            try:
                result = await provider.execute(client, config, agent_id=agent_id)
                results[result.name] = result
            except Exception:
                log.exception("Core provider %s failed", provider.name)
                results[provider.name] = ProviderResult(
                    name=provider.name, data={}, summary=f"(provider {provider.name} failed)"
                )
        return results

    async def run_provider(self, name: str, client: Any, config: dict) -> ProviderResult | None:
        """Run a single provider by name."""
        provider = get_provider(name)
        if not provider:
            return None
        return await provider.execute(client, config)
