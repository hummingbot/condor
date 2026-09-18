//! `collect_seed` — permissionless, and that is the point.
//!
//! When the curve fills, Meteora's keeper migrates the pool and 80 % of the
//! raise sits in DBC as the *creator's* migration fee. The creator is this
//! program's PDA, so the fee has exactly one destination: the Swig's own
//! associated account for the quote asset. Nobody can send it anywhere else,
//! which is why anybody may send it — the caller pays the gas and gets
//! nothing, and DBC's own one-time flag makes a second call a no-op that the
//! handler refuses before it spends anything (plan §1.4 Phase B).
//!
//! That is what "the users fund the wallet trustlessly" means in practice: not
//! that Condor delivers the seed promptly, but that Condor's promptness is
//! irrelevant.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::invoke_signed;

use crate::dbc;
use crate::error::VaultError;
use crate::instructions::strategy::authority_of;
use crate::state::{Protocol, Vault, PROTOCOL_SEED, VAULT_AUTHORITY_SEED, VAULT_SEED};
use crate::token;

#[derive(Accounts)]
pub struct CollectSeed<'info> {
    /// Anyone. Pays the transaction and receives nothing.
    #[account(mut)]
    pub payer: Signer<'info>,
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Account<'info, Protocol>,
    #[account(
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the DBC creator, signing by CPI.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, vault.swig_account.as_ref()],
        bump = vault.authority_bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,

    /// CHECK: DBC's signer PDA.
    #[account(address = dbc::DBC_POOL_AUTHORITY)]
    pub pool_authority: UncheckedAccount<'info>,
    /// CHECK: the config this vault's pool was launched from; checked against
    /// the pool itself in the handler, since each vault has its own.
    pub config: UncheckedAccount<'info>,
    /// CHECK: this vault's pool.
    #[account(mut, address = vault.dbc_pool @ VaultError::PoolNotDbc)]
    pub virtual_pool: UncheckedAccount<'info>,
    /// CHECK: the destination — derived from the funds owner and the quote
    /// mint in the handler, never taken on trust.
    #[account(mut)]
    pub token_quote_account: UncheckedAccount<'info>,
    /// CHECK: DBC's quote vault for this pool; checked against the pool.
    #[account(mut)]
    pub quote_vault: UncheckedAccount<'info>,
    /// CHECK: the vault's quote mint.
    #[account(address = vault.quote_mint @ VaultError::WrongQuoteMint)]
    pub quote_mint: UncheckedAccount<'info>,
    /// CHECK: the quote mint's token program.
    pub token_quote_program: UncheckedAccount<'info>,
    /// CHECK: DBC's Anchor event authority.
    pub event_authority: UncheckedAccount<'info>,
    /// CHECK: the DBC program, pinned by address.
    #[account(address = dbc::DBC_PROGRAM_ID)]
    pub dbc_program: UncheckedAccount<'info>,
}

pub fn collect_seed(ctx: Context<CollectSeed>) -> Result<()> {
    require!(ctx.accounts.vault.is_tokenized(), VaultError::NotTokenized);
    let pool = dbc::read_virtual_pool(&ctx.accounts.virtual_pool.to_account_info())?;
    require_keys_eq!(
        pool.config,
        ctx.accounts.config.key(),
        VaultError::PoolConfigMismatchForPool
    );
    require!(pool.is_migrated, VaultError::PoolNotMigrated);
    require!(
        !pool.creator_migration_fee_withdrawn(),
        VaultError::SeedAlreadyCollected
    );
    require_keys_eq!(
        pool.quote_vault,
        ctx.accounts.quote_vault.key(),
        VaultError::PoolNotDbc
    );
    require_keys_eq!(
        pool.creator,
        ctx.accounts.vault_authority.key(),
        VaultError::PoolCreatorMismatch
    );
    require_keys_eq!(
        authority_of(&ctx.accounts.vault)?,
        ctx.accounts.vault_authority.key(),
        VaultError::PoolCreatorMismatch
    );

    // The seed lands in the wallet's own account, or nowhere.
    token::require_associated(
        &ctx.accounts.token_quote_account.to_account_info(),
        &ctx.accounts.vault.funds_owner,
        &ctx.accounts.vault.quote_mint,
        &ctx.accounts.token_quote_program.key(),
    )?;

    let swig_account = ctx.accounts.vault.swig_account;
    let bump = ctx.accounts.vault.authority_bump;
    let seeds: [&[u8]; 3] = [
        VAULT_AUTHORITY_SEED,
        swig_account.as_ref(),
        std::slice::from_ref(&bump),
    ];

    let accounts = dbc::DbcAccounts {
        pool_authority: &ctx.accounts.pool_authority,
        config: &ctx.accounts.config,
        virtual_pool: &ctx.accounts.virtual_pool,
        token_quote_account: &ctx.accounts.token_quote_account,
        quote_vault: &ctx.accounts.quote_vault,
        quote_mint: &ctx.accounts.quote_mint,
        creator: &ctx.accounts.vault_authority,
        token_quote_program: &ctx.accounts.token_quote_program,
        event_authority: &ctx.accounts.event_authority,
        program: &ctx.accounts.dbc_program,
    };
    let (ix, infos) = accounts.withdraw_migration_fee();
    invoke_signed(&ix, &infos, &[&seeds])?;
    Ok(())
}
