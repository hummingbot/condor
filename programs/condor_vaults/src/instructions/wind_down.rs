//! `wind_down` and `finalize_wind_down` — the only exit, and it is one-way.
//!
//! `wind_down` stops the strategy for good: from that moment `publish_version`
//! and `set_active` refuse, so a vault cannot be wound down and then quietly
//! restarted under a new strategy. The runner calls it; so may the protocol
//! authority, for a vault whose runner has vanished, with no notice period — a
//! forced liquidation into the quote asset is not a theft, and a notice period
//! on an abandoned vault only delays the holders.
//!
//! **Only a tokenized vault winds down.** The whole ceremony exists to pay
//! holders: convert everything into the quote asset the launch chose and stop
//! the strategy. A private vault has no holders, no quote asset and nothing to
//! convert — its runner empties it through `execute_unchecked` whenever they
//! like. `wind_down` refuses one.
//!
//! Between the two calls the conversion happens through `execute`, by the
//! crank or by anyone the vault lets act: close every position, swap every
//! non-quote balance to `quote_mint`. Nothing moves anywhere — the wallet's
//! own quote account is what `redeem` pays from, because the wallet is this
//! program's PDA and signs the payout itself.
//!
//! `finalize_wind_down` is **administrator-signed**, a deliberate narrowing
//! (plan §1.6): the check it performs can only look at the accounts it is
//! handed, so a stranger could pass a short list and finalize a vault with
//! positions still open, stranding them. Making the caller the one party that
//! knows the whole list closes that.

use anchor_lang::prelude::*;

use crate::error::VaultError;
use crate::state::{
    Protocol, Vault, VaultState, PROTOCOL_SEED, VAULT_AUTHORITY_SEED, VAULT_SEED, WIND_DOWN_DUST,
};
use crate::token;

#[derive(Accounts)]
pub struct WindDown<'info> {
    /// The runner, or the protocol authority for an abandoned vault.
    pub signer: Signer<'info>,
    /// Optional, and only the abandoned-vault path needs it: it is read to see
    /// whether the signer is the protocol authority. A runner stopping their
    /// own private vault passes none, and so does not depend on Condor having
    /// initialized anything on this chain (D29).
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Option<Account<'info, Protocol>>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
}

pub fn wind_down(ctx: Context<WindDown>) -> Result<()> {
    // Only a tokenized vault winds down, because a wind-down exists to pay
    // holders: it converts everything into the quote asset the launch chose and
    // moves it into a pot the program pays redemptions from. A private vault
    // has no holders, no quote asset and nothing to convert — its runner takes
    // the assets out through the delegate, which they can do at any time and
    // without asking the program. Letting one "stop for good" was offering a
    // ceremony that did nothing except make the strategy unchangeable.
    require!(ctx.accounts.vault.is_tokenized(), VaultError::NotTokenized);
    let signer = ctx.accounts.signer.key();
    require!(
        signer == ctx.accounts.vault.runner
            || ctx
                .accounts
                .protocol
                .as_ref()
                .is_some_and(|protocol| signer == protocol.authority),
        VaultError::NotRunner
    );
    let vault = &mut ctx.accounts.vault;
    require!(
        vault.state.accepts_strategy_changes(),
        VaultError::WindingDown
    );
    vault.state = VaultState::WindingDown;
    vault.wind_down_ts = Clock::get()?.unix_timestamp;
    Ok(())
}

#[derive(Accounts)]
pub struct FinalizeWindDown<'info> {
    /// The administrator — the one party that knows the whole account list.
    #[account(address = protocol.administrator @ VaultError::NotAdministrator)]
    pub administrator: Signer<'info>,
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Account<'info, Protocol>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the wallet. Read here for its address; it signs nothing.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, vault.id.as_ref()],
        bump = vault.authority_bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,
    /// CHECK: the wallet's own quote account, derived in the handler. What
    /// `redeem` pays out of, so it has to hold something.
    pub redemption_pot: UncheckedAccount<'info>,
    /// CHECK: the quote mint's token program.
    pub quote_token_program: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
    // remaining_accounts: every token account of the funds owner the caller
    // knows about, including the NFT accounts of any open positions.
}

pub fn finalize_wind_down(ctx: Context<FinalizeWindDown>) -> Result<()> {
    require!(
        ctx.accounts.vault.state == VaultState::WindingDown,
        VaultError::NotWindingDown
    );

    // Anything of the wallet's that still holds more than dust and is not the
    // quote asset stops the finalize. A position NFT is such a balance — one
    // unit of its own mint — so an open position is caught by the same rule
    // that catches a forgotten token, and neither needs a special case.
    //
    // The vault's *own* token is the one exception, and it is not a loophole:
    // it is the unsold treasury, it is already excluded from the redemption
    // denominator, and nobody has a claim on it. Requiring it to be sold first
    // would make finishing a wind-down depend on there being a bid.
    let funds_owner = ctx.accounts.vault_authority.key();
    let quote_mint = ctx.accounts.vault.quote_mint;
    let own_mint = ctx.accounts.vault.mint;
    for account in ctx.remaining_accounts.iter() {
        let Some(balance) = token::read_token_account(account) else {
            continue;
        };
        if balance.owner != funds_owner {
            continue;
        }
        if balance.mint == quote_mint || balance.mint == own_mint {
            continue;
        }
        require!(
            balance.amount <= WIND_DOWN_DUST,
            VaultError::WindDownIncomplete
        );
    }

    // Everything is in the quote asset now, and it is in the wallet's own
    // quote account — which is what `redeem` pays from, so holders are owed
    // that it holds something. Verified, not performed: the conversion is
    // `execute` calls, and this only checks that they happened.
    let pot = token::require_associated(
        &ctx.accounts.redemption_pot.to_account_info(),
        &funds_owner,
        &quote_mint,
        &ctx.accounts.quote_token_program.key(),
    )?;
    require!(pot.amount > 0, VaultError::RedemptionPotEmpty);

    // No delegate from here on: nothing trades, and the only movement left is
    // `redeem`, which the wallet signs for itself.
    ctx.accounts.vault.delegate = Pubkey::default();

    ctx.accounts.vault.state = VaultState::Redeemable;
    Ok(())
}
