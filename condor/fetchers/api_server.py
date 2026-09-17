"""What a hummingbot-api server runs, and the client defaults its bots inherit (FEAT-121).

Two questions the Settings → Hummingbot API panel asks about the server the navbar
points at:

``/system/info`` — the versions and the provenance. Which hummingbot-api and which
hummingbot library, the image reference and its registry digest, the compose project,
and whether that image is *pinned*: built on the box, or brought up with an override
file that can repoint ``image:``. That last one is the question asked after every
incident, and today it is answered by ssh-ing in to read ``docker inspect``. It also
carries the ``MARKET_DATA_*`` tunables the process resolved, which the panel shows
read-only — the API cannot rewrite its own ``.env``, so an editor there would promise a
change that silently disappears.

``/bot-orchestration/rate-oracle/config`` — the slice of a credentials profile's
``conf_client.yml`` that a deploy copies into every new bot: the rate oracle source, the
global token, and the share of an exchange's API rate limit one instance may spend. It
applies to bots deployed *after* a change; running bots keep the copy they were built
with.

Both are past the pinned client's surface, so both go through
:mod:`condor.fetchers.raw_api` — see that module for why, and for why a 404 here means
"this server's API is too old for this panel" rather than "this server is down".

**One account.** Condor deploys with ``credentials_profile: master_account``
(``handlers/bots/controller_handlers.py``) and its settings routes already read that
account, so that is the profile these functions ask about. The API accepts others; there
is no Condor path that deploys from one, so exposing a picker would offer a choice that
changes nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from condor.fetchers import raw_api

logger = logging.getLogger(__name__)

#: The route that reports the server's own versions and image provenance.
SYSTEM_INFO_PATH = "/system/info"

#: The route that reads and writes the bot client defaults in a credentials profile's
#: ``conf_client.yml``. One constant so the read and the write can never disagree.
CLIENT_CONFIG_PATH = "/bot-orchestration/rate-oracle/config"

#: The credentials profile Condor deploys from, and therefore the only one it configures.
CLIENT_CONFIG_ACCOUNT = "master_account"


class ApiServerSettingsUnsupported(raw_api.ApiRouteUnsupported):
    """This server's hummingbot-api predates these routes.

    Not an outage: a server that has not been upgraded answers 404 to a well-formed
    request, and the panel should say "upgrade this server's API" rather than report it
    offline. A subclass of the shared capability exception so either name catches it.
    """


async def fetch_system_info(client) -> dict[str, Any]:
    """The server's versions, image provenance and market-data tunables.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates ``/system/info``.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved.
    """
    return await raw_api.request(
        client,
        "get",
        SYSTEM_INFO_PATH,
        unsupported=ApiServerSettingsUnsupported,
    )


async def fetch_client_config(
    client, account: str = CLIENT_CONFIG_ACCOUNT
) -> dict[str, Any]:
    """The client defaults bots deployed from ``account`` will inherit.

    Returns the persisted ``rate_oracle_source``, ``global_token`` and
    ``rate_limits_share_pct``, plus ``available_sources`` — the source list comes from
    the server rather than from a constant here, because the sources a bot can use are
    whatever the *server's* bundled hummingbot knows about.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates the route.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved.
    """
    return await raw_api.request(
        client,
        "get",
        CLIENT_CONFIG_PATH,
        params={"account_name": account},
        unsupported=ApiServerSettingsUnsupported,
    )


async def update_client_config(
    client,
    *,
    rate_oracle_source: Optional[str] = None,
    global_token_name: Optional[str] = None,
    global_token_symbol: Optional[str] = None,
    rate_limits_share_pct: Optional[float] = None,
    account: str = CLIENT_CONFIG_ACCOUNT,
) -> dict[str, Any]:
    """Change some of the client defaults; fields left ``None`` are not touched.

    Validation stays upstream. The server checks the oracle source against its own
    bundled hummingbot and the share against ``ClientConfigMap``'s own ``0 < pct <= 100``,
    then answers 400/422 — re-checking here would mean a second copy of a rule that has
    to agree with a *remote* library version, which is the copy that goes stale.

    Raises:
        ApiServerSettingsUnsupported: The server's API predates the route.
        aiohttp.ClientResponseError: Any other upstream failure, status preserved — in
            particular the 400/422 that names an invalid value, which the caller forwards.
    """
    changes: dict[str, Any] = {}
    if rate_oracle_source is not None:
        changes["rate_oracle_source"] = {"name": rate_oracle_source}

    global_token: dict[str, Any] = {}
    if global_token_name is not None:
        global_token["global_token_name"] = global_token_name
    if global_token_symbol is not None:
        global_token["global_token_symbol"] = global_token_symbol
    if global_token:
        changes["global_token"] = global_token

    if rate_limits_share_pct is not None:
        changes["rate_limits_share_pct"] = rate_limits_share_pct

    return await raw_api.request(
        client,
        "put",
        CLIENT_CONFIG_PATH,
        params={"account_name": account},
        json=changes,
        unsupported=ApiServerSettingsUnsupported,
    )
