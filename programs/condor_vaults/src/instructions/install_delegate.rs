//! `install_delegate` — the second signature, and the one with teeth.
//!
//! Adds the administrator's delegate role to the Swig, removing whatever role
//! was there before. This is also how an administrator is *replaced*: the
//! Swig's role table changes, funds and positions do not move, and the new
//! administrator needs the chain and (while the vault runs) the private config
//! the runner re-enters through its interface.
//!
//! **The administrator co-signs, but only once the vault is tokenized.** The
//! co-signature exists for exactly one reason: a runner who could install their
//! own key as delegate could walk the seed out, and the seed is other people's
//! money. While the vault is private there is no such money — the runner may
//! withdraw outright — so demanding Condor's signature would buy nobody
//! anything and would make a self-hosted private vault impossible. A runner
//! running their own strategy on their own machine with their own capital needs
//! no permission, and gets none imposed.
//!
//! From `tokenize` onward the signature is required, and the only delegate that
//! can be installed is one whose key `Protocol.administrator` holds. Until then
//! neither the signature nor the protocol account is asked for at all: a
//! private vault on a chain where Condor has initialized nothing is still a
//! vault its runner can run.

use anchor_lang::prelude::*;

use crate::error::VaultError;
use crate::state::{Protocol, Vault, VaultState, PROTOCOL_SEED, VAULT_AUTHORITY_SEED, VAULT_SEED};
use crate::swig;

#[derive(Accounts)]
pub struct InstallDelegate<'info> {
    /// The vault's runner. Pays: adding a role grows the Swig account.
    #[account(mut)]
    pub runner: Signer<'info>,
    /// The registered administrator, co-signing — required only for a
    /// tokenized vault, checked in the handler because the requirement depends
    /// on the vault's state rather than on the account list.
    pub administrator: Option<Signer<'info>>,
    /// The protocol record, and **optional for the same reason the
    /// administrator is**: it is read only to learn who the administrator is,
    /// which only matters once the vault is tokenized. Required here it would
    /// have made every private vault depend on Condor's protocol account
    /// existing on that chain — which is exactly the dependency a private vault
    /// is supposed not to have (D29).
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Option<Account<'info, Protocol>>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
        has_one = runner @ VaultError::NotRunner,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the Swig's root authority, signing by CPI. Its seeds and the
    /// stored bump are the proof it is this vault's.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, vault.swig_account.as_ref()],
        bump = vault.authority_bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,
    /// CHECK: this vault's Swig account; the address is checked against the record.
    #[account(mut, address = vault.swig_account @ VaultError::SwigMismatch)]
    pub swig_account: UncheckedAccount<'info>,
    /// CHECK: the Swig program, pinned by address.
    #[account(address = swig::SWIG_PROGRAM_ID)]
    pub swig_program: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
}

pub fn install_delegate(ctx: Context<InstallDelegate>, delegate: Pubkey) -> Result<()> {
    require!(
        ctx.accounts.vault.state != VaultState::Redeemable,
        VaultError::NotRedeemable
    );
    if ctx.accounts.vault.is_tokenized() {
        let signer = ctx
            .accounts
            .administrator
            .as_ref()
            .ok_or(VaultError::NotAdministrator)?;
        let protocol = ctx
            .accounts
            .protocol
            .as_ref()
            .ok_or(VaultError::NotAdministrator)?;
        require_keys_eq!(
            signer.key(),
            protocol.administrator,
            VaultError::NotAdministrator
        );
    }

    let swig_account = ctx.accounts.vault.swig_account;
    let root_role_id = swig::role_id_of(
        &ctx.accounts.swig_account.to_account_info(),
        &ctx.accounts.vault_authority.key(),
    )?
    .ok_or(VaultError::NoRootRole)?;

    let bump = ctx.accounts.vault.authority_bump;
    let seeds: [&[u8]; 3] = [
        VAULT_AUTHORITY_SEED,
        swig_account.as_ref(),
        std::slice::from_ref(&bump),
    ];
    let signer_seeds: &[&[&[u8]]] = &[&seeds];

    // Replace rather than accumulate: two delegates on one Swig would be two
    // keys that can move everything, and the vault page can only name one.
    if ctx.accounts.vault.has_delegate() {
        let previous = ctx.accounts.vault.delegate;
        if let Some(role_id) =
            swig::role_id_of(&ctx.accounts.swig_account.to_account_info(), &previous)?
        {
            swig::remove_role(
                &ctx.accounts.swig_program,
                &ctx.accounts.swig_account,
                &ctx.accounts.runner,
                &ctx.accounts.system_program,
                &ctx.accounts.vault_authority,
                root_role_id,
                role_id,
                signer_seeds,
            )?;
        }
    }

    swig::add_delegate(
        &ctx.accounts.swig_program,
        &ctx.accounts.swig_account,
        &ctx.accounts.runner,
        &ctx.accounts.system_program,
        &ctx.accounts.vault_authority,
        root_role_id,
        &delegate,
        signer_seeds,
    )?;

    ctx.accounts.vault.delegate = delegate;
    Ok(())
}
