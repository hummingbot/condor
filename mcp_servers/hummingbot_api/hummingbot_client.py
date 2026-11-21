"""
Hummingbot API client wrapper with connection management
"""

import asyncio
import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from hummingbot_api_client import HummingbotAPIClient

from mcp_servers.hummingbot_api.exceptions import MaxConnectionsAttemptError
from mcp_servers.hummingbot_api.settings import credentials_were_injected, settings

logger = logging.getLogger("hummingbot-mcp")


#: How long a failed connection is remembered before the next tool call may try
#: again. Short on purpose: the failure used to be cached for the life of the
#: process, so an API restart during a session's first call left that agent
#: unable to read or stop anything until its subprocess was respawned. The TTL
#: only stops a burst of calls from each paying the full retry ladder.
CONNECT_FAILURE_TTL_SECONDS = 10.0

#: Wrong credentials do not fix themselves in seconds, and hammering a login
#: endpoint is its own problem, so an auth failure is held longer — but still
#: not forever: the owner can correct the server's credentials mid-session.
AUTH_FAILURE_TTL_SECONDS = 60.0


class HummingbotClient:
    """Wrapper for HummingbotAPIClient with connection management"""

    def __init__(self):
        self._client: HummingbotAPIClient | None = None
        self._initialized = False
        self._last_error: Exception | None = None
        self._failed_url: str | None = None
        self._failed_at: float = 0.0
        self._failure_ttl: float = CONNECT_FAILURE_TTL_SECONDS
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _init_lock(self) -> asyncio.Lock:
        """One lock per event loop (a lock cannot be awaited across loops)."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _cached_failure(self) -> Exception | None:
        """The remembered connection error, while it is still fresh."""
        if self._last_error is None or self._failed_url != settings.api_url:
            return None
        if (time.monotonic() - self._failed_at) >= self._failure_ttl:
            return None
        return self._last_error

    def _remember_failure(self, error: Exception, ttl: float) -> None:
        self._failed_url = settings.api_url
        self._failed_at = time.monotonic()
        self._failure_ttl = ttl
        self._last_error = error

    async def initialize(self, force: bool = False) -> HummingbotAPIClient:
        """Initialize API client with retry logic

        Args:
            force: Force re-initialization even if previously failed
        """
        if self._client is not None and self._initialized:
            return self._client

        # Serialised: two tool calls arriving together at startup used to build
        # a client each, and the loser's aiohttp session was overwritten and
        # never closed. The second caller now waits and takes the first's
        # result — its client, or its fresh failure.
        async with self._init_lock():
            if self._client is not None and self._initialized:
                return self._client

            # A recent failure for this URL is re-raised rather than re-tried,
            # unless forced — and only until its TTL runs out.
            if not force:
                cached = self._cached_failure()
                if cached is not None:
                    raise cached

            return await self._connect()

    async def _discard(self, client: HummingbotAPIClient | None) -> None:
        """Close a client whose connection attempt failed; never raises."""
        if client is None:
            return
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.debug("closing a failed client: %s", exc)

    async def _connect(self) -> HummingbotAPIClient:
        # Reset failure state for new attempt
        self._failed_url = None
        self._last_error = None

        injected = credentials_were_injected()
        # Where to send the reader. A Condor-spawned session cannot call
        # `configure_server` (it is refused, and not mounted on most profiles),
        # so recommending it there is a dead end; and its URL is the owner's
        # internal host, which has no business in a chat transcript.
        where = (
            f"server '{settings.server_name}'" if injected else f"{settings.api_url}"
        )

        last_error = None
        for attempt in range(settings.max_retries):
            client: HummingbotAPIClient | None = None
            try:
                client = HummingbotAPIClient(
                    base_url=settings.api_url,
                    username=settings.api_username,
                    password=settings.api_password,
                    timeout=settings.client_timeout,
                )

                # Initialize and test connection
                await client.init()
                await client.accounts.list_accounts()

                self._client = client
                self._initialized = True
                logger.info(
                    f"Successfully connected to Hummingbot API at {settings.api_url}"
                )
                return client

            except Exception as e:
                await self._discard(client)
                last_error = e
                error_str = str(e).lower()
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

                # Don't retry on authentication errors
                if (
                    "401" in error_str
                    or "unauthorized" in error_str
                    or "authentication" in error_str
                ):
                    if injected:
                        message = (
                            f"❌ The Hummingbot API {where} rejected this session's "
                            "credentials (authentication failed).\n\n"
                            "This cannot be fixed from this session: the connection "
                            "is managed by the server's owner. Tell the user to "
                            "check the server's credentials in the Condor dashboard "
                            "(Settings → Servers). Nothing was read or changed."
                        )
                    else:
                        message = (
                            f"❌ Authentication failed when connecting to Hummingbot API at {where}\n\n"
                            f"The API credentials are incorrect:\n"
                            f"  - Username: {settings.api_username}\n\n"
                            f"💡 Solutions:\n"
                            f"  1. Verify your API credentials are correct\n"
                            f"  2. Use 'configure_server' tool with username/password to update credentials\n"
                            f"  3. Check your Hummingbot API server configuration\n\n"
                            f"Original error: {e}"
                        )
                    error = MaxConnectionsAttemptError(message)
                    self._remember_failure(error, AUTH_FAILURE_TTL_SECONDS)
                    raise error

                if attempt < settings.max_retries - 1:
                    await asyncio.sleep(settings.retry_delay)

        # All retries failed - save failure state and provide helpful error message
        error_str = str(last_error).lower() if last_error else ""

        if injected:
            error_message = (
                f"❌ Cannot reach the Hummingbot API {where} "
                f"({type(last_error).__name__} after {settings.max_retries} "
                "attempts). The API is not responding — it may be restarting.\n\n"
                "Nothing was read or changed. The connection is retried on the "
                f"next call: wait about {CONNECT_FAILURE_TTL_SECONDS:g} seconds and "
                "call the tool again. If it keeps failing, tell the user the server "
                "is unreachable; it is managed in the Condor dashboard "
                "(Settings → Servers) and cannot be changed from this session."
            )
        elif (
            "connection" in error_str
            or "refused" in error_str
            or "unreachable" in error_str
            or "timeout" in error_str
        ):
            error_message = (
                f"❌ Cannot reach Hummingbot API at {settings.api_url}\n\n"
                f"The API server is not responding. This usually means:\n"
                f"  - The API is not running\n"
                f"  - The API URL is incorrect\n"
                f"  - Network/firewall issues\n\n"
                f"💡 Solutions:\n"
                f"  1. Ensure the Hummingbot API is running and accessible, then call the tool again "
                f"(the connection is retried after about {CONNECT_FAILURE_TTL_SECONDS:g} seconds)\n"
                f"  2. Verify the API URL is correct: {settings.api_url}\n"
                f"  3. Use 'configure_server' tool with host/port to update the connection\n\n"
            )

            # Add Docker networking warning for localhost URLs
            if (
                "localhost" in settings.api_url
                and os.getenv("DOCKER_CONTAINER") == "true"
            ):
                system = platform.system()
                if system in ["Darwin", "Windows"]:
                    error_message += (
                        f"⚠️  Docker Networking Notice:\n"
                        f"You're running on {system} and trying to connect to 'localhost'.\n"
                        f"Docker containers on Mac/Windows cannot access 'localhost' on the host.\n\n"
                        f"💡 Try using 'host.docker.internal' instead:\n"
                        f"  Use 'configure_server' tool with host='host.docker.internal'\n\n"
                    )

            error_message += f"Original error: {last_error}"
        else:
            error_message = (
                f"❌ Failed to connect to Hummingbot API at {settings.api_url}\n\n"
                f"Connection failed after {settings.max_retries} attempts.\n\n"
                f"💡 Solutions:\n"
                f"  1. Check if the API is running and accessible, then call the tool again "
                f"(the connection is retried after about {CONNECT_FAILURE_TTL_SECONDS:g} seconds)\n"
                f"  2. Verify your credentials are correct\n"
                f"  3. Use 'configure_server' tool with host/port to configure the server\n\n"
                f"Original error: {last_error}"
            )
        error = MaxConnectionsAttemptError(error_message)
        self._remember_failure(error, CONNECT_FAILURE_TTL_SECONDS)
        raise error

    async def get_client(self) -> HummingbotAPIClient:
        """Get initialized client"""
        if not self._client or not self._initialized:
            return await self.initialize()
        return self._client

    @asynccontextmanager
    async def deployment_client(self):
        """Allow slow bot creation without changing the shared client's timeout."""
        # The SDK has no per-call timeout. Use its public client lifecycle for
        # this one POST; never retry a deployment whose outcome is unknown.
        client = HummingbotAPIClient(
            base_url=settings.api_url,
            username=settings.api_username,
            password=settings.api_password,
            timeout=aiohttp.ClientTimeout(
                total=max(120.0, settings.connection_timeout)
            ),
        )
        try:
            await client.init()
            yield client
        finally:
            await client.close()

    async def close(self):
        """Close the client connection and reset state"""
        if self._client:
            await self._client.close()
            self._client = None
            self._initialized = False
            # Reset failure state to allow retry with new configuration
            self._failed_url = None
            self._last_error = None


