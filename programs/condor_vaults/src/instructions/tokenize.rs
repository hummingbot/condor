//! `tokenize` — the vault stops being one person's.
//!
//! This is the one-way door. Before it, the creator may withdraw whatever they
//! put in; after it, nobody may withdraw anything, ever, and the only way
//! assets leave is `redeem` after a finished wind-down. Everything the program
//! does to protect strangers switches on here, because this is where there
//! start to be some.
//!
//! One CPI into Meteora's DBC, with **this program's PDA as the pool creator**.
//! That single choice is what makes the economics rules rather than promises:
//! the migration fee — 80 % of the raise — is the creator's to withdraw, the
//! creator is a PDA, and so `collect_seed` can be permissionless and the seed
//! can never be withdrawn by anyone at all. The curve fees and the surplus are
//! the creator's too, and `claim_income` is the only door out of them, opening
//! onto the creator recorded in the `Vault`.
//!
//! **The config is checked by its terms, not by its address.** A vault prices
//! its own launch off what it already holds — that is the point of having been
//! private first — so each one creates its own DBC config with its own start
//! price, and there is no single address the program could recognise.
//!
//! What it checks instead is every economic term that binds a holder, and each
//! is either a bound or a constant (`state::launch_rules` says which, and why).
//! The bounded ones are the creator's decisions and are **copied onto the
//! `Vault`** here, so a holder reads what this vault chose instead of decoding
//! a config account:
//!
//! * `migration_fee_pct`, 20–80 — the split between the strategy's capital and
//!   the holders' exit depth, and the number that most changes what the vault
//!   is;
//! * `creator_trading_fee_pct`, at most 50;
//! * `migration_fee_option`, any of Meteora's fixed-fee options.
//!
//! The constants are the 10 SOL threshold Meteora's keepers actually serve, the
//! migration fee going wholly to the creator, Token-2022 with a fixed supply,
//! Condor as fee claimer, and the **leftover receiver set to the vault's own
//! treasury** — which keeps the unissued supply out of the creator's hands and,
//! because it is the treasury, puts it where the delegate can market-make with it.
//!
//! `issue_bps` is the share of the fixed supply this sale offers. The rest is
//! retained by the vault. A buyer reads it as the ceiling on how far they can
//! later be diluted, which is why it is committed here rather than described
//! in a listing.
//!
//! DBC does the entire mint: Token-2022, six decimals, a fixed supply and
//! immutable authorities, with the name, symbol and URI passed straight
//! through. This program never touches mint authority, because it never has
//! one to touch.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};
use anchor_lang::solana_program::program::invoke_signed;

use crate::dbc;
use crate::error::VaultError;
use crate::state::{
    Protocol, Vault, BPS_DENOMINATOR, MAX_NAME_LEN, MAX_SYMBOL_LEN, MAX_URI_LEN, PROTOCOL_SEED,
    TREASURY_SEED, VAULT_SEED,
};

