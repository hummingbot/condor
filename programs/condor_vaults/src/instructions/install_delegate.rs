//! `install_delegate` — the key that trades.
//!
//! One field on the `Vault`, set by the creator. Replacing it is the same
//! instruction; the previous key loses `execute` the moment it is overwritten,
//! and nothing moves.
//!
//! **No co-signature, in either phase.** The design this replaces required
//! the administrator's signature on a tokenized vault because its delegate
//! could sign anything, so the only way to keep a creator from walking the
//! seed out was to keep them from holding the key. Now what a delegate may do
//! is decided by `execute` — which cannot move a token to anyone — and whose
//! key holds the role stops mattering. A creator may install their own key on
//! a tokenized vault and sign their own trades, and a holder is no worse off.
//!
//! Nor does this read the protocol account: a private vault on a chain where
//! Condor has initialized nothing is still a vault its creator can run (D29).

use anchor_lang::prelude::*;

use crate::error::VaultError;
use crate::state::{Vault, VaultState, VAULT_SEED};

#[derive(Accounts)]
pub struct InstallDelegate<'info> {
    pub creator: Signer<'info>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
        has_one = creator @ VaultError::NotCreator,
    )]
    pub vault: Account<'info, Vault>,
}

pub fn install_delegate(ctx: Context<InstallDelegate>, delegate: Pubkey) -> Result<()> {
    require!(
        ctx.accounts.vault.state != VaultState::Redeemable,
        VaultError::NotRedeemable
    );
    ctx.accounts.vault.delegate = delegate;
    Ok(())
}
