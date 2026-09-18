//! `create_vault` — a wallet that trades a strategy, owned by this program.
//!
//! Creates the Swig with **this program's PDA as its root authority** and, in
//! the same transaction, the `Vault` record addressed by it. The root being a
//! PDA is what the whole design rests on: there is no key anywhere that can
//! reach inside, only the instructions in this crate.
//!
//! **A vault starts private.** It has no mint, and while it has none the only
//! person with a claim on what is inside is the runner, who may withdraw at
//! will (`withdraw`). That is not a weaker version of the real thing — it is
//! the honest description of a wallet running one person's strategy with one
//! person's money. Everything that makes a vault trustless is machinery for
//! protecting *other* people, and it switches on at `tokenize`, when there
//! start to be some.
//!
//! The quote asset is chosen here rather than at the pin: a private vault
//! needs a unit of account from its first deposit — it is what the balance is
//! reported in, what a wind-down converts to, and what NAV is quoted in long
//! before there is a token to price against it.
//!
//! `fund_lamports` funds the wallet in the same breath, because the runner is
//! already the payer and a wallet with nothing in it is a draft with an extra
//! step.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::invoke;
use anchor_lang::solana_program::system_instruction;

use crate::error::VaultError;
use crate::state::{AgentRef, Vault, VaultState, VAULT_AUTHORITY_SEED, VAULT_SEED};
use crate::swig;

#[derive(Accounts)]
#[instruction(id: [u8; 32])]
pub struct CreateVault<'info> {
    /// Pays for the Swig, the `Vault`, and whatever it funds the wallet with.
    /// Recorded as the runner.
    #[account(mut)]
    pub runner: Signer<'info>,
    /// CHECK: the Swig account, verified against `["swig", id]` in the handler.
    #[account(mut)]
    pub swig_account: UncheckedAccount<'info>,
    /// CHECK: the PDA that becomes the Swig's root authority. Never signs here
    /// — a root authority is data in the create instruction, not a signer of
    /// it — but its address must be this program's, which the seeds enforce.
    #[account(
        seeds = [VAULT_AUTHORITY_SEED, swig_account.key().as_ref()],
        bump,
    )]
    pub vault_authority: UncheckedAccount<'info>,
    /// CHECK: the Swig's funds owner, verified against its own seeds.
    #[account(mut)]
    pub swig_funds_owner: UncheckedAccount<'info>,
    #[account(
        init,
        payer = runner,
        space = 8 + Vault::INIT_SPACE,
        seeds = [VAULT_SEED, swig_account.key().as_ref()],
        bump,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the Swig program, pinned by address.
    #[account(address = swig::SWIG_PROGRAM_ID)]
    pub swig_program: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
}

pub fn create_vault(
    ctx: Context<CreateVault>,
    id: [u8; 32],
    quote_mint: Pubkey,
    fund_lamports: u64,
) -> Result<()> {
    let (expected_swig, swig_bump) = swig::swig_pda(&id);
    require_keys_eq!(
        ctx.accounts.swig_account.key(),
        expected_swig,
        VaultError::SwigMismatch
    );
    let (expected_owner, wallet_bump) = swig::swig_funds_owner(&expected_swig);
    require_keys_eq!(
        ctx.accounts.swig_funds_owner.key(),
        expected_owner,
        VaultError::SwigMismatch
    );

    swig::create(
        &ctx.accounts.swig_program,
        &ctx.accounts.swig_account,
        &ctx.accounts.runner,
        &ctx.accounts.swig_funds_owner,
        &ctx.accounts.system_program,
        &id,
        &ctx.accounts.vault_authority.key(),
        swig_bump,
        wallet_bump,
    )?;

    if fund_lamports > 0 {
        invoke(
            &system_instruction::transfer(
                &ctx.accounts.runner.key(),
                &ctx.accounts.swig_funds_owner.key(),
                fund_lamports,
            ),
            &[
                ctx.accounts.runner.to_account_info(),
                ctx.accounts.swig_funds_owner.to_account_info(),
                ctx.accounts.system_program.to_account_info(),
            ],
        )?;
    }

    let vault = &mut ctx.accounts.vault;
    vault.runner = ctx.accounts.runner.key();
    vault.swig_account = expected_swig;
    vault.funds_owner = expected_owner;
    vault.mint = Pubkey::default();
    vault.dbc_pool = Pubkey::default();
    vault.agent_ref = AgentRef::default();
    vault.config_hash = [0u8; 32];
    vault.quote_mint = quote_mint;
    vault.version = 0;
    vault.fee_bps = 0;
    vault.issue_bps = 0;
    vault.migration_fee_pct = 0;
    vault.creator_trading_fee_pct = 0;
    vault.migration_fee_option = 0;
    // Nothing runs until a strategy is pinned; `pin` is what starts it.
    vault.state = VaultState::Paused;
    vault.delegate = Pubkey::default();
    vault.created_ts = Clock::get()?.unix_timestamp;
    vault.tokenized_ts = 0;
    vault.wind_down_ts = 0;
    vault.bump = ctx.bumps.vault;
    vault.authority_bump = ctx.bumps.vault_authority;
    vault._reserved = [0u8; 64];
    Ok(())
}
