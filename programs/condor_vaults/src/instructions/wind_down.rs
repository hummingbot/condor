//! `wind_down` and `finalize_wind_down` — the only exit, and it is one-way.
//!
//! `wind_down` stops the strategy for good: from that moment `publish_version`
//! and `set_active` refuse, so a vault cannot be wound down and then quietly
//! restarted under a new strategy. The creator calls it; so may the protocol
//! authority, for a vault whose creator has vanished, with no notice period — a
//! forced liquidation into the quote asset is not a theft, and a notice period
//! on an abandoned vault only delays the holders.
//!
//! **Only a tokenized vault winds down.** The whole ceremony exists to pay
//! holders: convert everything into the quote asset the launch chose and stop
//! the strategy. A private vault has no holders, no quote asset and nothing to
//! convert — its creator empties it through `execute_unchecked` whenever they
//! like. `wind_down` refuses one.
//!
//! Between the two calls the conversion happens through `execute`, by the
//! crank or by anyone the vault lets act: close every position, swap every
//! non-quote balance to `quote_mint`. Nothing moves anywhere — the treasury's
//! own quote account is what `redeem` pays from, because the treasury is this
//! program's PDA and signs the payout itself.
//!
//! `finalize_wind_down` **fixes the estate**: it reads the circulating supply
//! once — the mint, less what the curve still holds, less the graduated pool's
//! permanently locked liquidity, less the retained supply — and writes it to
//! the `Vault` as the number every redemption divides by. It is
//! administrator-signed, not because the reads need privilege but because
//! closing the estate before the conversion is finished strands whatever is
//! left, and that call should have a name on it.

use anchor_lang::prelude::*;

use crate::dbc;
use crate::error::VaultError;
use crate::state::{
    Protocol, Vault, VaultState, PROTOCOL_SEED, TREASURY_SEED, VAULT_SEED,
};
use crate::token;

#[derive(Accounts)]
pub struct WindDown<'info> {
    /// The creator, or the protocol authority for an abandoned vault.
    pub signer: Signer<'info>,
    /// Optional, and only the abandoned-vault path needs it: it is read to see
    /// whether the signer is the protocol authority. A creator stopping their
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
    // has no holders, no quote asset and nothing to convert — its creator takes
    // the assets out through the delegate, which they can do at any time and
    // without asking the program. Letting one "stop for good" was offering a
    // ceremony that did nothing except make the strategy unchangeable.
    require!(ctx.accounts.vault.is_tokenized(), VaultError::NotTokenized);
    let signer = ctx.accounts.signer.key();
    require!(
        signer == ctx.accounts.vault.creator
            || ctx
                .accounts
                .protocol
                .as_ref()
                .is_some_and(|protocol| signer == protocol.authority),
        VaultError::NotCreator
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
    /// The administrator. Not because the check below needs privilege — it
    /// needs none — but because closing the estate at the wrong moment strands
    /// whatever has not been converted, and that call wants a name on it.
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
    /// CHECK: the treasury. Read here for its address; it signs nothing.
    #[account(
        seeds = [TREASURY_SEED, vault.id.as_ref()],
        bump = vault.treasury_bump,
    )]
    pub treasury: UncheckedAccount<'info>,
    /// CHECK: the vault's own mint. Its supply is where the denominator starts.
    #[account(address = vault.mint @ VaultError::WrongMint)]
    pub mint: UncheckedAccount<'info>,
    /// CHECK: the curve, pinned by address. Read for what it still holds and
    /// for whether it has finished paying out.
    #[account(address = vault.dbc_pool @ VaultError::PoolNotDbc)]
    pub dbc_pool: UncheckedAccount<'info>,
    /// CHECK: the curve's own account for the token; checked against the pool.
    pub dbc_base_vault: UncheckedAccount<'info>,
    /// CHECK: the graduated pool. Derived from the terms the vault recorded, so
    /// it cannot be substituted. Required exactly when the curve has migrated.
    pub damm_pool: Option<UncheckedAccount<'info>>,
    /// CHECK: that pool's own account for the token — the permanently locked
    /// half of the raise. Read off the pool, never taken on trust.
    pub damm_base_vault: Option<UncheckedAccount<'info>>,
    /// CHECK: the treasury's own account for the vault token: retained supply,
    /// which was never sold and is nobody's claim. Derived in the handler.
    pub retained_token_account: UncheckedAccount<'info>,
    /// CHECK: the treasury's own quote account, derived in the handler. What
    /// `redeem` pays out of, so it has to hold something.
    pub redemption_pot: UncheckedAccount<'info>,
    /// CHECK: the vault token's program (Token-2022).
    pub token_program: UncheckedAccount<'info>,
    /// CHECK: the quote mint's token program.
    pub quote_token_program: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
}

