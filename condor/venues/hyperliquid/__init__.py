"""Hyperliquid venue package (§6.2b): perp + spot + HIP-4 outcome markets.

Deployments are separate venue ids (``hyperliquid`` = mainnet,
``hyperliquid-testnet`` = testnet); the credential shape is shared — an
agent (API-wallet) key that signs FOR the main ``account_address``, so the
address is a credential component verified live by the read-only probe.
"""

from __future__ import annotations

from condor.accounts.model import normalize_address
from condor.accounts.onboarding import OnboardingError
from condor.venues.spec import CredField, VenueSpec, field_value

_NETWORKS = {"hyperliquid": "mainnet", "hyperliquid-testnet": "testnet"}


# -- contract callables --------------------------------------------------------


def derive_custody(credentials: dict) -> str:
    """Custody = the submitted ``account_address`` (EVM shape, normalized
    lowercase) — the ``agent_private_key`` signs FOR it, so the address is a
    credential component, verified live by the read-only account probe."""
    from eth_account import Account

    agent_key = credentials.get("agent_private_key")
    if not agent_key:
        raise OnboardingError("hyperliquid onboarding needs agent_private_key")
    try:
        Account.from_key(agent_key)
    except Exception as e:
        raise OnboardingError(f"agent_private_key does not parse: {e}") from None
    account = credentials.get("account_address")
    if not account:
        raise OnboardingError(
            "hyperliquid onboarding needs account_address — the account "
            "the agent key signs FOR (verified by the read-only probe)"
        )
    try:
        return normalize_address(str(account), "evm")
    except ValueError as e:
        raise OnboardingError(f"account_address: {e}") from None


def probe(venue_id: str, ref, credentials: dict) -> None:
    """Read-only user_state query proving the agent key works for the account."""
    import asyncio

    from condor.venues.hyperliquid.client import HyperliquidClient

    client = HyperliquidClient(
        credentials["agent_private_key"],
        ref.custody_address,
        network=_NETWORKS[venue_id],
    )
    asyncio.run(client.account())


def make_connector(instrument: str, credentials: dict):
    """One client per product, all built from the same agent creds:
    perp → HyperliquidClient, spot → HyperliquidSpotClient,
    pred → HyperliquidOutcomeClient."""
    if instrument == "perp":
        from condor.venues.hyperliquid.client import HyperliquidClient as cls
    elif instrument == "spot":
        from condor.venues.hyperliquid.spot import HyperliquidSpotClient as cls
    elif instrument == "pred":
        from condor.venues.hyperliquid.outcome import HyperliquidOutcomeClient as cls
    else:
        raise ValueError(f"hyperliquid does not support instrument {instrument!r}")
    agent_key = credentials.get("agent_private_key")
    if not agent_key:
        raise RuntimeError(
            f"hyperliquid account {credentials.get('custody_address')} has no "
            "agent_private_key — re-onboard it"
        )
    return cls(agent_key, credentials["custody_address"], credentials["network"])


def normalize_instrument(cfg_or_str) -> str | None:
    """Canonical instrument id: coins/pair symbols uppercased (HL coins are
    case-insensitive aliases of one market); outcome market ids verbatim."""
    if isinstance(cfg_or_str, str):
        return cfg_or_str.upper()
    coin = field_value(cfg_or_str, "coin")
    if coin:
        return str(coin).upper()
    base = field_value(cfg_or_str, "base_token")
    quote = field_value(cfg_or_str, "quote_token")
    if base and quote:
        return f"{base}-{quote}".upper()
    market = field_value(cfg_or_str, "market")
    if market:
        return str(market)
    return None


def _spot_adapter(connector, cfg):
    from condor.executors.adapters import SpotAdapter

    return SpotAdapter(connector, cfg)


def _perp_adapter(connector, cfg):
    from condor.venues.hyperliquid.adapters import PerpAdapter

    return PerpAdapter(connector, cfg)


def _pred_adapter(connector, cfg):
    from condor.venues.hyperliquid.adapters import HyperliquidPredAdapter

    return HyperliquidPredAdapter(connector, cfg)


VENUE = VenueSpec(
    venue_ids=("hyperliquid", "hyperliquid-testnet"),
    address_style="evm",
    networks=_NETWORKS,
    credential_fields=(
        CredField("agent_private_key", sealed=True, description="Agent (API-wallet) private key that signs for the account"),
        CredField("account_address", sealed=False, description="Main account address the agent key signs FOR (custody)"),
    ),
    adapter_factories={
        "spot": _spot_adapter,
        "perp": _perp_adapter,
        "pred": _pred_adapter,
    },
    make_connector=make_connector,
    derive_custody=derive_custody,
    probe=probe,
    normalize_instrument=normalize_instrument,
)
