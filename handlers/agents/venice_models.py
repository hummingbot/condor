"""Venice AI model catalog fetcher.

Hits GET https://api.venice.ai/api/v1/models (requires VENICE_API_KEY) and
filters to entries that advertise `supportsFunctionCalling: true` under
`model_spec.capabilities`. Condor's whole architecture depends on tool-calling,
so models without it are hidden from the picker.

Also filters to `type == "text"` so image / audio models don't pollute the list.

Results are cached for an hour so paginating the picker doesn't refetch.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

VENICE_MODELS_URL = "https://api.venice.ai/api/v1/models"
CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class VeniceModel:
    slug: str            # e.g. "llama-3.3-70b"
    name: str            # human-friendly name (falls back to slug)
    context_length: int  # tokens
    input_price: float   # USD per 1M input tokens (Venice returns per-1M directly)
    output_price: float  # USD per 1M output tokens


_cache: tuple[float, list[VeniceModel]] | None = None


def _parse_price(value: object) -> float:
    """Venice returns pricing as `{"usd": <number>, "diem": <number>}` per 1M tokens."""
    try:
        if isinstance(value, dict):
            return float(value.get("usd", 0.0))
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _model_supports_tools(entry: dict) -> bool:
    spec = entry.get("model_spec") or {}
    caps = spec.get("capabilities") or {}
    return bool(caps.get("supportsFunctionCalling"))


def _model_is_text(entry: dict) -> bool:
    return entry.get("type") == "text"


async def fetch_models(force_refresh: bool = False) -> list[VeniceModel]:
    """Return the list of Venice models that support tool-calling.

    Sorted by slug for predictable pagination. Requires VENICE_API_KEY.
    """
    global _cache

    if not force_refresh and _cache is not None:
        cached_at, models = _cache
        if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
            return models

    api_key = os.environ.get("VENICE_API_KEY")
    if not api_key:
        log.warning("VENICE_API_KEY not set; cannot fetch Venice models")
        return []

    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(VENICE_MODELS_URL) as resp:
                resp.raise_for_status()
                payload = await resp.json()
    except Exception as e:
        log.warning("Failed to fetch Venice models: %s", e)
        if _cache is not None:
            return _cache[1]  # serve stale cache on failure
        return []

    raw = payload.get("data") or []
    models: list[VeniceModel] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("id")
        if not isinstance(slug, str) or not slug:
            continue
        if not _model_is_text(entry):
            continue
        if not _model_supports_tools(entry):
            continue

        spec = entry.get("model_spec") or {}
        pricing = spec.get("pricing") or {}
        # Prefer model_spec.availableContextTokens; fall back to top-level context_length.
        context_length = (
            spec.get("availableContextTokens")
            or entry.get("context_length")
            or 0
        )

        models.append(
            VeniceModel(
                slug=slug,
                name=str(spec.get("name") or slug),
                context_length=int(context_length),
                input_price=_parse_price(pricing.get("input")),
                output_price=_parse_price(pricing.get("output")),
            )
        )

    models.sort(key=lambda m: m.slug.lower())
    _cache = (time.monotonic(), models)
    log.info("Fetched %d Venice models with tool support", len(models))
    return models


def format_button_label(model: VeniceModel) -> str:
    """Short label for inline keyboard buttons. Telegram max ~38 chars looks good."""
    label = model.name or model.slug
    if len(label) > 38:
        label = label[:35] + "..."
    return label


def find_model_by_slug(
    models: list[VeniceModel], slug: str
) -> VeniceModel | None:
    """Case-insensitive exact-match lookup by slug."""
    target = slug.strip().lower()
    if not target:
        return None
    for m in models:
        if m.slug.lower() == target:
            return m
    return None
