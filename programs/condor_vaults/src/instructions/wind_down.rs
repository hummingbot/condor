//! `wind_down` and `finalize_wind_down` — the only exit, and it is one-way.
//!
//! `wind_down` stops the strategy for good: from that moment `publish_version`,
//! `set_fee` and `set_active` all refuse, so a vault cannot be wound down and
//! then quietly restarted under a new strategy. The runner calls it; so may the
//! protocol authority, for a vault whose runner has vanished, with no notice
//! period — a forced liquidation into the quote asset is not a theft, and a
//! notice period on an abandoned vault only delays the holders.
//!
//! Between the two calls the administrator does the work off chain: close every
//! position, sweep the fees that come out of them like any others, swap every
//! non-quote balance to `quote_mint` at Gateway-quoted slippage.
//!
//! `finalize_wind_down` is **administrator-signed**, and that is a deliberate
//! narrowing of an otherwise permissionless design (plan §1.6). The check it
//! performs can only look at the accounts it is handed, so a stranger could pass
//! a short list and finalize a vault with positions still open, stranding them
//! behind a removed delegate. Making the caller the one party that knows the
//! whole list closes that.
//!
//! ## Why the pot moves out of the Swig
//!
//! Swig refuses to be reached by CPI on any of its execution paths —
//! `sign_v2` opens with `check_stack_height(1, SwigError::Cpi)` — so this
//! program **cannot** pay a redemption out of the Swig wallet, however it is
//! written. That was found by running it against the real program, not by
//! reading it (plan M3, the D3 gate).
//!
//! So the redeemable quote leaves the Swig *before* finalize, into an ordinary
//! token account owned by this program's PDA, moved by the administrator at top
//! level where a Swig sign is legal. This instruction does not perform that
//! move; it **verifies** it, and refuses until it has happened.
//!
//! The result is better than the design it replaces. After finalize the pot is
//! program-owned, so `redeem` needs no Swig, no delegate and no administrator —
//! a holder is paid by a program that cannot refuse. And whether the sweep
//! happened is checkable by anyone: the wallet is empty and the pot is funded.

use anchor_lang::prelude::*;

use crate::error::VaultError;
use crate::state::{
    Protocol, Vault, VaultState, PROTOCOL_SEED, VAULT_AUTHORITY_SEED, VAULT_SEED, WIND_DOWN_DUST,
};
use crate::{swig, token};

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
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
}

pub fn wind_down(ctx: Context<WindDown>) -> Result<()> {
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
    /// The administrator. Pays, because removing a role rewrites the Swig.
    #[account(mut, address = protocol.administrator @ VaultError::NotAdministrator)]
    pub administrator: Signer<'info>,
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Account<'info, Protocol>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the Swig's root authority, signing by CPI.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, vault.swig_account.as_ref()],
        bump = vault.authority_bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,
    /// CHECK: this vault's Swig account.
    #[account(mut, address = vault.swig_account @ VaultError::SwigMismatch)]
    pub swig_account: UncheckedAccount<'info>,
    /// CHECK: the Swig program, pinned by address.
    #[account(address = swig::SWIG_PROGRAM_ID)]
    pub swig_program: UncheckedAccount<'info>,
    /// CHECK: the wallet's quote account, which must be emptied into the pot.
    pub wallet_quote_account: UncheckedAccount<'info>,
    /// CHECK: the redemption pot — the authority PDA's own account for the
    /// quote asset, derived in the handler. What `redeem` pays out of.
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
    let funds_owner = ctx.accounts.vault.funds_owner;
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

    // The quote must already be out of the Swig and in the pot. Verified, not
    // performed: this program cannot move it, because Swig will not sign by CPI.
    let quote_token_program = ctx.accounts.quote_token_program.key();
    let wallet_quote = token::require_associated(
        &ctx.accounts.wallet_quote_account.to_account_info(),
        &funds_owner,
        &quote_mint,
        &quote_token_program,
    )?;
    require!(
        wallet_quote.amount <= WIND_DOWN_DUST,
        VaultError::WindDownIncomplete
    );
    let pot = token::require_associated(
        &ctx.accounts.redemption_pot.to_account_info(),
        &ctx.accounts.vault_authority.key(),
        &quote_mint,
        &quote_token_program,
    )?;
    // A tokenized vault owes its holders something, so the pot has to hold it.
    // A private one owes nobody: its runner has already taken the assets out
    // through the delegate, and an empty pot is the correct end state.
    require!(
        !ctx.accounts.vault.is_tokenized() || pot.amount > 0,
        VaultError::RedemptionPotEmpty
    );

    // Remove the delegate: nothing trades from here on, and the only movement
    // left is `redeem`.
    if ctx.accounts.vault.has_delegate() {
        let root_role_id = swig::role_id_of(
            &ctx.accounts.swig_account.to_account_info(),
            &ctx.accounts.vault_authority.key(),
        )?
        .ok_or(VaultError::NoRootRole)?;
        let delegate = ctx.accounts.vault.delegate;
        if let Some(role_id) =
            swig::role_id_of(&ctx.accounts.swig_account.to_account_info(), &delegate)?
        {
            let swig_account = ctx.accounts.vault.swig_account;
            let bump = ctx.accounts.vault.authority_bump;
            let seeds: [&[u8]; 3] = [
                VAULT_AUTHORITY_SEED,
                swig_account.as_ref(),
                std::slice::from_ref(&bump),
            ];
            swig::remove_role(
                &ctx.accounts.swig_program,
                &ctx.accounts.swig_account,
                &ctx.accounts.administrator,
                &ctx.accounts.system_program,
                &ctx.accounts.vault_authority,
                root_role_id,
                role_id,
                &[&seeds],
            )?;
        }
        ctx.accounts.vault.delegate = Pubkey::default();
    }

    ctx.accounts.vault.state = VaultState::Redeemable;
    Ok(())
}
