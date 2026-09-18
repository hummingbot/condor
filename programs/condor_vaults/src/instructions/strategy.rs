//! `pin`, `publish_version`, `set_fee`, `set_active` — everything the runner
//! changes about a running vault, and the one place the wind-down's one-wayness
//! is enforced.
//!
//! What goes on chain is a *commitment*, never the parameters: the public agent
//! folder by commit, and the sha256 of the private config's canonical encoding.
//! A crank that is handed a config hashes it and compares, so nobody — Condor's
//! own operators included — can run a vault on parameters its runner did not
//! sign. Nothing here validates `agent_ref` against anything: on-chain
//! validation of a folder Condor does not control would be a claim Condor
//! cannot make (plan D17).

use anchor_lang::prelude::*;

use crate::error::VaultError;
use crate::state::{
    AgentRef, Vault, VaultState, BPS_DENOMINATOR, VAULT_AUTHORITY_SEED, VAULT_SEED,
};
use crate::swig;

#[derive(Accounts)]
pub struct Pin<'info> {
    pub runner: Signer<'info>,
    // No protocol account: nothing here reads one. Requiring it would have made
    // pinning a strategy — the last instruction of creating a private vault —
    // depend on Condor having initialized its protocol on this chain, which is
    // the one dependency a private vault is meant not to have (D29).
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
        has_one = runner @ VaultError::NotRunner,
    )]
    pub vault: Account<'info, Vault>,
}

/// Version 1, and the vault starts running.
///
/// Nothing here mentions a token: a private vault pins and runs a strategy for
/// its own runner, and most will do so for a while before anyone else is
/// invited in. `fee_bps` is recorded now and simply has nothing to buy until
/// `tokenize`.
pub fn pin(
    ctx: Context<Pin>,
    agent_ref: AgentRef,
    config_hash: [u8; 32],
    fee_bps: u16,
) -> Result<()> {
    require!(!ctx.accounts.vault.is_pinned(), VaultError::AlreadyPinned);
    require!(fee_bps <= BPS_DENOMINATOR, VaultError::BpsOutOfRange);

    let vault = &mut ctx.accounts.vault;
    vault.agent_ref = agent_ref;
    vault.config_hash = config_hash;
    vault.fee_bps = fee_bps;
    vault.version = 1;
    vault.state = VaultState::Running;
    Ok(())
}

/// Recompute the authority PDA from the stored bump. `create_program_address`
/// rather than `find_program_address`: the bump is known, and the difference is
/// a bump-seed search per call.
fn expected_authority(vault: &Vault) -> Result<Pubkey> {
    let bump = [vault.authority_bump];
    Pubkey::create_program_address(
        &[VAULT_AUTHORITY_SEED, vault.swig_account.as_ref(), &bump],
        &crate::ID,
    )
    .map_err(|_| error!(VaultError::PoolCreatorMismatch))
}

#[derive(Accounts)]
pub struct RunnerOnly<'info> {
    pub runner: Signer<'info>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
        has_one = runner @ VaultError::NotRunner,
    )]
    pub vault: Account<'info, Vault>,
}

/// A new version: a new agent pin, a new config hash, or both.
///
/// Refused from `wind_down` onwards. That refusal is the wind-down's whole
/// guarantee — holders are told the strategy stops and cannot be restarted
/// under a new name, and this is where that is true rather than promised.
pub fn publish_version(
    ctx: Context<RunnerOnly>,
    agent_ref: AgentRef,
    config_hash: [u8; 32],
) -> Result<()> {
    let vault = &mut ctx.accounts.vault;
    require!(vault.is_pinned(), VaultError::NotPinned);
    require!(
        vault.state.accepts_strategy_changes(),
        VaultError::WindingDown
    );
    vault.version = vault.version.checked_add(1).ok_or(VaultError::VersionOverflow)?;
    vault.agent_ref = agent_ref;
    vault.config_hash = config_hash;
    Ok(())
}

/// The share of realised LP fees the sweep burns. Holders read it; the tick
/// reads it from chain rather than from Condor's copy.
pub fn set_fee(ctx: Context<RunnerOnly>, fee_bps: u16) -> Result<()> {
    require!(fee_bps <= BPS_DENOMINATOR, VaultError::BpsOutOfRange);
    let vault = &mut ctx.accounts.vault;
    require!(vault.is_pinned(), VaultError::NotPinned);
    require!(
        vault.state.accepts_strategy_changes(),
        VaultError::WindingDown
    );
    vault.fee_bps = fee_bps;
    Ok(())
}

/// Pause and resume. Open positions stay open — pausing is "take nothing new",
/// not "close everything"; closing everything is what winding down is for.
pub fn set_active(ctx: Context<RunnerOnly>, active: bool) -> Result<()> {
    let vault = &mut ctx.accounts.vault;
    require!(vault.is_pinned(), VaultError::NotPinned);
    require!(
        vault.state.accepts_strategy_changes(),
        VaultError::WindingDown
    );
    vault.state = if active {
        VaultState::Running
    } else {
        VaultState::Paused
    };
    Ok(())
}

/// Re-exported so the wind-down and redeem handlers derive the authority the
/// same way rather than each writing the seeds out again.
pub fn authority_of(vault: &Vault) -> Result<Pubkey> {
    expected_authority(vault)
}

/// The vault's Swig still carries the administrator's delegate. Used by the
/// crank's start checks through the account decode, and by `finalize` to know
/// there is something to remove.
pub fn delegate_role_id(swig_account: &AccountInfo, delegate: &Pubkey) -> Result<Option<u32>> {
    swig::role_id_of(swig_account, delegate)
}