#[derive(Accounts)]
pub struct Tokenize<'info> {
    /// Pays for the pool, the mint and the vaults DBC creates.
    #[account(mut)]
    pub creator: Signer<'info>,
    #[account(seeds = [PROTOCOL_SEED], bump = protocol.bump)]
    pub protocol: Account<'info, Protocol>,
    #[account(
        mut,
        seeds = [VAULT_SEED, vault.id.as_ref()],
        bump = vault.bump,
        has_one = creator @ VaultError::NotCreator,
    )]
    pub vault: Account<'info, Vault>,
    /// CHECK: the DBC pool creator, signing by CPI.
    #[account(
        seeds = [TREASURY_SEED, vault.id.as_ref()],
        bump = vault.treasury_bump,
    )]
    pub treasury: UncheckedAccount<'info>,

    /// CHECK: this vault's own partner config. Checked term by term in the
    /// handler — see the module docs for why not by address.
    pub config: UncheckedAccount<'info>,
    /// CHECK: DBC's own signer PDA, a fixed address in its IDL.
    #[account(address = dbc::DBC_POOL_AUTHORITY)]
    pub pool_authority: UncheckedAccount<'info>,
    /// CHECK: the new mint. A fresh keypair, signing the outer transaction;
    /// DBC initializes it.
    #[account(mut)]
    pub base_mint: Signer<'info>,
    /// CHECK: the quote mint, which must be the one the partner config names.
    pub quote_mint: UncheckedAccount<'info>,
    /// CHECK: the virtual pool DBC creates.
    #[account(mut)]
    pub pool: UncheckedAccount<'info>,
    /// CHECK: DBC's base vault for this pool.
    #[account(mut)]
    pub base_vault: UncheckedAccount<'info>,
    /// CHECK: DBC's quote vault for this pool.
    #[account(mut)]
    pub quote_vault: UncheckedAccount<'info>,
    /// CHECK: the quote mint's token program.
    pub token_quote_program: UncheckedAccount<'info>,
    /// CHECK: Token-2022 — every Condor vault launches a 2022 mint (plan D6).
    #[account(address = dbc::TOKEN_2022_PROGRAM_ID)]
    pub token_program: UncheckedAccount<'info>,
    /// CHECK: DBC's Anchor event authority.
    pub event_authority: UncheckedAccount<'info>,
    /// CHECK: the DBC program, pinned by address.
    #[account(address = dbc::DBC_PROGRAM_ID)]
    pub dbc_program: UncheckedAccount<'info>,
    pub system_program: Program<'info, System>,
}

pub fn tokenize(
    ctx: Context<Tokenize>,
    name: String,
    symbol: String,
    uri: String,
    issue_bps: u16,
) -> Result<()> {
    require!(!ctx.accounts.vault.is_tokenized(), VaultError::AlreadyTokenized);
    require!(ctx.accounts.vault.is_pinned(), VaultError::NotPinned);
    require!(
        name.len() <= MAX_NAME_LEN && symbol.len() <= MAX_SYMBOL_LEN && uri.len() <= MAX_URI_LEN,
        VaultError::MetadataTooLong
    );
    // Selling none of it is not a sale, and selling all of it leaves nothing to
    // fund a later one — both are almost certainly a mistake in the form.
    require!(
        issue_bps > 0 && issue_bps <= BPS_DENOMINATOR,
        VaultError::BpsOutOfRange
    );

    let terms = check_launch_terms(
        &ctx.accounts.config.to_account_info(),
        &ctx.accounts.quote_mint.key(),
        &ctx.accounts.protocol.fee_claimer,
        &ctx.accounts.treasury.key(),
    )?;

        let seeds = ctx.accounts.vault.treasury_seeds();

    let ix = Instruction {
        program_id: dbc::DBC_PROGRAM_ID,
        accounts: vec![
            AccountMeta::new_readonly(ctx.accounts.config.key(), false),
            AccountMeta::new_readonly(ctx.accounts.pool_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.treasury.key(), true),
            AccountMeta::new(ctx.accounts.base_mint.key(), true),
            AccountMeta::new_readonly(ctx.accounts.quote_mint.key(), false),
            AccountMeta::new(ctx.accounts.pool.key(), false),
            AccountMeta::new(ctx.accounts.base_vault.key(), false),
            AccountMeta::new(ctx.accounts.quote_vault.key(), false),
            AccountMeta::new(ctx.accounts.creator.key(), true),
            AccountMeta::new_readonly(ctx.accounts.token_quote_program.key(), false),
            AccountMeta::new_readonly(ctx.accounts.token_program.key(), false),
            AccountMeta::new_readonly(ctx.accounts.system_program.key(), false),
            AccountMeta::new_readonly(ctx.accounts.event_authority.key(), false),
            AccountMeta::new_readonly(ctx.accounts.dbc_program.key(), false),
        ],
        data: dbc::initialize_pool_data(&name, &symbol, &uri),
    };
    invoke_signed(
        &ix,
        &[
            ctx.accounts.config.to_account_info(),
            ctx.accounts.pool_authority.to_account_info(),
            ctx.accounts.treasury.to_account_info(),
            ctx.accounts.base_mint.to_account_info(),
            ctx.accounts.quote_mint.to_account_info(),
            ctx.accounts.pool.to_account_info(),
            ctx.accounts.base_vault.to_account_info(),
            ctx.accounts.quote_vault.to_account_info(),
            ctx.accounts.creator.to_account_info(),
            ctx.accounts.token_quote_program.to_account_info(),
            ctx.accounts.token_program.to_account_info(),
            ctx.accounts.system_program.to_account_info(),
            ctx.accounts.event_authority.to_account_info(),
            ctx.accounts.dbc_program.to_account_info(),
        ],
        &[&seeds],
    )?;

    let vault = &mut ctx.accounts.vault;
    vault.mint = ctx.accounts.base_mint.key();
    vault.dbc_pool = ctx.accounts.pool.key();
    // The quote asset, fixed here and only here. `check_launch_terms` has
    // already required it to be the one the partner config names, so this is
    // the asset the curve sells for, the one the migrated DAMM v2 pool quotes
    // in, and therefore the one a wind-down converts into and a redemption
    // pays. One decision, made once, at the moment it starts to matter.
    vault.quote_mint = ctx.accounts.quote_mint.key();
    vault.issue_bps = issue_bps;
    vault.migration_fee_pct = terms.migration_fee_pct;
    vault.creator_trading_fee_pct = terms.creator_trading_fee_pct;
    vault.migration_fee_option = terms.migration_fee_option;
    vault.tokenized_ts = Clock::get()?.unix_timestamp;
    Ok(())
}

