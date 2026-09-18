"""The crank: what keeps a vault's strategy running, and what it refuses to do.

One loop per server. Each pass reads the vaults from the chain — not from
Condor's records, which are a local convenience — and for each one decides
whether Condor should be running it, whether anything on chain is waiting for a
push, and whether a wind-down needs finishing.

**What the crank is not.** It is not a privileged party. Everything it does is
something the program already allows: the strategy ticks are signed by a
delegate the creator installed and can remove, `collect_seed` and
`collect_leftover` are permissionless and pay the caller nothing, and the one
instruction restricted to Condor — `finalize_wind_down` — is restricted because
a stranger could otherwise strand a position behind a removed delegate, not
because Condor is owed anything. A creator who self-hosts runs this same loop
against their own vault and needs nothing from Condor at all.

**Start checks, every pass.** A vault only ticks when all of these hold, and
each one has a failure it is there to prevent:

* the chain says `Running` — the creator's pause is not advisory;
* the stored version, config hash and fee equal the chain's — otherwise Condor
  would be running parameters the creator did not sign, which is the whole point
  of putting a hash on chain;
* Condor's own scan passed *this version* — a private run policy, not an
  attestation (nothing on chain says Condor reviewed anything);
* the vault still names the delegate Condor holds — a replaced delegate means
  the creator has taken the wallet back, and the crank must notice rather than
  fail transaction by transaction;
* exactly one live engine per vault — two would double every position.

A vault that fails a check is skipped with the reason logged once, not retried
into a hot loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from condor import vault_store
from condor.gateway_client import VaultGateway
from condor.vault_config import config_hash

log = logging.getLogger(__name__)

#: How often a server's vaults are reconsidered. Slow on purpose: everything
#: urgent is driven by the engine's own tick, and this loop only decides which
#: engines should exist.
DEFAULT_INTERVAL_S = 30.0

#: The delegate pays for every transaction it signs, so it needs SOL of its own.
#: Below this the crank tops it up from the vault; above it, it leaves it alone.
DELEGATE_FLOOR_LAMPORTS = 20_000_000  # 0.02 SOL
DELEGATE_TOPUP_LAMPORTS = 50_000_000  # 0.05 SOL


class StartRefused(Exception):
    """A start check said no. The message is what the vault page shows."""


def check_can_run(record: dict[str, Any], chain: dict[str, Any]) -> None:
    """Raise :class:`StartRefused` unless Condor should be ticking this vault.

    Pure, so the rules can be read and tested without a chain, a Gateway or an
    engine — which is the only way a list like this stays honest.
    """
    state = chain.get("state")
    if state != "Running":
        raise StartRefused(f"the chain says {state}, not Running")

    if not chain.get("delegate"):
        raise StartRefused("no delegate is installed, so nothing can sign its trades")

    held = (record.get("delegate") or {}).get("address")
    if held and held != chain.get("delegate"):
        raise StartRefused(
            f"the installed delegate is {chain['delegate']}, not the one Condor holds "
            f"({held}) — this vault is being run by someone else"
        )

    pin = record.get("pin") or {}
    if not pin.get("config"):
        raise StartRefused("Condor has no config for this vault, only its hash")

    for key, label in (
        ("version", "version"),
        ("config_hash", "config hash"),
    ):
        stored, on_chain = pin.get(key), chain.get(key)
        if stored is not None and on_chain is not None and stored != on_chain:
            raise StartRefused(
                f"the stored {label} ({stored}) is not the chain's ({on_chain})"
            )

    # The hash is the commitment. Recomputing it here rather than trusting the
    # stored one is what makes "nobody can run a vault on parameters its creator
    # did not sign" true of Condor's own operators.
    recomputed = config_hash(pin["config"])
    if recomputed != chain.get("config_hash"):
        raise StartRefused(
            f"the stored config hashes to {recomputed[:16]}…, and the chain carries "
            f"{str(chain.get('config_hash'))[:16]}… — it is not the config this vault signed"
        )

    scan = pin.get("scan")
    if not scan or not scan.get("passed"):
        raise StartRefused(
            "Condor's own check has not passed for this version"
            + (
                f": {'; '.join(scan['findings'])}"
                if scan and scan.get("findings")
                else ""
            )
        )


class VaultCrank:
    """One server's vaults, reconsidered on an interval."""

    def __init__(
        self,
        server: str,
        network: str = "mainnet-beta",
        interval_s: float = DEFAULT_INTERVAL_S,
    ):
        self.server = server
        self.network = network
        self.interval_s = interval_s
        self._task: Optional[asyncio.Task] = None
        # Which agent run is this vault's, by agent id. The engines themselves
        # live in the loop supervisor, which every engine registers with on
        # start — a second registry here would be a second answer to "what is
        # running", and the one that leaks.
        self._agent_ids: dict[str, str] = {}
        # One log line per reason per vault, rather than one per pass: a vault
        # that is paused for a week should not write 20,000 identical lines.
        self._last_refusal: dict[str, str] = {}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._loop(), name=f"vault-crank:{self.server}"
        )
        log.info("vault crank started for %s", self.server)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for account in list(self._agent_ids):
            await self._stop_engine(account)
        log.info("vault crank stopped for %s", self.server)

    async def _loop(self) -> None:
        while True:
            try:
                await self.pass_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # One vault's bad pass must not take the loop down; the next
                # pass is thirty seconds away and the chain has not moved.
                log.exception("vault crank pass failed for %s", self.server)
            await asyncio.sleep(self.interval_s)

    async def pass_once(self) -> None:
        gw = await VaultGateway.for_server(_config_manager(), self.server, self.network)

        on_chain = {v["account"]: v for v in await gw.list_vaults()}
        # Keyed by account, carrying the user whose store it came from: the
        # engine needs that user to get an API client, and deriving it from the
        # store the record was read out of beats writing it into the record,
        # where it could disagree with the directory it lives in.
        records = {
            account: (user, record)
            for user, account, record in vault_store.iter_all_users()
            if record.get("server") == self.server
        }

        for account, chain in on_chain.items():
            entry = records.get(account)
            if entry is None:
                # Someone else's vault on the same chain. Condor reads it and
                # leaves it alone: it holds no config for it and no delegate on
                # it, and a vault is nobody's to run but its creator's.
                continue
            user, record = entry
            try:
                await self._tend(gw, account, user, record, _snake(chain))
            except Exception:
                log.exception("vault %s could not be tended", account)

        # An engine whose vault has gone (a re-forked chain, a deleted record)
        # has nothing left to trade for.
        for account in list(self._agent_ids):
            if account not in on_chain:
                await self._stop_engine(account)

    def engine_for(self, account: str):
        """This vault's running engine, or None — read from the supervisor,
        which is where every engine registers itself on start."""
        agent_id = self._agent_ids.get(account)
        return _supervisor().get(agent_id) if agent_id else None

    async def _tend(
        self, gw: VaultGateway, account: str, user: str, record: dict, chain: dict
    ) -> None:
        state = chain.get("state")

        if state == "WindingDown":
            await self._stop_engine(account)
            await self._wind_down(gw, account, record, chain)
            return

        if state == "Redeemable":
            await self._stop_engine(account)
            return

        # A migrated pool has two things waiting for a push, and both are
        # permissionless: the seed (80% of the raise) and the unsold supply.
        # Condor pays the gas because it is there, not because it must be.
        if chain.get("tokenized"):
            await self._collect(gw, account, chain)

        try:
            check_can_run(record, chain)
        except StartRefused as refusal:
            await self._stop_engine(account)
            if self._last_refusal.get(account) != str(refusal):
                self._last_refusal[account] = str(refusal)
                log.info("not running vault %s: %s", account, refusal)
            return

        self._last_refusal.pop(account, None)
        await self._ensure_engine(account, user, record, chain)
        await self._top_up_delegate(gw, account, chain)

    async def _collect(self, gw: VaultGateway, account: str, chain: dict) -> None:
        """Push the two permissionless post-migration calls, if they are due.

        Both refuse on chain when there is nothing to do — DBC keeps a one-time
        flag for each — so this does not need to know whether it has already
        run; it needs only to not treat a refusal as an error.
        """
        pool_config = chain.get("dbc_config") or chain.get("config")
        if not pool_config:
            return
        for route in ("collect-seed", "collect-leftover"):
            try:
                result = await gw.execute(
                    route, {"vaultAccount": account, "dbcConfig": pool_config}
                )
                log.info(
                    "vault %s: %s landed (%s)", account, route, result.get("signature")
                )
            except Exception as e:
                # Expected on almost every pass: there is usually nothing to
                # collect. Debug, not warning — a log that cries wolf every
                # thirty seconds is a log nobody reads.
                log.debug("vault %s: %s not due (%s)", account, route, e)

    async def _wind_down(
        self, gw: VaultGateway, account: str, record: dict, chain: dict
    ) -> None:
        """Finalize once everything is in the quote asset.

        The conversion — close every position, swap every other balance to
        the quote asset — is `execute` calls the strategy's own executors
        make, and nothing here moves money: the wallet is the program's PDA,
        its quote account is what `redeem` pays from, and the program signs
        the payout itself. This only asks the program to check that the
        conversion happened and to stop the strategy for good.

        Deliberately not permissionless. A stranger's close-and-swap signed by
        the vault's own authority would need oracle-bounded prices to be safe
        from a pool priced against it. What *is* permissionless is what comes
        after: `redeem` pays out with no delegate, no administrator and no key
        able to decline.
        """
        try:
            result = await gw.execute("finalize-wind-down", {"vaultAccount": account})
        except Exception as e:
            # The usual reason is the honest one: a position is still open, so a
            # balance is still in the wrong asset. The program refuses, and the
            # next pass tries again once the positions are closed.
            log.debug("vault %s not ready to finalize (%s)", account, e)
            return
        log.info("vault %s wind-down finalized (%s)", account, result.get("signature"))

    async def _top_up_delegate(
        self, gw: VaultGateway, account: str, chain: dict
    ) -> None:
        """Keep the delegate solvent enough to sign.

        The delegate pays its own transaction fees and holds nothing else. A
        delegate that runs out of SOL stops the vault in a way that looks, from
        every surface, like the strategy failing.
        """
        delegate = chain.get("delegate")
        if not delegate:
            return
        try:
            balances = await gw.balances(delegate)
        except Exception as e:
            log.debug("could not read delegate balance for %s: %s", account, e)
            return
        lamports = _native_lamports(balances)
        if lamports is None or lamports >= DELEGATE_FLOOR_LAMPORTS:
            return
        log.info(
            "vault %s: delegate %s is below the floor (%s lamports); topping up",
            account,
            delegate,
            lamports,
        )
        try:
            # An ordinary transfer out of the wallet, signed by the wallet. No
            # vault instruction is needed or wanted: paying for gas is not a
            # privileged act, and the delegate is already allowed to spend.
            await gw.fund_delegate(account, DELEGATE_TOPUP_LAMPORTS)
        except Exception as e:
            log.warning("vault %s: could not top up the delegate: %s", account, e)

    # ── engines ───────────────────────────────────────────────────────────────

    async def _ensure_account(self, account: str, treasury: str) -> None:
        """One hummingbot-api account per vault, bound to that vault's wallet.

        Without the binding an account trades as Gateway's *default* wallet —
        one address for the whole instance — so two vaults running at once
        would be one wallet on chain and neither position would belong to the
        vault that opened it. Both calls are idempotent: creating an account
        that exists is an error this swallows, and binding an address that is
        already bound writes the same file.
        """
        name = _account_name(account)
        client = await _config_manager().get_client(self.server)
        try:
            await client.accounts.add_account(name)
            log.info("vault %s: created hummingbot-api account %s", account, name)
        except Exception as e:
            # Already there, which is the usual case after the first pass.
            log.debug("vault %s: account %s not created (%s)", account, name, e)
        # `_post` rather than a named method: the pinned client has no
        # per-account binding call yet — it gains one in the same PR as the
        # route (plan M5).
        await client.accounts._post(
            f"/accounts/{name}/gateway-wallet",
            json={"chain": "solana", "address": treasury},
        )

    async def _ensure_engine(
        self, account: str, user: str, record: dict, chain: dict
    ) -> None:
        existing = self.engine_for(account)
        if existing is not None and getattr(existing, "_running", False):
            return

        # The account and its wallet binding come first: an engine started
        # against an unbound account trades from the wrong address.
        await self._ensure_account(account, chain["treasury"])

        from condor.agents.agent import AgentStore
        from condor.agents.engine import TickEngine
        from condor.agents.strategy import StrategyStore

        pin = record["pin"]
        ref = pin["agent_ref"]
        agent_slug = ref.get("agentSlug") or ref.get("agent_slug")
        strategy_slug = ref.get("strategySlug") or ref.get("strategy_slug")
        agent = AgentStore().get(agent_slug)
        strategy = StrategyStore().get(agent_slug, strategy_slug)
        if agent is None or strategy is None:
            log.warning(
                "vault %s pins %s/%s, which this install does not have",
                account,
                agent_slug,
                strategy_slug,
            )
            return

        # The run's config is the vault's signed config, and the wallet it
        # trades from is the vault's — never Gateway's default, which is one
        # address for the whole instance and would make two vaults one wallet.
        config = dict(pin["config"])
        # The vault's server, not the creator's default. Without this the engine
        # resolves a server from the user — and a vault on the fork would have
        # its strategy executing through whatever server that user last chose,
        # which is a different hummingbot-api, a different Gateway and a
        # different chain.
        config["server_name"] = self.server
        config["account_name"] = _account_name(account)
        config["wallet_address"] = chain["treasury"]
        config["vault_account"] = account
        config["execution_mode"] = "loop"

        engine = TickEngine(
            agent=agent,
            strategy=strategy,
            config=config,
            chat_id=0,
            user_id=int(user),
        )
        await engine.start()
        self._agent_ids[account] = engine.agent_id
        log.info(
            "vault %s running %s/%s v%s",
            account,
            agent_slug,
            strategy_slug,
            chain.get("version"),
        )

    async def _stop_engine(self, account: str) -> None:
        agent_id = self._agent_ids.pop(account, None)
        engine = _supervisor().get(agent_id) if agent_id else None
        if engine is None:
            return
        try:
            await engine.stop()
        except Exception:
            # A stop that finds no engine is "already stopped" — Condor
            # restarts kill engines, and the record outlives them.
            log.debug("vault %s engine was already gone", account, exc_info=True)
        log.info("vault %s stopped", account)


