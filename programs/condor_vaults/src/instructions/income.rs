//! `claim_income` and `claim_position_fee` — the runner's money, and only the
//! runner's.
//!
//! A vault has two incomes and they never mix (plan §1.5). The *vault's* income
//! is its strategy's LP fees, less the sweep, and it arrives in the Swig
//! without passing through here. The *runner's* income is what the token's own
//! market pays the pool creator: trading fees on the curve, the surplus above
//! the migration threshold, and — after graduation — the fees on the
//! permanently locked position the creator holds. All three are the PDA's to
//! claim, and this file is the only door out of them.
//!
//! Every destination is derived from `Vault.runner`. A caller-supplied
//! destination would make "the creator's fees are the runner's" a convention
//! rather than a rule.
//!
//! **Why two instructions and not one.** The plan asks for a single
//! `claim_income`. The curve fee and the surplus are two DBC calls over one
//! account set, so they share this one. The migrated position's fees are a
//! call into a *different program* — Meteora's DAMM v2 — over an account set
//! with nothing in common: a position, a position NFT account, a second pool.
//! One instruction covering both would be a 25-account struct, most of it
//! unused on either path, and Anchor could check none of it. Two instructions
//! that each type-check is the smaller thing.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::invoke_signed;

use crate::dbc;
use crate::error::VaultError;
use crate::instructions::strategy::authority_of;
use crate::state::{Protocol, Vault, PROTOCOL_SEED, VAULT_AUTHORITY_SEED, VAULT_SEED};
use crate::token;

/// Which of the pool creator's streams to sweep into the runner's accounts.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, Debug)]
pub enum IncomeSource {
    /// Trading fees earned while the curve is open, and after graduation the
    /// creator's share on the migrated pool.
    CurveFee,
    /// The part of the final swap that took the reserve past the threshold.
    /// Claimable once, after migration.
    Surplus,
}

#[derive(Accounts)]
pub struct ClaimIncome<'info> {
    #[account(mut)]
    pub runner: Signer<'info>,
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Account<'info, Protocol>,
    #[account(
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
        has_one = runner @ VaultError::NotRunner,
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
    pub pool: UncheckedAccount<'info>,
    /// CHECK: the runner's account for the vault token; derived in the handler.
    #[account(mut)]
    pub token_base_account: UncheckedAccount<'info>,
    /// CHECK: the runner's account for the quote asset; derived in the handler.
    #[account(mut)]
    pub token_quote_account: UncheckedAccount<'info>,
    /// CHECK: DBC's base vault; checked against the pool.
    #[account(mut)]
    pub base_vault: UncheckedAccount<'info>,
    /// CHECK: DBC's quote vault; checked against the pool.
    #[account(mut)]
    pub quote_vault: UncheckedAccount<'info>,
    /// CHECK: the vault's token mint.
    #[account(address = vault.mint @ VaultError::WrongMint)]
    pub base_mint: UncheckedAccount<'info>,
    /// CHECK: the vault's quote mint.
    #[account(address = vault.quote_mint @ VaultError::WrongQuoteMint)]
    pub quote_mint: UncheckedAccount<'info>,
    /// CHECK: the base mint's token program (Token-2022 for every vault).
    pub token_base_program: UncheckedAccount<'info>,
    /// CHECK: the quote mint's token program.
    pub token_quote_program: UncheckedAccount<'info>,
    /// CHECK: DBC's Anchor event authority.
    pub event_authority: UncheckedAccount<'info>,
    /// CHECK: the DBC program, pinned by address.
    #[account(address = dbc::DBC_PROGRAM_ID)]
    pub dbc_program: UncheckedAccount<'info>,
}

