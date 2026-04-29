"""Factory helpers for HummingbotAPIClient with optional TLS policy."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

import aiohttp
from hummingbot_api_client import HummingbotAPIClient

_INIT_PATCH_LOCK = asyncio.Lock()


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def build_ssl_option(
    tls_verify: Any = True,
    ca_bundle_path: str | None = None,
    client_cert_path: str | None = None,
    client_key_path: str | None = None,
):
    """Build aiohttp ssl option (None, False, or SSLContext)."""
    verify = _coerce_bool(tls_verify, default=True)
    has_client_cert = bool(client_cert_path or client_key_path)

    if (client_cert_path and not client_key_path) or (client_key_path and not client_cert_path):
        raise ValueError("Both client_cert_path and client_key_path must be provided for mTLS.")

    if not verify:
        if not has_client_cert:
            return False
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif ca_bundle_path:
        ca_path = Path(ca_bundle_path).expanduser()
        if not ca_path.exists():
            raise ValueError(f"CA bundle not found: {ca_path}")
        ctx = ssl.create_default_context(cafile=str(ca_path))
    else:
        if not has_client_cert:
            return None
        ctx = ssl.create_default_context()

    if has_client_cert:
        cert_path = Path(client_cert_path).expanduser()
        key_path = Path(client_key_path).expanduser()
        if not cert_path.exists():
            raise ValueError(f"Client cert not found: {cert_path}")
        if not key_path.exists():
            raise ValueError(f"Client key not found: {key_path}")
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


async def create_initialized_client(
    *,
    base_url: str,
    username: str,
    password: str,
    timeout: aiohttp.ClientTimeout | None = None,
    tls_verify: Any = True,
    ca_bundle_path: str | None = None,
    client_cert_path: str | None = None,
    client_key_path: str | None = None,
) -> HummingbotAPIClient:
    """Create and initialize a HummingbotAPIClient with optional TLS policy."""
    client = HummingbotAPIClient(
        base_url=base_url,
        username=username,
        password=password,
        timeout=timeout,
    )

    ssl_option = build_ssl_option(
        tls_verify=tls_verify,
        ca_bundle_path=ca_bundle_path,
        client_cert_path=client_cert_path,
        client_key_path=client_key_path,
    )
    if ssl_option is None:
        await client.init()
        return client

    # Inject our SSL-aware connector by patching aiohttp.ClientSession during init().
    # This lets upstream init() create its session and routers normally — we only
    # override the underlying connector, so any routers added upstream are picked up
    # automatically.
    connector = aiohttp.TCPConnector(ssl=ssl_option)
    original_cs = aiohttp.ClientSession

    def patched_cs(*args, **kwargs):
        kwargs.setdefault("connector", connector)
        return original_cs(*args, **kwargs)

    async with _INIT_PATCH_LOCK:
        aiohttp.ClientSession = patched_cs
        try:
            await client.init()
        except Exception:
            await connector.close()
            raise
        finally:
            aiohttp.ClientSession = original_cs
    return client
