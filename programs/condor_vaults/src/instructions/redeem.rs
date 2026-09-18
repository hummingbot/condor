//! `redeem` — burn the token, take a pro-rata share of what the vault holds.
//!
//! Available only once a wind-down has finished, and it pays out of the
//! **redemption pot**: the treasury's own token account for the quote asset,
//! which `finalize_wind_down` refused to run until everything else in the
//! treasury had been converted into it.
//!
//! The treasury is this program's PDA, so a holder is paid by a program that has
//! no way to refuse: no delegate, no administrator, no key stands between the
//! burn and the payout, in the one phase where a stranger's money is at
//! stake.
//!
//! **The denominator is what is circulating**, which is the mint's supply less
//! two balances that are not: the tokens in the graduated pool's vault, and the
//! tokens still in the vault's own retained supply. Both are read in the instruction
//! that pays, because both move.
//!
//! The pool's are excluded because counting them would dilute every holder in
//! favour of liquidity that is permanently locked and can never be redeemed.
//! Buying from the pool raises the denominator and hands the buyer the tokens;
//! selling does the reverse; arbitrage keeps the pool price and the redemption
//! value in step.
//!
//! The retained supply's are excluded because they were never sold. `issue_bps` is
//! circulating over max supply at launch, and a creator who issues 30 % is
//! promising the other 70 % is not chasing the same assets — leaving it in the
//! denominator would make that promise a lie by arithmetic. The retained supply is
//! simply the treasury's own balance of its own token: whether it is sitting
//! there, half of an LP position, or already sold for quote, the vault holds
//! the proceeds either way.
//!
//! The supply is read *before* the burn and the payout computed from it, so a
//! redemption is priced on the state it was quoted against.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::invoke_signed;

use crate::error::VaultError;
use crate::state::{Vault, VaultState, TREASURY_SEED, VAULT_SEED};
use crate::token;

#[derive(Accounts)]
pub struct Redeem<'info> {
    /// Any holder.
    #[account(mut)]
    pub holder: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the treasury — the pot's owner, signing the payout itself.
    #[account(
        seeds = [TREASURY_SEED, vault.id.as_ref()],
        bump = vault.treasury_bump,
    )]
    pub treasury: UncheckedAccount<'info>,

    /// CHECK: the vault's token mint; the burn changes its supply.
    #[account(mut, address = vault.mint @ VaultError::WrongMint)]
    pub mint: UncheckedAccount<'info>,
    /// CHECK: the holder's token account; checked to be theirs, for this mint.
    #[account(mut)]
    pub holder_token_account: UncheckedAccount<'info>,
    /// CHECK: the pool's vault for the token, excluded from the denominator.
    pub pool_token_vault: UncheckedAccount<'info>,
    /// CHECK: the treasury's own account for the token — unsold supply, also
    /// excluded. Derived from the funds owner in the handler.
    pub retained_token_account: UncheckedAccount<'info>,
    /// CHECK: the redemption pot — the authority PDA's own quote account,
    /// derived in the handler.
    #[account(mut)]
    pub redemption_pot: UncheckedAccount<'info>,
    /// CHECK: where the holder is paid; checked to be theirs.
    #[account(mut)]
    pub holder_quote_account: UncheckedAccount<'info>,
    /// CHECK: the vault's quote mint.
    #[account(address = vault.quote_mint @ VaultError::WrongQuoteMint)]
    pub quote_mint: UncheckedAccount<'info>,
    /// CHECK: the token program of the vault's own mint (Token-2022).
    pub token_program: UncheckedAccount<'info>,
    /// CHECK: the quote mint's token program.
    pub quote_token_program: UncheckedAccount<'info>,
}

