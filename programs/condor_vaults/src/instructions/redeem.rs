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
//! **The denominator is what was circulating when the vault stopped**, fixed
//! once by `finalize_wind_down` and stored on the `Vault`: the mint's supply,
//! less the tokens the curve never sold, less the graduated pool's permanently
//! locked liquidity, less the retained supply the vault still holds of its own
//! token.
//!
//! It used to be recomputed here, from accounts the caller passed. Two things
//! were wrong with that, and they compound. The pool's balance was checked only
//! for its *mint*, so any large account of the token shrank the denominator and
//! scaled the payout by the same factor — a holder could substitute somebody
//! else's balance and drain the pot. And even honestly supplied, the number
//! moved: the locked pool keeps trading after the vault is `Redeemable`, and
//! quote paid into it can never reach the pot, so buying the pool's inventory
//! raised the denominator by exactly what the buyer could then redeem against.
//! A fixed number has neither problem, and reads nothing.
//!
//! The pool's are excluded because counting them would dilute every holder in
//! favour of liquidity that is permanently locked and can never be redeemed.
//!
//! The retained supply's are excluded because they were never sold.
//! `circulating_supply` over `total_supply` is what the curve offered, and a creator who offers 30 % is
//! promising the other 70 % is not chasing the same assets — leaving it in the
//! denominator would make that promise a lie by arithmetic. The retained supply is
//! simply the treasury's own balance of its own token: whether it is sitting
//! there, half of an LP position, or already sold for quote, the vault holds
//! the proceeds either way.
//!
//! The payout is computed before the burn, against a denominator that predates
//! both, so a redemption is priced on the estate it is a share of.

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

    let quote_mint = token::read_mint(&ctx.accounts.quote_mint.to_account_info())?;
    // The estate, as `finalize_wind_down` fixed it. Nothing is counted here:
    // every balance this used to read keeps moving after the vault stops, and
    // the one that moves most is the graduated pool, which trades forever
    // against liquidity that can never reach the pot.
    let redeemable_supply = ctx.accounts.vault.redeemable_supply;
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
