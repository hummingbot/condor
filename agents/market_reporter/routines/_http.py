"""Bounded fixed-host HTTP helpers with report-safe failure receipts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from agents.market_reporter.routines._providers import (
    get_provider,
    validate_provider_url,
)

USER_AGENT = "CondorMarketReporter/1.0 research@hummingbot.org"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


@dataclass(frozen=True)
class FetchResult:
    provider_id: str
    status: str
    retrieved_at: str
    url: str
    status_code: int | None = None
    data: Any = None
    text: str | None = None
    byte_count: int = 0
    error: str | None = None


async def _request(
    provider_id: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json_body: Any = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
    retry: bool = True,
) -> FetchResult:
    validate_provider_url(provider_id, url)
    provider = get_provider(provider_id)
    deadline = min(float(timeout or provider["timeout"]), 20.0)
    size_limit = min(int(max_bytes or provider["max_bytes"]), 5_000_000)
    safe_url = _canonical_url(url)
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/xml, text/xml, text/csv, text/plain, application/rss+xml",
    }
    request_headers.update(headers or {})
    attempts = 2 if retry else 1

    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(deadline),
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    method,
                    url,
                    params=params,
                    headers=request_headers,
                    json=json_body,
                ) as response:
                    if response.is_redirect:
                        return FetchResult(
                            provider_id,
                            "unavailable",
                            _utc_now(),
                            safe_url,
                            response.status_code,
                            error="redirect_not_allowed",
                        )
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt + 1 < attempts:
                            await asyncio.sleep(0.25)
                            continue
                    if response.status_code >= 400:
                        return FetchResult(
                            provider_id,
                            "unavailable",
                            _utc_now(),
                            safe_url,
                            response.status_code,
                            error=f"http_{response.status_code}",
                        )
                    chunks: list[bytes] = []
                    count = 0
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > size_limit:
                            return FetchResult(
                                provider_id,
                                "unavailable",
                                _utc_now(),
                                safe_url,
                                response.status_code,
                                byte_count=count,
                                error="response_too_large",
                            )
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    return FetchResult(
                        provider_id,
                        "complete",
                        _utc_now(),
                        safe_url,
                        response.status_code,
                        text=raw.decode("utf-8", errors="replace"),
                        byte_count=len(raw),
                    )
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25)
                continue
            return FetchResult(
                provider_id,
                "unavailable",
                _utc_now(),
                safe_url,
                error="transport_or_timeout",
            )
    return FetchResult(
        provider_id,
        "unavailable",
        _utc_now(),
        safe_url,
        error="request_failed",
    )


async def fetch_text(
    provider_id: str,
    url: str,
    **kwargs: Any,
) -> FetchResult:
    return await _request(provider_id, url, **kwargs)


async def fetch_json(
    provider_id: str,
    url: str,
    **kwargs: Any,
) -> FetchResult:
    result = await _request(provider_id, url, **kwargs)
    if result.status != "complete" or result.text is None:
        return result
    try:
        data = json.loads(result.text)
    except (TypeError, json.JSONDecodeError):
        return FetchResult(
            result.provider_id,
            "unavailable",
            result.retrieved_at,
            result.url,
            result.status_code,
            byte_count=result.byte_count,
            error="invalid_json",
        )
    return FetchResult(
        result.provider_id,
        result.status,
        result.retrieved_at,
        result.url,
        result.status_code,
        data=data,
        byte_count=result.byte_count,
    )