pub fn redeem(ctx: Context<Redeem>, amount: u64) -> Result<()> {
    require!(
        ctx.accounts.vault.state == VaultState::Redeemable,
        VaultError::NotRedeemable
    );
    // A private vault that wound down has nothing to redeem *with*: its creator
    // took the assets out with `withdraw`, which is the whole point of it
    // having stayed private.
    require!(ctx.accounts.vault.is_tokenized(), VaultError::NotTokenized);
    require!(amount > 0, VaultError::RedemptionTooSmall);

    let holder_tokens =
        token::require_token_account(&ctx.accounts.holder_token_account.to_account_info())?;
    require_keys_eq!(
        holder_tokens.mint,
        ctx.accounts.vault.mint,
        VaultError::WrongMint
    );
    require_keys_eq!(
        holder_tokens.owner,
        ctx.accounts.holder.key(),
        VaultError::WrongTokenAccount
    );

    let pool_tokens = token::require_token_account(&ctx.accounts.pool_token_vault.to_account_info())?;
    require_keys_eq!(
        pool_tokens.mint,
        ctx.accounts.vault.mint,
        VaultError::WrongMint
    );
    let retained = token::require_associated(
        &ctx.accounts.retained_token_account.to_account_info(),
        &ctx.accounts.treasury.key(),
        &ctx.accounts.vault.mint,
        &ctx.accounts.token_program.key(),
    )?;

    let pot = token::require_associated(
        &ctx.accounts.redemption_pot.to_account_info(),
        &ctx.accounts.treasury.key(),
        &ctx.accounts.vault.quote_mint,
        &ctx.accounts.quote_token_program.key(),
    )?;
    let holder_quote =
        token::require_token_account(&ctx.accounts.holder_quote_account.to_account_info())?;
    require_keys_eq!(
        holder_quote.mint,
        ctx.accounts.vault.quote_mint,
        VaultError::WrongQuoteMint
    );
    require_keys_eq!(
        holder_quote.owner,
        ctx.accounts.holder.key(),
        VaultError::WrongTokenAccount
    );

    let mint = token::read_mint(&ctx.accounts.mint.to_account_info())?;
    let quote_mint = token::read_mint(&ctx.accounts.quote_mint.to_account_info())?;
    let redeemable_supply = mint
        .supply
        .checked_sub(pool_tokens.amount)
        .and_then(|s| s.checked_sub(retained.amount))
        .ok_or(VaultError::MathOverflow)?;
    require!(redeemable_supply > 0, VaultError::NothingToRedeem);
    require!(amount <= redeemable_supply, VaultError::MathOverflow);

    let payout = (amount as u128)
        .checked_mul(pot.amount as u128)
        .ok_or(VaultError::MathOverflow)?
        .checked_div(redeemable_supply as u128)
        .ok_or(VaultError::MathOverflow)? as u64;
    require!(payout > 0, VaultError::RedemptionTooSmall);

    // Burn first: the payout was priced on the supply above, and a burn that
    // failed after the transfer would pay twice for the same tokens.
    token::burn(
        &ctx.accounts.token_program,
        &ctx.accounts.holder_token_account,
        &ctx.accounts.mint,
        &ctx.accounts.holder,
        amount,
    )?;

    // Straight out of the treasury's own quote account, signed by the treasury. One
    // CPI, to the token program, and nothing that can decline.
        let seeds = ctx.accounts.vault.treasury_seeds();
    let metas: Vec<AccountMeta> = token::transfer_checked_metas(
        &ctx.accounts.redemption_pot.key(),
        &ctx.accounts.quote_mint.key(),
        &ctx.accounts.holder_quote_account.key(),
        &ctx.accounts.treasury.key(),
    );
    invoke_signed(
        &Instruction {
            program_id: ctx.accounts.quote_token_program.key(),
            accounts: metas,
            data: token::transfer_checked_data(payout, quote_mint.decimals),
        },
        &[
            ctx.accounts.redemption_pot.to_account_info(),
            ctx.accounts.quote_mint.to_account_info(),
            ctx.accounts.holder_quote_account.to_account_info(),
            ctx.accounts.treasury.to_account_info(),
            ctx.accounts.quote_token_program.to_account_info(),
        ],
        &[&seeds],
    )?;
    Ok(())
}
