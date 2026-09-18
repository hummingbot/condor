//! `condor_vaults` — a Swig wallet that trades a strategy and is owned by a
//! program rather than by a person.
//!
//! **A vault has two lives, and the second is optional.**
//!
//! It is created as a Swig wallet whose root authority is a PDA of this
//! program, with a `Vault` record addressed by that Swig. At first it is
//! *private*: it has no mint, and it runs its runner's strategy with its
//! runner's money. Nothing about it needs Condor — no delegate anyone else
//! holds, no registry, no permission — and there is deliberately no `withdraw`
//! instruction, because none is needed: the delegate the runner installed can
//! already move anything in the wallet, at top level, through Gateway. A
//! private vault is a wallet its owner controls, and the program's job there is
//! to stay out of the way.
//!
//! `tokenize` ends it. The vault launches a Token-2022 mint on a Meteora
//! bonding curve whose **pool creator is the same PDA**, sells `issue_bps` of
//! the fixed supply, and keeps the rest as a treasury the strategy market-makes
//! with — an LP position on the vault's own pool, single- or double-sided,
//! which is how unissued supply turns into vault capital as buyers arrive. When
//! the curve fills, 80 % of what it raised becomes the vault's capital by a rule
//! nobody can skip (`collect_seed`, permissionless) and 20 % becomes
//! permanently locked liquidity. The runner can still change the
//! strategy, pause it, and wind it down once — after which holders redeem the
//! quote asset pro-rata and nothing trades again.
//!
//! Every guarantee this program makes is a guarantee to somebody who is not the
//! runner. That is why they all start at `tokenize`, and why a private vault
//! carries almost none of them: there is nobody there to protect.
//!
//! What is deliberately *not* here:
//!
//! * **No attestation.** Nothing on chain says Condor reviewed anything. Condor
//!   scans a strategy before its own crank runs it and refuses on failure, in
//!   its own database — a private run policy, not an endorsement (plan D17).
//! * **No Swig action limits.** They fight LP flows, and the honest statement
//!   is that the installed administrator is trusted while installed. What
//!   bounds it is that the runner can replace it and that it can never rewrite
//!   the role table.
//! * **No name registry.** A vault's identity is its Swig address. Two vaults
//!   may call themselves the same thing, and every listing shows the address.
//!
//! Layout: `state.rs` holds the two accounts, `swig.rs` and `dbc.rs` the two
//! foreign wire formats (see `swig.rs` for why by hand), `token.rs` the three
//! SPL calls, and `instructions/` one file per handler.

use anchor_lang::prelude::*;

pub mod dbc;
pub mod error;
pub mod instructions;
pub mod state;
pub mod token;
pub mod venues;

use instructions::*;
use state::AgentRef;

declare_id!("Bbn3CpNCSH6WmD9uhNJXe76Dy9ouPki2nYyzVvb8jYVy");

#[program]
pub mod condor_vaults {
    use super::*;

    // ── protocol ────────────────────────────────────────────────────────────

    /// Write the `Protocol` account. The program's upgrade authority signs and
    /// becomes the protocol authority.
    pub fn initialize(
        ctx: Context<Initialize>,
        administrator: Pubkey,
        fee_claimer: Pubkey,
        dbc_config: Pubkey,
    ) -> Result<()> {
        instructions::protocol::initialize(ctx, administrator, fee_claimer, dbc_config)
    }

    /// Hand the protocol authority to another key — a Squads multisig, before
    /// the first mainnet vault.
    pub fn set_authority(ctx: Context<SetProtocolKey>, key: Pubkey) -> Result<()> {
        instructions::protocol::set_authority(ctx, key)
    }

    /// Rotate the administrator: the system whose delegate key runs the vaults.
    pub fn set_administrator(ctx: Context<SetProtocolKey>, key: Pubkey) -> Result<()> {
        instructions::protocol::set_administrator(ctx, key)
    }

    // ── a private vault ─────────────────────────────────────────────────────

    /// The `Vault` record and its wallet, two PDAs from one id, funded.
    pub fn create_vault(ctx: Context<CreateVault>, id: [u8; 32], fund_lamports: u64) -> Result<()> {
        instructions::create_vault::create_vault(ctx, id, fund_lamports)
    }

