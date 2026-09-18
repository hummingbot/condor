//! The treasury: the part of the supply that was not sold at launch.
//!
//! `issue_bps` is circulating over max supply at the moment of tokenization.
//! The remainder is not burned and is not the runner's — DBC's leftover
//! receiver is the **vault's wallet**, so it lands there alongside its
//! capital.
//!
//! That it lands *in the wallet* rather than in a pot of its own is the whole
//! design. The treasury is not a special kind of supply waiting to be issued;
//! it is an asset the vault owns, and the vault's assets live where the
//! delegate can trade them. So after graduation the vault can market-make its
//! own token — an LP position against its own pool, earning fees, deepening the
//! market its holders exit through — with no instruction here at all, because
//! the executor path already reaches it. A treasury the delegate could not
//! touch would have been a pot that could only ever shrink.
//!
//! One instruction, because the treasury needs no special way out.
//!
//! `collect_leftover` is permissionless, like `collect_seed`: after migration
//! DBC still holds the unsold base tokens, and this moves them to the wallet.
//! Nobody can point it anywhere else, because the destination is written into
//! the config as the leftover receiver and checked by DBC.
//!
//! **There is no `inject`, and there was.** An earlier version let the runner
//! buy treasury tokens from the vault at the migrated pool's price, so that
//! capital could enter after launch. It is unnecessary: an LP position funded
//! from the treasury does the same thing better. As the market buys, treasury
//! supply enters circulation and the quote paid for it lands in the vault —
//! continuously, at whatever the market is, with the strategy as the
//! counterparty rather than the runner. That deleted an instruction, a price
//! oracle question, and a privilege the runner did not need.
//!
//! Treasury tokens never count as circulating: `redeem` subtracts the wallet's
//! own balance of the token from its denominator exactly as it subtracts the
//! pool's. Whichever way they leave the wallet — sold by the delegate or worked
//! as half of an LP position — the quote comes back to the vault, so
//! circulating supply and vault capital move together, and `issue_bps` is still
//! the ceiling on how far a holder can be diluted.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::invoke;

use crate::dbc;
use crate::error::VaultError;
use crate::state::{Vault, VAULT_AUTHORITY_SEED, VAULT_SEED};
use crate::token;

#[derive(Accounts)]
pub struct CollectLeftover<'info> {
    /// Anyone. Pays the transaction and receives nothing.
    #[account(mut)]
    pub payer: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the wallet — the leftover receiver DBC pays, which it checks
    /// against the config. Not a signer: that is what makes this call
    /// permissionless.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, vault.id.as_ref()],
        bump = vault.authority_bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,

    /// CHECK: DBC's signer PDA.
    #[account(address = dbc::DBC_POOL_AUTHORITY)]
    pub pool_authority: UncheckedAccount<'info>,
    /// CHECK: the config this vault's pool was launched from.
    pub config: UncheckedAccount<'info>,
    /// CHECK: this vault's pool.
    #[account(mut, address = vault.dbc_pool @ VaultError::PoolNotDbc)]
    pub virtual_pool: UncheckedAccount<'info>,
    /// CHECK: the destination — the wallet's own account for the vault token,
    /// derived in the handler.
    #[account(mut)]
    pub token_base_account: UncheckedAccount<'info>,
    /// CHECK: DBC's base vault; checked against the pool.
    #[account(mut)]
    pub base_vault: UncheckedAccount<'info>,
    /// CHECK: the vault's token mint.
    #[account(address = vault.mint @ VaultError::WrongMint)]
    pub base_mint: UncheckedAccount<'info>,
    /// CHECK: Token-2022 — every vault token is one.
    #[account(address = dbc::TOKEN_2022_PROGRAM_ID)]
    pub token_base_program: UncheckedAccount<'info>,
    /// CHECK: DBC's Anchor event authority.
    pub event_authority: UncheckedAccount<'info>,
    /// CHECK: the DBC program, pinned by address.
    #[account(address = dbc::DBC_PROGRAM_ID)]
    pub dbc_program: UncheckedAccount<'info>,
}

pub fn collect_leftover(ctx: Context<CollectLeftover>) -> Result<()> {
    require!(ctx.accounts.vault.is_tokenized(), VaultError::NotTokenized);
    let pool = dbc::read_virtual_pool(&ctx.accounts.virtual_pool.to_account_info())?;
    require_keys_eq!(
        pool.config,
        ctx.accounts.config.key(),
        VaultError::PoolConfigMismatchForPool
    );
    require!(pool.is_migrated, VaultError::PoolNotMigrated);
    require_keys_eq!(
        pool.base_vault,
        ctx.accounts.base_vault.key(),
        VaultError::PoolNotDbc
    );
    token::require_associated(
        &ctx.accounts.token_base_account.to_account_info(),
        &ctx.accounts.vault_authority.key(),
        &ctx.accounts.vault.mint,
        &ctx.accounts.token_base_program.key(),
    )?;

    // The leftover receiver is a plain account in this instruction, not a
    // signer: DBC pays whoever the config named, and the config named this
    // wallet at tokenize. So the call needs no signature at all — which is what
    // makes it permissionless.
    let ix = Instruction {
        program_id: dbc::DBC_PROGRAM_ID,
        accounts: vec![
            AccountMeta::new_readonly(ctx.accounts.pool_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.config.key(), false),
            AccountMeta::new(ctx.accounts.virtual_pool.key(), false),
            AccountMeta::new(ctx.accounts.token_base_account.key(), false),
            AccountMeta::new(ctx.accounts.base_vault.key(), false),
            AccountMeta::new_readonly(ctx.accounts.base_mint.key(), false),
            AccountMeta::new_readonly(ctx.accounts.vault_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.token_base_program.key(), false),
            AccountMeta::new_readonly(ctx.accounts.event_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.dbc_program.key(), false),
        ],
        data: dbc::withdraw_leftover_data(),
    };
    invoke(
        &ix,
        &[
            ctx.accounts.pool_authority.to_account_info(),
            ctx.accounts.config.to_account_info(),
            ctx.accounts.virtual_pool.to_account_info(),
            ctx.accounts.token_base_account.to_account_info(),
            ctx.accounts.base_vault.to_account_info(),
            ctx.accounts.base_mint.to_account_info(),
            ctx.accounts.vault_authority.to_account_info(),
            ctx.accounts.token_base_program.to_account_info(),
            ctx.accounts.event_authority.to_account_info(),
            ctx.accounts.dbc_program.to_account_info(),
        ],
    )?;
    Ok(())
}
