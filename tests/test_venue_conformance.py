"""Venue-package conformance suite (§6.2b / §11).

One reusable conformance check runs against EVERY package loaded from
``condor/venues/`` (parametrized): the VENUE contract fields are present and
callable, every claimed venue_id resolves in the registry built from the
packages, credential fields carry sealed flags, and ``normalize_instrument``
collapses known aliases (SOL vs WSOL on solana, coin case on hyperliquid,
token_id passthrough on polymarket).

Plus the §11 acceptance vehicle: a TOY venue package written into a tmp dir
and picked up by the loader — registered at startup, passing the same
conformance checks, and run end-to-end (create → poll → cancel → custody
derivation → probe, including sealed onboarding into an AccountStore) with
ZERO core edits.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from decimal import Decimal

import pytest

from condor.accounts import onboarding
from condor.accounts.registry import UnknownVenueError, default_registry
from condor.accounts.store import AccountStore
from condor.venues.registry import (
    VenueContractError,
    load_venue_packages,
    venue_spec,
)
from condor.venues.spec import INSTRUMENTS, CredField, VenueSpec

BUILTIN_VENUE_IDS = sorted(load_venue_packages())


# -- the reusable conformance check (every package must pass it) ---------------


def check_conformance(venue_id: str, spec: VenueSpec) -> None:
    # Contract fields present, typed, and callable.
    assert isinstance(spec, VenueSpec)
    assert venue_id in spec.venue_ids
    assert spec.address_style in ("evm", "verbatim")
    assert spec.credential_fields, "a venue must declare its onboarding fields"
    for field in spec.credential_fields:
        assert isinstance(field, CredField)
        assert field.name
        assert isinstance(field.sealed, bool)
    assert any(f.sealed for f in spec.credential_fields), (
        "a venue must seal at least its signing credential"
    )
    assert spec.adapter_factories, "a venue must claim at least one instrument"
    for instrument, factory in spec.adapter_factories.items():
        assert instrument in INSTRUMENTS
        assert callable(factory)
    for name in ("make_connector", "derive_custody", "probe", "normalize_instrument"):
        assert callable(getattr(spec, name))
    # Every claimed venue_id resolves in the registry built from the packages,
    # with the package's own normalization style and network.
    registry = default_registry()
    for vid in spec.venue_ids:
        assert registry.is_registered(vid)
        venue = registry.get(vid)
        assert venue.address_style == spec.address_style
        assert venue.network == spec.networks[vid]
        assert venue_spec(vid) is spec


@pytest.mark.parametrize("venue_id", BUILTIN_VENUE_IDS)
def test_builtin_package_conforms(venue_id):
    check_conformance(venue_id, venue_spec(venue_id))


def test_unregistered_venue_id_errors():
    with pytest.raises(UnknownVenueError, match="unregistered venue_id"):
        venue_spec("binance")
    from condor.executors.adapters import make_adapter

    with pytest.raises(UnknownVenueError):
        make_adapter("spot", "binance", object(), object())


# -- normalize_instrument: alias collapse per venue -----------------------------


def test_solana_normalizes_sol_wsol_to_one_mint():
    from condor.venues.solana.connector import WSOL_MINT

    normalize = venue_spec("solana").normalize_instrument
    canonical = normalize("SOL-USDC")
    assert canonical == normalize("WSOL-USDC") == normalize("wsol-USDC")
    assert canonical.startswith(WSOL_MINT)  # symbols collapse to the mint
    assert normalize(f"{WSOL_MINT}-USDC") == canonical  # the mint IS canonical
    # Unknown mints pass through verbatim (base58 is case-sensitive).
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    assert normalize(f"{mint}-USDC").startswith(mint)


def test_solana_config_and_string_forms_agree():
    from condor.executors.order import OrderSpotConfig

    normalize = venue_spec("solana").normalize_instrument

    def cfg(base):
        return OrderSpotConfig(
            chain_network="solana-mainnet-beta",
            wallet_address="w",
            base_token=base,
            quote_token="USDC",
            amount=Decimal("1"),
            side="BUY",
            notional_quote=Decimal("10"),
        )

    assert cfg("SOL").instrument_id() == cfg("WSOL").instrument_id() == normalize("SOL-USDC")


def test_hyperliquid_normalizes_coin_case():
    from condor.executors.order import OrderPerpConfig

    normalize = venue_spec("hyperliquid").normalize_instrument
    assert normalize("btc") == normalize("BTC") == "BTC"
    cfg = OrderPerpConfig(coin="btc", side="LONG", notional_quote=Decimal("10"))
    assert cfg.instrument_id() == "BTC"
    # testnet is a different venue id but shares the package's normalization
    assert venue_spec("hyperliquid-testnet").normalize_instrument("eth") == "ETH"


def test_polymarket_token_id_is_canonical_passthrough():
    from condor.executors.order import OrderPredConfig

    token = "71321045679252212594626385532706912750332728571942532289631379312455583992563"
    normalize = venue_spec("polymarket").normalize_instrument
    assert normalize(token) == token
    cfg = OrderPredConfig(market=token, amount_quote=Decimal("5"))
    assert cfg.instrument_id() == token


def test_unregistered_venue_keeps_generic_instrument_fallback():
    from condor.executors.order import OrderSpotConfig

    cfg = OrderSpotConfig(
        venue="somedex",  # not a registered venue package
        chain_network="somechain-mainnet",
        wallet_address="w",
        base_token="AAA",
        quote_token="BBB",
        amount=Decimal("1"),
        side="BUY",
        notional_quote=Decimal("10"),
    )
    assert cfg.instrument_id() == "AAA-BBB"


# -- the §11 toy venue package: one folder, zero core edits ---------------------

TOY_PACKAGE = '''
"""Toy venue package — the §11 conformance fixture. Implements the full
contract against a fake in-memory venue; nothing in core knows it exists."""

from condor.executors.adapters import InstrumentAdapter
from condor.executors.orders import OrderStatus
from condor.venues.spec import CredField, VenueSpec, field_value

VALID_TOKENS = {"sesame"}


class ToyClient:
    """Fake in-memory venue: an order book of oid -> status."""

    def __init__(self, custody_address):
        self.custody_address = custody_address
        self.orders = {}
        self._seq = 0

    async def place(self, market, side, qty):
        self._seq += 1
        oid = f"toy-{self._seq}"
        self.orders[oid] = "open"
        return oid

    async def status(self, oid):
        return self.orders[oid]

    async def cancel(self, oid):
        self.orders[oid] = "canceled"


class ToyOrderAdapter(InstrumentAdapter):
    async def place(self, state) -> None:
        from condor.executors.order import OrderStates

        oid = await self.c.place(
            f"{self.cfg.base_token}-{self.cfg.quote_token}",
            self.cfg.side,
            self.cfg.amount,
        )
        state.open_ref = oid
        state.state = OrderStates.RESTING
        self.record_landed_order(
            state,
            venue_order_id=oid,
            role=self._leg_role(),
            side=self.cfg.side.lower(),
            requested_qty=self.cfg.amount,
        )

    async def poll(self, state) -> None:
        from condor.executors.order import OrderStates

        status = await self.c.status(state.open_ref)
        if status == "open":
            return
        if status == "canceled":
            state.state = OrderStates.FAILED
            self.update_landed_order(state, state.open_ref, status=OrderStatus.CANCELED)
        else:
            state.state = OrderStates.DONE
            self.update_landed_order(state, state.open_ref, status=OrderStatus.FILLED)

    async def cancel(self, state) -> None:
        if state.open_ref:
            self.update_landed_order(
                state, state.open_ref, status=OrderStatus.CANCEL_PENDING
            )
            await self.c.cancel(state.open_ref)


def derive_custody(credentials):
    token = credentials.get("api_token")
    if not token:
        raise ValueError("toy onboarding needs api_token")
    return f"toy-{token}"


def probe(venue_id, ref, credentials):
    if credentials.get("api_token") not in VALID_TOKENS:
        raise RuntimeError("toy venue rejected the api_token")


def make_connector(instrument, credentials):
    if instrument != "spot":
        raise ValueError(f"toy does not support instrument {instrument!r}")
    return ToyClient(credentials.get("custody_address"))


def normalize_instrument(cfg_or_str):
    if isinstance(cfg_or_str, str):
        return cfg_or_str.upper()
    base = field_value(cfg_or_str, "base_token")
    quote = field_value(cfg_or_str, "quote_token")
    if base and quote:
        return f"{base}-{quote}".upper()
    return None


VENUE = VenueSpec(
    venue_ids=("toy",),
    address_style="verbatim",
    networks={"toy": "mainnet"},
    credential_fields=(
        CredField("api_token", sealed=True, description="toy API token"),
        CredField("handle", sealed=False, description="display handle"),
    ),
    adapter_factories={"spot": lambda connector, cfg: ToyOrderAdapter(connector, cfg)},
    make_connector=make_connector,
    derive_custody=derive_custody,
    probe=probe,
    normalize_instrument=normalize_instrument,
)
'''


@pytest.fixture()
def toy_venue(tmp_path):
    """Drop a `_toy` package folder where the loader scans and refresh — the
    §11 'adding a venue = adding one folder' path, zero core edits."""
    import condor.venues as venues_pkg

    pkg_root = tmp_path / "toy_venues"
    (pkg_root / "_toy").mkdir(parents=True)
    (pkg_root / "_toy" / "__init__.py").write_text(TOY_PACKAGE)
    venues_pkg.__path__.append(str(pkg_root))
    try:
        yield load_venue_packages(refresh=True)["toy"]
    finally:
        venues_pkg.__path__.remove(str(pkg_root))
        sys.modules.pop("condor.venues._toy", None)
        load_venue_packages(refresh=True)
        assert "toy" not in load_venue_packages()


def test_toy_package_registers_and_conforms(toy_venue):
    check_conformance("toy", toy_venue)
    assert default_registry().is_registered("toy")
    # aliases collapse through the toy's own normalization, consulted by
    # ExecutorConfig.instrument_id with zero core edits
    from condor.executors.order import OrderSpotConfig

    cfg = OrderSpotConfig(
        venue="toy",
        chain_network="toy-mainnet",
        wallet_address="w",
        base_token="foo",
        quote_token="bar",
        amount=Decimal("1"),
        side="BUY",
        notional_quote=Decimal("10"),
    )
    assert cfg.instrument_id() == "FOO-BAR"


def test_toy_venue_end_to_end(toy_venue, tmp_path, monkeypatch):
    """§11 acceptance: create → poll → cancel → custody derivation → probe,
    all through the CORE dispatch paths (onboarding, make_adapter), with the
    toy package as the only venue-specific code."""
    spec = toy_venue

    # -- custody derivation + probe through onboarding's registry dispatch --
    creds = {"api_token": "sesame", "handle": "tester"}
    ref = onboarding.derive_custody("toy", creds)
    assert (ref.venue_id, ref.custody_address) == ("toy", "toy-sesame")
    onboarding.default_probe("toy", ref, creds)  # read-only, passes
    with pytest.raises(onboarding.ProbeError, match="toy read-only probe failed"):
        onboarding.default_probe("toy", ref, {"api_token": "wrong"})

    # -- full onboarding: sealed persistence into a structured AccountStore --
    monkeypatch.setenv(
        "CONDOR_SECRETS_KEY", base64.b64encode(b"k" * 32).decode()
    )
    store = AccountStore(path=tmp_path / "venues.json")
    stored = onboarding.onboard_account(
        "toy", creds, name="toy-main", store=store, make_default=True
    )
    assert stored == ref
    entry = store.load()["toy"]["accounts"][ref.custody_address]
    assert entry["api_token"].startswith("enc:v1:")  # sealed per CredField
    assert entry["handle"] == "tester"  # plaintext per CredField
    assert store.resolve("toy") == ref  # venue default resolves

    # -- create → poll → cancel through core make_adapter dispatch --
    from condor.executors.adapters import make_adapter
    from condor.executors.order import OrderSpotConfig, OrderState, OrderStates
    from condor.executors.orders import OrderStatus

    cfg = OrderSpotConfig(
        venue="toy",
        chain_network="toy-mainnet",
        wallet_address=ref.custody_address,
        base_token="FOO",
        quote_token="BAR",
        amount=Decimal("2"),
        side="BUY",
        notional_quote=Decimal("10"),
    )
    client = spec.make_connector(
        "spot", {**creds, "custody_address": ref.custody_address}
    )
    adapter = make_adapter("spot", "toy", client, cfg)
    state = OrderState()
    adapter.bind(state)

    asyncio.run(adapter.place(state))  # create
    oid = state.open_ref
    assert oid and state.state == OrderStates.RESTING
    assert state.orders[0].status == OrderStatus.OPEN

    asyncio.run(adapter.poll(state))  # poll: still resting on the book
    assert state.state == OrderStates.RESTING

    asyncio.run(adapter.cancel(state))  # cancel
    assert state.orders[0].status == OrderStatus.CANCEL_PENDING
    assert client.orders[oid] == "canceled"

    asyncio.run(adapter.poll(state))  # poll confirms the terminal state
    assert state.state == OrderStates.FAILED
    assert state.orders[0].status == OrderStatus.CANCELED

    # the toy claims only spot — other instruments error through core dispatch
    with pytest.raises(ValueError, match="does not support instrument"):
        make_adapter("perp", "toy", client, cfg)


def test_broken_venue_package_fails_loud(tmp_path):
    """A package that does not implement the contract is a startup error,
    never a silent skip."""
    import condor.venues as venues_pkg

    pkg_root = tmp_path / "broken_venues"
    (pkg_root / "_broken").mkdir(parents=True)
    (pkg_root / "_broken" / "__init__.py").write_text("")  # no VENUE at all
    venues_pkg.__path__.append(str(pkg_root))
    try:
        with pytest.raises(VenueContractError, match="exports no VENUE"):
            load_venue_packages(refresh=True)
    finally:
        venues_pkg.__path__.remove(str(pkg_root))
        sys.modules.pop("condor.venues._broken", None)
        load_venue_packages(refresh=True)
    assert sorted(load_venue_packages()) == BUILTIN_VENUE_IDS