    /// Set the key that trades. No co-signature in either phase: what the key
    /// may do is decided by `execute`, not by whose it is.
    pub fn install_delegate(ctx: Context<InstallDelegate>, delegate: Pubkey) -> Result<()> {
        instructions::install_delegate::install_delegate(ctx, delegate)
    }

    /// Commit the strategy and start running. No token required.
    pub fn pin(
        ctx: Context<Pin>,
        agent_ref: AgentRef,
        config_hash: [u8; 32],
    ) -> Result<()> {
        instructions::strategy::pin(ctx, agent_ref, config_hash)
    }

    /// The wallet invokes any program with any accounts. Private vaults only:
    /// this is the runner's own money, and it is also how they take it out.
    pub fn execute_unchecked<'info>(ctx: Context<'info, Execute<'info>>, data: Vec<u8>) -> Result<()> {
        instructions::execute::execute_unchecked(ctx, data)
    }

    /// The wallet invokes an allowed venue, and every account the call may
    /// write is the wallet's, the venue's, or the caller's own. The only way a
    /// tokenized vault's wallet acts.
    pub fn execute<'info>(ctx: Context<'info, Execute<'info>>, data: Vec<u8>) -> Result<()> {
        instructions::execute::execute(ctx, data)
    }

    // ── tokenizing ──────────────────────────────────────────────────────────

    /// One way. Launches the token on Meteora's curve with the PDA as pool
    /// creator, selling `issue_bps` of the supply.
    pub fn tokenize(
        ctx: Context<Tokenize>,
        name: String,
        symbol: String,
        uri: String,
        issue_bps: u16,
    ) -> Result<()> {
        instructions::tokenize::tokenize(ctx, name, symbol, uri, issue_bps)
    }

    /// Move the unsold supply from DBC into the vault's wallet, where the
    /// delegate can put it to work. Anyone.
    pub fn collect_leftover(ctx: Context<CollectLeftover>) -> Result<()> {
        instructions::treasury::collect_leftover(ctx)
    }

    // ── running ─────────────────────────────────────────────────────────────

    /// A new version: a new agent pin, a new config hash, or both.
    pub fn publish_version(
        ctx: Context<RunnerOnly>,
        agent_ref: AgentRef,
        config_hash: [u8; 32],
    ) -> Result<()> {
        instructions::strategy::publish_version(ctx, agent_ref, config_hash)
    }

    /// Pause and resume.
    pub fn set_active(ctx: Context<RunnerOnly>, active: bool) -> Result<()> {
        instructions::strategy::set_active(ctx, active)
    }

    /// The curve's trading fees, or the surplus, into the runner's accounts.
    pub fn claim_income(ctx: Context<ClaimIncome>, source: IncomeSource) -> Result<()> {
        instructions::income::claim_income(ctx, source)
    }

    /// The migrated pool's locked position fees, into the runner's accounts.
    pub fn claim_position_fee(ctx: Context<ClaimPositionFee>) -> Result<()> {
        instructions::income::claim_position_fee(ctx)
    }

    /// The seed: 80 % of the raise, into the wallet. Anyone may call it.
    pub fn collect_seed(ctx: Context<CollectSeed>) -> Result<()> {
        instructions::collect_seed::collect_seed(ctx)
    }

    // ── the exit ────────────────────────────────────────────────────────────

    /// One-way. The runner, or the protocol authority for an abandoned vault.
    pub fn wind_down(ctx: Context<WindDown>) -> Result<()> {
        instructions::wind_down::wind_down(ctx)
    }

    /// Administrator-signed. Refuses until the wallet is empty and — for a
    /// tokenized vault — the redemption pot is funded; then removes the
    /// delegate.
    pub fn finalize_wind_down(ctx: Context<FinalizeWindDown>) -> Result<()> {
        instructions::wind_down::finalize_wind_down(ctx)
    }

    /// Burn tokens, take the quote asset pro-rata.
    pub fn redeem(ctx: Context<Redeem>, amount: u64) -> Result<()> {
        instructions::redeem::redeem(ctx, amount)
    }
}