pub fn finalize_wind_down(ctx: Context<FinalizeWindDown>) -> Result<()> {
    require!(
        ctx.accounts.vault.state == VaultState::WindingDown,
        VaultError::NotWindingDown
    );
    let treasury = ctx.accounts.treasury.key();
    let vault_mint = ctx.accounts.vault.mint;

    // **There is no emptiness sweep, and there was one.** It walked the token
    // accounts the caller handed it and refused while any non-quote balance
    // exceeded dust. It never worked: a position NFT is one unit, and one is
    // below dust, so the open positions it was written to catch went straight
    // through; a DLMM position is not a token account at all, so it was not
    // even looked at. What it did do was hand anyone a way to stop a wind-down
    // for good — send the treasury a thousand units of a worthless mint and no
    // honest list can ever come back clean. The only escape was for the
    // administrator to pass a filtered list, at which point the check was
    // verifying nothing. A check that a dishonest caller can evade and an
    // honest one cannot satisfy is worse than no check: it is the same trust,
    // plus a griefing vector. What replaces it is below — the facts the chain
    // can actually attest.
    //
    // The consequence is stated rather than hidden: whatever is not in the pot
    // when this lands is stranded. Converting first is the administrator's job
    // and the crank's, and holders are trusting them for it.

    // The curve first: what it still holds of the token, and whether it has
    // finished paying. A seed or a leftover arriving *after* the estate is
    // fixed would pay early redeemers out of one pot and late ones out of
    // another, which is not the pro-rata this promises.
    let pool = dbc::read_virtual_pool(&ctx.accounts.dbc_pool.to_account_info())?;
    require_keys_eq!(
        pool.base_vault,
        ctx.accounts.dbc_base_vault.key(),
        VaultError::PoolNotDbc
    );
    if pool.is_migrated {
        require!(
            pool.creator_migration_fee_withdrawn() && pool.is_withdraw_leftover,
            VaultError::WindDownIncomplete
        );
    }
    let unsold = token::require_token_account(&ctx.accounts.dbc_base_vault.to_account_info())?;
    require_keys_eq!(unsold.mint, vault_mint, VaultError::WrongMint);

    // Then the graduated pool, if there is one. Its balance of the token is
    // liquidity locked forever, not a holding, so it never redeems.
    let locked = match (&ctx.accounts.damm_pool, &ctx.accounts.damm_base_vault) {
        (Some(pool_account), Some(base_vault)) => {
            require!(pool.is_migrated, VaultError::PoolNotGraduated);
            let expected = dbc::damm_v2_pool(
                ctx.accounts.vault.pool_fee_option,
                &vault_mint,
                &ctx.accounts.vault.quote_mint,
            )?;
            require_keys_eq!(pool_account.key(), expected, VaultError::PoolNotDbc);
            require_keys_eq!(
                base_vault.key(),
                dbc::damm_v2_vault_for(&pool_account.to_account_info(), &vault_mint)?,
                VaultError::PoolNotDbc
            );
            token::require_token_account(&base_vault.to_account_info())?.amount
        }
        (None, None) => {
            // A vault can wind down without ever graduating. Then there is no
            // pool, and the tokens the curve did not sell are still the curve's.
            require!(!pool.is_migrated, VaultError::WindDownIncomplete);
            0
        }
        _ => return Err(VaultError::WindDownIncomplete.into()),
    };

    // And the treasury's own balance of its own token: retained supply, which
    // was offered to nobody.
    let retained = token::require_associated(
        &ctx.accounts.retained_token_account.to_account_info(),
        &treasury,
        &vault_mint,
        &ctx.accounts.token_program.key(),
    )?;

    // The estate, fixed here and never recomputed. Everything above keeps
    // moving after this instruction — the graduated pool trades forever — and
    // a denominator that moved with it would let a buyer shrink it and redeem
    // against the difference.
    let mint = token::read_mint(&ctx.accounts.mint.to_account_info())?;
    let redeemable = mint
        .supply
        .checked_sub(unsold.amount)
        .and_then(|s| s.checked_sub(locked))
        .and_then(|s| s.checked_sub(retained.amount))
        .ok_or(VaultError::MathOverflow)?;
    require!(redeemable > 0, VaultError::NothingToRedeem);

    let pot = token::require_associated(
        &ctx.accounts.redemption_pot.to_account_info(),
        &treasury,
        &ctx.accounts.vault.quote_mint,
        &ctx.accounts.quote_token_program.key(),
    )?;
    require!(pot.amount > 0, VaultError::RedemptionPotEmpty);

    let vault = &mut ctx.accounts.vault;
    vault.redeemable_supply = redeemable;
    // No delegate from here on: nothing trades, and the only movement left is
    // `redeem`, which the treasury signs for itself.
    vault.delegate = Pubkey::default();
    vault.state = VaultState::Redeemable;
    Ok(())
}
