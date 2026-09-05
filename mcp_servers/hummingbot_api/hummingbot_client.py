"""
Hummingbot API client wrapper with connection management
"""

import asyncio
import logging
import os
import platform
import time
from typing import Any

from hummingbot_api_client import HummingbotAPIClient

from mcp_servers.hummingbot_api.exceptions import MaxConnectionsAttemptError
from mcp_servers.hummingbot_api.settings import settings

logger = logging.getLogger("hummingbot-mcp")


class HummingbotClient:
    """Wrapper for HummingbotAPIClient with connection management"""

    def __init__(self):
        self._client: HummingbotAPIClient | None = None
        self._initialized = False
        self._last_error: Exception | None = None
        self._failed_url: str | None = None

    async def initialize(self, force: bool = False) -> HummingbotAPIClient:
        """Initialize API client with retry logic

        Args:
            force: Force re-initialization even if previously failed
        """
        if self._client is not None and self._initialized:
            return self._client

        # If we've already failed for this URL, don't retry unless forced or URL changed
        if not force and self._failed_url == settings.api_url and self._last_error:
            raise self._last_error

        # Reset failure state for new attempt
        self._failed_url = None
        self._last_error = None

        last_error = None
        for attempt in range(settings.max_retries):
            try:
                self._client = HummingbotAPIClient(
                    base_url=settings.api_url,
                    username=settings.api_username,
                    password=settings.api_password,
                    timeout=settings.client_timeout,
                )

                # Initialize and test connection
                await self._client.init()
                await self._client.accounts.list_accounts()

                self._initialized = True
                logger.info(
                    f"Successfully connected to Hummingbot API at {settings.api_url}"
                )
                return self._client

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

                # Don't retry on authentication errors
                if (
                    "401" in error_str
                    or "unauthorized" in error_str
                    or "authentication" in error_str
                ):
                    self._failed_url = settings.api_url
                    self._last_error = MaxConnectionsAttemptError(
                        f"❌ Authentication failed when connecting to Hummingbot API at {settings.api_url}\n\n"
                        f"The API credentials are incorrect:\n"
                        f"  - Username: {settings.api_username}\n"
                        f"  - Password: {'*' * len(settings.api_password)}\n\n"
                        f"💡 Solutions:\n"
                        f"  1. Verify your API credentials are correct\n"
                        f"  2. Use 'configure_server' tool with username/password to update credentials\n"
                        f"  3. Check your Hummingbot API server configuration\n\n"
                        f"Original error: {e}"
                    )
                    raise self._last_error

                if attempt < settings.max_retries - 1:
                    await asyncio.sleep(settings.retry_delay)

        # All retries failed - save failure state and provide helpful error message
        self._failed_url = settings.api_url
        error_str = str(last_error).lower() if last_error else ""

        if (
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
                f"  1. Ensure the Hummingbot API is running and accessible\n"
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
            self._last_error = MaxConnectionsAttemptError(error_message)
        else:
            self._last_error = MaxConnectionsAttemptError(
                f"❌ Failed to connect to Hummingbot API at {settings.api_url}\n\n"
                f"Connection failed after {settings.max_retries} attempts.\n\n"
                f"💡 Solutions:\n"
                f"  1. Check if the API is running and accessible\n"
                f"  2. Verify your credentials are correct\n"
                f"  3. Use 'configure_server' tool with host/port to configure the server\n\n"
                f"Original error: {last_error}"
            )
        raise self._last_error

    async def get_client(self) -> HummingbotAPIClient:
        """Get initialized client"""
        if not self._client or not self._initialized:
            return await self.initialize()
        return self._client

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