pub fn claim_income(ctx: Context<ClaimIncome>, source: IncomeSource) -> Result<()> {
    require!(ctx.accounts.vault.is_tokenized(), VaultError::NotTokenized);
    let pool = dbc::read_virtual_pool(&ctx.accounts.pool.to_account_info())?;
    require_keys_eq!(
        pool.config,
        ctx.accounts.config.key(),
        VaultError::PoolConfigMismatchForPool
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
    require_keys_eq!(
        pool.quote_vault,
        ctx.accounts.quote_vault.key(),
        VaultError::PoolNotDbc
    );
    require_keys_eq!(
        pool.base_vault,
        ctx.accounts.base_vault.key(),
        VaultError::PoolNotDbc
    );

    // The destinations are the runner's own associated accounts, rebuilt here.
    token::require_associated(
        &ctx.accounts.token_quote_account.to_account_info(),
        &ctx.accounts.runner.key(),
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

    match source {
        IncomeSource::Surplus => {
            let accounts = dbc::DbcAccounts {
                pool_authority: &ctx.accounts.pool_authority,
                config: &ctx.accounts.config,
                virtual_pool: &ctx.accounts.pool,
                token_quote_account: &ctx.accounts.token_quote_account,
                quote_vault: &ctx.accounts.quote_vault,
                quote_mint: &ctx.accounts.quote_mint,
                creator: &ctx.accounts.vault_authority,
                token_quote_program: &ctx.accounts.token_quote_program,
                event_authority: &ctx.accounts.event_authority,
                program: &ctx.accounts.dbc_program,
            };
            let (ix, infos) = accounts.creator_withdraw_surplus();
            invoke_signed(&ix, &infos, &[&seeds])?;
        }
        IncomeSource::CurveFee => {
            // The base side can be paid too, so it is derived as well.
            token::require_associated(
                &ctx.accounts.token_base_account.to_account_info(),
                &ctx.accounts.runner.key(),
                &ctx.accounts.vault.mint,
                &ctx.accounts.token_base_program.key(),
            )?;
            let ix = Instruction {
                program_id: dbc::DBC_PROGRAM_ID,
                accounts: vec![
                    AccountMeta::new_readonly(ctx.accounts.pool_authority.key(), false),
                    AccountMeta::new(ctx.accounts.pool.key(), false),
                    AccountMeta::new(ctx.accounts.token_base_account.key(), false),
                    AccountMeta::new(ctx.accounts.token_quote_account.key(), false),
                    AccountMeta::new(ctx.accounts.base_vault.key(), false),
                    AccountMeta::new(ctx.accounts.quote_vault.key(), false),
                    AccountMeta::new_readonly(ctx.accounts.base_mint.key(), false),
                    AccountMeta::new_readonly(ctx.accounts.quote_mint.key(), false),
                    AccountMeta::new_readonly(ctx.accounts.vault_authority.key(), true),
                    AccountMeta::new_readonly(ctx.accounts.token_base_program.key(), false),
                    AccountMeta::new_readonly(ctx.accounts.token_quote_program.key(), false),
                    AccountMeta::new_readonly(ctx.accounts.event_authority.key(), false),
                    AccountMeta::new_readonly(ctx.accounts.dbc_program.key(), false),
                ],
                data: dbc::claim_creator_trading_fee_data(),
            };
            invoke_signed(
                &ix,
                &[
                    ctx.accounts.pool_authority.to_account_info(),
                    ctx.accounts.pool.to_account_info(),
                    ctx.accounts.token_base_account.to_account_info(),
                    ctx.accounts.token_quote_account.to_account_info(),
                    ctx.accounts.base_vault.to_account_info(),
                    ctx.accounts.quote_vault.to_account_info(),
                    ctx.accounts.base_mint.to_account_info(),
                    ctx.accounts.quote_mint.to_account_info(),
                    ctx.accounts.vault_authority.to_account_info(),
                    ctx.accounts.token_base_program.to_account_info(),
                    ctx.accounts.token_quote_program.to_account_info(),
                    ctx.accounts.event_authority.to_account_info(),
                    ctx.accounts.dbc_program.to_account_info(),
                ],
                &[&seeds],
            )?;
        }
    }
    Ok(())
}

#[derive(Accounts)]
pub struct ClaimPositionFee<'info> {
    #[account(mut)]
    pub runner: Signer<'info>,
    #[account(
        seeds = [VAULT_SEED, vault.swig_account.as_ref()],
        bump = vault.bump,
        has_one = runner @ VaultError::NotRunner,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the position's owner, signing by CPI.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, vault.swig_account.as_ref()],
        bump = vault.authority_bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,

    /// CHECK: DAMM v2's signer PDA, a fixed address in its IDL.
    #[account(address = dbc::DAMM_V2_POOL_AUTHORITY)]
    pub pool_authority: UncheckedAccount<'info>,
    /// CHECK: the migrated DAMM v2 pool.
    pub pool: UncheckedAccount<'info>,
    /// CHECK: the creator's permanently locked position.
    #[account(mut)]
    pub position: UncheckedAccount<'info>,
    /// CHECK: the runner's account for the vault token; derived in the handler.
    #[account(mut)]
    pub token_a_account: UncheckedAccount<'info>,
    /// CHECK: the runner's account for the quote asset; derived in the handler.
    #[account(mut)]
    pub token_b_account: UncheckedAccount<'info>,
    /// CHECK: the pool's vault for token A.
    #[account(mut)]
    pub token_a_vault: UncheckedAccount<'info>,
    /// CHECK: the pool's vault for token B.
    #[account(mut)]
    pub token_b_vault: UncheckedAccount<'info>,
    /// CHECK: the pool's token A. DAMM v2 orders its two mints canonically, so
    /// which side the vault token lands on is not knowable in advance — the
    /// handler checks the pair rather than each slot.
    pub token_a_mint: UncheckedAccount<'info>,
    /// CHECK: the pool's token B; see `token_a_mint`.
    pub token_b_mint: UncheckedAccount<'info>,
    /// CHECK: the account holding the position NFT; must be the authority's.
    pub position_nft_account: UncheckedAccount<'info>,
    /// CHECK: token A's program.
    pub token_a_program: UncheckedAccount<'info>,
    /// CHECK: token B's program.
    pub token_b_program: UncheckedAccount<'info>,
    /// CHECK: DAMM v2's Anchor event authority.
    pub event_authority: UncheckedAccount<'info>,
    /// CHECK: the DAMM v2 program, pinned by address.
    #[account(address = dbc::DAMM_V2_PROGRAM_ID)]
    pub damm_program: UncheckedAccount<'info>,
}

/// The migrated pool pays the creator's locked position; the position is the
/// PDA's, and what it pays is the runner's.
pub fn claim_position_fee(ctx: Context<ClaimPositionFee>) -> Result<()> {
    require_keys_eq!(
        authority_of(&ctx.accounts.vault)?,
        ctx.accounts.vault_authority.key(),
        VaultError::PoolCreatorMismatch
    );
    // The position NFT is held by the vault's authority — otherwise this is
    // somebody else's position being claimed into this runner's accounts.
    let nft = token::require_token_account(&ctx.accounts.position_nft_account.to_account_info())?;
    require_keys_eq!(
        nft.owner,
        ctx.accounts.vault_authority.key(),
        VaultError::WrongTokenAccount
    );
    // The pair must be this vault's, in either order; each destination is then
    // derived from the mint that actually sits in its slot.
    let a = ctx.accounts.token_a_mint.key();
    let b = ctx.accounts.token_b_mint.key();
    let vault_mint = ctx.accounts.vault.mint;
    let quote_mint = ctx.accounts.vault.quote_mint;
    require!(
        (a == vault_mint && b == quote_mint) || (a == quote_mint && b == vault_mint),
        VaultError::WrongMint
    );
    token::require_associated(
        &ctx.accounts.token_a_account.to_account_info(),
        &ctx.accounts.runner.key(),
        &a,
        &ctx.accounts.token_a_program.key(),
    )?;
    token::require_associated(
        &ctx.accounts.token_b_account.to_account_info(),
        &ctx.accounts.runner.key(),
        &b,
        &ctx.accounts.token_b_program.key(),
    )?;

    let swig_account = ctx.accounts.vault.swig_account;
    let bump = ctx.accounts.vault.authority_bump;
    let seeds: [&[u8]; 3] = [
        VAULT_AUTHORITY_SEED,
        swig_account.as_ref(),
        std::slice::from_ref(&bump),
    ];

    let ix = Instruction {
        program_id: dbc::DAMM_V2_PROGRAM_ID,
        accounts: vec![
            AccountMeta::new_readonly(ctx.accounts.pool_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.pool.key(), false),
            AccountMeta::new(ctx.accounts.position.key(), false),
            AccountMeta::new(ctx.accounts.token_a_account.key(), false),
            AccountMeta::new(ctx.accounts.token_b_account.key(), false),
            AccountMeta::new(ctx.accounts.token_a_vault.key(), false),
            AccountMeta::new(ctx.accounts.token_b_vault.key(), false),
            AccountMeta::new_readonly(ctx.accounts.token_a_mint.key(), false),
            AccountMeta::new_readonly(ctx.accounts.token_b_mint.key(), false),
            AccountMeta::new_readonly(ctx.accounts.position_nft_account.key(), false),
            AccountMeta::new_readonly(ctx.accounts.vault_authority.key(), true),
            AccountMeta::new_readonly(ctx.accounts.token_a_program.key(), false),
            AccountMeta::new_readonly(ctx.accounts.token_b_program.key(), false),
            AccountMeta::new_readonly(ctx.accounts.event_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.damm_program.key(), false),
        ],
        data: dbc::claim_position_fee_data(),
    };
    invoke_signed(
        &ix,
        &[
            ctx.accounts.pool_authority.to_account_info(),
            ctx.accounts.pool.to_account_info(),
            ctx.accounts.position.to_account_info(),
            ctx.accounts.token_a_account.to_account_info(),
            ctx.accounts.token_b_account.to_account_info(),
            ctx.accounts.token_a_vault.to_account_info(),
            ctx.accounts.token_b_vault.to_account_info(),
            ctx.accounts.token_a_mint.to_account_info(),
            ctx.accounts.token_b_mint.to_account_info(),
            ctx.accounts.position_nft_account.to_account_info(),
            ctx.accounts.vault_authority.to_account_info(),
            ctx.accounts.token_a_program.to_account_info(),
            ctx.accounts.token_b_program.to_account_info(),
            ctx.accounts.event_authority.to_account_info(),
            ctx.accounts.damm_program.to_account_info(),
        ],
        &[&seeds],
    )?;
    Ok(())
}
