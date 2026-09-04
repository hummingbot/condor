"""One real on-chain executor, end to end, through every Condor surface it touches.

Skipped unless the environment names a Hummingbot API (``HUMMINGBOT_API_URL``)
and an Aomi bearer (``AOMI_TOKEN``). The commit test additionally needs
``AOMI_E2E_WALLET`` — the Aomi wallet's own address, so the transaction is a
0-value self-transfer on Base: the smallest real transaction, proving the
wallet, the chain, the signing mode and the commit path without moving funds.

Precondition on the API side: its process must carry ``AOMI_URL``/``AOMI_TOKEN``
and the wallet must be in ``server_auto`` signing mode, else the executor ends
``FAILED`` with ``reason: awaiting_wallet`` and the assertion says so.
"""

from __future__ import annotations

import asyncio
import os
import re
import time

import pytest

pytestmark = pytest.mark.e2e

API_URL = os.environ.get("HUMMINGBOT_API_URL", "").strip()
AOMI_TOKEN = os.environ.get("AOMI_TOKEN", "").strip()
WALLET = os.environ.get("AOMI_E2E_WALLET", "").strip()
TIMEOUT = float(os.environ.get("E2E_EXECUTOR_TIMEOUT", "300"))
POLL_SECONDS = 3.0
CHAIN_ID = 8453
AGENT_ID = "e2e-agent"
REQUIRE_COMMIT = os.environ.get("AOMI_E2E_REQUIRE_COMMIT", "").strip() not in (
    "",
    "0",
    "false",
)

if not (API_URL and AOMI_TOKEN):
    pytest.skip(
        "set HUMMINGBOT_API_URL and AOMI_TOKEN to run the on-chain e2e",
        allow_module_level=True,
    )

TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


async def _client():
    """The way ``config_manager.py`` builds one, from the environment."""
    from aiohttp import ClientTimeout
    from hummingbot_api_client import HummingbotAPIClient

    client = HummingbotAPIClient(
        base_url=API_URL.rstrip("/"),
        username=os.environ.get("HUMMINGBOT_USERNAME", "admin"),
        password=os.environ.get("HUMMINGBOT_PASSWORD", "admin"),
        timeout=ClientTimeout(total=60, connect=10),
    )
    await client.init()
    return client


def _run(coro):
    return asyncio.run(coro)


async def _wait_terminated(client, executor_id: str) -> dict:
    deadline = time.monotonic() + TIMEOUT
    last: dict = {}
    while time.monotonic() < deadline:
        last = await client.executors.get_executor(executor_id)
        if str(last.get("status") or "").upper() == "TERMINATED":
            return last
        await asyncio.sleep(POLL_SECONDS)
    pytest.fail(
        f"executor {executor_id} did not terminate within {TIMEOUT:.0f}s "
        f"(last status={last.get('status')!r}, custom_info={last.get('custom_info')!r})"
    )


def test_the_api_offers_the_onchain_executor():
    async def _go():
        client = await _client()
        try:
            types = await client.executors.get_available_executor_types()
        finally:
            await client.close()
        return types

    types = _run(_go())
    assert "onchain_executor" in str(types), types


def test_show_schema_renders_the_onchain_fields():
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors

    async def _go():
        client = await _client()
        try:
            return await manage_executors(
                client, ManageExecutorsRequest(executor_type="onchain_executor")
            )
        finally:
            await client.close()

    result = _run(_go())
    assert "error" not in result, result
    assert "chain_id" in result["formatted_output"]


@pytest.mark.skipif(not WALLET, reason="set AOMI_E2E_WALLET (the Aomi wallet address)")
def test_self_transfer_commits_and_surfaces_everywhere():
    from condor.agents.providers.defi_positions import DefiPositionsProvider
    from condor.routine_store import WebRoutineContext
    from mcp_servers.hummingbot_api.schemas import ManageExecutorsRequest
    from mcp_servers.hummingbot_api.tools.executors import manage_executors
    from tests.conftest import load_shared_routine

    async def _go():
        client = await _client()
        try:
            created = await manage_executors(
                client,
                ManageExecutorsRequest(
                    action="create",
                    executor_type="onchain_executor",
                    executor_config={
                        "controller_id": AGENT_ID,
                        "chain_id": CHAIN_ID,
                        "mode": "calls",
                        "calls": [
                            {
                                "to": WALLET,
                                "description": "condor e2e self-transfer",
                                "data": {"signature": "", "args": [], "raw": ""},
                                "value": "0",
                            }
                        ],
                        "notional_quote": 1,
                    },
                ),
            )
            assert "error" not in created, created
            executor_id = created.get("executor_id") or re.search(
                r"Executor ID: (\S+)", created["formatted_output"]
            ).group(1)
            print(f"\n[e2e] executor id: {executor_id}")

            final = await _wait_terminated(client, executor_id)
            info = final.get("custom_info") or {}
            close_type = final.get("close_type")
            error = info.get("error") or {}

            # The provider must surface the executor whatever its outcome.
            provider = await DefiPositionsProvider().execute(
                client,
                {"chain_id": CHAIN_ID, "wallet_address": WALLET},
                agent_id=AGENT_ID,
            )
            assert str(executor_id)[:12] in provider.summary, provider.summary
            assert str(close_type) in provider.summary, provider.summary
            print(f"[e2e] provider:\n{provider.summary}")

            if (
                close_type == "FAILED"
                and error.get("backend_code") == "pipeline_commit_failed"
                and not REQUIRE_COMMIT
            ):
                pytest.skip(
                    "staging could not sign/broadcast for this wallet: "
                    f"{error.get('backend_code')} (request {error.get('request_id')}); "
                    "the executor recorded the failure; set AOMI_E2E_REQUIRE_COMMIT=1 to fail instead"
                )
            assert (
                close_type == "COMPLETED"
            ), f"close_type={close_type!r} error={error!r}"
            hashes = info.get("tx_hashes") or []
            assert hashes and TX_HASH.match(str(hashes[0])), info
            print(f"[e2e] tx hash: {hashes[0]}")
        finally:
            await client.close()

        catalog = load_shared_routine("aomi_catalog")
        result = await catalog.run(
            catalog.Config(describe=False), WebRoutineContext(server_name="e2e")
        )
        text = result if isinstance(result, str) else result.text
        assert text.startswith("# Aomi Pipeline catalog"), text[:200]

    _run(_go())
