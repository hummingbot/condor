"""Phase 3 acceptance (§6.2/§11): creation capabilities, idempotent creates,
and instrument leases."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from condor.accounts.model import AccountRef
from condor.executors import ops
from condor.executors.capabilities import (
    CapabilityError,
    CapabilityRegistry,
    get_capability_registry,
)
from condor.executors.leases import LeaseConflict, LeaseManager
from condor.executors.log import ExecutorLog


class _QuoteGateway:
    def __init__(self):
        self.calls = 0

    async def quote_swap(self, **kw):
        self.calls += 1
        return {"price": 100.0}


def _runtime(tmp_path):
    store = ExecutorLog(tmp_path)
    gateway = _QuoteGateway()
    created: list[str] = []

    def fake_create(config, executor_id=None, *, connector=None):
        eid = executor_id or "gen_1"
        created.append(eid)
        # persist an opener so replay checks can read the record back
        executor = SimpleNamespace(
            id=eid,
            status=SimpleNamespace(value="PENDING"),
            close_reason=None,
            config=config,
            state_model=lambda: SimpleNamespace(model_dump_json=lambda: "{}"),
        )
        store._append(
            config.agent_slug or "_manual",
            {
                "ts": 1.0,
                "event": "opened",
                "id": eid,
                "type": config.type,
                "agent_slug": config.agent_slug,
                "agent_id": config.agent_id,
                "status": "PENDING",
                "config": __import__("json").loads(config.model_dump_json()),
                "state": {},
            },
        )
        return eid

    rt = SimpleNamespace(
        store=store,
        leases=LeaseManager(),
        connector_for_spec=lambda type_, venue, account_ref=None: gateway,
        create_executor=fake_create,
        list_running=lambda: [],
        gateway=gateway,
        created=created,
    )
    return rt


SWAP_CONFIG = {
    "chain_network": "solana-mainnet-beta",
    "wallet_address": "7fEsqLYz3Zr7SNoBn9GnCTHU5V2Ta8DFXECfWQEmYVWk",
    "base_token": "SOL",
    "quote_token": "USDC",
    "amount": "0.5",
    "side": "SELL",
    "notional_quote": "50",
}
SOL_REF = AccountRef("solana", SWAP_CONFIG["wallet_address"])


def _create(rt, cap_id, executor_id="", config=None):
    return asyncio.run(
        ops.create(
            rt,
            type="order_spot",
            config=dict(config or SWAP_CONFIG),
            capability=cap_id,
            executor_id=executor_id,
        )
    )


# -- capability rejection matrix (§11) --


def test_omitted_capability_rejected(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ops.ExecutorOpError) as ei:
        _create(rt, "")
    assert ei.value.status == 403
    assert "capability" in ei.value.message


def test_unknown_and_expired_capability_rejected(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ops.ExecutorOpError) as ei:
        _create(rt, "bogus-id")
    assert ei.value.status == 403

    reg = get_capability_registry()
    cap = reg.mint_run_capability("acme", "acme_9", account_ref=SOL_REF)
    reg.revoke_run("acme_9")  # run ended
    with pytest.raises(ops.ExecutorOpError) as ei:
        _create(rt, cap.id)
    assert ei.value.status == 403


def test_agent_capability_attributes_from_server_side_entry(tmp_path):
    rt = _runtime(tmp_path)
    cap = get_capability_registry().mint_run_capability(
        "acme", "acme_3", account_ref=SOL_REF
    )
    result = _create(rt, cap.id, executor_id="e-attr")
    assert result["origin"] == "agent"
    rec = rt.store.load("e-attr")
    assert rec.agent_slug == "acme"
    assert rec.agent_id == "acme_3"
    get_capability_registry().revoke_run("acme_3")


def test_condor_direct_not_risk_capped_but_leased(tmp_path):
    reg = CapabilityRegistry()
    reg.write_direct_token()
    token = __import__(
        "condor.executors.capabilities", fromlist=["DIRECT_TOKEN_PATH"]
    ).DIRECT_TOKEN_PATH.read_text()
    cap = reg.register_direct(token, "conn-1")
    assert cap.origin == "condor"
    # connection close revokes
    reg.revoke_connection("conn-1")
    with pytest.raises(CapabilityError):
        reg.resolve(cap.id)


def test_agent_session_cannot_downgrade_to_direct():
    reg = CapabilityRegistry()
    reg.write_direct_token()
    from condor.executors.capabilities import DIRECT_TOKEN_PATH

    token = DIRECT_TOKEN_PATH.read_text()
    run_cap = reg.mint_run_capability("acme", "acme_1", account_ref=SOL_REF)
    with pytest.raises(CapabilityError, match="cannot register as condor-direct"):
        reg.register_direct(token, "conn-2", session_capability=run_cap.id)
    # altered token also rejected
    with pytest.raises(CapabilityError, match="invalid direct-session token"):
        reg.register_direct(token + "x", "conn-3")


# -- idempotent create (§11 Phase 3) --


def test_duplicate_create_same_id_same_hash_replays(tmp_path):
    rt = _runtime(tmp_path)
    cap = get_capability_registry().mint_run_capability(
        "acme", "acme_4", account_ref=SOL_REF
    )
    r1 = _create(rt, cap.id, executor_id="e-dup")
    r2 = _create(rt, cap.id, executor_id="e-dup")
    assert r1["id"] == r2["id"] == "e-dup"
    assert r2.get("replayed") is True
    # single venue-side create
    assert rt.created.count("e-dup") == 1
    get_capability_registry().revoke_run("acme_4")


def test_same_id_different_hash_rejected(tmp_path):
    rt = _runtime(tmp_path)
    cap = get_capability_registry().mint_run_capability(
        "acme", "acme_5", account_ref=SOL_REF
    )
    _create(rt, cap.id, executor_id="e-rebind")
    other = dict(SWAP_CONFIG, amount="0.9", notional_quote="90")
    with pytest.raises(ops.ExecutorOpError) as ei:
        _create(rt, cap.id, executor_id="e-rebind", config=other)
    assert ei.value.status == 409
    assert "rebound" in ei.value.message or "different" in ei.value.message
    get_capability_registry().revoke_run("acme_5")


def test_same_id_same_hash_different_owner_is_rejected(tmp_path):
    rt = _runtime(tmp_path)
    reg = get_capability_registry()
    owner_a = reg.mint_run_capability("acme", "run-a", account_ref=SOL_REF)
    owner_b = reg.mint_run_capability("other", "run-b", account_ref=SOL_REF)
    _create(rt, owner_a.id, executor_id="shared-id")
    with pytest.raises(ops.ExecutorOpError) as ei:
        _create(rt, owner_b.id, executor_id="shared-id")
    assert ei.value.status == 409
    assert "owner" in ei.value.message
    reg.revoke_run("run-a")
    reg.revoke_run("run-b")


def test_two_accounts_same_venue_bind_and_lease_independently(tmp_path):
    from condor.executors.wallets import account_store

    alt_ref = AccountRef("solana", "AltSolanaCustody111111111111111111111111111")
    account_store().upsert_account("solana", alt_ref.custody_address, {"name": "alt"})
    reg = get_capability_registry()
    main = reg.mint_run_capability("main-agent", "main-run", account_ref=SOL_REF)
    alt = reg.mint_run_capability("alt-agent", "alt-run", account_ref=alt_ref)
    rt = _runtime(tmp_path)

    first = _create(rt, main.id, executor_id="main-order")
    second = _create(rt, alt.id, executor_id="alt-order")
    assert first["id"] == "main-order" and second["id"] == "alt-order"
    assert rt.store.load("main-order").config["account_ref"] == SOL_REF.as_dict()
    assert rt.store.load("alt-order").config["account_ref"] == alt_ref.as_dict()
    leases = rt.leases.snapshot()
    assert {key[0]: owner for key, owner in leases.items()} == {
        str(SOL_REF): "main-run",
        str(alt_ref): "alt-run",
    }
    assert len({key[1] for key in leases}) == 1
    reg.revoke_run("main-run")
    reg.revoke_run("alt-run")


# -- leases (§11: two concurrent actors on one (AccountRef, instrument)) --


def test_lease_second_actor_rejected(tmp_path):
    rt = _runtime(tmp_path)
    reg = get_capability_registry()
    cap_a = reg.mint_run_capability("acme", "acme_6", account_ref=SOL_REF)
    cap_b = reg.mint_run_capability("other", "other_1", account_ref=SOL_REF)
    _create(rt, cap_a.id, executor_id="e-lease-a")
    with pytest.raises(ops.ExecutorOpError) as ei:
        _create(rt, cap_b.id, executor_id="e-lease-b")
    assert ei.value.status == 409
    assert "lease" in ei.value.message
    # Same actor may hold the lease through several executors.
    r = _create(rt, cap_a.id, executor_id="e-lease-a2")
    assert r["id"] == "e-lease-a2"
    reg.revoke_run("acme_6")
    reg.revoke_run("other_1")


def test_lease_manager_release_semantics():
    lm = LeaseManager()
    ref = AccountRef("solana", "_default")
    lm.acquire(ref, "SOL-USDC", owner="a_1", executor_id="e1")
    lm.acquire(ref, "SOL-USDC", owner="a_1", executor_id="e2")
    with pytest.raises(LeaseConflict):
        lm.acquire(ref, "SOL-USDC", owner="b_1", executor_id="e3")
    lm.release(ref, "SOL-USDC", executor_id="e1")
    assert lm.holder(ref, "SOL-USDC") == "a_1"  # e2 still holds
    lm.release(ref, "SOL-USDC", executor_id="e2")
    assert lm.holder(ref, "SOL-USDC") is None
    lm.acquire(ref, "SOL-USDC", owner="b_1", executor_id="e3")  # now free
