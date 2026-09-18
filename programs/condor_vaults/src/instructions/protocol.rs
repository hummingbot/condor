//! `initialize`, `set_authority`, `set_administrator` — the three that make
//! the program multisig-ready without a redeploy (plan §1.7).
//!
//! The rule these enforce is small and load-bearing: **an authority
//! instruction takes a separate payer and never reads the authority's
//! lamports**. A Squads multisig executes through its own payer and signs by
//! CPI; Anchor's `Signer` accepts a CPI signer, so handing `authority` to a
//! vault later is one call and no migration. An `authority` that were also
//! `mut` and the rent payer would quietly make that impossible.

use anchor_lang::prelude::*;

use crate::error::VaultError;
use crate::state::{Protocol, PROTOCOL_SEED};

#[derive(Accounts)]
pub struct Initialize<'info> {
    /// Becomes `Protocol.authority`. Must be the program's upgrade authority,
    /// which is the only thing that makes this a first initialization rather
    /// than a land grab.
    pub authority: Signer<'info>,
    #[account(mut)]
    pub payer: Signer<'info>,
    #[account(
        init,
        payer = payer,
        space = 8 + Protocol::INIT_SPACE,
        seeds = [PROTOCOL_SEED],
        bump,
    )]
    pub protocol: Account<'info, Protocol>,
    #[account(
        constraint = program.programdata_address()? == Some(program_data.key())
            @ VaultError::NotUpgradeAuthority
    )]
    pub program: Program<'info, crate::program::CondorVaults>,
    #[account(
        constraint = program_data.upgrade_authority_address == Some(authority.key())
            @ VaultError::NotUpgradeAuthority
    )]
    pub program_data: Account<'info, ProgramData>,
    pub system_program: Program<'info, System>,
}

pub fn initialize(
    ctx: Context<Initialize>,
    administrator: Pubkey,
    fee_claimer: Pubkey,
    dbc_config: Pubkey,
) -> Result<()> {
    let protocol = &mut ctx.accounts.protocol;
    protocol.authority = ctx.accounts.authority.key();
    protocol.administrator = administrator;
    protocol.fee_claimer = fee_claimer;
    protocol.dbc_config = dbc_config;
    protocol.bump = ctx.bumps.protocol;
    protocol._reserved = [0u8; 64];
    Ok(())
}

#[derive(Accounts)]
pub struct SetProtocolKey<'info> {
    /// Not `mut`: nothing here spends, and an authority that is never a payer
    /// is an authority a multisig can hold.
    pub authority: Signer<'info>,
    #[account(
        mut,
        seeds = [PROTOCOL_SEED],
        bump = protocol.bump,
        has_one = authority @ VaultError::NotAuthority,
    )]
    pub protocol: Account<'info, Protocol>,
}

/// Hand the protocol to a different key — a Squads vault, before mainnet.
pub fn set_authority(ctx: Context<SetProtocolKey>, key: Pubkey) -> Result<()> {
    ctx.accounts.protocol.authority = key;
    Ok(())
}

/// Rotate the crank's key. Existing vaults keep the delegate they have until
/// their creator installs the new administrator's — the program never moves a
/// delegate on a creator's behalf.
pub fn set_administrator(ctx: Context<SetProtocolKey>, key: Pubkey) -> Result<()> {
    ctx.accounts.protocol.administrator = key;
    Ok(())
}
