"""OpenRouter model catalog fetcher.

Hits GET https://openrouter.ai/api/v1/models (public, unauthenticated) and
filters to entries that advertise `tools` in their supported_parameters.
Condor's whole architecture depends on tool-calling, so models without it
are hidden from the picker.

Results are cached for an hour so paginating the picker doesn't refetch.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class OpenRouterModel:
    slug: str            # e.g. "anthropic/claude-sonnet-4-5"
    name: str            # human-friendly name from the API
    context_length: int  # tokens
    prompt_price: float  # USD per 1M input tokens, 0 if free
    completion_price: float


_cache: tuple[float, list[OpenRouterModel]] | None = None


def _parse_price(value: object) -> float:
    """OpenRouter returns prices as strings in USD per token. Convert to per-1M."""
    try:
        return float(value) * 1_000_000  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _model_supports_tools(entry: dict) -> bool:
    params = entry.get("supported_parameters") or []
    return "tools" in params


async def fetch_models(force_refresh: bool = False) -> list[OpenRouterModel]:
    """Return the list of OpenRouter models that support tool-calling.

    Sorted by provider then model id for predictable pagination.
    """
    global _cache

    if not force_refresh and _cache is not None:
        cached_at, models = _cache
        if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
            return models

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(OPENROUTER_MODELS_URL) as resp:
                resp.raise_for_status()
                payload = await resp.json()
    except Exception as e:
        log.warning("Failed to fetch OpenRouter models: %s", e)
        if _cache is not None:
            return _cache[1]  # serve stale cache on failure
        return []

    raw = payload.get("data") or []
    models: list[OpenRouterModel] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("id")
        if not isinstance(slug, str) or not slug or slug.startswith("~"):
            continue  # skip canonical aliases like "~anthropic/claude-haiku-latest"
        if not _model_supports_tools(entry):
            continue
        pricing = entry.get("pricing") or {}
        models.append(
            OpenRouterModel(
                slug=slug,
                name=str(entry.get("name") or slug),
                context_length=int(entry.get("context_length") or 0),
                prompt_price=_parse_price(pricing.get("prompt")),
                completion_price=_parse_price(pricing.get("completion")),
            )
        )

    models.sort(key=lambda m: m.slug.lower())
    _cache = (time.monotonic(), models)
    log.info("Fetched %d OpenRouter models with tool support", len(models))
    return models


def format_button_label(model: OpenRouterModel) -> str:
    """Short label for inline keyboard buttons. Telegram max ~30 chars looks good."""
    label = model.name or model.slug
    if len(label) > 38:
        label = label[:35] + "..."
    return label


def find_model_by_slug(
    models: list[OpenRouterModel], slug: str
) -> OpenRouterModel | None:
    """Case-insensitive exact-match lookup by slug."""
    target = slug.strip().lower()
    if not target:
        return None
    for m in models:
        if m.slug.lower() == target:
            return m
    return None