/// The terms a vault chose, once they have been checked.
pub struct LaunchTerms {
    pub migration_fee_pct: u8,
    pub creator_trading_fee_pct: u8,
    pub migration_fee_option: u8,
}

/// Every term of the partner config that binds a holder.
///
/// Each line here is a promise a listing would otherwise have to make in prose.
/// A config that fails any of them is refused before the mint exists, so there
/// is no half-launched vault to explain.
fn check_launch_terms(
    config: &AccountInfo,
    quote_mint: &Pubkey,
    fee_claimer: &Pubkey,
    treasury: &Pubkey,
) -> Result<LaunchTerms> {
    use crate::state::launch_rules as rules;
    let c = dbc::read_pool_config(config)?;
    require_keys_eq!(c.quote_mint, *quote_mint, VaultError::WrongQuoteMint);
    require_keys_eq!(c.fee_claimer, *fee_claimer, VaultError::LaunchTermsMismatch);
    // The unissued supply lands in the vault's own treasury — not in the
    // creator's, where nothing would stop them selling it at once, and not in a
    // pot of its own, where the delegate could never put it to work.
    require_keys_eq!(
        c.leftover_receiver,
        *treasury,
        VaultError::LaunchTermsMismatch
    );

    // Constants: varying any of these breaks something the design rests on.
    require!(
        c.migration_option == dbc::MIGRATION_OPTION_DAMM_V2
            && c.token_type == rules::TOKEN_TYPE_TOKEN_2022
            && c.fixed_token_supply
            && c.creator_migration_fee_pct == rules::CREATOR_MIGRATION_FEE_PCT
            && c.migration_quote_threshold == rules::MIGRATION_QUOTE_THRESHOLD,
        VaultError::LaunchTermsMismatch
    );

    // Bounds: the creator's decisions, recorded on the vault for holders to read.
    require!(
        c.migration_fee_pct >= rules::MIN_MIGRATION_FEE_PCT
            && c.migration_fee_pct <= rules::MAX_MIGRATION_FEE_PCT,
        VaultError::MigrationFeeOutOfRange
    );
    require!(
        c.creator_trading_fee_pct <= rules::MAX_CREATOR_TRADING_FEE_PCT,
        VaultError::LaunchTermsMismatch
    );
    require!(
        c.migration_fee_option <= rules::MAX_MIGRATION_FEE_OPTION,
        VaultError::LaunchTermsMismatch
    );

    Ok(LaunchTerms {
        migration_fee_pct: c.migration_fee_pct,
        creator_trading_fee_pct: c.creator_trading_fee_pct,
        migration_fee_option: c.migration_fee_option,
    })
}
