"""Factory helpers for HummingbotAPIClient with optional TLS policy."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import aiohttp
from hummingbot_api_client import HummingbotAPIClient
from hummingbot_api_client import client as hb_client_module


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

    if ca_bundle_path:
        ca_path = Path(ca_bundle_path).expanduser()
        if not ca_path.exists():
            raise ValueError(f"CA bundle not found: {ca_path}")
        ctx = ssl.create_default_context(cafile=str(ca_path))
    elif verify:
        if not has_client_cert:
            return None
        ctx = ssl.create_default_context()
    else:
        if not has_client_cert:
            return False
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    if has_client_cert:
        cert_path = Path(client_cert_path).expanduser()
        key_path = Path(client_key_path).expanduser()
        if not cert_path.exists():
            raise ValueError(f"Client cert not found: {cert_path}")
        if not key_path.exists():
            raise ValueError(f"Client key not found: {key_path}")
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


def _attach_routers(client: HummingbotAPIClient, session: aiohttp.ClientSession) -> None:
    """Attach router instances to a preconfigured client session."""
    client._session = session
    client._accounts = hb_client_module.AccountsRouter(session, client.base_url)
    client._archived_bots = hb_client_module.ArchivedBotsRouter(session, client.base_url)
    client._backtesting = hb_client_module.BacktestingRouter(session, client.base_url)
    client._bot_orchestration = hb_client_module.BotOrchestrationRouter(session, client.base_url)
    client._connectors = hb_client_module.ConnectorsRouter(session, client.base_url)
    client._controllers = hb_client_module.ControllersRouter(session, client.base_url)
    client._docker = hb_client_module.DockerRouter(session, client.base_url)
    client._executors = hb_client_module.ExecutorsRouter(session, client.base_url)
    client._gateway = hb_client_module.GatewayRouter(session, client.base_url)
    client._gateway_swap = hb_client_module.GatewaySwapRouter(session, client.base_url)
    client._gateway_clmm = hb_client_module.GatewayCLMMRouter(session, client.base_url)
    client._market_data = hb_client_module.MarketDataRouter(session, client.base_url)
    client._portfolio = hb_client_module.PortfolioRouter(session, client.base_url)
    client._scripts = hb_client_module.ScriptsRouter(session, client.base_url)
    client._trading = hb_client_module.TradingRouter(session, client.base_url)
    client._ws = hb_client_module.WebSocketRouter(
        session, client.base_url, client._username, client._password
    )


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

    connector = aiohttp.TCPConnector(ssl=ssl_option)
    session = aiohttp.ClientSession(auth=client.auth, timeout=client.timeout, connector=connector)
    try:
        _attach_routers(client, session)
        return client
    except Exception:
        await session.close()
        raise