def _supervisor():
    """The one registry of running engines (``condor.runtime.loops``)."""
    from condor.runtime.loops import get_supervisor

    return get_supervisor()


def _account_name(account: str) -> str:
    """The hummingbot-api account this vault trades through.

    One per vault, so each binds its own Gateway wallet: without that every
    account falls back to Gateway's *default* wallet and two vaults trading at
    once are the same wallet on chain.
    """
    return f"vault_{account[:12]}"


def _native_lamports(balances: Any) -> Optional[int]:
    """The SOL in a balances response, however Gateway shaped it."""
    if isinstance(balances, dict):
        inner = balances.get("balances", balances)
        if isinstance(inner, dict):
            for key in ("SOL", "So11111111111111111111111111111111111111112"):
                if key in inner:
                    return int(float(inner[key]) * 1_000_000_000)
    return None


def _snake(chain: dict[str, Any]) -> dict[str, Any]:
    """Gateway answers in camelCase; everything below reads snake_case."""
    out = {}
    for key, value in chain.items():
        snake = "".join("_" + c.lower() if c.isupper() else c for c in key)
        out[snake] = value
    return out


def _config_manager():
    from config_manager import get_config_manager

    return get_config_manager()


# ── the process-wide set of cranks ────────────────────────────────────────────


_cranks: dict[str, VaultCrank] = {}


def get_crank(server: str, network: str = "mainnet-beta") -> VaultCrank:
    """The crank for a server, created on first ask.

    One per server, not one per vault: a crank is a decision loop about which
    engines should exist, and a server is the unit that has a Gateway, a chain
    and a set of vaults on it.
    """
    crank = _cranks.get(server)
    if crank is None:
        crank = VaultCrank(server, network)
        _cranks[server] = crank
    return crank


async def start_configured_cranks() -> list[str]:
    """Start a crank for every server Condor has a vault on.

    Not every configured server: the vault routes live in Condor's Gateway fork,
    so a loop pointed at a stock Gateway would do nothing but log a 404 twice a
    minute. A record is written by the create flow, so by the time there is
    anything to crank there is a record to find it by, and an install with no
    vaults starts no loops and says nothing about it.
    """
    servers = {
        record.get("server")
        for _user, _account, record in vault_store.iter_all_users()
        if record.get("server")
    }
    started: list[str] = []
    for name in sorted(servers):
        await get_crank(name).start()
        started.append(name)
    return started


async def stop_all_cranks() -> None:
    for crank in list(_cranks.values()):
        await crank.stop()
    _cranks.clear()
