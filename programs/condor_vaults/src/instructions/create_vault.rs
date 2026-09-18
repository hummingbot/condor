//! `create_vault` — a wallet that trades a strategy, owned by this program.
//!
//! Two PDAs from one random `id`: the `Vault` record, which is the vault's
//! public identity, and the treasury — a system account with no data that holds
//! the SOL and owns every token account and position the vault will ever
//! have. There is no key anywhere that can reach inside either; only the
//! instructions in this crate, signing with the treasury's seeds.
//!
//! **A vault starts private.** It has no mint, and while it has none the only
//! person with a claim on what is inside is the creator, who may empty it at
//! will through `execute_unchecked`. That is not a weaker version of the real
//! thing — it is the honest description of a treasury running one person's
//! strategy with one person's money. Everything that makes a vault trustless
//! is machinery for protecting *other* people, and it switches on at
//! `tokenize`, when there start to be some.
//!
//! `fund_lamports` funds the treasury in the same breath, because the creator is
//! already the payer and a treasury with nothing in it is a draft with an extra
//! step. At least the rent floor for an empty account, or the treasury would
//! not survive to be funded later.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::invoke;
use anchor_lang::solana_program::system_instruction;

use crate::error::VaultError;
use crate::state::{AgentRef, Vault, VaultState, TREASURY_SEED, VAULT_SEED};

#[derive(Accounts)]
#[instruction(id: [u8; 32])]
pub struct CreateVault<'info> {
    /// Pays for the `Vault` and whatever it funds the treasury with. Recorded
    /// as the creator.
    #[account(mut)]
    pub creator: Signer<'info>,
    /// CHECK: the treasury. Never signs here; its seeds are what make it this
    /// vault's and this program's.
    #[account(
        mut,
        seeds = [TREASURY_SEED, id.as_ref()],
        bump,
    )]
    pub treasury: UncheckedAccount<'info>,
    #[account(
        init,
        payer = creator,
        space = 8 + Vault::INIT_SPACE,
        seeds = [VAULT_SEED, id.as_ref()],
        bump,
    )]
    pub vault: Account<'info, Vault>,
    pub system_program: Program<'info, System>,
}

pub fn create_vault(ctx: Context<CreateVault>, id: [u8; 32], fund_lamports: u64) -> Result<()> {
    let floor = Rent::get()?.minimum_balance(0);
    require!(fund_lamports >= floor, VaultError::FundingBelowRent);
    invoke(
        &system_instruction::transfer(
            &ctx.accounts.creator.key(),
            &ctx.accounts.treasury.key(),
            fund_lamports,
        ),
        &[
            ctx.accounts.creator.to_account_info(),
            ctx.accounts.treasury.to_account_info(),
            ctx.accounts.system_program.to_account_info(),
        ],
    )?;

    let vault = &mut ctx.accounts.vault;
    vault.creator = ctx.accounts.creator.key();
    vault.id = id;
    vault.mint = Pubkey::default();
    vault.dbc_pool = Pubkey::default();
    vault.agent_ref = AgentRef::default();
    vault.config_hash = [0u8; 32];
    // No quote asset yet: `tokenize` writes it from the launch config, which
    // is where it is chosen and what the graduated pool will quote in.
    vault.quote_mint = Pubkey::default();
    vault.version = 0;
    vault.circulating_supply = 0;
    vault.total_supply = 0;
    vault.graduation_quote_threshold = 0;
    vault.redeemable_supply = 0;
    vault.creator_trading_fee_pct = 0;
    vault.pool_fee_option = 0;
    // Nothing runs until a strategy is pinned; `pin` is what starts it.
    vault.state = VaultState::Paused;
    vault.delegate = Pubkey::default();
    vault.created_ts = Clock::get()?.unix_timestamp;
    vault.tokenized_ts = 0;
    vault.wind_down_ts = 0;
    vault.bump = ctx.bumps.vault;
    vault.treasury_bump = ctx.bumps.treasury;
    vault._reserved = [0u8; 64];
    Ok(())
}