# Trading rules change on the venue's own schedule (a listing, a tick-size change),
# not on ours, so a minute-old answer is as good as a fresh one — and a create is
# often one of a burst (a DCA ladder, a grid rebuilt level by level), which is
# exactly where a round trip per call would show.
TRADING_RULES_TTL_SECONDS = 60.0


class TradingRulesCache:
    """The venue's rules for one ``(connector, pair)``, held for a TTL.

    ``get`` answers ``None`` whenever the venue cannot say — a connector with no
    rules endpoint (every gateway/DEX one), an empty body, a pair the venue does
    not list, a request that failed. That is the fail-open answer on purpose: only
    a rule that is actually present may refuse an order, because a rules outage
    must not become a trading outage.

    The miss is cached alongside the hit, so a venue that has nothing to say is
    asked once a minute rather than once an executor.
    """

    def __init__(self, ttl: float = TRADING_RULES_TTL_SECONDS):
        self._ttl = ttl
        self._entries: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}

    def clear(self) -> None:
        """Drop every cached answer. Used by tests and by a server reconfigure."""
        self._entries.clear()

    async def get(
        self, client: Any, connector_name: str, trading_pair: str
    ) -> dict[str, Any] | None:
        """Return the pair's trading rules, or ``None`` if they are unknowable."""
        key = (connector_name, trading_pair)
        now = time.monotonic()
        cached = self._entries.get(key)
        if cached is not None and (now - cached[0]) < self._ttl:
            return cached[1]

        rules = await self._fetch(client, connector_name, trading_pair)
        self._entries[key] = (now, rules)
        return rules

    @staticmethod
    async def _fetch(
        client: Any, connector_name: str, trading_pair: str
    ) -> dict[str, Any] | None:
        connectors = getattr(client, "connectors", None)
        if connectors is None:
            return None
        try:
            result = await connectors.get_trading_rules(
                connector_name=connector_name, trading_pairs=[trading_pair]
            )
        except Exception as exc:
            logger.warning(
                "Trading rules unavailable for %s %s, proceeding without them: %s",
                connector_name,
                trading_pair,
                exc,
            )
            return None

        if not isinstance(result, dict) or not result:
            return None
        rules = result.get(trading_pair)
        if rules is None and len(result) == 1:
            # Asked for one pair, got one entry back under the venue's own
            # spelling of it — that entry is the answer.
            rules = next(iter(result.values()))
        return rules if isinstance(rules, dict) else None


# Global client instance
hummingbot_client = HummingbotClient()

# Process-level, shared by every executor create tool.
trading_rules_cache = TradingRulesCache()
